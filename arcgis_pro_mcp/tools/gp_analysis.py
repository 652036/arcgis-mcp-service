"""Vector geoprocessing analysis, overlay, proximity, and geometry tools."""

from __future__ import annotations

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool


@tool(
    name="arcgis_pro_gp_buffer",
    description="",
)
def arcgis_pro_gp_buffer(
    in_features: str,
    out_feature_class: str,
    buffer_distance_or_field: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_buffer(arcpy, in_features, out_feature_class, buffer_distance_or_field)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_clip",
    description="",
)
def arcgis_pro_gp_clip(
    in_features: str,
    clip_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_clip(arcpy, in_features, clip_features, out_feature_class)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.validate_input_path_optional(in_features, "in_features"),
            "clip_features": _srv.validate_input_path_optional(clip_features, "clip_features"),
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_analysis_select",
    description="",
)
def arcgis_pro_gp_analysis_select(
    in_features: str,
    out_feature_class: str,
    where_clause: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_select(arcpy, in_features, out_feature_class, where_clause)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_copy_features",
    description="",
)
def arcgis_pro_gp_copy_features(in_features: str, out_feature_class: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_copy_features(arcpy, in_features, out_feature_class)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_dissolve",
    description="",
)
def arcgis_pro_gp_dissolve(
    in_features: str,
    out_feature_class: str,
    dissolve_field: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_dissolve(arcpy, in_features, out_feature_class, dissolve_field)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.normalize_path(in_features),
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_intersect",
    description="",
)
def arcgis_pro_gp_intersect(
    in_feature_paths: list[str],
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_intersect(arcpy, in_feature_paths, out_feature_class)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_feature_paths": [_srv.normalize_path(p) for p in in_feature_paths],
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_union",
    description="",
)
def arcgis_pro_gp_union(
    in_feature_paths: list[str],
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_union(arcpy, in_feature_paths, out_feature_class)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_feature_paths": [_srv.normalize_path(p) for p in in_feature_paths],
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_erase",
    description="",
)
def arcgis_pro_gp_erase(
    in_features: str,
    erase_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_erase(arcpy, in_features, erase_features, out_feature_class)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.normalize_path(in_features),
            "erase_features": _srv.normalize_path(erase_features),
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_spatial_join",
    description="",
)
def arcgis_pro_gp_spatial_join(
    target_features: str,
    join_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_spatial_join(arcpy, target_features, join_features, out_feature_class)
    return _srv._json_dumps(
        {
            "ok": True,
            "target_features": _srv.normalize_path(target_features),
            "join_features": _srv.normalize_path(join_features),
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_statistics",
    description="",
)
def arcgis_pro_gp_statistics(
    in_table: str,
    out_table: str,
    statistics_fields: str,
    case_field: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_statistics(arcpy, in_table, out_table, statistics_fields, case_field)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_table": _srv.normalize_path(in_table),
            "out_table": _srv.normalize_path(out_table),
        },
    )


@tool(
    name="arcgis_pro_gp_frequency",
    description="",
)
def arcgis_pro_gp_frequency(
    in_table: str,
    out_table: str,
    frequency_fields: str,
    summary_fields: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_frequency(arcpy, in_table, out_table, frequency_fields, summary_fields)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_table": _srv.normalize_path(in_table),
            "out_table": _srv.normalize_path(out_table),
        },
    )


@tool(
    name="arcgis_pro_gp_table_select",
    description="",
)
def arcgis_pro_gp_table_select(
    in_table: str,
    out_table: str,
    where_clause: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_table_select(arcpy, in_table, out_table, where_clause)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_table": _srv.normalize_path(in_table),
            "out_table": _srv.normalize_path(out_table),
        },
    )


@tool(
    name="arcgis_pro_gp_merge",
    description="",
)
def arcgis_pro_gp_merge(
    in_feature_paths: list[str],
    output_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_merge(arcpy, in_feature_paths, output_feature_class)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_feature_paths": [_srv.normalize_path(p) for p in in_feature_paths],
            "output_feature_class": _srv.normalize_path(output_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_project",
    description="",
)
def arcgis_pro_gp_project(
    in_dataset: str,
    out_dataset: str,
    out_wkid: int,
    transform_method: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_project(arcpy, in_dataset, out_dataset, out_wkid, transform_method)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_dataset": _srv.normalize_path(in_dataset),
            "out_dataset": _srv.normalize_path(out_dataset),
            "out_wkid": int(out_wkid),
        },
    )


@tool(
    name="arcgis_pro_gp_near",
    description="",
)
def arcgis_pro_gp_near(in_features: str, near_features: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_near(arcpy, in_features, near_features)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.normalize_path(in_features),
            "near_features": _srv.normalize_path(near_features),
        },
    )


@tool(
    name="arcgis_pro_gp_generate_near_table",
    description="",
)
def arcgis_pro_gp_generate_near_table(
    in_features: str,
    near_features: str,
    out_table: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_generate_near_table(arcpy, in_features, near_features, out_table)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.normalize_path(in_features),
            "near_features": _srv.normalize_path(near_features),
            "out_table": _srv.normalize_path(out_table),
        },
    )


@tool(
    name="arcgis_pro_gp_multiple_ring_buffer",
    description="",
)
def arcgis_pro_gp_multiple_ring_buffer(
    in_features: str,
    out_feature_class: str,
    distances: list[float],
    buffer_unit: str = "Meters",
    dissolve_option: str = "ALL",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_multiple_ring_buffer(
        arcpy, in_features, out_feature_class, distances, buffer_unit, dissolve_option
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_feature_to_point",
    description="",
)
def arcgis_pro_gp_feature_to_point(
    in_features: str,
    out_feature_class: str,
    point_location: str = "CENTROID",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_feature_to_point(arcpy, in_features, out_feature_class, point_location)
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_feature_to_line",
    description="",
)
def arcgis_pro_gp_feature_to_line(in_features: str, out_feature_class: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_feature_to_line(arcpy, in_features, out_feature_class)
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_points_to_line",
    description="",
)
def arcgis_pro_gp_points_to_line(
    in_features: str,
    out_feature_class: str,
    line_field: str = "",
    sort_field: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_points_to_line(
        arcpy, in_features, out_feature_class, line_field, sort_field
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_polygon_to_line",
    description="",
)
def arcgis_pro_gp_polygon_to_line(
    in_features: str,
    out_feature_class: str,
    neighbor_option: str = "IDENTIFY_NEIGHBORS",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_polygon_to_line(arcpy, in_features, out_feature_class, neighbor_option)
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_minimum_bounding_geometry",
    description="",
)
def arcgis_pro_gp_minimum_bounding_geometry(
    in_features: str,
    out_feature_class: str,
    geometry_type: str = "ENVELOPE",
    group_option: str = "NONE",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_minimum_bounding_geometry(
        arcpy, in_features, out_feature_class, geometry_type, group_option
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_convex_hull",
    description="",
)
def arcgis_pro_gp_convex_hull(
    in_features: str,
    out_feature_class: str,
    group_option: str = "ALL",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_convex_hull(arcpy, in_features, out_feature_class, group_option)
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_split_by_attributes",
    description="",
)
def arcgis_pro_gp_split_by_attributes(
    in_table: str,
    target_workspace: str,
    split_fields: list[str],
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_split_by_attributes(arcpy, in_table, target_workspace, split_fields)
    return _srv._json_dumps(
        {"ok": True, "target_workspace": _srv.normalize_path(target_workspace)},
    )


@tool(
    name="arcgis_pro_gp_identity",
    description="",
)
def arcgis_pro_gp_identity(
    in_features: str,
    identity_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_identity(arcpy, in_features, identity_features, out_feature_class)
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_symmetrical_difference",
    description="",
)
def arcgis_pro_gp_symmetrical_difference(
    in_features: str,
    update_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_symmetrical_difference(
        arcpy, in_features, update_features, out_feature_class
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_count_overlapping_features",
    description="",
)
def arcgis_pro_gp_count_overlapping_features(
    in_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_count_overlapping_features(arcpy, in_features, out_feature_class)
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_repair_geometry",
    description="以固定 KEEP_NULL 模式就地修复几何；不会删除空几何记录。",
)
def arcgis_pro_gp_repair_geometry(in_features: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_repair_geometry(arcpy, in_features)
    return _srv._json_dumps({"ok": True, "in_features": _srv.normalize_path(in_features)})


@tool(
    name="arcgis_pro_gp_check_geometry",
    description="",
)
def arcgis_pro_gp_check_geometry(in_features: str, out_table: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_check_geometry(arcpy, in_features, out_table)
    return _srv._json_dumps({"ok": True, "out_table": _srv.normalize_path(out_table)})


@tool(
    name="arcgis_pro_gp_eliminate",
    description="",
)
def arcgis_pro_gp_eliminate(
    in_features: str,
    out_feature_class: str,
    condition: str = "AREA",
    part_area: float = 0.0,
    part_area_percent: float = 0.0,
    part_option: str = "CONTAINED_ONLY",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_eliminate(
        arcpy,
        in_features,
        out_feature_class,
        condition,
        part_area,
        part_area_percent,
        part_option,
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_multipart_to_singlepart",
    description="",
)
def arcgis_pro_gp_multipart_to_singlepart(
    in_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_multipart_to_singlepart(arcpy, in_features, out_feature_class)
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_aggregate_polygons",
    description="",
)
def arcgis_pro_gp_aggregate_polygons(
    in_features: str,
    out_feature_class: str,
    aggregation_distance: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_analysis.run_aggregate_polygons(
        arcpy, in_features, out_feature_class, aggregation_distance
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )
