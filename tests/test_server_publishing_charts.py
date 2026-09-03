from __future__ import annotations

import inspect
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import server


class ServerPublishingChartRegistrationTests(unittest.TestCase):
    def tearDown(self) -> None:
        server._PROJECT_CACHE.clear()

    def test_all_public_publishing_and_chart_tools_are_registered(self) -> None:
        registered = {tool.name for tool in server.mcp._tool_manager.list_tools()}
        expected = {
            "arcgis_pro_portal_status",
            "arcgis_pro_get_artifact_digest",
            "arcgis_pro_create_sharing_draft",
            "arcgis_pro_stage_service_definition",
            "arcgis_pro_publish_service_definition",
            "arcgis_pro_chart_info",
            "arcgis_pro_list_charts",
            "arcgis_pro_upsert_chart",
            "arcgis_pro_export_chart",
            "arcgis_pro_chart_mutation_capabilities",
        }
        self.assertTrue(expected.issubset(registered), expected - registered)

    def test_capabilities_expose_publish_gates_allowlists_and_chart_tools(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_PUBLISH": "1",
                "ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE": "1",
                "ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE": "1",
                "ARCGIS_PRO_MCP_PORTAL_ALLOWLIST": "https://portal.example.com/portal",
                "ARCGIS_PRO_MCP_SERVER_ALLOWLIST": "https://server.example.com/server",
            },
            clear=True,
        ), patch.object(server, "_window_status_fields", return_value={}):
            payload = json.loads(server.arcgis_pro_server_capabilities())

        self.assertTrue(payload["allow_publish"])
        self.assertTrue(payload["allow_public_share"])
        self.assertTrue(payload["allow_publish_overwrite"])
        self.assertTrue(payload["allow_enterprise_write"])
        self.assertTrue(payload["portal_allowlist_configured"])
        self.assertTrue(payload["server_allowlist_configured"])
        self.assertIn(
            "arcgis_pro_create_sharing_draft",
            payload["tools_require_allow_publish"],
        )
        self.assertIn(
            "arcgis_pro_publish_service_definition",
            payload["tools_require_allow_public_share_when_everyone"],
        )
        self.assertIn("arcgis_pro_upsert_chart", payload["tools_require_allow_write"])
        self.assertIn("arcgis_pro_export_chart", payload["tools_export"])
        self.assertIn("arcgis_pro_list_charts", payload["tools_read_only"])

    def test_registered_functions_do_not_accept_credentials(self) -> None:
        publishing_functions = (
            server.arcgis_pro_portal_status,
            server.arcgis_pro_create_sharing_draft,
            server.arcgis_pro_stage_service_definition,
            server.arcgis_pro_publish_service_definition,
        )
        forbidden = {"username", "user", "password", "passwd", "token", "secret"}
        for function in publishing_functions:
            with self.subTest(function=function.__name__):
                parameters = set(inspect.signature(function).parameters)
                self.assertFalse(parameters & forbidden)

    def test_invalid_chart_member_type_fails_before_project_open(self) -> None:
        with patch.object(server, "_open_project") as open_project:
            with self.assertRaisesRegex(RuntimeError, "member_type"):
                server.arcgis_pro_list_charts(
                    "CURRENT",
                    "Map",
                    "Roads",
                    member_type="MAP",
                )
        open_project.assert_not_called()

    def test_invalid_publishing_member_type_fails_before_project_open(self) -> None:
        with patch.object(server, "_open_project") as open_project:
            with self.assertRaisesRegex(RuntimeError, "member_type"):
                server.arcgis_pro_create_sharing_draft(
                    "CURRENT",
                    "Map",
                    "Roads",
                    "C:/controlled/roads.sddraft",
                    member_type="WORKSPACE",
                )
        open_project.assert_not_called()

    def test_chart_table_resolution_reuses_map_and_table_selectors(self) -> None:
        table = SimpleNamespace(name="Inspections", URI="table://inspections")
        map_obj = SimpleNamespace(
            name="Operations",
            URI="map://operations",
            listTables=lambda: [table],
        )
        project = SimpleNamespace(listMaps=lambda: [map_obj])
        chart_values = [{"title": "Status", "type": "PIE"}]
        with patch.object(
            server,
            "_open_project",
            return_value=("arcpy", project, "C:/work/demo.aprx"),
        ), patch.object(server.charts, "list_charts", return_value=chart_values) as list_charts:
            payload = json.loads(
                server.arcgis_pro_list_charts(
                    "C:/work/demo.aprx",
                    "Operations",
                    "Inspections",
                    member_type="table",
                )
            )
        list_charts.assert_called_once_with(table)
        self.assertEqual(payload["member_type"], "TABLE")
        self.assertEqual(payload["charts"], chart_values)

    def test_scoped_layer_sharing_uses_map_subset_overload(self) -> None:
        layer = SimpleNamespace(name="Roads", longName="Roads", URI="layer://roads")
        draft = object()
        map_obj = SimpleNamespace(
            name="Operations",
            URI="map://operations",
            listLayers=lambda: [layer],
            getWebLayerSharingDraft=MagicMock(return_value=draft),
        )
        project = SimpleNamespace(listMaps=lambda: [map_obj])

        def fake_create(_arcpy, source, service_name, output_path, **kwargs):
            self.assertEqual(service_name, "Roads_Service")
            self.assertEqual(output_path, "C:/controlled/roads.sddraft")
            self.assertEqual(kwargs["service_type"], "FEATURE")
            self.assertIs(
                source.getWebLayerSharingDraft("HOSTING_SERVER", "FEATURE", service_name),
                draft,
            )
            return {"artifact": {"sha256": "a" * 64}}

        with patch.object(
            server,
            "_open_project",
            return_value=("arcpy", project, "C:/work/demo.aprx"),
        ), patch.object(server.publishing, "create_sharing_draft", side_effect=fake_create):
            payload = json.loads(
                server.arcgis_pro_create_sharing_draft(
                    "C:/work/demo.aprx",
                    "Operations",
                    "Roads_Service",
                    "C:/controlled/roads.sddraft",
                    member_name="Roads",
                    member_type="LAYER",
                )
            )

        map_obj.getWebLayerSharingDraft.assert_called_once_with(
            "HOSTING_SERVER",
            "FEATURE",
            "Roads_Service",
            [layer],
        )
        self.assertEqual(payload["source"]["member_type"], "LAYER")

    def test_style_item_uses_official_style_class_wildcard_order(self) -> None:
        item = SimpleNamespace(name="North Arrow 1", URI="style://north-arrow-1")
        project = SimpleNamespace(listStyleItems=MagicMock(return_value=[item]))
        selected = server._get_style_item(
            project,
            "ArcGIS 2D",
            "North Arrows",
            "North Arrow 1",
        )
        self.assertIs(selected, item)
        project.listStyleItems.assert_called_once_with(
            "ArcGIS 2D",
            "North Arrows",
            "North Arrow 1",
        )

        with self.assertRaisesRegex(RuntimeError, "必须同时提供 style"):
            server._get_style_item(project, "", "North Arrows", "North Arrow 1")

    def test_upsert_chart_delegates_and_does_not_serialize_raw_chart_object(self) -> None:
        layer = object()
        raw_chart = object()
        result = {
            "created": True,
            "chart": raw_chart,
            "info": {"title": "Counts", "type": "BAR"},
        }
        with patch.object(
            server,
            "_chart_member",
            return_value=("arcpy", layer, "C:/work/demo.aprx", "LAYER"),
        ), patch.object(server.charts, "upsert_chart", return_value=result) as upsert:
            payload = json.loads(
                server.arcgis_pro_upsert_chart(
                    "C:/work/demo.aprx",
                    "Operations",
                    "Roads",
                    "BAR",
                    "Counts",
                    x="TYPE",
                    y="VALUE",
                )
            )
        upsert.assert_called_once()
        self.assertNotIn("chart", payload)
        self.assertEqual(payload["info"]["title"], "Counts")

    def test_stage_and_publish_wrappers_preserve_policy_arguments(self) -> None:
        with patch.object(
            server,
            "_publishing_arcpy",
            return_value=("arcpy", "C:/work/demo.aprx"),
        ), patch.object(
            server.publishing,
            "stage_service_definition",
            return_value={"artifact": {"sha256": "b" * 64}},
        ) as stage, patch.object(
            server.publishing,
            "publish_service_definition",
            return_value={"published": True},
        ) as publish:
            server.arcgis_pro_stage_service_definition(
                "C:/controlled/service.sddraft",
                "C:/controlled/service.sd",
                "a" * 64,
                sharing_level="EVERYONE",
                overwrite_existing_service=True,
                aprx_path="C:/work/demo.aprx",
            )
            server.arcgis_pro_publish_service_definition(
                "C:/controlled/service.sd",
                "b" * 64,
                server_type="FEDERATED_SERVER",
                federated_server_url="https://server.example.com/server",
                sharing_level="EVERYONE",
                overwrite_existing_service=True,
                aprx_path="C:/work/demo.aprx",
            )

        self.assertEqual(stage.call_args.kwargs["sharing_level"], "EVERYONE")
        self.assertTrue(stage.call_args.kwargs["overwrite_existing_service"])
        self.assertEqual(publish.call_args.kwargs["sharing_level"], "EVERYONE")
        self.assertTrue(publish.call_args.kwargs["overwrite_existing_service"])
        self.assertEqual(
            publish.call_args.kwargs["federated_server_url"],
            "https://server.example.com/server",
        )


if __name__ == "__main__":
    unittest.main()
