from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import gp_raster, raster_checked


class RasterGeoprocessingTests(unittest.TestCase):
    def test_reclassify_preserves_integer_remap_tokens(self) -> None:
        result = SimpleNamespace(save=MagicMock())
        reclassify = MagicMock(return_value=result)
        remap_range = MagicMock(return_value="remap-object")
        arcpy = SimpleNamespace(
            sa=SimpleNamespace(Reclassify=reclassify, RemapRange=remap_range)
        )
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
            },
            clear=True,
        ):
            source = Path(root, "input.tif")
            source.touch()
            output = Path(root, "output.tif")
            gp_raster.run_reclassify(
                arcpy,
                str(source),
                "Value",
                "0 10 1;10.5 20 2",
                str(output),
            )

        remap_range.assert_called_once_with([[0, 10, 1], [10.5, 20, 2]])
        reclassify.assert_called_once_with(
            os.path.normpath(str(source)), "Value", "remap-object", "DATA"
        )
        result.save.assert_called_once_with(os.path.normpath(str(output)))

    def test_topo_to_raster_builds_typed_point_elevation_input(self) -> None:
        topo_point_elevation = MagicMock(return_value="topo-input")
        result = SimpleNamespace(save=MagicMock())
        topo_to_raster = MagicMock(return_value=result)
        arcpy = SimpleNamespace(
            sa=SimpleNamespace(
                TopoPointElevation=topo_point_elevation,
                TopoToRaster=topo_to_raster,
            ),
        )
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
            },
            clear=True,
        ):
            source = Path(root, "points.shp")
            source.touch()
            output = Path(root, "surface.tif")
            gp_raster.run_topo_to_raster(
                arcpy,
                str(source),
                str(output),
                25,
                "HEIGHT",
            )

        topo_point_elevation.assert_called_once_with(
            [[os.path.normpath(str(source)), "HEIGHT"]]
        )
        topo_to_raster.assert_called_once_with(
            ["topo-input"], 25.0, data_type="SPOT"
        )
        result.save.assert_called_once_with(os.path.normpath(str(output)))


class RasterPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        root = Path(self.contexts.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.contexts.enter_context(patch.dict(os.environ, {
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
            "ARCGIS_PRO_MCP_INPUT_ROOTS": str(root),
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": str(root),
        }, clear=True))
        self.source = str(root / "input.tif")
        self.template = str(root / "boundary.shp")
        self.output = str(root / "output.tif")
        Path(self.source).touch()
        Path(self.template).touch()
        self.arcpy = MagicMock()
        self.arcpy.Exists.return_value = False

    def reclassify(self, **changes) -> None:
        request = dict(arcpy=self.arcpy, in_raster=self.source, reclass_field="Value",
                       remap="0 10 1;10 20 2", out_raster=self.output)
        request.update(changes)
        gp_raster.run_reclassify(**request)

    def test_value_remap_and_missing_value_policy_reach_arcpy(self) -> None:
        self.reclassify(remap="1 10;2 20", remap_mode="value", missing_values="nodata")
        self.arcpy.sa.RemapValue.assert_called_once_with([[1, 10], [2, 20]])
        self.arcpy.sa.RemapRange.assert_not_called()
        self.arcpy.sa.Reclassify.assert_called_once_with(
            self.source, "Value", self.arcpy.sa.RemapValue.return_value, "NODATA"
        )
        self.arcpy.sa.Reclassify.return_value.save.assert_called_once_with(self.output)

    def test_range_remaps_are_ordered_and_shared_endpoints_allowed(self) -> None:
        self.reclassify(remap="10 20 2;0 10 1")
        self.arcpy.sa.RemapRange.assert_called_once_with([[0, 10, 1], [10, 20, 2]])

    def test_invalid_remaps_fail_before_geoprocessing(self) -> None:
        cases = [
            {"remap": "0 10 1;9 20 2"}, {"remap": "10 0 1"},
            {"remap": "0 inf 1"}, {"remap": "0 10 nan"}, {"remap": "0 10 1.5"},
            {"remap": "0 10"}, {"remap": "bad 10 1"},
            {"remap_mode": "VALUE", "remap": "1 2;1 3"},
            {"remap_mode": "VALUE", "remap": "1 2.5"},
            {"remap_mode": "GUESS"}, {"missing_values": "IGNORE"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.reclassify(**changes)
        self.arcpy.sa.RemapRange.assert_not_called()
        self.arcpy.sa.RemapValue.assert_not_called()
        self.arcpy.sa.Reclassify.assert_not_called()

    def test_error_policy_rejects_attribute_field_before_pixel_scan(self) -> None:
        with patch.object(raster_checked, "_backend") as backend:
            with self.assertRaisesRegex(RuntimeError, "ERROR_RECLASS_FIELD_UNSUPPORTED"):
                self.reclassify(reclass_field="LandUseClass", missing_values="ERROR")
        backend.assert_not_called()
        self.arcpy.sa.Reclassify.assert_not_called()

    def test_error_policy_accepts_value_field_case_insensitively(self) -> None:
        with patch.object(raster_checked, "_backend", side_effect=RuntimeError("scan reached")) as backend:
            with self.assertRaisesRegex(RuntimeError, "^scan reached$"):
                self.reclassify(reclass_field=" vAlUe ", missing_values="error")
        backend.assert_called_once_with()
        self.arcpy.sa.Reclassify.assert_not_called()

    def test_native_missing_value_policies_allow_attribute_fields_without_scan(self) -> None:
        for policy in ("DATA", "NODATA"):
            self.arcpy.reset_mock()
            with self.subTest(policy=policy), patch.object(raster_checked, "_backend") as backend:
                self.reclassify(reclass_field="LandUseClass", missing_values=policy)
                backend.assert_not_called()
                self.arcpy.sa.Reclassify.assert_called_once_with(
                    self.source, "LandUseClass", self.arcpy.sa.RemapRange.return_value, policy
                )

    def test_existing_output_is_not_overwritten(self) -> None:
        Path(self.output).write_bytes(b"original")
        with self.assertRaisesRegex(RuntimeError, "OUTPUT_ALREADY_EXISTS"):
            self.reclassify()
        self.arcpy.sa.Reclassify.assert_not_called()
        self.assertEqual(Path(self.output).read_bytes(), b"original")

    def test_invalid_clip_geometry_never_calls_arcpy(self) -> None:
        cases = [{"rectangle": x} for x in ("0 0 10", "0 0 0 10", "10 0 0 10", "0 0 nan 10", "a b c d")]
        cases.append({"clipping_geometry": True})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                gp_raster.run_clip_raster(self.arcpy, self.source, self.output, **changes)
        self.arcpy.management.Clip.assert_not_called()

    def test_clip_forwards_explicit_rectangle_and_polygon_template(self) -> None:
        gp_raster.run_clip_raster(self.arcpy, self.source, self.output, rectangle="0 0 10 10")
        self.arcpy.management.Clip.assert_called_once_with(
            self.source, "0 0 10 10", self.output, "#", "#", "NONE"
        )
        self.arcpy.management.Clip.reset_mock()
        gp_raster.run_clip_raster(self.arcpy, self.source, self.output,
                                  in_template_dataset=self.template, clipping_geometry=True)
        self.arcpy.management.Clip.assert_called_once_with(
            self.source, "#", self.output, self.template, "#", "ClippingGeometry"
        )

    def test_clip_and_extract_restore_environment_even_when_arcpy_fails(self) -> None:
        inherited = {"mask": "inherited mask", "extent": "inherited extent", "cellSize": 30}
        effective = dict(inherited)

        @contextmanager
        def manager(**values):
            saved = dict(effective)
            effective.update(values)
            try:
                yield
            finally:
                effective.clear()
                effective.update(saved)

        def fail(*args):
            self.assertEqual(effective, dict(inherited, mask=None, cellSize=5))
            raise RuntimeError("native failure")

        self.arcpy.EnvManager.side_effect = manager
        self.arcpy.management.Clip.side_effect = fail
        self.arcpy.sa.ExtractByMask.side_effect = fail
        environment = {"mask": {"state": "CLEAR"}, "extent": {"state": "UNSET"},
                       "cell_size": {"state": "VALUE", "value": 5}}
        for operation in (
            lambda: gp_raster.run_clip_raster(self.arcpy, self.source, self.output, environment=environment),
            lambda: gp_raster.run_extract_by_mask(self.arcpy, self.source, self.template, self.output,
                                                 environment=environment),
        ):
            with self.assertRaisesRegex(RuntimeError, "native failure"):
                operation()
            self.assertEqual(effective, inherited)

    def test_zonal_statistics_forwards_both_nodata_policies(self) -> None:
        for policy in ("data", "nodata"):
            self.arcpy.sa.ZonalStatisticsAsTable.reset_mock()
            gp_raster.run_zonal_statistics_as_table(self.arcpy, self.template, "ZONE", self.source,
                                                    self.output, "mean", policy)
            self.arcpy.sa.ZonalStatisticsAsTable.assert_called_once_with(
                self.template, "ZONE", self.source, self.output, policy.upper(), "MEAN"
            )

    def test_zonal_statistics_rejects_unknown_nodata_policy(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ignore_nodata"):
            gp_raster.run_zonal_statistics_as_table(self.arcpy, self.template, "ZONE", self.source,
                                                    self.output, ignore_nodata="SKIP")
        self.arcpy.sa.ZonalStatisticsAsTable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
