"""Opt-in native GP dispatch test using only generated temporary datasets."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import gp_generic


@unittest.skipUnless(os.environ.get("ARCGIS_MCP_RUN_NATIVE_QA") == "1", "requires explicit native QA opt-in")
class NativeGenericGPTests(unittest.TestCase):
    def test_spatial_analyst_native_gp_without_top_level_alias(self) -> None:
        import arcpy
        import numpy as np

        with tempfile.TemporaryDirectory(prefix="native_sa_") as temporary, patch.dict(os.environ, {
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": temporary,
            "ARCGIS_PRO_MCP_INPUT_ROOTS": temporary,
        }):
            os.environ.pop("ARCGIS_PRO_MCP_ENABLE_GENERIC_GP", None)
            root = Path(temporary).resolve()
            source, output = str(root / "input.tif"), str(root / "abs.tif")
            try:
                raster = arcpy.NumPyArrayToRaster(np.array([[-3, 0, 7]], dtype="int16"), arcpy.Point(0, 0), 1, 1)
                raster.save(source)
                del raster
                gp_generic.run_tool(arcpy, "sa.Abs", {"out_raster": output, "in_raster_or_constant": source})
                np.testing.assert_array_equal(arcpy.RasterToNumPyArray(output), [[3, 0, 7]])
            finally:
                arcpy.management.ClearWorkspaceCache()

    def test_default_native_dispatch_without_deployment_allowlist(self) -> None:
        import arcpy

        with tempfile.TemporaryDirectory(prefix="native_gp_") as temporary, patch.dict(os.environ, {
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
            "ARCGIS_PRO_MCP_INPUT_ROOTS": temporary,
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": temporary,
        }):
            os.environ.pop("ARCGIS_PRO_MCP_ENABLE_GENERIC_GP", None)
            os.environ.pop("ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST", None)
            root = Path(temporary).resolve()
            source, buffered, copied = (str(root / name) for name in ("points.shp", "buffer.shp", "copy.shp"))
            try:
                arcpy.management.CreateFeatureclass(str(root), "points.shp", "POINT", spatial_reference=arcpy.SpatialReference(32650))
                with arcpy.da.InsertCursor(source, ["SHAPE@XY"]) as rows:
                    rows.insertRow([(500000, 1000000)])
                    rows.insertRow([(500100, 1000000)])
                del rows
                self.assertTrue(gp_generic.generic_gp_enabled())
                gp_generic.run_tool(arcpy, "analysis.Buffer", {
                    "in_features": source, "out_feature_class": buffered,
                    "buffer_distance_or_field": "10 Meters", "dissolve_option": "NONE",
                })
                self.assertEqual(int(arcpy.management.GetCount(buffered)[0]), 2)
                self.assertEqual(arcpy.Describe(buffered).shapeType, "Polygon")
                with arcpy.da.SearchCursor(buffered, ["SHAPE@AREA"]) as rows:
                    for (area,) in rows:
                        self.assertGreater(area, 300)
                        self.assertLess(area, 320)
                del rows
                os.environ["ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST"] = "unrelated.Tool"
                gp_generic.run_tool(arcpy, "CopyFeatures_management", {
                    "in_features": buffered, "out_feature_class": copied,
                })
                self.assertEqual(int(arcpy.management.GetCount(copied)[0]), 2)
                with self.assertRaisesRegex(RuntimeError, "拒绝覆盖已有输出"):
                    gp_generic.run_tool(arcpy, "management.CopyFeatures", {
                        "in_features": source, "out_feature_class": copied,
                    })
                self.assertEqual(int(arcpy.management.GetCount(source)[0]), 2)
            finally:
                arcpy.management.ClearWorkspaceCache()


if __name__ == "__main__":
    unittest.main()
