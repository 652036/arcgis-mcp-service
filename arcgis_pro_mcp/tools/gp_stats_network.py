"""Spatial statistics, space-time forecasting, and network analysis tools."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool


@tool(
    name="arcgis_pro_na_create_route_layer",
    description="",
)
def arcgis_pro_na_create_route_layer(
    network_data_source: str,
    layer_name: str = "Route",
    travel_mode: str = "",
) -> str:
    arcpy = _srv._arcpy()
    result = _srv.gp_network.run_make_route_analysis_layer(
        arcpy, network_data_source, layer_name, travel_mode
    )
    return _srv._json_dumps({"ok": True, "layer": result})


@tool(
    name="arcgis_pro_na_add_locations",
    description="",
)
def arcgis_pro_na_add_locations(
    in_network_analysis_layer: str,
    sub_layer: str,
    in_table: str,
    field_mappings: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_network.run_add_locations(
        arcpy, in_network_analysis_layer, sub_layer, in_table, field_mappings
    )
    return _srv._json_dumps({"ok": True, "sub_layer": sub_layer})


@tool(
    name="arcgis_pro_na_solve",
    description="",
)
def arcgis_pro_na_solve(
    in_network_analysis_layer: str,
    ignore_invalids: bool = True,
) -> str:
    arcpy = _srv._arcpy()
    result = _srv.gp_network.run_solve(arcpy, in_network_analysis_layer, ignore_invalids)
    return _srv._json_dumps({"ok": True, "result": result})


@tool(
    name="arcgis_pro_na_service_area",
    description="",
)
def arcgis_pro_na_service_area(
    network_data_source: str,
    layer_name: str = "ServiceArea",
    travel_mode: str = "",
    cutoffs: list[float] | None = None,
) -> str:
    arcpy = _srv._arcpy()
    result = _srv.gp_network.run_make_service_area_analysis_layer(
        arcpy, network_data_source, layer_name, travel_mode, cutoffs
    )
    return _srv._json_dumps({"ok": True, "layer": result})


@tool(
    name="arcgis_pro_na_od_matrix",
    description="",
)
def arcgis_pro_na_od_matrix(
    network_data_source: str,
    layer_name: str = "ODMatrix",
    travel_mode: str = "",
    cutoff: float | None = None,
    number_of_destinations_to_find: int | None = None,
) -> str:
    arcpy = _srv._arcpy()
    result = _srv.gp_network.run_make_od_cost_matrix_layer(
        arcpy, network_data_source, layer_name, travel_mode, cutoff, number_of_destinations_to_find
    )
    return _srv._json_dumps({"ok": True, "layer": result})


@tool(
    name="arcgis_pro_gp_hot_spots",
    description="",
)
def arcgis_pro_gp_hot_spots(
    in_features: str,
    input_field: str,
    out_feature_class: str,
    conceptualization: str = "FIXED_DISTANCE_BAND",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "NONE",
    distance_band: float | None = None,
    apply_fdr: bool = False,
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_hot_spots(
        arcpy,
        in_features,
        input_field,
        out_feature_class,
        conceptualization,
        distance_method,
        standardization,
        distance_band,
        apply_fdr,
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class), "messages": msgs},
    )


@tool(
    name="arcgis_pro_gp_optimized_hot_spots",
    description="",
)
def arcgis_pro_gp_optimized_hot_spots(
    in_features: str,
    out_features: str,
    analysis_field: str = "",
    aggregation_method: str = "",
    cell_size: float | None = None,
    distance_band: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_optimized_hot_spots(
        arcpy, in_features, out_features, analysis_field, aggregation_method, cell_size, distance_band
    )
    return _srv._json_dumps(
        {"ok": True, "out_features": _srv.normalize_path(out_features), "messages": msgs},
    )


@tool(
    name="arcgis_pro_gp_cluster_outlier",
    description="",
)
def arcgis_pro_gp_cluster_outlier(
    in_features: str,
    input_field: str,
    out_feature_class: str,
    conceptualization: str = "FIXED_DISTANCE_BAND",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "NONE",
    distance_band: float | None = None,
    apply_fdr: bool = False,
    number_of_permutations: int | None = None,
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_cluster_outlier(
        arcpy,
        in_features,
        input_field,
        out_feature_class,
        conceptualization,
        distance_method,
        standardization,
        distance_band,
        apply_fdr,
        number_of_permutations,
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class), "messages": msgs},
    )


@tool(
    name="arcgis_pro_gp_spatial_autocorrelation",
    description="",
)
def arcgis_pro_gp_spatial_autocorrelation(
    in_features: str,
    input_field: str,
    conceptualization: str = "INVERSE_DISTANCE",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "ROW",
    distance_band: float | None = None,
    generate_report: bool = False,
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_spatial_autocorrelation(
        arcpy,
        in_features,
        input_field,
        conceptualization,
        distance_method,
        standardization,
        distance_band,
        generate_report,
    )
    return _srv._json_dumps({"ok": True, "messages": msgs})


@tool(
    name="arcgis_pro_gp_average_nearest_neighbor",
    description="",
)
def arcgis_pro_gp_average_nearest_neighbor(
    in_features: str,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    generate_report: bool = False,
    area: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_average_nearest_neighbor(
        arcpy, in_features, distance_method, generate_report, area
    )
    return _srv._json_dumps({"ok": True, "messages": msgs})


@tool(
    name="arcgis_pro_gp_multi_distance_spatial_clustering",
    description="",
)
def arcgis_pro_gp_multi_distance_spatial_clustering(
    in_features: str,
    out_table: str,
    number_of_distance_bands: int,
    compute_confidence_envelope: str = "0_PERMUTATIONS_-_NO_CONFIDENCE_ENVELOPE",
    weight_field: str = "",
    beginning_distance: float | None = None,
    distance_increment: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_multi_distance_spatial_clustering(
        arcpy,
        in_features,
        out_table,
        number_of_distance_bands,
        compute_confidence_envelope,
        weight_field,
        beginning_distance,
        distance_increment,
    )
    return _srv._json_dumps(
        {"ok": True, "out_table": _srv.normalize_path(out_table), "messages": msgs},
    )


@tool(
    name="arcgis_pro_gp_ordinary_least_squares",
    description="",
)
def arcgis_pro_gp_ordinary_least_squares(
    in_features: str,
    unique_id_field: str,
    out_feature_class: str,
    dependent_variable: str,
    explanatory_variables: list[str],
    coefficient_output_table: str = "",
    diagnostic_output_table: str = "",
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_ordinary_least_squares(
        arcpy,
        in_features,
        unique_id_field,
        out_feature_class,
        dependent_variable,
        explanatory_variables,
        coefficient_output_table,
        diagnostic_output_table,
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class), "messages": msgs},
    )


@tool(
    name="arcgis_pro_gp_gwr",
    description="",
)
def arcgis_pro_gp_gwr(
    in_features: str,
    dependent_variable: str,
    explanatory_variables: list[str],
    out_features: str,
    model_type: str = "CONTINUOUS",
    neighborhood_type: str = "NUMBER_OF_NEIGHBORS",
    neighborhood_selection_method: str = "GOLDEN_SEARCH",
    number_of_neighbors: int | None = None,
    distance_band: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_gwr(
        arcpy,
        in_features,
        dependent_variable,
        explanatory_variables,
        out_features,
        model_type,
        neighborhood_type,
        neighborhood_selection_method,
        number_of_neighbors,
        distance_band,
    )
    return _srv._json_dumps(
        {"ok": True, "out_features": _srv.normalize_path(out_features), "messages": msgs},
    )


@tool(
    name="arcgis_pro_gp_forest",
    description="",
)
def arcgis_pro_gp_forest(
    in_features: str,
    variable_predict: str,
    explanatory_variables: list[str],
    prediction_type: str = "TRAIN",
    explanatory_variables_categorical: list[str] | None = None,
    treat_variable_as_categorical: bool = False,
    number_of_trees: int = 100,
    output_trained_features: str = "",
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_stats.run_forest(
        arcpy,
        in_features,
        variable_predict,
        explanatory_variables,
        prediction_type,
        explanatory_variables_categorical,
        treat_variable_as_categorical,
        number_of_trees,
        output_trained_features,
    )
    return _srv._json_dumps({"ok": True, "messages": msgs})


@tool(
    name="arcgis_pro_gp_central_feature",
    description="",
)
def arcgis_pro_gp_central_feature(
    in_features: str,
    out_feature_class: str,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    weight_field: str = "",
    case_field: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_stats.run_central_feature(
        arcpy, in_features, out_feature_class, distance_method, weight_field, case_field
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_mean_center",
    description="",
)
def arcgis_pro_gp_mean_center(
    in_features: str,
    out_feature_class: str,
    weight_field: str = "",
    case_field: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_stats.run_mean_center(arcpy, in_features, out_feature_class, weight_field, case_field)
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_directional_distribution",
    description="",
)
def arcgis_pro_gp_directional_distribution(
    in_features: str,
    out_feature_class: str,
    ellipse_size: str = "1_STANDARD_DEVIATION",
    weight_field: str = "",
    case_field: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_stats.run_directional_distribution(
        arcpy, in_features, out_feature_class, ellipse_size, weight_field, case_field
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_create_random_points",
    description="",
)
def arcgis_pro_gp_create_random_points(
    out_path: str,
    out_name: str,
    number_of_points: int,
    constraining_feature_class: str = "",
    minimum_allowed_distance: str = "",
) -> str:
    arcpy = _srv._arcpy()
    created = _srv.gp_stats.run_create_random_points(
        arcpy, out_path, out_name, number_of_points, constraining_feature_class, minimum_allowed_distance
    )
    return _srv._json_dumps({"ok": True, "created": created})


@tool(
    name="arcgis_pro_gp_generate_tessellation",
    description="",
)
def arcgis_pro_gp_generate_tessellation(
    output_feature_class: str,
    extent: str,
    shape_type: str = "HEXAGON",
    size: str = "",
    spatial_reference_wkid: int | None = None,
) -> str:
    arcpy = _srv._arcpy()
    out = _srv.gp_stats.run_generate_tessellation(
        arcpy, output_feature_class, extent, shape_type, size, spatial_reference_wkid
    )
    return _srv._json_dumps({"ok": True, "output_feature_class": _srv.normalize_path(out)})


@tool(
    name="arcgis_pro_network_travel_modes",
    description="列出网络数据源可用的 travel modes 及阻抗、限制和层级属性。",
)
def arcgis_pro_network_travel_modes(network_data_source: str) -> str:
    return _srv._json_dumps(
        _srv.network_analysis.list_travel_modes(_srv._arcpy(), network_data_source)
    )


@tool(
    name="arcgis_pro_network_solve_route",
    description="使用 arcpy.nax 一次性求解路线并导出、核验 Routes/Stops/Directions，不保留进程内 solver。",
)
def arcgis_pro_network_solve_route(
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
) -> str:
    return _srv._json_dumps(
        _srv.network_analysis.solve_route_once(
            _srv._arcpy(),
            network_data_source,
            stops,
            travel_mode,
            out_routes,
            out_stops,
            out_directions,
            point_barriers,
            line_barriers,
            polygon_barriers,
            time_of_day,
            ignore_invalid_locations,
            overwrite,
            confirm_overwrite,
        )
    )


@tool(
    name="arcgis_pro_network_solve_service_area",
    description="用本地 NetworkDataset 一次性求解 Service Area，导出并核验 polygons/lines/facilities。",
)
def arcgis_pro_network_solve_service_area(
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
) -> str:
    return _srv._json_dumps(
        _srv.network_analysis.solve_service_area_once(
            _srv._arcpy(),
            network_data_source,
            facilities,
            travel_mode,
            cutoffs,
            out_polygons,
            out_lines,
            out_facilities,
            point_barriers,
            line_barriers,
            polygon_barriers,
            time_units,
            distance_units,
            travel_direction,
            geometry_at_cutoff,
            geometry_at_overlap,
            polygon_detail,
            time_of_day,
            time_zone,
            ignore_invalid_locations,
        )
    )


@tool(
    name="arcgis_pro_network_solve_closest_facility",
    description="用本地 NetworkDataset 一次性求解 Closest Facility，并核验所有显式输出。",
)
def arcgis_pro_network_solve_closest_facility(
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
) -> str:
    return _srv._json_dumps(
        _srv.network_analysis.solve_closest_facility_once(
            _srv._arcpy(),
            network_data_source,
            incidents,
            facilities,
            travel_mode,
            out_routes,
            out_incidents,
            out_facilities,
            out_directions,
            point_barriers,
            line_barriers,
            polygon_barriers,
            impedance_cutoff,
            target_facility_count,
            time_units,
            distance_units,
            travel_direction,
            time_of_day,
            time_of_day_usage,
            time_zone,
            route_shape_type,
            ignore_invalid_locations,
        )
    )


@tool(
    name="arcgis_pro_network_solve_od_cost_matrix",
    description="用本地 NetworkDataset 一次性求解 Origin-Destination Cost Matrix，拒绝计费 URL 服务。",
)
def arcgis_pro_network_solve_od_cost_matrix(
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
) -> str:
    return _srv._json_dumps(
        _srv.network_analysis.solve_origin_destination_cost_matrix_once(
            _srv._arcpy(),
            network_data_source,
            origins,
            destinations,
            travel_mode,
            out_lines,
            out_origins,
            out_destinations,
            point_barriers,
            line_barriers,
            polygon_barriers,
            impedance_cutoff,
            destination_count,
            time_units,
            distance_units,
            time_of_day,
            time_zone,
            line_shape_type,
            ignore_invalid_locations,
        )
    )


@tool(
    name="arcgis_pro_calculate_distance_band",
    description="计算使每个要素至少具有指定邻居数的距离阈值，不创建数据集。",
)
def arcgis_pro_calculate_distance_band(
    in_features: str,
    number_of_neighbors: int,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.calculate_distance_band(
            _srv._arcpy(),
            in_features,
            number_of_neighbors,
            distance_method,
            environment,
        )
    )


@tool(
    name="arcgis_pro_generate_spatial_weights_matrix",
    description="以固定 conceptualization 和参数白名单生成新 .swm 空间权重矩阵。",
)
def arcgis_pro_generate_spatial_weights_matrix(
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
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.generate_spatial_weights_matrix(
            _srv._arcpy(),
            in_features,
            unique_id_field,
            output_swm,
            conceptualization,
            distance_method,
            exponent,
            threshold_distance,
            number_of_neighbors,
            row_standardization,
            environment,
        )
    )


@tool(
    name="arcgis_pro_find_point_clusters",
    description="以 DBSCAN/HDBSCAN 聚类点；显式核验 Advanced 许可、距离和时间参数。",
)
def arcgis_pro_find_point_clusters(
    input_points: str,
    output_features: str,
    clustering_method: str,
    minimum_points: int,
    search_distance: str,
    use_time: bool = False,
    search_duration: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.find_point_clusters(
            _srv._arcpy(),
            input_points,
            output_features,
            clustering_method,
            minimum_points,
            search_distance,
            use_time,
            search_duration,
            environment,
        )
    )


@tool(
    name="arcgis_pro_multivariate_clustering",
    description="运行多变量聚类，限制分析字段、初始化方式、簇数量和可选评估表。",
)
def arcgis_pro_multivariate_clustering(
    in_features: str,
    output_features: str,
    analysis_fields: list[str],
    clustering_method: str = "K_MEANS",
    initialization_method: str = "OPTIMIZED_SEED_LOCATIONS",
    initialization_field: str = "",
    number_of_clusters: int | None = None,
    output_table: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.multivariate_clustering(
            _srv._arcpy(),
            in_features,
            output_features,
            analysis_fields,
            clustering_method,
            initialization_method,
            initialization_field,
            number_of_clusters,
            output_table,
            environment,
        )
    )


@tool(
    name="arcgis_pro_spatially_constrained_multivariate_clustering",
    description="运行带空间与规模约束的多变量聚类，并核验要素和评估表输出。",
)
def arcgis_pro_spatially_constrained_multivariate_clustering(
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
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.spatially_constrained_multivariate_clustering(
            _srv._arcpy(),
            in_features,
            output_features,
            output_table,
            analysis_fields,
            size_constraints,
            constraint_field,
            min_constraint,
            max_constraint,
            number_of_clusters,
            spatial_constraints,
            weights_matrix_file,
            number_of_permutations,
            environment,
        )
    )


@tool(
    name="arcgis_pro_generalized_linear_regression",
    description="用固定 model type 和显式解释变量运行广义线性回归。",
)
def arcgis_pro_generalized_linear_regression(
    in_features: str,
    dependent_variable: str,
    model_type: str,
    output_features: str,
    explanatory_variables: list[str],
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.generalized_linear_regression(
            _srv._arcpy(),
            in_features,
            dependent_variable,
            model_type,
            output_features,
            explanatory_variables,
            environment,
        )
    )


@tool(
    name="arcgis_pro_create_space_time_cube",
    description="从时空点创建新 .nc cube，限制时间/距离单位、对齐方式与 summary field 表。",
)
def arcgis_pro_create_space_time_cube(
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
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.create_space_time_cube(
            _srv._arcpy(),
            in_features,
            output_cube,
            time_field,
            time_step_interval,
            distance_interval,
            template_cube,
            time_step_alignment,
            reference_time,
            summary_fields,
            aggregation_shape_type,
            environment,
        )
    )


@tool(
    name="arcgis_pro_emerging_hot_spot_analysis",
    description="对受控时空 cube 执行新兴热点分析，校验邻域距离、时间窗口和 mask。",
)
def arcgis_pro_emerging_hot_spot_analysis(
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
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.emerging_hot_spot_analysis(
            _srv._arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            neighborhood_distance,
            neighborhood_time_step,
            polygon_mask,
            conceptualization,
            number_of_neighbors,
            define_global_window,
            environment,
        )
    )


@tool(
    name="arcgis_pro_time_series_clustering",
    description="聚类时空 cube 的时间序列，并可生成图表表及受控 popup。",
)
def arcgis_pro_time_series_clustering(
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    characteristic_of_interest: str,
    cluster_count: int | None = None,
    output_table_for_charts: str = "",
    shape_characteristics_to_ignore: list[str] | None = None,
    enable_time_series_popups: bool = False,
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.time_series_clustering(
            _srv._arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            characteristic_of_interest,
            cluster_count,
            output_table_for_charts,
            shape_characteristics_to_ignore,
            enable_time_series_popups,
            environment,
        )
    )


@tool(
    name="arcgis_pro_curve_fit_forecast",
    description="对时空 cube 运行 Curve Fit Forecast，限制曲线、验证、异常值和置信度参数。",
)
def arcgis_pro_curve_fit_forecast(
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
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.curve_fit_forecast(
            _srv._arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            output_cube,
            number_of_time_steps_to_forecast,
            curve_type,
            number_for_validation,
            outlier_option,
            level_of_confidence,
            maximum_number_of_outliers,
            environment,
        )
    )


@tool(
    name="arcgis_pro_exponential_smoothing_forecast",
    description="对时空 cube 运行 Exponential Smoothing Forecast 并核验要素与 cube 输出。",
)
def arcgis_pro_exponential_smoothing_forecast(
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
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.exponential_smoothing_forecast(
            _srv._arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            output_cube,
            number_of_time_steps_to_forecast,
            season_length,
            number_for_validation,
            outlier_option,
            level_of_confidence,
            maximum_number_of_outliers,
            environment,
        )
    )


@tool(
    name="arcgis_pro_forest_based_forecast",
    description="对时空 cube 运行 Forest-based Forecast，白名单化树、窗口、深度和抽样参数。",
)
def arcgis_pro_forest_based_forecast(
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
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.forest_based_forecast(
            _srv._arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            output_cube,
            number_of_time_steps_to_forecast,
            time_window,
            number_for_validation,
            number_of_trees,
            minimum_leaf_size,
            maximum_depth,
            sample_size,
            forecast_approach,
            outlier_option,
            level_of_confidence,
            maximum_number_of_outliers,
            environment,
        )
    )


@tool(
    name="arcgis_pro_evaluate_forecasts_by_location",
    description="按位置比较多个预测 cube，使用 ArcPy 真实 EvaluateForecastsByLocation API。",
)
def arcgis_pro_evaluate_forecasts_by_location(
    in_cubes: list[str],
    output_features: str,
    output_cube: str = "",
    evaluate_using_validation_results: str = "USE_VALIDATION",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.spatial_modeling.evaluate_forecasts_by_location(
            _srv._arcpy(),
            in_cubes,
            output_features,
            output_cube,
            evaluate_using_validation_results,
            environment,
        )
    )
