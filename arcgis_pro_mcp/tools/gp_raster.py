"""Checked raster QA, GP raster, hydrology, distance, and mosaic dataset tools."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool


@tool(
    name="arcgis_pro_gp_slope",
    description="",
)
def arcgis_pro_gp_slope(
    in_raster: str,
    out_raster: str,
    output_measurement: str = "DEGREE",
    z_factor: float = 1.0,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_slope(arcpy, in_raster, out_raster, output_measurement, z_factor)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_aspect",
    description="",
)
def arcgis_pro_gp_aspect(in_raster: str, out_raster: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_aspect(arcpy, in_raster, out_raster)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_hillshade",
    description="",
)
def arcgis_pro_gp_hillshade(
    in_raster: str,
    out_raster: str,
    azimuth: float = 315.0,
    altitude: float = 45.0,
    z_factor: float = 1.0,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_hillshade(arcpy, in_raster, out_raster, azimuth, altitude, z_factor)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_reclassify",
    description="",
)
def arcgis_pro_gp_reclassify(
    in_raster: str,
    reclass_field: str,
    remap: str,
    out_raster: str,
    missing_values: str = "DATA",
    remap_mode: str = "RANGE",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_reclassify(arcpy, in_raster, reclass_field, remap, out_raster, missing_values, remap_mode)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_extract_by_mask",
    description="",
)
def arcgis_pro_gp_extract_by_mask(
    in_raster: str,
    in_mask_data: str,
    out_raster: str,
    environment: dict[str, Any] | None = None,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_extract_by_mask(arcpy, in_raster, in_mask_data, out_raster, environment)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_extract_by_attributes",
    description="",
)
def arcgis_pro_gp_extract_by_attributes(
    in_raster: str,
    where_clause: str,
    out_raster: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_extract_by_attributes(arcpy, in_raster, where_clause, out_raster)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_zonal_statistics_as_table",
    description="",
)
def arcgis_pro_gp_zonal_statistics_as_table(
    in_zone_data: str,
    zone_field: str,
    in_value_raster: str,
    out_table: str,
    statistics_type: str = "ALL",
    ignore_nodata: str = "DATA",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_zonal_statistics_as_table(
        arcpy, in_zone_data, zone_field, in_value_raster, out_table, statistics_type, ignore_nodata
    )
    return _srv._json_dumps({"ok": True, "out_table": _srv.normalize_path(out_table)})


@tool(
    name="arcgis_pro_gp_kernel_density",
    description="",
)
def arcgis_pro_gp_kernel_density(
    in_features: str,
    population_field: str,
    out_raster: str,
    cell_size: float | None = None,
    search_radius: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_kernel_density(
        arcpy, in_features, population_field, out_raster, cell_size, search_radius
    )
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_point_density",
    description="",
)
def arcgis_pro_gp_point_density(
    in_features: str,
    population_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_point_density(arcpy, in_features, population_field, out_raster, cell_size)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_idw",
    description="",
)
def arcgis_pro_gp_idw(
    in_point_features: str,
    z_field: str,
    out_raster: str,
    cell_size: float | None = None,
    power: float = 2.0,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_idw(arcpy, in_point_features, z_field, out_raster, cell_size, power)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_kriging",
    description="",
)
def arcgis_pro_gp_kriging(
    in_point_features: str,
    z_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_kriging(arcpy, in_point_features, z_field, out_raster, cell_size)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_topo_to_raster",
    description="",
)
def arcgis_pro_gp_topo_to_raster(
    in_topo_features: str,
    out_raster: str,
    cell_size: float | None = None,
    elevation_field: str = "VALUE",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_topo_to_raster(
        arcpy,
        in_topo_features,
        out_raster,
        cell_size,
        elevation_field,
    )
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_raster_to_polygon",
    description="",
)
def arcgis_pro_gp_raster_to_polygon(
    in_raster: str,
    out_polygon_features: str,
    simplify: bool = True,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_raster_to_polygon(arcpy, in_raster, out_polygon_features, simplify)
    return _srv._json_dumps(
        {"ok": True, "out_polygon_features": _srv.normalize_path(out_polygon_features)},
    )


@tool(
    name="arcgis_pro_gp_polygon_to_raster",
    description="",
)
def arcgis_pro_gp_polygon_to_raster(
    in_features: str,
    value_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_polygon_to_raster(arcpy, in_features, value_field, out_raster, cell_size)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_feature_to_raster",
    description="",
)
def arcgis_pro_gp_feature_to_raster(
    in_features: str,
    field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_feature_to_raster(arcpy, in_features, field, out_raster, cell_size)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_raster_calculator",
    description="",
)
def arcgis_pro_gp_raster_calculator(expression: str, out_raster: str, input_rasters: dict[str, str] | None = None) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_raster_calculator(arcpy, expression, out_raster, input_rasters)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_mosaic_to_new_raster",
    description="",
)
def arcgis_pro_gp_mosaic_to_new_raster(
    input_rasters: list[str],
    output_location: str,
    raster_dataset_name: str,
    number_of_bands: int = 1,
    pixel_type: str = "32_BIT_FLOAT",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_mosaic_to_new_raster(
        arcpy, input_rasters, output_location, raster_dataset_name, number_of_bands, pixel_type
    )
    return _srv._json_dumps(
        {"ok": True, "output": f"{_srv.normalize_path(output_location)}\\{raster_dataset_name}"},
    )


@tool(name="arcgis_pro_analysis_asset_info", description="Register a physical file version and GIS role for checked clipping. File mode; no GUI selection.")
def arcgis_pro_analysis_asset_info(dataset_path: str, role: str, identity_basis: str, vector_layer: str = "") -> str:
    return _srv._json_dumps(_srv.raster_checked.asset_info(dataset_path, role, identity_basis, vector_layer))


@tool(name="arcgis_pro_analysis_result_status", description="Revalidate checked-run evidence and file versions; never infer success from file existence.")
def arcgis_pro_analysis_result_status(run_id: str) -> str:
    return _srv._json_dumps(_srv.raster_checked.result_status(run_id))


@tool(name="arcgis_pro_gp_clip_raster_checked", description="File-mode single-band north-up GeoTIFF clip with explicit AOI, reference grid, immutable run ID and full-domain quality checks.")
def arcgis_pro_gp_clip_raster_checked(
    run_id: str, source_asset: dict[str, Any], grid_asset: dict[str, Any],
    clip_mode: str, minimum_coverage: float, boundary_asset: dict[str, Any] | None = None,
    rectangle: list[float] | None = None, rectangle_crs: str = "",
    missing_data_reason: str = "", validity: dict[str, Any] | None = None,
    maximum_output_cells: int = 100_000_000,
) -> str:
    return _srv._json_dumps(_srv.raster_checked.run_clip_checked(
        _srv._arcpy(), run_id, source_asset, grid_asset, clip_mode, minimum_coverage,
        boundary_asset, rectangle, rectangle_crs, missing_data_reason, validity, maximum_output_cells,
    ))


@tool(
    name="arcgis_pro_gp_clip_raster",
    description="",
)
def arcgis_pro_gp_clip_raster(
    in_raster: str,
    out_raster: str,
    rectangle: str = "",
    in_template_dataset: str = "",
    clipping_geometry: bool = False,
    environment: dict[str, Any] | None = None,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_clip_raster(
        arcpy, in_raster, out_raster, rectangle, in_template_dataset, clipping_geometry, environment
    )
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_resample",
    description="",
)
def arcgis_pro_gp_resample(
    in_raster: str,
    out_raster: str,
    cell_size: str,
    resampling_type: str = "NEAREST",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_resample(arcpy, in_raster, out_raster, cell_size, resampling_type)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_project_raster",
    description="",
)
def arcgis_pro_gp_project_raster(
    in_raster: str,
    out_raster: str,
    out_wkid: int,
    resampling_type: str = "NEAREST",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_project_raster(arcpy, in_raster, out_raster, out_wkid, resampling_type)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_gp_nibble",
    description="",
)
def arcgis_pro_gp_nibble(
    in_raster: str,
    in_mask_raster: str,
    out_raster: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_raster.run_nibble(arcpy, in_raster, in_mask_raster, out_raster)
    return _srv._json_dumps({"ok": True, "out_raster": _srv.normalize_path(out_raster)})


@tool(
    name="arcgis_pro_raster_info",
    description="读取栅格格式、波段、像元类型、范围、空间参考和基本统计属性。",
)
def arcgis_pro_raster_info(raster_path: str) -> str:
    return _srv._json_dumps(_srv.raster_runtime.raster_info(_srv._arcpy(), raster_path))


@tool(
    name="arcgis_pro_raster_fill",
    description="在一次受控 Spatial Analyst 签出和局部环境中填洼，并核验输出栅格。",
)
def arcgis_pro_raster_fill(
    in_surface_raster: str,
    out_surface_raster: str,
    z_limit: float | None = None,
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_runtime.run_fill(
            _srv._arcpy(),
            in_surface_raster,
            out_surface_raster,
            z_limit,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_flow_direction",
    description="计算 D8/MFD/DINF 流向，可选输出 drop raster，并核验所有输出。",
)
def arcgis_pro_raster_flow_direction(
    in_surface_raster: str,
    out_flow_direction_raster: str,
    force_flow: str = "NORMAL",
    flow_direction_type: str = "D8",
    out_drop_raster: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_runtime.run_flow_direction(
            _srv._arcpy(),
            in_surface_raster,
            out_flow_direction_raster,
            force_flow,
            flow_direction_type,
            out_drop_raster,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_flow_accumulation",
    description="计算流量累积栅格，可选权重栅格和受控分析环境，并核验输出。",
)
def arcgis_pro_raster_flow_accumulation(
    in_flow_direction_raster: str,
    out_accumulation_raster: str,
    in_weight_raster: str = "",
    data_type: str = "FLOAT",
    flow_direction_type: str = "D8",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_runtime.run_flow_accumulation(
            _srv._arcpy(),
            in_flow_direction_raster,
            out_accumulation_raster,
            in_weight_raster,
            data_type,
            flow_direction_type,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_snap_pour_point",
    description="把倾泻点吸附到高累积像元，并在 Spatial Analyst 会话中核验输出。",
)
def arcgis_pro_raster_snap_pour_point(
    in_pour_point_data: str,
    in_accumulation_raster: str,
    out_raster: str,
    snap_distance: float,
    pour_point_field: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_runtime.run_snap_pour_point(
            _srv._arcpy(),
            in_pour_point_data,
            in_accumulation_raster,
            out_raster,
            snap_distance,
            pour_point_field,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_watershed",
    description="根据流向栅格和倾泻点生成分水岭栅格，并核验输出。",
)
def arcgis_pro_raster_watershed(
    in_flow_direction_raster: str,
    in_pour_point_data: str,
    out_raster: str,
    pour_point_field: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_runtime.run_watershed(
            _srv._arcpy(),
            in_flow_direction_raster,
            in_pour_point_data,
            out_raster,
            pour_point_field,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_calculate_statistics",
    description="在原位计算栅格统计，限制 skip factor、忽略值、AOI 和局部环境。",
)
def arcgis_pro_raster_calculate_statistics(
    in_raster_dataset: str,
    x_skip_factor: int = 1,
    y_skip_factor: int = 1,
    ignore_values: list[int] | None = None,
    skip_existing: str = "OVERWRITE",
    area_of_interest: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.calculate_statistics(
            _srv._arcpy(),
            in_raster_dataset,
            x_skip_factor,
            y_skip_factor,
            ignore_values,
            skip_existing,
            area_of_interest,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_build_pyramids",
    description="构建或更新栅格金字塔；删除金字塔需破坏性门禁和源路径精确回显。",
)
def arcgis_pro_raster_build_pyramids(
    in_raster_dataset: str,
    pyramid_level: int = -1,
    skip_first: bool = False,
    resample_technique: str = "NEAREST",
    compression_type: str = "DEFAULT",
    compression_quality: int = 75,
    skip_existing: str = "OVERWRITE",
    confirm_delete_pyramids: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.build_pyramids(
            _srv._arcpy(),
            in_raster_dataset,
            pyramid_level,
            skip_first,
            resample_technique,
            compression_type,
            compression_quality,
            skip_existing,
            confirm_delete_pyramids,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_set_nodata",
    description="按波段设置 NoData；仅作用于精确回显的受控栅格。",
)
def arcgis_pro_raster_set_nodata(
    in_raster: str,
    nodata_values: list[list[Any]],
    confirm_raster_path: str,
    data_type: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.set_raster_nodata(
            _srv._arcpy(),
            in_raster,
            nodata_values,
            confirm_raster_path,
            data_type,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_copy",
    description="用固定签名 CopyRaster 复制到 GP 输出根，拒绝隐式覆盖并核验输出。",
)
def arcgis_pro_raster_copy(
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
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.copy_raster(
            _srv._arcpy(),
            in_raster,
            out_rasterdataset,
            background_value,
            nodata_value,
            onebit_to_eightbit,
            colormap_to_rgb,
            pixel_type,
            scale_pixel_value,
            rgb_to_colormap,
            output_format,
            apply_transform,
            process_as_multidimensional,
            build_multidimensional_transpose,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_focal_statistics",
    description="以受约束邻域和统计类型运行 Focal Statistics，并核验新输出。",
)
def arcgis_pro_raster_focal_statistics(
    in_raster: str,
    out_raster: str,
    neighborhood: dict[str, Any] | None = None,
    statistics_type: str = "MEAN",
    ignore_nodata: str = "DATA",
    percentile_value: float = 90,
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.focal_statistics(
            _srv._arcpy(),
            in_raster,
            out_raster,
            neighborhood,
            statistics_type,
            ignore_nodata,
            percentile_value,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_cell_statistics",
    description="对受控栅格或常量列表运行 Cell Statistics，限制统计、多波段和百分位参数。",
)
def arcgis_pro_raster_cell_statistics(
    in_rasters_or_constants: list[Any],
    out_raster: str,
    statistics_type: str = "MEAN",
    ignore_nodata: str = "DATA",
    process_as_multiband: str = "SINGLE_BAND",
    percentile_value: float = 90,
    percentile_interpolation_type: str = "AUTO_DETECT",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.cell_statistics(
            _srv._arcpy(),
            in_rasters_or_constants,
            out_raster,
            statistics_type,
            ignore_nodata,
            process_as_multiband,
            percentile_value,
            percentile_interpolation_type,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_con",
    description="以受控栅格/常量和可选 SQL 条件执行 Con 条件计算。",
)
def arcgis_pro_raster_con(
    in_conditional_raster: str,
    in_true_raster_or_constant: Any,
    out_raster: str,
    in_false_raster_or_constant: Any = None,
    where_clause: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.conditional_con(
            _srv._arcpy(),
            in_conditional_raster,
            in_true_raster_or_constant,
            out_raster,
            in_false_raster_or_constant,
            where_clause,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_set_null",
    description="以受控条件栅格、false 值和可选 SQL 条件执行 SetNull。",
)
def arcgis_pro_raster_set_null(
    in_conditional_raster: str,
    in_false_raster_or_constant: Any,
    out_raster: str,
    where_clause: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.set_null(
            _srv._arcpy(),
            in_conditional_raster,
            in_false_raster_or_constant,
            out_raster,
            where_clause,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_euclidean_distance",
    description="计算欧氏距离及可选方向/反向方向栅格，逐项核验全部输出。",
)
def arcgis_pro_raster_euclidean_distance(
    in_source_data: str,
    out_distance_raster: str,
    maximum_distance: float | None = None,
    cell_size: float | str | None = None,
    out_direction_raster: str = "",
    distance_method: str = "PLANAR",
    in_barrier_data: str = "",
    out_back_direction_raster: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.euclidean_distance(
            _srv._arcpy(),
            in_source_data,
            out_distance_raster,
            maximum_distance,
            cell_size,
            out_direction_raster,
            distance_method,
            in_barrier_data,
            out_back_direction_raster,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_distance_accumulation",
    description="计算距离累积及可选 back/source direction/location 派生栅格并核验。",
)
def arcgis_pro_raster_distance_accumulation(
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
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.distance_accumulation(
            _srv._arcpy(),
            in_source_data,
            out_distance_accumulation_raster,
            in_barrier_data,
            in_surface_raster,
            in_cost_raster,
            in_vertical_raster,
            in_horizontal_raster,
            out_back_direction_raster,
            out_source_direction_raster,
            out_source_location_raster,
            source_initial_accumulation,
            source_maximum_accumulation,
            source_cost_multiplier,
            source_direction,
            distance_method,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_optimal_path_as_line",
    description="从距离累积和反向方向栅格生成最优路径线并核验输出。",
)
def arcgis_pro_raster_optimal_path_as_line(
    in_destination_data: str,
    in_distance_accumulation_raster: str,
    in_back_direction_raster: str,
    out_polyline_features: str,
    destination_field: str = "",
    path_type: str = "EACH_ZONE",
    create_network_paths: str = "DESTINATIONS_TO_SOURCES",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.optimal_path_as_line(
            _srv._arcpy(),
            in_destination_data,
            in_distance_accumulation_raster,
            in_back_direction_raster,
            out_polyline_features,
            destination_field,
            path_type,
            create_network_paths,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_stream_order",
    description="用 STRAHLER/SHREVE 方法计算河网等级栅格。",
)
def arcgis_pro_raster_stream_order(
    in_stream_raster: str,
    in_flow_direction_raster: str,
    out_raster: str,
    order_method: str = "STRAHLER",
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.stream_order(
            _srv._arcpy(),
            in_stream_raster,
            in_flow_direction_raster,
            out_raster,
            order_method,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_stream_to_feature",
    description="把河网栅格转换为新折线要素类并核验。",
)
def arcgis_pro_raster_stream_to_feature(
    in_stream_raster: str,
    in_flow_direction_raster: str,
    out_polyline_features: str,
    simplify: bool = True,
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.stream_to_feature(
            _srv._arcpy(),
            in_stream_raster,
            in_flow_direction_raster,
            out_polyline_features,
            simplify,
            environment,
        )
    )


@tool(
    name="arcgis_pro_raster_basin",
    description="从 D8 流向栅格划分流域并核验新输出。",
)
def arcgis_pro_raster_basin(
    in_flow_direction_raster: str,
    out_raster: str,
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.basin(
            _srv._arcpy(), in_flow_direction_raster, out_raster, environment
        )
    )


@tool(
    name="arcgis_pro_create_mosaic_dataset",
    description="在 GP 输出根内的新地理数据库工作空间创建 mosaic dataset，并核验派生输出。",
)
def arcgis_pro_create_mosaic_dataset(
    in_workspace: str,
    in_mosaicdataset_name: str,
    coordinate_system: int | str,
    num_bands: int | None = None,
    pixel_type: str = "",
    product_definition: str = "NONE",
    product_band_definitions: list[list[Any]] | None = None,
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.create_mosaic_dataset(
            _srv._arcpy(),
            in_workspace,
            in_mosaicdataset_name,
            coordinate_system,
            num_bands,
            pixel_type,
            product_definition,
            product_band_definitions,
            environment,
        )
    )


@tool(
    name="arcgis_pro_add_rasters_to_mosaic_dataset",
    description="把受控路径的栅格加入 mosaic dataset，并限制重复项、金字塔、统计和缩略图选项。",
)
def arcgis_pro_add_rasters_to_mosaic_dataset(
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
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.add_rasters_to_mosaic_dataset(
            _srv._arcpy(),
            in_mosaic_dataset,
            input_paths,
            raster_type,
            update_cellsize_ranges,
            update_boundary,
            update_overviews,
            include_subfolders,
            duplicate_items_action,
            build_pyramids_for_sources,
            calculate_statistics_for_sources,
            build_thumbnails,
            filter_expression,
            operation_description,
            environment,
        )
    )


@tool(
    name="arcgis_pro_build_mosaic_footprints",
    description="以固定参数更新 mosaic dataset footprints，并读回核验目标。",
)
def arcgis_pro_build_mosaic_footprints(
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
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.build_mosaic_footprints(
            _srv._arcpy(),
            in_mosaic_dataset,
            where_clause,
            reset_footprint,
            min_data_value,
            max_data_value,
            approx_num_vertices,
            shrink_distance,
            maintain_edges,
            skip_derived_images,
            update_boundary,
            request_size,
            min_region_size,
            simplification_method,
            edge_tolerance,
            max_sliver_size,
            min_thinness_ratio,
            environment,
        )
    )


@tool(
    name="arcgis_pro_build_mosaic_overviews",
    description="定义并生成缺失或过期 mosaic dataset overviews，限制为受控目标。",
)
def arcgis_pro_build_mosaic_overviews(
    in_mosaic_dataset: str,
    where_clause: str = "",
    define_missing_tiles: bool = True,
    generate_overviews: bool = True,
    generate_missing_images: bool = True,
    regenerate_stale_images: bool = True,
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.build_mosaic_overviews(
            _srv._arcpy(),
            in_mosaic_dataset,
            where_clause,
            define_missing_tiles,
            generate_overviews,
            generate_missing_images,
            regenerate_stale_images,
            environment,
        )
    )


@tool(
    name="arcgis_pro_remove_rasters_from_mosaic_dataset",
    description="删除精确 where 命中的 mosaic items；需破坏性门禁、目标/条件回显和预期数量。",
)
def arcgis_pro_remove_rasters_from_mosaic_dataset(
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
) -> str:
    return _srv._json_dumps(
        _srv.raster_advanced.remove_rasters_from_mosaic_dataset(
            _srv._arcpy(),
            in_mosaic_dataset,
            where_clause,
            expected_item_count,
            confirm_mosaic_dataset,
            confirm_where_clause,
            update_boundary,
            mark_overview_items,
            delete_overview_images,
            delete_item_cache,
            update_cellsize_ranges,
            environment,
        )
    )
