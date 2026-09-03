from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import cartography


class FakeSpatialReference:
    def __init__(self, factory_code: int = 0, name: str = "") -> None:
        self.factoryCode = factory_code
        self.name = name or f"WKID {factory_code}"


class FakeExtent:
    def __init__(self, xmin, ymin, xmax, ymax, *args) -> None:
        self.XMin = xmin
        self.YMin = ymin
        self.XMax = xmax
        self.YMax = ymax
        self.spatialReference = args[4] if len(args) >= 5 else None


class FakeArcpy:
    @staticmethod
    def Extent(xmin, ymin, xmax, ymax, *args):
        return FakeExtent(xmin, ymin, xmax, ymax, *args)

    @staticmethod
    def SpatialReference(wkid):
        return FakeSpatialReference(wkid)

    @staticmethod
    def Point(x, y):
        return (x, y)


class FakeCamera:
    def __init__(self, extent: FakeExtent) -> None:
        self.extent = extent
        self.set_calls = []

    def getExtent(self):
        return self.extent

    def setExtent(self, extent):
        self.set_calls.append(extent)
        self.extent = extent


class FakeMapFrame:
    type = "MAPFRAME_ELEMENT"

    def __init__(self, name="Main frame", uri="mf://1", map_obj=None) -> None:
        self.name = name
        self.URI = uri
        self.map = map_obj
        self.camera = FakeCamera(FakeExtent(0, 0, 1, 1))
        self.elementPositionX = 0.0
        self.elementPositionY = 0.0
        self.elementWidth = 1.0
        self.elementHeight = 1.0
        self.elementRotation = 0.0
        self.visible = True


class FakeElement:
    def __init__(self, name, element_type, uri="") -> None:
        self.name = name
        self.type = element_type
        self.URI = uri
        self.visible = True
        self.locked = False
        self.elementPositionX = 0.0
        self.elementPositionY = 0.0
        self.elementWidth = 1.0
        self.elementHeight = 1.0
        self.elementRotation = 0.0


class FakeTextElement(FakeElement):
    def __init__(self, name, text="") -> None:
        super().__init__(name, "TEXT_ELEMENT", f"text://{name}")
        self.text = text


class FakeLayout:
    def __init__(self, name="Layout", uri="layout://1", elements=None, map_series=None) -> None:
        self.name = name
        self.URI = uri
        self.elements = list(elements or [])
        self.mapSeries = map_series
        self.deleted = []

    def listElements(self, element_type=None):
        if element_type == "MAPFRAME_ELEMENT":
            return [item for item in self.elements if "MAPFRAME" in item.type]
        return list(self.elements)

    def createMapFrame(self, geometry, map_obj, name):
        element = FakeMapFrame(name=name, uri=f"mf://{name}", map_obj=map_obj)
        element.elementPositionX, element.elementPositionY = geometry
        self.elements.append(element)
        return element

    def deleteElement(self, element):
        self.deleted.append(element)
        self.elements.remove(element)


class FakeProject:
    def __init__(self, layouts=None, active_view=None) -> None:
        self.layouts = list(layouts or [])
        self.activeView = active_view

    def listLayouts(self):
        return list(self.layouts)

    def importDocument(self, path, **kwargs):
        layout = FakeLayout("Imported", f"layout://{len(self.layouts) + 1}")
        self.layouts.append(layout)
        self.import_call = (path, kwargs)
        return layout

    def createTextElement(self, layout, geometry, text_type, text, **kwargs):
        element = FakeTextElement(kwargs["name"], text)
        element.elementPositionX, element.elementPositionY = geometry
        layout.elements.append(element)
        self.text_call = (layout, geometry, text_type, text, kwargs)
        return element


class FakeMapView:
    def __init__(self) -> None:
        self.camera = object()
        self.calls = []

    def exportToPNG(self, output_path, **kwargs):
        self.calls.append(("PNG", output_path, kwargs))

    def exportToJPEG(self, output_path, **kwargs):
        self.calls.append(("JPEG", output_path, kwargs))

    def exportToTIFF(self, output_path, **kwargs):
        self.calls.append(("TIFF", output_path, kwargs))


class FakeBookmark:
    def __init__(self, name, description="") -> None:
        self.name = name
        self.description = description
        self.thumbnail_updates = 0

    def updateThumbnail(self):
        self.thumbnail_updates += 1


class FakeMap:
    def __init__(self, name="Map", uri="map://1") -> None:
        self.name = name
        self.URI = uri
        self.bookmarks = []
        self.tables = []
        self.exports = []

    def listBookmarks(self):
        return list(self.bookmarks)

    def removeBookmark(self, bookmark):
        self.bookmarks.remove(bookmark)

    def importBookmarks(self, path):
        self.import_path = path
        self.bookmarks.append(FakeBookmark("Imported"))

    def exportBookmarks(self, path):
        self.exports.append(path)

    def listTables(self):
        return list(self.tables)


class FakeBookmarkSource:
    def __init__(self, map_obj) -> None:
        self.map = map_obj

    def createBookmark(self, name, description):
        bookmark = FakeBookmark(name, description)
        self.map.bookmarks.append(bookmark)
        return bookmark


class FakeTable:
    def __init__(self, name="Owners") -> None:
        self.name = name
        self.URI = f"table://{name}"
        self.longName = name
        self.dataSource = r"server=db;password=plain-text;dataset=owners"
        self.isBroken = False
        self.definitionQuery = "ACTIVE = 1"
        self.visible = True
        self.connectionProperties = {
            "connection_info": {"user": "editor", "password": "do-not-leak", "token": "abc"},
            "dataset": "owners",
        }
    def listDefinitionQueries(self):
        return [{"name": "Active", "sql": "ACTIVE = 1", "isActive": True}]

    def getSelectionSet(self):
        return {9, 2, 5}


class FakeFeatureLayer:
    def __init__(self, name="Parcels") -> None:
        self.name = name
        self.URI = f"layer://{name}"
        self.open_calls = []

    def openTableView(self, **kwargs):
        self.open_calls.append(kwargs)


class FakeMapSeries:
    enabled = True

    def __init__(self) -> None:
        self.currentPageNumber = 1
        self.pageCount = 3
        self.pageNameField = type("Field", (), {"name": "SHEET"})()
        self.mapFrame = FakeMapFrame()
        self.indexLayer = type("Layer", (), {"name": "Index"})()
        self.selectedIndexFeatures = [2]
        self.refresh_count = 0
        self.exports = []

    def getPageNumberFromName(self, name):
        return {"North": 2, "South": 3}.get(name)

    def refresh(self):
        self.refresh_count += 1

    def exportToPDF(self, output_path, **kwargs):
        self.exports.append((output_path, kwargs))


class FakeDefinitionMember:
    def __init__(self) -> None:
        self.queries = [
            {"name": "Old", "sql": "TYPE = 1", "isActive": True},
            {"name": "Other", "sql": "TYPE = 2", "isActive": False},
        ]

    def listDefinitionQueries(self):
        return [dict(item) for item in self.queries]

    def updateDefinitionQueries(self, queries):
        self.queries = [dict(item) for item in queries]


class FakeLabelClass:
    def __init__(self, name, expression="", sql_query="", language="Arcade") -> None:
        self.name = name
        self.expression = expression
        self.SQLQuery = sql_query
        self.expressionEngine = language
        self.visible = False


class FakeLabelLayer:
    def __init__(self) -> None:
        self.classes = [FakeLabelClass("Default", "$feature.NAME")]

    def listLabelClasses(self):
        return list(self.classes)

    def createLabelClass(self, name, expression, sql_query, language):
        label_class = FakeLabelClass(name, expression, sql_query, language)
        self.classes.append(label_class)
        return label_class


class CartographyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_CIM_WRITE": "1",
                "ARCGIS_PRO_MCP_EXPORT_ROOT": self.temp_dir.name,
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp_dir.name,
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_map_frame_extent_uses_camera_and_spatial_reference(self):
        map_frame = FakeMapFrame()
        result = cartography.set_map_frame_extent(FakeArcpy(), map_frame, 1, 2, 11, 22, 3857)
        self.assertEqual(result["xmin"], 1.0)
        self.assertEqual(result["spatial_reference"]["wkid"], 3857)
        self.assertEqual(len(map_frame.camera.set_calls), 1)

    def test_map_frame_extent_rejects_invalid_bounds_and_write_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "xmin < xmax"):
            cartography.set_map_frame_extent(FakeArcpy(), FakeMapFrame(), 5, 0, 1, 10)
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "0"}):
            with self.assertRaisesRegex(RuntimeError, "写入类操作已禁用"):
                cartography.set_map_frame_extent(FakeArcpy(), FakeMapFrame(), 0, 0, 1, 1)

    def test_export_active_map_view_validates_root_and_dimensions(self):
        view = FakeMapView()
        project = FakeProject(active_view=view)
        output = os.path.join(self.temp_dir.name, "exports", "map.jpg")
        result = cartography.export_active_view(project, output, width=800, height=600, jpeg_quality=82)
        self.assertEqual(result["view_type"], "MAP_VIEW")
        self.assertEqual(view.calls[0][0], "JPEG")
        self.assertEqual(view.calls[0][2]["jpeg_quality"], 82)
        self.assertEqual(view.calls[0][2]["width"], 800)
        outside = os.path.join(os.path.dirname(self.temp_dir.name), "outside", "map.png")
        with self.assertRaisesRegex(RuntimeError, "EXPORT_ROOT"):
            cartography.export_active_view(project, outside)

    def test_export_active_map_view_refuses_existing_output(self):
        view = FakeMapView()
        project = FakeProject(active_view=view)
        output = Path(self.temp_dir.name, "existing.png")
        output.write_bytes(b"original")
        with self.assertRaisesRegex(RuntimeError, "拒绝隐式覆盖"):
            cartography.export_active_view(project, str(output))
        self.assertEqual(output.read_bytes(), b"original")
        self.assertEqual(view.calls, [])

    def test_open_table_view_uses_layer_api_and_marks_sdk_boundary_for_table(self):
        layer = FakeFeatureLayer()
        result = cartography.open_table_view(layer, show_selected=True)
        self.assertTrue(result["opened"])
        self.assertTrue(result["show_selected"])
        self.assertEqual(layer.open_calls, [{"show_selected": True}])
        with self.assertRaisesRegex(RuntimeError, "SDK Add-in"):
            cartography.open_table_view(FakeTable())

    def test_bookmark_crud_import_and_export(self):
        map_obj = FakeMap()
        source = FakeBookmarkSource(map_obj)
        created = cartography.create_bookmark(source, "Start", "first")
        self.assertEqual(created["description"], "first")
        updated = cartography.update_bookmark(
            map_obj,
            "Start",
            new_name="Renamed",
            description="updated",
            update_thumbnail=True,
        )
        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual(map_obj.bookmarks[0].thumbnail_updates, 1)

        source_path = Path(self.temp_dir.name, "shared.bkmx")
        source_path.write_bytes(b"bookmark")
        imported = cartography.import_bookmarks(map_obj, str(source_path))
        self.assertEqual(imported["imported_count"], 1)
        destination = os.path.join(self.temp_dir.name, "out", "all.bkmx")
        exported = cartography.export_bookmarks(map_obj, destination)
        self.assertEqual(exported["bookmark_count"], 2)
        deleted = cartography.delete_bookmark(map_obj, "Renamed")
        self.assertTrue(deleted["deleted"])

    def test_layout_template_import_and_map_frame_binding(self):
        template = Path(self.temp_dir.name, "template.pagx")
        template.write_text("{}", encoding="utf-8")
        project = FakeProject()
        result = cartography.create_layout_from_template(project, str(template), "Atlas")
        self.assertEqual(result["name"], "Atlas")
        self.assertTrue(project.import_call[1]["include_layout"])

        map_a = FakeMap("A", "map://a")
        map_b = FakeMap("B", "map://b")
        map_frame = FakeMapFrame(map_obj=map_a)
        layout = FakeLayout(elements=[map_frame])
        bound = cartography.bind_map_frame(layout, map_frame.URI, map_b)
        self.assertEqual(bound["bound_map_uri"], "map://b")
        self.assertIs(map_frame.map, map_b)

    def test_layout_element_info_upsert_and_delete(self):
        layout = FakeLayout(elements=[FakeTextElement("Title", "old")])
        project = FakeProject([layout])
        updated = cartography.upsert_layout_element(
            FakeArcpy(),
            project,
            layout,
            "TEXT",
            "Title",
            text="new",
            x=4,
            visible=False,
        )
        self.assertFalse(updated["created"])
        self.assertEqual(updated["element"].text, "new")
        self.assertEqual(updated["element"].elementPositionX, 4.0)
        self.assertFalse(updated["element"].visible)

        created = cartography.upsert_layout_element(
            FakeArcpy(),
            project,
            layout,
            "TEXT_ELEMENT",
            "Subtitle",
            text="hello",
            x=1,
            y=2,
            width=3,
        )
        self.assertTrue(created["created"])
        info = cartography.layout_element_info(layout, "TEXT")
        self.assertEqual([item["name"] for item in info], ["Title", "Subtitle"])
        deleted = cartography.delete_layout_element(layout, "Subtitle", "TEXT")
        self.assertTrue(deleted["deleted"])

    def test_layout_element_duplicate_name_requires_unique_selection(self):
        layout = FakeLayout(elements=[FakeTextElement("Title"), FakeTextElement("Title")])
        with self.assertRaisesRegex(RuntimeError, "不唯一"):
            cartography.delete_layout_element(layout, "Title", "TEXT")

    def test_map_series_info_page_refresh_and_export(self):
        map_series = FakeMapSeries()
        layout = FakeLayout(map_series=map_series)
        info = cartography.map_series_info(layout)
        self.assertEqual(info["page_count"], 3)
        changed = cartography.set_map_series_page(layout, page_name="North")
        self.assertEqual(changed["current_page_number"], 2)
        refreshed = cartography.refresh_map_series(layout)
        self.assertEqual(refreshed["current_page_number"], 2)
        self.assertEqual(map_series.refresh_count, 1)

        output = os.path.join(self.temp_dir.name, "atlas.pdf")
        exported = cartography.export_map_series_pdf(
            layout,
            output,
            page_range_type="RANGE",
            page_range_string="1-2",
        )
        self.assertEqual(exported["page_range_string"], "1-2")
        self.assertEqual(map_series.exports[0][1]["resolution"], 300)
        with self.assertRaisesRegex(RuntimeError, "page_range_string"):
            cartography.export_map_series_pdf(layout, output, page_range_type="RANGE")

    def test_definition_query_upsert_activates_one_and_delete_removes_one(self):
        member = FakeDefinitionMember()
        result = cartography.upsert_definition_query(member, "New", "TYPE = 3", is_active=True)
        self.assertTrue(result["isActive"])
        self.assertEqual([item["name"] for item in member.queries if item["isActive"]], ["New"])
        deleted = cartography.delete_definition_query(member, "Old")
        self.assertTrue(deleted["deleted"])
        self.assertNotIn("Old", [item["name"] for item in member.queries])

    def test_label_class_upsert_updates_and_creates(self):
        layer = FakeLabelLayer()
        updated = cartography.upsert_label_class(
            layer,
            "Default",
            "$feature.ROAD",
            sql_query="ROAD IS NOT NULL",
            visible=True,
        )
        self.assertEqual(updated["expression"], "$feature.ROAD")
        self.assertTrue(updated["visible"])
        created = cartography.upsert_label_class(layer, "Secondary", "$feature.ID")
        self.assertEqual(created["expression_engine"], "ARCADE")
        self.assertEqual(len(cartography.list_label_classes(layer)), 2)
        with self.assertRaisesRegex(RuntimeError, "仅允许 Arcade"):
            cartography.upsert_label_class(
                layer,
                "Unsafe",
                "__import__('os')",
                language="Python",
            )

    def test_table_properties_redacts_secrets_and_bounds_selection(self):
        result = cartography.table_properties(FakeTable(), selection_sample_limit=2)
        connection_info = result["connection_properties"]["connection_info"]
        self.assertEqual(connection_info["password"], "[REDACTED]")
        self.assertEqual(connection_info["token"], "[REDACTED]")
        self.assertEqual(connection_info["user"], "editor")
        self.assertNotIn("plain-text", result["data_source"])
        self.assertEqual(result["selection_count"], 3)
        self.assertEqual(result["selection_sample"], [2, 5])
        self.assertTrue(result["selection_truncated"])

        table = FakeTable()
        changed = cartography.update_table_properties(table, new_name="Residents", definition_query="")
        self.assertEqual(changed["name"], "Residents")
        self.assertEqual(changed["definition_query"], "")


if __name__ == "__main__":
    unittest.main()
