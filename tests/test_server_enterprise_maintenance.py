from __future__ import annotations

import importlib
import inspect
import json
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from mcp.server.fastmcp import FastMCP as _FastMCP  # noqa: F401
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer

    _fastmcp = types.ModuleType("mcp.server.fastmcp")
    _fastmcp.FastMCP = MCPServer
    sys.modules["mcp.server.fastmcp"] = _fastmcp

server = importlib.import_module("arcgis_pro_mcp.server")


_ENTERPRISE_TOOLS = {
    "arcgis_pro_list_versions",
    "arcgis_pro_create_version",
    "arcgis_pro_change_version",
    "arcgis_pro_reconcile_versions",
    "arcgis_pro_post_version",
    "arcgis_pro_delete_version",
    "arcgis_pro_register_as_versioned",
    "arcgis_pro_unregister_as_versioned",
    "arcgis_pro_dataset_maintenance_info",
    "arcgis_pro_add_index",
    "arcgis_pro_remove_index",
    "arcgis_pro_rebuild_indexes",
    "arcgis_pro_analyze_datasets",
    "arcgis_pro_enable_editor_tracking",
    "arcgis_pro_disable_editor_tracking",
    "arcgis_pro_add_global_ids",
}


class ServerEnterpriseMaintenanceTests(unittest.TestCase):
    def tearDown(self) -> None:
        server._PROJECT_CACHE.clear()

    def test_all_enterprise_and_maintenance_tools_are_registered(self) -> None:
        registered = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        self.assertTrue(_ENTERPRISE_TOOLS.issubset(registered), _ENTERPRISE_TOOLS - registered)

    def test_capabilities_publish_enterprise_and_destructive_gate_catalogs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
            },
            clear=True,
        ), patch.object(server, "_window_status_fields", return_value={}):
            payload = json.loads(server.arcgis_pro_server_capabilities())
        self.assertTrue(payload["allow_enterprise_write"])
        self.assertTrue(payload["allow_destructive"])
        self.assertIn(
            "arcgis_pro_create_version",
            payload["tools_require_allow_enterprise_write"],
        )
        for name in (
            "arcgis_pro_reconcile_versions",
            "arcgis_pro_post_version",
            "arcgis_pro_delete_version",
            "arcgis_pro_unregister_as_versioned",
            "arcgis_pro_remove_index",
            "arcgis_pro_disable_editor_tracking",
        ):
            with self.subTest(name=name):
                self.assertIn(name, payload["tools_require_allow_destructive"])
        self.assertIn("arcgis_pro_list_versions", payload["tools_read_only"])
        self.assertIn(
            "arcgis_pro_dataset_maintenance_info",
            payload["tools_read_only"],
        )

    def test_change_version_validates_member_type_before_project_open_and_resolves_table(self) -> None:
        with patch.object(server, "_open_project") as open_project:
            with self.assertRaisesRegex(RuntimeError, "member_type"):
                server.arcgis_pro_change_version(
                    "CURRENT",
                    "Operations",
                    "Inspections",
                    "TRANSACTIONAL",
                    member_type="MAP",
                    version_name="OWNER.EditA",
                )
        open_project.assert_not_called()

        table = SimpleNamespace(name="Inspections", URI="table://inspections")
        map_obj = SimpleNamespace(
            name="Operations",
            URI="map://operations",
            listTables=lambda: [table],
        )
        project = SimpleNamespace(listMaps=lambda: [map_obj])
        with patch.object(
            server,
            "_open_project",
            return_value=("arcpy", project, "C:/work/demo.aprx"),
        ), patch.object(
            server.enterprise_gdb,
            "change_version",
            return_value={"changed": True},
        ) as change:
            result = json.loads(
                server.arcgis_pro_change_version(
                    "C:/work/demo.aprx",
                    "Operations",
                    "Inspections",
                    "TRANSACTIONAL",
                    member_type="table",
                    version_name="OWNER.EditA",
                )
            )
        change.assert_called_once_with(
            "arcpy",
            table,
            "TRANSACTIONAL",
            version_name="OWNER.EditA",
            history_date="",
            include_participating=True,
        )
        self.assertEqual(result["member_type"], "TABLE")

    def test_wrappers_preserve_exact_confirmation_arguments_and_never_accept_credentials(self) -> None:
        with patch.object(server, "_arcpy", return_value="arcpy"), patch.object(
            server.enterprise_gdb,
            "unregister_as_versioned",
            return_value={"unregistered": True},
        ) as unregister, patch.object(
            server.schema_maintenance,
            "remove_index",
            return_value={"removed": True},
        ) as remove_index:
            server.arcgis_pro_unregister_as_versioned(
                "C:/connections/owner.sde/OWNER.Roads",
                keep_edit="NO_KEEP_EDIT",
                compress_default="COMPRESS_DEFAULT",
                confirm_dataset_path="C:/connections/owner.sde/OWNER.Roads",
                confirm_discard_edits="DISCARD_OUTSTANDING_EDITS",
            )
            server.arcgis_pro_remove_index(
                "C:/connections/owner.sde/OWNER.Roads",
                "IDX_NAME",
                confirm_index_name="IDX_NAME",
            )
        unregister.assert_called_once_with(
            "arcpy",
            "C:/connections/owner.sde/OWNER.Roads",
            keep_edit="NO_KEEP_EDIT",
            compress_default="COMPRESS_DEFAULT",
            confirm_dataset_path="C:/connections/owner.sde/OWNER.Roads",
            confirm_discard_edits="DISCARD_OUTSTANDING_EDITS",
        )
        remove_index.assert_called_once_with(
            "arcpy",
            "C:/connections/owner.sde/OWNER.Roads",
            "IDX_NAME",
            confirm_index_name="IDX_NAME",
        )

        forbidden = {"username", "user", "password", "passwd", "token", "secret"}
        for name in _ENTERPRISE_TOOLS:
            with self.subTest(name=name):
                parameters = set(inspect.signature(getattr(server, name)).parameters)
                self.assertFalse(parameters & forbidden)


if __name__ == "__main__":
    unittest.main()
