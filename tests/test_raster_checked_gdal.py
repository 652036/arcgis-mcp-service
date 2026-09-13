"""Optional GDAL-only metadata checks; no ArcPy host or production data."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from arcgis_pro_mcp import raster_checked as rc


@unittest.skipUnless(os.environ.get("ARCGIS_MCP_RUN_NATIVE_QA") == "1", "requires native QA opt-in and GDAL")
class RasterMetadataTests(unittest.TestCase):
    def test_nonstandard_crs_and_unknown_crs(self):
        from osgeo import gdal, osr

        sr = osr.SpatialReference()
        sr.SetProjCS("Synthetic custom TM")
        sr.SetWellKnownGeogCS("WGS84")
        sr.SetTM(0, 3.25, 0.9996, 500000, 0)
        self.assertIsNone(sr.GetAuthorityCode(None))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "custom.tif")
            ds = gdal.GetDriverByName("GTiff").Create(path, 1, 1, 1, gdal.GDT_Float32)
            ds.SetGeoTransform((500000, 1, 0, 1000, 0, -1))
            ds.SetProjection(sr.ExportToWkt())
            ds = None
            opened, meta = rc._raster(path)
            del opened
            self.assertIsNone(rc._srs(meta["grid"]["crs_wkt"]).GetAuthorityCode(None))
            rc._same_crs(sr.ExportToWkt(), meta["grid"]["crs_wkt"])
            ds = gdal.Open(path, gdal.GA_Update)
            ds.SetProjection("")
            ds = None
            with self.assertRaisesRegex(RuntimeError, "UNKNOWN_CRS"):
                rc._raster(path)

    def test_complex_and_int64_are_not_silently_cast(self):
        from osgeo import gdal, osr

        sr = osr.SpatialReference()
        sr.ImportFromEPSG(32631)
        with tempfile.TemporaryDirectory() as tmp:
            for dtype in (gdal.GDT_CFloat32, gdal.GDT_Int64, gdal.GDT_UInt64):
                path = str(Path(tmp) / f"type_{dtype}.tif")
                ds = gdal.GetDriverByName("GTiff").Create(path, 1, 1, 1, dtype)
                ds.SetGeoTransform((500000, 1, 0, 1000, 0, -1))
                ds.SetProjection(sr.ExportToWkt())
                ds = None
                with self.assertRaisesRegex(RuntimeError, "UNSUPPORTED_PIXEL_TYPE"):
                    rc._raster(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
