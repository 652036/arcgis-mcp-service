from __future__ import annotations

import json
import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from mcp.server.fastmcp import FastMCP

from arcgis_pro_mcp import pro_attach, server


class ProAttachTests(unittest.TestCase):
    def setUp(self) -> None:
        pro_attach.clear_confirmed_target()

    def tearDown(self) -> None:
        pro_attach.clear_confirmed_target()

    def test_in_pro_host_reads_env(self) -> None:
        with patch.dict(os.environ, {pro_attach.ENV_IN_HOST: "1"}, clear=False):
            self.assertTrue(pro_attach.in_pro_host())
        with patch.dict(os.environ, {pro_attach.ENV_IN_HOST: ""}, clear=False):
            self.assertFalse(pro_attach.in_pro_host())

    def test_host_available_false_when_unreachable(self) -> None:
        with patch.dict(os.environ, {pro_attach.ENV_IN_HOST: ""}, clear=False), patch.object(
            pro_attach,
            "host_health",
            return_value=None,
        ):
            self.assertFalse(pro_attach.host_available())
            self.assertFalse(pro_attach.host_status()["window_attached"])

    def test_install_stdio_proxy_skips_inside_host(self) -> None:
        with patch.dict(os.environ, {pro_attach.ENV_IN_HOST: "1"}, clear=False):
            self.assertEqual(pro_attach.install_stdio_proxy(SimpleNamespace()), 0)

    def test_install_stdio_proxy_matches_real_fastmcp_manager(self) -> None:
        local_mcp = FastMCP("proxy-contract-test")

        @local_mcp.tool(name="probe_project")
        def probe_project(aprx_path: str) -> str:
            return f"local:{aprx_path}"

        with patch.dict(os.environ, {pro_attach.ENV_IN_HOST: ""}, clear=False):
            self.assertEqual(pro_attach.install_stdio_proxy(local_mcp), 1)
        tool = local_mcp._tool_manager.get_tool("probe_project")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.fn(aprx_path=r"C:\\demo.aprx"), r"local:C:\\demo.aprx")

    def test_install_stdio_proxy_wraps_tools(self) -> None:
        original = lambda **kwargs: "local"  # noqa: E731

        class Tool:
            def __init__(self) -> None:
                self.fn = original

        tool = Tool()

        class Manager:
            def list_tools(self):
                return [SimpleNamespace(name="arcgis_pro_list_maps")]

            def get_tool(self, name: str):
                assert name == "arcgis_pro_list_maps"
                return tool

        mcp = SimpleNamespace(_tool_manager=Manager())
        with patch.dict(os.environ, {pro_attach.ENV_IN_HOST: ""}, clear=False):
            count = pro_attach.install_stdio_proxy(mcp)
        self.assertEqual(count, 1)
        self.assertIsNot(tool.fn, original)
        with patch.object(pro_attach, "host_health") as health:
            self.assertEqual(tool.fn(aprx_path="C:\\a.aprx"), "local")
            health.assert_not_called()
        snapshot = {
            "ok": True,
            "ready": True,
            "service": pro_attach.SERVICE_NAME,
            "protocol_version": pro_attach.PROTOCOL_VERSION,
            "session_id": "session",
            "project": r"C:\\current.aprx",
        }
        with patch.object(pro_attach, "host_health", return_value=snapshot), patch.object(
            pro_attach,
            "host_call",
            return_value="from-host",
        ) as call:
            self.assertEqual(tool.fn(aprx_path="CURRENT"), "from-host")
            call.assert_called_once_with(
                "arcgis_pro_list_maps",
                {"aprx_path": "CURRENT"},
                snapshot,
            )

    def test_current_project_call_fails_closed_when_host_is_missing(self) -> None:
        original = lambda **kwargs: "local"  # noqa: E731

        class Tool:
            def __init__(self) -> None:
                self.fn = original

        tool = Tool()
        manager = SimpleNamespace(
            list_tools=lambda: [SimpleNamespace(name="arcgis_pro_list_maps")],
            get_tool=lambda name: tool,
        )
        with patch.dict(os.environ, {pro_attach.ENV_IN_HOST: ""}, clear=False):
            pro_attach.install_stdio_proxy(SimpleNamespace(_tool_manager=manager))
        with patch.object(pro_attach, "host_health", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "CURRENT"):
                tool.fn(aprx_path="CURRENT")

    def test_forwarded_environment_is_allowlisted(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "0",
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": r"C:\\safe",
                "UNRELATED_SECRET": "do-not-forward",
            },
            clear=True,
        ):
            payload = pro_attach.forwarded_environment()
        self.assertEqual(payload["ARCGIS_PRO_MCP_ALLOW_WRITE"], "0")
        self.assertEqual(payload["ARCGIS_PRO_MCP_GP_OUTPUT_ROOT"], r"C:\\safe")
        self.assertNotIn("UNRELATED_SECRET", payload)

    def test_host_health_requires_service_identity_and_protocol(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok": true}'

        with patch.object(pro_attach.urllib.request, "urlopen", return_value=Response()):
            self.assertIsNone(pro_attach.host_health())

    def test_host_health_uses_one_atomic_endpoint_snapshot(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "ok": True,
                        "ready": True,
                        "service": pro_attach.SERVICE_NAME,
                        "protocol_version": pro_attach.PROTOCOL_VERSION,
                        "package_version": pro_attach.PACKAGE_VERSION,
                    }
                ).encode()

        states = [
            {
                "service": pro_attach.SERVICE_NAME,
                "protocol_version": pro_attach.PROTOCOL_VERSION,
                "package_version": pro_attach.PACKAGE_VERSION,
                "port": 18001,
                "token": "token-a",
            },
            {
                "service": pro_attach.SERVICE_NAME,
                "protocol_version": pro_attach.PROTOCOL_VERSION,
                "package_version": pro_attach.PACKAGE_VERSION,
                "port": 18002,
                "token": "token-b",
            },
        ]
        with (
            patch.dict(os.environ, {pro_attach.ENV_PORT: "18001"}, clear=False),
            patch.object(pro_attach, "read_state", side_effect=states) as read,
            patch.object(
                pro_attach.urllib.request,
                "urlopen",
                return_value=Response(),
            ) as urlopen,
        ):
            health = pro_attach.host_health()
        self.assertEqual(read.call_count, 1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:18001/health")
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers[pro_attach.TOKEN_HEADER.lower()], "token-a")
        self.assertEqual(health["_endpoint_base"], "http://127.0.0.1:18001")

    def test_host_call_preserves_structured_http_error(self) -> None:
        body = BytesIO(b'{"ok": false, "error": "denied by policy"}')
        error = HTTPError("http://127.0.0.1/call", 403, "Forbidden", {}, body)
        snapshot = {
            "ok": True,
            "session_id": "session",
            "project": r"C:\\current.aprx",
        }
        pro_attach.confirm_host_target(snapshot)
        with patch.object(pro_attach.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "denied by policy"):
                pro_attach.host_call("arcgis_pro_list_maps", {"aprx_path": "CURRENT"}, snapshot)

    def test_current_call_requires_explicit_target_confirmation_and_rejects_restart(self) -> None:
        first = {
            "ok": True,
            "ready": True,
            "session_id": "session-a",
            "project": r"C:\\first.aprx",
        }
        with self.assertRaisesRegex(RuntimeError, "先调用 arcgis_pro_window_status"):
            pro_attach._require_confirmed_target(first)

        with patch.object(pro_attach, "host_health", return_value=first):
            status = pro_attach.host_status(confirm_target=True)
        self.assertTrue(status["target_confirmed"])

        restarted = {**first, "session_id": "session-b"}
        with self.assertRaisesRegex(RuntimeError, "会话或工程已变化"):
            pro_attach._require_confirmed_target(restarted)

    def test_invalid_or_mismatched_configured_port_never_uses_default_state(self) -> None:
        with patch.dict(os.environ, {pro_attach.ENV_PORT: "70000"}, clear=False), patch.object(
            pro_attach,
            "read_state",
        ) as read:
            with self.assertRaisesRegex(RuntimeError, "1-65535"):
                pro_attach._endpoint_snapshot()
            read.assert_not_called()

        state = {
            "service": pro_attach.SERVICE_NAME,
            "protocol_version": pro_attach.PROTOCOL_VERSION,
            "package_version": pro_attach.PACKAGE_VERSION,
            "port": 19002,
            "token": "wrong-endpoint-token",
        }
        with patch.dict(os.environ, {pro_attach.ENV_PORT: "19001"}, clear=False), patch.object(
            pro_attach,
            "read_state",
            return_value=state,
        ):
            endpoint = pro_attach._endpoint_snapshot()
        self.assertEqual(endpoint.base, "http://127.0.0.1:19001")
        self.assertEqual(endpoint.token, "")

    def test_environment_info_includes_window_status(self) -> None:
        fake_arcpy = SimpleNamespace(
            GetInstallInfo=lambda: {"Version": "3.6"},
            ProductInfo=lambda: "ArcInfo",
        )
        with patch.object(server, "_arcpy", return_value=fake_arcpy), patch.object(
            server,
            "_window_status_fields",
            return_value={"window_attached": False, "window_host": None},
        ):
            payload = json.loads(server.arcgis_pro_environment_info())
        self.assertFalse(payload["window_attached"])
        self.assertIn("allow_write", payload)
