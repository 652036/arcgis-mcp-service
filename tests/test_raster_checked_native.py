"""Opt-in native ArcPy tests, restricted to generated temporary fixtures."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import raster_checked as rc


@unittest.skipUnless(os.environ.get("ARCGIS_MCP_RUN_NATIVE_QA") == "1", "requires explicit native QA opt-in")
class NativeCheckedClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import arcpy
        import numpy as np
        from osgeo import gdal, ogr, osr

        cls.arcpy, cls.np, cls.gdal, cls.ogr = arcpy, np, gdal, ogr
        cls.tmp = tempfile.TemporaryDirectory(prefix="gis_reliability_")
        cls.root = Path(cls.tmp.name)
        cls.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": str(cls.root),
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": str(cls.root),
            },
        )
        cls.env.start()
        cls.sr = osr.SpatialReference()
        cls.sr.ImportFromEPSG(32631)
        cls.wkt = cls.sr.ExportToWkt()
        cls.full = cls.raster("full", np.arange(100, dtype="float32").reshape(10, 10))
        cls.grid = rc.asset_info(cls.full, "GRID_REFERENCE", "synthetic independent 10x10 reference")
        cls.source = rc.asset_info(cls.full, "CONTINUOUS", "synthetic numbered pixels")
        cls.results = []

    @classmethod
    def tearDownClass(cls):
        report = os.environ.get("ARCGIS_MCP_NATIVE_QA_REPORT")
        if report:
            Path(report).write_text(
                json.dumps({"arcgis": cls.arcpy.GetInstallInfo()["Version"], "results": cls.results}, indent=2),
                encoding="utf-8",
            )
        cls.env.stop()
        cls.arcpy.management.ClearWorkspaceCache()
        cls.tmp.cleanup()

    @classmethod
    def raster(cls, name, values, x=500000, y=1000):
        path = str(cls.root / (name + ".tif"))
        ds = cls.gdal.GetDriverByName("GTiff").Create(path, values.shape[1], values.shape[0], 1, cls.gdal.GDT_Float32)
        ds.SetGeoTransform((x, 1, 0, y, 0, -1))
        ds.SetProjection(cls.wkt)
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(-9999)
        band.WriteArray(values)
        ds.FlushCache()
        ds = None
        return path

    @classmethod
    def boundary(cls, name, geometry):
        path = str(cls.root / (name + ".shp"))
        ds = cls.ogr.GetDriverByName("ESRI Shapefile").CreateDataSource(path)
        layer = ds.CreateLayer(name, cls.sr, cls.ogr.wkbPolygon)
        f = cls.ogr.Feature(layer.GetLayerDefn())
        f.SetGeometry(cls.ogr.CreateGeometryFromWkt(geometry))
        layer.CreateFeature(f)
        f = None
        ds = None
        return rc.asset_info(path, "REPORTING_AOI", "synthetic " + name)

    def run_case(self, name, expected, covered, source=None, boundary=None, minimum=1, reason=""):
        args = dict(
            run_id=str(uuid.uuid4()),
            source_asset=source or self.source,
            grid_asset=self.grid,
            clip_mode="POLYGON_MASK" if boundary else "RECTANGLE",
            minimum_coverage=minimum,
            boundary_asset=boundary,
            missing_data_reason=reason,
        )
        if not boundary:
            args.update(rectangle=[500000, 990, 500010, 1000], rectangle_crs=self.wkt)
        result = rc.run_clip_checked(self.arcpy, **args)
        self.results.append({"case": name, "result": result})
        self.assertEqual(result["execution_status"], "SUCCEEDED", result)
        metrics = next(c["metrics"] for c in result["checks"] if c["name"] == "coverage")
        self.assertEqual(metrics["expected_cells"], expected, result)
        self.assertEqual(metrics["valid_cells"], covered, result)
        self.assertEqual(result["eligible_for_downstream"], covered > 0 and covered / expected >= minimum, result)
        output_path = Path(result["output_raster"])
        before = (output_path.stat().st_mtime_ns, rc._hash(output_path))
        self.assertEqual(rc.run_clip_checked(self.arcpy, **args)["run_id"], result["run_id"])
        self.assertEqual(before, (output_path.stat().st_mtime_ns, rc._hash(output_path)))
        return result

    def test_native_algebra_and_reclassification(self):
        from arcgis_pro_mcp import gp_raster
        from arcgis_pro_mcp.raster_runtime import checked_out_extension

        with checked_out_extension(self.arcpy, "Spatial"):
            path = self.raster("thresholds", self.np.array([[9.5, 10, 10.5]], dtype="float32"))
            output = str(self.root / "reclassified.tif")
            gp_raster.run_reclassify(self.arcpy, path, "Value", "0 10 1;10 20 3", output, "ERROR")
            ds = self.gdal.Open(output)
            self.assertEqual(ds.ReadAsArray().tolist(), [[1, 1, 3]])
            ds = None
            self.results.append({"case": "T15 shared endpoint", "actual": [1, 1, 3], "passed": True})
            category = self.raster("categories", self.np.array([[1, 2, 3]], dtype="float32"))
            with self.assertRaisesRegex(RuntimeError, "UNMAPPED_INPUT_VALUES"):
                gp_raster.run_reclassify(
                    self.arcpy, category, "Value", "1 1;2 3", str(self.root / "must_not_exist.tif"), "ERROR", "VALUE"
                )
            self.assertFalse((self.root / "must_not_exist.tif").exists())
            self.results.append({"case": "T16 unmapped category", "passed": True})
            calc = str(self.root / "calculated.tif")
            gp_raster.run_raster_calculator(self.arcpy, "x * 2 + 1", calc, {"x": path})
            ds = self.gdal.Open(calc)
            self.assertEqual(ds.ReadAsArray().tolist(), [[20, 21, 22]])
            ds = None
            self.results.append({"case": "T22 IA signature and saved output", "actual": [20, 21, 22], "passed": True})

    def test_empty_domain_and_all_nodata(self):
        missing = rc.asset_info(
            self.raster("all_missing", self.np.full((10, 10), -9999, dtype="float32")),
            "CONTINUOUS",
            "all NoData fixture",
        )
        self.run_case("T13 all NoData", 100, 0, source=missing)
        tiny = self.boundary("subpixel", "POLYGON ((500000 990,500000.1 990,500000.1 990.1,500000 990.1,500000 990))")
        result = rc.run_clip_checked(self.arcpy, str(uuid.uuid4()), self.source, self.grid, "POLYGON_MASK", 1, tiny)
        self.assertFalse(result["eligible_for_downstream"])
        self.results.append({"case": "T13 empty pixel-centre domain", "result": result})

    def test_l_hole_multipart(self):
        ell = self.boundary(
            "ell", "POLYGON ((500000 990,500010 990,500010 995,500005 995,500005 1000,500000 1000,500000 990))"
        )
        self.run_case("T01 L shape", 75, 75, boundary=ell)
        hole = self.boundary(
            "hole",
            "POLYGON ((500000 990,500010 990,500010 1000,500000 1000,500000 990),(500003 993,500003 997,500007 997,500007 993,500003 993))",
        )
        self.run_case("T02 hole", 84, 84, boundary=hole)
        islands = self.boundary(
            "islands",
            "MULTIPOLYGON (((500000 990,500002 990,500002 992,500000 992,500000 990)),((500008 998,500010 998,500010 1000,500008 1000,500008 998)))",
        )
        self.run_case("T03 islands", 8, 8, boundary=islands)

    def test_coverage_and_policy(self):
        self.run_case("rectangle real zero valid", 100, 100)
        data = self.np.ones((10, 10), dtype="float32")
        data[:, 4] = -9999
        src = rc.asset_info(self.raster("strip", data), "CONTINUOUS", "missing stripe")
        failed = self.run_case("T11 strip strict", 100, 90, source=src)
        self.run_case("T12 explicit missing policy", 100, 90, source=src, minimum=0.9, reason="synthetic known stripe")
        short = rc.asset_info(
            self.raster("short", self.np.ones((10, 5), dtype="float32")), "CONTINUOUS", "half source coverage"
        )
        self.run_case("T10 fixed denominator", 100, 50, source=short)
        # QA failed output remains ineligible if copied and registered under a new name.
        import shutil

        copied = str(self.root / "renamed_failed.tif")
        shutil.copyfile(failed["output_raster"], copied)
        # Simulate a move: detection must use persisted identity even after the old path disappears.
        Path(failed["output_raster"]).unlink()
        asset = rc.asset_info(copied, "CONTINUOUS", "renamed diagnostic")
        with self.assertRaisesRegex(RuntimeError, "INELIGIBLE_UPSTREAM"):
            rc._verify_asset(asset, {"CONTINUOUS"})

    def test_grid_and_identity_fail_closed(self):
        shifted = rc.asset_info(
            self.raster("half", self.np.ones((10, 10), dtype="float32"), x=500000.5), "CONTINUOUS", "half pixel offset"
        )
        args = dict(
            run_id=str(uuid.uuid4()),
            source_asset=shifted,
            grid_asset=self.grid,
            clip_mode="RECTANGLE",
            minimum_coverage=1,
            rectangle=[500000, 990, 500010, 1000],
            rectangle_crs=self.wkt,
        )
        result = rc.run_clip_checked(self.arcpy, **args)
        self.results.append({"case": "T08 half pixel", "result": result})
        self.assertFalse(result["eligible_for_downstream"])
        self.assertIn("GRID_ORIGIN", result["error"])
        self.assertEqual(result["execution_status"], "NOT_STARTED")
        with self.assertRaisesRegex(RuntimeError, "RUN_ID_PLAN_CONFLICT"):
            rc.run_clip_checked(self.arcpy, **dict(args, minimum_coverage=0.5, missing_data_reason="changed"))
        with self.assertRaisesRegex(RuntimeError, "RECTANGLE_AND_CRS"):
            rc.run_clip_checked(self.arcpy, **dict(args, run_id=str(uuid.uuid4()), rectangle_crs=""))

    def test_environment_and_version_invalidation(self):
        oldmask = self.arcpy.env.mask
        oldextent = self.arcpy.env.extent
        try:
            self.arcpy.env.mask = self.full
            self.arcpy.env.extent = "500000 990 500003 993"
            expected_extent = str(self.arcpy.env.extent)
            self.run_case("T06 inherited mask extent", 100, 100)
            self.assertEqual(str(self.arcpy.env.extent), expected_extent)
        finally:
            self.arcpy.env.mask = oldmask
            self.arcpy.env.extent = oldextent
        src = rc.asset_info(
            self.raster("mutable", self.np.full((10, 10), 7, dtype="float32")), "CONTINUOUS", "version change fixture"
        )
        r = self.run_case("T39 before version change", 100, 100, source=src)
        ds = self.gdal.Open(src["path"], self.gdal.GA_Update)
        ds.GetRasterBand(1).WriteArray(self.np.zeros((10, 10), dtype="float32"))
        ds = None
        self.assertFalse(rc.result_status(r["run_id"])["eligible_for_downstream"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
