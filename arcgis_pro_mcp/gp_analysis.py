"""Additional geoprocessing analysis tools: geometry conversions, overlays, repair."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)

_MAX_SQL = 8000


def _sql(w: str) -> str | None:
    s = (w or "").strip()
    if len(s) > _MAX_SQL:
        raise RuntimeError("SQL/where 表达式过长")
    return s or None


def run_multiple_ring_buffer(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    distances: list[float],
    buffer_unit: str = "Meters",
    dissolve_option: str = "ALL",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    if not distances:
        raise RuntimeError("distances 不能为空")
    arcpy.analysis.MultipleRingBuffer(inf, out, distances, buffer_unit, "", dissolve_option)


def run_feature_to_point(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    point_location: str = "CENTROID",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    pl = point_location.strip().upper()
    if pl not in ("CENTROID", "INSIDE"):
        raise RuntimeError("point_location 须为 CENTROID 或 INSIDE")
    arcpy.management.FeatureToPoint(inf, out, pl)


def run_feature_to_line(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.management.FeatureToLine(inf, out)


def run_points_to_line(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    line_field: str = "",
    sort_field: str = "",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    lf = (line_field or "").strip()
    sf = (sort_field or "").strip()
    arcpy.management.PointsToLine(inf, out, lf or None, sf or None)


def run_polygon_to_line(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    neighbor_option: str = "IDENTIFY_NEIGHBORS",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.management.PolygonToLine(inf, out, neighbor_option.strip().upper())


def run_minimum_bounding_geometry(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    geometry_type: str = "ENVELOPE",
    group_option: str = "NONE",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    gt = geometry_type.strip().upper()
    valid_gt = {"RECTANGLE_BY_AREA", "RECTANGLE_BY_WIDTH", "CONVEX_HULL", "CIRCLE", "ENVELOPE"}
    if gt not in valid_gt:
        raise RuntimeError(f"geometry_type 须为 {sorted(valid_gt)}")
    arcpy.management.MinimumBoundingGeometry(inf, out, gt, group_option.strip().upper())


def run_convex_hull(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    group_option: str = "ALL",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.management.MinimumBoundingGeometry(inf, out, "CONVEX_HULL", group_option.strip().upper())


def run_split_by_attributes(
    arcpy: Any,
    in_table: str,
    target_workspace: str,
    split_fields: list[str],
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_table, "in_table")
    tw = validate_gp_output_path(target_workspace, "target_workspace")
    if not split_fields:
        raise RuntimeError("split_fields 不能为空")
    arcpy.analysis.SplitByAttributes(inf, tw, split_fields)


def run_identity(
    arcpy: Any,
    in_features: str,
    identity_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    idf = validate_input_path_optional(identity_features, "identity_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.Identity(inf, idf, out)


def run_symmetrical_difference(
    arcpy: Any,
    in_features: str,
    update_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    uf = validate_input_path_optional(update_features, "update_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.SymDiff(inf, uf, out)


def run_count_overlapping_features(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.CountOverlappingFeatures(inf, out)


def run_repair_geometry(
    arcpy: Any,
    in_features: str,
) -> None:
    require_allow_write()
    inf = validate_input_path_optional(in_features, "in_features")
    arcpy.management.RepairGeometry(inf)


def run_check_geometry(
    arcpy: Any,
    in_features: str,
    out_table: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_table, "out_table")
    arcpy.management.CheckGeometry(inf, out)


def run_eliminate(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    selection_type: str = "LENGTH",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    st = selection_type.strip().upper()
    if st not in ("LENGTH", "AREA"):
        raise RuntimeError("selection_type 须为 LENGTH 或 AREA")
    arcpy.management.EliminatePolygonPart(inf, out, st)


def run_multipart_to_singlepart(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.management.MultipartToSinglepart(inf, out)


def run_aggregate_polygons(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    aggregation_distance: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    ad = aggregation_distance.strip()
    if not ad:
        raise RuntimeError("aggregation_distance 不能为空（如 \"100 Meters\"）")
    arcpy.cartography.AggregatePolygons(inf, out, ad)
