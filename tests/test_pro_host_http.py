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
    """Match Windows CI consoles so Chinese host banners cannot crash startup."""
    buf = io.BytesIO()
    narrow = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", write_through=True)
    return patch.object(sys, "stdout", narrow)


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request_json(
    base: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    content_type: str | None = None,
    timeout: float = 2.0,
) -> tuple[int, dict[str, Any]]:
    headers = {"Connection": "close"}
    if token is not None:
        headers[pro_attach.TOKEN_HEADER] = token
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type or "application/json"
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
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise AssertionError(f"expected a JSON object, got {parsed!r}")
    return status, parsed


def _wait_for_state(path: Path, host_errors: list[BaseException]) -> dict[str, Any]:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if host_errors:
            raise AssertionError("window host failed during startup") from host_errors[0]
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        # Publication is atomic, but Windows can briefly deny a concurrent read
        # while the final protected ACL/owner is applied. Match read_state's
        # fail-closed OSError handling and keep polling until the file is ready.
        except (OSError, json.JSONDecodeError):
            time.sleep(0.01)
            continue
        if isinstance(state, dict):
            return state
        time.sleep(0.01)
    raise AssertionError("window host did not publish its state in time")


@contextmanager
def _running_host(invoke_tool: Any, *, job_timeout: float = 0.75) -> Iterator[SimpleNamespace]:
    port = _unused_loopback_port()
    host_errors: list[BaseException] = []
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
                host_errors.append(exc)

        with (
            patch.dict(os.environ, {pro_host.ENV_PORT: str(port)}, clear=False),
            patch.dict(
                sys.modules,
                {"arcpy": SimpleNamespace(env=SimpleNamespace(isCancelled=False))},
            ),
            patch.object(pro_host, "HOST_JOB_TIMEOUT", job_timeout),
            patch.object(pro_host, "state_path", return_value=str(state_path)),
            patch.object(pro_host, "_current_project", side_effect=current_project),
            patch.object(pro_host, "_invoke_tool", side_effect=invoke_tool),
            _cp1252_stdout(),
        ):
            os.environ.pop(pro_host.ENV_IN_HOST, None)
            host_thread = threading.Thread(target=run_host, name="test-pro-host", daemon=True)
            host_thread.start()
            state: dict[str, Any] = {}
            token = ""
            base = f"http://127.0.0.1:{port}"
            try:
                state = _wait_for_state(state_path, host_errors)
                token = str(state["token"])
                yield SimpleNamespace(
                    base=base,
                    token=token,
                    state=state,
                    state_path=state_path,
                    project=project,
                    project_path=project_path,
                    thread=host_thread,
                    errors=host_errors,
                    job_timeout=job_timeout,
                )
            finally:
                if host_thread.is_alive() and token:
                    try:
                        _request_json(base, "/stop", token=token, payload={})
                    except OSError:
                        pass
                host_thread.join(timeout=3.0)


def _valid_call(host: SimpleNamespace, request_id: str, tool: str) -> dict[str, Any]:
    return {
        "protocol_version": pro_attach.PROTOCOL_VERSION,
        "session_id": host.state["session_id"],
        "request_id": request_id,
        "expected_project": host.project_path,
        "tool": tool,
        "arguments": {"aprx_path": "CURRENT"},
        "environment": {},
    }


class ProHostHttpTests(unittest.TestCase):
    def test_real_loopback_protocol_rejections_current_call_and_stop_cleanup(self) -> None:
        port = _unused_loopback_port()
        host_errors: list[BaseException] = []
        invoke_calls: list[dict[str, Any]] = []
        blocking_started = threading.Event()
        release_blocking = threading.Event()
        blocking_response: list[tuple[int, dict[str, Any]]] = []
        blocking_errors: list[BaseException] = []
        blocking_client: threading.Thread | None = None

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

            def invoke_tool(
                name: str,
                arguments: dict[str, Any],
                environment: dict[str, Any] | None = None,
                current_project: Any | None = None,
                current_project_path: str = "",
            ) -> str:
                invoke_calls.append(
                    {
                        "name": name,
                        "arguments": dict(arguments),
                        "environment": dict(environment or {}),
                        "current_project": current_project,
                        "current_project_path": current_project_path,
                    }
                )
                if name == "blocking":
                    blocking_started.set()
                    if not release_blocking.wait(timeout=3.0):
                        raise RuntimeError("test did not release the blocking call")
                return f"executed:{name}"

            def run_host() -> None:
                try:
                    pro_host.main()
                except BaseException as exc:  # noqa: BLE001
                    host_errors.append(exc)

            def run_blocking_call(base: str, token: str, payload: dict[str, Any]) -> None:
                try:
                    blocking_response.append(
                        _request_json(base, "/call", token=token, payload=payload)
                    )
                except BaseException as exc:  # noqa: BLE001
                    blocking_errors.append(exc)

            with (
                patch.dict(os.environ, {pro_host.ENV_PORT: str(port)}, clear=False),
                patch.dict(
                    sys.modules,
                    {"arcpy": SimpleNamespace(env=SimpleNamespace(isCancelled=False))},
                ),
                patch.object(pro_host, "state_path", return_value=str(state_path)),
                patch.object(pro_host, "_current_project", side_effect=current_project),
                patch.object(pro_host, "_invoke_tool", side_effect=invoke_tool),
                _cp1252_stdout(),
            ):
                os.environ.pop(pro_host.ENV_IN_HOST, None)
                host_thread = threading.Thread(target=run_host, name="test-pro-host", daemon=True)
                host_thread.start()
                state: dict[str, Any] = {}
                token = ""
                base = f"http://127.0.0.1:{port}"
                try:
                    state = _wait_for_state(state_path, host_errors)
                    token = str(state["token"])
                    self.assertEqual(state["port"], port)
                    self.assertEqual(state["project"], project_path)

                    status, body = _request_json(base, "/health")
                    self.assertEqual(status, 401)
                    self.assertEqual(body, {"ok": False, "error": "unauthorized"})

                    status, body = _request_json(base, "/health", token="wrong-token")
                    self.assertEqual(status, 401)
                    self.assertEqual(body["error"], "unauthorized")

                    status, health = _request_json(base, "/health", token=token)
                    self.assertEqual(status, 200)
                    self.assertTrue(health["ok"])
                    self.assertTrue(health["ready"])
                    self.assertEqual(health["service"], pro_attach.SERVICE_NAME)
                    self.assertEqual(health["protocol_version"], pro_attach.PROTOCOL_VERSION)
                    self.assertEqual(health["session_id"], state["session_id"])

                    call = {
                        "protocol_version": pro_attach.PROTOCOL_VERSION,
                        "session_id": state["session_id"],
                        "request_id": "normal-current",
                        "expected_project": project_path,
                        "tool": "normal",
                        "arguments": {"aprx_path": "CURRENT", "value": 7},
                        "environment": {"ARCGIS_PRO_MCP_ALLOW_WRITE": "0"},
                    }

                    status, body = _request_json(
                        base,
                        "/call",
                        token=token,
                        payload=call,
                        content_type="text/plain",
                    )
                    self.assertEqual(status, 415)
                    self.assertIn("application/json", body["error"])

                    wrong_protocol = {**call, "protocol_version": pro_attach.PROTOCOL_VERSION + 1}
                    status, body = _request_json(
                        base,
                        "/call",
                        token=token,
                        payload=wrong_protocol,
                    )
                    self.assertEqual(status, 409)
                    self.assertEqual(body["protocol_version"], pro_attach.PROTOCOL_VERSION)

                    wrong_session = {**call, "session_id": "stale-session"}
                    status, body = _request_json(
                        base,
                        "/call",
                        token=token,
                        payload=wrong_session,
                    )
                    self.assertEqual(status, 409)
                    self.assertEqual(body["session_id"], state["session_id"])

                    status, body = _request_json(base, "/call", token=token, payload=call)
                    self.assertEqual(status, 200)
                    self.assertTrue(body["ok"])
                    self.assertEqual(body["request_id"], "normal-current")
                    self.assertEqual(body["result"], "executed:normal")
                    self.assertEqual(invoke_calls[0]["arguments"]["aprx_path"], "CURRENT")
                    self.assertIs(invoke_calls[0]["current_project"], project)
                    self.assertEqual(invoke_calls[0]["current_project_path"], project_path)

                    blocking_call = {
                        **call,
                        "request_id": "blocking-current",
                        "tool": "blocking",
                    }
                    blocking_client = threading.Thread(
                        target=run_blocking_call,
                        args=(base, token, blocking_call),
                        name="test-pro-host-client",
                        daemon=True,
                    )
                    blocking_client.start()
                    self.assertTrue(blocking_started.wait(timeout=2.0))

                    status, body = _request_json(base, "/stop", token=token, payload={})
                    self.assertEqual(status, 200)
                    self.assertTrue(body["stopping"])

                    after_stop = {
                        **call,
                        "request_id": "must-not-run",
                        "tool": "after-stop",
                    }
                    status, body = _request_json(
                        base,
                        "/call",
                        token=token,
                        payload=after_stop,
                    )
                    self.assertEqual(status, 503)
                    self.assertIn("stopping", body["error"])

                    release_blocking.set()
                    blocking_client.join(timeout=2.0)
                    self.assertFalse(blocking_client.is_alive())
                    self.assertEqual(blocking_errors, [])
                    self.assertEqual(blocking_response[0][0], 200)
                    self.assertEqual(blocking_response[0][1]["result"], "executed:blocking")

                    host_thread.join(timeout=3.0)
                    self.assertFalse(host_thread.is_alive())
                    self.assertEqual(host_errors, [])
                    self.assertFalse(state_path.exists())
                    self.assertEqual([item["name"] for item in invoke_calls], ["normal", "blocking"])
                finally:
                    release_blocking.set()
                    if blocking_client is not None:
                        blocking_client.join(timeout=2.0)
                    if host_thread.is_alive() and token:
                        try:
                            _request_json(base, "/stop", token=token, payload={})
                        except OSError:
                            pass
                    host_thread.join(timeout=3.0)

    def test_call_stop_admission_race_never_leaves_an_orphan_job(self) -> None:
        invoked: list[str] = []

        def invoke_tool(
            name: str,
            arguments: dict[str, Any],
            environment: dict[str, Any] | None = None,
            current_project: Any | None = None,
            current_project_path: str = "",
        ) -> str:
            invoked.append(name)
            return f"executed:{name}"

        with _running_host(invoke_tool, job_timeout=0.4) as host:
            original_init = pro_host._Job.__init__
            job_constructed = threading.Event()
            release_job = threading.Event()
            call_response: list[tuple[int, dict[str, Any]]] = []
            call_errors: list[BaseException] = []

            def gated_init(
                job: pro_host._Job,
                payload: dict[str, Any],
                request_id: str,
            ) -> None:
                original_init(job, payload, request_id)
                if request_id == "admission-race":
                    job_constructed.set()
                    if not release_job.wait(timeout=3.0):
                        raise RuntimeError("test did not release job construction")

            def issue_call() -> None:
                try:
                    call_response.append(
                        _request_json(
                            host.base,
                            "/call",
                            token=host.token,
                            payload=_valid_call(host, "admission-race", "must-not-run"),
                        )
                    )
                except BaseException as exc:  # noqa: BLE001
                    call_errors.append(exc)

            client = threading.Thread(target=issue_call, name="test-admission-client", daemon=True)
            with patch.object(pro_host._Job, "__init__", new=gated_init):
                client.start()
                try:
                    self.assertTrue(job_constructed.wait(timeout=2.0))
                    status, stop_body = _request_json(
                        host.base,
                        "/stop",
                        token=host.token,
                        payload={},
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(stop_body["stopping"])

                    host.thread.join(timeout=2.0)
                    self.assertFalse(host.thread.is_alive())
                    self.assertFalse(host.state_path.exists())
                finally:
                    release_job.set()
                    client.join(timeout=2.0)

            self.assertFalse(client.is_alive())
            self.assertEqual(call_errors, [])
            self.assertEqual(call_response[0][0], 503)
            self.assertIn("stopping", call_response[0][1]["error"])
            self.assertEqual(invoked, [])
            self.assertEqual(host.errors, [])

    def test_keyboard_interrupt_replies_to_active_call_and_stops_host(self) -> None:
        def interrupt_tool(
            name: str,
            arguments: dict[str, Any],
            environment: dict[str, Any] | None = None,
            current_project: Any | None = None,
            current_project_path: str = "",
        ) -> str:
            raise KeyboardInterrupt("simulated active-job interrupt")

        with _running_host(interrupt_tool, job_timeout=1.0) as host:
            started_at = time.monotonic()
            status, body = _request_json(
                host.base,
                "/call",
                token=host.token,
                payload=_valid_call(host, "keyboard-interrupt", "interrupt"),
            )
            elapsed = time.monotonic() - started_at

            self.assertEqual(status, 200)
            self.assertFalse(body["ok"])
            self.assertEqual(body["protocol_version"], pro_attach.PROTOCOL_VERSION)
            self.assertEqual(body["session_id"], host.state["session_id"])
            self.assertEqual(body["request_id"], "keyboard-interrupt")
            self.assertIn("simulated active-job interrupt", body["error"])
            self.assertLess(elapsed, host.job_timeout)

            host.thread.join(timeout=2.0)
            self.assertFalse(host.thread.is_alive())
            self.assertFalse(host.state_path.exists())
            self.assertEqual(host.errors, [])


if __name__ == "__main__":
    unittest.main()
