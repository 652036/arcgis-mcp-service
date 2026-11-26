from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mcp.server.fastmcp.tools import Tool

from arcgis_pro_mcp import pro_attach, tool_protocol
from arcgis_pro_mcp.tools import (
    AVAILABLE_TOOL_PROFILES,
    CORE_PROFILE_TOOLS,
    TOOL_PROFILE_ENV,
    parse_tool_profile,
)


def _fresh_import_server(profile: str | None = None):
    """Import arcgis_pro_mcp.server freshly under an isolated ARCGIS_PRO_MCP_TOOL_PROFILE."""
    env_override: dict[str, str] = {}
    if profile is not None:
        env_override[TOOL_PROFILE_ENV] = profile
    previous = sys.modules.pop("arcgis_pro_mcp.server", None)
    try:
        with patch.dict(os.environ, env_override, clear=(profile is None)):
            return importlib.import_module("arcgis_pro_mcp.server"), previous
    except Exception:
        sys.modules.pop("arcgis_pro_mcp.server", None)
        if previous is not None:
            sys.modules["arcgis_pro_mcp.server"] = previous
        raise


def _restore_server(previous: object | None) -> None:
    sys.modules.pop("arcgis_pro_mcp.server", None)
    if previous is not None:
        sys.modules["arcgis_pro_mcp.server"] = previous  # type: ignore[assignment]


class ToolProfileTests(unittest.TestCase):
    def test_parse_tool_profile_defaults_combinations_and_invalid(self) -> None:
        default_profile = parse_tool_profile("")
        self.assertEqual(default_profile.profile, "full")
        self.assertTrue(default_profile.include_all)

        core = parse_tool_profile("core")
        self.assertEqual(core.profile, "core")
        self.assertFalse(core.include_all)
        self.assertEqual(core.explicit_tools, CORE_PROFILE_TOOLS)

        combo = parse_tool_profile(" core , Editing , RASTER ")
        self.assertEqual(combo.profile, "core,editing,raster")
        self.assertIn("editing", combo.domains)
        self.assertIn("raster", combo.domains)
        self.assertEqual(combo.explicit_tools, CORE_PROFILE_TOOLS)

        aliased = parse_tool_profile("core,sdk,stats,publishing")
        self.assertEqual(aliased.profile, "core,enterprise,stats_network,cartography")

        for bad in ("unknown_profile", "core,", ",editing", "core,nope"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_tool_profile(bad)

    def test_core_profile_shrinks_tools_list_under_90kb_and_keeps_python_exports(self) -> None:
        srv, prev = _fresh_import_server("core")
        try:
            tools = asyncio.run(srv.mcp.list_tools())
            registered_names = {t.name for t in tools}
            self.assertEqual(registered_names, CORE_PROFILE_TOOLS)
            self.assertGreaterEqual(len(tools), 50)
            self.assertLessEqual(len(tools), 75)

            payload = [
                t.model_dump(mode="json", by_alias=True, exclude_none=True)
                for t in tools
            ]
            byte_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            self.assertLess(byte_size, 90_000)

            # Backwards compatibility: non-core Python callables stay exported on server
            self.assertTrue(callable(getattr(srv, "arcgis_pro_sdk_bridge_status", None)))
            self.assertTrue(callable(getattr(srv, "arcgis_pro_create_version", None)))

            # Capabilities and environment info report active profile metadata
            with patch.object(srv, "_window_status_fields", return_value={}):
                caps = json.loads(srv.arcgis_pro_server_capabilities())
            self.assertEqual(caps["tool_profile"], "core")
            self.assertEqual(caps["registered_tool_count"], len(CORE_PROFILE_TOOLS))
            self.assertEqual(caps["available_tool_profiles"], list(AVAILABLE_TOOL_PROFILES))
            self.assertEqual(set(caps["registered_tools"]), CORE_PROFILE_TOOLS)

            fake_arcpy = SimpleNamespace(
                GetInstallInfo=lambda: {"Version": "3.4"},
                ProductInfo=lambda: "ArcInfo",
            )
            with patch.object(srv, "_arcpy", return_value=fake_arcpy), patch.object(
                srv, "_window_status_fields", return_value={}
            ):
                env_info = json.loads(srv.arcgis_pro_environment_info())
            self.assertEqual(env_info["tool_profile"], "core")
            self.assertEqual(env_info["registered_tool_count"], len(CORE_PROFILE_TOOLS))
            self.assertEqual(env_info["available_tool_profiles"], list(AVAILABLE_TOOL_PROFILES))

            # install_stdio_proxy wraps only registered tools in core mode
            with patch.object(pro_attach, "in_pro_host", return_value=False):
                wrapped_count = pro_attach.install_stdio_proxy(srv.mcp)
            self.assertEqual(wrapped_count, len(CORE_PROFILE_TOOLS))
        finally:
            _restore_server(prev)

    def test_comma_separated_domain_profile_registers_union(self) -> None:
        srv, prev = _fresh_import_server("core,editing,raster")
        try:
            registered = set(srv.mcp._tool_manager._tools)
            self.assertTrue(CORE_PROFILE_TOOLS.issubset(registered))
            self.assertIn("arcgis_pro_edit_apply", registered)
            self.assertIn("arcgis_pro_create_domain", registered)
            self.assertIn("arcgis_pro_raster_watershed", registered)
            self.assertIn("arcgis_pro_create_mosaic_dataset", registered)
            self.assertNotIn("arcgis_pro_sdk_bridge_status", registered)
            self.assertNotIn("arcgis_pro_create_version", registered)
            self.assertEqual(srv._ACTIVE_TOOL_PROFILE, "core,editing,raster")
        finally:
            _restore_server(prev)

    def test_invalid_profile_env_fails_closed_on_import(self) -> None:
        with self.assertRaises(ValueError):
            _fresh_import_server("core,does_not_exist")

    def test_single_pass_schema_build_and_idempotent_finalize(self) -> None:
        orig_from_function = Tool.from_function
        call_count = 0

        def counting_from_function(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return orig_from_function(*args, **kwargs)

        with patch.object(Tool, "from_function", side_effect=counting_from_function):
            srv, prev = _fresh_import_server("core")
            try:
                registered_count = len(srv.mcp._tool_manager._tools)
                self.assertEqual(call_count, registered_count)
                # Calling finalize_tool_registry again performs ZERO schema rebuilds
                tool_protocol.finalize_tool_registry(srv.mcp)
                self.assertEqual(call_count, registered_count)
            finally:
                _restore_server(prev)

        call_count = 0
        with patch.object(Tool, "from_function", side_effect=counting_from_function):
            srv, prev = _fresh_import_server("full")
            try:
                registered_count = len(srv.mcp._tool_manager._tools)
                self.assertEqual(registered_count, 460)
                self.assertEqual(call_count, 460)
                tool_protocol.finalize_tool_registry(srv.mcp)
                self.assertEqual(call_count, 460)
            finally:
                _restore_server(prev)


class ZeroCopyRedactionHotPathTests(unittest.TestCase):
    def test_structured_call_redacts_once_without_json_roundtrip(self) -> None:
        from arcgis_pro_mcp import server
        from arcgis_pro_mcp.redaction import redact_text_values

        def sample_tool() -> str:
            return server._json_dumps(
                {
                    "ok": True,
                    "url": "https://alice:topsecret@portal.example.com/arcgis",
                    "note": "password=my_secret_pw",
                    "custom_obj": Path("C:/demo/token=abc123.aprx"),
                }
            )

        wrapped = tool_protocol._structured_callable(sample_tool)

        # Direct Python call returns indented JSON str and redacts secrets
        direct = sample_tool()
        self.assertIsInstance(direct, str)
        self.assertIn("\n  ", direct)
        self.assertNotIn("topsecret", direct)
        self.assertNotIn("my_secret_pw", direct)

        # Structured MCP call bypasses json.dumps, json.loads, and second redaction
        with patch.object(
            server,
            "_redact_text_values",
            wraps=redact_text_values,
        ) as srv_redact, patch.object(
            tool_protocol,
            "redact_text_values",
            wraps=redact_text_values,
        ) as tp_redact, patch.object(
            server.json,
            "dumps",
            side_effect=AssertionError("json.dumps must not run inside structured MCP call"),
        ), patch.object(
            tool_protocol.json,
            "loads",
            side_effect=AssertionError("json.loads must not run for _PreRedactedPayload"),
        ):
            result = wrapped()

        self.assertIsInstance(result, dict)
        self.assertEqual(srv_redact.call_count, 1)
        self.assertEqual(tp_redact.call_count, 0)
        self.assertNotIn("topsecret", repr(result))
        self.assertNotIn("my_secret_pw", repr(result))
        self.assertNotIn("abc123", repr(result))
        self.assertIsInstance(result["custom_obj"], str)

    def test_non_json_dumps_returns_still_redact_in_structured_wrapper(self) -> None:
        raw_str_fn = tool_protocol._structured_callable(
            lambda: '{"secret_msg": "Bearer super_secret_token_999"}'
        )
        raw_dict_fn = tool_protocol._structured_callable(
            lambda: {"detail": "https://bob:hunter2@db.internal/sde"}
        )

        str_res = raw_str_fn()
        dict_res = raw_dict_fn()

        self.assertNotIn("super_secret_token_999", repr(str_res))
        self.assertNotIn("hunter2", repr(dict_res))


class ProjectCacheLruTests(unittest.TestCase):
    def setUp(self) -> None:
        from arcgis_pro_mcp import server

        self._server = server
        self._orig_max = server._PROJECT_CACHE_MAX_SIZE
        server.clear_project_cache()

    def tearDown(self) -> None:
        self._server.configure_project_cache(self._orig_max)
        self._server.clear_project_cache()

    def test_project_cache_is_bounded_lru_ordered_dict(self) -> None:
        srv = self._server
        self.assertIsInstance(srv._PROJECT_CACHE, OrderedDict)
        self.assertEqual(srv.DEFAULT_MAX_PROJECT_CACHE, 16)
        self.assertEqual(srv.configure_project_cache(3), 3)

        with tempfile.TemporaryDirectory() as root:
            paths = []
            for name in ("a.aprx", "b.aprx", "c.aprx", "d.aprx"):
                p = Path(root) / name
                p.touch()
                paths.append(str(p))

            opened = {os.path.normpath(p): SimpleNamespace(name=p) for p in paths}
            arcgis_project = MagicMock(side_effect=lambda p: opened[os.path.normpath(p)])
            fake_arcpy = SimpleNamespace(mp=SimpleNamespace(ArcGISProject=arcgis_project))

            with patch.dict(
                os.environ,
                {"ARCGIS_PRO_MCP_PROJECT_ROOTS": root},
                clear=True,
            ), patch.object(srv, "_arcpy", return_value=fake_arcpy):
                srv._open_project(paths[0])  # [a]
                srv._open_project(paths[1])  # [a, b]
                srv._open_project(paths[2])  # [a, b, c]
                # Touch 'a' so 'b' becomes the LRU entry
                srv._open_project(paths[0])  # [b, c, a]
                # Opening 'd' evicts 'b'
                srv._open_project(paths[3])  # [c, a, d]

            stats = srv.project_cache_stats()
            self.assertEqual(stats["size"], 3)
            self.assertEqual(stats["max_size"], 3)
            self.assertEqual(
                stats["keys"],
                [
                    srv._project_cache_key(paths[2]),
                    srv._project_cache_key(paths[0]),
                    srv._project_cache_key(paths[3]),
                ],
            )
            self.assertNotIn(srv._project_cache_key(paths[1]), srv._PROJECT_CACHE)

            # Shrinking capacity evicts oldest entries immediately
            srv.configure_project_cache(1)
            self.assertEqual(
                list(srv._PROJECT_CACHE.keys()),
                [srv._project_cache_key(paths[3])],
            )

        with self.assertRaises(ValueError):
            srv.configure_project_cache(0)

    def test_project_cache_thread_safety_under_concurrent_opens(self) -> None:
        srv = self._server
        srv.configure_project_cache(4)

        with tempfile.TemporaryDirectory() as root:
            paths = []
            for idx in range(12):
                p = Path(root) / f"proj_{idx}.aprx"
                p.touch()
                paths.append(str(p))

            fake_arcpy = SimpleNamespace(
                mp=SimpleNamespace(ArcGISProject=lambda p: SimpleNamespace(path=p))
            )
            errors: list[BaseException] = []

            def worker(worker_id: int) -> None:
                try:
                    for step in range(25):
                        srv._open_project(paths[(worker_id + step) % len(paths)])
                        srv.project_cache_stats()
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            with patch.dict(
                os.environ,
                {"ARCGIS_PRO_MCP_PROJECT_ROOTS": root},
                clear=True,
            ), patch.object(srv, "_arcpy", return_value=fake_arcpy):
                threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            self.assertEqual(errors, [])
            self.assertLessEqual(len(srv._PROJECT_CACHE), 4)


if __name__ == "__main__":
    unittest.main()
