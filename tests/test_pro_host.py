from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import pro_attach, pro_host, server


class ProHostTests(unittest.TestCase):
    def test_window_host_endpoint_is_exclusive(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:
                return

        first = pro_host._ExclusiveThreadingHTTPServer(("127.0.0.1", 0), Handler)
        try:
            port = int(first.server_address[1])
            with self.assertRaises(OSError):
                pro_host._ExclusiveThreadingHTTPServer(("127.0.0.1", port), Handler)
        finally:
            first.server_close()

    def test_client_environment_applies_allowlist_and_restores_process(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_EXPORT_ROOT": r"C:\\before",
            },
            clear=True,
        ):
            with pro_host._client_environment({"ARCGIS_PRO_MCP_ALLOW_WRITE": "0"}):
                self.assertEqual(os.environ["ARCGIS_PRO_MCP_ALLOW_WRITE"], "0")
                self.assertNotIn("ARCGIS_PRO_MCP_EXPORT_ROOT", os.environ)
                self.assertEqual(os.environ[pro_host.ENV_IN_HOST], "1")
            self.assertEqual(os.environ["ARCGIS_PRO_MCP_ALLOW_WRITE"], "1")
            self.assertEqual(os.environ["ARCGIS_PRO_MCP_EXPORT_ROOT"], r"C:\\before")
            self.assertNotIn(pro_host.ENV_IN_HOST, os.environ)

    def test_client_environment_rejects_unknown_keys(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "不允许的键"):
            with pro_host._client_environment({"PATH": "bad"}):
                pass

    def test_current_project_rejects_unsaved_project_identity(self) -> None:
        fake_arcpy = SimpleNamespace(
            mp=SimpleNamespace(ArcGISProject=lambda _target: SimpleNamespace(filePath=""))
        )
        with patch.dict(sys.modules, {"arcpy": fake_arcpy}):
            with self.assertRaisesRegex(RuntimeError, "尚未保存"):
                pro_host._current_project()

    def test_invoke_tool_accepts_only_current_project(self) -> None:
        calls: list[str] = []

        def tool_fn(aprx_path: str) -> str:
            calls.append(aprx_path)
            return f"{aprx_path}:{os.environ.get('ARCGIS_PRO_MCP_ALLOW_WRITE')}"

        manager = SimpleNamespace(get_tool=lambda name: SimpleNamespace(fn=tool_fn))
        fake_mcp = SimpleNamespace(_tool_manager=manager)
        with patch.object(server, "mcp", fake_mcp):
            with self.assertRaisesRegex(RuntimeError, "只接受 aprx_path=CURRENT"):
                pro_host._invoke_tool("demo", {"aprx_path": r"C:\\demo.aprx"}, {})
            with self.assertRaisesRegex(RuntimeError, "必须显式携带|只接受"):
                pro_host._invoke_tool("demo", {}, {})
            result = pro_host._invoke_tool(
                "demo",
                {"aprx_path": "current"},
                {"ARCGIS_PRO_MCP_ALLOW_WRITE": "0"},
            )
        self.assertEqual(result, "CURRENT:0")
        self.assertEqual(calls, ["CURRENT"])

    def test_clear_state_only_removes_own_session(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            path.write_text(json.dumps({"session_id": "new"}), encoding="utf-8")
            with patch.object(pro_host, "state_path", return_value=str(path)):
                pro_host._clear_state("old")
                self.assertTrue(path.exists())
                pro_host._clear_state("new")
                self.assertFalse(path.exists())

    def test_job_cancel_and_claim_are_atomic(self) -> None:
        cancelled = pro_host._Job({}, "cancelled")
        self.assertTrue(cancelled.cancel_if_queued())
        self.assertFalse(cancelled.claim())

        running = pro_host._Job({}, "running")
        self.assertTrue(running.claim())
        self.assertFalse(running.cancel_if_queued())
        running.finish("SUCCEEDED")
        self.assertEqual(running.state, "SUCCEEDED")

        expired = pro_host._Job({}, "expired")
        expired.deadline = 0
        self.assertFalse(expired.claim())
        self.assertEqual(expired.state, "CANCELLED")

    def test_default_single_instance_and_explicit_port_state_paths_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with (
                patch.dict(os.environ, {}, clear=False),
                patch.object(pro_attach.tempfile, "gettempdir", return_value=root),
            ):
                os.environ.pop(pro_attach.ENV_PORT, None)
                default_path = pro_attach.state_path()
                self.assertEqual(pro_host._candidate_ports(), [pro_attach.DEFAULT_PORT])

                with patch.dict(
                    os.environ,
                    {pro_attach.ENV_PORT: str(pro_attach.DEFAULT_PORT)},
                ):
                    self.assertEqual(pro_attach.state_path(), default_path)
                    self.assertEqual(pro_host._candidate_ports(), [pro_attach.DEFAULT_PORT])

                explicit_ports = (19001, 19002)
                explicit_paths: list[str] = []
                for port in explicit_ports:
                    with patch.dict(os.environ, {pro_attach.ENV_PORT: str(port)}):
                        path = pro_attach.state_path()
                        explicit_paths.append(path)
                        self.assertEqual(path, pro_attach.state_path())
                        self.assertEqual(pro_host._candidate_ports(), [port])

            self.assertEqual(len({default_path, *explicit_paths}), 3)
            self.assertEqual(Path(default_path).name, pro_attach.STATE_NAME)
            self.assertIn("19001", Path(explicit_paths[0]).name)
            self.assertIn("19002", Path(explicit_paths[1]).name)



    def test_write_console_survives_cp1252_stdout(self) -> None:
        import io

        buf = io.BytesIO()
        narrow = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
        with patch.object(sys, "stdout", narrow):
            pro_host._write_console("已接入 ArcGIS Pro 当前窗口\n")
            pro_host._write_console("窗口宿主已停止。\n")
        self.assertGreater(buf.tell(), 0)

    def test_prefer_replace_errors_makes_print_safe_on_cp1252(self) -> None:
        import io

        buf = io.BytesIO()
        narrow = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
        with patch.object(sys, "stdout", narrow), patch.object(sys, "stderr", narrow):
            # Without replace-mode, this print raises UnicodeEncodeError on cp1252.
            with self.assertRaises(UnicodeEncodeError):
                print("工程：demo")
            pro_host._prefer_replace_errors_on_stdio()
            print("工程：demo")


if __name__ == "__main__":
    unittest.main()
