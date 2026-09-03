from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import enterprise_gdb


class _Result:
    messageCount = 1

    def getMessage(self, index: int) -> str:
        return f"message-{index}"


def _version(name: str, *, parent: str = "SDE.DEFAULT") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        access="Private",
        description=f"description for {name}",
        isOwner=True,
        created=datetime(2026, 1, 2, 3, 4, 5),
        lastModified=datetime(2026, 1, 3, 4, 5, 6),
        parentVersionName=parent,
        ancestors=[SimpleNamespace(name=parent)] if name != parent else [],
        children=[],
    )


class _Management:
    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def CreateVersion(self, *args: object) -> _Result:
        self.calls.append(("CreateVersion", args))
        self.owner.versions.append(_version(f"OWNER.{args[2]}", parent=str(args[1])))
        return _Result()

    def ChangeVersion(self, *args: object) -> _Result:
        self.calls.append(("ChangeVersion", args))
        member = args[0]
        member.connectionProperties["connection_info"]["version"] = str(args[2])
        return _Result()

    def ReconcileVersions(self, *args: object) -> _Result:
        self.calls.append(("ReconcileVersions", args))
        return _Result()

    def DeleteVersion(self, *args: object) -> _Result:
        self.calls.append(("DeleteVersion", args))
        target = str(args[1])
        self.owner.versions = [item for item in self.owner.versions if item.name != target]
        return _Result()

    def RegisterAsVersioned(self, *args: object) -> _Result:
        self.calls.append(("RegisterAsVersioned", args))
        self.owner.versioned = True
        return _Result()

    def UnregisterAsVersioned(self, *args: object) -> _Result:
        self.calls.append(("UnregisterAsVersioned", args))
        self.owner.versioned = False
        return _Result()


class _Arcpy:
    def __init__(self) -> None:
        self.versions = [_version("SDE.DEFAULT"), _version("OWNER.EditA")]
        self.versioned = False
        self.management = _Management(self)
        self.da = SimpleNamespace(ListVersions=lambda _workspace: list(self.versions))

    def Exists(self, _path: str) -> bool:
        return True

    def TestSchemaLock(self, _path: str) -> bool:
        return True

    def Describe(self, _path: str) -> SimpleNamespace:
        return SimpleNamespace(isVersioned=self.versioned)


class EnterpriseGdbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = str(Path(self.temp_dir.name) / "owner.sde")
        self.dataset = str(Path(self.workspace) / "OWNER.Roads")
        self.write_env = {
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
            "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE": "1",
            "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp_dir.name,
        }
        self.destructive_env = {
            **self.write_env,
            "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
        }

    def test_list_versions_serializes_rich_version_objects_and_allowlists_urls(self) -> None:
        arcpy = _Arcpy()
        with patch.dict(os.environ, self.write_env, clear=True):
            result = enterprise_gdb.list_versions(arcpy, self.workspace)
        self.assertTrue(result["detailed"])
        self.assertEqual(result["version_count"], 2)
        self.assertEqual(result["versions"][1]["parent_version_name"], "SDE.DEFAULT")
        self.assertEqual(result["versions"][1]["created"], "2026-01-02T03:04:05")

        url = "https://server.example.com/server/rest/services/Roads/FeatureServer"
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_SERVER_ALLOWLIST": url},
            clear=True,
        ):
            self.assertEqual(
                enterprise_gdb.list_versions(arcpy, url)["workspace_path"],
                url,
            )
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_SERVER_ALLOWLIST": "https://server.example.com/server"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "FeatureServer"):
                enterprise_gdb.list_versions(arcpy, url)

    def test_create_version_requires_enterprise_gate_and_verifies_created_name(self) -> None:
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
                enterprise_gdb.create_version(
                    arcpy,
                    self.workspace,
                    "SDE.DEFAULT",
                    "Maintenance",
                )
        with patch.dict(os.environ, self.write_env, clear=True):
            result = enterprise_gdb.create_version(
                arcpy,
                self.workspace,
                "SDE.DEFAULT",
                "Maintenance",
                access_permission="protected",
                description="September work",
            )
        self.assertEqual(result["version_name"], "OWNER.Maintenance")
        self.assertEqual(
            arcpy.management.calls[-1][1],
            (
                self.workspace,
                "SDE.DEFAULT",
                "Maintenance",
                "PROTECTED",
                "September work",
            ),
        )

    def test_change_version_uses_resolved_member_and_parses_history_date(self) -> None:
        arcpy = _Arcpy()
        member = SimpleNamespace(connectionProperties={"connection_info": {}})
        with patch.dict(os.environ, self.write_env, clear=True):
            result = enterprise_gdb.change_version(
                arcpy,
                member,
                "transactional",
                version_name="OWNER.EditA",
                include_participating=False,
            )
            historical = enterprise_gdb.change_version(
                arcpy,
                member,
                "historical",
                history_date="2026-09-03T08:30:00+08:00",
            )
        self.assertTrue(result["verified"])
        self.assertEqual(arcpy.management.calls[0][1][-1], "EXCLUDE")
        self.assertIsInstance(arcpy.management.calls[1][1][3], datetime)
        self.assertEqual(historical["history_date"], "2026-09-03T08:30:00+08:00")

    def test_reconcile_always_requires_destructive_gate_and_exact_scope(self) -> None:
        arcpy = _Arcpy()
        arguments = {
            "target_version": "SDE.DEFAULT",
            "edit_versions": ["OWNER.EditA"],
            "confirm_action": "RECONCILE_AND_POST",
            "confirm_target_version": "SDE.DEFAULT",
            "confirm_edit_versions": ["OWNER.EditA"],
            "with_post": True,
        }
        with patch.dict(os.environ, self.write_env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "破坏性"):
                enterprise_gdb.reconcile_versions(arcpy, self.workspace, **arguments)
        with patch.dict(os.environ, self.destructive_env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "confirm_edit_versions"):
                enterprise_gdb.reconcile_versions(
                    arcpy,
                    self.workspace,
                    **{**arguments, "confirm_edit_versions": []},
                )
            result = enterprise_gdb.reconcile_versions(
                arcpy,
                self.workspace,
                **arguments,
            )
        self.assertTrue(result["posted"])
        call = arcpy.management.calls[-1]
        self.assertEqual(call[0], "ReconcileVersions")
        self.assertEqual(call[1][8:10], ("POST", "KEEP_VERSION"))

    def test_post_and_delete_require_exact_target_and_reject_default(self) -> None:
        arcpy = _Arcpy()
        with patch.dict(os.environ, self.destructive_env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "精确回显"):
                enterprise_gdb.post_version(
                    arcpy,
                    self.workspace,
                    "OWNER.EditA",
                    "SDE.DEFAULT",
                )
            posted = enterprise_gdb.post_version(
                arcpy,
                self.workspace,
                "OWNER.EditA",
                "SDE.DEFAULT",
                confirm_version_name="OWNER.EditA",
                confirm_target_version="SDE.DEFAULT",
                confirm_action="RECONCILE_AND_POST",
            )
            self.assertTrue(posted["irreversible"])
            self.assertEqual(arcpy.management.calls[-1][0], "ReconcileVersions")
            self.assertEqual(
                arcpy.management.calls[-1][1][1:10],
                (
                    "ALL_VERSIONS",
                    "SDE.DEFAULT",
                    ["OWNER.EditA"],
                    "LOCK_ACQUIRED",
                    "ABORT_CONFLICTS",
                    "BY_OBJECT",
                    "FAVOR_TARGET_VERSION",
                    "POST",
                    "KEEP_VERSION",
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "Default"):
                enterprise_gdb.delete_version(
                    arcpy,
                    self.workspace,
                    "SDE.DEFAULT",
                    confirm_version_name="SDE.DEFAULT",
                )
            deleted = enterprise_gdb.delete_version(
                arcpy,
                self.workspace,
                "OWNER.EditA",
                confirm_version_name="OWNER.EditA",
            )
        self.assertTrue(deleted["deleted"])
        self.assertNotIn("OWNER.EditA", [item.name for item in arcpy.versions])

    def test_register_and_unregister_verify_state_and_discard_confirmation(self) -> None:
        arcpy = _Arcpy()
        with patch.dict(os.environ, self.write_env, clear=True):
            registered = enterprise_gdb.register_as_versioned(
                arcpy,
                self.dataset,
                edit_to_base="NO_EDITS_TO_BASE",
            )
        self.assertTrue(registered["changed"])

        with patch.dict(os.environ, self.destructive_env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "confirm_dataset_path"):
                enterprise_gdb.unregister_as_versioned(arcpy, self.dataset)
            with self.assertRaisesRegex(RuntimeError, "DISCARD_OUTSTANDING_EDITS"):
                enterprise_gdb.unregister_as_versioned(
                    arcpy,
                    self.dataset,
                    keep_edit="NO_KEEP_EDIT",
                    confirm_dataset_path=self.dataset,
                )
            result = enterprise_gdb.unregister_as_versioned(
                arcpy,
                self.dataset,
                keep_edit="NO_KEEP_EDIT",
                compress_default="COMPRESS_DEFAULT",
                confirm_dataset_path=self.dataset,
                confirm_discard_edits="DISCARD_OUTSTANDING_EDITS",
            )
        self.assertTrue(result["discarded_outstanding_edits"])
        self.assertFalse(arcpy.versioned)


if __name__ == "__main__":
    unittest.main()
