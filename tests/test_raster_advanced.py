from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from arcgis_pro_mcp import raster_advanced


def _norm(path: Any) -> Any:
    return os.path.normpath(path) if isinstance(path, str) else path


class _Result:
    messageCount = 1

    def __init__(self, value: Any = "") -> None:
        self.value = value

    def getOutput(self, index: int) -> Any:
        del index
        return self.value

    def getMessage(self, index: int) -> str:
        return f"message-{index}"


class _SavedRaster:
    def __init__(self, owner: _Arcpy, operation: str) -> None:
        self.owner = owner
        self.operation = operation

    def save(self, path: str) -> None:
        normalized = _norm(path)
        self.owner.saved.append((self.operation, normalized))
        self.owner.existing.add(normalized)


class _Environment:
    def __init__(self, owner: _Arcpy, values: dict[str, Any]) -> None:
        self.owner = owner
        self.values = values

    def __enter__(self) -> _Environment:
        self.owner.environment_events.append(("enter", self.values))
        return self

    def __exit__(self, *_args: Any) -> None:
        self.owner.environment_events.append(("exit", self.values))


class _SearchCursor:
    def __init__(self, owner: _Arcpy, where_clause: str) -> None:
        self.owner = owner
        self.where_clause = where_clause

    def __enter__(self) -> _SearchCursor:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def __iter__(self):
        return iter([(oid,) for oid in self.owner.matching_oids])


class _Management:
    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner

    def _record(self, name: str, args: tuple[Any, ...]) -> _Result:
        self.owner.management_calls.append((name, args))
        return _Result(args[0] if args else "")

    def CalculateStatistics(self, *args: Any) -> _Result:
        return self._record("CalculateStatistics", args)

    def BuildPyramids(self, *args: Any) -> _Result:
        return self._record("BuildPyramids", args)

    def SetRasterProperties(self, *args: Any) -> _Result:
        return self._record("SetRasterProperties", args)

    def CopyRaster(self, *args: Any) -> _Result:
        self.owner.existing.add(_norm(args[1]))
        return self._record("CopyRaster", args)

    def CreateMosaicDataset(self, *args: Any) -> _Result:
        output = _norm(os.path.join(args[0], args[1]))
        self.owner.existing.add(output)
        self.owner.dataset_counts[output] = 0
        return self._record("CreateMosaicDataset", args)

    def AddRastersToMosaicDataset(self, *args: Any) -> _Result:
        mosaic = _norm(args[0])
        self.owner.dataset_counts[mosaic] = self.owner.dataset_counts.get(mosaic, 0) + len(args[2])
        return self._record("AddRastersToMosaicDataset", args)

    def BuildFootprints(self, *args: Any) -> _Result:
        return self._record("BuildFootprints", args)

    def BuildOverviews(self, *args: Any) -> _Result:
        return self._record("BuildOverviews", args)

    def RemoveRastersFromMosaicDataset(self, *args: Any) -> _Result:
        mosaic = _norm(args[0])
        self.owner.dataset_counts[mosaic] = max(
            0,
            self.owner.dataset_counts.get(mosaic, 0) - len(self.owner.matching_oids),
        )
        self.owner.matching_oids.clear()
        return self._record("RemoveRastersFromMosaicDataset", args)

    def GetCount(self, dataset: Any) -> _Result:
        return _Result(str(self.owner.dataset_counts.get(_norm(dataset), 0)))


class _NoOutputManagement(_Management):
    def CopyRaster(self, *args: Any) -> _Result:
        return self._record("CopyRaster", args)


class _SpatialAnalyst:
    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner

    def _raster(self, name: str, args: tuple[Any, ...]) -> _SavedRaster:
        self.owner.sa_calls.append((name, args))
        if name == "EucDistance":
            for index in (3, 6):
                if args[index] not in (None, "", "#"):
                    self.owner.existing.add(_norm(args[index]))
        elif name == "DistanceAccumulation":
            for index in (8, 9, 10):
                if args[index] not in (None, "", "#"):
                    self.owner.existing.add(_norm(args[index]))
        return _SavedRaster(self.owner, name)

    def FocalStatistics(self, *args: Any) -> _SavedRaster:
        return self._raster("FocalStatistics", args)

    def CellStatistics(self, *args: Any) -> _SavedRaster:
        return self._raster("CellStatistics", args)

    def Con(self, *args: Any) -> _SavedRaster:
        return self._raster("Con", args)

    def SetNull(self, *args: Any) -> _SavedRaster:
        return self._raster("SetNull", args)

    def EucDistance(self, *args: Any) -> _SavedRaster:
        return self._raster("EucDistance", args)

    def DistanceAccumulation(self, *args: Any) -> _SavedRaster:
        return self._raster("DistanceAccumulation", args)

    def OptimalPathAsLine(self, *args: Any) -> _Result:
        self.owner.sa_calls.append(("OptimalPathAsLine", args))
        self.owner.existing.add(_norm(args[3]))
        return _Result(args[3])

    def StreamOrder(self, *args: Any) -> _SavedRaster:
        return self._raster("StreamOrder", args)

    def StreamToFeature(self, *args: Any) -> _Result:
        self.owner.sa_calls.append(("StreamToFeature", args))
        self.owner.existing.add(_norm(args[2]))
        return _Result(args[2])

    def Basin(self, *args: Any) -> _SavedRaster:
        return self._raster("Basin", args)

    def NbrRectangle(self, *args: Any) -> tuple[str, tuple[Any, ...]]:
        return ("NbrRectangle", args)

    def NbrCircle(self, *args: Any) -> tuple[str, tuple[Any, ...]]:
        return ("NbrCircle", args)

    def NbrAnnulus(self, *args: Any) -> tuple[str, tuple[Any, ...]]:
        return ("NbrAnnulus", args)

    def NbrWedge(self, *args: Any) -> tuple[str, tuple[Any, ...]]:
        return ("NbrWedge", args)

    def NbrIrregular(self, *args: Any) -> tuple[str, tuple[Any, ...]]:
        return ("NbrIrregular", args)

    def NbrWeight(self, *args: Any) -> tuple[str, tuple[Any, ...]]:
        return ("NbrWeight", args)


class _Arcpy:
    def __init__(self, existing: list[str], *, extension_status: str = "Available") -> None:
        self.existing = {_norm(path) for path in existing}
        self.extension_status = extension_status
        self.extension_events: list[tuple[str, str]] = []
        self.environment_events: list[tuple[str, dict[str, Any]]] = []
        self.management_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.sa_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.saved: list[tuple[str, str]] = []
        self.dataset_counts: dict[str, int] = {}
        self.matching_oids: list[int] = []
        self.management = _Management(self)
        self.sa = _SpatialAnalyst(self)
        self.da = SimpleNamespace(SearchCursor=self._search_cursor)

    def Exists(self, path: Any) -> bool:
        return _norm(path) in self.existing

    def EnvManager(self, **values: Any) -> _Environment:
        return _Environment(self, values)

    def CheckExtension(self, name: str) -> str:
        self.extension_events.append(("check", name))
        return self.extension_status

    def CheckOutExtension(self, name: str) -> str:
        self.extension_events.append(("out", name))
        return "CheckedOut"

    def CheckInExtension(self, name: str) -> str:
        self.extension_events.append(("in", name))
        return "CheckedIn"

    def SpatialReference(self, wkid: int) -> tuple[str, int]:
        return ("SpatialReference", wkid)

    def _search_cursor(
        self,
        dataset: Any,
        fields: list[str],
        *,
        where_clause: str,
    ) -> _SearchCursor:
        del dataset, fields
        return _SearchCursor(self, where_clause)


class RasterAdvancedTests(unittest.TestCase):
    def _policy(self, input_root: str, output_root: str, *, destructive: bool = False):
        values = {
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
            "ARCGIS_PRO_MCP_INPUT_ROOTS": os.pathsep.join([input_root, output_root]),
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
        }
        if destructive:
            values["ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE"] = "1"
        return patch.dict(os.environ, values, clear=True)

    def test_raster_management_calls_documented_tools_and_verifies_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            source = str(Path(input_root) / "source.tif")
            aoi = str(Path(input_root) / "aoi.shp")
            copied = str(Path(output_root) / "copied.tif")
            arcpy = _Arcpy([source, aoi])
            with self._policy(input_root, output_root):
                stats = raster_advanced.calculate_statistics(
                    arcpy,
                    source,
                    2,
                    3,
                    [0, 255],
                    "SKIP_EXISTING",
                    aoi,
                    {"parallel_processing_factor": "50%"},
                )
                pyramids = raster_advanced.build_pyramids(
                    arcpy,
                    source,
                    4,
                    True,
                    "BILINEAR",
                    "JPEG",
                    80,
                    "SKIP_EXISTING",
                )
                nodata = raster_advanced.set_raster_nodata(
                    arcpy,
                    source,
                    [[1, -9999]],
                    source,
                    "ELEVATION",
                )
                copied_result = raster_advanced.copy_raster(
                    arcpy,
                    source,
                    copied,
                    background_value=0,
                    nodata_value=0,
                    pixel_type="16_BIT_SIGNED",
                    output_format="TIFF",
                )

        self.assertTrue(stats["verified"])
        self.assertEqual(pyramids["pyramid_level"], 4)
        self.assertEqual(nodata["nodata"], [[1, -9999.0]])
        self.assertEqual(copied_result["output_path"], _norm(copied))
        self.assertEqual(
            [name for name, _args in arcpy.management_calls],
            ["CalculateStatistics", "BuildPyramids", "SetRasterProperties", "CopyRaster"],
        )
        self.assertEqual([len(args) for _name, args in arcpy.management_calls], [6, 7, 5, 14])
        self.assertEqual(arcpy.management_calls[-1][1][10], "TIFF")
        self.assertEqual(arcpy.environment_events[0][0], "enter")
        self.assertEqual(arcpy.environment_events[1][0], "exit")

    def test_build_pyramids_delete_requires_gate_and_exact_path_echo(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            source = str(Path(input_root) / "source.tif")
            arcpy = _Arcpy([source])
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, "ALLOW_DESTRUCTIVE"):
                    raster_advanced.build_pyramids(
                        arcpy,
                        source,
                        pyramid_level=0,
                        confirm_delete_pyramids=source,
                    )
            with self._policy(input_root, output_root, destructive=True):
                with self.assertRaisesRegex(RuntimeError, "精确回显"):
                    raster_advanced.build_pyramids(
                        arcpy,
                        source,
                        pyramid_level=0,
                        confirm_delete_pyramids="wrong",
                    )
                result = raster_advanced.build_pyramids(
                    arcpy,
                    source,
                    pyramid_level=0,
                    confirm_delete_pyramids=source,
                )
        self.assertEqual(result["pyramid_level"], 0)

    def test_local_spatial_analyst_operations_use_license_and_typed_neighborhood(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            first = str(Path(input_root) / "first.tif")
            second = str(Path(input_root) / "second.tif")
            arcpy = _Arcpy([first, second])
            outputs = [str(Path(output_root) / f"out-{index}.tif") for index in range(4)]
            with self._policy(input_root, output_root):
                raster_advanced.focal_statistics(
                    arcpy,
                    first,
                    outputs[0],
                    {"type": "ANNULUS", "inner_radius": 2, "outer_radius": 5},
                    "SUM",
                )
                raster_advanced.cell_statistics(
                    arcpy,
                    [first, second, 5],
                    outputs[1],
                    "MAXIMUM",
                )
                raster_advanced.conditional_con(
                    arcpy,
                    first,
                    second,
                    outputs[2],
                    0,
                    "VALUE >= 1",
                )
                raster_advanced.set_null(
                    arcpy,
                    first,
                    second,
                    outputs[3],
                    "VALUE < 0",
                )

        self.assertEqual(
            [name for name, _args in arcpy.sa_calls],
            ["FocalStatistics", "CellStatistics", "Con", "SetNull"],
        )
        self.assertEqual(arcpy.sa_calls[0][1][1][0], "NbrAnnulus")
        self.assertEqual(arcpy.extension_events.count(("check", "Spatial")), 4)
        self.assertEqual(arcpy.extension_events.count(("in", "Spatial")), 0)

    def test_distance_path_and_hydrology_operations_verify_secondary_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            source = str(Path(input_root) / "source.shp")
            destination = str(Path(input_root) / "destination.shp")
            stream = str(Path(input_root) / "stream.tif")
            flow = str(Path(input_root) / "flow.tif")
            accumulation_input = str(Path(input_root) / "accumulation.tif")
            back_input = str(Path(input_root) / "back.tif")
            arcpy = _Arcpy([source, destination, stream, flow, accumulation_input, back_input])
            euc = str(Path(output_root) / "euc.tif")
            euc_direction = str(Path(output_root) / "euc-direction.tif")
            distance = str(Path(output_root) / "distance.tif")
            distance_back = str(Path(output_root) / "distance-back.tif")
            source_direction = str(Path(output_root) / "source-direction.tif")
            source_location = str(Path(output_root) / "source-location.tif")
            path = str(Path(output_root) / "path.shp")
            order = str(Path(output_root) / "order.tif")
            streams = str(Path(output_root) / "streams.shp")
            basins = str(Path(output_root) / "basins.tif")
            with self._policy(input_root, output_root):
                euc_result = raster_advanced.euclidean_distance(
                    arcpy,
                    source,
                    euc,
                    maximum_distance=1000,
                    cell_size=10,
                    out_direction_raster=euc_direction,
                    distance_method="GEODESIC",
                )
                distance_result = raster_advanced.distance_accumulation(
                    arcpy,
                    source,
                    distance,
                    out_back_direction_raster=distance_back,
                    out_source_direction_raster=source_direction,
                    out_source_location_raster=source_location,
                    source_initial_accumulation=0,
                    source_maximum_accumulation=10000,
                    source_cost_multiplier=1,
                )
                raster_advanced.optimal_path_as_line(
                    arcpy,
                    destination,
                    accumulation_input,
                    back_input,
                    path,
                    "OBJECTID",
                    "EACH_CELL",
                    "NETWORK_PATHS",
                )
                raster_advanced.stream_order(arcpy, stream, flow, order, "SHREVE")
                raster_advanced.stream_to_feature(arcpy, stream, flow, streams, False)
                raster_advanced.basin(arcpy, flow, basins)

        self.assertEqual(euc_result["direction_raster"], _norm(euc_direction))
        self.assertTrue(euc_result["deprecated"])
        self.assertEqual(distance_result["back_direction_raster"], _norm(distance_back))
        self.assertEqual(distance_result["source_direction_raster"], _norm(source_direction))
        self.assertEqual(distance_result["source_location_raster"], _norm(source_location))
        self.assertEqual(
            [name for name, _args in arcpy.sa_calls],
            [
                "EucDistance",
                "DistanceAccumulation",
                "OptimalPathAsLine",
                "StreamOrder",
                "StreamToFeature",
                "Basin",
            ],
        )
        self.assertEqual([len(args) for _name, args in arcpy.sa_calls], [7, 16, 7, 3, 4, 1])

    def test_wedge_neighborhood_uses_documented_argument_order(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            source = str(Path(input_root) / "source.tif")
            output = str(Path(output_root) / "wedge.tif")
            arcpy = _Arcpy([source])
            with self._policy(input_root, output_root):
                raster_advanced.focal_statistics(
                    arcpy,
                    source,
                    output,
                    {
                        "type": "WEDGE",
                        "radius": 7,
                        "start_angle": 30,
                        "end_angle": 120,
                        "units": "CELL",
                    },
                )

        neighborhood = arcpy.sa_calls[0][1][1]
        self.assertEqual(neighborhood, ("NbrWedge", (7.0, 30.0, 120.0, "CELL")))

    def test_mosaic_lifecycle_counts_and_exact_remove_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            workspace = str(Path(output_root) / "imagery.gdb")
            source_one = str(Path(input_root) / "one.tif")
            source_two = str(Path(input_root) / "two.tif")
            arcpy = _Arcpy([workspace, source_one, source_two])
            mosaic = _norm(str(Path(workspace) / "imagery"))
            with self._policy(input_root, output_root, destructive=True):
                created = raster_advanced.create_mosaic_dataset(
                    arcpy,
                    workspace,
                    "imagery",
                    4326,
                    3,
                    "8_BIT_UNSIGNED",
                    "NATURAL_COLOR_RGB",
                )
                added = raster_advanced.add_rasters_to_mosaic_dataset(
                    arcpy,
                    mosaic,
                    [source_one, source_two],
                    build_pyramids_for_sources=True,
                    calculate_statistics_for_sources=True,
                    filter_expression="*.tif",
                )
                with patch.dict(
                    os.environ,
                    {"ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "0"},
                ):
                    with self.assertRaisesRegex(RuntimeError, "破坏性操作已禁用"):
                        raster_advanced.add_rasters_to_mosaic_dataset(
                            arcpy,
                            mosaic,
                            [source_one],
                            duplicate_items_action="OVERWRITE_DUPLICATES",
                        )
                footprints = raster_advanced.build_mosaic_footprints(
                    arcpy,
                    mosaic,
                    "OBJECTID >= 0",
                    min_data_value=1,
                    max_data_value=254,
                    approx_num_vertices=25,
                    simplification_method="CONVEX_HULL",
                )
                overviews = raster_advanced.build_mosaic_overviews(arcpy, mosaic)

                arcpy.matching_oids[:] = [1, 2]
                with self.assertRaisesRegex(RuntimeError, "expected_item_count"):
                    raster_advanced.remove_rasters_from_mosaic_dataset(
                        arcpy,
                        mosaic,
                        "OBJECTID >= 0",
                        1,
                        mosaic,
                        "OBJECTID >= 0",
                    )
                removed = raster_advanced.remove_rasters_from_mosaic_dataset(
                    arcpy,
                    mosaic,
                    "OBJECTID >= 0",
                    2,
                    mosaic,
                    "OBJECTID >= 0",
                )

        self.assertEqual(created["output_path"], mosaic)
        self.assertEqual(added["added_count"], 2)
        self.assertTrue(footprints["verified"])
        self.assertTrue(overviews["verified"])
        self.assertEqual(removed["removed_count"], 2)
        self.assertEqual(removed["remaining_matching_count"], 0)
        self.assertEqual(
            [name for name, _args in arcpy.management_calls],
            [
                "CreateMosaicDataset",
                "AddRastersToMosaicDataset",
                "BuildFootprints",
                "BuildOverviews",
                "RemoveRastersFromMosaicDataset",
            ],
        )
        self.assertEqual([len(args) for _name, args in arcpy.management_calls], [7, 22, 16, 6, 8])

    def test_exact_confirmations_and_destructive_gate_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            source = str(Path(input_root) / "source.tif")
            mosaic = str(Path(input_root) / "imagery.gdb" / "imagery")
            arcpy = _Arcpy([source, mosaic])
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, "confirm_raster_path"):
                    raster_advanced.set_raster_nodata(arcpy, source, [[1, -9999]], "wrong")
                with self.assertRaisesRegex(RuntimeError, "ALLOW_DESTRUCTIVE"):
                    raster_advanced.remove_rasters_from_mosaic_dataset(
                        arcpy,
                        mosaic,
                        "OBJECTID = 1",
                        1,
                        mosaic,
                        "OBJECTID = 1",
                    )
            with self._policy(input_root, output_root, destructive=True):
                with self.assertRaisesRegex(RuntimeError, "confirm_mosaic_dataset"):
                    raster_advanced.remove_rasters_from_mosaic_dataset(
                        arcpy,
                        mosaic,
                        "OBJECTID = 1",
                        1,
                        "wrong",
                        "OBJECTID = 1",
                    )
                with self.assertRaisesRegex(RuntimeError, "confirm_where_clause"):
                    raster_advanced.remove_rasters_from_mosaic_dataset(
                        arcpy,
                        mosaic,
                        "OBJECTID = 1",
                        1,
                        mosaic,
                        "OBJECTID = 2",
                    )

        self.assertEqual(arcpy.management_calls, [])

    def test_missing_derived_output_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            source = str(Path(input_root) / "source.tif")
            output = str(Path(output_root) / "missing.tif")
            arcpy = _Arcpy([source])
            arcpy.management = _NoOutputManagement(arcpy)
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, "未创建或不可见"):
                    raster_advanced.copy_raster(arcpy, source, output)

    def test_output_roots_environment_and_extension_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            source = str(Path(input_root) / "source.tif")
            outside = str(Path(input_root) / "outside.tif")
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, "GP_OUTPUT_ROOT"):
                    raster_advanced.copy_raster(_Arcpy([source]), source, outside)
                with self.assertRaisesRegex(RuntimeError, "不支持的环境参数"):
                    raster_advanced.focal_statistics(
                        _Arcpy([source]),
                        source,
                        str(Path(output_root) / "out.tif"),
                        environment={"workspace": input_root},
                    )
                with self.assertRaisesRegex(RuntimeError, "不可用"):
                    raster_advanced.basin(
                        _Arcpy([source], extension_status="NotLicensed"),
                        source,
                        str(Path(output_root) / "basin.tif"),
                    )


if __name__ == "__main__":
    unittest.main()
