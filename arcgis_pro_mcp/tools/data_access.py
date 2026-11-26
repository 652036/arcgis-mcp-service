"""Describe, workspace listing, Data Access cursors, metadata, geocoding, and LiDAR tools."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool


@tool(
    name="arcgis_pro_describe",
    description="",
)
def arcgis_pro_describe(dataset_path: str) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    try:
        summary = _srv._describe_summary(arcpy, p)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(_srv._safe_error(ex, 800)) from ex
    return _srv._json_dumps({"dataset_path": p, "describe": summary})


@tool(
    name="arcgis_pro_list_fields",
    description="",
)
def arcgis_pro_list_fields(dataset_path: str) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    rows: list[dict[str, Any]] = []
    try:
        for f in arcpy.ListFields(p):
            row: dict[str, Any] = {"name": f.name, "type": f.type}
            for attr in ("aliasName", "length", "precision", "scale", "isNullable", "editable", "required"):
                try:
                    row[attr] = getattr(f, attr, None)
                except Exception:  # noqa: BLE001
                    pass
            try:
                dom = f.domain
                if dom:
                    row["domain"] = str(dom)[:500]
            except Exception:  # noqa: BLE001
                pass
            rows.append(row)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(_srv._safe_error(ex, 800)) from ex
    return _srv._json_dumps({"dataset_path": p, "fields": rows, "field_count": len(rows)})


@tool(
    name="arcgis_pro_workspace_list_feature_classes",
    description="",
)
def arcgis_pro_workspace_list_feature_classes(
    workspace_path: str,
    feature_dataset: str = "",
    feature_type: str = "",
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _srv._arcpy()
    ws = _srv.validate_input_path_optional(workspace_path, "workspace_path")
    names = _srv.workspace_listing.list_feature_classes(
        arcpy, ws, feature_dataset, feature_type, wild_card, max_items
    )
    return _srv._json_dumps({"workspace_path": ws, "feature_classes": names})


@tool(
    name="arcgis_pro_workspace_list_rasters",
    description="",
)
def arcgis_pro_workspace_list_rasters(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _srv._arcpy()
    ws = _srv.validate_input_path_optional(workspace_path, "workspace_path")
    names = _srv.workspace_listing.list_rasters(arcpy, ws, wild_card, max_items)
    return _srv._json_dumps({"workspace_path": ws, "rasters": names})


@tool(
    name="arcgis_pro_workspace_list_tables",
    description="",
)
def arcgis_pro_workspace_list_tables(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _srv._arcpy()
    ws = _srv.validate_input_path_optional(workspace_path, "workspace_path")
    names = _srv.workspace_listing.list_tables(arcpy, ws, wild_card, max_items)
    return _srv._json_dumps({"workspace_path": ws, "tables": names})


@tool(
    name="arcgis_pro_da_table_sample",
    description="",
)
def arcgis_pro_da_table_sample(
    dataset_path: str,
    fields: list[str],
    where_clause: str = "",
    max_rows: int = 50,
    include_shape_wkt: bool = False,
) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    rows = _srv.da_read.table_sample(
        arcpy, p, fields, where_clause, max_rows, include_shape_wkt
    )
    return _srv._json_dumps({"dataset_path": p, "row_count": len(rows), "rows": rows})


@tool(
    name="arcgis_pro_da_query_rows",
    description="",
)
def arcgis_pro_da_query_rows(
    dataset_path: str,
    fields: list[str],
    where_clause: str = "",
    order_by: str = "",
    max_rows: int = 100,
    offset: int = 0,
    include_shape_wkt: bool = False,
) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    rows = _srv.da_read.query_rows(
        arcpy, p, fields, where_clause, order_by, max_rows, offset, include_shape_wkt
    )
    return _srv._json_dumps(
        {
            "dataset_path": p,
            "field_count": len([f for f in fields if f.strip()]),
            "row_count": len(rows),
            "rows": rows,
        },
    )


@tool(
    name="arcgis_pro_da_distinct_values",
    description="",
)
def arcgis_pro_da_distinct_values(
    dataset_path: str,
    field_name: str,
    where_clause: str = "",
    max_values: int = 100,
    max_rows_scanned: int = 50000,
) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    vals = _srv.da_read.distinct_values(
        arcpy, p, field_name, where_clause, max_values, max_rows_scanned
    )
    return _srv._json_dumps(
        {
            "dataset_path": p,
            "field_name": field_name.strip(),
            "value_count": len(vals),
            "values": vals,
        },
    )


@tool(
    name="arcgis_pro_da_update_field_constant",
    description="",
)
def arcgis_pro_da_update_field_constant(
    dataset_path: str,
    field_name: str,
    value_string: str,
    where_clause: str = "",
    max_rows_updated: int = 1000,
) -> str:
    arcpy = _srv._arcpy()
    n, truncated = _srv.da_write.update_field_constant(
        arcpy,
        dataset_path,
        field_name,
        value_string,
        where_clause,
        max_rows_updated,
    )
    return _srv._json_dumps(
        {
            "ok": True,
            "dataset_path": _srv.normalize_path(dataset_path),
            "field_name": field_name.strip(),
            "rows_updated": n,
            "truncated": truncated,
        },
    )


@tool(
    name="arcgis_pro_da_insert_features",
    description="",
)
def arcgis_pro_da_insert_features(
    dataset_path: str,
    fields: list[str],
    rows: list[list[Any]],
) -> str:
    arcpy = _srv._arcpy()
    n = _srv.da_write.insert_features(arcpy, dataset_path, fields, rows)
    return _srv._json_dumps(
        {"ok": True, "dataset_path": _srv.normalize_path(dataset_path), "rows_inserted": n},
    )


@tool(
    name="arcgis_pro_da_update_features",
    description="",
)
def arcgis_pro_da_update_features(
    dataset_path: str,
    updates: dict[str, Any],
    where_clause: str = "",
    max_rows_updated: int = 1000,
) -> str:
    arcpy = _srv._arcpy()
    n, truncated = _srv.da_write.update_features(
        arcpy, dataset_path, updates, where_clause, max_rows_updated
    )
    return _srv._json_dumps(
        {
            "ok": True,
            "dataset_path": _srv.normalize_path(dataset_path),
            "rows_updated": n,
            "truncated": truncated,
        },
    )


@tool(
    name="arcgis_pro_da_delete_selected",
    description="",
)
def arcgis_pro_da_delete_selected(
    dataset_path: str,
    where_clause: str,
    max_rows_deleted: int = 1000,
    expected_count: int | None = None,
) -> str:
    _srv.require_allow_destructive()
    if expected_count is None:
        raise RuntimeError(
            "该工具实际按 where_clause 删除，不读取 Pro 图层选择集；必须提供 expected_count。"
            "新工作流请使用 arcgis_pro_edit_preflight/apply。"
        )
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    with arcpy.da.SearchCursor(p, ["OID@"], where_clause) as cursor:
        actual_count = sum(1 for _ in cursor)
    if actual_count != int(expected_count):
        raise RuntimeError(f"expected_count={expected_count}，实际命中 {actual_count} 行；未删除")
    if actual_count > int(max_rows_deleted):
        raise RuntimeError("实际命中行数超过 max_rows_deleted；未删除")
    n, truncated = _srv.da_write.delete_selected(arcpy, dataset_path, where_clause, max_rows_deleted)
    return _srv._json_dumps(
        {
            "ok": True,
            "dataset_path": _srv.normalize_path(dataset_path),
            "rows_deleted": n,
            "truncated": truncated,
        },
    )


@tool(
    name="arcgis_pro_get_metadata",
    description="",
)
def arcgis_pro_get_metadata(dataset_path: str) -> str:
    arcpy = _srv._arcpy()
    md = _srv.metadata.get_metadata(arcpy, dataset_path)
    return _srv._json_dumps({"dataset_path": _srv.normalize_path(dataset_path), "metadata": md})


@tool(
    name="arcgis_pro_set_metadata",
    description="",
)
def arcgis_pro_set_metadata(
    dataset_path: str,
    title: str = "",
    tags: str = "",
    summary: str = "",
    description: str = "",
    credits: str = "",
    access_constraints: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.metadata.set_metadata(arcpy, dataset_path, title, tags, summary, description, credits, access_constraints)
    return _srv._json_dumps({"ok": True, "dataset_path": _srv.normalize_path(dataset_path)})


@tool(
    name="arcgis_pro_workspace_list_datasets",
    description="",
)
def arcgis_pro_workspace_list_datasets(
    workspace_path: str,
    dataset_type: str = "",
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _srv._arcpy()
    ws = _srv.validate_input_path_optional(workspace_path, "workspace_path")
    names = _srv._list_workspace_datasets(arcpy, ws, dataset_type, wild_card, max_items)
    return _srv._json_dumps(
        {
            "workspace_path": ws,
            "dataset_type": dataset_type.strip(),
            "datasets": names,
        },
    )


@tool(
    name="arcgis_pro_workspace_list_feature_datasets",
    description="",
)
def arcgis_pro_workspace_list_feature_datasets(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _srv._arcpy()
    ws = _srv.validate_input_path_optional(workspace_path, "workspace_path")
    names = _srv._list_workspace_datasets(arcpy, ws, "Feature", wild_card, max_items)
    return _srv._json_dumps({"workspace_path": ws, "feature_datasets": names})


@tool(
    name="arcgis_pro_workspace_list_domains",
    description="",
)
def arcgis_pro_workspace_list_domains(
    workspace_path: str,
    max_items: int = 200,
) -> str:
    arcpy = _srv._arcpy()
    ws = _srv.validate_input_path_optional(workspace_path, "workspace_path")
    domains = _srv._list_workspace_domains(arcpy, ws, max_items)
    return _srv._json_dumps({"workspace_path": ws, "domains": domains})


@tool(
    name="arcgis_pro_da_delete_where",
    description="按 where_clause 删除数据集记录；必须启用破坏性门禁并提供精确 expected_count。",
)
def arcgis_pro_da_delete_where(
    dataset_path: str,
    where_clause: str,
    expected_count: int,
    max_rows_deleted: int = 1000,
) -> str:
    return _srv.arcgis_pro_da_delete_selected(
        dataset_path,
        where_clause,
        max_rows_deleted=max_rows_deleted,
        expected_count=expected_count,
    )


@tool(
    name="arcgis_pro_dataset_exists",
    description="在输入根策略内检查一个数据集是否存在，并返回规范化路径。",
)
def arcgis_pro_dataset_exists(dataset_path: str) -> str:
    return _srv._json_dumps(_srv.dataset_management.dataset_exists(_srv._arcpy(), dataset_path))


@tool(
    name="arcgis_pro_verify_output_dataset",
    description="核验 GP 输出根目录内的数据集是否真实创建，并可要求至少一条记录。",
)
def arcgis_pro_verify_output_dataset(
    output_path: str,
    require_nonempty: bool = False,
) -> str:
    return _srv._json_dumps(
        _srv.dataset_management.verify_output_dataset(
            _srv._arcpy(),
            output_path,
            require_nonempty=require_nonempty,
        )
    )


@tool(
    name="arcgis_pro_dataset_schema",
    description="读取字段、索引、空间参考、子类型、附件和关系类的一体化数据集方案快照。",
)
def arcgis_pro_dataset_schema(dataset_path: str) -> str:
    return _srv._json_dumps(_srv.dataset_management.dataset_schema(_srv._arcpy(), dataset_path))


@tool(
    name="arcgis_pro_locator_info",
    description="读取受控本地 locator 的能力、地址字段、输出字段和空间参考，不访问计费服务。",
)
def arcgis_pro_locator_info(locator_path: str) -> str:
    return _srv._json_dumps(_srv.geocoding.locator_info(_srv._arcpy(), locator_path))


@tool(
    name="arcgis_pro_geocode_addresses",
    description="用受控本地 locator 批量地理编码地址表；typed 字段映射且拒绝远程计费 locator。",
)
def arcgis_pro_geocode_addresses(
    in_table: str,
    locator_path: str,
    address_fields: list[dict[str, str]],
    out_feature_class: str,
    country_codes: list[str] | None = None,
    location_type: str = "ADDRESS_LOCATION",
    categories: list[str] | None = None,
    output_fields: str = "MINIMAL_AND_USER",
) -> str:
    return _srv._json_dumps(
        _srv.geocoding.geocode_addresses(
            _srv._arcpy(),
            in_table,
            locator_path,
            address_fields,
            out_feature_class,
            country_codes=country_codes,
            location_type=location_type,
            categories=categories,
            output_fields=output_fields,
        )
    )


@tool(
    name="arcgis_pro_reverse_geocode",
    description="用受控本地 locator 对点要素反向地理编码；限制 feature/location 类型并核验输出。",
)
def arcgis_pro_reverse_geocode(
    in_features: str,
    locator_path: str,
    out_feature_class: str,
    feature_types: list[str] | None = None,
    location_type: str = "ADDRESS_LOCATION",
) -> str:
    return _srv._json_dumps(
        _srv.geocoding.reverse_geocode(
            _srv._arcpy(),
            in_features,
            locator_path,
            out_feature_class,
            feature_types=feature_types,
            location_type=location_type,
        )
    )


@tool(
    name="arcgis_pro_las_dataset_info",
    description="读取受控 .lasd 的文件数、点数、统计、金字塔、范围和空间参考。",
)
def arcgis_pro_las_dataset_info(las_dataset: str) -> str:
    return _srv._json_dumps(_srv.lidar.las_dataset_info(_srv._arcpy(), las_dataset))


@tool(
    name="arcgis_pro_create_las_dataset",
    description="从受控 LAS/LAZ/ZLAS 文件或目录创建新 .lasd；源旁路统计/PRJ 写入必须显式请求。",
)
def arcgis_pro_create_las_dataset(
    input_paths: list[str],
    out_las_dataset: str,
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
) -> str:
    return _srv._json_dumps(
        _srv.lidar.create_las_dataset(
            _srv._arcpy(),
            input_paths,
            out_las_dataset,
            recurse_folders=recurse_folders,
            surface_constraints=surface_constraints,
            spatial_reference_wkid=spatial_reference_wkid,
            compute_statistics=compute_statistics,
            relative_paths=relative_paths,
            create_las_prj=create_las_prj,
            processing_extent=processing_extent,
            boundary=boundary,
            contained_files_only=contained_files_only,
            confirm_all_las_prj=confirm_all_las_prj,
        )
    )


@tool(
    name="arcgis_pro_calculate_las_statistics",
    description="计算 LAS 统计并可导出报告；强制重算需要破坏性门禁、目标回显和固定确认短语。",
)
def arcgis_pro_calculate_las_statistics(
    las_dataset: str,
    expected_las_dataset: str,
    calculation_type: str = "SKIP_EXISTING_STATS",
    out_report: str = "",
    summary_level: str = "DATASET",
    delimiter: str = "SPACE",
    decimal_separator: str = "DECIMAL_POINT",
    confirm_overwrite: str = "",
) -> str:
    return _srv._json_dumps(
        _srv.lidar.calculate_las_statistics(
            _srv._arcpy(),
            las_dataset,
            expected_las_dataset=expected_las_dataset,
            calculation_type=calculation_type,
            out_report=out_report,
            summary_level=summary_level,
            delimiter=delimiter,
            decimal_separator=decimal_separator,
            confirm_overwrite=confirm_overwrite,
        )
    )


@tool(
    name="arcgis_pro_build_las_pyramid",
    description="为精确确认的 .lasd 构建或更新显示金字塔，可使用受约束 class-code 权重。",
)
def arcgis_pro_build_las_pyramid(
    las_dataset: str,
    expected_las_dataset: str,
    point_selection_method: str = "Z_MIN",
    class_code_weights: list[dict[str, int]] | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.lidar.build_las_pyramid(
            _srv._arcpy(),
            las_dataset,
            expected_las_dataset=expected_las_dataset,
            point_selection_method=point_selection_method,
            class_code_weights=class_code_weights,
        )
    )
