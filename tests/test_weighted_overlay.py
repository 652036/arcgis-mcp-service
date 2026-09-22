from __future__ import annotations

import copy
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import gp_generic, weighted_overlay


class WeightedOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {
            "ARCGIS_PRO_MCP_INPUT_ROOTS": str(self.root),
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": str(self.root),
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.sa = SimpleNamespace(RemapValue=MagicMock(side_effect=lambda v: v), WOTable=MagicMock())
        self.native = MagicMock()
        self.arcpy = SimpleNamespace(
            sa=self.sa, gp=SimpleNamespace(WeightedOverlay_sa=self.native),
            ListTools=lambda: ["WeightedOverlay_sa"],
            GetParameterInfo=lambda _: [
                SimpleNamespace(name="in_weighted_overlay_table", direction="Input", parameterType="Required"),
                SimpleNamespace(name="out_raster", direction="Output", parameterType="Required"),
            ],
            Exists=lambda _: False, EnvManager=lambda **_: nullcontext(),
        )
        self.table = {"evaluation_scale": [1, 9, 1], "rasters": [{
            "raster": str(self.root / "landuse.tif"), "influence": 100,
            "field": "Value", "remap": [[1, 5], [2, "NODATA"], [3, "RESTRICTED"]],
        }]}

    def test_both_registered_spellings_receive_native_table_and_controlled_output(self):
        for name in ("sa.WeightedOverlay", "WeightedOverlay_sa"):
            with self.subTest(name=name):
                gp_generic.run_tool(self.arcpy, name, {
                    "in_weighted_overlay_table": self.table,
                    "out_raster": str(self.root / "output.tif"),
                })
                self.native.assert_called_with(str(self.sa.WOTable.return_value), str(self.root / "output.tif"))
        self.sa.WOTable.assert_called_with([
            [str(self.root / "landuse.tif"), 100, "Value", [[1, 5], [2, "NODATA"], [3, "RESTRICTED"]]],
        ], [1, 9, 1])

    def test_every_embedded_raster_is_root_checked(self):
        second = copy.deepcopy(self.table["rasters"][0])
        second.update(raster=str(self.root.parent / "outside.tif"), influence=50)
        self.table["rasters"][0]["influence"] = 50
        self.table["rasters"].append(second)
        with self.assertRaises(RuntimeError):
            weighted_overlay.native_table(self.arcpy, self.table)
        self.sa.WOTable.assert_not_called()

    def test_weights_are_integer_percentages_summing_to_100(self):
        for weight in (True, 100.0, -1, 101, 99):
            with self.subTest(weight=weight), self.assertRaises(RuntimeError):
                self.table["rasters"][0]["influence"] = weight
                weighted_overlay.native_table(self.arcpy, self.table)
        self.sa.WOTable.assert_not_called()

    def test_invalid_scale_and_out_of_scale_remap_are_rejected(self):
        for scale in ([1, 1, 1], [1, 9, 0], [1, 9, 3], [True, 9, 1], [1, 9]):
            with self.subTest(scale=scale), self.assertRaises(RuntimeError):
                self.table["evaluation_scale"] = scale
                weighted_overlay.native_table(self.arcpy, self.table)
        self.table["evaluation_scale"] = [1, 9, 2]
        self.table["rasters"][0]["remap"] = [[1, 2]]
        with self.assertRaises(RuntimeError):
            weighted_overlay.native_table(self.arcpy, self.table)

    def test_duplicate_nonfinite_and_ambiguous_remaps_are_rejected(self):
        for remap in ([[1, 1], [1, 2]], [[float("nan"), 1]], [[1, float("inf")]], [[1, "bad"]], [[1, 2, 3]], []):
            with self.subTest(remap=remap), self.assertRaises(RuntimeError):
                self.table["rasters"][0]["remap"] = remap
                weighted_overlay.native_table(self.arcpy, self.table)

    def test_raw_native_text_and_unknown_fields_are_not_a_validation_bypass(self):
        for value in ("('outside.tif' 100 'Value' (1 1)); 1 9 1", {}, dict(self.table, code="run()")):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                weighted_overlay.native_table(self.arcpy, value)

    def test_serialization_delimiters_are_rejected(self):
        for path in ("a'b.tif", "a;b.tif", "a\nb.tif"):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.table["rasters"][0]["raster"] = str(self.root / path)
                weighted_overlay.native_table(self.arcpy, self.table)
        self.native.assert_not_called()

    def test_invalid_output_is_rejected_before_native_execution(self):
        with self.assertRaises(RuntimeError):
            gp_generic.run_tool(self.arcpy, "sa.WeightedOverlay", {
                "in_weighted_overlay_table": self.table,
                "out_raster": str(self.root.parent / "outside.tif"),
            })
        self.native.assert_not_called()
