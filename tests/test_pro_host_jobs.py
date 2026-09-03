from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from arcgis_pro_mcp import pro_attach, pro_host


def _cp1252_stdout():
    """Exercise the same narrow Windows console used by the host HTTP tests."""
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict", write_through=True)
    return patch.object(sys, "stdout", stream)


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request_json(
    base: str,
    path: str,
    *,
    token: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 4.0,
) -> tuple[int, dict[str, Any]]:
    headers = {"Connection": "close", pro_attach.TOKEN_HEADER: token}
    data = None
    method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
    decoded = json.loads(body.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise AssertionError(f"expected JSON object, got {decoded!r}")
    return status, decoded


def _wait_for_state(path: Path, errors: list[BaseException]) -> dict[str, Any]:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if errors:
            raise AssertionError("window host failed during startup") from errors[0]
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.01)
            continue
        if isinstance(state, dict):
            return state
    raise AssertionError("window host did not publish its state")


@contextmanager
def _running_host(invoke_tool: Any, *, job_timeout: float = 4.0) -> Iterator[SimpleNamespace]:
    port = _unused_loopback_port()
    errors: list[BaseException] = []
    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root) / "host-state.json"
        project_path = str(Path(root) / "current.aprx")
        project = SimpleNamespace(
            filePath=project_path,
            activeView=None,
            activeMap=None,
            isReadOnly=False,
        )

        def current_project() -> tuple[Any, str]:
            return project, project_path

        def run_host() -> None:
            try:
                pro_host.main()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with (
            patch.dict(os.environ, {pro_host.ENV_PORT: str(port)}, clear=False),
            patch.dict(
                sys.modules,
                {"arcpy": SimpleNamespace(env=SimpleNamespace(isCancelled=False))},
            ),
            patch.object(pro_host, "HOST_JOB_TIMEOUT", job_timeout),
            patch.object(pro_host, "state_path", return_value=str(state_path)),
            patch.object(pro_attach, "state_path", return_value=str(state_path)),
            patch.object(pro_host, "_current_project", side_effect=current_project),
            patch.object(pro_host, "_invoke_tool", side_effect=invoke_tool),
            _cp1252_stdout(),
        ):
            os.environ.pop(pro_host.ENV_IN_HOST, None)
            thread = threading.Thread(target=run_host, name="test-pro-host-jobs", daemon=True)
            thread.start()
            state: dict[str, Any] = {}
            token = ""
            base = f"http://127.0.0.1:{port}"
            try:
                state = _wait_for_state(state_path, errors)
                token = str(state["token"])
                yield SimpleNamespace(
                    base=base,
                    token=token,
                    state=state,
                    state_path=state_path,
                    project=project,
                    project_path=project_path,
                    thread=thread,
                    errors=errors,
                )
            finally:
                if thread.is_alive() and token:
                    try:
                        _request_json(base, "/stop", token=token, payload={})
                    except OSError:
                        pass
                thread.join(timeout=3.0)


def _call_payload(
    host: SimpleNamespace,
    request_id: str,
    tool: str,
    *,
    idempotency_key: str = "",
) -> dict[str, Any]:
    return {
        "protocol_version": pro_attach.PROTOCOL_VERSION,
        "session_id": host.state["session_id"],
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "expected_project": host.project_path,
        "tool": tool,
        "arguments": {"aprx_path": "CURRENT"},
        "environment": {},
    }


def _poll_job(host: SimpleNamespace, request_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 3.0
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, last = _request_json(
            host.base,
            f"/jobs/{request_id}",
            token=host.token,
        )
        if status == 200 and last.get("state") in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return last
        time.sleep(0.02)
    raise AssertionError(f"job did not reach a terminal state: {last!r}")


class ProHostJobProtocolTests(unittest.TestCase):
    def tearDown(self) -> None:
        pro_attach.clear_confirmed_target()

    def test_real_loopback_async_status_cancel_and_idempotency(self) -> None:
        invoked: list[str] = []
        blocking_started = threading.Event()
        release_blocking = threading.Event()

        def invoke_tool(
            name: str,
            arguments: dict[str, Any],
            environment: dict[str, Any] | None = None,
            current_project: Any | None = None,
            current_project_path: str = "",
        ) -> str:
            del arguments, environment, current_project, current_project_path
            invoked.append(name)
            if name == "blocking":
                blocking_started.set()
                if not release_blocking.wait(timeout=3.0):
                    raise RuntimeError("test did not release the blocking job")
            return json.dumps({"executed": name})

        with _running_host(invoke_tool) as host:
            blocking = _call_payload(
                host,
                "async-blocking",
                "blocking",
                idempotency_key="stable-blocking-key",
            )
            status, body = _request_json(
                host.base,
                "/jobs/submit",
                token=host.token,
                payload=blocking,
            )
            self.assertEqual(status, 202)
            self.assertIn(body["state"], {"QUEUED", "RUNNING"})
            self.assertTrue(body["asynchronous"])
            self.assertTrue(blocking_started.wait(timeout=2.0))

            status, running_cancel = _request_json(
                host.base,
                "/jobs/cancel",
                token=host.token,
                payload={
                    "protocol_version": pro_attach.PROTOCOL_VERSION,
                    "session_id": host.state["session_id"],
                    "request_id": "async-blocking",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(running_cancel["state"], "RUNNING")
            self.assertFalse(running_cancel["cancelled"])
            self.assertTrue(running_cancel["cancel_requested"])
            self.assertFalse(running_cancel["cancel_supported"])

            queued = _call_payload(host, "async-queued", "must-not-run")
            status, queued_body = _request_json(
                host.base,
                "/jobs/submit",
                token=host.token,
                payload=queued,
            )
            self.assertEqual(status, 202)
            self.assertEqual(queued_body["state"], "QUEUED")

            status, cancelled = _request_json(
                host.base,
                "/jobs/cancel",
                token=host.token,
                payload={
                    "protocol_version": pro_attach.PROTOCOL_VERSION,
                    "session_id": host.state["session_id"],
                    "request_id": "async-queued",
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(cancelled["cancelled"])
            self.assertEqual(cancelled["state"], "CANCELLED")

            status, persisted_cancel = _request_json(
                host.base,
                "/jobs/async-queued",
                token=host.token,
            )
            self.assertEqual(status, 200)
            self.assertEqual(persisted_cancel["state"], "CANCELLED")

            release_blocking.set()
            succeeded = _poll_job(host, "async-blocking")
            self.assertEqual(succeeded["state"], "SUCCEEDED")
            self.assertEqual(json.loads(succeeded["result"]), {"executed": "blocking"})
            self.assertTrue(succeeded["cancel_requested"])

            replay = {**blocking, "request_id": "client-generated-replay-id"}
            status, replay_body = _request_json(
                host.base,
                "/jobs/submit",
                token=host.token,
                payload=replay,
            )
            self.assertEqual(status, 200)
            self.assertTrue(replay_body["idempotent_replay"])
            self.assertEqual(replay_body["request_id"], "async-blocking")
            self.assertEqual(replay_body["state"], "SUCCEEDED")

            conflict = {**replay, "request_id": "conflict", "tool": "different"}
            status, conflict_body = _request_json(
                host.base,
                "/jobs/submit",
                token=host.token,
                payload=conflict,
            )
            self.assertEqual(status, 409)
            self.assertIn("idempotency_key", conflict_body["error"])

            status, missing = _request_json(
                host.base,
                "/jobs/does-not-exist",
                token=host.token,
            )
            self.assertEqual(status, 404)
            self.assertEqual(missing["request_id"], "does-not-exist")
            self.assertEqual(invoked, ["blocking"])

    def test_real_loopback_event_poll_reports_timeout_then_context_revision(self) -> None:
        def invoke_tool(*args: Any, **kwargs: Any) -> str:
            del args, kwargs
            return "{}"

        with _running_host(invoke_tool) as host:
            status, health = _request_json(host.base, "/health", token=host.token)
            self.assertEqual(status, 200)
            revision = int(health["context_revision"])

            status, unchanged = _request_json(
                host.base,
                f"/events?after_revision={revision}&timeout_ms=0",
                token=host.token,
            )
            self.assertEqual(status, 200)
            self.assertFalse(unchanged["changed"])
            self.assertEqual(unchanged["revision"], revision)
            self.assertEqual(unchanged["event_source"], "python_poll")
            self.assertFalse(unchanged["draw_complete_supported"])

            extent = SimpleNamespace(XMin=1, YMin=2, XMax=3, YMax=4)
            camera = SimpleNamespace(
                scale=5000,
                heading=0,
                pitch=-90,
                roll=0,
                getExtent=lambda: extent,
            )
            active_map = SimpleNamespace(name="Changed map", listLayers=lambda: [])
            host.project.activeMap = active_map
            host.project.activeView = SimpleNamespace(
                name="Changed map",
                map=active_map,
                camera=camera,
            )

            status, changed = _request_json(
                host.base,
                f"/events?after_revision={revision}&timeout_ms=2500",
                token=host.token,
                timeout=4.0,
            )
            self.assertEqual(status, 200)
            self.assertTrue(changed["changed"])
            self.assertGreater(changed["revision"], revision)
            self.assertEqual(changed["active_view"]["type"], "MAP_VIEW")
            self.assertEqual(changed["active_view"]["map_name"], "Changed map")

            status, invalid = _request_json(
                host.base,
                "/events?after_revision=not-an-int",
                token=host.token,
            )
            self.assertEqual(status, 400)
            self.assertIn("invalid event query", invalid["error"])

    def test_pro_attach_clients_use_real_loopback_host(self) -> None:
        invoked: list[str] = []
        blocking_started = threading.Event()
        release_blocking = threading.Event()

        def invoke_tool(
            name: str,
            arguments: dict[str, Any],
            environment: dict[str, Any] | None = None,
            current_project: Any | None = None,
            current_project_path: str = "",
        ) -> str:
            del arguments, environment, current_project, current_project_path
            invoked.append(name)
            if name == "client-blocking":
                blocking_started.set()
                if not release_blocking.wait(timeout=3.0):
                    raise RuntimeError("test did not release the client blocking job")
            return json.dumps({"tool": name, "verified": True})

        with _running_host(invoke_tool) as host:
            status, health = _request_json(host.base, "/health", token=host.token)
            self.assertEqual(status, 200)
            health["_endpoint_base"] = host.base
            health["_endpoint_token"] = host.token
            pro_attach.confirm_host_target(health)

            first = pro_attach.submit_host_job(
                "client-blocking",
                {"aprx_path": "CURRENT"},
                idempotency_key="client-stable-key",
                host_snapshot=health,
            )
            self.assertIn(first["state"], {"QUEUED", "RUNNING"})
            self.assertTrue(blocking_started.wait(timeout=2.0))

            queued = pro_attach.submit_host_job(
                "client-queued",
                {"aprx_path": "CURRENT"},
                host_snapshot=health,
            )
            cancelled = pro_attach.cancel_host_job(queued["request_id"], health)
            self.assertEqual(cancelled["state"], "CANCELLED")
            self.assertTrue(cancelled["cancelled"])
            self.assertEqual(
                pro_attach.host_job_status(queued["request_id"], health)["state"],
                "CANCELLED",
            )

            current_revision = int(health["context_revision"])
            event = pro_attach.wait_for_window_change(current_revision, 0, health)
            self.assertFalse(event["changed"])
            self.assertEqual(event["revision"], current_revision)

            release_blocking.set()
            deadline = time.monotonic() + 3.0
            first_status: dict[str, Any] = {}
            while time.monotonic() < deadline:
                first_status = pro_attach.host_job_status(first["request_id"], health)
                if first_status["state"] == "SUCCEEDED":
                    break
                time.sleep(0.02)
            self.assertEqual(first_status["state"], "SUCCEEDED")
            self.assertEqual(
                first_status["result"],
                {"tool": "client-blocking", "verified": True},
            )

            replay = pro_attach.submit_host_job(
                "client-blocking",
                {"aprx_path": "CURRENT"},
                idempotency_key="client-stable-key",
                host_snapshot=health,
            )
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(replay["request_id"], first["request_id"])
            self.assertEqual(replay["result"], first_status["result"])
            self.assertEqual(invoked, ["client-blocking"])

            with self.assertRaisesRegex(RuntimeError, "request_id"):
                pro_attach.host_job_status("bad/request", health)
            with self.assertRaisesRegex(RuntimeError, "CURRENT"):
                pro_attach.submit_host_job(
                    "bad-target",
                    {"aprx_path": host.project_path},
                    host_snapshot=health,
                )


if __name__ == "__main__":
    unittest.main()
