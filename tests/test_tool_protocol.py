from __future__ import annotations

import ast
import importlib
import inspect
import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any, get_origin


class _CompatTool:
    """Small mcp<2 Tool contract used only when the global environment has mcp 2.x."""

    def __init__(
        self,
        fn: Any,
        *,
        name: str,
        title: str | None,
        description: str | None,
        annotations: Any,
        icons: Any,
        meta: dict[str, Any] | None,
        structured_output: bool,
    ) -> None:
        self.fn = fn
        self.name = name
        self.title = title
        self.description = description
        self.annotations = annotations
        self.icons = icons
        self.meta = meta
        self.structured_output = structured_output
        self.parameters = {"type": "object", "properties": {}}
        return_type = inspect.signature(fn).return_annotation
        is_mapping = return_type is dict or get_origin(return_type) is dict
        self.output_schema = (
            {"type": "object", "additionalProperties": True}
            if is_mapping
            else {"type": "string"}
        )

    @classmethod
    def from_function(
        cls,
        fn: Any,
        *,
        name: str,
        title: str | None = None,
        description: str | None = None,
        annotations: Any = None,
        icons: Any = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool = False,
        **kwargs: Any,
    ) -> _CompatTool:
        del kwargs
        return cls(
            fn,
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )


class _CompatToolManager:
    def __init__(self) -> None:
        self._tools: dict[str, _CompatTool] = {}

    def list_tools(self) -> list[_CompatTool]:
        return list(self._tools.values())

    def get_tool(self, name: str) -> _CompatTool | None:
        return self._tools.get(name)


class _CompatFastMCP:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._tool_manager = _CompatToolManager()

    def tool(
        self,
        *,
        name: str,
        description: str = "",
        **kwargs: Any,
    ) -> Any:
        del kwargs

        def register(fn: Any) -> Any:
            self._tool_manager._tools[name] = _CompatTool.from_function(
                fn,
                name=name,
                description=description,
            )
            return fn

        return register


def _declared_tool_names(server_path: str) -> list[str]:
    tree = ast.parse(Path(server_path).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute) or decorator.func.attr != "tool":
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.append(str(keyword.value.value))
    return names


def _annotation_bool(annotation: Any, snake_name: str, alias_name: str) -> bool | None:
    value = getattr(annotation, snake_name, None)
    if value is None:
        value = getattr(annotation, alias_name, None)
    return value


class ToolProtocolTests(unittest.TestCase):
    def test_structured_result_coercion_wraps_every_return_shape(self) -> None:
        from arcgis_pro_mcp.tool_protocol import _coerce_structured_result

        self.assertEqual(_coerce_structured_result(None), {"ok": True})
        self.assertEqual(_coerce_structured_result([1, 2]), {"ok": True, "result": [1, 2]})
        self.assertEqual(_coerce_structured_result(False), {"ok": True, "result": False})
        self.assertEqual(_coerce_structured_result(7), {"ok": True, "result": 7})

    def test_high_risk_tool_policy_matches_runtime_gates(self) -> None:
        from arcgis_pro_mcp.tool_protocol import tool_policy

        for name in (
            "arcgis_pro_gp_calculate_field",
            "arcgis_pro_gp_calculate_geometry",
            "arcgis_pro_gp_delete_field",
            "arcgis_pro_remove_join",
            "arcgis_pro_release_project",
            "arcgis_pro_reload_project",
        ):
            with self.subTest(name=name):
                policy = tool_policy(name)
                self.assertTrue(policy["destructive"])
                self.assertIn("ARCGIS_PRO_MCP_ALLOW_WRITE", policy["gates"])
                self.assertIn("ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE", policy["gates"])

        for name in (
            "arcgis_pro_set_label_font",
            "arcgis_pro_update_label_expression",
            "arcgis_pro_upsert_label_class",
        ):
            with self.subTest(name=name):
                self.assertIn("ARCGIS_PRO_MCP_ALLOW_CIM_WRITE", tool_policy(name)["gates"])

        for name in (
            "arcgis_pro_edit_preflight",
            "arcgis_pro_edit_geometry_preflight",
            "arcgis_pro_edit_workspace_preflight",
            "arcgis_pro_connection_repair_preflight",
        ):
            with self.subTest(name=name):
                policy = tool_policy(name)
                self.assertTrue(policy["read_only"])
                self.assertNotIn("ARCGIS_PRO_MCP_ALLOW_WRITE", policy["gates"])
                self.assertNotIn("ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE", policy["gates"])

        generic_gates = set(tool_policy("arcgis_pro_gp_run_tool")["gates"])
        self.assertTrue(
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE",
                "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP",
                "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST",
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT",
            }.issubset(generic_gates)
        )
        self.assertIn(
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT",
            tool_policy("arcgis_pro_current_map_run_analysis")["gates"],
        )

        for name in (
            "arcgis_pro_get_artifact_digest",
            "arcgis_pro_create_sharing_draft",
            "arcgis_pro_stage_service_definition",
            "arcgis_pro_publish_service_definition",
        ):
            with self.subTest(name=name):
                self.assertIn("ARCGIS_PRO_MCP_EXPORT_ROOT", tool_policy(name)["gates"])

        import_document = tool_policy("arcgis_pro_import_document")
        self.assertIn(
            {"when": "log_files=true", "gate": "ARCGIS_PRO_MCP_EXPORT_ROOT"},
            import_document["conditional_gates"],
        )
        for name in ("arcgis_pro_reconcile_versions", "arcgis_pro_post_version"):
            with self.subTest(name=name):
                self.assertIn(
                    {
                        "when": "out_log_path is provided",
                        "gate": "ARCGIS_PRO_MCP_EXPORT_ROOT",
                    },
                    tool_policy(name)["conditional_gates"],
                )

        add_rasters = tool_policy("arcgis_pro_add_rasters_to_mosaic_dataset")
        self.assertIn(
            {
                "when": "duplicate_items_action=OVERWRITE_DUPLICATES",
                "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
            },
            add_rasters["conditional_gates"],
        )
        db_connection = tool_policy("arcgis_pro_create_db_connection")
        self.assertTrue(db_connection["open_world"])
        self.assertIn("ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST", db_connection["gates"])

        for name in (
            "arcgis_pro_gp_count_overlapping_features",
            "arcgis_pro_gp_check_geometry",
        ):
            with self.subTest(name=name):
                gates = set(tool_policy(name)["gates"])
                self.assertIn("ARCGIS_PRO_MCP_ALLOW_WRITE", gates)
                self.assertIn("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT", gates)

        detach = tool_policy("arcgis_pro_detach_window")
        self.assertFalse(detach["destructive"])
        self.assertIn("ARCGIS_PRO_MCP_ALLOW_WRITE", detach["gates"])

        for name in (
            "arcgis_pro_export_active_view_image",
            "arcgis_pro_export_bookmarks",
            "arcgis_pro_export_chart",
            "arcgis_pro_export_map_series_pdf",
            "arcgis_pro_export_map_to_image",
            "arcgis_pro_export_mapx",
            "arcgis_pro_export_report_pdf",
            "arcgis_pro_export_topology_errors",
            "arcgis_pro_gp_export_features",
            "arcgis_pro_gp_export_table",
            "arcgis_pro_paste_layer_properties",
            "arcgis_pro_save_layer_file",
            "arcgis_pro_upsert_definition_query",
        ):
            with self.subTest(name=name):
                self.assertIn("ARCGIS_PRO_MCP_ALLOW_WRITE", tool_policy(name)["gates"])

    def test_every_registered_tool_has_structured_discovery_metadata(self) -> None:
        """Import the real registry, with a narrow mcp<2 shim only on mcp 2.x hosts."""
        previous_fastmcp = sys.modules.get("mcp.server.fastmcp")
        previous_server = sys.modules.pop("arcgis_pro_mcp.server", None)
        shim_installed = False
        try:
            try:
                importlib.import_module("mcp.server.fastmcp")
            except ModuleNotFoundError:
                shim = types.ModuleType("mcp.server.fastmcp")
                shim.FastMCP = _CompatFastMCP
                sys.modules["mcp.server.fastmcp"] = shim
                shim_installed = True

            server = importlib.import_module("arcgis_pro_mcp.server")
            tools = list(server.mcp._tool_manager.list_tools())
            registered_names = [tool.name for tool in tools]
            declared_names = _declared_tool_names(server.__file__)

            self.assertGreater(len(tools), 300)
            self.assertEqual(len(registered_names), len(set(registered_names)))
            self.assertEqual(set(registered_names), set(declared_names))

            for tool in tools:
                with self.subTest(tool=tool.name):
                    self.assertTrue((tool.title or "").strip())
                    self.assertTrue((tool.description or "").strip())
                    self.assertEqual(tool.output_schema.get("type"), "object")
                    self.assertIsNotNone(tool.annotations)
                    self.assertIsInstance(
                        _annotation_bool(tool.annotations, "read_only_hint", "readOnlyHint"),
                        bool,
                    )
                    self.assertIsInstance(
                        _annotation_bool(tool.annotations, "destructive_hint", "destructiveHint"),
                        bool,
                    )
                    self.assertIsInstance(
                        _annotation_bool(tool.annotations, "idempotent_hint", "idempotentHint"),
                        bool,
                    )
                    self.assertIsInstance(
                        _annotation_bool(tool.annotations, "open_world_hint", "openWorldHint"),
                        bool,
                    )
                    arcgis_meta = (tool.meta or {}).get("arcgisPro")
                    self.assertIsInstance(arcgis_meta, dict)
                    self.assertIsInstance(arcgis_meta.get("requiresCurrent"), bool)
                    self.assertIsInstance(arcgis_meta.get("requiresSdkBridge"), bool)
                    self.assertIsInstance(arcgis_meta.get("gates"), list)

            by_name = {tool.name: tool for tool in tools}
            self.assertTrue(by_name["arcgis_pro_active_view_info"].meta["arcgisPro"]["requiresCurrent"])
            self.assertTrue(by_name["arcgis_pro_window_job_submit"].meta["arcgisPro"]["requiresCurrent"])
            self.assertFalse(by_name["arcgis_pro_list_maps"].meta["arcgisPro"]["requiresCurrent"])
            self.assertFalse(
                by_name["arcgis_pro_sdk_bridge_status"].meta["arcgisPro"]["requiresSdkBridge"]
            )
            self.assertTrue(
                by_name["arcgis_pro_sdk_edit_undo"].meta["arcgisPro"]["requiresSdkBridge"]
            )
            self.assertTrue(
                _annotation_bool(
                    by_name["arcgis_pro_sdk_context"].annotations,
                    "read_only_hint",
                    "readOnlyHint",
                )
            )
            sdk_delete = by_name["arcgis_pro_sdk_delete_selected_features"]
            self.assertTrue(
                _annotation_bool(
                    sdk_delete.annotations,
                    "destructive_hint",
                    "destructiveHint",
                )
            )
            self.assertEqual(
                set(sdk_delete.meta["arcgisPro"]["gates"]),
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE",
                    "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
                    "ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS",
                },
            )

            add_rule = by_name["arcgis_pro_add_attribute_rule"]
            self.assertTrue(
                _annotation_bool(
                    add_rule.annotations,
                    "destructive_hint",
                    "destructiveHint",
                )
            )
            self.assertEqual(
                set(add_rule.meta["arcgisPro"]["gates"]),
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE",
                    "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
                },
            )
            self.assertIn(
                "ARCGIS_PRO_MCP_INPUT_ROOTS",
                by_name["arcgis_pro_import_contingent_values"].meta["arcgisPro"]["gates"],
            )
            export_rule_gates = by_name["arcgis_pro_export_attribute_rules"].meta[
                "arcgisPro"
            ]["gates"]
            self.assertEqual(export_rule_gates, ["ARCGIS_PRO_MCP_EXPORT_ROOT"])

            list_maps_annotations = by_name["arcgis_pro_list_maps"].annotations
            self.assertTrue(
                _annotation_bool(list_maps_annotations, "read_only_hint", "readOnlyHint")
            )
            remove_annotations = by_name["arcgis_pro_remove_layer"].annotations
            self.assertTrue(
                _annotation_bool(remove_annotations, "destructive_hint", "destructiveHint")
            )

            direct = server.arcgis_pro_tool_info("arcgis_pro_environment_info")
            self.assertIsInstance(direct, str)
            self.assertEqual(json.loads(direct)["tool_count"], 1)

            registered_tool_info = by_name["arcgis_pro_tool_info"].fn(
                name="arcgis_pro_environment_info"
            )
            self.assertIsInstance(registered_tool_info, dict)
            self.assertEqual(registered_tool_info["tool_count"], 1)
            self.assertEqual(
                registered_tool_info["tools"][0]["output_schema"]["type"],
                "object",
            )
        finally:
            sys.modules.pop("arcgis_pro_mcp.server", None)
            if shim_installed:
                sys.modules.pop("mcp.server.fastmcp", None)
            if previous_fastmcp is not None:
                sys.modules["mcp.server.fastmcp"] = previous_fastmcp
            if previous_server is not None:
                sys.modules["arcgis_pro_mcp.server"] = previous_server


if __name__ == "__main__":
    unittest.main()
