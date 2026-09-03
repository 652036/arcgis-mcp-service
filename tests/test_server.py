from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import server


class ServerToolTests(unittest.TestCase):
    def tearDown(self) -> None:
        server._PROJECT_CACHE.clear()

    def test_list_projects_without_roots_returns_empty_payload(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            payload = json.loads(server.arcgis_pro_list_projects(max_items=10))
        self.assertEqual(payload["project_count"], 0)
        self.assertEqual(payload["projects"], [])
        self.assertIn("ARCGIS_PRO_MCP_PROJECT_ROOTS", payload["note"])

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

    def test_open_project_reuses_cached_project_for_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_path = Path(root) / "demo.aprx"
            project_path.touch()
            opened_project = object()
            arcgis_project = MagicMock(return_value=opened_project)
            fake_arcpy = SimpleNamespace(mp=SimpleNamespace(ArcGISProject=arcgis_project))

            with patch.dict(
                os.environ,
                {"ARCGIS_PRO_MCP_PROJECT_ROOTS": root},
                clear=True,
            ), patch.object(server, "_arcpy", return_value=fake_arcpy):
                first = server._open_project(str(project_path))
                second = server._open_project(f'"{project_path}"')

        self.assertIs(first[1], opened_project)
        self.assertIs(second[1], opened_project)
        self.assertEqual(first[2], second[2])
        arcgis_project.assert_called_once_with(os.path.normpath(str(project_path)))

    def test_open_project_current_is_not_cached(self) -> None:
        first_project = SimpleNamespace(filePath=r"C:\work\a.aprx")
        second_project = SimpleNamespace(filePath=r"C:\work\b.aprx")
        arcgis_project = MagicMock(side_effect=[first_project, second_project])
        fake_arcpy = SimpleNamespace(mp=SimpleNamespace(ArcGISProject=arcgis_project))
        with patch.object(server, "_arcpy", return_value=fake_arcpy):
            first = server._open_project("CURRENT")
            second = server._open_project("current")
        self.assertIs(first[1], first_project)
        self.assertIs(second[1], second_project)
        self.assertEqual(first[2], r"C:\work\a.aprx")
        self.assertEqual(second[2], r"C:\work\b.aprx")
        self.assertEqual(arcgis_project.call_count, 2)
        arcgis_project.assert_called_with("CURRENT")

    def test_open_project_current_reuses_request_bound_reference(self) -> None:
        bound_project = SimpleNamespace(filePath=r"C:\\work\\live.aprx")
        arcgis_project = MagicMock()
        fake_arcpy = SimpleNamespace(mp=SimpleNamespace(ArcGISProject=arcgis_project))
        with patch.object(server, "_arcpy", return_value=fake_arcpy), server._bind_current_project(
            bound_project,
            r"C:\\work\\live.aprx",
        ):
            first = server._open_project("CURRENT")
            second = server._open_project("CURRENT")
        self.assertIs(first[1], bound_project)
        self.assertIs(second[1], bound_project)
        self.assertEqual(first[2], r"C:\\work\\live.aprx")
        arcgis_project.assert_not_called()

    def test_duplicate_layer_names_require_long_name_or_uri(self) -> None:
        first = SimpleNamespace(name="Roads", longName=r"Group A\Roads", URI="layer://a")
        second = SimpleNamespace(name="Roads", longName=r"Group B\Roads", URI="layer://b")
        target_map = SimpleNamespace(listLayers=lambda: [first, second])

        with self.assertRaisesRegex(RuntimeError, "名称不唯一"):
            server._find_layer(target_map, "Roads")
        self.assertIs(server._find_layer(target_map, r"Group B\Roads"), second)
        self.assertIs(server._find_layer(target_map, "layer://a"), first)

    def test_duplicate_map_names_can_be_selected_by_uri(self) -> None:
        first = SimpleNamespace(name="Map", URI="map://a")
        second = SimpleNamespace(name="Map", URI="map://b")
        project = SimpleNamespace(listMaps=lambda: [first, second])

        with self.assertRaisesRegex(RuntimeError, "名称不唯一"):
            server._get_map(project, "Map")
        self.assertIs(server._get_map(project, "map://b"), second)

    def test_layer_properties_reads_renderer_type_not_symbology_type(self) -> None:
        renderer = SimpleNamespace(type="SimpleRenderer")
        layer = SimpleNamespace(
            name="Roads",
            longName="Roads",
            visible=True,
            symbology=SimpleNamespace(renderer=renderer),
        )
        with patch.object(
            server,
            "_open_project",
            return_value=(object(), object(), r"C:\\demo.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            return_value=layer,
        ):
            payload = json.loads(
                server.arcgis_pro_layer_properties(r"C:\\demo.aprx", "Map", "Roads")
            )

        self.assertEqual(payload["symbology_kind"], "renderer")
        self.assertEqual(payload["symbology_type"], "SimpleRenderer")
        self.assertNotIn("symbology_error", payload)

    def test_attribute_selection_verifies_actual_set_and_refreshes_current_window(self) -> None:
        result = SimpleNamespace(getOutput=lambda index: "1" if index == 1 else "layer")
        select = MagicMock(return_value=result)
        refresh = MagicMock()
        fake_arcpy = SimpleNamespace(
            management=SimpleNamespace(SelectLayerByAttribute=select),
            RefreshLayer=refresh,
        )
        layer = SimpleNamespace(name="Roads", getSelectionSet=lambda: {7})
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"},
            clear=True,
        ), patch.object(
            server,
            "_open_project",
            return_value=(fake_arcpy, object(), r"C:\\live.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            return_value=layer,
        ):
            payload = json.loads(
                server.arcgis_pro_select_layer_by_attribute(
                    "CURRENT",
                    "Map",
                    "Roads",
                    "NEW_SELECTION",
                    '"NAME" = \'Target\'',
                )
            )

        select.assert_called_once_with(layer, "NEW_SELECTION", '"NAME" = \'Target\'')
        refresh.assert_called_once_with("Roads")
        self.assertEqual(payload["selected_count"], 1)
        self.assertEqual(payload["result_count"], 1)
        self.assertIs(payload["selection_verified"], True)
        self.assertIs(payload["ui_refresh_requested"], True)

    def test_attribute_selection_rejects_mismatched_result_count(self) -> None:
        result = SimpleNamespace(getOutput=lambda index: "25" if index == 1 else "layer")
        fake_arcpy = SimpleNamespace(
            management=SimpleNamespace(
                SelectLayerByAttribute=MagicMock(return_value=result)
            ),
            RefreshLayer=MagicMock(),
        )
        layer = SimpleNamespace(name="Roads", getSelectionSet=lambda: {7})
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"},
            clear=True,
        ), patch.object(
            server,
            "_open_project",
            return_value=(fake_arcpy, object(), r"C:\\live.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            return_value=layer,
        ):
            with self.assertRaisesRegex(RuntimeError, "计数与图层实际选择集不一致"):
                server.arcgis_pro_select_layer_by_attribute(
                    "CURRENT",
                    "Map",
                    "Roads",
                    "NEW_SELECTION",
                    '"NAME" = \'Target\'',
                )

        fake_arcpy.RefreshLayer.assert_not_called()

    def test_location_selection_uses_count_output_and_verifies_selection(self) -> None:
        def get_output(index: int) -> str:
            return {0: "input", 1: "input;other", 2: "2"}[index]

        result = SimpleNamespace(getOutput=get_output)
        select = MagicMock(return_value=result)
        refresh = MagicMock()
        fake_arcpy = SimpleNamespace(
            management=SimpleNamespace(SelectLayerByLocation=select),
            RefreshLayer=refresh,
        )
        input_layer = SimpleNamespace(name="Parcels", getSelectionSet=lambda: {4, 8})
        selecting_layer = SimpleNamespace(name="Districts")

        def find_layer(_map: object, name: str) -> object:
            return input_layer if name == "Parcels" else selecting_layer

        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"},
            clear=True,
        ), patch.object(
            server,
            "_open_project",
            return_value=(fake_arcpy, object(), r"C:\\live.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            side_effect=find_layer,
        ):
            payload = json.loads(
                server.arcgis_pro_select_layer_by_location(
                    "CURRENT",
                    "Map",
                    "Parcels",
                    "INTERSECT",
                    "Districts",
                )
            )

        select.assert_called_once_with(
            input_layer,
            "INTERSECT",
            selecting_layer,
            "",
            "NEW_SELECTION",
            "NOT_INVERT",
        )
        refresh.assert_called_once_with("Parcels")
        self.assertEqual(payload["selected_count"], 2)
        self.assertEqual(payload["result_count"], 2)
        self.assertIs(payload["selection_verified"], True)

    def test_clear_layer_selection_verifies_empty_set(self) -> None:
        selected = {3}

        def clear_selection(*_args: object) -> None:
            selected.clear()

        clear = MagicMock(side_effect=clear_selection)
        refresh = MagicMock()
        fake_arcpy = SimpleNamespace(
            management=SimpleNamespace(SelectLayerByAttribute=clear),
            RefreshLayer=refresh,
        )
        layer = SimpleNamespace(name="Roads", getSelectionSet=lambda: selected)
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"},
            clear=True,
        ), patch.object(
            server,
            "_open_project",
            return_value=(fake_arcpy, object(), r"C:\\live.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            return_value=layer,
        ):
            payload = json.loads(
                server.arcgis_pro_clear_map_selection(
                    "CURRENT",
                    "Map",
                    scope="layer",
                    layer_name="Roads",
                )
            )

        clear.assert_called_once_with(layer, "CLEAR_SELECTION", "")
        refresh.assert_called_once_with("Roads")
        self.assertEqual(payload["layers_cleared"], 1)
        self.assertIs(payload["selection_verified"], True)

    def test_clear_layer_selection_rejects_remaining_selection(self) -> None:
        clear = MagicMock()
        fake_arcpy = SimpleNamespace(
            management=SimpleNamespace(SelectLayerByAttribute=clear),
            RefreshLayer=MagicMock(),
        )
        layer = SimpleNamespace(name="Roads", getSelectionSet=lambda: {3})
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"},
            clear=True,
        ), patch.object(
            server,
            "_open_project",
            return_value=(fake_arcpy, object(), r"C:\\live.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            return_value=layer,
        ):
            with self.assertRaisesRegex(RuntimeError, "仍有选中要素"):
                server.arcgis_pro_clear_map_selection(
                    "CURRENT",
                    "Map",
                    scope="layer",
                    layer_name="Roads",
                )

        clear.assert_called_once_with(layer, "CLEAR_SELECTION", "")
        fake_arcpy.RefreshLayer.assert_not_called()

    def test_selection_readers_use_exact_layer_selection_set(self) -> None:
        layer = SimpleNamespace(name="Roads", getSelectionSet=lambda: {25, 1, 9})
        with patch.object(
            server,
            "_open_project",
            return_value=(object(), object(), r"C:\\demo.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            return_value=layer,
        ):
            count_payload = json.loads(
                server.arcgis_pro_layer_selection_count(
                    r"C:\\demo.aprx",
                    "Map",
                    "Roads",
                )
            )
            fids_payload = json.loads(
                server.arcgis_pro_layer_selection_fids(
                    r"C:\\demo.aprx",
                    "Map",
                    "Roads",
                    max_fids=2,
                )
            )

        self.assertEqual(count_payload["selected_count"], 3)
        self.assertEqual(count_payload["selected_or_total_count"], 3)
        self.assertIs(count_payload["selection_verified"], True)
        self.assertEqual(fids_payload["fids"], [1, 9])
        self.assertEqual(fids_payload["selected_count"], 3)
        self.assertIs(fids_payload["truncated"], True)

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

    def test_active_view_info_reports_live_camera(self) -> None:
        extent = SimpleNamespace(
            XMin=1,
            YMin=2,
            XMax=3,
            YMax=4,
            spatialReference=SimpleNamespace(name="WGS 84", factoryCode=4326, type="Geographic"),
        )
        camera = SimpleNamespace(
            scale=25000,
            heading=0,
            pitch=-90,
            roll=0,
            mode="MAP",
            getExtent=lambda: extent,
        )
        active_map = SimpleNamespace(name="Live Map")
        view = SimpleNamespace(map=active_map, camera=camera)
        project = SimpleNamespace(
            activeView=view,
            activeMap=active_map,
            isReadOnly=False,
        )
        with patch.object(
            server,
            "_open_project",
            return_value=(object(), project, r"C:\\live.aprx"),
        ):
            payload = json.loads(server.arcgis_pro_active_view_info("CURRENT"))
        self.assertEqual(payload["active_view"]["type"], "MAP_VIEW")
        self.assertEqual(payload["active_view"]["map_name"], "Live Map")
        self.assertEqual(payload["active_view"]["camera"]["extent"]["xmin"], 1.0)

    def test_active_view_tools_reject_file_mode(self) -> None:
        with patch.object(server, "_open_project") as open_project:
            with self.assertRaisesRegex(RuntimeError, "CURRENT"):
                server.arcgis_pro_active_view_info(r"C:\\offline.aprx")
        open_project.assert_not_called()

    def test_capabilities_classify_live_window_tools(self) -> None:
        with patch.object(
            server,
            "_window_status_fields",
            return_value={"window_attached": False, "execution_mode": "file"},
        ):
            payload = json.loads(server.arcgis_pro_server_capabilities())
        self.assertIn("arcgis_pro_active_view_info", payload["tools_read_only"])
        self.assertIn("arcgis_pro_set_active_view_extent", payload["tools_require_allow_write"])
        self.assertIn("arcgis_pro_refresh_layer", payload["tools_require_window"])

    def test_open_map_view_can_focus_it_after_closing_views(self) -> None:
        target_map = SimpleNamespace(name="Target", openView=MagicMock())
        project = SimpleNamespace(closeViews=MagicMock())
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=True), patch.object(
            server,
            "_open_project",
            return_value=(object(), project, r"C:\\live.aprx"),
        ), patch.object(server, "_get_map", return_value=target_map):
            payload = json.loads(
                server.arcgis_pro_open_map_view("CURRENT", "Target", close_other_views=True)
            )
        project.closeViews.assert_called_once_with("MAPS_AND_LAYOUTS")
        target_map.openView.assert_called_once_with()
        self.assertEqual(payload["opened_map"], "Target")

    def test_set_active_view_extent_uses_map_view_camera(self) -> None:
        extent = SimpleNamespace(
            XMin=1,
            YMin=2,
            XMax=3,
            YMax=4,
            spatialReference=None,
        )
        camera = SimpleNamespace(
            scale=1000,
            heading=0,
            pitch=-90,
            roll=0,
            getExtent=lambda: extent,
            setExtent=MagicMock(),
        )
        spatial_reference = SimpleNamespace()
        active_map = SimpleNamespace(name="Live Map", spatialReference=spatial_reference)
        view = SimpleNamespace(map=active_map, camera=camera, panToExtent=MagicMock())
        project = SimpleNamespace(activeView=view)
        made_extent = SimpleNamespace(spatialReference=None)
        fake_arcpy = SimpleNamespace(Extent=MagicMock(return_value=made_extent))
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=True), patch.object(
            server,
            "_open_project",
            return_value=(fake_arcpy, project, r"C:\\live.aprx"),
        ):
            payload = json.loads(
                server.arcgis_pro_set_active_view_extent("CURRENT", 1, 2, 3, 4)
            )
        camera.setExtent.assert_called_once_with(made_extent)
        fake_arcpy.Extent.assert_called_once_with(
            1.0,
            2.0,
            3.0,
            4.0,
            None,
            None,
            None,
            None,
            spatial_reference,
        )
        view.panToExtent.assert_not_called()
        self.assertEqual(payload["map_name"], "Live Map")

    def test_zoom_active_view_to_layer_uses_map_view_extent(self) -> None:
        extent = SimpleNamespace(
            XMin=1,
            YMin=2,
            XMax=3,
            YMax=4,
            spatialReference=None,
        )
        camera = SimpleNamespace(
            scale=1000,
            heading=0,
            pitch=-90,
            roll=0,
            getExtent=lambda: extent,
            setExtent=MagicMock(),
        )
        layer = SimpleNamespace(name="Roads", getSelectionSet=lambda: {1})
        active_map = SimpleNamespace(name="Live Map")
        view = SimpleNamespace(
            map=active_map,
            camera=camera,
            getLayerExtent=MagicMock(return_value=extent),
        )
        project = SimpleNamespace(activeView=view)
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=True), patch.object(
            server,
            "_open_project",
            return_value=(object(), project, r"C:\\live.aprx"),
        ), patch.object(server, "_find_layer", return_value=layer):
            server.arcgis_pro_zoom_active_view_to_layer(
                "CURRENT",
                "Roads",
                selection_only=True,
            )
        view.getLayerExtent.assert_called_once_with(layer, True, True)
        camera.setExtent.assert_called_once_with(extent)

    def test_refresh_layer_requests_pro_redraw(self) -> None:
        refresh = MagicMock()
        fake_arcpy = SimpleNamespace(RefreshLayer=refresh)
        project = object()
        layer = SimpleNamespace(name="Roads")
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=True), patch.object(
            server,
            "_open_project",
            return_value=(fake_arcpy, project, r"C:\\live.aprx"),
        ), patch.object(server, "_get_map", return_value="map"), patch.object(
            server,
            "_find_layer",
            return_value=layer,
        ):
            payload = json.loads(server.arcgis_pro_refresh_layer("CURRENT", "Map", "Roads"))
        refresh.assert_called_once_with("Roads")
        self.assertEqual(payload["ok"], True)

    def test_save_project_rejects_read_only_current_reference(self) -> None:
        project = SimpleNamespace(isReadOnly=True, save=MagicMock())
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=True), patch.object(
            server,
            "_open_project",
            return_value=(object(), project, r"C:\\live.aprx"),
        ):
            with self.assertRaisesRegex(RuntimeError, "只读"):
                server.arcgis_pro_save_project("CURRENT")
        project.save.assert_not_called()

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
