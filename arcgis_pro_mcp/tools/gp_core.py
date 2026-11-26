"""Generic geoprocessing, GP inspection, schema/field, creation, and conversion tools."""

from __future__ import annotations

import re
from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool


@tool(
    name="arcgis_pro_gp_list_registered",
    description="",
)
def arcgis_pro_gp_list_registered() -> str:
    return _srv._json_dumps({"gp_tools": _srv.gp_allowlist.list_registered_gp_tools()})


@tool(
    name="arcgis_pro_gp_get_count",
    description="",
)
def arcgis_pro_gp_get_count(dataset_path: str) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    cnt = _srv.gp_allowlist.gp_get_count(arcpy, p)
    return _srv._json_dumps({"dataset_path": p, "count": cnt})


@tool(
    name="arcgis_pro_gp_get_raster_property",
    description="",
)
def arcgis_pro_gp_get_raster_property(
    raster_path: str,
    property_type: str,
) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(raster_path, "raster_path")
    val = _srv.gp_allowlist.gp_get_raster_property(arcpy, p, property_type)
    return _srv._json_dumps(
        {"raster_path": p, "property_type": property_type.strip().upper(), "value": val},
    )


@tool(
    name="arcgis_pro_gp_get_cell_value",
    description="",
)
def arcgis_pro_gp_get_cell_value(
    raster_path: str,
    location_xy: str,
    band_index: int | None = None,
) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(raster_path, "raster_path")
    val = _srv.gp_allowlist.gp_get_cell_value(arcpy, p, location_xy, band_index)
    return _srv._json_dumps(
        {"raster_path": p, "location_xy": location_xy.strip(), "value": val},
    )


@tool(
    name="arcgis_pro_gp_test_schema_lock",
    description="",
)
def arcgis_pro_gp_test_schema_lock(dataset_path: str) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    val = _srv.gp_allowlist.gp_test_schema_lock(arcpy, p)
    return _srv._json_dumps({"dataset_path": p, "schema_lock": val})


@tool(
    name="arcgis_pro_gp_add_field",
    description="",
)
def arcgis_pro_gp_add_field(
    in_table: str,
    field_name: str,
    field_type: str,
    field_length: int | None = None,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_schema.run_add_field(arcpy, in_table, field_name, field_type, field_length)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_table": _srv.normalize_path(in_table),
            "field_name": field_name.strip(),
            "field_type": field_type.strip().upper(),
        },
    )


@tool(
    name="arcgis_pro_gp_delete_field",
    description=(
        "删除精确字段集合并读回核验；需要破坏性门禁、方案锁、目标路径与字段列表精确回显。"
    ),
)
def arcgis_pro_gp_delete_field(
    in_table: str,
    drop_field: str,
    confirm_in_table: str,
    confirm_drop_fields: list[str],
) -> str:
    arcpy = _srv._arcpy()
    result = _srv.gp_schema.run_delete_field(
        arcpy,
        in_table,
        drop_field,
        confirm_in_table=confirm_in_table,
        confirm_drop_fields=confirm_drop_fields,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_gp_export_features",
    description="",
)
def arcgis_pro_gp_export_features(in_features: str, out_path: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_export_features(arcpy, in_features, out_path)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.normalize_path(in_features),
            "out_path": _srv.normalize_path(out_path),
        },
    )


@tool(
    name="arcgis_pro_gp_export_table",
    description="",
)
def arcgis_pro_gp_export_table(in_table: str, out_path: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_write.run_export_table(arcpy, in_table, out_path)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_table": _srv.normalize_path(in_table),
            "out_path": _srv.normalize_path(out_path),
        },
    )


@tool(
    name="arcgis_pro_gp_calculate_field",
    description=(
        "以受限 Arcade 表达式重算现有字段；禁止 Python/VB/code block/Portal FeatureSet，"
        "并要求破坏性门禁、目标路径和记录数精确确认。"
    ),
)
def arcgis_pro_gp_calculate_field(
    in_table: str,
    field_name: str,
    expression: str,
    expected_count: int,
    confirm_in_table: str,
    expression_type: str = "ARCADE",
) -> str:
    _srv.require_allow_destructive()
    if confirm_in_table != in_table:
        raise RuntimeError("confirm_in_table 必须逐字符精确回显 in_table")
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(in_table, "in_table")
    fn = field_name.strip()
    if not fn:
        raise RuntimeError("field_name 不能为空")
    list_fields = getattr(arcpy, "ListFields", None)
    if not callable(list_fields):
        raise RuntimeError("当前 ArcPy 不支持 ListFields，无法核验目标字段")
    fields = [str(field.name) for field in list_fields(p)]
    matches = [name for name in fields if name.casefold() == fn.casefold()]
    if len(matches) != 1:
        raise RuntimeError(f"field_name 必须唯一匹配现有字段；可用字段：{fields[:50]}")
    fn = matches[0]
    expr = _srv.validate_safe_arcade_expression(expression)
    et = expression_type.strip().upper()
    if et != "ARCADE":
        raise RuntimeError("expression_type 仅允许 ARCADE；Python/VB/code block 不通过 MCP 暴露")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise RuntimeError("expected_count 必须为非负整数")
    actual_count = int(arcpy.management.GetCount(p).getOutput(0))
    if actual_count != expected_count:
        raise RuntimeError(
            f"expected_count={expected_count}，实际记录数为 {actual_count}；未计算字段"
        )
    result = arcpy.management.CalculateField(p, fn, expr, "ARCADE")
    after_count = int(arcpy.management.GetCount(p).getOutput(0))
    if after_count != actual_count:
        raise RuntimeError("CalculateField 返回后记录数改变；结果异常，请勿自动重试")
    return _srv._json_dumps(
        {
            "ok": True,
            "in_table": p,
            "field_name": fn,
            "expression_type": "ARCADE",
            "rows_targeted": actual_count,
            "count_verified": True,
            "messages": str(getattr(result, "getMessages", lambda: "")() or "")[:4000],
        }
    )


@tool(
    name="arcgis_pro_gp_calculate_geometry",
    description=(
        "对精确确认的现有字段整表计算几何属性；要求破坏性门禁、目标/字段映射回显和记录数确认。"
    ),
)
def arcgis_pro_gp_calculate_geometry(
    in_features: str,
    geometry_property: list[list[str]],
    expected_count: int,
    confirm_in_features: str,
    confirm_geometry_property: list[list[str]],
    length_unit: str = "",
    area_unit: str = "",
) -> str:
    _srv.require_allow_destructive()
    if confirm_in_features != in_features:
        raise RuntimeError("confirm_in_features 必须逐字符精确回显 in_features")
    if confirm_geometry_property != geometry_property:
        raise RuntimeError("confirm_geometry_property 必须精确回显 geometry_property")
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(in_features, "in_features")
    if not geometry_property or len(geometry_property) > 64:
        raise RuntimeError("geometry_property 必须为 1–64 项")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise RuntimeError("expected_count 必须为非负整数")
    list_fields = getattr(arcpy, "ListFields", None)
    if not callable(list_fields):
        raise RuntimeError("当前 ArcPy 不支持 ListFields，无法核验目标字段")
    available = [str(field.name) for field in list_fields(p)]
    aliases = {
        "AREA": "AREA_GEODESIC",
        "LENGTH": "PERIMETER_LENGTH_GEODESIC",
        "PERIMETER": "PERIMETER_LENGTH_GEODESIC",
    }
    mapped: list[list[str]] = []
    for pair in geometry_property:
        if not isinstance(pair, list) or len(pair) != 2:
            raise RuntimeError("geometry_property 每项须为 [字段名, 几何属性]")
        requested_field = str(pair[0]).strip()
        matches = [name for name in available if name.casefold() == requested_field.casefold()]
        if len(matches) != 1:
            raise RuntimeError(
                f"几何属性目标字段必须唯一匹配现有字段：{requested_field!r}；"
                f"可用字段：{available[:50]}"
            )
        prop = aliases.get(str(pair[1]).strip().upper(), str(pair[1]).strip().upper())
        if not prop or len(prop) > 80 or not re.fullmatch(r"[A-Z][A-Z0-9_]*", prop):
            raise RuntimeError(f"几何属性枚举无效：{prop!r}")
        mapped.append([matches[0], prop])
    geometry_property = mapped
    lu = (length_unit or "").strip()
    au = (area_unit or "").strip()
    kwargs: dict[str, Any] = {}
    if lu:
        kwargs["length_unit"] = lu
    if au:
        kwargs["area_unit"] = au
    actual_count = int(arcpy.management.GetCount(p).getOutput(0))
    if actual_count != expected_count:
        raise RuntimeError(
            f"expected_count={expected_count}，实际记录数为 {actual_count}；未计算几何属性"
        )
    result = arcpy.management.CalculateGeometryAttributes(p, mapped, **kwargs)
    after_count = int(arcpy.management.GetCount(p).getOutput(0))
    if after_count != actual_count:
        raise RuntimeError("CalculateGeometryAttributes 返回后记录数改变；请勿自动重试")
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": p,
            "geometry_property": mapped,
            "rows_targeted": actual_count,
            "count_verified": True,
            "messages": str(getattr(result, "getMessages", lambda: "")() or "")[:4000],
        }
    )


@tool(
    name="arcgis_pro_gp_append",
    description="",
)
def arcgis_pro_gp_append(
    inputs: list[str],
    target: str,
    schema_type: str = "TEST",
) -> str:
    _srv.require_allow_write()
    arcpy = _srv._arcpy()
    if not inputs:
        raise RuntimeError("inputs 不能为空")
    ins = [_srv.validate_input_path_optional(p, f"input_{i}") for i, p in enumerate(inputs)]
    tgt = _srv.validate_input_path_optional(target, "target")
    st = schema_type.strip().upper()
    if st not in ("TEST", "NO_TEST"):
        raise RuntimeError("schema_type 须为 TEST 或 NO_TEST")
    arcpy.management.Append(ins, tgt, st)
    return _srv._json_dumps({"ok": True, "target": _srv.normalize_path(target), "input_count": len(ins)})


@tool(
    name="arcgis_pro_gp_delete_features",
    description="",
)
def arcgis_pro_gp_delete_features(
    in_features: str,
    expected_count: int | None = None,
) -> str:
    _srv.require_allow_destructive()
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(in_features, "in_features")
    actual_count = int(arcpy.management.GetCount(p).getOutput(0))
    if expected_count is None or actual_count != int(expected_count):
        raise RuntimeError(
            f"必须提供匹配的 expected_count；当前记录数为 {actual_count}，本次未删除"
        )
    arcpy.management.DeleteFeatures(p)
    remaining = int(arcpy.management.GetCount(p).getOutput(0))
    if remaining != 0:
        raise RuntimeError(f"DeleteFeatures 返回后仍有 {remaining} 条记录")
    return _srv._json_dumps(
        {"ok": True, "in_features": _srv.normalize_path(in_features), "deleted_count": actual_count}
    )


@tool(
    name="arcgis_pro_gp_truncate_table",
    description="",
)
def arcgis_pro_gp_truncate_table(
    in_table: str,
    expected_count: int | None = None,
    confirm_all: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm_all:
        raise RuntimeError("清空表需要 confirm_all=true")
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(in_table, "in_table")
    actual_count = int(arcpy.management.GetCount(p).getOutput(0))
    if expected_count is None or actual_count != int(expected_count):
        raise RuntimeError(
            f"必须提供匹配的 expected_count；当前记录数为 {actual_count}，本次未清空"
        )
    arcpy.management.TruncateTable(p)
    remaining = int(arcpy.management.GetCount(p).getOutput(0))
    if remaining != 0:
        raise RuntimeError(f"TruncateTable 返回后仍有 {remaining} 条记录")
    return _srv._json_dumps(
        {"ok": True, "in_table": _srv.normalize_path(in_table), "deleted_count": actual_count}
    )


@tool(
    name="arcgis_pro_gp_create_feature_class",
    description="",
)
def arcgis_pro_gp_create_feature_class(
    out_path: str,
    out_name: str,
    geometry_type: str,
    spatial_reference_wkid: int | None = None,
) -> str:
    arcpy = _srv._arcpy()
    result_path = _srv.gp_create.run_create_feature_class(
        arcpy, out_path, out_name, geometry_type, spatial_reference_wkid
    )
    return _srv._json_dumps({"ok": True, "created": result_path})


@tool(
    name="arcgis_pro_gp_create_table",
    description="",
)
def arcgis_pro_gp_create_table(out_path: str, out_name: str) -> str:
    arcpy = _srv._arcpy()
    result_path = _srv.gp_create.run_create_table(arcpy, out_path, out_name)
    return _srv._json_dumps({"ok": True, "created": result_path})


@tool(
    name="arcgis_pro_gp_create_file_gdb",
    description="",
)
def arcgis_pro_gp_create_file_gdb(out_folder_path: str, out_name: str) -> str:
    arcpy = _srv._arcpy()
    result_path = _srv.gp_create.run_create_file_gdb(arcpy, out_folder_path, out_name)
    return _srv._json_dumps({"ok": True, "created": result_path})


@tool(
    name="arcgis_pro_gp_create_feature_dataset",
    description="",
)
def arcgis_pro_gp_create_feature_dataset(
    out_dataset_path: str,
    out_name: str,
    spatial_reference_wkid: int,
) -> str:
    arcpy = _srv._arcpy()
    result_path = _srv.gp_create.run_create_feature_dataset(
        arcpy, out_dataset_path, out_name, spatial_reference_wkid
    )
    return _srv._json_dumps({"ok": True, "created": result_path})


@tool(
    name="arcgis_pro_gp_copy_feature_class",
    description="",
)
def arcgis_pro_gp_copy_feature_class(in_features: str, out_feature_class: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_create.run_copy_feature_class(arcpy, in_features, out_feature_class)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_features": _srv.normalize_path(in_features),
            "out_feature_class": _srv.normalize_path(out_feature_class),
        },
    )


@tool(
    name="arcgis_pro_gp_rename_dataset",
    description="",
)
def arcgis_pro_gp_rename_dataset(in_data: str, out_data: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_create.run_rename_dataset(arcpy, in_data, out_data)
    return _srv._json_dumps(
        {"ok": True, "in_data": _srv.normalize_path(in_data), "out_data": out_data.strip()},
    )


@tool(
    name="arcgis_pro_gp_delete_dataset",
    description="",
)
def arcgis_pro_gp_delete_dataset(in_data: str, confirm: bool = False) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除数据集需要 confirm=true")
    arcpy = _srv._arcpy()
    target = _srv.validate_input_path_optional(in_data, "in_data")
    _srv.gp_create.run_delete_dataset(arcpy, in_data)
    if arcpy.Exists(target):
        raise RuntimeError("Delete 返回后数据集仍存在")
    return _srv._json_dumps({"ok": True, "deleted": _srv.normalize_path(in_data)})


@tool(
    name="arcgis_pro_gp_alter_field",
    description="",
)
def arcgis_pro_gp_alter_field(
    in_table: str,
    field_name: str,
    new_field_name: str = "",
    new_field_alias: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_create.run_alter_field(arcpy, in_table, field_name, new_field_name, new_field_alias)
    return _srv._json_dumps(
        {
            "ok": True,
            "in_table": _srv.normalize_path(in_table),
            "field_name": field_name.strip(),
            "new_field_name": new_field_name.strip() or "(unchanged)",
            "new_field_alias": new_field_alias.strip() or "(unchanged)",
        },
    )


@tool(
    name="arcgis_pro_gp_import_csv_to_table",
    description="",
)
def arcgis_pro_gp_import_csv_to_table(
    in_csv: str,
    out_path: str,
    out_name: str,
) -> str:
    arcpy = _srv._arcpy()
    result_path = _srv.gp_convert.run_import_csv_to_table(arcpy, in_csv, out_path, out_name)
    return _srv._json_dumps({"ok": True, "created": result_path})


@tool(
    name="arcgis_pro_gp_xy_table_to_point",
    description="",
)
def arcgis_pro_gp_xy_table_to_point(
    in_table: str,
    out_feature_class: str,
    x_field: str,
    y_field: str,
    spatial_reference_wkid: int = 4326,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_convert.run_xy_table_to_point(
        arcpy, in_table, out_feature_class, x_field, y_field, spatial_reference_wkid
    )
    return _srv._json_dumps(
        {"ok": True, "out_feature_class": _srv.normalize_path(out_feature_class)},
    )


@tool(
    name="arcgis_pro_gp_json_to_features",
    description="",
)
def arcgis_pro_gp_json_to_features(in_json_file: str, out_features: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_convert.run_json_to_features(arcpy, in_json_file, out_features)
    return _srv._json_dumps({"ok": True, "out_features": _srv.normalize_path(out_features)})


@tool(
    name="arcgis_pro_gp_features_to_json",
    description="",
)
def arcgis_pro_gp_features_to_json(
    in_features: str,
    out_json_file: str,
    format_json: bool = True,
    include_z_values: bool = False,
    include_m_values: bool = False,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_convert.run_features_to_json(
        arcpy, in_features, out_json_file, format_json, include_z_values, include_m_values
    )
    return _srv._json_dumps({"ok": True, "out_json_file": _srv.normalize_path(out_json_file)})


@tool(
    name="arcgis_pro_gp_kml_to_layer",
    description="",
)
def arcgis_pro_gp_kml_to_layer(in_kml_file: str, output_folder: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_convert.run_kml_to_layer(arcpy, in_kml_file, output_folder)
    return _srv._json_dumps({"ok": True, "output_folder": _srv.normalize_path(output_folder)})


@tool(
    name="arcgis_pro_gp_excel_to_table",
    description="",
)
def arcgis_pro_gp_excel_to_table(
    input_excel: str,
    out_table: str,
    sheet: str = "",
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_convert.run_excel_to_table(arcpy, input_excel, out_table, sheet)
    return _srv._json_dumps({"ok": True, "out_table": _srv.normalize_path(out_table)})


@tool(
    name="arcgis_pro_gp_table_to_excel",
    description="",
)
def arcgis_pro_gp_table_to_excel(in_table: str, output_excel: str) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_convert.run_table_to_excel(arcpy, in_table, output_excel)
    return _srv._json_dumps({"ok": True, "output_excel": _srv.normalize_path(output_excel)})


@tool(
    name="arcgis_pro_gp_feature_class_to_shapefile",
    description="",
)
def arcgis_pro_gp_feature_class_to_shapefile(
    input_features: list[str],
    output_folder: str,
) -> str:
    arcpy = _srv._arcpy()
    _srv.gp_convert.run_feature_class_to_shapefile(arcpy, input_features, output_folder)
    return _srv._json_dumps({"ok": True, "output_folder": _srv.normalize_path(output_folder)})


@tool(
    name="arcgis_pro_gp_run_tool",
    description=(
        "优先通过原生 GP 工具名（如 analysis.Buffer 或 Buffer_analysis）与原生命名参数执行地理处理。"
        "默认开启，无需部署工具白名单；工具必须存在于 ArcPy ListTools。"
        "需要写入权限和 GP 输出根内的完整新输出路径，禁止覆盖、原地修改和代码执行。"
        "WeightedOverlay 的 in_weighted_overlay_table 使用含 evaluation_scale 和 rasters 的 JSON 对象；"
        "每行包含 raster、influence、field、remap（整数值对），所有 influence 之和须为 100。"
        "需要 CURRENT、质量检查或专用语义时使用对应接口。"
    ),
)
def arcgis_pro_gp_run_tool(
    tool_name: str,
    parameters: dict[str, Any] | None = None,
) -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_generic.run_tool(arcpy, tool_name, parameters)
    return _srv._json_dumps({"ok": True, "tool_name": tool_name, "messages": msgs})


@tool(
    name="arcgis_pro_gp_get_messages",
    description="",
)
def arcgis_pro_gp_get_messages() -> str:
    arcpy = _srv._arcpy()
    msgs = _srv.gp_generic.get_messages(arcpy)
    return _srv._json_dumps({"messages": msgs})


@tool(
    name="arcgis_pro_gp_list_toolboxes",
    description="",
)
def arcgis_pro_gp_list_toolboxes() -> str:
    arcpy = _srv._arcpy()
    toolboxes = _srv.gp_generic.list_toolboxes(arcpy)
    return _srv._json_dumps({"toolboxes": toolboxes, "count": len(toolboxes)})


@tool(
    name="arcgis_pro_gp_list_tools_in_toolbox",
    description="",
)
def arcgis_pro_gp_list_tools_in_toolbox(toolbox: str) -> str:
    arcpy = _srv._arcpy()
    tools = _srv.gp_generic.list_tools_in_toolbox(arcpy, toolbox)
    return _srv._json_dumps({"toolbox": toolbox, "tools": tools, "count": len(tools)})


@tool(
    name="arcgis_pro_gp_table_to_table",
    description="",
)
def arcgis_pro_gp_table_to_table(
    in_rows: str,
    out_path: str,
    out_name: str,
) -> str:
    arcpy = _srv._arcpy()
    result_path = _srv.gp_convert.run_table_to_table(arcpy, in_rows, out_path, out_name)
    return _srv._json_dumps({"ok": True, "created": result_path})


@tool(
    name="arcgis_pro_extension_status",
    description="读取 Spatial、3D、Image Analyst、Network 等 ArcGIS 扩展许可状态，不执行签出。",
)
def arcgis_pro_extension_status(extension_names: list[str] | None = None) -> str:
    return _srv._json_dumps(
        {
            "extensions": _srv.raster_runtime.extension_status(
                _srv._arcpy(), extension_names
            )
        }
    )


@tool(
    name="arcgis_pro_validate_analysis_environment",
    description="校验受支持的 ArcPy 环境映射及其中的输入路径，不改变全局环境。",
)
def arcgis_pro_validate_analysis_environment(
    environment: dict[str, Any] | None = None,
) -> str:
    return _srv._json_dumps(
        {
            "ok": True,
            "arcpy_environment": _srv.raster_runtime.validate_environment(environment),
        }
    )
