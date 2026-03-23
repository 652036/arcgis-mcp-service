"""Network analysis tools: routing, service area, OD cost matrix."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    validate_input_path_optional,
)


def run_make_route_analysis_layer(
    arcpy: Any,
    network_data_source: str,
    layer_name: str = "Route",
    travel_mode: str = "",
) -> str:
    require_allow_write()
    nds = validate_input_path_optional(network_data_source, "network_data_source")
    ln = layer_name.strip() or "Route"
    tm = (travel_mode or "").strip()
    if tm:
        result = arcpy.na.MakeRouteAnalysisLayer(nds, ln, tm)
    else:
        result = arcpy.na.MakeRouteAnalysisLayer(nds, ln)
    return str(result.getOutput(0))


def run_add_locations(
    arcpy: Any,
    in_network_analysis_layer: str,
    sub_layer: str,
    in_table: str,
    field_mappings: str = "",
) -> None:
    require_allow_write()
    inf = validate_input_path_optional(in_table, "in_table")
    sl = sub_layer.strip()
    if not sl:
        raise RuntimeError("sub_layer 不能为空（如 Stops、Facilities 等）")
    fm = (field_mappings or "").strip()
    if fm:
        arcpy.na.AddLocations(in_network_analysis_layer, sl, inf, fm)
    else:
        arcpy.na.AddLocations(in_network_analysis_layer, sl, inf)


def run_solve(
    arcpy: Any,
    in_network_analysis_layer: str,
    ignore_invalids: bool = True,
) -> str:
    require_allow_write()
    ii = "SKIP" if ignore_invalids else "HALT"
    result = arcpy.na.Solve(in_network_analysis_layer, ii)
    try:
        return str(result.getOutput(0))
    except Exception:
        return str(result)


def run_make_service_area_analysis_layer(
    arcpy: Any,
    network_data_source: str,
    layer_name: str = "ServiceArea",
    travel_mode: str = "",
    cutoffs: list[float] | None = None,
) -> str:
    require_allow_write()
    nds = validate_input_path_optional(network_data_source, "network_data_source")
    ln = layer_name.strip() or "ServiceArea"
    tm = (travel_mode or "").strip()
    kwargs: dict[str, Any] = {}
    if tm:
        kwargs["travel_mode"] = tm
    if cutoffs:
        kwargs["cutoffs"] = cutoffs
    result = arcpy.na.MakeServiceAreaAnalysisLayer(nds, ln, **kwargs)
    return str(result.getOutput(0))


def run_make_od_cost_matrix_layer(
    arcpy: Any,
    network_data_source: str,
    layer_name: str = "ODMatrix",
    travel_mode: str = "",
    cutoff: float | None = None,
    number_of_destinations_to_find: int | None = None,
) -> str:
    require_allow_write()
    nds = validate_input_path_optional(network_data_source, "network_data_source")
    ln = layer_name.strip() or "ODMatrix"
    tm = (travel_mode or "").strip()
    kwargs: dict[str, Any] = {}
    if tm:
        kwargs["travel_mode"] = tm
    if cutoff is not None:
        kwargs["cutoff"] = float(cutoff)
    if number_of_destinations_to_find is not None:
        kwargs["number_of_destinations_to_find"] = int(number_of_destinations_to_find)
    result = arcpy.na.MakeODCostMatrixAnalysisLayer(nds, ln, **kwargs)
    return str(result.getOutput(0))
