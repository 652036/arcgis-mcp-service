from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import gp_raster


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
            os.path.normpath(str(source)), "Value", "remap-object"
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


if __name__ == "__main__":
    unittest.main()
