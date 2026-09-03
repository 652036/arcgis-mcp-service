"""ArcGIS extension, scoped environment, raster inspection, and hydrology helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from arcgis_pro_mcp.paths import (
    is_probably_path,
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)

_DEFAULT_EXTENSIONS = ("Spatial", "3D", "ImageAnalyst", "Network")
_ENV_KEYS = {
    "snap_raster": "snapRaster",
    "mask": "mask",
    "extent": "extent",
    "cell_size": "cellSize",
    "output_coordinate_system": "outputCoordinateSystem",
    "geographic_transformations": "geographicTransformations",
    "parallel_processing_factor": "parallelProcessingFactor",
}
_PATH_ENV_KEYS = frozenset({"snap_raster", "mask"})
_FLOW_DIRECTION_TYPES = frozenset({"D8", "MFD", "DINF"})
_FLOW_ACCUMULATION_TYPES = frozenset({"FLOAT", "INTEGER", "DOUBLE"})


def extension_status(
    arcpy: Any,
    extension_names: list[str] | tuple[str, ...] | None = None,
) -> dict[str, str]:
    """Return license availability without checking out any extension."""
    checker = getattr(arcpy, "CheckExtension", None)
    if not callable(checker):
        raise RuntimeError("当前 arcpy 不支持 CheckExtension")
    names = extension_names or _DEFAULT_EXTENSIONS
    if not names or len(names) > 32:
        raise RuntimeError("extension_names 数量必须为 1–32")
    result: dict[str, str] = {}
    for raw_name in names:
        name = (raw_name or "").strip()
        if not name or len(name) > 80 or any(ch in name for ch in ("\r", "\n", ";")):
            raise RuntimeError("extension name 无效")
        result[name] = str(checker(name))
    return result


@contextmanager
def checked_out_extension(arcpy: Any, extension_name: str) -> Iterator[None]:
    """Check out one extension for a single call and check it back in afterwards."""
    name = (extension_name or "").strip()
    if not name:
        raise RuntimeError("extension_name 不能为空")
    status = str(arcpy.CheckExtension(name))
    checked_out_here = False
    if status == "Available":
        checkout_result = str(arcpy.CheckOutExtension(name))
        if checkout_result not in {"CheckedOut", "AlreadyInitialized"}:
            raise RuntimeError(f"扩展 {name!r} 签出失败：{checkout_result}")
        checked_out_here = True
    elif status not in {"CheckedOut", "AlreadyInitialized"}:
        raise RuntimeError(f"扩展 {name!r} 不可用：{status}")
    try:
        yield
    finally:
        if checked_out_here:
            try:
                arcpy.CheckInExtension(name)
            except Exception:  # noqa: BLE001
                pass


def validate_environment(environment: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a public snake_case environment mapping without mutating ArcPy state."""
    if not environment:
        return {}
    unknown = sorted(set(environment) - set(_ENV_KEYS))
    if unknown:
        raise RuntimeError(f"不支持的环境参数：{unknown}")
    normalized: dict[str, Any] = {}
    for public_name, value in environment.items():
        if value is None or value == "":
            continue
        if public_name in _PATH_ENV_KEYS:
            if not isinstance(value, str):
                raise RuntimeError(f"{public_name} 必须为路径字符串")
            value = validate_input_path_optional(value, public_name)
        elif (
            public_name in {"extent", "cell_size", "output_coordinate_system"}
            and isinstance(value, str)
            and is_probably_path(value)
        ):
            value = validate_input_path_optional(value, public_name)
        elif public_name == "cell_size" and isinstance(value, (int, float)):
            if float(value) <= 0:
                raise RuntimeError("cell_size 必须大于 0")
            value = float(value)
        elif public_name == "parallel_processing_factor":
            value = str(value).strip()
            if not value or len(value) > 32:
                raise RuntimeError("parallel_processing_factor 无效")
        normalized[_ENV_KEYS[public_name]] = value
    return normalized


@contextmanager
def scoped_environment(arcpy: Any, environment: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    """Apply allowlisted ArcPy environments through ``EnvManager`` for one call."""
    values = validate_environment(environment)
    if not values:
        yield values
        return
    manager = getattr(arcpy, "EnvManager", None)
    if not callable(manager):
        raise RuntimeError("当前 arcpy 不支持 EnvManager")
    with manager(**values):
        yield values


def _extent(extent: Any) -> dict[str, float] | None:
    if extent is None:
        return None
    result: dict[str, float] = {}
    for source, target in (("XMin", "xmin"), ("YMin", "ymin"), ("XMax", "xmax"), ("YMax", "ymax")):
        value = getattr(extent, source, None)
        if value is not None:
            result[target] = float(value)
    return result or None


def _raster_property(arcpy: Any, path: str, property_name: str) -> str | None:
    getter = getattr(getattr(arcpy, "management", None), "GetRasterProperties", None)
    if not callable(getter):
        return None
    try:
        value = getter(path, property_name).getOutput(0)
    except Exception:  # noqa: BLE001
        return None
    return None if value is None else str(value)


def raster_info(arcpy: Any, raster_path: str) -> dict[str, Any]:
    """Return a consolidated raster metadata snapshot."""
    path = validate_input_path_optional(raster_path, "raster_path")
    exists = getattr(arcpy, "Exists", None)
    if callable(exists) and not bool(exists(path)):
        raise RuntimeError(f"raster_path 不存在：{path}")
    desc = arcpy.Describe(path)
    spatial_reference = getattr(desc, "spatialReference", None)
    payload: dict[str, Any] = {
        "raster_path": path,
        "data_type": getattr(desc, "dataType", None),
        "format": getattr(desc, "format", None),
        "band_count": getattr(desc, "bandCount", None),
        "pixel_type": getattr(desc, "pixelType", None),
        "compression_type": getattr(desc, "compressionType", None),
        "has_raster_attribute_table": bool(getattr(desc, "hasRAT", False)),
        "mean_cell_width": getattr(desc, "meanCellWidth", None),
        "mean_cell_height": getattr(desc, "meanCellHeight", None),
        "no_data_value": getattr(desc, "noDataValue", None),
        "extent": _extent(getattr(desc, "extent", None)),
    }
    if spatial_reference is not None:
        payload["spatial_reference"] = {
            "name": getattr(spatial_reference, "name", None),
            "factory_code": getattr(spatial_reference, "factoryCode", None),
        }
    properties: dict[str, str] = {}
    for name in ("MINIMUM", "MAXIMUM", "MEAN", "STD", "VALUETYPE", "BANDCOUNT", "CELLSIZEX", "CELLSIZEY"):
        value = _raster_property(arcpy, path, name)
        if value is not None:
            properties[name.lower()] = value
    payload["properties"] = properties
    return payload


def _prepare_output(path: str, label: str) -> str:
    require_allow_write()
    require_gp_output_root_mandatory()
    return validate_gp_output_path(path, label)


def _save_and_verify(arcpy: Any, raster: Any, output_path: str) -> dict[str, Any]:
    raster.save(output_path)
    exists = getattr(arcpy, "Exists", None)
    if callable(exists) and not bool(exists(output_path)):
        raise RuntimeError(f"栅格输出未创建：{output_path}")
    return {"output_raster": output_path, "exists": True, "verified": True}


def run_fill(
    arcpy: Any,
    in_surface_raster: str,
    out_surface_raster: str,
    z_limit: float | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = validate_input_path_optional(in_surface_raster, "in_surface_raster")
    output = _prepare_output(out_surface_raster, "out_surface_raster")
    if z_limit is not None and float(z_limit) <= 0:
        raise RuntimeError("z_limit 必须大于 0")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.Fill(source, "#" if z_limit is None else float(z_limit))
        return _save_and_verify(arcpy, result, output)


def run_flow_direction(
    arcpy: Any,
    in_surface_raster: str,
    out_flow_direction_raster: str,
    force_flow: str = "NORMAL",
    flow_direction_type: str = "D8",
    out_drop_raster: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = validate_input_path_optional(in_surface_raster, "in_surface_raster")
    output = _prepare_output(out_flow_direction_raster, "out_flow_direction_raster")
    force = (force_flow or "NORMAL").strip().upper()
    if force not in {"NORMAL", "FORCE"}:
        raise RuntimeError("force_flow 须为 NORMAL 或 FORCE")
    direction = (flow_direction_type or "D8").strip().upper()
    if direction not in _FLOW_DIRECTION_TYPES:
        raise RuntimeError(f"flow_direction_type 须为 {sorted(_FLOW_DIRECTION_TYPES)}")
    drop = _prepare_output(out_drop_raster, "out_drop_raster") if out_drop_raster else "#"
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.FlowDirection(source, force, drop, direction)
        payload = _save_and_verify(arcpy, result, output)
    if drop != "#":
        exists = getattr(arcpy, "Exists", None)
        if callable(exists) and not bool(exists(drop)):
            raise RuntimeError(f"栅格输出未创建：{drop}")
        payload["drop_raster"] = drop
    return payload


def run_flow_accumulation(
    arcpy: Any,
    in_flow_direction_raster: str,
    out_accumulation_raster: str,
    in_weight_raster: str = "",
    data_type: str = "FLOAT",
    flow_direction_type: str = "D8",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    direction_raster = validate_input_path_optional(
        in_flow_direction_raster, "in_flow_direction_raster"
    )
    weight = (
        validate_input_path_optional(in_weight_raster, "in_weight_raster")
        if in_weight_raster
        else None
    )
    output = _prepare_output(out_accumulation_raster, "out_accumulation_raster")
    output_type = (data_type or "FLOAT").strip().upper()
    if output_type not in _FLOW_ACCUMULATION_TYPES:
        raise RuntimeError(f"data_type 须为 {sorted(_FLOW_ACCUMULATION_TYPES)}")
    direction = (flow_direction_type or "D8").strip().upper()
    if direction not in _FLOW_DIRECTION_TYPES:
        raise RuntimeError(f"flow_direction_type 须为 {sorted(_FLOW_DIRECTION_TYPES)}")
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.FlowAccumulation(
            direction_raster,
            weight or "#",
            output_type,
            direction,
        )
        return _save_and_verify(arcpy, result, output)


def run_snap_pour_point(
    arcpy: Any,
    in_pour_point_data: str,
    in_accumulation_raster: str,
    out_raster: str,
    snap_distance: float,
    pour_point_field: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pour_points = validate_input_path_optional(in_pour_point_data, "in_pour_point_data")
    accumulation = validate_input_path_optional(in_accumulation_raster, "in_accumulation_raster")
    output = _prepare_output(out_raster, "out_raster")
    distance = float(snap_distance)
    if distance <= 0:
        raise RuntimeError("snap_distance 必须大于 0")
    field = (pour_point_field or "").strip() or "#"
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.SnapPourPoint(pour_points, accumulation, distance, field)
        return _save_and_verify(arcpy, result, output)


def run_watershed(
    arcpy: Any,
    in_flow_direction_raster: str,
    in_pour_point_data: str,
    out_raster: str,
    pour_point_field: str = "",
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    direction = validate_input_path_optional(
        in_flow_direction_raster, "in_flow_direction_raster"
    )
    pour_points = validate_input_path_optional(in_pour_point_data, "in_pour_point_data")
    output = _prepare_output(out_raster, "out_raster")
    field = (pour_point_field or "").strip() or "#"
    with checked_out_extension(arcpy, "Spatial"), scoped_environment(arcpy, environment):
        result = arcpy.sa.Watershed(direction, pour_points, field)
        return _save_and_verify(arcpy, result, output)
