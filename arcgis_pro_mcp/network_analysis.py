"""Run policy-constrained, durable one-shot analyses with ``arcpy.nax``."""

from __future__ import annotations

import math
import os
import re
from datetime import datetime
from typing import Any

from arcgis_pro_mcp.dataset_management import verify_output_dataset
from arcgis_pro_mcp.paths import (
    require_allow_destructive,
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)
from arcgis_pro_mcp.raster_runtime import checked_out_extension

_NETWORK_SERVICE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[\\/]{2}")
_NETWORK_DATA_TYPES = frozenset({"networkdataset"})
_TIME_UNITS = {
    "SECONDS": "Seconds",
    "MINUTES": "Minutes",
    "HOURS": "Hours",
    "DAYS": "Days",
}
_DISTANCE_UNITS = {
    "FEET": "Feet",
    "YARDS": "Yards",
    "MILES": "Miles",
    "NAUTICAL_MILES": "NauticalMiles",
    "METERS": "Meters",
    "KILOMETERS": "Kilometers",
    "INCHES": "Inches",
    "CENTIMETERS": "Centimeters",
    "MILLIMETERS": "Millimeters",
    "DECIMETERS": "Decimeters",
}
_TRAVEL_DIRECTIONS = {
    "FROM_FACILITY": "FromFacility",
    "TO_FACILITY": "ToFacility",
}
_TIME_ZONES = {
    "LOCAL_TIME_AT_LOCATIONS": "LocalTimeAtLocations",
    "UTC": "UTC",
}
_TIME_OF_DAY_USAGE = {
    "DEPARTURE_TIME": "DepartureTime",
    "ARRIVAL_TIME": "ArrivalTime",
}
_ROUTE_SHAPES = {
    "NO_LINE": "NoLine",
    "STRAIGHT_LINE": "StraightLine",
    "TRUE_SHAPE": "TrueShape",
    "TRUE_SHAPE_WITH_MEASURES": "TrueShapeWithMeasures",
}
_LINE_SHAPES = {
    "NO_LINE": "NoLine",
    "STRAIGHT_LINE": "StraightLine",
}
_SERVICE_AREA_CUTOFF_GEOMETRY = {
    "DISKS": "Disks",
    "RINGS": "Rings",
}
_SERVICE_AREA_OVERLAP_GEOMETRY = {
    "SPLIT": "Split",
    "OVERLAP": "Overlap",
    "DISSOLVE": "Dissolve",
}
_SERVICE_AREA_POLYGON_DETAIL = {
    "GENERALIZED": "Generalized",
    "STANDARD": "Standard",
    "HIGH": "High",
}


def _require_exists(arcpy: Any, path: Any, label: str) -> None:
    exists = getattr(arcpy, "Exists", None)
    if not callable(exists):
        raise RuntimeError("当前 arcpy 不支持 Exists，无法核验网络分析路径")
    if not bool(exists(path)):
        raise RuntimeError(f"{label} 不存在：{path}")


def _local_input(arcpy: Any, value: str, label: str) -> str:
    path = validate_input_path_optional(value, label)
    _require_exists(arcpy, path, label)
    return path


def _optional_local_input(arcpy: Any, value: str, label: str) -> str:
    return _local_input(arcpy, value, label) if value else ""


def _local_network_dataset(arcpy: Any, value: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError("network_data_source 必须是本地 network dataset 的绝对路径")
    candidate = value.strip().strip('"')
    if _NETWORK_SERVICE_RE.match(candidate) or candidate.lower().startswith(("http:", "https:")):
        raise RuntimeError("拒绝 Portal/URL 网络服务；仅允许本地 network dataset，避免消耗 credits")
    network = _local_input(arcpy, value, "network_data_source")
    describe = getattr(arcpy, "Describe", None)
    if not callable(describe):
        raise RuntimeError("当前 arcpy 不支持 Describe，无法确认本地 network dataset")
    try:
        data_type = str(getattr(describe(network), "dataType", ""))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法描述 network_data_source：{str(exc)[:300]}") from exc
    normalized = re.sub(r"[\s_-]+", "", data_type).lower()
    if normalized not in _NETWORK_DATA_TYPES:
        raise RuntimeError(
            f"network_data_source 必须是本地 network dataset；实际 dataType={data_type!r}"
        )
    return network


def _finite_positive(value: Any, label: str, *, optional: bool = False) -> float | None:
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool):
        raise RuntimeError(f"{label} 必须为大于 0 的有限数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 必须为大于 0 的有限数值") from exc
    if not math.isfinite(number) or number <= 0:
        raise RuntimeError(f"{label} 必须为大于 0 的有限数值")
    return number


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(f"{label} 必须为大于 0 的整数")
    return value


def _cutoffs(values: list[float]) -> list[float]:
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise RuntimeError("cutoffs 必须是包含 1–64 个值的列表")
    return [float(_finite_positive(value, f"cutoffs[{index}]")) for index, value in enumerate(values)]


def _token(value: str, mapping: dict[str, str], label: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} 须为 {sorted(mapping)}")
    normalized = value.strip().upper()
    member_name = mapping.get(normalized)
    if member_name is None:
        raise RuntimeError(f"{label} 须为 {sorted(mapping)}")
    return normalized, member_name


def _typed_value(
    arcpy: Any,
    container_name: str,
    value: str,
    mapping: dict[str, str],
    label: str,
) -> tuple[str, Any]:
    normalized, member_name = _token(value, mapping, label)
    container = getattr(getattr(arcpy, "nax", None), container_name, None)
    if container is None:
        raise RuntimeError(f"当前 ArcPy 缺少 {container_name}")
    return normalized, _enum_member(container, member_name, container_name)


def _prepare_new_outputs(arcpy: Any, requested: dict[str, str]) -> dict[str, str]:
    require_allow_write()
    require_gp_output_root_mandatory()
    outputs = {
        label: validate_gp_output_path(path, label)
        for label, path in requested.items()
        if path
    }
    if not outputs:
        raise RuntimeError("至少需要一个输出路径")
    identities: dict[str, str] = {}
    for label, path in outputs.items():
        identity = os.path.normcase(os.path.realpath(path))
        if identity in identities:
            raise RuntimeError(f"输出路径不能重复：{identities[identity]} 与 {label}")
        identities[identity] = label
        exists = getattr(arcpy, "Exists", None)
        if not callable(exists):
            raise RuntimeError("当前 arcpy 不支持 Exists，无法拒绝输出覆盖")
        if bool(exists(path)):
            raise RuntimeError(f"{label} 已存在，拒绝隐式覆盖：{path}")
    return outputs


def _travel_mode_payload(name: str, mode: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name}
    for source, target in (
        ("type", "type"),
        ("impedance", "impedance"),
        ("timeAttributeName", "time_attribute"),
        ("distanceAttributeName", "distance_attribute"),
        ("uTurnPolicy", "u_turn_policy"),
        ("useHierarchy", "use_hierarchy"),
    ):
        try:
            value = getattr(mode, source)
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            payload[target] = value
    try:
        payload["restrictions"] = list(mode.restrictions or [])
    except Exception:  # noqa: BLE001
        pass
    return payload


def _get_travel_modes(arcpy: Any, network_data_source: str) -> dict[str, Any]:
    nax = getattr(arcpy, "nax", None)
    getter = getattr(nax, "GetTravelModes", None)
    if not callable(getter):
        getter = getattr(getattr(arcpy, "na", None), "GetTravelModes", None)
    if not callable(getter):
        raise RuntimeError("当前 ArcPy 不支持 GetTravelModes")
    modes = getter(network_data_source)
    if not isinstance(modes, dict):
        raise RuntimeError("GetTravelModes 未返回预期的 travel mode 字典")
    return modes


def list_travel_modes(arcpy: Any, network_data_source: str) -> dict[str, Any]:
    """List local network travel modes without creating a solver object."""
    network = _local_network_dataset(arcpy, network_data_source)
    modes = _get_travel_modes(arcpy, network)
    return {
        "network_data_source": network,
        "travel_modes": [_travel_mode_payload(name, mode) for name, mode in modes.items()],
    }


def _parse_time_of_day(value: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError("time_of_day 必须为 ISO-8601 日期时间")
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("time_of_day 必须为 ISO-8601 日期时间") from exc


def _enum_member(container: Any, member_name: str, label: str) -> Any:
    member = getattr(container, member_name, None)
    if member is None:
        raise RuntimeError(f"当前 ArcPy 不支持 {label}={member_name}")
    return member


def _solver_messages(arcpy: Any, result: Any) -> list[dict[str, str]]:
    getter = getattr(result, "solverMessages", None)
    if not callable(getter):
        return []
    severity_type = getattr(getattr(arcpy, "nax", None), "MessageSeverity", None)
    all_severity = getattr(severity_type, "All", None)
    try:
        raw_messages = getter(all_severity) if all_severity is not None else getter()
    except Exception as exc:  # noqa: BLE001
        return [{"severity": "unknown", "message": f"读取 solver messages 失败：{str(exc)[:300]}"}]
    messages: list[dict[str, str]] = []
    for item in raw_messages or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            severity, message = item[0], item[1]
        else:
            severity, message = "unknown", item
        messages.append({"severity": str(severity), "message": str(message)})
    return messages


def _unlocated_count(arcpy: Any, result: Any, stops_output_type: Any) -> int | None:
    search_cursor = getattr(result, "searchCursor", None)
    if not callable(search_cursor):
        return None
    try:
        cursor = search_cursor(stops_output_type, ["Status"])
        return sum(1 for row in cursor if row and row[0] not in (0, "0", None))
    except Exception:  # noqa: BLE001
        return None


def _load_if_present(
    arcpy: Any,
    solver: Any,
    input_type_container_name: str,
    input_enum_name: str,
    dataset_path: str,
    label: str,
) -> None:
    if not dataset_path:
        return
    path = _local_input(arcpy, dataset_path, label)
    input_types = getattr(arcpy.nax, input_type_container_name, None)
    if input_types is None:
        raise RuntimeError(f"当前 ArcPy 缺少 {input_type_container_name}")
    input_type = _enum_member(input_types, input_enum_name, input_type_container_name)
    solver.load(input_type, path)


def _solver_with_travel_mode(
    arcpy: Any,
    network: str,
    solver_name: str,
    travel_mode: str,
) -> tuple[Any, str]:
    if not isinstance(travel_mode, str):
        raise RuntimeError("travel_mode 必须是 list_travel_modes 返回的名称")
    mode_name = travel_mode.strip()
    if not mode_name:
        raise RuntimeError("travel_mode 不能为空；请先调用 list_travel_modes")
    modes = _get_travel_modes(arcpy, network)
    if mode_name not in modes:
        raise RuntimeError(f"未找到 travel mode {mode_name!r}；可选：{list(modes)[:30]}")
    solver_type = getattr(getattr(arcpy, "nax", None), solver_name, None)
    if not callable(solver_type):
        raise RuntimeError(f"当前 ArcPy 不支持 arcpy.nax.{solver_name}")
    solver = solver_type(network)
    solver.travelMode = modes[mode_name]
    return solver, mode_name


def _solve_or_raise(arcpy: Any, solver: Any, analysis_name: str) -> tuple[Any, list[dict[str, str]]]:
    result = solver.solve()
    messages = _solver_messages(arcpy, result)
    if not bool(getattr(result, "solveSucceeded", False)):
        summary = "; ".join(item["message"] for item in messages[:10])
        raise RuntimeError(f"{analysis_name} 求解失败：{summary or '无消息'}")
    return result, messages


def _export_outputs(
    arcpy: Any,
    result: Any,
    output_type_container_name: str,
    members: dict[str, tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    output_types = getattr(getattr(arcpy, "nax", None), output_type_container_name, None)
    if output_types is None:
        raise RuntimeError(f"当前 ArcPy 缺少 {output_type_container_name}")
    outputs: dict[str, dict[str, Any]] = {}
    exists = getattr(arcpy, "Exists", None)
    for key, (member_name, output_path) in members.items():
        if callable(exists) and bool(exists(output_path)):
            raise RuntimeError(f"{key} 输出在求解期间已出现，拒绝覆盖：{output_path}")
        output_type = _enum_member(output_types, member_name, output_type_container_name)
        result.export(output_type, output_path)
        outputs[key] = verify_output_dataset(arcpy, output_path)
    return outputs


def solve_route_once(
    arcpy: Any,
    network_data_source: str,
    stops: str,
    travel_mode: str,
    out_routes: str,
    out_stops: str,
    out_directions: str = "",
    point_barriers: str = "",
    line_barriers: str = "",
    polygon_barriers: str = "",
    time_of_day: str = "",
    ignore_invalid_locations: bool = True,
    overwrite: bool = False,
    confirm_overwrite: bool = False,
) -> dict[str, Any]:
    """Solve and export one Route analysis without retaining process-local layers."""
    require_allow_write()
    require_gp_output_root_mandatory()
    network = _local_network_dataset(arcpy, network_data_source)
    stops_path = _local_input(arcpy, stops, "stops")
    if not out_routes or not out_stops:
        raise RuntimeError("out_routes 和 out_stops 不能为空")
    requested_outputs = {
        "out_routes": out_routes,
        "out_stops": out_stops,
        "out_directions": out_directions,
    }
    exists = getattr(arcpy, "Exists", None)
    if not overwrite and callable(exists):
        collisions = [
            validate_gp_output_path(path, label)
            for label, path in requested_outputs.items()
            if path and bool(exists(validate_gp_output_path(path, label)))
        ]
        if collisions:
            raise RuntimeError(f"输出已存在，默认拒绝覆盖：{collisions}")
    if overwrite:
        require_allow_destructive()
        if not confirm_overwrite:
            raise RuntimeError("overwrite=true 时必须同时设置 confirm_overwrite=true")
        require_gp_output_root_mandatory()
        for label, raw_path in requested_outputs.items():
            if not raw_path:
                continue
            path = validate_gp_output_path(raw_path, label)
            if callable(exists) and bool(exists(path)):
                arcpy.management.Delete(path)
                if bool(exists(path)):
                    raise RuntimeError(f"覆盖前无法删除已有输出：{path}")
    output_paths = _prepare_new_outputs(arcpy, requested_outputs)
    routes_output = output_paths["out_routes"]
    stops_output = output_paths["out_stops"]
    directions_output = output_paths.get("out_directions", "")
    mode_name = (travel_mode or "").strip()
    if not mode_name:
        raise RuntimeError("travel_mode 不能为空；请先调用 list_travel_modes")
    time_value = _parse_time_of_day(time_of_day)

    with checked_out_extension(arcpy, "Network"):
        solver, mode_name = _solver_with_travel_mode(arcpy, network, "Route", mode_name)
        if hasattr(solver, "ignoreInvalidLocations"):
            solver.ignoreInvalidLocations = bool(ignore_invalid_locations)
        if time_value is not None:
            solver.timeOfDay = time_value
        if directions_output and hasattr(solver, "returnDirections"):
            solver.returnDirections = True

        _load_if_present(arcpy, solver, "RouteInputDataType", "Stops", stops_path, "stops")
        _load_if_present(
            arcpy,
            solver,
            "RouteInputDataType",
            "PointBarriers",
            point_barriers,
            "point_barriers",
        )
        _load_if_present(
            arcpy,
            solver,
            "RouteInputDataType",
            "LineBarriers",
            line_barriers,
            "line_barriers",
        )
        _load_if_present(
            arcpy,
            solver,
            "RouteInputDataType",
            "PolygonBarriers",
            polygon_barriers,
            "polygon_barriers",
        )

        result, messages = _solve_or_raise(arcpy, solver, "Network Analyst Route")

        output_types = getattr(arcpy.nax, "RouteOutputDataType", None)
        if output_types is None:
            raise RuntimeError("当前 ArcPy 缺少 RouteOutputDataType")
        stops_type = _enum_member(output_types, "Stops", "RouteOutputDataType")
        unlocated = _unlocated_count(arcpy, result, stops_type)
        exports = {
            "routes": ("Routes", routes_output),
            "stops": ("Stops", stops_output),
        }
        if directions_output:
            exports["directions"] = ("Directions", directions_output)
        outputs = _export_outputs(arcpy, result, "RouteOutputDataType", exports)

    return {
        "solve_succeeded": True,
        "network_data_source": network,
        "travel_mode": mode_name,
        "time_of_day": time_value.isoformat() if time_value is not None else None,
        "unlocated_stops": unlocated,
        "messages": messages,
        "outputs": outputs,
    }


def solve_service_area_once(
    arcpy: Any,
    network_data_source: str,
    facilities: str,
    travel_mode: str,
    cutoffs: list[float],
    out_polygons: str,
    out_lines: str = "",
    out_facilities: str = "",
    point_barriers: str = "",
    line_barriers: str = "",
    polygon_barriers: str = "",
    time_units: str = "MINUTES",
    distance_units: str = "KILOMETERS",
    travel_direction: str = "FROM_FACILITY",
    geometry_at_cutoff: str = "RINGS",
    geometry_at_overlap: str = "OVERLAP",
    polygon_detail: str = "STANDARD",
    time_of_day: str = "",
    time_zone: str = "LOCAL_TIME_AT_LOCATIONS",
    ignore_invalid_locations: bool = True,
) -> dict[str, Any]:
    """Solve a local ``arcpy.nax.ServiceArea`` analysis and export its outputs."""
    network = _local_network_dataset(arcpy, network_data_source)
    facilities_path = _local_input(arcpy, facilities, "facilities")
    point_path = _optional_local_input(arcpy, point_barriers, "point_barriers")
    line_path = _optional_local_input(arcpy, line_barriers, "line_barriers")
    polygon_path = _optional_local_input(arcpy, polygon_barriers, "polygon_barriers")
    if not out_polygons and not out_lines:
        raise RuntimeError("out_polygons 与 out_lines 至少提供一个")
    output_paths = _prepare_new_outputs(
        arcpy,
        {
            "out_polygons": out_polygons,
            "out_lines": out_lines,
            "out_facilities": out_facilities,
        },
    )
    breaks = _cutoffs(cutoffs)
    time_token, time_unit = _typed_value(arcpy, "TimeUnits", time_units, _TIME_UNITS, "time_units")
    distance_token, distance_unit = _typed_value(
        arcpy,
        "DistanceUnits",
        distance_units,
        _DISTANCE_UNITS,
        "distance_units",
    )
    direction_token, direction = _typed_value(
        arcpy,
        "TravelDirection",
        travel_direction,
        _TRAVEL_DIRECTIONS,
        "travel_direction",
    )
    cutoff_token, cutoff_geometry = _typed_value(
        arcpy,
        "ServiceAreaPolygonCutoffGeometry",
        geometry_at_cutoff,
        _SERVICE_AREA_CUTOFF_GEOMETRY,
        "geometry_at_cutoff",
    )
    overlap_token, overlap_geometry = _typed_value(
        arcpy,
        "ServiceAreaOverlapGeometry",
        geometry_at_overlap,
        _SERVICE_AREA_OVERLAP_GEOMETRY,
        "geometry_at_overlap",
    )
    detail_token, detail = _typed_value(
        arcpy,
        "ServiceAreaPolygonDetail",
        polygon_detail,
        _SERVICE_AREA_POLYGON_DETAIL,
        "polygon_detail",
    )
    time_zone_token, time_zone_value = _typed_value(
        arcpy,
        "TimeZoneUsage",
        time_zone,
        _TIME_ZONES,
        "time_zone",
    )
    time_value = _parse_time_of_day(time_of_day)

    with checked_out_extension(arcpy, "Network"):
        solver, mode_name = _solver_with_travel_mode(
            arcpy,
            network,
            "ServiceArea",
            travel_mode,
        )
        solver.defaultImpedanceCutoffs = breaks
        solver.timeUnits = time_unit
        solver.distanceUnits = distance_unit
        solver.travelDirection = direction
        solver.geometryAtCutoff = cutoff_geometry
        solver.geometryAtOverlap = overlap_geometry
        solver.polygonDetail = detail
        solver.timeZone = time_zone_value
        solver.ignoreInvalidLocations = bool(ignore_invalid_locations)
        if time_value is not None:
            solver.timeOfDay = time_value

        output_type_container = getattr(arcpy.nax, "ServiceAreaOutputType", None)
        if output_type_container is None:
            raise RuntimeError("当前 ArcPy 缺少 ServiceAreaOutputType")
        if out_polygons and out_lines:
            output_type_member = "PolygonsAndLines"
        elif out_lines:
            output_type_member = "Lines"
        else:
            output_type_member = "Polygons"
        solver.outputType = _enum_member(
            output_type_container,
            output_type_member,
            "ServiceAreaOutputType",
        )

        for member_name, path, label in (
            ("Facilities", facilities_path, "facilities"),
            ("PointBarriers", point_path, "point_barriers"),
            ("LineBarriers", line_path, "line_barriers"),
            ("PolygonBarriers", polygon_path, "polygon_barriers"),
        ):
            _load_if_present(
                arcpy,
                solver,
                "ServiceAreaInputDataType",
                member_name,
                path,
                label,
            )
        result, messages = _solve_or_raise(arcpy, solver, "ServiceArea")
        output_types = getattr(arcpy.nax, "ServiceAreaOutputDataType", None)
        if output_types is None:
            raise RuntimeError("当前 ArcPy 缺少 ServiceAreaOutputDataType")
        facilities_type = _enum_member(
            output_types,
            "Facilities",
            "ServiceAreaOutputDataType",
        )
        unlocated = _unlocated_count(arcpy, result, facilities_type)
        exports: dict[str, tuple[str, str]] = {}
        if "out_polygons" in output_paths:
            exports["polygons"] = ("Polygons", output_paths["out_polygons"])
        if "out_lines" in output_paths:
            exports["lines"] = ("Lines", output_paths["out_lines"])
        if "out_facilities" in output_paths:
            exports["facilities"] = ("Facilities", output_paths["out_facilities"])
        outputs = _export_outputs(
            arcpy,
            result,
            "ServiceAreaOutputDataType",
            exports,
        )

    return {
        "solve_succeeded": True,
        "network_data_source": network,
        "travel_mode": mode_name,
        "cutoffs": breaks,
        "time_units": time_token,
        "distance_units": distance_token,
        "travel_direction": direction_token,
        "geometry_at_cutoff": cutoff_token,
        "geometry_at_overlap": overlap_token,
        "polygon_detail": detail_token,
        "time_zone": time_zone_token,
        "time_of_day": time_value.isoformat() if time_value is not None else None,
        "unlocated_facilities": unlocated,
        "messages": messages,
        "outputs": outputs,
    }


def solve_closest_facility_once(
    arcpy: Any,
    network_data_source: str,
    incidents: str,
    facilities: str,
    travel_mode: str,
    out_routes: str,
    out_incidents: str = "",
    out_facilities: str = "",
    out_directions: str = "",
    point_barriers: str = "",
    line_barriers: str = "",
    polygon_barriers: str = "",
    impedance_cutoff: float | None = None,
    target_facility_count: int = 1,
    time_units: str = "MINUTES",
    distance_units: str = "KILOMETERS",
    travel_direction: str = "TO_FACILITY",
    time_of_day: str = "",
    time_of_day_usage: str = "DEPARTURE_TIME",
    time_zone: str = "LOCAL_TIME_AT_LOCATIONS",
    route_shape_type: str = "TRUE_SHAPE_WITH_MEASURES",
    ignore_invalid_locations: bool = True,
) -> dict[str, Any]:
    """Solve a local ``arcpy.nax.ClosestFacility`` analysis and export results."""
    network = _local_network_dataset(arcpy, network_data_source)
    incidents_path = _local_input(arcpy, incidents, "incidents")
    facilities_path = _local_input(arcpy, facilities, "facilities")
    point_path = _optional_local_input(arcpy, point_barriers, "point_barriers")
    line_path = _optional_local_input(arcpy, line_barriers, "line_barriers")
    polygon_path = _optional_local_input(arcpy, polygon_barriers, "polygon_barriers")
    if not out_routes:
        raise RuntimeError("out_routes 不能为空")
    output_paths = _prepare_new_outputs(
        arcpy,
        {
            "out_routes": out_routes,
            "out_incidents": out_incidents,
            "out_facilities": out_facilities,
            "out_directions": out_directions,
        },
    )
    cutoff = _finite_positive(impedance_cutoff, "impedance_cutoff", optional=True)
    target_count = _positive_integer(target_facility_count, "target_facility_count")
    time_token, time_unit = _typed_value(arcpy, "TimeUnits", time_units, _TIME_UNITS, "time_units")
    distance_token, distance_unit = _typed_value(
        arcpy,
        "DistanceUnits",
        distance_units,
        _DISTANCE_UNITS,
        "distance_units",
    )
    direction_token, direction = _typed_value(
        arcpy,
        "TravelDirection",
        travel_direction,
        _TRAVEL_DIRECTIONS,
        "travel_direction",
    )
    usage_token, usage = _typed_value(
        arcpy,
        "TimeOfDayUsage",
        time_of_day_usage,
        _TIME_OF_DAY_USAGE,
        "time_of_day_usage",
    )
    time_zone_token, time_zone_value = _typed_value(
        arcpy,
        "TimeZoneUsage",
        time_zone,
        _TIME_ZONES,
        "time_zone",
    )
    shape_token, shape = _typed_value(
        arcpy,
        "RouteShapeType",
        route_shape_type,
        _ROUTE_SHAPES,
        "route_shape_type",
    )
    time_value = _parse_time_of_day(time_of_day)

    with checked_out_extension(arcpy, "Network"):
        solver, mode_name = _solver_with_travel_mode(
            arcpy,
            network,
            "ClosestFacility",
            travel_mode,
        )
        solver.defaultImpedanceCutoff = cutoff
        solver.defaultTargetFacilityCount = target_count
        solver.timeUnits = time_unit
        solver.distanceUnits = distance_unit
        solver.travelDirection = direction
        solver.timeOfDayUsage = usage
        solver.timeZone = time_zone_value
        solver.routeShapeType = shape
        solver.ignoreInvalidLocations = bool(ignore_invalid_locations)
        solver.returnDirections = bool(out_directions)
        if time_value is not None:
            solver.timeOfDay = time_value
        for member_name, path, label in (
            ("Incidents", incidents_path, "incidents"),
            ("Facilities", facilities_path, "facilities"),
            ("PointBarriers", point_path, "point_barriers"),
            ("LineBarriers", line_path, "line_barriers"),
            ("PolygonBarriers", polygon_path, "polygon_barriers"),
        ):
            _load_if_present(
                arcpy,
                solver,
                "ClosestFacilityInputDataType",
                member_name,
                path,
                label,
            )
        result, messages = _solve_or_raise(arcpy, solver, "ClosestFacility")
        output_types = getattr(arcpy.nax, "ClosestFacilityOutputDataType", None)
        if output_types is None:
            raise RuntimeError("当前 ArcPy 缺少 ClosestFacilityOutputDataType")
        incidents_type = _enum_member(
            output_types,
            "Incidents",
            "ClosestFacilityOutputDataType",
        )
        facilities_type = _enum_member(
            output_types,
            "Facilities",
            "ClosestFacilityOutputDataType",
        )
        unlocated_incidents = _unlocated_count(arcpy, result, incidents_type)
        unlocated_facilities = _unlocated_count(arcpy, result, facilities_type)
        exports = {"routes": ("Routes", output_paths["out_routes"])}
        if "out_incidents" in output_paths:
            exports["incidents"] = ("Incidents", output_paths["out_incidents"])
        if "out_facilities" in output_paths:
            exports["facilities"] = ("Facilities", output_paths["out_facilities"])
        if "out_directions" in output_paths:
            exports["directions"] = ("Directions", output_paths["out_directions"])
        outputs = _export_outputs(
            arcpy,
            result,
            "ClosestFacilityOutputDataType",
            exports,
        )

    return {
        "solve_succeeded": True,
        "network_data_source": network,
        "travel_mode": mode_name,
        "impedance_cutoff": cutoff,
        "target_facility_count": target_count,
        "time_units": time_token,
        "distance_units": distance_token,
        "travel_direction": direction_token,
        "time_of_day_usage": usage_token,
        "time_zone": time_zone_token,
        "route_shape_type": shape_token,
        "time_of_day": time_value.isoformat() if time_value is not None else None,
        "unlocated_incidents": unlocated_incidents,
        "unlocated_facilities": unlocated_facilities,
        "messages": messages,
        "outputs": outputs,
    }


def solve_origin_destination_cost_matrix_once(
    arcpy: Any,
    network_data_source: str,
    origins: str,
    destinations: str,
    travel_mode: str,
    out_lines: str,
    out_origins: str = "",
    out_destinations: str = "",
    point_barriers: str = "",
    line_barriers: str = "",
    polygon_barriers: str = "",
    impedance_cutoff: float | None = None,
    destination_count: int = 1,
    time_units: str = "MINUTES",
    distance_units: str = "KILOMETERS",
    time_of_day: str = "",
    time_zone: str = "LOCAL_TIME_AT_LOCATIONS",
    line_shape_type: str = "NO_LINE",
    ignore_invalid_locations: bool = True,
) -> dict[str, Any]:
    """Solve a local ``arcpy.nax.OriginDestinationCostMatrix`` analysis."""
    network = _local_network_dataset(arcpy, network_data_source)
    origins_path = _local_input(arcpy, origins, "origins")
    destinations_path = _local_input(arcpy, destinations, "destinations")
    point_path = _optional_local_input(arcpy, point_barriers, "point_barriers")
    line_path = _optional_local_input(arcpy, line_barriers, "line_barriers")
    polygon_path = _optional_local_input(arcpy, polygon_barriers, "polygon_barriers")
    if not out_lines:
        raise RuntimeError("out_lines 不能为空")
    output_paths = _prepare_new_outputs(
        arcpy,
        {
            "out_lines": out_lines,
            "out_origins": out_origins,
            "out_destinations": out_destinations,
        },
    )
    cutoff = _finite_positive(impedance_cutoff, "impedance_cutoff", optional=True)
    target_count = _positive_integer(destination_count, "destination_count")
    time_token, time_unit = _typed_value(arcpy, "TimeUnits", time_units, _TIME_UNITS, "time_units")
    distance_token, distance_unit = _typed_value(
        arcpy,
        "DistanceUnits",
        distance_units,
        _DISTANCE_UNITS,
        "distance_units",
    )
    time_zone_token, time_zone_value = _typed_value(
        arcpy,
        "TimeZoneUsage",
        time_zone,
        _TIME_ZONES,
        "time_zone",
    )
    shape_token, shape = _typed_value(
        arcpy,
        "LineShapeType",
        line_shape_type,
        _LINE_SHAPES,
        "line_shape_type",
    )
    time_value = _parse_time_of_day(time_of_day)

    with checked_out_extension(arcpy, "Network"):
        solver, mode_name = _solver_with_travel_mode(
            arcpy,
            network,
            "OriginDestinationCostMatrix",
            travel_mode,
        )
        solver.defaultImpedanceCutoff = cutoff
        solver.defaultDestinationCount = target_count
        solver.timeUnits = time_unit
        solver.distanceUnits = distance_unit
        solver.timeZone = time_zone_value
        solver.lineShapeType = shape
        solver.ignoreInvalidLocations = bool(ignore_invalid_locations)
        if time_value is not None:
            solver.timeOfDay = time_value
        for member_name, path, label in (
            ("Origins", origins_path, "origins"),
            ("Destinations", destinations_path, "destinations"),
            ("PointBarriers", point_path, "point_barriers"),
            ("LineBarriers", line_path, "line_barriers"),
            ("PolygonBarriers", polygon_path, "polygon_barriers"),
        ):
            _load_if_present(
                arcpy,
                solver,
                "OriginDestinationCostMatrixInputDataType",
                member_name,
                path,
                label,
            )
        result, messages = _solve_or_raise(
            arcpy,
            solver,
            "OriginDestinationCostMatrix",
        )
        output_types = getattr(arcpy.nax, "OriginDestinationCostMatrixOutputDataType", None)
        if output_types is None:
            raise RuntimeError("当前 ArcPy 缺少 OriginDestinationCostMatrixOutputDataType")
        origins_type = _enum_member(
            output_types,
            "Origins",
            "OriginDestinationCostMatrixOutputDataType",
        )
        destinations_type = _enum_member(
            output_types,
            "Destinations",
            "OriginDestinationCostMatrixOutputDataType",
        )
        unlocated_origins = _unlocated_count(arcpy, result, origins_type)
        unlocated_destinations = _unlocated_count(arcpy, result, destinations_type)
        exports = {"lines": ("Lines", output_paths["out_lines"])}
        if "out_origins" in output_paths:
            exports["origins"] = ("Origins", output_paths["out_origins"])
        if "out_destinations" in output_paths:
            exports["destinations"] = (
                "Destinations",
                output_paths["out_destinations"],
            )
        outputs = _export_outputs(
            arcpy,
            result,
            "OriginDestinationCostMatrixOutputDataType",
            exports,
        )

    return {
        "solve_succeeded": True,
        "network_data_source": network,
        "travel_mode": mode_name,
        "impedance_cutoff": cutoff,
        "destination_count": target_count,
        "time_units": time_token,
        "distance_units": distance_token,
        "time_zone": time_zone_token,
        "line_shape_type": shape_token,
        "time_of_day": time_value.isoformat() if time_value is not None else None,
        "unlocated_origins": unlocated_origins,
        "unlocated_destinations": unlocated_destinations,
        "messages": messages,
        "outputs": outputs,
    }
