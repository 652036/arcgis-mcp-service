from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import raster_runtime


class _Result:
    def __init__(self, value: str) -> None:
        self.value = value

    def getOutput(self, index: int) -> str:
        return self.value


class _SavedRaster:
    def __init__(self, owner: _Arcpy, operation: str) -> None:
        self.owner = owner
        self.operation = operation

    def save(self, path: str) -> None:
        self.owner.saved.append((self.operation, path))
        self.owner.outputs.add(os.path.normpath(path))


class _SpatialAnalyst:
    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __getattr__(self, name: str):
        def call(*args: object) -> _SavedRaster:
            self.calls.append((name, args))
            if name == "FlowDirection" and len(args) > 2 and args[2]:
                self.owner.outputs.add(os.path.normpath(str(args[2])))
            return _SavedRaster(self.owner, name)

        return call


class _EnvManager:
    def __init__(self, owner: _Arcpy, values: dict[str, object]) -> None:
        self.owner = owner
        self.values = values

    def __enter__(self) -> _EnvManager:
        self.owner.env_events.append(("enter", self.values))
        return self

    def __exit__(self, *_args: object) -> None:
        self.owner.env_events.append(("exit", self.values))


class _Arcpy:
    def __init__(self, extension_status: str = "Available") -> None:
        self.status = extension_status
        self.extension_events: list[tuple[str, str]] = []
        self.env_events: list[tuple[str, dict[str, object]]] = []
        self.saved: list[tuple[str, str]] = []
        self.outputs: set[str] = set()
        self.sa = _SpatialAnalyst(self)
        self.management = SimpleNamespace(GetRasterProperties=self._get_raster_property)

    def CheckExtension(self, name: str) -> str:
        self.extension_events.append(("check", name))
        return self.status

    def CheckOutExtension(self, name: str) -> str:
        self.extension_events.append(("out", name))
        return "CheckedOut"

    def CheckInExtension(self, name: str) -> str:
        self.extension_events.append(("in", name))
        return "CheckedIn"

    def EnvManager(self, **values: object) -> _EnvManager:
        return _EnvManager(self, values)

    def Exists(self, path: str) -> bool:
        # Input paths are virtual in these tests; saved outputs are tracked too.
        return True if path else False

    def Describe(self, _path: str) -> SimpleNamespace:
        return SimpleNamespace(
            dataType="RasterDataset",
            format="TIFF",
            bandCount=1,
            pixelType="F32",
            compressionType="LZW",
            hasRAT=False,
            meanCellWidth=10.0,
            meanCellHeight=10.0,
            noDataValue=-9999,
            extent=SimpleNamespace(XMin=0, YMin=1, XMax=100, YMax=101),
            spatialReference=SimpleNamespace(name="WGS 84 / UTM zone 50N", factoryCode=32650),
        )

    def _get_raster_property(self, _path: str, property_name: str) -> _Result:
        return _Result({"MINIMUM": "1", "MAXIMUM": "9"}.get(property_name, "1"))


class RasterRuntimeTests(unittest.TestCase):
    def test_extension_status_does_not_checkout(self) -> None:
        arcpy = _Arcpy()
        result = raster_runtime.extension_status(arcpy, ["Spatial", "Network"])
        self.assertEqual(result, {"Spatial": "Available", "Network": "Available"})
        self.assertEqual(arcpy.extension_events, [("check", "Spatial"), ("check", "Network")])

    def test_extension_scope_checks_in_even_when_body_raises(self) -> None:
        arcpy = _Arcpy()
        with self.assertRaisesRegex(ValueError, "boom"):
            with raster_runtime.checked_out_extension(arcpy, "Spatial"):
                raise ValueError("boom")
        self.assertEqual(
            arcpy.extension_events,
            [("check", "Spatial"), ("out", "Spatial"), ("in", "Spatial")],
        )

    def test_extension_scope_rejects_unavailable_license(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "不可用"):
            with raster_runtime.checked_out_extension(_Arcpy("NotLicensed"), "Spatial"):
                pass

    def test_scoped_environment_validates_paths_and_restores_context(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": root},
            clear=True,
        ):
            snap = str(Path(root) / "snap.tif")
            with raster_runtime.scoped_environment(
                arcpy,
                {"snap_raster": snap, "cell_size": 10, "parallel_processing_factor": "50%"},
            ) as effective:
                self.assertEqual(effective["snapRaster"], os.path.normpath(snap))
                self.assertEqual(effective["cellSize"], 10.0)
        self.assertEqual(arcpy.env_events[0][0], "enter")
        self.assertEqual(arcpy.env_events[-1][0], "exit")

    def test_scoped_environment_rejects_unknown_keys(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "不支持的环境参数"):
            raster_runtime.validate_environment({"workspace": "C:\\unsafe"})

    def test_environment_raster_references_obey_input_roots(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as other_root:
            with patch.dict(
                os.environ,
                {"ARCGIS_PRO_MCP_INPUT_ROOTS": input_root},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "INPUT_ROOTS"):
                    raster_runtime.validate_environment(
                        {"cell_size": str(Path(other_root) / "reference.tif")}
                    )

    def test_raster_info_returns_consolidated_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": root},
            clear=True,
        ):
            result = raster_runtime.raster_info(_Arcpy(), str(Path(root) / "surface.tif"))
        self.assertEqual(result["band_count"], 1)
        self.assertEqual(result["extent"]["xmax"], 100.0)
        self.assertEqual(result["spatial_reference"]["factory_code"], 32650)
        self.assertEqual(result["properties"]["minimum"], "1")

    def test_hydrology_minimum_chain_uses_scoped_license_env_and_output_root(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            surface = str(Path(input_root) / "surface.tif")
            pour_points = str(Path(input_root) / "pour_points.shp")
            filled = str(Path(output_root) / "filled.tif")
            flow = str(Path(output_root) / "flow.tif")
            accumulation = str(Path(output_root) / "accumulation.tif")
            snapped = str(Path(output_root) / "snapped.tif")
            watershed = str(Path(output_root) / "watershed.tif")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": os.pathsep.join([input_root, output_root]),
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                raster_runtime.run_fill(arcpy, surface, filled, environment={"cell_size": 10})
                raster_runtime.run_flow_direction(arcpy, filled, flow)
                raster_runtime.run_flow_accumulation(arcpy, flow, accumulation)
                raster_runtime.run_snap_pour_point(
                    arcpy, pour_points, accumulation, snapped, 25, "OBJECTID"
                )
                result = raster_runtime.run_watershed(
                    arcpy, flow, snapped, watershed, "VALUE"
                )
        self.assertEqual(result["verified"], True)
        self.assertEqual(
            [name for name, _args in arcpy.sa.calls],
            ["Fill", "FlowDirection", "FlowAccumulation", "SnapPourPoint", "Watershed"],
        )
        self.assertEqual(len(arcpy.saved), 5)
        self.assertEqual(arcpy.extension_events.count(("in", "Spatial")), 5)

    def test_hydrology_rejects_output_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "GP_OUTPUT_ROOT"):
                    raster_runtime.run_fill(
                        _Arcpy(),
                        str(Path(input_root) / "surface.tif"),
                        str(Path(input_root) / "filled.tif"),
                    )


if __name__ == "__main__":
    unittest.main()
