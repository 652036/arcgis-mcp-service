"""Advanced, policy-constrained ArcPy raster and mosaic-dataset helpers.

The module intentionally has no MCP registration and accepts ``arcpy`` from its
caller.  Every public operation validates its paths before calling ArcPy, applies
only the raster environment keys allowlisted by :mod:`raster_runtime`, and verifies
the documented derived output after the tool returns.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any

from arcgis_pro_mcp.paths import (
    is_probably_path,
    require_allow_destructive,
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)
from arcgis_pro_mcp.raster_runtime import checked_out_extension, scoped_environment

_STATISTICS = frozenset(
    {
        "MAJORITY",
        "MAXIMUM",
        "MEAN",
        "MEDIAN",
        "MINIMUM",
        "MINORITY",
        "PERCENTILE",
        "RANGE",
        "STD",
        "SUM",
        "VARIETY",
    }
)
_PIXEL_TYPES = frozenset(
    {
        "1_BIT",
        "2_BIT",
        "4_BIT",
        "8_BIT_UNSIGNED",
        "8_BIT_SIGNED",
        "16_BIT_UNSIGNED",
        "16_BIT_SIGNED",
        "32_BIT_UNSIGNED",
        "32_BIT_SIGNED",
        "32_BIT_FLOAT",
        "64_BIT",
    }
)
_COPY_FORMATS = {
    "AVIF": "AVIF",
    "TIFF": "TIFF",
    "COG": "COG",
    "IMAGINE IMAGE": "IMAGINE Image",
    "BMP": "BMP",
    "GIF": "GIF",
    "PNG": "PNG",
    "JPEG": "JPEG",
    "JPEGXL": "JPEGXL",
    "JP2": "JP2",
    "GRID": "GRID",
    "BIL": "BIL",
    "BSQ": "BSQ",
    "BIP": "BIP",
    "ENVI": "ENVI",
    "CRF": "CRF",
    "MRF": "MRF",
    "NETCDF": "NetCDF",
    "WEBP": "WEBP",
    "ZARR": "Zarr",
}
_DATA_TYPES = frozenset(
    {
        "GENERIC",
        "ELEVATION",
        "THEMATIC",
        "PROCESSED",
        "SCIENTIFIC",
        "VECTOR_UV",
        "VECTOR_MAGDIR",
        "DATE",
        "SAR",
    }
)
_SAFE_DATASET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _enum(value: str, allowed: frozenset[str], label: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized not in allowed:
        raise RuntimeError(f"{label} 须为 {sorted(allowed)}")
    return normalized


def _canonical_enum(value: str, allowed: dict[str, str], label: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized not in allowed:
        raise RuntimeError(f"{label} 须为 {sorted(allowed)}")
    return allowed[normalized]


def _clean_text(value: str, label: str, *, maximum: int = 4096, required: bool = False) -> str:
    cleaned = (value or "").strip()
    if required and not cleaned:
        raise RuntimeError(f"{label} 不能为空")
    if len(cleaned) > maximum or "\x00" in cleaned:
        raise RuntimeError(f"{label} 无效")
    return cleaned


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{label} 必须为有限数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 必须为有限数值") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"{label} 必须为有限数值")
    return number


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


def _exists(arcpy: Any, path: Any) -> bool:
    checker = getattr(arcpy, "Exists", None)
    if not callable(checker):
        raise RuntimeError("当前 arcpy 不支持 Exists，无法核验栅格输出")
    return bool(checker(path))


def _require_existing(arcpy: Any, path: Any, label: str) -> None:
    if not _exists(arcpy, path):
        raise RuntimeError(f"{label} 不存在：{path}")


def _input(arcpy: Any, value: Any, label: str) -> Any:
    path = validate_input_path_optional(value, label)
    _require_existing(arcpy, path, label)
    return path


def _optional_input(arcpy: Any, value: Any, label: str) -> Any:
    if value in (None, "", "#"):
        return None
    return _input(arcpy, value, label)


def _prepare_new_output(arcpy: Any, output_path: str, label: str) -> str:
    require_allow_write()
    require_gp_output_root_mandatory()
    path = validate_gp_output_path(output_path, label)
    if _exists(arcpy, path):
        raise RuntimeError(f"{label} 已存在；高级栅格工具拒绝隐式覆盖：{path}")
    return path


def _verify_output(arcpy: Any, output_path: str, label: str) -> dict[str, Any]:
    if not _exists(arcpy, output_path):
        raise RuntimeError(f"{label} 未创建或不可见：{output_path}")
    return {"output_path": output_path, "exists": True, "verified": True}


def _verify_in_place(arcpy: Any, path: Any, label: str, result: Any) -> dict[str, Any]:
    _require_existing(arcpy, path, label)
    return {
        "output_path": path,
        "exists": True,
        "verified": True,
        "messages": _messages(result),
    }


def _save_raster(arcpy: Any, raster: Any, output_path: str) -> dict[str, Any]:
    saver = getattr(raster, "save", None)
    if not callable(saver):
        raise RuntimeError("Spatial Analyst 未返回可保存的 Raster 对象")
    saver(output_path)
    return _verify_output(arcpy, output_path, "output_raster")


def _raster_or_constant(arcpy: Any, value: Any, label: str) -> Any:
    if isinstance(value, bool):
        raise RuntimeError(f"{label} 必须为栅格路径或有限数值")
    if isinstance(value, (int, float)):
        number = _finite(value, label)
        return int(number) if isinstance(value, int) else number
    return _input(arcpy, value, label)


def _optional_raster_or_constant(arcpy: Any, value: Any, label: str) -> Any:
    if value in (None, "", "#"):
        return None
    return _raster_or_constant(arcpy, value, label)


def _optional_number(value: Any, label: str, *, minimum: float | None = None) -> Any:
    if value in (None, "", "#"):
        return None
    number = _finite(value, label)
    if minimum is not None and number < minimum:
        raise RuntimeError(f"{label} 必须大于或等于 {minimum}")
    return number


def _gp_optional(value: Any) -> Any:
    return "#" if value is None else value


def _number_or_field(
    value: Any,
    label: str,
    *,
    minimum: float,
    strict: bool = False,
) -> Any:
    if value in (None, "", "#"):
        return None
    if isinstance(value, str):
        return _clean_text(value, label, maximum=160, required=True)
    number = _finite(value, label)
    if number < minimum or (strict and number == minimum):
        comparator = "大于" if strict else "大于或等于"
        raise RuntimeError(f"{label} 必须{comparator} {minimum}")
    return number


def calculate_statistics(
    arcpy: Any,
    in_raster_dataset: str,
    x_skip_factor: int = 1,
    y_skip_factor: int = 1,
    ignore_values: list[int] | None = None,
    skip_existing: str = "OVERWRITE",
    area_of_interest: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the documented ``management.CalculateStatistics`` in place."""
    require_allow_write()
    raster = _input(arcpy, in_raster_dataset, "in_raster_dataset")
    x_skip = int(x_skip_factor)
    y_skip = int(y_skip_factor)
    if x_skip < 1 or y_skip < 1:
        raise RuntimeError("x_skip_factor 和 y_skip_factor 必须大于 0")
    ignored = list(ignore_values or [])
    if len(ignored) > 256 or any(isinstance(value, bool) or not isinstance(value, int) for value in ignored):
        raise RuntimeError("ignore_values 最多包含 256 个整数")
    existing = _enum(skip_existing, frozenset({"OVERWRITE", "SKIP_EXISTING"}), "skip_existing")
    aoi = _optional_input(arcpy, area_of_interest, "area_of_interest")
    with scoped_environment(arcpy, environment):
        result = arcpy.management.CalculateStatistics(
            raster,
            x_skip,
            y_skip,
            ignored or "#",
            existing,
            aoi or "#",
        )
    payload = _verify_in_place(arcpy, raster, "in_raster_dataset", result)
    payload["operation"] = "CalculateStatistics"
    return payload


def build_pyramids(
    arcpy: Any,
    in_raster_dataset: str,
    pyramid_level: int = -1,
    skip_first: bool = False,
    resample_technique: str = "NEAREST",
    compression_type: str = "DEFAULT",
    compression_quality: int = 75,
    skip_existing: str = "OVERWRITE",
    confirm_delete_pyramids: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build pyramids, or delete them only after a destructive exact-path echo."""
    require_allow_write()
    level = int(pyramid_level)
    if level < -1 or level > 29:
        raise RuntimeError("pyramid_level 须为 -1、0 或 1–29")
    if level == 0:
        require_allow_destructive()
        if confirm_delete_pyramids != in_raster_dataset:
            raise RuntimeError("删除金字塔时 confirm_delete_pyramids 必须精确回显 in_raster_dataset")
    raster = _input(arcpy, in_raster_dataset, "in_raster_dataset")
    resampling = _enum(
        resample_technique,
        frozenset({"NEAREST", "BILINEAR", "CUBIC"}),
        "resample_technique",
    )
    compression = _enum(
        compression_type,
        frozenset({"DEFAULT", "LZ77", "JPEG", "JPEG_YCBCR", "NONE"}),
        "compression_type",
    )
    if compression == "JPEG_YCBCR":
        compression = "JPEG_YCbCr"
    quality = int(compression_quality)
    if quality < 0 or quality > 100:
        raise RuntimeError("compression_quality 须为 0–100")
    existing = _enum(skip_existing, frozenset({"OVERWRITE", "SKIP_EXISTING"}), "skip_existing")
    with scoped_environment(arcpy, environment):
        result = arcpy.management.BuildPyramids(
            raster,
            level,
            "SKIP_FIRST" if skip_first else "NONE",
            resampling,
            compression,
            quality,
            existing,
        )
    payload = _verify_in_place(arcpy, raster, "in_raster_dataset", result)
    payload.update({"operation": "BuildPyramids", "pyramid_level": level})
    return payload


def set_raster_nodata(
    arcpy: Any,
    in_raster: str,
    nodata_values: list[list[Any]],
    confirm_raster_path: str,
    data_type: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set per-band NoData values with an exact target echo."""
    require_allow_write()
    if confirm_raster_path != in_raster:
        raise RuntimeError("confirm_raster_path 必须精确回显 in_raster")
    raster = _input(arcpy, in_raster, "in_raster")
    if not isinstance(nodata_values, list) or not nodata_values or len(nodata_values) > 1024:
        raise RuntimeError("nodata_values 必须是 1–1024 行的 [band_index, nodata_value] 列表")
    rows: list[list[Any]] = []
    bands: set[int] = set()
    for index, row in enumerate(nodata_values):
        if not isinstance(row, list) or len(row) != 2:
            raise RuntimeError(f"nodata_values[{index}] 必须为 [band_index, nodata_value]")
        band = int(row[0])
        if band < 1 or band in bands:
            raise RuntimeError("band_index 必须大于 0 且不能重复")
        bands.add(band)
        value = row[1]
        if isinstance(value, str):
            value = _clean_text(value, f"nodata_values[{index}][1]", maximum=128, required=True)
        else:
            value = _finite(value, f"nodata_values[{index}][1]")
        rows.append([band, value])
    raster_type = _enum(data_type, _DATA_TYPES, "data_type") if data_type else "#"
    with scoped_environment(arcpy, environment):
        result = arcpy.management.SetRasterProperties(raster, raster_type, "#", "#", rows)
    payload = _verify_in_place(arcpy, raster, "in_raster", result)
    payload.update({"operation": "SetRasterProperties", "nodata": rows})
    return payload


def copy_raster(
    arcpy: Any,
    in_raster: str,
    out_rasterdataset: str,
    background_value: float | None = None,
    nodata_value: str | float | int | None = None,
    onebit_to_eightbit: bool = False,
    colormap_to_rgb: bool = False,
    pixel_type: str = "",
    scale_pixel_value: bool = False,
    rgb_to_colormap: bool = False,
    output_format: str = "",
    apply_transform: bool = False,
    process_as_multidimensional: bool = False,
    build_multidimensional_transpose: bool = False,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy a raster to a new, root-constrained output dataset."""
    source = _input(arcpy, in_raster, "in_raster")
    output = _prepare_new_output(arcpy, out_rasterdataset, "out_rasterdataset")
    background = "#" if background_value is None else _finite(background_value, "background_value")
    if nodata_value is None:
        nodata: Any = "#"
    elif isinstance(nodata_value, str):
        nodata = _clean_text(nodata_value, "nodata_value", maximum=128, required=True)
    else:
        nodata = _finite(nodata_value, "nodata_value")
    pixel = _enum(pixel_type, _PIXEL_TYPES, "pixel_type") if pixel_type else "#"
    raster_format = _canonical_enum(output_format, _COPY_FORMATS, "output_format") if output_format else "#"
    if build_multidimensional_transpose and not process_as_multidimensional:
        raise RuntimeError("build_multidimensional_transpose=true 需要 process_as_multidimensional=true")
    with scoped_environment(arcpy, environment):
        result = arcpy.management.CopyRaster(
            source,
            output,
            "#",
            background,
            nodata,
            "OneBitTo8Bit" if onebit_to_eightbit else "NONE",
            "ColormapToRGB" if colormap_to_rgb else "NONE",
            pixel,
            "ScalePixelValue" if scale_pixel_value else "NONE",
            "RGBToColormap" if rgb_to_colormap else "NONE",
            raster_format,
            "Transform" if apply_transform else "NONE",
            "ALL_SLICES" if process_as_multidimensional else "CURRENT_SLICE",
            "TRANSPOSE" if build_multidimensional_transpose else "NO_TRANSPOSE",
        )
    payload = _verify_output(arcpy, output, "out_rasterdataset")
    payload.update({"operation": "CopyRaster", "messages": _messages(result)})
    return payload


def _neighborhood(arcpy: Any, specification: dict[str, Any] | None) -> Any:
    spec = dict(specification or {"type": "RECTANGLE", "width": 3, "height": 3, "units": "CELL"})
    kind = _enum(
        str(spec.pop("type", "RECTANGLE")),
        frozenset({"ANNULUS", "CIRCLE", "RECTANGLE", "WEDGE", "IRREGULAR", "WEIGHT"}),
        "neighborhood.type",
    )
    sa = arcpy.sa
    if kind in {"IRREGULAR", "WEIGHT"}:
        unknown = set(spec) - {"kernel_file"}
        if unknown:
            raise RuntimeError(f"不支持的 neighborhood 参数：{sorted(unknown)}")
        kernel = _input(arcpy, spec.get("kernel_file"), "neighborhood.kernel_file")
        constructor = sa.NbrIrregular if kind == "IRREGULAR" else sa.NbrWeight
        return constructor(kernel)
    units = _enum(str(spec.pop("units", "CELL")), frozenset({"CELL", "MAP"}), "neighborhood.units")
    if kind == "RECTANGLE":
        unknown = set(spec) - {"width", "height"}
        width = int(spec.get("width", 3))
        height = int(spec.get("height", 3))
        if unknown or not (1 <= width <= 4096 and 1 <= height <= 4096):
            raise RuntimeError("RECTANGLE 只接受 1–4096 的 width、height")
        return sa.NbrRectangle(width, height, units)
    if kind == "CIRCLE":
        unknown = set(spec) - {"radius"}
        radius = _finite(spec.get("radius", 3), "neighborhood.radius")
        if unknown or not (0 < radius <= 2047):
            raise RuntimeError("CIRCLE 只接受 0–2047 的 radius")
        return sa.NbrCircle(radius, units)
    if kind == "ANNULUS":
        unknown = set(spec) - {"inner_radius", "outer_radius"}
        inner = _finite(spec.get("inner_radius", 1), "neighborhood.inner_radius")
        outer = _finite(spec.get("outer_radius", 3), "neighborhood.outer_radius")
        if unknown or not (0 < inner < outer <= 2047):
            raise RuntimeError("ANNULUS 需要 0 < inner_radius < outer_radius <= 2047")
        return sa.NbrAnnulus(inner, outer, units)
    unknown = set(spec) - {"radius", "start_angle", "end_angle"}
    radius = _finite(spec.get("radius", 3), "neighborhood.radius")
    start = _finite(spec.get("start_angle", 0), "neighborhood.start_angle")
    end = _finite(spec.get("end_angle", 90), "neighborhood.end_angle")
    if unknown or not (0 < radius <= 2047):
        raise RuntimeError("WEDGE 只接受 radius、start_angle、end_angle")
    # arcpy.sa exports ParameterClasses.NbrWedge(radius, startAngle, endAngle, units).
    return sa.NbrWedge(radius, start, end, units)


def focal_statistics(
    arcpy: Any,
    in_raster: str,
    out_raster: str,
    neighborhood: dict[str, Any] | None = None,
    statistics_type: str = "MEAN",
    ignore_nodata: str = "DATA",
    percentile_value: float = 90,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = _input(arcpy, in_raster, "in_raster")
    output = _prepare_new_output(arcpy, out_raster, "out_raster")
    statistic = _enum(statistics_type, _STATISTICS, "statistics_type")
    nodata = _enum(ignore_nodata, frozenset({"DATA", "NODATA"}), "ignore_nodata")
    percentile = _finite(percentile_value, "percentile_value")
    if not 0 <= percentile <= 100:
        raise RuntimeError("percentile_value 须为 0–100")
    nbr = _neighborhood(arcpy, neighborhood)
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.FocalStatistics(source, nbr, statistic, nodata, percentile)
        payload = _save_raster(arcpy, result, output)
    payload["operation"] = "FocalStatistics"
    return payload


def cell_statistics(
    arcpy: Any,
    in_rasters_or_constants: list[Any],
    out_raster: str,
    statistics_type: str = "MEAN",
    ignore_nodata: str = "DATA",
    process_as_multiband: str = "SINGLE_BAND",
    percentile_value: float = 90,
    percentile_interpolation_type: str = "AUTO_DETECT",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(in_rasters_or_constants, list) or not 1 <= len(in_rasters_or_constants) <= 256:
        raise RuntimeError("in_rasters_or_constants 必须包含 1–256 项")
    inputs = [
        _raster_or_constant(arcpy, value, f"in_rasters_or_constants[{index}]")
        for index, value in enumerate(in_rasters_or_constants)
    ]
    if all(isinstance(value, (int, float)) for value in inputs):
        supplied_env = environment or {}
        if "cell_size" not in supplied_env or "extent" not in supplied_env:
            raise RuntimeError("仅使用常量时必须在 environment 中同时提供 cell_size 和 extent")
    output = _prepare_new_output(arcpy, out_raster, "out_raster")
    statistic = _enum(statistics_type, _STATISTICS, "statistics_type")
    nodata = _enum(ignore_nodata, frozenset({"DATA", "NODATA"}), "ignore_nodata")
    multiband = _enum(
        process_as_multiband,
        frozenset({"SINGLE_BAND", "MULTI_BAND"}),
        "process_as_multiband",
    )
    percentile = _finite(percentile_value, "percentile_value")
    if not 0 <= percentile <= 100:
        raise RuntimeError("percentile_value 须为 0–100")
    interpolation = _enum(
        percentile_interpolation_type,
        frozenset({"AUTO_DETECT", "NEAREST", "LINEAR"}),
        "percentile_interpolation_type",
    )
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.CellStatistics(inputs, statistic, nodata, multiband, percentile, interpolation)
        payload = _save_raster(arcpy, result, output)
    payload["operation"] = "CellStatistics"
    return payload


def conditional_con(
    arcpy: Any,
    in_conditional_raster: str,
    in_true_raster_or_constant: Any,
    out_raster: str,
    in_false_raster_or_constant: Any = None,
    where_clause: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conditional = _input(arcpy, in_conditional_raster, "in_conditional_raster")
    true_value = _raster_or_constant(arcpy, in_true_raster_or_constant, "in_true_raster_or_constant")
    false_value = _optional_raster_or_constant(
        arcpy,
        in_false_raster_or_constant,
        "in_false_raster_or_constant",
    )
    output = _prepare_new_output(arcpy, out_raster, "out_raster")
    clause = _clean_text(where_clause, "where_clause")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.Con(conditional, true_value, _gp_optional(false_value), clause or "#")
        payload = _save_raster(arcpy, result, output)
    payload["operation"] = "Con"
    return payload


def set_null(
    arcpy: Any,
    in_conditional_raster: str,
    in_false_raster_or_constant: Any,
    out_raster: str,
    where_clause: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conditional = _input(arcpy, in_conditional_raster, "in_conditional_raster")
    false_value = _raster_or_constant(arcpy, in_false_raster_or_constant, "in_false_raster_or_constant")
    output = _prepare_new_output(arcpy, out_raster, "out_raster")
    clause = _clean_text(where_clause, "where_clause")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.SetNull(conditional, false_value, clause or "#")
        payload = _save_raster(arcpy, result, output)
    payload["operation"] = "SetNull"
    return payload


def euclidean_distance(
    arcpy: Any,
    in_source_data: str,
    out_distance_raster: str,
    maximum_distance: float | None = None,
    cell_size: float | str | None = None,
    out_direction_raster: str = "",
    distance_method: str = "PLANAR",
    in_barrier_data: str = "",
    out_back_direction_raster: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = _input(arcpy, in_source_data, "in_source_data")
    output = _prepare_new_output(arcpy, out_distance_raster, "out_distance_raster")
    maximum = _optional_number(maximum_distance, "maximum_distance", minimum=0)
    if maximum == 0:
        raise RuntimeError("maximum_distance 必须大于 0")
    if isinstance(cell_size, str) and cell_size not in ("", "#"):
        resolved_cell_size: Any = _input(arcpy, cell_size, "cell_size")
    else:
        resolved_cell_size = _optional_number(cell_size, "cell_size", minimum=0)
        if resolved_cell_size == 0:
            raise RuntimeError("cell_size 必须大于 0")
    direction = (
        _prepare_new_output(arcpy, out_direction_raster, "out_direction_raster")
        if out_direction_raster
        else None
    )
    back_direction = (
        _prepare_new_output(arcpy, out_back_direction_raster, "out_back_direction_raster")
        if out_back_direction_raster
        else None
    )
    method = _enum(distance_method, frozenset({"PLANAR", "GEODESIC"}), "distance_method")
    barrier = _optional_input(arcpy, in_barrier_data, "in_barrier_data")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.EucDistance(
            source,
            _gp_optional(maximum),
            _gp_optional(resolved_cell_size),
            _gp_optional(direction),
            method,
            _gp_optional(barrier),
            _gp_optional(back_direction),
        )
        payload = _save_raster(arcpy, result, output)
    for key, path in (("direction_raster", direction), ("back_direction_raster", back_direction)):
        if path:
            _verify_output(arcpy, path, key)
            payload[key] = path
    payload.update({"operation": "EucDistance", "deprecated": True})
    return payload


def distance_accumulation(
    arcpy: Any,
    in_source_data: str,
    out_distance_accumulation_raster: str,
    in_barrier_data: str = "",
    in_surface_raster: str = "",
    in_cost_raster: str = "",
    in_vertical_raster: str = "",
    in_horizontal_raster: str = "",
    out_back_direction_raster: str = "",
    out_source_direction_raster: str = "",
    out_source_location_raster: str = "",
    source_initial_accumulation: float | str | None = None,
    source_maximum_accumulation: float | str | None = None,
    source_cost_multiplier: float | str | None = None,
    source_direction: str = "FROM_SOURCE",
    distance_method: str = "PLANAR",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = _input(arcpy, in_source_data, "in_source_data")
    output = _prepare_new_output(
        arcpy,
        out_distance_accumulation_raster,
        "out_distance_accumulation_raster",
    )
    barrier = _optional_input(arcpy, in_barrier_data, "in_barrier_data")
    surface = _optional_input(arcpy, in_surface_raster, "in_surface_raster")
    cost = _optional_input(arcpy, in_cost_raster, "in_cost_raster")
    vertical = _optional_input(arcpy, in_vertical_raster, "in_vertical_raster")
    horizontal = _optional_input(arcpy, in_horizontal_raster, "in_horizontal_raster")
    back = (
        _prepare_new_output(arcpy, out_back_direction_raster, "out_back_direction_raster")
        if out_back_direction_raster
        else None
    )
    source_direction_output = (
        _prepare_new_output(arcpy, out_source_direction_raster, "out_source_direction_raster")
        if out_source_direction_raster
        else None
    )
    source_location = (
        _prepare_new_output(arcpy, out_source_location_raster, "out_source_location_raster")
        if out_source_location_raster
        else None
    )
    initial = _number_or_field(source_initial_accumulation, "source_initial_accumulation", minimum=0)
    maximum = _number_or_field(
        source_maximum_accumulation,
        "source_maximum_accumulation",
        minimum=0,
        strict=True,
    )
    multiplier = _number_or_field(
        source_cost_multiplier,
        "source_cost_multiplier",
        minimum=0,
        strict=True,
    )
    travel_direction = _enum(
        source_direction,
        frozenset({"FROM_SOURCE", "TO_SOURCE"}),
        "source_direction",
    )
    method = _enum(distance_method, frozenset({"PLANAR", "GEODESIC"}), "distance_method")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.DistanceAccumulation(
            source,
            _gp_optional(barrier),
            _gp_optional(surface),
            _gp_optional(cost),
            _gp_optional(vertical),
            "#",
            _gp_optional(horizontal),
            "#",
            _gp_optional(back),
            _gp_optional(source_direction_output),
            _gp_optional(source_location),
            _gp_optional(initial),
            _gp_optional(maximum),
            _gp_optional(multiplier),
            travel_direction,
            method,
        )
        payload = _save_raster(arcpy, result, output)
    secondary = {
        "back_direction_raster": back,
        "source_direction_raster": source_direction_output,
        "source_location_raster": source_location,
    }
    for key, path in secondary.items():
        if path:
            _verify_output(arcpy, path, key)
            payload[key] = path
    payload["operation"] = "DistanceAccumulation"
    return payload


def optimal_path_as_line(
    arcpy: Any,
    in_destination_data: str,
    in_distance_accumulation_raster: str,
    in_back_direction_raster: str,
    out_polyline_features: str,
    destination_field: str = "",
    path_type: str = "EACH_ZONE",
    create_network_paths: str = "DESTINATIONS_TO_SOURCES",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    destination = _input(arcpy, in_destination_data, "in_destination_data")
    accumulation = _input(
        arcpy,
        in_distance_accumulation_raster,
        "in_distance_accumulation_raster",
    )
    back = _input(arcpy, in_back_direction_raster, "in_back_direction_raster")
    output = _prepare_new_output(arcpy, out_polyline_features, "out_polyline_features")
    field = _clean_text(destination_field, "destination_field", maximum=160)
    path = _enum(path_type, frozenset({"EACH_ZONE", "BEST_SINGLE", "EACH_CELL"}), "path_type")
    network = _enum(
        create_network_paths,
        frozenset({"NETWORK_PATHS", "DESTINATIONS_TO_SOURCES"}),
        "create_network_paths",
    )
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.OptimalPathAsLine(
            destination,
            accumulation,
            back,
            output,
            field or "#",
            path,
            network,
        )
    payload = _verify_output(arcpy, output, "out_polyline_features")
    payload.update({"operation": "OptimalPathAsLine", "messages": _messages(result)})
    return payload


def stream_order(
    arcpy: Any,
    in_stream_raster: str,
    in_flow_direction_raster: str,
    out_raster: str,
    order_method: str = "STRAHLER",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stream = _input(arcpy, in_stream_raster, "in_stream_raster")
    flow = _input(arcpy, in_flow_direction_raster, "in_flow_direction_raster")
    output = _prepare_new_output(arcpy, out_raster, "out_raster")
    method = _enum(order_method, frozenset({"STRAHLER", "SHREVE"}), "order_method")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.StreamOrder(stream, flow, method)
        payload = _save_raster(arcpy, result, output)
    payload["operation"] = "StreamOrder"
    return payload


def stream_to_feature(
    arcpy: Any,
    in_stream_raster: str,
    in_flow_direction_raster: str,
    out_polyline_features: str,
    simplify: bool = True,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stream = _input(arcpy, in_stream_raster, "in_stream_raster")
    flow = _input(arcpy, in_flow_direction_raster, "in_flow_direction_raster")
    output = _prepare_new_output(arcpy, out_polyline_features, "out_polyline_features")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.StreamToFeature(
            stream,
            flow,
            output,
            "SIMPLIFY" if simplify else "NO_SIMPLIFY",
        )
    payload = _verify_output(arcpy, output, "out_polyline_features")
    payload.update({"operation": "StreamToFeature", "messages": _messages(result)})
    return payload


def basin(
    arcpy: Any,
    in_flow_direction_raster: str,
    out_raster: str,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    flow = _input(arcpy, in_flow_direction_raster, "in_flow_direction_raster")
    output = _prepare_new_output(arcpy, out_raster, "out_raster")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        # The ArcGIS Pro 3.6 arcpy.sa wrapper has the documented one-input signature.
        result = arcpy.sa.Basin(flow)
        payload = _save_raster(arcpy, result, output)
    payload["operation"] = "Basin"
    return payload


def _mosaic(arcpy: Any, in_mosaic_dataset: str) -> Any:
    require_allow_write()
    return _input(arcpy, in_mosaic_dataset, "in_mosaic_dataset")


def _spatial_reference(arcpy: Any, value: int | str) -> Any:
    if isinstance(value, bool):
        raise RuntimeError("coordinate_system 必须为 WKID、WKT 或 .prj 路径")
    if isinstance(value, int):
        if value <= 0:
            raise RuntimeError("coordinate_system WKID 必须大于 0")
        factory = getattr(arcpy, "SpatialReference", None)
        return factory(value) if callable(factory) else value
    text = _clean_text(value, "coordinate_system", maximum=32768, required=True)
    if is_probably_path(text):
        return _input(arcpy, text, "coordinate_system")
    return text


def _try_count(arcpy: Any, dataset: Any) -> int | None:
    getter = getattr(getattr(arcpy, "management", None), "GetCount", None)
    if not callable(getter):
        return None
    try:
        return int(str(getter(dataset).getOutput(0)))
    except Exception:  # noqa: BLE001
        return None


def create_mosaic_dataset(
    arcpy: Any,
    in_workspace: str,
    in_mosaicdataset_name: str,
    coordinate_system: int | str,
    num_bands: int | None = None,
    pixel_type: str = "",
    product_definition: str = "NONE",
    product_band_definitions: list[list[Any]] | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_allow_write()
    require_gp_output_root_mandatory()
    workspace = validate_gp_output_path(in_workspace, "in_workspace")
    _require_existing(arcpy, workspace, "in_workspace")
    name = (in_mosaicdataset_name or "").strip()
    if not _SAFE_DATASET_NAME.fullmatch(name):
        raise RuntimeError("in_mosaicdataset_name 必须是安全的 geodatabase 数据集名称")
    output = validate_gp_output_path(os.path.join(workspace, name), "out_mosaic_dataset")
    if _exists(arcpy, output):
        raise RuntimeError(f"out_mosaic_dataset 已存在：{output}")
    spatial_reference = _spatial_reference(arcpy, coordinate_system)
    bands: Any = "#"
    if num_bands is not None:
        bands = int(num_bands)
        if bands < 1 or bands > 65535:
            raise RuntimeError("num_bands 须为 1–65535")
    pixel = _enum(pixel_type, _PIXEL_TYPES, "pixel_type") if pixel_type else "#"
    product = _clean_text(product_definition, "product_definition", maximum=160, required=True)
    definitions: Any = product_band_definitions or "#"
    if product_band_definitions is not None:
        if not isinstance(product_band_definitions, list) or len(product_band_definitions) > 1024:
            raise RuntimeError("product_band_definitions 必须是至多 1024 行的列表")
        for index, row in enumerate(product_band_definitions):
            if not isinstance(row, list) or len(row) != 3:
                raise RuntimeError(f"product_band_definitions[{index}] 必须含 3 列")
    with scoped_environment(arcpy, environment):
        result = arcpy.management.CreateMosaicDataset(
            workspace,
            name,
            spatial_reference,
            bands,
            pixel,
            product,
            definitions,
        )
    payload = _verify_output(arcpy, output, "out_mosaic_dataset")
    payload.update({"operation": "CreateMosaicDataset", "messages": _messages(result)})
    return payload


def add_rasters_to_mosaic_dataset(
    arcpy: Any,
    in_mosaic_dataset: str,
    input_paths: list[str],
    raster_type: str = "Raster Dataset",
    update_cellsize_ranges: bool = True,
    update_boundary: bool = True,
    update_overviews: bool = False,
    include_subfolders: bool = False,
    duplicate_items_action: str = "EXCLUDE_DUPLICATES",
    build_pyramids_for_sources: bool = False,
    calculate_statistics_for_sources: bool = False,
    build_thumbnails: bool = False,
    filter_expression: str = "",
    operation_description: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    duplicates = _enum(
        duplicate_items_action,
        frozenset({"EXCLUDE_DUPLICATES", "ALLOW_DUPLICATES", "OVERWRITE_DUPLICATES"}),
        "duplicate_items_action",
    )
    if duplicates == "OVERWRITE_DUPLICATES":
        require_allow_destructive()
    mosaic = _mosaic(arcpy, in_mosaic_dataset)
    if not isinstance(input_paths, list) or not 1 <= len(input_paths) <= 1024:
        raise RuntimeError("input_paths 必须包含 1–1024 个路径")
    inputs = [_input(arcpy, path, f"input_paths[{index}]") for index, path in enumerate(input_paths)]
    raster_kind = _clean_text(raster_type, "raster_type", maximum=256, required=True)
    if is_probably_path(raster_kind):
        raster_kind = _input(arcpy, raster_kind, "raster_type")
    filter_value = _clean_text(filter_expression, "filter_expression", maximum=2048)
    description = _clean_text(operation_description, "operation_description", maximum=1024)
    before_count = _try_count(arcpy, mosaic)
    with scoped_environment(arcpy, environment):
        result = arcpy.management.AddRastersToMosaicDataset(
            mosaic,
            raster_kind,
            inputs,
            "UPDATE_CELL_SIZES" if update_cellsize_ranges else "NO_CELL_SIZES",
            "UPDATE_BOUNDARY" if update_boundary else "NO_BOUNDARY",
            "UPDATE_OVERVIEWS" if update_overviews else "NO_OVERVIEWS",
            "#",
            "#",
            "#",
            "#",
            filter_value or "#",
            "SUBFOLDERS" if include_subfolders else "NO_SUBFOLDERS",
            duplicates,
            "BUILD_PYRAMIDS" if build_pyramids_for_sources else "NO_PYRAMIDS",
            "CALCULATE_STATISTICS" if calculate_statistics_for_sources else "NO_STATISTICS",
            "BUILD_THUMBNAILS" if build_thumbnails else "NO_THUMBNAILS",
            description or "#",
            "#",
            "NO_STATISTICS",
            "#",
            "NO_PIXEL_CACHE",
            "#",
        )
    payload = _verify_in_place(arcpy, mosaic, "in_mosaic_dataset", result)
    after_count = _try_count(arcpy, mosaic)
    payload.update({"operation": "AddRastersToMosaicDataset", "before_count": before_count, "after_count": after_count})
    if before_count is not None and after_count is not None:
        payload["added_count"] = after_count - before_count
    return payload


def build_mosaic_footprints(
    arcpy: Any,
    in_mosaic_dataset: str,
    where_clause: str = "",
    reset_footprint: str = "RADIOMETRY",
    min_data_value: float | None = None,
    max_data_value: float | None = None,
    approx_num_vertices: int | None = None,
    shrink_distance: float | None = None,
    maintain_edges: bool = False,
    skip_derived_images: bool = True,
    update_boundary: bool = True,
    request_size: int | None = None,
    min_region_size: int | None = None,
    simplification_method: str = "NONE",
    edge_tolerance: float | None = None,
    max_sliver_size: int | None = None,
    min_thinness_ratio: float | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mosaic = _mosaic(arcpy, in_mosaic_dataset)
    clause = _clean_text(where_clause, "where_clause")
    reset = _enum(
        reset_footprint,
        frozenset({"RADIOMETRY", "GEOMETRY", "COPY_TO_SIBLING", "NONE"}),
        "reset_footprint",
    )
    simplify = _enum(
        simplification_method,
        frozenset({"NONE", "CONVEX_HULL", "ENVELOPE"}),
        "simplification_method",
    )
    if approx_num_vertices is not None and not (
        int(approx_num_vertices) == -1 or 4 <= int(approx_num_vertices) <= 10_000
    ):
        raise RuntimeError("approx_num_vertices 须为 -1 或 4–10000")
    thinness = _optional_number(min_thinness_ratio, "min_thinness_ratio", minimum=0)
    if thinness is not None and thinness > 1:
        raise RuntimeError("min_thinness_ratio 须为 0–1")
    with scoped_environment(arcpy, environment):
        result = arcpy.management.BuildFootprints(
            mosaic,
            clause or "#",
            reset,
            _gp_optional(_optional_number(min_data_value, "min_data_value")),
            _gp_optional(_optional_number(max_data_value, "max_data_value")),
            "#" if approx_num_vertices is None else int(approx_num_vertices),
            _gp_optional(_optional_number(shrink_distance, "shrink_distance")),
            "MAINTAIN_EDGES" if maintain_edges else "NO_MAINTAIN_EDGES",
            "SKIP_DERIVED_IMAGES" if skip_derived_images else "NO_SKIP_DERIVED_IMAGES",
            "UPDATE_BOUNDARY" if update_boundary else "NO_BOUNDARY",
            "#" if request_size is None else int(request_size),
            "#" if min_region_size is None else int(min_region_size),
            simplify,
            _gp_optional(_optional_number(edge_tolerance, "edge_tolerance")),
            "#" if max_sliver_size is None else int(max_sliver_size),
            _gp_optional(thinness),
        )
    payload = _verify_in_place(arcpy, mosaic, "in_mosaic_dataset", result)
    payload["operation"] = "BuildFootprints"
    return payload


def build_mosaic_overviews(
    arcpy: Any,
    in_mosaic_dataset: str,
    where_clause: str = "",
    define_missing_tiles: bool = True,
    generate_overviews: bool = True,
    generate_missing_images: bool = True,
    regenerate_stale_images: bool = True,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mosaic = _mosaic(arcpy, in_mosaic_dataset)
    clause = _clean_text(where_clause, "where_clause")
    with scoped_environment(arcpy, environment):
        result = arcpy.management.BuildOverviews(
            mosaic,
            clause or "#",
            "DEFINE_MISSING_TILES" if define_missing_tiles else "NO_DEFINE_MISSING_TILES",
            "GENERATE_OVERVIEWS" if generate_overviews else "NO_GENERATE_OVERVIEWS",
            "GENERATE_MISSING_IMAGES" if generate_missing_images else "IGNORE_MISSING_IMAGES",
            "REGENERATE_STALE_IMAGES" if regenerate_stale_images else "IGNORE_STALE_IMAGES",
        )
    payload = _verify_in_place(arcpy, mosaic, "in_mosaic_dataset", result)
    payload["operation"] = "BuildOverviews"
    return payload


def _matching_oid_count(arcpy: Any, mosaic: Any, where_clause: str) -> int:
    da = getattr(arcpy, "da", None)
    cursor_factory = getattr(da, "SearchCursor", None)
    if not callable(cursor_factory):
        raise RuntimeError("当前 arcpy 不支持 da.SearchCursor，无法执行删除前精确计数")
    try:
        with cursor_factory(mosaic, ["OID@"], where_clause=where_clause) as rows:
            return sum(1 for _row in rows)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法按 where_clause 核验 mosaic item 数量：{str(exc)[:300]}") from exc


def remove_rasters_from_mosaic_dataset(
    arcpy: Any,
    in_mosaic_dataset: str,
    where_clause: str,
    expected_item_count: int,
    confirm_mosaic_dataset: str,
    confirm_where_clause: str,
    update_boundary: bool = True,
    mark_overview_items: bool = True,
    delete_overview_images: bool = True,
    delete_item_cache: bool = True,
    update_cellsize_ranges: bool = True,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Remove exactly the queried mosaic items after count and exact-echo checks."""
    require_allow_destructive()
    if confirm_mosaic_dataset != in_mosaic_dataset:
        raise RuntimeError("confirm_mosaic_dataset 必须精确回显 in_mosaic_dataset")
    if confirm_where_clause != where_clause:
        raise RuntimeError("confirm_where_clause 必须精确回显 where_clause")
    clause = _clean_text(where_clause, "where_clause", required=True)
    if clause == "#":
        raise RuntimeError("where_clause 必须是显式 SQL 表达式，不能为 #")
    expected = int(expected_item_count)
    if expected < 1:
        raise RuntimeError("expected_item_count 必须大于 0")
    mosaic = _input(arcpy, in_mosaic_dataset, "in_mosaic_dataset")
    before_total = _try_count(arcpy, mosaic)
    matched = _matching_oid_count(arcpy, mosaic, clause)
    if matched != expected:
        raise RuntimeError(f"expected_item_count={expected}，实际命中 {matched} 项；未删除")
    with scoped_environment(arcpy, environment):
        result = arcpy.management.RemoveRastersFromMosaicDataset(
            mosaic,
            clause,
            "UPDATE_BOUNDARY" if update_boundary else "NO_BOUNDARY",
            "MARK_OVERVIEW_ITEMS" if mark_overview_items else "NO_MARK_OVERVIEW_ITEMS",
            "DELETE_OVERVIEW_IMAGES" if delete_overview_images else "NO_DELETE_OVERVIEW_IMAGES",
            "DELETE_ITEM_CACHE" if delete_item_cache else "NO_DELETE_ITEM_CACHE",
            "REMOVE_MOSAICDATASET_ITEMS",
            "UPDATE_CELL_SIZES" if update_cellsize_ranges else "NO_CELL_SIZES",
        )
    _require_existing(arcpy, mosaic, "in_mosaic_dataset")
    remaining_matches = _matching_oid_count(arcpy, mosaic, clause)
    if remaining_matches != 0:
        raise RuntimeError(
            f"RemoveRastersFromMosaicDataset 已返回，但查询仍命中 {remaining_matches} 项；结果未知"
        )
    after_total = _try_count(arcpy, mosaic)
    return {
        "operation": "RemoveRastersFromMosaicDataset",
        "output_path": mosaic,
        "exists": True,
        "verified": True,
        "removed_count": matched,
        "remaining_matching_count": remaining_matches,
        "before_count": before_total,
        "after_count": after_total,
        "messages": _messages(result),
    }
