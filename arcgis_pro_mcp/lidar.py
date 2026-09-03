"""Constrained LAS dataset inspection, creation, statistics, and pyramid tools."""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_destructive,
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
    validate_output_in_export_root,
)

_PRJ_MODES = frozenset({"NO_FILES", "FILES_MISSING_PROJECTION", "ALL_FILES"})
_PYRAMID_METHODS = frozenset({"Z_MIN", "Z_MAX", "CLOSEST_TO_CENTER", "CLASS_CODE"})
_SURFACE_TYPES = frozenset(
    {
        "HARDLINE",
        "SOFTLINE",
        "HARDCLIP",
        "SOFTCLIP",
        "HARDERASE",
        "SOFTERASE",
        "HARDREPLACE",
        "SOFTREPLACE",
    }
)


def _messages(result: Any) -> str:
    method = getattr(result, "getMessages", None)
    return str(method() or "")[:8000] if callable(method) else ""


def _required_text(value: Any, label: str, maximum: int = 512) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or "\x00" in text:
        raise RuntimeError(f"{label} 不能为空、包含 NUL 或超过 {maximum} 字符")
    return text


def _exists(arcpy: Any, path: str) -> bool:
    checker = getattr(arcpy, "Exists", None)
    if callable(checker):
        return bool(checker(path))
    return os.path.exists(path)


def _input_las_dataset(arcpy: Any, path: str) -> str:
    value = validate_input_path_optional(path, "las_dataset")
    if not value.lower().endswith(".lasd"):
        raise RuntimeError("las_dataset 必须是 .lasd 文件")
    if not _exists(arcpy, value):
        raise RuntimeError(f"LAS 数据集不存在：{value}")
    return value


def _exact_target(expected: str, actual: str, label: str = "expected_las_dataset") -> None:
    expected_value = os.path.normcase(os.path.realpath(os.path.expanduser(str(expected or ""))))
    actual_value = os.path.normcase(os.path.realpath(os.path.expanduser(actual)))
    if not expected_value or expected_value != actual_value:
        raise RuntimeError(f"{label} 必须精确回显目标 LAS 数据集绝对路径")


def _extent(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    payload: dict[str, float] = {}
    for source, target in (("XMin", "xmin"), ("YMin", "ymin"), ("XMax", "xmax"), ("YMax", "ymax")):
        item = getattr(value, source, None)
        if item is not None:
            payload[target] = float(item)
    return payload or None


def _spatial_reference(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "name": str(getattr(value, "name", "") or ""),
        "wkid": getattr(value, "factoryCode", None),
    }


def _las_dataset_payload(arcpy: Any, path: str) -> dict[str, Any]:
    desc = arcpy.Describe(path)
    result: dict[str, Any] = {
        "las_dataset": path,
        "data_type": getattr(desc, "dataType", None),
        "file_count": getattr(desc, "fileCount", None),
        "point_count": getattr(desc, "pointCount", None),
        "constraint_count": getattr(desc, "constraintCount", None),
        "has_statistics": bool(getattr(desc, "hasStatistics", False)),
        "needs_update_statistics": bool(getattr(desc, "needsUpdateStatistics", False)),
        "extent": _extent(getattr(desc, "extent", None)),
        "spatial_reference": _spatial_reference(getattr(desc, "spatialReference", None)),
    }
    for source, target in (
        ("hasPyramid", "has_pyramid"),
        ("needsUpdatePyramid", "needs_update_pyramid"),
        ("pyramidStatus", "pyramid_status"),
        ("pyramidSelectionMethod", "pyramid_selection_method"),
    ):
        try:
            value = getattr(desc, source)
        except Exception:  # noqa: BLE001
            continue
        result[target] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return result


def las_dataset_info(arcpy: Any, las_dataset: str) -> dict[str, Any]:
    """Return a stable, JSON-friendly LAS dataset metadata snapshot."""
    return _las_dataset_payload(arcpy, _input_las_dataset(arcpy, las_dataset))


def _new_lasd_output(arcpy: Any, output_path: str) -> str:
    require_gp_output_root_mandatory()
    output = validate_gp_output_path(output_path, "out_las_dataset")
    if not output.lower().endswith(".lasd"):
        raise RuntimeError("out_las_dataset 必须以 .lasd 结尾")
    if _exists(arcpy, output):
        raise RuntimeError("out_las_dataset 已存在；本工具不执行隐式覆盖")
    return output


def _surface_constraints(
    values: list[dict[str, Any]] | None,
) -> list[list[str]]:
    result: list[list[str]] = []
    if len(values or []) > 64:
        raise RuntimeError("surface_constraints 最多 64 项")
    for index, raw in enumerate(values or []):
        if not isinstance(raw, dict) or set(raw) != {"feature_class", "height_field", "surface_type"}:
            raise RuntimeError(
                f"surface_constraints[{index}] 必须且只可包含 feature_class/height_field/surface_type"
            )
        feature_class = validate_input_path_optional(
            raw["feature_class"], f"surface_constraints[{index}].feature_class"
        )
        height_field = _required_text(
            raw["height_field"], f"surface_constraints[{index}].height_field", 128
        )
        surface_type = _required_text(
            raw["surface_type"], f"surface_constraints[{index}].surface_type", 32
        ).upper()
        if surface_type not in _SURFACE_TYPES:
            raise RuntimeError(f"surface_constraints[{index}].surface_type 不受支持")
        result.append([feature_class, height_field, surface_type.lower()])
    return result


def _processing_extent(arcpy: Any, extent: list[float] | None) -> Any:
    if extent in (None, []):
        return "#"
    if not isinstance(extent, list) or len(extent) != 4:
        raise RuntimeError("processing_extent 必须是 [xmin, ymin, xmax, ymax]")
    values = [float(item) for item in extent]
    if values[0] >= values[2] or values[1] >= values[3]:
        raise RuntimeError("processing_extent 必须满足 xmin < xmax 且 ymin < ymax")
    constructor = getattr(arcpy, "Extent", None)
    return constructor(*values) if callable(constructor) else " ".join(str(item) for item in values)


def create_las_dataset(
    arcpy: Any,
    input_paths: list[str],
    out_las_dataset: str,
    *,
    recurse_folders: bool = False,
    surface_constraints: list[dict[str, Any]] | None = None,
    spatial_reference_wkid: int | None = None,
    compute_statistics: bool = False,
    relative_paths: bool = False,
    create_las_prj: str = "NO_FILES",
    processing_extent: list[float] | None = None,
    boundary: str = "",
    contained_files_only: bool = False,
    confirm_all_las_prj: str = "",
) -> dict[str, Any]:
    """Create a new .lasd; optional statistics/PRJ sidecars are explicit."""
    require_allow_write()
    if not isinstance(input_paths, list) or not input_paths or len(input_paths) > 512:
        raise RuntimeError("input_paths 必须为 1–512 个路径")
    inputs = [
        validate_input_path_optional(value, f"input_paths[{index}]")
        for index, value in enumerate(input_paths)
    ]
    output = _new_lasd_output(arcpy, out_las_dataset)
    constraints = _surface_constraints(surface_constraints)
    prj_mode = _required_text(create_las_prj, "create_las_prj", 64).upper()
    if prj_mode not in _PRJ_MODES:
        raise RuntimeError(f"create_las_prj 须为 {sorted(_PRJ_MODES)}")
    if prj_mode != "NO_FILES" and spatial_reference_wkid is None:
        raise RuntimeError("创建 LAS PRJ 时必须提供 spatial_reference_wkid")
    if prj_mode == "ALL_FILES":
        require_allow_destructive()
        if confirm_all_las_prj != "CREATE_PRJ_FOR_ALL_LAS_FILES":
            raise RuntimeError(
                "create_las_prj=ALL_FILES 可能替换现有定义；confirm_all_las_prj 必须为 "
                "CREATE_PRJ_FOR_ALL_LAS_FILES"
            )
    spatial_reference: Any = "#"
    if spatial_reference_wkid is not None:
        wkid = int(spatial_reference_wkid)
        if wkid <= 0 or wkid > 9999999:
            raise RuntimeError("spatial_reference_wkid 无效")
        constructor = getattr(arcpy, "SpatialReference", None)
        spatial_reference = constructor(wkid) if callable(constructor) else wkid
    boundary_value = (
        validate_input_path_optional(boundary, "boundary") if str(boundary or "").strip() else "#"
    )
    result = arcpy.management.CreateLasDataset(
        inputs,
        output,
        "RECURSION" if recurse_folders else "NO_RECURSION",
        constraints or "#",
        spatial_reference,
        "COMPUTE_STATS" if compute_statistics else "NO_COMPUTE_STATS",
        "RELATIVE_PATHS" if relative_paths else "ABSOLUTE_PATHS",
        prj_mode,
        _processing_extent(arcpy, processing_extent),
        boundary_value,
        "CONTAINED_FILES" if contained_files_only else "INTERSECTED_FILES",
    )
    if not _exists(arcpy, output):
        raise RuntimeError(f"CreateLasDataset 未创建输出：{output}")
    payload = _las_dataset_payload(arcpy, output)
    payload.update(
        {
            "messages": _messages(result),
            "input_count": len(inputs),
            "computed_statistics": bool(compute_statistics),
            "created_prj_mode": prj_mode,
            "source_sidecar_write": bool(compute_statistics or prj_mode != "NO_FILES"),
        }
    )
    return payload


def calculate_las_statistics(
    arcpy: Any,
    las_dataset: str,
    *,
    expected_las_dataset: str,
    calculation_type: str = "SKIP_EXISTING_STATS",
    out_report: str = "",
    summary_level: str = "DATASET",
    delimiter: str = "SPACE",
    decimal_separator: str = "DECIMAL_POINT",
    confirm_overwrite: str = "",
) -> dict[str, Any]:
    """Calculate LAS statistics, with a destructive gate for forced recomputation."""
    require_allow_write()
    path = _input_las_dataset(arcpy, las_dataset)
    _exact_target(expected_las_dataset, path)
    calculation = _required_text(calculation_type, "calculation_type", 64).upper()
    if calculation not in {"SKIP_EXISTING_STATS", "OVERWRITE_EXISTING_STATS"}:
        raise RuntimeError("calculation_type 不受支持")
    if calculation == "OVERWRITE_EXISTING_STATS":
        require_allow_destructive()
        if confirm_overwrite != "OVERWRITE_ALL_LAS_STATISTICS":
            raise RuntimeError(
                "confirm_overwrite 必须为 OVERWRITE_ALL_LAS_STATISTICS"
            )
    summary = _required_text(summary_level, "summary_level", 32).upper()
    separator = _required_text(delimiter, "delimiter", 16).upper()
    decimal = _required_text(decimal_separator, "decimal_separator", 32).upper()
    if summary not in {"DATASET", "LAS_FILES"}:
        raise RuntimeError("summary_level 须为 DATASET/LAS_FILES")
    if separator not in {"SPACE", "COMMA"}:
        raise RuntimeError("delimiter 须为 SPACE/COMMA")
    if decimal not in {"DECIMAL_POINT", "DECIMAL_COMMA"}:
        raise RuntimeError("decimal_separator 须为 DECIMAL_POINT/DECIMAL_COMMA")
    if separator == "COMMA" and decimal == "DECIMAL_COMMA":
        raise RuntimeError("delimiter 与 decimal_separator 不可同时使用逗号")
    report = "#"
    if str(out_report or "").strip():
        report = validate_output_in_export_root(out_report, "out_report")
        if os.path.exists(report):
            raise RuntimeError("out_report 已存在；本工具不执行隐式覆盖")
        parent = os.path.dirname(report)
        if parent:
            os.makedirs(parent, exist_ok=True)
    result = arcpy.management.LasDatasetStatistics(
        path,
        calculation,
        report,
        summary,
        separator,
        decimal,
    )
    if report != "#" and not os.path.isfile(report):
        raise RuntimeError(f"LAS 统计报告未创建：{report}")
    payload = las_dataset_info(arcpy, path)
    payload.update(
        {
            "messages": _messages(result),
            "calculation_type": calculation,
            "source_sidecar_write": True,
        }
    )
    if report != "#":
        payload["report"] = report
        payload["report_size_bytes"] = os.path.getsize(report)
    return payload


def build_las_pyramid(
    arcpy: Any,
    las_dataset: str,
    *,
    expected_las_dataset: str,
    point_selection_method: str = "Z_MIN",
    class_code_weights: list[dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Build or update a LAS display pyramid beside an explicitly confirmed .lasd."""
    require_allow_write()
    path = _input_las_dataset(arcpy, las_dataset)
    _exact_target(expected_las_dataset, path)
    method = _required_text(point_selection_method, "point_selection_method", 64).upper()
    if method not in _PYRAMID_METHODS:
        raise RuntimeError(f"point_selection_method 须为 {sorted(_PYRAMID_METHODS)}")
    weights: list[list[int]] = []
    if len(class_code_weights or []) > 256:
        raise RuntimeError("class_code_weights 最多 256 项")
    for index, item in enumerate(class_code_weights or []):
        if not isinstance(item, dict) or set(item) != {"class_code", "weight"}:
            raise RuntimeError(
                f"class_code_weights[{index}] 必须且只可包含 class_code/weight"
            )
        class_code = int(item["class_code"])
        weight = int(item["weight"])
        if not 0 <= class_code <= 255 or not 1 <= weight <= 1000000:
            raise RuntimeError(f"class_code_weights[{index}] 数值超出允许范围")
        weights.append([class_code, weight])
    if method == "CLASS_CODE" and not weights:
        raise RuntimeError("CLASS_CODE 方法必须提供 class_code_weights")
    if method != "CLASS_CODE" and weights:
        raise RuntimeError("class_code_weights 仅适用于 CLASS_CODE 方法")
    result = arcpy.management.BuildLasDatasetPyramid(path, method, weights or "#")
    payload = las_dataset_info(arcpy, path)
    payload.update(
        {
            "messages": _messages(result),
            "point_selection_method": method,
            "source_sidecar_write": True,
        }
    )
    return payload
