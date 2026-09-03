from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import gp_schema


class _Management:
    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner
        self.calls: list[tuple[str, list[str]]] = []

    def DeleteField(self, _table: str, names: list[str]) -> SimpleNamespace:
        self.calls.append((_table, list(names)))
        lowered = {name.casefold() for name in names}
        self.owner.fields = [field for field in self.owner.fields if field.name.casefold() not in lowered]
        return SimpleNamespace(getMessages=lambda: "deleted")


class _Arcpy:
    def __init__(self) -> None:
        self.fields = [SimpleNamespace(name="OBJECTID"), SimpleNamespace(name="OLD_A"), SimpleNamespace(name="OLD_B")]
        self.management = _Management(self)

    def TestSchemaLock(self, _path: str) -> bool:
        return True

    def ListFields(self, _path: str) -> list[SimpleNamespace]:
        return list(self.fields)


class DeleteFieldSecurityTests(unittest.TestCase):
    def test_delete_field_requires_destructive_gate_and_exact_echoes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            table = str(Path(root) / "data.gdb" / "assets")
            base_env = {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
            }
            arcpy = _Arcpy()
            with patch.dict(os.environ, base_env, clear=True):
                with self.assertRaisesRegex(RuntimeError, "ALLOW_DESTRUCTIVE"):
                    gp_schema.run_delete_field(
                        arcpy,
                        table,
                        "OLD_A;OLD_B",
                        confirm_in_table=table,
                        confirm_drop_fields=["OLD_A", "OLD_B"],
                    )

            with patch.dict(
                os.environ,
                {**base_env, "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1"},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "confirm_in_table"):
                    gp_schema.run_delete_field(
                        arcpy,
                        table,
                        "OLD_A;OLD_B",
                        confirm_in_table=table + "-wrong",
                        confirm_drop_fields=["OLD_A", "OLD_B"],
                    )
                with self.assertRaisesRegex(RuntimeError, "confirm_drop_fields"):
                    gp_schema.run_delete_field(
                        arcpy,
                        table,
                        "OLD_A;OLD_B",
                        confirm_in_table=table,
                        confirm_drop_fields=["OLD_B", "OLD_A"],
                    )
                result = gp_schema.run_delete_field(
                    arcpy,
                    table,
                    "OLD_A;OLD_B",
                    confirm_in_table=table,
                    confirm_drop_fields=["OLD_A", "OLD_B"],
                )

        self.assertTrue(result["verified"])
        self.assertEqual(result["deleted_fields"], ["OLD_A", "OLD_B"])
        self.assertEqual(arcpy.management.calls, [(os.path.normpath(table), ["OLD_A", "OLD_B"])])


if __name__ == "__main__":
    unittest.main()
