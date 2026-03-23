"""Allowlisted arcpy.management / analysis helpers (no dynamic tool names)."""

from __future__ import annotations

from typing import Any

# Esri GetRasterProperties property_type common values
_RASTER_PROPERTIES: frozenset[str] = frozenset(
    {
        "MINIMUM",
        "MAXIMUM",
        "MEAN",
        "STD",
        "UNIQUEVALUECOUNT",
        "TOP",
        "LEFT",
        "RIGHT",
        "BOTTOM",
        "COLUMNCOUNT",
        "ROWCOUNT",
        "VALUETYPE",
        "BANDCOUNT",
        "CELLSIZEX",
        "CELLSIZEY",
        "MEANCELLHEIGHT",
        "MEANCELLWIDTH",
        "ALL",
    },
)


def gp_get_count_layer(arcpy: Any, layer: Any) -> str:
    r = arcpy.management.GetCount(layer)
    try:
        return str(r.getOutput(0))
    except Exception:  # noqa: BLE001
        return str(r)


def gp_get_count(arcpy: Any, dataset_path: str) -> str:
    r = arcpy.management.GetCount(dataset_path)
    try:
        return str(r.getOutput(0))
    except Exception:  # noqa: BLE001
        return str(r)


def gp_get_raster_property(arcpy: Any, raster_path: str, property_type: str) -> str:
    pt = property_type.strip().upper()
    if pt not in _RASTER_PROPERTIES:
        raise RuntimeError(
            f"不支持的 property_type：{property_type!r}，可选：{sorted(_RASTER_PROPERTIES)}"
        )
    r = arcpy.management.GetRasterProperties(raster_path, pt)
    try:
        return str(r.getOutput(0))
    except Exception:  # noqa: BLE001
        return str(r)


def gp_get_cell_value(arcpy: Any, raster_path: str, location_xy: str, band_index: int | None) -> str:
    loc = location_xy.strip()
    parts = loc.replace(",", " ").split()
    if len(parts) != 2:
        raise RuntimeError("location_xy 须为两个数字，如 \"452000 5412000\" 或 \"-118.2 34.05\"")
    try:
        float(parts[0])
        float(parts[1])
    except ValueError as e:
        raise RuntimeError("location_xy 坐标无法解析为数字") from e
    if band_index is None:
        r = arcpy.management.GetCellValue(raster_path, loc, "#")
    else:
        r = arcpy.management.GetCellValue(raster_path, loc, str(int(band_index)))
    try:
        return str(r.getOutput(0))
    except Exception:  # noqa: BLE001
        return str(r)


def gp_test_schema_lock(arcpy: Any, dataset_path: str) -> str:
    r = arcpy.management.TestSchemaLock(dataset_path)
    try:
        return str(r.getOutput(0))
    except Exception:  # noqa: BLE001
        return str(r)


def list_registered_gp_tools() -> list[dict[str, str]]:
    return [
        {"name": "arcgis_pro_gp_get_count", "role": "返回要素类/表等的记录数（GetCount）"},
        {"name": "arcgis_pro_gp_get_raster_property", "role": "读取栅格属性（GetRasterProperties）"},
        {"name": "arcgis_pro_gp_get_cell_value", "role": "栅格像元值（GetCellValue）"},
        {"name": "arcgis_pro_gp_test_schema_lock", "role": "测试方案锁（TestSchemaLock）"},
        {"name": "arcgis_pro_gp_buffer", "role": "Buffer 缓冲区"},
        {"name": "arcgis_pro_gp_clip", "role": "Clip 裁剪"},
        {"name": "arcgis_pro_gp_analysis_select", "role": "analysis.Select 子集"},
        {"name": "arcgis_pro_gp_copy_features", "role": "CopyFeatures 复制要素"},
        {"name": "arcgis_pro_gp_dissolve", "role": "Dissolve 融合"},
        {"name": "arcgis_pro_gp_intersect", "role": "Intersect 相交"},
        {"name": "arcgis_pro_gp_union", "role": "Union 联合"},
        {"name": "arcgis_pro_gp_erase", "role": "Erase 擦除"},
        {"name": "arcgis_pro_gp_spatial_join", "role": "SpatialJoin 空间连接"},
        {"name": "arcgis_pro_gp_statistics", "role": "Statistics 汇总统计"},
        {"name": "arcgis_pro_gp_frequency", "role": "Frequency 频率分析"},
        {"name": "arcgis_pro_gp_table_select", "role": "TableSelect 表筛选"},
        {"name": "arcgis_pro_gp_merge", "role": "Merge 合并"},
        {"name": "arcgis_pro_gp_project", "role": "Project 投影变换"},
        {"name": "arcgis_pro_gp_add_field", "role": "AddField 添加字段"},
        {"name": "arcgis_pro_gp_delete_field", "role": "DeleteField 删除字段"},
        {"name": "arcgis_pro_gp_export_features", "role": "ExportFeatures 导出要素"},
        {"name": "arcgis_pro_gp_export_table", "role": "ExportTable 导出表"},
        {"name": "arcgis_pro_gp_table_to_table", "role": "TableToTable 表转表"},
        {"name": "arcgis_pro_gp_near", "role": "Near 最近邻"},
        {"name": "arcgis_pro_gp_generate_near_table", "role": "GenerateNearTable 邻近表"},
        {"name": "arcgis_pro_gp_calculate_field", "role": "CalculateField 字段计算器"},
        {"name": "arcgis_pro_gp_calculate_geometry", "role": "CalculateGeometryAttributes 计算几何"},
        {"name": "arcgis_pro_gp_append", "role": "Append 追加要素"},
        {"name": "arcgis_pro_gp_delete_features", "role": "DeleteFeatures 删除要素"},
        {"name": "arcgis_pro_gp_truncate_table", "role": "TruncateTable 清空表"},
        {"name": "arcgis_pro_gp_create_feature_class", "role": "CreateFeatureclass 创建要素类"},
        {"name": "arcgis_pro_gp_create_table", "role": "CreateTable 创建表"},
        {"name": "arcgis_pro_gp_create_file_gdb", "role": "CreateFileGDB 创建文件数据库"},
        {"name": "arcgis_pro_gp_create_feature_dataset", "role": "CreateFeatureDataset 创建要素数据集"},
        {"name": "arcgis_pro_gp_copy_feature_class", "role": "CopyFeatures 复制要素类"},
        {"name": "arcgis_pro_gp_rename_dataset", "role": "Rename 重命名数据集"},
        {"name": "arcgis_pro_gp_delete_dataset", "role": "Delete 删除数据集"},
        {"name": "arcgis_pro_gp_alter_field", "role": "AlterField 修改字段属性"},
        {"name": "arcgis_pro_gp_import_csv_to_table", "role": "TableToTable CSV 导入"},
        {"name": "arcgis_pro_gp_xy_table_to_point", "role": "XYTableToPoint XY 表转点"},
        {"name": "arcgis_pro_gp_json_to_features", "role": "JSONToFeatures GeoJSON 转要素"},
        {"name": "arcgis_pro_gp_features_to_json", "role": "FeaturesToJSON 要素转 GeoJSON"},
        {"name": "arcgis_pro_gp_kml_to_layer", "role": "KMLToLayer KML 转图层"},
        {"name": "arcgis_pro_gp_excel_to_table", "role": "ExcelToTable Excel 导入"},
        {"name": "arcgis_pro_gp_table_to_excel", "role": "TableToExcel 表导出 Excel"},
        {"name": "arcgis_pro_gp_feature_class_to_shapefile", "role": "FeatureClassToShapefile 导出 SHP"},
        {"name": "arcgis_pro_gp_multiple_ring_buffer", "role": "MultipleRingBuffer 多环缓冲"},
        {"name": "arcgis_pro_gp_feature_to_point", "role": "FeatureToPoint 面转点"},
        {"name": "arcgis_pro_gp_feature_to_line", "role": "FeatureToLine 面转线"},
        {"name": "arcgis_pro_gp_points_to_line", "role": "PointsToLine 点转线"},
        {"name": "arcgis_pro_gp_polygon_to_line", "role": "PolygonToLine 多边形转线"},
        {"name": "arcgis_pro_gp_minimum_bounding_geometry", "role": "MinimumBoundingGeometry 最小外接"},
        {"name": "arcgis_pro_gp_convex_hull", "role": "ConvexHull 凸包"},
        {"name": "arcgis_pro_gp_split_by_attributes", "role": "SplitByAttributes 按属性分割"},
        {"name": "arcgis_pro_gp_identity", "role": "Identity 标识叠加"},
        {"name": "arcgis_pro_gp_symmetrical_difference", "role": "SymDiff 对称差"},
        {"name": "arcgis_pro_gp_count_overlapping_features", "role": "CountOverlappingFeatures 重叠计数"},
        {"name": "arcgis_pro_gp_repair_geometry", "role": "RepairGeometry 修复几何"},
        {"name": "arcgis_pro_gp_check_geometry", "role": "CheckGeometry 检查几何"},
        {"name": "arcgis_pro_gp_eliminate", "role": "EliminatePolygonPart 消除细碎面"},
        {"name": "arcgis_pro_gp_multipart_to_singlepart", "role": "MultipartToSinglepart 多转单"},
        {"name": "arcgis_pro_gp_aggregate_polygons", "role": "AggregatePolygons 聚合面"},
        {"name": "arcgis_pro_gp_slope", "role": "Slope 坡度"},
        {"name": "arcgis_pro_gp_aspect", "role": "Aspect 坡向"},
        {"name": "arcgis_pro_gp_hillshade", "role": "HillShade 山影"},
        {"name": "arcgis_pro_gp_reclassify", "role": "Reclassify 重分类"},
        {"name": "arcgis_pro_gp_extract_by_mask", "role": "ExtractByMask 按掩膜提取"},
        {"name": "arcgis_pro_gp_extract_by_attributes", "role": "ExtractByAttributes 按属性提取栅格"},
        {"name": "arcgis_pro_gp_zonal_statistics_as_table", "role": "ZonalStatisticsAsTable 分区统计"},
        {"name": "arcgis_pro_gp_kernel_density", "role": "KernelDensity 核密度"},
        {"name": "arcgis_pro_gp_point_density", "role": "PointDensity 点密度"},
        {"name": "arcgis_pro_gp_idw", "role": "IDW 反距离权重插值"},
        {"name": "arcgis_pro_gp_kriging", "role": "Kriging 克里金插值"},
        {"name": "arcgis_pro_gp_topo_to_raster", "role": "TopoToRaster 地形转栅格"},
        {"name": "arcgis_pro_gp_raster_to_polygon", "role": "RasterToPolygon 栅格转面"},
        {"name": "arcgis_pro_gp_polygon_to_raster", "role": "PolygonToRaster 面转栅格"},
        {"name": "arcgis_pro_gp_feature_to_raster", "role": "FeatureToRaster 要素转栅格"},
        {"name": "arcgis_pro_gp_raster_calculator", "role": "RasterCalculator 栅格计算器"},
        {"name": "arcgis_pro_gp_mosaic_to_new_raster", "role": "MosaicToNewRaster 镶嵌栅格"},
        {"name": "arcgis_pro_gp_clip_raster", "role": "Clip 裁剪栅格"},
        {"name": "arcgis_pro_gp_resample", "role": "Resample 栅格重采样"},
        {"name": "arcgis_pro_gp_project_raster", "role": "ProjectRaster 栅格投影变换"},
        {"name": "arcgis_pro_gp_nibble", "role": "Nibble 栅格填充"},
        {"name": "arcgis_pro_gp_run_tool", "role": "通用 GP 执行引擎"},
        {"name": "arcgis_pro_gp_get_messages", "role": "获取 GP 执行消息"},
        {"name": "arcgis_pro_gp_list_toolboxes", "role": "列出可用工具箱"},
        {"name": "arcgis_pro_gp_list_tools_in_toolbox", "role": "列出工具箱中的工具"},
        {"name": "arcgis_pro_gp_validate_topology", "role": "ValidateTopology 验证拓扑"},
    ]
