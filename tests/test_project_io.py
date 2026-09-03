from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import project_io


class FakeItem:
    def __init__(self, name: str, uri: str, workspace: str = "") -> None:
        self.name = name
        self.URI = uri
        self.longName = name
        self.isBroken = False
        self.connectionProperties = {
            "connection_info": {"database": workspace, "password": "do-not-return"},
            "dataset": name,
        }
        self.pasted = None

    def updateConnectionProperties(self, old, new, auto, validate, ignore_case):
        del old, auto, validate, ignore_case
        if isinstance(new, dict):
            self.connectionProperties = new
        else:
            self.connectionProperties["connection_info"]["database"] = new

    def pasteProperties(self, source, properties):
        self.pasted = (source, properties)

    def exportToMAPX(self, output):
        with open(output, "wb") as stream:
            stream.write(b"mapx")

    def saveACopy(self, output):
        with open(output, "wb") as stream:
            stream.write(b"lyrx")


class FakeMap:
    def __init__(self, name: str, uri: str, layers=None, tables=None) -> None:
        self.name = name
        self.URI = uri
        self._layers = list(layers or [])
        self._tables = list(tables or [])

    def listLayers(self):
        return self._layers

    def listTables(self):
        return self._tables


class FakeProject:
    def __init__(self, maps=None) -> None:
        self._maps = list(maps or [])
        self._layouts = []
        self._reports = []
        self.homeFolder = ""
        self.import_args = None

    def listMaps(self):
        return self._maps

    def listLayouts(self):
        return self._layouts

    def listReports(self):
        return self._reports

    def importDocument(self, *args):
        self.import_args = args
        item = FakeMap("Imported", "map://imported")
        self._maps.append(item)
        return item


class ProjectIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.root,
                "ARCGIS_PRO_MCP_EXPORT_ROOT": self.root,
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_import_document_uses_boolean_log_flag_and_reports_added_items(self) -> None:
        source = os.path.join(self.root, "legacy.mapx")
        with open(source, "wb") as stream:
            stream.write(b"x")
        project = FakeProject()
        result = project_io.import_document(project, source)
        self.assertEqual(project.import_args, (source, True, False, False))
        self.assertEqual(result["added"]["maps"][0]["name"], "Imported")

    def test_export_and_layer_file_verify_created_artifacts(self) -> None:
        item = FakeItem("Roads", "layer://roads")
        mapx = os.path.join(self.root, "map.mapx")
        lyrx = os.path.join(self.root, "roads.lyrx")
        self.assertEqual(project_io.export_mapx(item, mapx)["bytes"], 4)
        self.assertEqual(project_io.save_layer_file(item, lyrx)["bytes"], 4)

    def test_paste_properties_rejects_all_and_accepts_semantic_subset(self) -> None:
        source = FakeItem("Source", "layer://source")
        target = FakeItem("Target", "layer://target")
        with self.assertRaisesRegex(RuntimeError, "显式选择"):
            project_io.paste_layer_properties(target, source, ["ALL"])
        result = project_io.paste_layer_properties(
            target, source, ["popups", "display_filters"]
        )
        self.assertEqual(result["properties"], ["POPUPS", "DISPLAY_FILTERS"])

    def test_connection_repair_is_preflight_bound_and_redacts_passwords(self) -> None:
        old = os.path.join(self.root, "old.gdb")
        new = os.path.join(self.root, "new.gdb")
        item = FakeItem("Roads", "layer://roads", old)
        project = FakeProject([FakeMap("Main", "map://main", [item])])
        repairs = [
            {
                "map_identifier": "map://main",
                "item_identifier": "layer://roads",
                "current_workspace": old,
                "new_workspace": new,
                "new_dataset_name": "RoadCenterlines",
            }
        ]
        preflight = project_io.connection_repair_preflight(project, repairs)
        self.assertNotIn("do-not-return", str(preflight))
        applied = project_io.connection_repair_apply(
            project, repairs, preflight["repair_token"]
        )
        self.assertEqual(applied["repaired_count"], 1)
        self.assertEqual(item.connectionProperties["dataset"], "RoadCenterlines")
        self.assertNotIn("do-not-return", str(applied))

    def test_connection_repair_rejects_changed_target_after_preflight(self) -> None:
        old = os.path.join(self.root, "old.gdb")
        new = os.path.join(self.root, "new.gdb")
        item = FakeItem("Roads", "layer://roads", old)
        project = FakeProject([FakeMap("Main", "map://main", [item])])
        repairs = [{
            "map_identifier": "Main",
            "item_identifier": "Roads",
            "current_workspace": old,
            "new_workspace": new,
        }]
        token = project_io.connection_repair_preflight(project, repairs)["repair_token"]
        item.connectionProperties["dataset"] = "Changed"
        with self.assertRaisesRegex(RuntimeError, "预检后改变"):
            project_io.connection_repair_apply(project, repairs, token)

    def test_connection_repair_preflight_does_not_require_write_gate(self) -> None:
        old = os.path.join(self.root, "old.gdb")
        new = os.path.join(self.root, "new.gdb")
        item = FakeItem("Roads", "layer://roads", old)
        project = FakeProject([FakeMap("Main", "map://main", [item])])
        repairs = [{
            "map_identifier": "map://main",
            "item_identifier": "layer://roads",
            "current_workspace": old,
            "new_workspace": new,
        }]
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": self.root},
            clear=True,
        ):
            preflight = project_io.connection_repair_preflight(project, repairs)
        self.assertIn("repair_token", preflight)

    def test_transformations_build_extent_and_return_recommended(self) -> None:
        class SpatialReference:
            def __init__(self, wkid):
                self.factoryCode = wkid

        class Extent:
            def __init__(self, *values):
                self.values = values
                self.spatialReference = None

        arcpy = SimpleNamespace(
            SpatialReference=SpatialReference,
            Extent=Extent,
            ListTransformations=lambda *args: ["Best", "Fallback"],
        )
        result = project_io.list_transformations(
            arcpy, 4326, 3857, extent=[-10, -5, 10, 5]
        )
        self.assertEqual(result["recommended"], "Best")

    def test_create_and_update_report(self) -> None:
        section = SimpleNamespace(
            name="Main",
            type="REPORT_SECTION",
            visible=True,
            definitionQuery="",
            fields=[],
            statistics=[],
            referenceDataSource={},
            setReferenceDataSource=lambda source: setattr(
                section, "referenceDataSource", source
            ),
        )
        report = SimpleNamespace(
            name="Report",
            URI="report://one",
            listSections=lambda: [section],
        )
        captured: list[tuple[object, ...]] = []

        def create_report(*args: object) -> object:
            captured.append(args)
            return report

        project = SimpleNamespace(createReport=create_report)
        created = project_io.create_report(
            project,
            object(),
            name="Report",
            fields=[{"fieldName": "TYPE", "sortInfo": "ASC"}],
        )
        self.assertEqual(created["report"]["name"], "Report")
        self.assertEqual(
            captured[0][2],
            [{"fieldName": "TYPE", "sortInfo": "ASC", "groupField": False}],
        )
        updated = project_io.update_report_section(
            report, "Main", definition_query="TYPE = 1", visible=False
        )
        self.assertEqual(updated["sections"][0]["definition_query"], "TYPE = 1")
        self.assertFalse(updated["sections"][0]["visible"])


if __name__ == "__main__":
    unittest.main()
