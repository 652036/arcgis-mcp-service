from __future__ import annotations

import os
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import editing


class Cursor:
    def __init__(self, arcpy, dataset, fields, where, writable=False):
        self.arcpy = arcpy
        self.dataset = dataset
        self.fields = fields
        self.writable = writable
        rows = arcpy.rows[dataset]
        requested = None
        match = re.search(r"IN \(([^)]*)\)", str(where or ""))
        if match:
            requested = {int(value) for value in match.group(1).split(",") if value}
        self.source = [row for row in rows if requested is None or row["OID"] in requested]
        self.index = 0
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        if self.index >= len(self.source):
            raise StopIteration
        self.current = self.source[self.index]
        self.index += 1
        return [self.current[self._key(field)] for field in self.fields]

    @staticmethod
    def _key(field):
        return "OID" if field in {"OID", "OID@"} else field

    def updateRow(self, values):
        for field, value in zip(self.fields, values, strict=True):
            self.current[self._key(field)] = value

    def deleteRow(self):
        self.arcpy.rows[self.dataset].remove(self.current)


class FakeEditor:
    def __init__(self, arcpy, workspace):
        self.arcpy = arcpy
        self.workspace = workspace
        self.calls = []
        arcpy.editors.append(self)

    def startEditing(self, with_undo, multiuser):
        self.calls.append(("startEditing", with_undo, multiuser))

    def startOperation(self):
        self.calls.append(("startOperation",))

    def stopOperation(self):
        self.calls.append(("stopOperation",))

    def abortOperation(self):
        self.calls.append(("abortOperation",))

    def stopEditing(self, save):
        self.calls.append(("stopEditing", save))


class FakeArcpy:
    def __init__(self, workspace, datasets):
        self.workspace = workspace
        self.rows = datasets
        self.editors = []
        self.da = SimpleNamespace(
            SearchCursor=lambda dataset, fields, where=None: Cursor(
                self, dataset, fields, where
            ),
            UpdateCursor=lambda dataset, fields, where=None: Cursor(
                self, dataset, fields, where, True
            ),
            Editor=lambda workspace: FakeEditor(self, workspace),
        )

    def Describe(self, value):
        if value == self.workspace:
            return SimpleNamespace(workspaceType="LocalDatabase", dataType="Workspace")
        return SimpleNamespace(
            OIDFieldName="OID",
            path=self.workspace,
            isVersioned=False,
        )

    def ListFields(self, _dataset):
        return [
            SimpleNamespace(name="OID", type="OID"),
            SimpleNamespace(name="NAME", type="String", length=100),
            SimpleNamespace(name="SHAPE", type="Geometry"),
        ]

    @staticmethod
    def AddFieldDelimiters(_dataset, field):
        return field


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = os.path.join(self.temp.name, "data.gdb")
        self.first = os.path.join(self.workspace, "first")
        self.second = os.path.join(self.workspace, "second")
        self.arcpy = FakeArcpy(
            self.workspace,
            {
                self.first: [
                    {"OID": 1, "NAME": "A", "SHAPE@WKT": "POINT (0 0)"},
                    {"OID": 2, "NAME": "B", "SHAPE@WKT": "POINT (1 1)"},
                ],
                self.second: [
                    {"OID": 5, "NAME": "C", "SHAPE@WKT": "POINT (5 5)"},
                ],
            },
        )
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp.name,
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_geometry_preflight_and_apply_are_oid_bound(self):
        operations = [{
            "kind": "update_geometry",
            "geometry_token": "SHAPE@WKT",
            "rows": [{"oid": 2, "geometry": "POINT (9 9)"}],
            "expected_count": 1,
        }]
        preflight = editing.edit_preflight(self.arcpy, self.first, operations)
        self.assertEqual(preflight["plan"][0]["oid_sample"], [2])
        result = editing.edit_apply(
            self.arcpy, self.first, operations, preflight["edit_token"]
        )
        self.assertTrue(result["committed"])
        self.assertEqual(self.arcpy.rows[self.first][1]["SHAPE@WKT"], "POINT (9 9)")
        self.assertIn(("stopEditing", True), self.arcpy.editors[-1].calls)

    def test_token_rejects_changed_operations(self):
        operations = [{
            "kind": "update_geometry",
            "geometry_token": "SHAPE@WKT",
            "rows": [{"oid": 1, "geometry": "POINT (2 2)"}],
        }]
        token = editing.edit_preflight(self.arcpy, self.first, operations)["edit_token"]
        operations[0]["rows"][0]["geometry"] = "POINT (3 3)"
        with self.assertRaisesRegex(RuntimeError, "预检后改变"):
            editing.edit_apply(self.arcpy, self.first, operations, token)

    def test_workspace_edit_commits_two_datasets_in_one_editor(self):
        operations = [
            {
                "dataset_path": self.first,
                "kind": "update_geometry",
                "geometry_token": "SHAPE@WKT",
                "rows": [{"oid": 1, "geometry": "POINT (2 2)"}],
            },
            {
                "dataset_path": self.second,
                "kind": "update_geometry",
                "geometry_token": "SHAPE@WKT",
                "rows": [{"oid": 5, "geometry": "POINT (6 6)"}],
            },
        ]
        preflight = editing.workspace_edit_preflight(self.arcpy, operations)
        result = editing.workspace_edit_apply(
            self.arcpy, operations, preflight["edit_token"]
        )
        self.assertEqual(result["changed_count"], 2)
        self.assertEqual(len(self.arcpy.editors), 1)

    def test_geometry_update_rejects_missing_oid_before_opening_editor(self):
        operations = [{
            "kind": "update_geometry",
            "geometry_token": "SHAPE@WKT",
            "rows": [{"oid": 999, "geometry": "POINT (2 2)"}],
        }]
        with self.assertRaisesRegex(RuntimeError, "OID 不存在"):
            editing.edit_preflight(self.arcpy, self.first, operations)
        self.assertEqual(self.arcpy.editors, [])

    def test_preflight_is_read_only_even_for_delete_plan(self):
        operations = [{"kind": "delete", "where_clause": "OBJECTID = 1"}]
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp.name},
            clear=True,
        ):
            result = editing.edit_preflight(self.arcpy, self.first, operations)
        self.assertGreater(result["total_affected"], 0)
        self.assertEqual(result["plan"][0]["kind"], "delete")
        self.assertIn("edit_token", result)


if __name__ == "__main__":
    unittest.main()
