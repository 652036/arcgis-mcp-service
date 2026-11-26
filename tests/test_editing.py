from __future__ import annotations

import os
import re
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import editing


class InsertCursor:
    def __init__(self, arcpy, dataset, fields):
        self.arcpy = arcpy
        self.dataset = dataset
        self.fields = fields

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def insertRow(self, values):
        rows = self.arcpy.rows[self.dataset]
        next_oid = max((row["OID"] for row in rows), default=0) + 1
        record = {"OID": next_oid}
        for field, value in zip(self.fields, values, strict=True):
            record[field] = value
        rows.append(record)
        return next_oid


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
        if self.arcpy.fail_start_operation:
            raise RuntimeError("startOperation failed")

    def stopOperation(self):
        self.calls.append(("stopOperation",))

    def abortOperation(self):
        self.calls.append(("abortOperation",))

    def stopEditing(self, save):
        self.calls.append(("stopEditing", save))


class FakeArcpy:
    def __init__(self, workspace, datasets, *, fail_start_operation=False):
        self.workspace = workspace
        self.rows = datasets
        self.editors = []
        self.fail_start_operation = fail_start_operation
        self.da = SimpleNamespace(
            SearchCursor=lambda dataset, fields, where=None: Cursor(
                self, dataset, fields, where
            ),
            UpdateCursor=lambda dataset, fields, where=None: Cursor(
                self, dataset, fields, where, True
            ),
            InsertCursor=lambda dataset, fields: InsertCursor(
                self, dataset, fields
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

    def test_edit_token_cannot_be_replayed_for_insert_or_update(self):
        insert_ops = [{
            "kind": "insert",
            "fields": ["NAME"],
            "rows": [["Inserted"]],
        }]
        insert_token = editing.edit_preflight(self.arcpy, self.first, insert_ops)["edit_token"]
        first_result = editing.edit_apply(self.arcpy, self.first, insert_ops, insert_token)
        self.assertTrue(first_result["committed"])
        self.assertEqual(len(self.arcpy.rows[self.first]), 3)
        self.assertEqual(len(self.arcpy.editors), 1)
        with self.assertRaisesRegex(RuntimeError, "已被使用"):
            editing.edit_apply(self.arcpy, self.first, insert_ops, insert_token)
        self.assertEqual(len(self.arcpy.rows[self.first]), 3)
        self.assertEqual(len(self.arcpy.editors), 1)

        update_ops = [{
            "kind": "update_geometry",
            "geometry_token": "SHAPE@WKT",
            "rows": [{"oid": 1, "geometry": "POINT (7 7)"}],
        }]
        update_token = editing.edit_preflight(self.arcpy, self.first, update_ops)["edit_token"]
        editing.edit_apply(self.arcpy, self.first, update_ops, update_token)
        with self.assertRaisesRegex(RuntimeError, "已被使用"):
            editing.edit_apply(self.arcpy, self.first, update_ops, update_token)

    def test_workspace_edit_token_cannot_be_replayed(self):
        operations = [
            {
                "dataset_path": self.first,
                "kind": "insert",
                "fields": ["NAME"],
                "rows": [["W1"]],
            },
            {
                "dataset_path": self.second,
                "kind": "insert",
                "fields": ["NAME"],
                "rows": [["W2"]],
            },
        ]
        token = editing.workspace_edit_preflight(self.arcpy, operations)["edit_token"]
        editing.workspace_edit_apply(self.arcpy, operations, token)
        self.assertEqual(len(self.arcpy.editors), 1)
        with self.assertRaisesRegex(RuntimeError, "已被使用"):
            editing.workspace_edit_apply(self.arcpy, operations, token)
        self.assertEqual(len(self.arcpy.editors), 1)
        self.assertEqual(len(self.arcpy.rows[self.first]), 3)
        self.assertEqual(len(self.arcpy.rows[self.second]), 2)

    def test_consumed_token_nonces_are_pruned_after_expiry(self):
        with editing._CONSUMED_TOKENS_LOCK:
            editing._CONSUMED_TOKENS["expired-jti"] = time.time() - 10
            editing._CONSUMED_TOKENS["active-jti"] = time.time() + 300
        try:
            self.assertFalse(editing._is_token_consumed("expired-jti", time.time()))
            with editing._CONSUMED_TOKENS_LOCK:
                self.assertNotIn("expired-jti", editing._CONSUMED_TOKENS)
                self.assertIn("active-jti", editing._CONSUMED_TOKENS)
        finally:
            with editing._CONSUMED_TOKENS_LOCK:
                editing._CONSUMED_TOKENS.pop("expired-jti", None)
                editing._CONSUMED_TOKENS.pop("active-jti", None)

    def test_start_operation_failure_stops_editing_without_saving(self):
        self.arcpy.fail_start_operation = True
        dataset_ops = [{
            "kind": "update_geometry",
            "geometry_token": "SHAPE@WKT",
            "rows": [{"oid": 1, "geometry": "POINT (4 4)"}],
        }]
        token = editing.edit_preflight(self.arcpy, self.first, dataset_ops)["edit_token"]
        with self.assertRaisesRegex(RuntimeError, "startOperation failed"):
            editing.edit_apply(self.arcpy, self.first, dataset_ops, token)
        self.assertEqual(
            self.arcpy.editors[-1].calls,
            [
                ("startEditing", False, False),
                ("startOperation",),
                ("stopEditing", False),
            ],
        )

        workspace_ops = [{
            "dataset_path": self.first,
            "kind": "update_geometry",
            "geometry_token": "SHAPE@WKT",
            "rows": [{"oid": 1, "geometry": "POINT (4 4)"}],
        }]
        ws_token = editing.workspace_edit_preflight(self.arcpy, workspace_ops)["edit_token"]
        with self.assertRaisesRegex(RuntimeError, "startOperation failed"):
            editing.workspace_edit_apply(self.arcpy, workspace_ops, ws_token)
        self.assertEqual(
            self.arcpy.editors[-1].calls,
            [
                ("startEditing", False, False),
                ("startOperation",),
                ("stopEditing", False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
