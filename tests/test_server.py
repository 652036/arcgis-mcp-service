from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

if "mcp.server.fastmcp" not in sys.modules:
    mcp_module = ModuleType("mcp")
    mcp_server_module = ModuleType("mcp.server")
    fastmcp_module = ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def tool(self, *args: object, **kwargs: object):
            def decorator(func):
                return func

            return decorator

        def run(self) -> None:
            return None

    fastmcp_module.FastMCP = _FakeFastMCP
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = mcp_server_module
    sys.modules["mcp.server.fastmcp"] = fastmcp_module

from arcgis_pro_mcp import server


class ServerToolTests(unittest.TestCase):
    def test_list_projects_uses_configured_roots(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            nested = Path(root) / "nested"
            nested.mkdir()
            first = Path(root) / "first.aprx"
            second = nested / "second.aprx"
            first.touch()
            second.touch()
            with patch.dict(
                os.environ,
                {"ARCGIS_PRO_MCP_PROJECT_ROOTS": root},
                clear=True,
            ):
                payload = json.loads(server.arcgis_pro_list_projects(max_items=10))
        self.assertEqual(payload["project_count"], 2)
        self.assertEqual(set(payload["projects"]), {os.path.normpath(str(first)), os.path.normpath(str(second))})

    def test_da_query_rows_delegates_to_shared_reader(self) -> None:
        captured: dict[str, object] = {}

        def fake_query_rows(
            arcpy: object,
            dataset_path: str,
            fields: list[str],
            where_clause: str,
            order_by: str,
            max_rows: int,
            offset: int,
            include_shape_wkt: bool,
        ) -> list[dict[str, object]]:
            captured["arcpy"] = arcpy
            captured["dataset_path"] = dataset_path
            captured["fields"] = fields
            captured["where_clause"] = where_clause
            captured["order_by"] = order_by
            captured["max_rows"] = max_rows
            captured["offset"] = offset
            captured["include_shape_wkt"] = include_shape_wkt
            return [{"OBJECTID": 1}]

        with patch.object(server, "_arcpy", return_value="fake-arcpy"), patch.object(
            server,
            "validate_input_path_optional",
            return_value="/tmp/data.gdb/roads",
        ), patch.object(server.da_read, "query_rows", side_effect=fake_query_rows):
            payload = json.loads(
                server.arcgis_pro_da_query_rows(
                    "ignored",
                    ["OBJECTID"],
                    where_clause="OBJECTID > 0",
                    order_by="OBJECTID",
                    max_rows=10,
                    offset=5,
                    include_shape_wkt=True,
                )
            )

        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(captured["dataset_path"], "/tmp/data.gdb/roads")
        self.assertEqual(captured["order_by"], "OBJECTID")
        self.assertEqual(captured["include_shape_wkt"], True)

    def test_remove_join_uses_arcpy_from_open_project(self) -> None:
        remove_join = MagicMock()
        fake_arcpy = SimpleNamespace(management=SimpleNamespace(RemoveJoin=remove_join))

        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=True), patch.object(
            server,
            "_open_project",
            return_value=(fake_arcpy, object(), "/tmp/demo.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            return_value="layer",
        ):
            payload = json.loads(
                server.arcgis_pro_remove_join("/tmp/demo.aprx", "Map", "Layer", "join_1")
            )

        remove_join.assert_called_once_with("layer", "join_1")
        self.assertEqual(payload["ok"], True)

    def test_create_db_connection_supports_env_backed_credentials(self) -> None:
        create_connection = MagicMock()
        fake_arcpy = SimpleNamespace(management=SimpleNamespace(CreateDatabaseConnection=create_connection))

        with tempfile.TemporaryDirectory() as output_root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                "DB_USER": "gis_user",
                "DB_PASS": "secret_pass",
            },
            clear=True,
        ), patch.object(server, "_arcpy", return_value=fake_arcpy):
            payload = json.loads(
                server.arcgis_pro_create_db_connection(
                    output_root,
                    "enterprise",
                    "SQL_SERVER",
                    "db-instance",
                    authentication="DATABASE_AUTH",
                    username_env_var="DB_USER",
                    password_env_var="DB_PASS",
                )
            )

        create_connection.assert_called_once()
        _, kwargs = create_connection.call_args
        self.assertEqual(kwargs["username"], "gis_user")
        self.assertEqual(kwargs["password"], "secret_pass")
        self.assertEqual(payload["username_source"], "env")
        self.assertEqual(payload["password_source"], "env")
