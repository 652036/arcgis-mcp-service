from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from arcgis_pro_mcp import spatial_modeling


def _norm(value: Any) -> Any:
    return os.path.normpath(value) if isinstance(value, str) else value


class _Result:
    messageCount = 1

    def __init__(self, outputs: list[Any] | None = None) -> None:
        self.outputs = outputs or []

    def getOutput(self, index: int) -> Any:
        return self.outputs[index]

    def getMessage(self, index: int) -> str:
        return f"message-{index}"


class _Environment:
    def __init__(self, owner: _Arcpy, values: dict[str, Any]) -> None:
        self.owner = owner
        self.values = values

    def __enter__(self) -> _Environment:
        self.owner.environment_events.append(("enter", self.values))
        return self

    def __exit__(self, *_args: Any) -> None:
        self.owner.environment_events.append(("exit", self.values))


class _Toolbox:
    def __init__(self, owner: _Arcpy, name: str) -> None:
        self.owner = owner
        self.name = name

    def __getattr__(self, tool_name: str):
        def invoke(*args: Any) -> _Result:
            qualified = f"{self.name}.{tool_name}"
            self.owner.calls.append((qualified, args))
            if qualified == "stats.CalculateDistanceBand":
                return _Result(["10.5", "20", "31.25"])
            if self.owner.mark_outputs:
                output_indices = {
                    "stats.GenerateSpatialWeightsMatrix": (2,),
                    "stats.MultivariateClustering": (1, 7),
                    "stats.SpatiallyConstrainedMultivariateClustering": (1, 11),
                    "stats.GeneralizedLinearRegression": (3,),
                    "gapro.FindPointClusters": (1,),
                    "stpm.CreateSpaceTimeCube": (1,),
                    "stpm.EmergingHotSpotAnalysis": (2,),
                    "stpm.TimeSeriesClustering": (2, 5),
                    "stpm.CurveFitForecast": (2, 3),
                    "stpm.ExponentialSmoothingForecast": (2, 3),
                    "stpm.ForestBasedForecast": (2, 3),
                    "stpm.EvaluateForecastsByLocation": (1, 2),
                }.get(qualified, ())
                for index in output_indices:
                    if args[index] not in (None, "", "#"):
                        self.owner.existing.add(_norm(args[index]))
            return _Result()

        return invoke


class _Arcpy:
    def __init__(
        self,
        existing: list[str],
        *,
        product: str = "ArcInfo",
        mark_outputs: bool = True,
    ) -> None:
        self.existing = {_norm(value) for value in existing}
        self.product = product
        self.mark_outputs = mark_outputs
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.environment_events: list[tuple[str, dict[str, Any]]] = []
        self.stats = _Toolbox(self, "stats")
        self.gapro = _Toolbox(self, "gapro")
        self.stpm = _Toolbox(self, "stpm")
        self.management = SimpleNamespace(GetCount=lambda _path: _Result(["7"]))

    def Exists(self, value: Any) -> bool:
        return _norm(value) in self.existing

    def ProductInfo(self) -> str:
        return self.product

    def EnvManager(self, **values: Any) -> _Environment:
        return _Environment(self, values)


@contextmanager
def _policy_roots(*, write: bool = True) -> Iterator[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
        environment = {
            # Forecast cubes produced under the GP root are valid inputs to the
            # evaluation stage, so both controlled roots are explicitly readable.
            "ARCGIS_PRO_MCP_INPUT_ROOTS": os.pathsep.join((input_root, output_root)),
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
        }
        if write:
            environment["ARCGIS_PRO_MCP_ALLOW_WRITE"] = "1"
        with patch.dict(os.environ, environment, clear=True):
            yield input_root, output_root


class SpatialModelingTests(unittest.TestCase):
    def test_calculate_distance_band_returns_verified_derived_values(self) -> None:
        with _policy_roots(write=False) as (input_root, _output_root):
            source = str(Path(input_root) / "points.shp")
            arcpy = _Arcpy([source])
            result = spatial_modeling.calculate_distance_band(
                arcpy,
                source,
                8,
                environment={"output_coordinate_system": 3857},
            )
        self.assertEqual(result["minimum_distance"], 10.5)
        self.assertEqual(result["average_distance"], 20.0)
        self.assertEqual(result["maximum_distance"], 31.25)
        self.assertEqual(
            arcpy.calls,
            [("stats.CalculateDistanceBand", (_norm(source), 8, "EUCLIDEAN_DISTANCE"))],
        )
        self.assertEqual(
            arcpy.environment_events,
            [("enter", {"outputCoordinateSystem": 3857}), ("exit", {"outputCoordinateSystem": 3857})],
        )

    def test_generate_spatial_weights_matrix_uses_documented_nine_parameters(self) -> None:
        with _policy_roots() as (input_root, output_root):
            source = str(Path(input_root) / "polygons.gdb" / "districts")
            output = str(Path(output_root) / "districts.swm")
            arcpy = _Arcpy([source])
            result = spatial_modeling.generate_spatial_weights_matrix(
                arcpy,
                source,
                "DISTRICT_ID",
                output,
                "K_NEAREST_NEIGHBORS",
                number_of_neighbors=12,
                row_standardization="NO_STANDARDIZATION",
            )
        self.assertTrue(result["outputs"]["output_swm"]["verified"])
        self.assertEqual(
            arcpy.calls[0],
            (
                "stats.GenerateSpatialWeightsMatrix",
                (
                    _norm(source),
                    "DISTRICT_ID",
                    _norm(output),
                    "K_NEAREST_NEIGHBORS",
                    "EUCLIDEAN",
                    None,
                    None,
                    12,
                    "NO_STANDARDIZATION",
                ),
            ),
        )

    def test_find_point_clusters_enforces_advanced_and_typed_time_parameters(self) -> None:
        with _policy_roots() as (input_root, output_root):
            source = str(Path(input_root) / "events.shp")
            output = str(Path(output_root) / "result.gdb" / "clusters")
            arcpy = _Arcpy([source])
            result = spatial_modeling.find_point_clusters(
                arcpy,
                source,
                output,
                "DBSCAN",
                5,
                "2 kilometers",
                use_time=True,
                search_duration="3 Days",
            )
            with self.assertRaisesRegex(RuntimeError, "Advanced"):
                spatial_modeling.find_point_clusters(
                    _Arcpy([source], product="ArcView"),
                    source,
                    str(Path(output_root) / "result.gdb" / "denied"),
                    "DBSCAN",
                    5,
                    "2 Kilometers",
                )
        self.assertEqual(result["license_requirement"], "Advanced")
        self.assertEqual(
            arcpy.calls[0][1][2:],
            ("DBSCAN", 5, "2 Kilometers", "TIME", "3 Days"),
        )

    def test_spatial_statistics_models_have_fixed_typed_signatures(self) -> None:
        with _policy_roots() as (input_root, output_root):
            source = str(Path(input_root) / "data.gdb" / "areas")
            weights = str(Path(input_root) / "weights.swm")
            arcpy = _Arcpy([source, weights])
            multi_output = str(Path(output_root) / "out.gdb" / "multi")
            multi_table = str(Path(output_root) / "out.gdb" / "multi_eval")
            spatial_output = str(Path(output_root) / "out.gdb" / "spatial")
            spatial_table = str(Path(output_root) / "out.gdb" / "spatial_eval")
            glr_output = str(Path(output_root) / "out.gdb" / "glr")

            spatial_modeling.multivariate_clustering(
                arcpy,
                source,
                multi_output,
                ["POP", "INCOME"],
                number_of_clusters=4,
                output_table=multi_table,
            )
            spatial_modeling.spatially_constrained_multivariate_clustering(
                arcpy,
                source,
                spatial_output,
                spatial_table,
                ["POP", "INCOME"],
                size_constraints="ATTRIBUTE_VALUE",
                constraint_field="POP",
                min_constraint=100,
                spatial_constraints="GET_SPATIAL_WEIGHTS_FROM_FILE",
                weights_matrix_file=weights,
                number_of_permutations=99,
            )
            glr = spatial_modeling.generalized_linear_regression(
                arcpy,
                source,
                "CASES",
                "COUNT",
                glr_output,
                ["POP", "INCOME"],
            )

        self.assertEqual([name for name, _args in arcpy.calls], [
            "stats.MultivariateClustering",
            "stats.SpatiallyConstrainedMultivariateClustering",
            "stats.GeneralizedLinearRegression",
        ])
        self.assertEqual(arcpy.calls[0][1][2], ["POP", "INCOME"])
        self.assertEqual(arcpy.calls[1][1][9], _norm(weights))
        self.assertEqual(glr["outputs"]["output_features"]["row_count"], 7)

    def test_create_space_time_cube_converts_typed_summary_rows_and_datetime(self) -> None:
        with _policy_roots() as (input_root, output_root):
            source = str(Path(input_root) / "events.shp")
            output = str(Path(output_root) / "events.nc")
            arcpy = _Arcpy([source])
            result = spatial_modeling.create_space_time_cube(
                arcpy,
                source,
                output,
                "EVENT_TIME",
                "1 Weeks",
                "500 Meters",
                time_step_alignment="REFERENCE_TIME",
                reference_time="2026-09-03T00:00:00",
                summary_fields=[
                    {"field": "VALUE", "statistic": "MEAN", "fill_empty_bins": "ZEROS"}
                ],
                aggregation_shape_type="FISHNET_GRID",
            )
        args = arcpy.calls[0][1]
        self.assertEqual(args[4], "1 Weeks")
        self.assertEqual(args[7], "500 Meters")
        self.assertEqual(args[8], [["VALUE", "MEAN", "ZEROS"]])
        self.assertEqual(args[6].isoformat(), "2026-09-03T00:00:00")
        self.assertTrue(result["outputs"]["output_cube"]["verified"])

    def test_hot_spot_and_time_series_clustering_are_allowlisted(self) -> None:
        with _policy_roots() as (input_root, output_root):
            cube = str(Path(input_root) / "events.nc")
            mask = str(Path(input_root) / "mask.shp")
            arcpy = _Arcpy([cube, mask])
            hot = str(Path(output_root) / "out.gdb" / "hot")
            clustered = str(Path(output_root) / "out.gdb" / "clusters")
            chart_table = str(Path(output_root) / "out.gdb" / "cluster_charts")
            spatial_modeling.emerging_hot_spot_analysis(
                arcpy,
                cube,
                "COUNT",
                hot,
                neighborhood_distance="5 Miles",
                neighborhood_time_step=2,
                polygon_mask=mask,
                define_global_window="NEIGHBORHOOD_TIME_STEP",
            )
            spatial_modeling.time_series_clustering(
                arcpy,
                cube,
                "COUNT",
                clustered,
                "PROFILE_FOURIER",
                cluster_count=3,
                output_table_for_charts=chart_table,
                shape_characteristics_to_ignore=["TIME_LAG", "RANGE"],
                enable_time_series_popups=True,
            )
        self.assertEqual(arcpy.calls[0][1][3:9], (
            "5 Miles",
            2,
            _norm(mask),
            "FIXED_DISTANCE",
            None,
            "NEIGHBORHOOD_TIME_STEP",
        ))
        self.assertEqual(arcpy.calls[1][1][6], ["TIME_LAG", "RANGE"])
        self.assertEqual(arcpy.calls[1][1][7], "CREATE_POPUP")

    def test_forecasts_and_official_evaluate_by_location_name(self) -> None:
        with _policy_roots() as (input_root, output_root):
            input_cube = str(Path(input_root) / "history.nc")
            arcpy = _Arcpy([input_cube])

            curve_features = str(Path(output_root) / "out.gdb" / "curve")
            curve_cube = str(Path(output_root) / "curve.nc")
            exp_features = str(Path(output_root) / "out.gdb" / "exp")
            exp_cube = str(Path(output_root) / "exp.nc")
            forest_features = str(Path(output_root) / "out.gdb" / "forest")
            forest_cube = str(Path(output_root) / "forest.nc")

            spatial_modeling.curve_fit_forecast(
                arcpy,
                input_cube,
                "VALUE",
                curve_features,
                curve_cube,
                4,
                curve_type="GOMPERTZ",
                number_for_validation=2,
            )
            spatial_modeling.exponential_smoothing_forecast(
                arcpy,
                input_cube,
                "VALUE",
                exp_features,
                exp_cube,
                4,
                12,
            )
            spatial_modeling.forest_based_forecast(
                arcpy,
                input_cube,
                "VALUE",
                forest_features,
                forest_cube,
                4,
                time_window=6,
                number_of_trees=200,
                forecast_approach="VALUE_DETREND",
            )
            evaluated_features = str(Path(output_root) / "out.gdb" / "best")
            evaluated_cube = str(Path(output_root) / "best.nc")
            evaluated = spatial_modeling.evaluate_forecasts_by_location(
                arcpy,
                [curve_cube, exp_cube, forest_cube],
                evaluated_features,
                evaluated_cube,
                "USE_VALIDATION",
            )

        self.assertEqual([name for name, _args in arcpy.calls], [
            "stpm.CurveFitForecast",
            "stpm.ExponentialSmoothingForecast",
            "stpm.ForestBasedForecast",
            "stpm.EvaluateForecastsByLocation",
        ])
        self.assertEqual(arcpy.calls[0][1][5], "GOMPERTZ")
        self.assertEqual(arcpy.calls[1][1][5], 12)
        self.assertEqual(arcpy.calls[2][1][11], "VALUE_DETREND")
        self.assertEqual(evaluated["tool"], "stpm.EvaluateForecastsByLocation")

    def test_write_gate_environment_allowlist_and_output_verification_fail_closed(self) -> None:
        with _policy_roots(write=False) as (input_root, output_root):
            source = str(Path(input_root) / "areas.shp")
            arcpy = _Arcpy([source])
            with self.assertRaisesRegex(RuntimeError, "ALLOW_WRITE"):
                spatial_modeling.generalized_linear_regression(
                    arcpy,
                    source,
                    "Y",
                    "CONTINUOUS",
                    str(Path(output_root) / "out.gdb" / "glr"),
                    ["X"],
                )

        with _policy_roots() as (input_root, output_root):
            source = str(Path(input_root) / "areas.shp")
            arcpy = _Arcpy([source], mark_outputs=False)
            with self.assertRaisesRegex(RuntimeError, "未创建"):
                spatial_modeling.generalized_linear_regression(
                    arcpy,
                    source,
                    "Y",
                    "CONTINUOUS",
                    str(Path(output_root) / "out.gdb" / "glr"),
                    ["X"],
                )
            with self.assertRaisesRegex(RuntimeError, "不支持这些环境参数"):
                spatial_modeling.calculate_distance_band(
                    arcpy,
                    source,
                    4,
                    environment={"workspace": output_root},
                )

    def test_invalid_cross_parameter_combinations_fail_before_arcpy(self) -> None:
        with _policy_roots() as (input_root, output_root):
            source = str(Path(input_root) / "areas.shp")
            cube = str(Path(input_root) / "cube.nc")
            arcpy = _Arcpy([source, cube])
            with self.assertRaisesRegex(RuntimeError, "number_of_neighbors"):
                spatial_modeling.generate_spatial_weights_matrix(
                    arcpy,
                    source,
                    "OID",
                    str(Path(output_root) / "weights.swm"),
                    "K_NEAREST_NEIGHBORS",
                )
            with self.assertRaisesRegex(RuntimeError, "仅适用于 PROFILE_FOURIER"):
                spatial_modeling.time_series_clustering(
                    arcpy,
                    cube,
                    "COUNT",
                    str(Path(output_root) / "out.gdb" / "clusters"),
                    "VALUE",
                    shape_characteristics_to_ignore=["TIME_LAG"],
                )


if __name__ == "__main__":
    unittest.main()
