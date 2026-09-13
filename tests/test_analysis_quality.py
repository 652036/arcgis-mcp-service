from __future__ import annotations

import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import analysis_quality as qa
from arcgis_pro_mcp import gp_raster, raster_runtime


class QualityContractTests(unittest.TestCase):
    def test_missing_warning_na_and_failed_never_qualify(self):
        required = {"grid", "coverage"}
        for checks in (
            [],
            [qa.check("grid", True)],
            [qa.check("grid", True), qa.check("coverage", False)],
            [qa.check("grid", True), {"name": "coverage", "status": "NOT_APPLICABLE"}],
            [qa.check("grid", True), qa.check("coverage", True), {"name": "extra", "status": "WARNING"}],
        ):
            self.assertFalse(qa.assess("SUCCEEDED", checks, required)["eligible_for_downstream"])
        passed = [qa.check("grid", True), qa.check("coverage", True)]
        self.assertTrue(qa.assess("SUCCEEDED", passed, required)["eligible_for_downstream"])
        self.assertFalse(qa.assess("UNKNOWN", passed, required)["eligible_for_downstream"])
        with self.assertRaisesRegex(RuntimeError, "DUPLICATE"):
            qa.assess("SUCCEEDED", passed + passed, required)

    def test_existence_is_not_quality(self):
        for exists in (None, True, False):
            result = qa.existence_evidence(exists)
            self.assertFalse(result["eligible_for_downstream"])
            self.assertEqual(result["verified"], exists is True)

    def test_fixed_denominator_and_grid(self):
        self.assertEqual(qa.coverage_counts(100, 50), 0.5)
        self.assertEqual(qa.coverage_counts(100, 0), 0)
        with self.assertRaisesRegex(RuntimeError, "EMPTY_EXPECTED"):
            qa.coverage_counts(0, 0)
        a = dict(x0=0, y0=10, dx=1, dy=1, rows=10, cols=10)
        self.assertEqual(qa.grid_offset(a, dict(a, x0=1)), (1, 0))
        with self.assertRaisesRegex(RuntimeError, "GRID_WINDOW"):
            qa.grid_offset(a, dict(a, x0=1), same_window=True)
        with self.assertRaisesRegex(RuntimeError, "GRID_ORIGIN"):
            qa.grid_offset(a, dict(a, x0=0.5))
        for value in (True, math.nan, math.inf):
            with self.assertRaises(RuntimeError):
                qa.finite_number(value, "fixture")

    def test_environment_states(self):
        self.assertEqual(
            raster_runtime.validate_environment(
                {"mask": {"state": "CLEAR"}, "extent": {"state": "UNSET"}, "cell_size": {"state": "VALUE", "value": 30}}
            ),
            {"mask": None, "cellSize": 30},
        )
        self.assertEqual(raster_runtime.validate_environment({"mask": None}), {})
        for value in (True, math.nan, -1):
            with self.assertRaises(RuntimeError):
                raster_runtime.validate_environment({"cell_size": value})
        with self.assertRaises(RuntimeError):
            raster_runtime.validate_environment({"mask": {"state": "CLEAR", "value": "anything"}})

    def test_checked_tool_policies(self):
        from arcgis_pro_mcp.tool_protocol import tool_policy

        for name in ("arcgis_pro_analysis_asset_info", "arcgis_pro_analysis_result_status"):
            p = tool_policy(name)
            self.assertTrue(p["read_only"])
            self.assertNotIn("ARCGIS_PRO_MCP_ALLOW_WRITE", p["gates"])
        p = tool_policy("arcgis_pro_gp_clip_raster_checked")
        self.assertFalse(p["read_only"])
        self.assertIn("ARCGIS_PRO_MCP_ALLOW_WRITE", p["gates"])
        self.assertIn("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT", p["gates"])

    def test_calculator_signature_save_and_no_retry(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
                },
            ),
        ):
            inp = Path(root, "in.tif")
            inp.touch()
            out = str(Path(root, "out.tif"))
            result = SimpleNamespace(save=MagicMock())
            calc = MagicMock(return_value=result)
            arcpy = SimpleNamespace(ia=SimpleNamespace(RasterCalculator=calc), Exists=lambda p: False)
            gp_raster.run_raster_calculator(arcpy, "x * 2", out, {"x": str(inp)})
            calc.assert_called_once_with([str(inp)], ["x"], "x * 2")
            result.save.assert_called_once_with(out)
            calc.reset_mock()
            calc.side_effect = TypeError("actual failure")
            with self.assertRaises(TypeError):
                gp_raster.run_raster_calculator(arcpy, "x + 1", out, {"x": str(inp)})
            self.assertEqual(calc.call_count, 1)
            for expr in ('__import__("os")', "x.__class__", "x[0]", "open(x)", "True"):
                with self.assertRaises(RuntimeError):
                    gp_raster.run_raster_calculator(arcpy, expr, out, {"x": str(inp)})


if __name__ == "__main__":
    unittest.main()
