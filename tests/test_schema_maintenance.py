from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import schema_maintenance


class _Result:
    messageCount = 1

    def getMessage(self, index: int) -> str:
        return f"message-{index}"


class _Management:
    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def AddIndex(self, *args: object) -> _Result:
        self.calls.append(("AddIndex", args))
        self.owner.indexes.append(
            SimpleNamespace(
                name=str(args[2]),
                isUnique=args[3] == "UNIQUE",
                isAscending=args[4] == "ASCENDING",
                fields=[SimpleNamespace(name=value) for value in args[1]],
            )
        )
        return _Result()

    def RemoveIndex(self, *args: object) -> _Result:
        self.calls.append(("RemoveIndex", args))
        self.owner.indexes = [item for item in self.owner.indexes if item.name != args[1]]
        return _Result()

    def RebuildIndexes(self, *args: object) -> _Result:
        self.calls.append(("RebuildIndexes", args))
        return _Result()

    def AnalyzeDatasets(self, *args: object) -> _Result:
        self.calls.append(("AnalyzeDatasets", args))
        return _Result()

    def EnableEditorTracking(self, *args: object) -> _Result:
        self.calls.append(("EnableEditorTracking", args))
        self.owner.tracking_enabled = True
        self.owner.tracking_fields = tuple(str(value) for value in args[1:5])
        return _Result()

    def DisableEditorTracking(self, *args: object) -> _Result:
        self.calls.append(("DisableEditorTracking", args))
        if all(str(value).startswith("DISABLE_") for value in args[1:5]):
            self.owner.tracking_enabled = False
        return _Result()

    def AddGlobalIDs(self, *args: object) -> _Result:
        self.calls.append(("AddGlobalIDs", args))
        raw = args[0]
        paths = raw if isinstance(raw, list) else [raw]
        for path in paths:
            self.owner.global_ids[str(path)] = "GLOBALID"
        return _Result()


class _Arcpy:
    def __init__(self) -> None:
        self.indexes = [
            SimpleNamespace(
                name="IDX_NAME",
                isUnique=False,
                isAscending=True,
                fields=[SimpleNamespace(name="NAME")],
            )
        ]
        self.fields = [
            SimpleNamespace(name="OBJECTID", type="OID"),
            SimpleNamespace(name="NAME", type="String"),
            SimpleNamespace(name="CREATED_BY", type="String"),
            SimpleNamespace(name="CREATED_AT", type="Date"),
            SimpleNamespace(name="EDITED_BY", type="String"),
            SimpleNamespace(name="EDITED_AT", type="Date"),
        ]
        self.tracking_enabled = False
        self.tracking_fields = ("", "", "", "")
        self.global_ids: dict[str, str] = {}
        self.management = _Management(self)

    def Exists(self, _path: str) -> bool:
        return True

    def TestSchemaLock(self, _path: str) -> bool:
        return True

    def ListFields(self, path: str) -> list[SimpleNamespace]:
        values = list(self.fields)
        if path in self.global_ids:
            values.append(SimpleNamespace(name=self.global_ids[path], type="GlobalID"))
        return values

    def ListIndexes(self, _path: str) -> list[SimpleNamespace]:
        return list(self.indexes)

    def Describe(self, path: str) -> SimpleNamespace:
        creator, created, editor, edited = self.tracking_fields
        return SimpleNamespace(
            isVersioned=True,
            editorTrackingEnabled=self.tracking_enabled,
            creatorFieldName=creator,
            createdAtFieldName=created,
            editorFieldName=editor,
            editedAtFieldName=edited,
            isTimeInUTC=True,
            globalIDFieldName=self.global_ids.get(path, ""),
        )


class SchemaMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = str(Path(self.temp_dir.name) / "owner.sde")
        self.dataset = str(Path(self.workspace) / "OWNER.Roads")
        self.other_dataset = str(Path(self.workspace) / "OWNER.Bridges")
        self.write_env = {
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
            "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE": "1",
            "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp_dir.name,
        }
        self.destructive_env = {
            **self.write_env,
            "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
        }

    def test_info_reports_index_tracking_global_id_and_version_state(self) -> None:
        arcpy = _Arcpy()
        arcpy.global_ids[self.dataset] = "GLOBALID"
        arcpy.tracking_enabled = True
        arcpy.tracking_fields = ("CREATED_BY", "CREATED_AT", "EDITED_BY", "EDITED_AT")
        with patch.dict(os.environ, self.write_env, clear=True):
            result = schema_maintenance.dataset_maintenance_info(arcpy, self.dataset)
        self.assertTrue(result["is_versioned"])
        self.assertEqual(result["indexes"][0]["fields"], ["NAME"])
        self.assertEqual(result["global_id_field"], "GLOBALID")
        self.assertEqual(result["editor_tracking"]["creator_field"], "CREATED_BY")

    def test_add_and_remove_index_enforce_gates_canonical_fields_and_confirmation(self) -> None:
        arcpy = _Arcpy()
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp_dir.name,
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "企业级"):
                schema_maintenance.add_index(
                    arcpy,
                    self.dataset,
                    ["objectid"],
                    "IDX_OID",
                )
        with patch.dict(os.environ, self.write_env, clear=True):
            added = schema_maintenance.add_index(
                arcpy,
                self.dataset,
                ["objectid"],
                "IDX_OID",
                unique="unique",
                ascending="ascending",
            )
        self.assertEqual(added["index"]["fields"], ["OBJECTID"])
        self.assertEqual(
            arcpy.management.calls[-1][1],
            (self.dataset, ["OBJECTID"], "IDX_OID", "UNIQUE", "ASCENDING"),
        )

        with patch.dict(os.environ, self.destructive_env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "精确回显"):
                schema_maintenance.remove_index(arcpy, self.dataset, "IDX_OID")
            removed = schema_maintenance.remove_index(
                arcpy,
                self.dataset,
                "IDX_OID",
                confirm_index_name="IDX_OID",
            )
        self.assertTrue(removed["removed"])
        self.assertNotIn("IDX_OID", [item.name for item in arcpy.indexes])

    def test_rebuild_and_analyze_use_relative_dataset_names_and_typed_flags(self) -> None:
        arcpy = _Arcpy()
        with patch.dict(os.environ, self.write_env, clear=True):
            rebuilt = schema_maintenance.rebuild_indexes(
                arcpy,
                self.workspace,
                ["OWNER.Roads"],
                delta_only="all",
            )
            analyzed = schema_maintenance.analyze_datasets(
                arcpy,
                self.workspace,
                ["OWNER.Roads"],
                analyze_archive=False,
            )
            with self.assertRaisesRegex(RuntimeError, "相对 workspace"):
                schema_maintenance.rebuild_indexes(
                    arcpy,
                    self.workspace,
                    ["C:OWNER.Roads"],
                )
        self.assertEqual(rebuilt["delta_only"], "ALL")
        self.assertTrue(analyzed["analyzed"])
        self.assertEqual(
            arcpy.management.calls[0][1],
            (self.workspace, "NO_SYSTEM", ["OWNER.Roads"], "ALL"),
        )
        self.assertEqual(arcpy.management.calls[1][1][-1], "NO_ANALYZE_ARCHIVE")

    def test_enable_and_disable_editor_tracking_verify_describe_state(self) -> None:
        arcpy = _Arcpy()
        with patch.dict(os.environ, self.write_env, clear=True):
            enabled = schema_maintenance.enable_editor_tracking(
                arcpy,
                self.dataset,
                "CREATED_BY",
                "CREATED_AT",
                "EDITED_BY",
                "EDITED_AT",
                add_fields=False,
                record_dates_in="utc",
            )
        self.assertTrue(enabled["editor_tracking"]["enabled"])
        self.assertEqual(arcpy.management.calls[-1][1][-2:], ("NO_ADD_FIELDS", "UTC"))

        with patch.dict(os.environ, self.write_env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "破坏性"):
                schema_maintenance.disable_editor_tracking(
                    arcpy,
                    self.dataset,
                    confirm_dataset_path=self.dataset,
                )
        with patch.dict(os.environ, self.destructive_env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "精确回显"):
                schema_maintenance.disable_editor_tracking(
                    arcpy,
                    self.dataset,
                    confirm_dataset_path=self.other_dataset,
                )
            disabled = schema_maintenance.disable_editor_tracking(
                arcpy,
                self.dataset,
                confirm_dataset_path=self.dataset,
            )
        self.assertTrue(disabled["fields_retained"])
        self.assertFalse(disabled["editor_tracking"]["enabled"])

    def test_add_global_ids_is_bounded_verified_and_idempotent(self) -> None:
        arcpy = _Arcpy()
        arcpy.global_ids[self.other_dataset] = "GLOBALID"
        with patch.dict(os.environ, self.write_env, clear=True):
            result = schema_maintenance.add_global_ids(
                arcpy,
                [self.dataset, self.other_dataset],
            )
            repeated = schema_maintenance.add_global_ids(
                arcpy,
                [self.dataset, self.other_dataset],
            )
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["already_present_count"], 1)
        self.assertFalse(repeated["changed"])
        add_calls = [call for call in arcpy.management.calls if call[0] == "AddGlobalIDs"]
        self.assertEqual(len(add_calls), 1)
        self.assertEqual(add_calls[0][1], (self.dataset,))


if __name__ == "__main__":
    unittest.main()
