"""Typed, policy-constrained spatial modeling and forecasting helpers.

Only documented ArcPy entry points are called.  The module deliberately does
not import ArcPy and does not register MCP tools; callers inject ``arcpy`` so
the validation layer remains testable outside ArcGIS Pro.
"""

from __future__ import annotations

import math
import os
import re
from datetime import datetime
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)
from arcgis_pro_mcp.raster_runtime import scoped_environment

_FIELD_MAX = 128
_ENVIRONMENT_KEYS = frozenset(
    {
        "extent",
        "output_coordinate_system",
        "geographic_transformations",
        "parallel_processing_factor",
    }
)
_LINEAR_UNITS = {
    "METERS": "Meters",
    "KILOMETERS": "Kilometers",
    "FEET": "Feet",
    "MILES": "Miles",
    "YARDS": "Yards",
    "NAUTICAL MILES": "Nautical Miles",
}
_CUBE_LINEAR_UNITS = {
    "METERS": "Meters",
    "KILOMETERS": "Kilometers",
    "FEET": "Feet",
    "MILES": "Miles",
}
_TIME_UNITS = {
    "SECONDS": "Seconds",
    "MINUTES": "Minutes",
    "HOURS": "Hours",
    "DAYS": "Days",
    "WEEKS": "Weeks",
    "MONTHS": "Months",
    "YEARS": "Years",
}
_UNIT_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s+([A-Za-z ]+)$")

_SWM_CONCEPTUALIZATIONS = frozenset(
    {
        "INVERSE_DISTANCE",
        "FIXED_DISTANCE",
        "K_NEAREST_NEIGHBORS",
        "CONTIGUITY_EDGES_ONLY",
        "CONTIGUITY_EDGES_CORNERS",
        "DELAUNAY_TRIANGULATION",
    }
)
_DISTANCE_METHODS = frozenset({"EUCLIDEAN", "MANHATTAN"})
_ROW_STANDARDIZATIONS = frozenset({"ROW_STANDARDIZATION", "NO_STANDARDIZATION"})
_CLUSTERING_METHODS = frozenset({"K_MEANS", "K_MEDOIDS"})
_INITIALIZATION_METHODS = frozenset(
    {
        "OPTIMIZED_SEED_LOCATIONS",
        "USER_DEFINED_SEED_LOCATIONS",
        "RANDOM_SEED_LOCATIONS",
    }
)
_SIZE_CONSTRAINTS = frozenset({"NONE", "NUM_FEATURES", "ATTRIBUTE_VALUE"})
_SPATIAL_CONSTRAINTS = frozenset(
    {
        "CONTIGUITY_EDGES_ONLY",
        "CONTIGUITY_EDGES_CORNERS",
        "TRIMMED_DELAUNAY_TRIANGULATION",
        "GET_SPATIAL_WEIGHTS_FROM_FILE",
    }
)
_GLR_MODELS = frozenset({"CONTINUOUS", "BINARY", "COUNT"})
_POINT_CLUSTER_METHODS = frozenset({"DBSCAN", "HDBSCAN"})
_CUBE_ALIGNMENTS = frozenset({"END_TIME", "START_TIME", "REFERENCE_TIME"})
_CUBE_SHAPES = frozenset({"FISHNET_GRID", "HEXAGON_GRID"})
_SUMMARY_STATISTICS = frozenset({"SUM", "MEAN", "MIN", "MAX", "STD", "MEDIAN"})
_EMPTY_BIN_METHODS = frozenset(
    {"ZEROS", "SPATIAL_NEIGHBORS", "SPACE_TIME_NEIGHBORS", "TEMPORAL_TREND"}
)
_HOT_SPOT_CONCEPTUALIZATIONS = frozenset(
    {
        "FIXED_DISTANCE",
        "K_NEAREST_NEIGHBORS",
        "CONTIGUITY_EDGES_ONLY",
        "CONTIGUITY_EDGES_CORNERS",
    }
)
_GLOBAL_WINDOWS = frozenset(
    {"ENTIRE_CUBE", "NEIGHBORHOOD_TIME_STEP", "INDIVIDUAL_TIME_STEP"}
)
_TIME_SERIES_CHARACTERISTICS = frozenset({"VALUE", "PROFILE", "PROFILE_FOURIER"})
_SHAPE_CHARACTERISTICS = frozenset({"TIME_LAG", "RANGE"})
_CURVE_TYPES = frozenset({"LINEAR", "PARABOLIC", "EXPONENTIAL", "GOMPERTZ", "AUTO_DETECT"})
_OUTLIER_OPTIONS = frozenset({"NONE", "IDENTIFY"})
_CONFIDENCE_LEVELS = frozenset({"90%", "95%", "99%"})
_FORECAST_APPROACHES = frozenset({"VALUE", "VALUE_DETREND", "RESIDUAL"})
_EVALUATION_METHODS = frozenset({"USE_VALIDATION", "NO_VALIDATION"})


def _enum(value: str, allowed: frozenset[str], label: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized not in allowed:
        raise RuntimeError(f"{label} 须为 {sorted(allowed)}")
    return normalized


def _field(value: str, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise RuntimeError(f"{label} 不能为空")
    if len(cleaned) > _FIELD_MAX or any(char in cleaned for char in ("\x00", ";", "\r", "\n")):
        raise RuntimeError(f"{label} 无效")
    return cleaned


def _fields(values: list[str], label: str) -> list[str]:
    if not isinstance(values, list) or not values or len(values) > 128:
        raise RuntimeError(f"{label} 必须包含 1–128 个字段")
    cleaned = [_field(value, label) for value in values]
    if len({value.casefold() for value in cleaned}) != len(cleaned):
        raise RuntimeError(f"{label} 不得包含重复字段")
    return cleaned


def _finite(value: Any, label: str, *, minimum: float | None = None, strict: bool = False) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{label} 必须为有限数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 必须为有限数值") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"{label} 必须为有限数值")
    if minimum is not None and (number < minimum or (strict and number == minimum)):
        comparator = "大于" if strict else "大于或等于"
        raise RuntimeError(f"{label} 必须{comparator} {minimum}")
    return number


def _optional_finite(
    value: float | int | None,
    label: str,
    *,
    minimum: float | None = None,
    strict: bool = False,
) -> float | None:
    if value is None:
        return None
    return _finite(value, label, minimum=minimum, strict=strict)


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{label} 必须为整数")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f"–{maximum}" if maximum is not None else " 以上"
        raise RuntimeError(f"{label} 必须在 {minimum}{suffix} 范围内")
    return value


def _optional_integer(
    value: int | None,
    label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _integer(value, label, minimum=minimum, maximum=maximum)


def _unit(value: str, label: str, allowed: dict[str, str]) -> str:
    match = _UNIT_RE.fullmatch((value or "").strip())
    if match is None:
        raise RuntimeError(f"{label} 必须为“正数 + 空格 + 单位”")
    number = _finite(match.group(1), label, minimum=0, strict=True)
    unit = allowed.get(match.group(2).strip().upper())
    if unit is None:
        raise RuntimeError(f"{label} 的单位须为 {sorted(allowed.values())}")
    return f"{number:g} {unit}"


def _linear_unit(value: str, label: str) -> str:
    return _unit(value, label, _LINEAR_UNITS)


def _cube_linear_unit(value: str, label: str) -> str:
    return _unit(value, label, _CUBE_LINEAR_UNITS)


def _time_unit(value: str, label: str) -> str:
    return _unit(value, label, _TIME_UNITS)


def _iso_datetime(value: str, label: str) -> datetime | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise RuntimeError(f"{label} 必须为 ISO-8601 日期时间") from exc
    if parsed.tzinfo is not None:
        raise RuntimeError(f"{label} 不得包含时区；请使用 ArcGIS 工程的本地时间")
    return parsed


def _exists(arcpy: Any, path: Any) -> bool:
    checker = getattr(arcpy, "Exists", None)
    if callable(checker):
        try:
            if bool(checker(path)):
                return True
        except Exception:  # noqa: BLE001
            pass
    return isinstance(path, str) and os.path.exists(path)


def _input(arcpy: Any, value: Any, label: str) -> Any:
    path = validate_input_path_optional(value, label)
    if not _exists(arcpy, path):
        raise RuntimeError(f"{label} 不存在：{path}")
    return path


def _file_input(arcpy: Any, value: str, label: str, suffix: str) -> str:
    path = _input(arcpy, value, label)
    if not isinstance(path, str) or not path.lower().endswith(suffix):
        raise RuntimeError(f"{label} 必须为 {suffix} 文件")
    return path


def _new_output(arcpy: Any, value: str, label: str, *, suffix: str = "") -> str:
    require_allow_write()
    require_gp_output_root_mandatory()
    path = validate_gp_output_path(value, label)
    if suffix and not path.lower().endswith(suffix):
        raise RuntimeError(f"{label} 必须使用 {suffix} 扩展名")
    if _exists(arcpy, path):
        raise RuntimeError(f"{label} 已存在；空间建模工具拒绝隐式覆盖：{path}")
    return path


def _tool(arcpy: Any, toolbox_name: str, tool_name: str) -> Any:
    toolbox = getattr(arcpy, toolbox_name, None)
    tool = getattr(toolbox, tool_name, None)
    if not callable(tool):
        raise RuntimeError(
            f"当前 ArcGIS Pro/ArcPy 不提供 arcpy.{toolbox_name}.{tool_name}；"
            "请核对 Pro 版本和许可等级"
        )
    return tool


def _messages(result: Any) -> list[str]:
    messages: list[str] = []
    try:
        for index in range(int(result.messageCount)):
            messages.append(str(result.getMessage(index)))
    except Exception:  # noqa: BLE001
        try:
            combined = str(result.getMessages())
        except Exception:  # noqa: BLE001
            combined = ""
        if combined:
            messages.append(combined)
    return messages


def _verify_output(arcpy: Any, path: str, label: str) -> dict[str, Any]:
    if not _exists(arcpy, path):
        raise RuntimeError(f"{label} 未创建或 ArcPy 不可见：{path}")
    payload: dict[str, Any] = {"path": path, "exists": True, "verified": True}
    if not path.lower().endswith((".nc", ".swm")):
        get_count = getattr(getattr(arcpy, "management", None), "GetCount", None)
        if callable(get_count):
            try:
                payload["row_count"] = int(get_count(path).getOutput(0))
            except Exception:  # noqa: BLE001
                pass
    return payload


def _result_payload(
    arcpy: Any,
    result: Any,
    tool_name: str,
    outputs: dict[str, str],
    *,
    license_requirement: str = "Basic",
) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "license_requirement": license_requirement,
        "outputs": {
            name: _verify_output(arcpy, path, name) for name, path in outputs.items()
        },
        "messages": _messages(result),
    }


def _modeling_environment(environment: dict[str, Any] | None) -> dict[str, Any] | None:
    if environment is None:
        return None
    if not isinstance(environment, dict):
        raise RuntimeError("environment 必须为字典")
    unknown = sorted(set(environment) - _ENVIRONMENT_KEYS)
    if unknown:
        raise RuntimeError(f"空间建模不支持这些环境参数：{unknown}")
    return environment


def _require_advanced(arcpy: Any) -> None:
    product_info = getattr(arcpy, "ProductInfo", None)
    if not callable(product_info):
        raise RuntimeError("无法核验 Advanced 许可：当前 arcpy 不支持 ProductInfo")
    level = str(product_info()).strip().upper()
    if level not in {"ARCINFO", "ADVANCED"}:
        raise RuntimeError(f"FindPointClusters 需要 ArcGIS Pro Advanced 许可；当前为 {level or 'UNKNOWN'}")


def calculate_distance_band(
    arcpy: Any,
    in_features: str,
    number_of_neighbors: int,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ``stats.CalculateDistanceBand`` and verify its three derived values."""
    source = _input(arcpy, in_features, "in_features")
    neighbors = _integer(number_of_neighbors, "number_of_neighbors", minimum=1)
    method = _enum(
        distance_method,
        frozenset({"EUCLIDEAN_DISTANCE", "MANHATTAN_DISTANCE"}),
        "distance_method",
    )
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stats", "CalculateDistanceBand")(source, neighbors, method)
    try:
        values = [float(result.getOutput(index)) for index in range(3)]
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("CalculateDistanceBand 未返回可核验的最小/平均/最大距离") from exc
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise RuntimeError("CalculateDistanceBand 返回了无效距离")
    return {
        "tool": "stats.CalculateDistanceBand",
        "license_requirement": "Basic",
        "minimum_distance": values[0],
        "average_distance": values[1],
        "maximum_distance": values[2],
        "messages": _messages(result),
    }


def generate_spatial_weights_matrix(
    arcpy: Any,
    in_features: str,
    unique_id_field: str,
    output_swm: str,
    conceptualization: str,
    distance_method: str = "EUCLIDEAN",
    exponent: float | None = None,
    threshold_distance: float | None = None,
    number_of_neighbors: int | None = None,
    row_standardization: str = "ROW_STANDARDIZATION",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a documented ``.swm`` using the stable first nine parameters."""
    source = _input(arcpy, in_features, "in_features")
    unique_id = _field(unique_id_field, "unique_id_field")
    output = _new_output(arcpy, output_swm, "output_swm", suffix=".swm")
    concept = _enum(conceptualization, _SWM_CONCEPTUALIZATIONS, "conceptualization")
    method = _enum(distance_method, _DISTANCE_METHODS, "distance_method")
    power = _optional_finite(exponent, "exponent", minimum=0, strict=True)
    threshold = _optional_finite(threshold_distance, "threshold_distance", minimum=0)
    neighbors = _optional_integer(number_of_neighbors, "number_of_neighbors", minimum=1)
    if concept == "K_NEAREST_NEIGHBORS" and neighbors is None:
        raise RuntimeError("K_NEAREST_NEIGHBORS 必须提供 number_of_neighbors")
    standardization = _enum(
        row_standardization,
        _ROW_STANDARDIZATIONS,
        "row_standardization",
    )
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stats", "GenerateSpatialWeightsMatrix")(
            source,
            unique_id,
            output,
            concept,
            method,
            power,
            threshold,
            neighbors,
            standardization,
        )
    return _result_payload(
        arcpy,
        result,
        "stats.GenerateSpatialWeightsMatrix",
        {"output_swm": output},
    )


def find_point_clusters(
    arcpy: Any,
    input_points: str,
    output_features: str,
    clustering_method: str,
    minimum_points: int,
    search_distance: str,
    use_time: bool = False,
    search_duration: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the Advanced-only GeoAnalytics Desktop point cluster tool."""
    _require_advanced(arcpy)
    source = _input(arcpy, input_points, "input_points")
    output = _new_output(arcpy, output_features, "output_features")
    method = _enum(clustering_method, _POINT_CLUSTER_METHODS, "clustering_method")
    minimum = _integer(minimum_points, "minimum_points", minimum=2)
    distance = _linear_unit(search_distance, "search_distance")
    if not isinstance(use_time, bool):
        raise RuntimeError("use_time 必须为布尔值")
    if method == "HDBSCAN" and (use_time or search_duration):
        raise RuntimeError("HDBSCAN 不支持时间聚类参数")
    duration = _time_unit(search_duration, "search_duration") if search_duration else None
    if use_time and duration is None:
        raise RuntimeError("use_time=true 时必须提供 search_duration")
    if not use_time and duration is not None:
        raise RuntimeError("search_duration 仅可在 use_time=true 时提供")
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "gapro", "FindPointClusters")(
            source,
            output,
            method,
            minimum,
            distance,
            "TIME" if use_time else "NO_TIME",
            duration,
        )
    return _result_payload(
        arcpy,
        result,
        "gapro.FindPointClusters",
        {"output_features": output},
        license_requirement="Advanced",
    )


def multivariate_clustering(
    arcpy: Any,
    in_features: str,
    output_features: str,
    analysis_fields: list[str],
    clustering_method: str = "K_MEANS",
    initialization_method: str = "OPTIMIZED_SEED_LOCATIONS",
    initialization_field: str = "",
    number_of_clusters: int | None = None,
    output_table: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = _input(arcpy, in_features, "in_features")
    output = _new_output(arcpy, output_features, "output_features")
    fields = _fields(analysis_fields, "analysis_fields")
    method = _enum(clustering_method, _CLUSTERING_METHODS, "clustering_method")
    initialization = _enum(
        initialization_method,
        _INITIALIZATION_METHODS,
        "initialization_method",
    )
    seed_field = _field(initialization_field, "initialization_field") if initialization_field else None
    clusters = _optional_integer(number_of_clusters, "number_of_clusters", minimum=2, maximum=30)
    if initialization == "USER_DEFINED_SEED_LOCATIONS":
        if seed_field is None:
            raise RuntimeError("USER_DEFINED_SEED_LOCATIONS 必须提供 initialization_field")
        if clusters is not None:
            raise RuntimeError("用户定义种子时不得设置 number_of_clusters")
    elif seed_field is not None:
        raise RuntimeError("initialization_field 仅可用于 USER_DEFINED_SEED_LOCATIONS")
    table = _new_output(arcpy, output_table, "output_table") if output_table else None
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stats", "MultivariateClustering")(
            source,
            output,
            fields,
            method,
            initialization,
            seed_field,
            clusters,
            table,
        )
    outputs = {"output_features": output}
    if table is not None:
        outputs["output_table"] = table
    return _result_payload(arcpy, result, "stats.MultivariateClustering", outputs)


def spatially_constrained_multivariate_clustering(
    arcpy: Any,
    in_features: str,
    output_features: str,
    output_table: str,
    analysis_fields: list[str],
    size_constraints: str = "NONE",
    constraint_field: str = "",
    min_constraint: float | None = None,
    max_constraint: float | None = None,
    number_of_clusters: int | None = None,
    spatial_constraints: str = "CONTIGUITY_EDGES_CORNERS",
    weights_matrix_file: str = "",
    number_of_permutations: int = 0,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = _input(arcpy, in_features, "in_features")
    output = _new_output(arcpy, output_features, "output_features")
    table = _new_output(arcpy, output_table, "output_table")
    fields = _fields(analysis_fields, "analysis_fields")
    size = _enum(size_constraints, _SIZE_CONSTRAINTS, "size_constraints")
    constraint = _field(constraint_field, "constraint_field") if constraint_field else None
    minimum = _optional_finite(min_constraint, "min_constraint", minimum=0, strict=True)
    maximum = _optional_finite(max_constraint, "max_constraint", minimum=0, strict=True)
    clusters = _optional_integer(number_of_clusters, "number_of_clusters", minimum=2, maximum=30)
    spatial = _enum(spatial_constraints, _SPATIAL_CONSTRAINTS, "spatial_constraints")
    permutations = _integer(
        number_of_permutations,
        "number_of_permutations",
        minimum=0,
        maximum=10000,
    )
    if size == "NONE" and any(value is not None for value in (constraint, minimum, maximum)):
        raise RuntimeError("size_constraints=NONE 时不得设置约束字段或上下限")
    if size == "NUM_FEATURES" and constraint is not None:
        raise RuntimeError("NUM_FEATURES 不使用 constraint_field")
    if size == "ATTRIBUTE_VALUE" and constraint is None:
        raise RuntimeError("ATTRIBUTE_VALUE 必须提供 constraint_field")
    if size != "NONE" and minimum is None and maximum is None:
        raise RuntimeError("启用 cluster size constraint 时至少提供 min_constraint 或 max_constraint")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise RuntimeError("min_constraint 不得大于 max_constraint")
    if maximum is not None and clusters is not None:
        raise RuntimeError("设置 max_constraint 时不得设置 number_of_clusters")
    if spatial == "GET_SPATIAL_WEIGHTS_FROM_FILE":
        weights = _file_input(arcpy, weights_matrix_file, "weights_matrix_file", ".swm")
    else:
        if weights_matrix_file:
            raise RuntimeError("weights_matrix_file 仅可用于 GET_SPATIAL_WEIGHTS_FROM_FILE")
        weights = None
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stats", "SpatiallyConstrainedMultivariateClustering")(
            source,
            output,
            fields,
            size,
            constraint,
            minimum,
            maximum,
            clusters,
            spatial,
            weights,
            permutations,
            table,
        )
    return _result_payload(
        arcpy,
        result,
        "stats.SpatiallyConstrainedMultivariateClustering",
        {"output_features": output, "output_table": table},
    )


def generalized_linear_regression(
    arcpy: Any,
    in_features: str,
    dependent_variable: str,
    model_type: str,
    output_features: str,
    explanatory_variables: list[str],
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fit the documented GLR core model without open-ended prediction mappings."""
    source = _input(arcpy, in_features, "in_features")
    dependent = _field(dependent_variable, "dependent_variable")
    model = _enum(model_type, _GLR_MODELS, "model_type")
    output = _new_output(arcpy, output_features, "output_features")
    explanatory = _fields(explanatory_variables, "explanatory_variables")
    if dependent.casefold() in {field.casefold() for field in explanatory}:
        raise RuntimeError("dependent_variable 不得同时作为 explanatory_variable")
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stats", "GeneralizedLinearRegression")(
            source,
            dependent,
            model,
            output,
            explanatory,
        )
    return _result_payload(
        arcpy,
        result,
        "stats.GeneralizedLinearRegression",
        {"output_features": output},
    )


def _summary_fields(values: list[dict[str, str]] | None) -> list[list[str]]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > 64:
        raise RuntimeError("summary_fields 最多包含 64 项")
    output: list[list[str]] = []
    required_keys = {"field", "statistic", "fill_empty_bins"}
    for index, item in enumerate(values):
        if not isinstance(item, dict) or set(item) != required_keys:
            raise RuntimeError(
                f"summary_fields[{index}] 必须且只能包含 field/statistic/fill_empty_bins"
            )
        output.append(
            [
                _field(item["field"], f"summary_fields[{index}].field"),
                _enum(
                    item["statistic"],
                    _SUMMARY_STATISTICS,
                    f"summary_fields[{index}].statistic",
                ),
                _enum(
                    item["fill_empty_bins"],
                    _EMPTY_BIN_METHODS,
                    f"summary_fields[{index}].fill_empty_bins",
                ),
            ]
        )
    return output


def create_space_time_cube(
    arcpy: Any,
    in_features: str,
    output_cube: str,
    time_field: str,
    time_step_interval: str,
    distance_interval: str,
    template_cube: str = "",
    time_step_alignment: str = "END_TIME",
    reference_time: str = "",
    summary_fields: list[dict[str, str]] | None = None,
    aggregation_shape_type: str = "HEXAGON_GRID",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a regular-grid cube with typed summary-field value-table rows."""
    source = _input(arcpy, in_features, "in_features")
    output = _new_output(arcpy, output_cube, "output_cube", suffix=".nc")
    temporal_field = _field(time_field, "time_field")
    template = _file_input(arcpy, template_cube, "template_cube", ".nc") if template_cube else None
    if template is not None:
        if time_step_interval or distance_interval or reference_time:
            raise RuntimeError(
                "使用 template_cube 时 time_step_interval、distance_interval 和 reference_time 必须为空"
            )
        interval = None
        distance = None
        reference = None
    else:
        interval = _time_unit(time_step_interval, "time_step_interval")
        distance = _cube_linear_unit(distance_interval, "distance_interval")
        reference = _iso_datetime(reference_time, "reference_time")
    alignment = _enum(time_step_alignment, _CUBE_ALIGNMENTS, "time_step_alignment")
    if alignment == "REFERENCE_TIME" and template is None and reference is None:
        raise RuntimeError("REFERENCE_TIME 必须提供 reference_time")
    if alignment != "REFERENCE_TIME" and reference is not None:
        raise RuntimeError("reference_time 仅可用于 REFERENCE_TIME")
    summaries = _summary_fields(summary_fields)
    shape = _enum(aggregation_shape_type, _CUBE_SHAPES, "aggregation_shape_type")
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stpm", "CreateSpaceTimeCube")(
            source,
            output,
            temporal_field,
            template,
            interval,
            alignment,
            reference,
            distance,
            summaries,
            shape,
        )
    return _result_payload(
        arcpy,
        result,
        "stpm.CreateSpaceTimeCube",
        {"output_cube": output},
    )


def emerging_hot_spot_analysis(
    arcpy: Any,
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    neighborhood_distance: str = "",
    neighborhood_time_step: int | None = None,
    polygon_mask: str = "",
    conceptualization: str = "FIXED_DISTANCE",
    number_of_neighbors: int | None = None,
    define_global_window: str = "ENTIRE_CUBE",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cube = _file_input(arcpy, in_cube, "in_cube", ".nc")
    variable = _field(analysis_variable, "analysis_variable")
    output = _new_output(arcpy, output_features, "output_features")
    distance = _linear_unit(neighborhood_distance, "neighborhood_distance") if neighborhood_distance else None
    time_step = _optional_integer(neighborhood_time_step, "neighborhood_time_step", minimum=1)
    mask = _input(arcpy, polygon_mask, "polygon_mask") if polygon_mask else None
    concept = _enum(
        conceptualization,
        _HOT_SPOT_CONCEPTUALIZATIONS,
        "conceptualization",
    )
    neighbors = _optional_integer(number_of_neighbors, "number_of_neighbors", minimum=1)
    window = _enum(define_global_window, _GLOBAL_WINDOWS, "define_global_window")
    if concept == "FIXED_DISTANCE" and distance is None:
        raise RuntimeError("FIXED_DISTANCE 必须提供 neighborhood_distance")
    if concept == "K_NEAREST_NEIGHBORS" and neighbors is None:
        raise RuntimeError("K_NEAREST_NEIGHBORS 必须提供 number_of_neighbors")
    if window == "NEIGHBORHOOD_TIME_STEP" and time_step is None:
        raise RuntimeError("NEIGHBORHOOD_TIME_STEP 必须提供 neighborhood_time_step")
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stpm", "EmergingHotSpotAnalysis")(
            cube,
            variable,
            output,
            distance,
            time_step,
            mask,
            concept,
            neighbors,
            window,
        )
    return _result_payload(
        arcpy,
        result,
        "stpm.EmergingHotSpotAnalysis",
        {"output_features": output},
    )


def time_series_clustering(
    arcpy: Any,
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    characteristic_of_interest: str,
    cluster_count: int | None = None,
    output_table_for_charts: str = "",
    shape_characteristics_to_ignore: list[str] | None = None,
    enable_time_series_popups: bool = False,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cube = _file_input(arcpy, in_cube, "in_cube", ".nc")
    variable = _field(analysis_variable, "analysis_variable")
    output = _new_output(arcpy, output_features, "output_features")
    characteristic = _enum(
        characteristic_of_interest,
        _TIME_SERIES_CHARACTERISTICS,
        "characteristic_of_interest",
    )
    clusters = _optional_integer(cluster_count, "cluster_count", minimum=2, maximum=30)
    table = (
        _new_output(arcpy, output_table_for_charts, "output_table_for_charts")
        if output_table_for_charts
        else None
    )
    raw_ignored = shape_characteristics_to_ignore or []
    if not isinstance(raw_ignored, list) or len(raw_ignored) > 2:
        raise RuntimeError("shape_characteristics_to_ignore 最多包含 TIME_LAG 和 RANGE")
    ignored = [
        _enum(value, _SHAPE_CHARACTERISTICS, "shape_characteristics_to_ignore")
        for value in raw_ignored
    ]
    if len(set(ignored)) != len(ignored):
        raise RuntimeError("shape_characteristics_to_ignore 不得重复")
    if ignored and characteristic != "PROFILE_FOURIER":
        raise RuntimeError("shape_characteristics_to_ignore 仅适用于 PROFILE_FOURIER")
    if not isinstance(enable_time_series_popups, bool):
        raise RuntimeError("enable_time_series_popups 必须为布尔值")
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stpm", "TimeSeriesClustering")(
            cube,
            variable,
            output,
            characteristic,
            clusters,
            table,
            ignored or None,
            "CREATE_POPUP" if enable_time_series_popups else "NO_POPUP",
        )
    outputs = {"output_features": output}
    if table is not None:
        outputs["output_table_for_charts"] = table
    return _result_payload(arcpy, result, "stpm.TimeSeriesClustering", outputs)


def _forecast_common(
    arcpy: Any,
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    output_cube: str,
    number_of_time_steps_to_forecast: int,
    number_for_validation: int,
    outlier_option: str,
    level_of_confidence: str,
    maximum_number_of_outliers: int,
) -> tuple[str, str, str, str, int, int, str, str, int]:
    cube = _file_input(arcpy, in_cube, "in_cube", ".nc")
    variable = _field(analysis_variable, "analysis_variable")
    features = _new_output(arcpy, output_features, "output_features")
    forecast_cube = _new_output(arcpy, output_cube, "output_cube", suffix=".nc")
    if os.path.normcase(features) == os.path.normcase(forecast_cube):
        raise RuntimeError("output_features 与 output_cube 必须不同")
    steps = _integer(
        number_of_time_steps_to_forecast,
        "number_of_time_steps_to_forecast",
        minimum=1,
        maximum=10000,
    )
    validation = _integer(number_for_validation, "number_for_validation", minimum=0, maximum=10000)
    outliers = _enum(outlier_option, _OUTLIER_OPTIONS, "outlier_option")
    confidence = _enum(level_of_confidence, _CONFIDENCE_LEVELS, "level_of_confidence")
    maximum_outliers = _integer(
        maximum_number_of_outliers,
        "maximum_number_of_outliers",
        minimum=1,
        maximum=10000,
    )
    return (
        cube,
        variable,
        features,
        forecast_cube,
        steps,
        validation,
        outliers,
        confidence,
        maximum_outliers,
    )


def curve_fit_forecast(
    arcpy: Any,
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    output_cube: str,
    number_of_time_steps_to_forecast: int,
    curve_type: str = "AUTO_DETECT",
    number_for_validation: int = 0,
    outlier_option: str = "NONE",
    level_of_confidence: str = "90%",
    maximum_number_of_outliers: int = 1,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    common = _forecast_common(
        arcpy,
        in_cube,
        analysis_variable,
        output_features,
        output_cube,
        number_of_time_steps_to_forecast,
        number_for_validation,
        outlier_option,
        level_of_confidence,
        maximum_number_of_outliers,
    )
    curve = _enum(curve_type, _CURVE_TYPES, "curve_type")
    cube, variable, features, forecast_cube, steps, validation, outliers, confidence, maximum = common
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stpm", "CurveFitForecast")(
            cube,
            variable,
            features,
            forecast_cube,
            steps,
            curve,
            validation,
            outliers,
            confidence,
            maximum,
        )
    return _result_payload(
        arcpy,
        result,
        "stpm.CurveFitForecast",
        {"output_features": features, "output_cube": forecast_cube},
    )


def exponential_smoothing_forecast(
    arcpy: Any,
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    output_cube: str,
    number_of_time_steps_to_forecast: int,
    season_length: int,
    number_for_validation: int = 0,
    outlier_option: str = "NONE",
    level_of_confidence: str = "90%",
    maximum_number_of_outliers: int = 1,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    common = _forecast_common(
        arcpy,
        in_cube,
        analysis_variable,
        output_features,
        output_cube,
        number_of_time_steps_to_forecast,
        number_for_validation,
        outlier_option,
        level_of_confidence,
        maximum_number_of_outliers,
    )
    season = _integer(season_length, "season_length", minimum=2, maximum=10000)
    cube, variable, features, forecast_cube, steps, validation, outliers, confidence, maximum = common
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stpm", "ExponentialSmoothingForecast")(
            cube,
            variable,
            features,
            forecast_cube,
            steps,
            season,
            validation,
            outliers,
            confidence,
            maximum,
        )
    return _result_payload(
        arcpy,
        result,
        "stpm.ExponentialSmoothingForecast",
        {"output_features": features, "output_cube": forecast_cube},
    )


def forest_based_forecast(
    arcpy: Any,
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    output_cube: str,
    number_of_time_steps_to_forecast: int,
    time_window: int | None = None,
    number_for_validation: int = 0,
    number_of_trees: int = 100,
    minimum_leaf_size: int | None = None,
    maximum_depth: int | None = None,
    sample_size: int = 100,
    forecast_approach: str = "VALUE",
    outlier_option: str = "NONE",
    level_of_confidence: str = "90%",
    maximum_number_of_outliers: int = 1,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    common = _forecast_common(
        arcpy,
        in_cube,
        analysis_variable,
        output_features,
        output_cube,
        number_of_time_steps_to_forecast,
        number_for_validation,
        outlier_option,
        level_of_confidence,
        maximum_number_of_outliers,
    )
    window = _optional_integer(time_window, "time_window", minimum=1, maximum=10000)
    trees = _integer(number_of_trees, "number_of_trees", minimum=1, maximum=10000)
    leaf_size = _optional_integer(minimum_leaf_size, "minimum_leaf_size", minimum=1)
    depth = _optional_integer(maximum_depth, "maximum_depth", minimum=1)
    sample = _integer(sample_size, "sample_size", minimum=1, maximum=100)
    approach = _enum(forecast_approach, _FORECAST_APPROACHES, "forecast_approach")
    cube, variable, features, forecast_cube, steps, validation, outliers, confidence, maximum = common
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stpm", "ForestBasedForecast")(
            cube,
            variable,
            features,
            forecast_cube,
            steps,
            window,
            validation,
            trees,
            leaf_size,
            depth,
            sample,
            approach,
            outliers,
            confidence,
            maximum,
        )
    return _result_payload(
        arcpy,
        result,
        "stpm.ForestBasedForecast",
        {"output_features": features, "output_cube": forecast_cube},
    )


def evaluate_forecasts_by_location(
    arcpy: Any,
    in_cubes: list[str],
    output_features: str,
    output_cube: str = "",
    evaluate_using_validation_results: str = "USE_VALIDATION",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap the actual ArcPy name ``EvaluateForecastsByLocation``."""
    if not isinstance(in_cubes, list) or not 2 <= len(in_cubes) <= 32:
        raise RuntimeError("in_cubes 必须包含 2–32 个预测 .nc 文件")
    cubes = [_file_input(arcpy, value, "in_cubes", ".nc") for value in in_cubes]
    if len({os.path.normcase(path) for path in cubes}) != len(cubes):
        raise RuntimeError("in_cubes 不得重复")
    features = _new_output(arcpy, output_features, "output_features")
    forecast_cube = (
        _new_output(arcpy, output_cube, "output_cube", suffix=".nc") if output_cube else None
    )
    method = _enum(
        evaluate_using_validation_results,
        _EVALUATION_METHODS,
        "evaluate_using_validation_results",
    )
    with scoped_environment(arcpy, _modeling_environment(environment)):
        result = _tool(arcpy, "stpm", "EvaluateForecastsByLocation")(
            cubes,
            features,
            forecast_cube,
            method,
        )
    outputs = {"output_features": features}
    if forecast_cube is not None:
        outputs["output_cube"] = forecast_cube
    return _result_payload(arcpy, result, "stpm.EvaluateForecastsByLocation", outputs)
