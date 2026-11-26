from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import gp_convert


class GpConvertSecurityTests(unittest.TestCase):
    def _env(self, root: str) -> dict[str, str]:
        return {
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
            "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
        }

    def test_table_to_table_rejects_traversal_absolute_semicolon_and_sde_before_arcpy(self) -> None:
        table_to_table = MagicMock()
        arcpy = SimpleNamespace(conversion=SimpleNamespace(TableToTable=table_to_table))
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, self._env(root), clear=True):
            in_rows = str(Path(root) / "input.dbf")
            out_dir = str(Path(root) / "out")
            bad_names = [
                r"..\..\outside.dbf",
                "../outside.dbf",
                r"C:\outside\evil.dbf",
                "/outside/evil.dbf",
                "sub/nested.dbf",
                r"sub\nested.dbf",
                "good.dbf;C:\\evil\\b.dbf",
                "good.dbf:ads",
                "bad\n.dbf",
                "bad\x00.dbf",
                "NUL",
                "   ",
            ]
            for bad_name in bad_names:
                with self.subTest(out_name=bad_name):
                    with self.assertRaises(RuntimeError):
                        gp_convert.run_table_to_table(arcpy, in_rows, out_dir, bad_name)

            # Writing into an .sde connection container via out_path + out_name must also fail before ArcPy.
            with self.assertRaisesRegex(RuntimeError, r"\.sde/\.ags"):
                gp_convert.run_table_to_table(
                    arcpy,
                    in_rows,
                    str(Path(root) / "prod.sde"),
                    "dbo_HackTable",
                )

            # Semicolon injection in input/output paths must also fail before ArcPy.
            with self.assertRaises(RuntimeError):
                gp_convert.run_table_to_table(
                    arcpy,
                    rf"{in_rows};C:\Windows\System32\secret.dbf",
                    out_dir,
                    "ok.dbf",
                )
            with self.assertRaises(RuntimeError):
                gp_convert.run_table_to_table(
                    arcpy,
                    in_rows,
                    rf"{out_dir};C:\evil",
                    "ok.dbf",
                )
        table_to_table.assert_not_called()

    def test_import_csv_to_table_rejects_traversal_absolute_and_semicolon_before_arcpy(self) -> None:
        table_to_table = MagicMock()
        arcpy = SimpleNamespace(conversion=SimpleNamespace(TableToTable=table_to_table))
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, self._env(root), clear=True):
            in_csv = str(Path(root) / "records.csv")
            out_dir = str(Path(root) / "out")
            for bad_name in (
                r"..\outside.dbf",
                r"..\..\outside.dbf",
                r"C:\evil\outside.dbf",
                "good.dbf;evil.dbf",
                "records:stream.dbf",
                "bad\r\n.dbf",
            ):
                with self.subTest(out_name=bad_name):
                    with self.assertRaises(RuntimeError):
                        gp_convert.run_import_csv_to_table(arcpy, in_csv, out_dir, bad_name)
        table_to_table.assert_not_called()

    def test_valid_table_to_table_and_import_csv_succeed(self) -> None:
        table_to_table = MagicMock()
        arcpy = SimpleNamespace(conversion=SimpleNamespace(TableToTable=table_to_table))
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, self._env(root), clear=True):
            in_rows = str(Path(root) / "input.dbf")
            in_csv = str(Path(root) / "records.csv")
            out_dir = str(Path(root) / "out")

            result1 = gp_convert.run_table_to_table(arcpy, in_rows, out_dir, "copied.dbf")
            result2 = gp_convert.run_import_csv_to_table(arcpy, in_csv, out_dir, "imported.dbf")

            self.assertEqual(result1, os.path.normpath(os.path.join(out_dir, "copied.dbf")))
            self.assertEqual(result2, os.path.normpath(os.path.join(out_dir, "imported.dbf")))
            self.assertEqual(table_to_table.call_count, 2)
            table_to_table.assert_any_call(os.path.normpath(in_rows), os.path.normpath(out_dir), "copied.dbf")
            table_to_table.assert_any_call(os.path.normpath(in_csv), os.path.normpath(out_dir), "imported.dbf")


if __name__ == "__main__":
    unittest.main()
