"""MCP tools for ArcGIS Pro via arcpy.mp (mapping module)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from arcgis_pro_mcp import (
    da_read,
    da_write,
    gp_allowlist,
    gp_analysis,
    gp_convert,
    gp_create,
    gp_generic,
    gp_network,
    gp_raster,
    gp_schema,
    gp_write,
    metadata,
    symbology,
    workspace_listing,
)
from arcgis_pro_mcp.paths import (
    normalize_path,
    require_allow_write,
    validate_input_path_optional,
    validate_output_in_export_root,
    writes_allowed,
)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


mcp = FastMCP(
    "arcgis-pro",
    instructions=(
        "通过 ArcPy 自动化 ArcGIS Pro 工程：读/写地图与图层、布局与导出、白名单地理处理。"
        "无法替代 Pro 全部 UI；写入与选择需 ARCGIS_PRO_MCP_ALLOW_WRITE=1。"
        "导出路径受 ARCGIS_PRO_MCP_EXPORT_ROOT 约束（若设置）；"
        "写入型 GP（Buffer/Clip/叠加分析/统计/投影等）必须设置 ARCGIS_PRO_MCP_GP_OUTPUT_ROOT 且输出位于其下。"
        "须在 Windows 上使用 Pro 捆绑的 Python。"
    ),
)


def _arcpy():
    try:
        import arcpy  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError(
            "未检测到 arcpy。请在已安装 ArcGIS Pro 的 Windows 上，使用 Pro 自带的 Python 启动本 MCP，"
            '例如："C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" '
            "-m arcgis_pro_mcp"
        ) from e
    return arcpy


def _open_project(aprx_path: str) -> tuple[Any, Any, str]:
    arcpy = _arcpy()
    path = aprx_path.strip().strip('"')
    if not path:
        raise RuntimeError("aprx_path 不能为空")
    project = arcpy.mp.ArcGISProject(path)
    return arcpy, project, path


def _get_map(project: Any, map_name: str) -> Any:
    for m in project.listMaps():
        if m.name == map_name:
            return m
    available = [m.name for m in project.listMaps()]
    raise RuntimeError("Invalid arguments")


def _get_layout(project: Any, layout_name: str) -> Any:
    for lyt in project.listLayouts():
        if lyt.name == layout_name:
            return lyt
    available = [lyt.name for lyt in project.listLayouts()]
    raise RuntimeError("Invalid arguments")


def _find_layer(map_obj: Any, layer_name: str) -> Any:
    for lyr in map_obj.listLayers():
        if lyr.name == layer_name:
            return lyr
    names = [x.name for x in map_obj.listLayers()]
    raise RuntimeError("Invalid arguments")


@contextmanager
def _workspace_ctx(arcpy: Any, workspace_path: str):
    old = arcpy.env.workspace
    arcpy.env.workspace = workspace_path
    try:
        yield
    finally:
        arcpy.env.workspace = old


def _sanitize_wild_card(wild_card: str, max_len: int = 120) -> str:
    wc = wild_card.strip()
    if len(wc) > max_len:
        raise RuntimeError("wild_card 杩囬暱")
    return wc or "*"


def _list_workspace_datasets(
    arcpy: Any,
    workspace_path: str,
    dataset_type: str = "",
    wild_card: str = "*",
    max_items: int = 200,
) -> list[str]:
    cap = max(1, min(int(max_items), 2000))
    wc = _sanitize_wild_card(wild_card)
    dt = dataset_type.strip()
    with _workspace_ctx(arcpy, workspace_path):
        names = arcpy.ListDatasets(wc, dt or "") or []
    return [str(n) for n in names[:cap]]


def _list_workspace_domains(arcpy: Any, workspace_path: str, max_items: int = 200) -> list[dict[str, Any]]:
    cap = max(1, min(int(max_items), 2000))
    try:
        list_domains = arcpy.da.ListDomains  # type: ignore[attr-defined]
    except AttributeError:
        list_domains = getattr(arcpy, "ListDomains", None)
    if list_domains is None:
        raise RuntimeError("当前 arcpy 版本不支持 ListDomains")
    domains = list_domains(workspace_path) or []
    rows: list[dict[str, Any]] = []
    for dom in domains[:cap]:
        row: dict[str, Any] = {}
        for attr in (
            "name",
            "domainType",
            "fieldType",
            "splitPolicy",
            "mergePolicy",
            "description",
            "owner",
        ):
            try:
                v = getattr(dom, attr, None)
                if v is not None:
                    row[attr] = v
            except Exception:  # noqa: BLE001
                pass
        try:
            coded_values = getattr(dom, "codedValues", None)
            if coded_values:
                row["coded_values"] = coded_values
        except Exception:  # noqa: BLE001
            pass
        rows.append(row)
    return rows


_MAX_QUERY_WHERE = 8000
_MAX_QUERY_CELL = 2000


def _sanitize_order_by(order_by: str) -> str:
    ob = (order_by or "").strip()
    if not ob:
        return ""
    if len(ob) > _MAX_QUERY_WHERE:
        raise RuntimeError("order_by 杩囬暱")
    if any(ch in ob for ch in ("\r", "\n", ";")):
        raise RuntimeError("order_by 涓嶈兘鍖呭惈鎹㈣鎴?鍒嗗彿")
    return ob

def _validate_view_name(name: str, label: str) -> str:
    out = (name or "").strip()
    if not out:
        raise RuntimeError(f"{label} cannot be empty")
    if len(out) > 128:
        raise RuntimeError(f"{label} too long")
    if any(ch in out for ch in ("\r", "\n", ";", "\\", "/")):
        raise RuntimeError(f"{label} contains invalid characters")
    return out


def _query_rows(
    arcpy: Any,
    dataset_path: str,
    fields: list[str],
    where_clause: str = "",
    order_by: str = "",
    max_rows: int = 100,
    offset: int = 0,
    include_shape_wkt: bool = False,
) -> list[dict[str, Any]]:
    w = (where_clause or "").strip()
    if len(w) > _MAX_QUERY_WHERE:
        raise RuntimeError("where_clause 杩囬暱")
    cap = max(1, min(int(max_rows), 500))
    start = max(0, min(int(offset), 1_000_000))
    fnames = [f.strip() for f in fields if f.strip()]
    if not fnames:
        raise RuntimeError("fields 涓嶈兘涓虹┖")
    if any(f.upper().startswith("SHAPE@") for f in fnames):
        raise RuntimeError("fields 涓嶄笉鑳藉寘鍚?SHAPE@*锛岃浣跨敤 include_shape_wkt")
    valid = {f.name for f in arcpy.ListFields(dataset_path)}
    missing = [f for f in fnames if f not in valid]
    if missing:
        raise RuntimeError("Invalid arguments")
    cursor_fields = list(fnames)
    if include_shape_wkt:
        cursor_fields.append("SHAPE@WKT")
    ob = _sanitize_order_by(order_by)
    sql_clause = (None, f"ORDER BY {ob}") if ob else None
    rows_out: list[dict[str, Any]] = []
    with arcpy.da.SearchCursor(dataset_path, cursor_fields, w or None, sql_clause=sql_clause) as cur:  # type: ignore[attr-defined]
        for idx, row in enumerate(cur):
            if idx < start:
                continue
            if len(rows_out) >= cap:
                break
            d: dict[str, Any] = {}
            for j, name in enumerate(cursor_fields):
                val = row[j]
                if val is None:
                    d[name] = None
                elif isinstance(val, (int, float, bool)):
                    d[name] = val
                else:
                    s = str(val)
                    d[name] = s if len(s) <= _MAX_QUERY_CELL else s[:_MAX_QUERY_CELL] + "..."
            rows_out.append(d)
    return rows_out


def _spatial_ref_dict(sr: Any) -> dict[str, Any] | None:
    if sr is None:
        return None
    out: dict[str, Any] = {}
    try:
        out["name"] = sr.name
    except Exception:  # noqa: BLE001
        pass
    try:
        out["factory_code"] = int(sr.factoryCode)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["type"] = sr.type
    except Exception:  # noqa: BLE001
        pass
    try:
        wkt = sr.exportToString()
        if wkt:
            out["wkt"] = wkt[:2000]
    except Exception:  # noqa: BLE001
        pass
    return out or None


def _extent_dict(ext: Any) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for key in ("XMin", "YMin", "XMax", "YMax", "ZMin", "ZMax", "MMin", "MMax"):
        try:
            v = getattr(ext, key, None)
            if v is not None:
                d[key.lower()] = float(v)
        except Exception:  # noqa: BLE001
            pass
    try:
        d["spatial_reference"] = _spatial_ref_dict(ext.spatialReference)
    except Exception:  # noqa: BLE001
        pass
    return d


def _describe_summary(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    dobj = arcpy.Describe(dataset_path)
    out: dict[str, Any] = {}
    for attr in (
        "name",
        "baseName",
        "catalogPath",
        "path",
        "file",
        "dataType",
        "category",
        "workspacePath",
        "shapeType",
        "hasSpatialIndex",
        "hasM",
        "hasZ",
        "length",
        "areaFieldName",
        "geometryType",
    ):
        try:
            v = getattr(dobj, attr, None)
            if v is not None and not callable(v):
                if isinstance(v, (str, int, float, bool)):
                    out[attr] = v
                else:
                    out[attr] = str(v)[:1000]
        except Exception:  # noqa: BLE001
            pass
    try:
        out["extent"] = _extent_dict(dobj.extent)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["spatial_reference"] = _spatial_ref_dict(dobj.spatialReference)
    except Exception:  # noqa: BLE001
        pass
    return out


@mcp.tool(
    name="arcgis_pro_environment_info",
    description="",
)
def arcgis_pro_environment_info() -> str:
    arcpy = _arcpy()
    info: dict[str, Any] = {}
    try:
        inst = arcpy.GetInstallInfo()
        info["install_info"] = inst
        if isinstance(inst, dict):
            info["pro_version"] = inst.get("Version")
    except Exception as ex:  # noqa: BLE001
        info["install_info_error"] = str(ex)[:500]
    try:
        info["product_info"] = arcpy.ProductInfo()
    except Exception as ex:  # noqa: BLE001
        info["product_info_error"] = str(ex)[:500]
    return _json_dumps(info)


@mcp.tool(
    name="arcgis_pro_server_capabilities",
    description="",
)
def arcgis_pro_server_capabilities() -> str:
    write = writes_allowed()
    tools_read = [
        "arcgis_pro_environment_info",
        "arcgis_pro_server_capabilities",
        "arcgis_pro_describe",
        "arcgis_pro_list_fields",
        "arcgis_pro_project_connections",
        "arcgis_pro_project_summary",
        "arcgis_pro_list_maps",
        "arcgis_pro_list_layouts",
        "arcgis_pro_list_reports",
        "arcgis_pro_list_layers",
        "arcgis_pro_list_tables",
        "arcgis_pro_map_spatial_reference",
        "arcgis_pro_map_camera",
        "arcgis_pro_list_bookmarks",
        "arcgis_pro_layer_properties",
        "arcgis_pro_list_layout_elements",
        "arcgis_pro_mapframe_extent",
        "arcgis_pro_gp_get_count",
        "arcgis_pro_gp_get_raster_property",
        "arcgis_pro_gp_get_cell_value",
        "arcgis_pro_gp_test_schema_lock",
        "arcgis_pro_gp_list_registered",
        "arcgis_pro_workspace_list_datasets",
        "arcgis_pro_workspace_list_feature_datasets",
        "arcgis_pro_workspace_list_domains",
        "arcgis_pro_workspace_list_feature_classes",
        "arcgis_pro_workspace_list_rasters",
        "arcgis_pro_workspace_list_tables",
        "arcgis_pro_da_table_sample",
        "arcgis_pro_da_query_rows",
        "arcgis_pro_da_distinct_values",
        "arcgis_pro_layer_selection_count",
        "arcgis_pro_layer_selection_fids",
        "arcgis_pro_get_layer_extent",
        "arcgis_pro_list_layer_renderers",
        "arcgis_pro_list_layout_map_frames",
        "arcgis_pro_list_broken_sources",
        "arcgis_pro_list_sde_datasets",
        "arcgis_pro_gp_get_messages",
        "arcgis_pro_gp_list_toolboxes",
        "arcgis_pro_gp_list_tools_in_toolbox",
        "arcgis_pro_get_metadata",
    ]
    tools_write = [
        "arcgis_pro_save_project",
        "arcgis_pro_save_project_copy",
        "arcgis_pro_set_layer_visible",
        "arcgis_pro_set_layer_transparency",
        "arcgis_pro_set_definition_query",
        "arcgis_pro_select_layer_by_attribute",
        "arcgis_pro_make_feature_layer",
        "arcgis_pro_make_table_view",
        "arcgis_pro_mapframe_zoom_to_bookmark",
        "arcgis_pro_add_layer_from_path",
        "arcgis_pro_remove_layer",
        "arcgis_pro_add_table_from_path",
        "arcgis_pro_remove_table",
        "arcgis_pro_rename_map",
        "arcgis_pro_rename_layout",
        "arcgis_pro_create_group_layer",
        "arcgis_pro_move_layer",
        "arcgis_pro_rename_layer",
        "arcgis_pro_set_map_reference_scale",
        "arcgis_pro_set_map_default_camera",
        "arcgis_pro_select_layer_by_location",
        "arcgis_pro_clear_map_selection",
        "arcgis_pro_add_join",
        "arcgis_pro_remove_join",
        "arcgis_pro_update_layout_text_element",
        "arcgis_pro_set_mapframe_extent",
        "arcgis_pro_set_map_spatial_reference",
        "arcgis_pro_layer_replace_data_source",
        "arcgis_pro_apply_symbology_from_layer",
        "arcgis_pro_set_layer_scale_range",
        "arcgis_pro_toggle_layer_labels",
        "arcgis_pro_da_update_field_constant",
        "arcgis_pro_da_insert_features",
        "arcgis_pro_da_update_features",
        "arcgis_pro_da_delete_selected",
        "arcgis_pro_gp_buffer",
        "arcgis_pro_gp_clip",
        "arcgis_pro_gp_analysis_select",
        "arcgis_pro_gp_copy_features",
        "arcgis_pro_gp_dissolve",
        "arcgis_pro_gp_intersect",
        "arcgis_pro_gp_union",
        "arcgis_pro_gp_erase",
        "arcgis_pro_gp_spatial_join",
        "arcgis_pro_gp_statistics",
        "arcgis_pro_gp_frequency",
        "arcgis_pro_gp_table_select",
        "arcgis_pro_gp_merge",
        "arcgis_pro_gp_project",
        "arcgis_pro_gp_add_field",
        "arcgis_pro_gp_delete_field",
        "arcgis_pro_gp_export_features",
        "arcgis_pro_gp_export_table",
        "arcgis_pro_gp_near",
        "arcgis_pro_gp_generate_near_table",
        "arcgis_pro_gp_calculate_field",
        "arcgis_pro_gp_calculate_geometry",
        "arcgis_pro_gp_append",
        "arcgis_pro_gp_delete_features",
        "arcgis_pro_gp_truncate_table",
        "arcgis_pro_gp_create_feature_class",
        "arcgis_pro_gp_create_table",
        "arcgis_pro_gp_create_file_gdb",
        "arcgis_pro_gp_create_feature_dataset",
        "arcgis_pro_gp_copy_feature_class",
        "arcgis_pro_gp_rename_dataset",
        "arcgis_pro_gp_delete_dataset",
        "arcgis_pro_gp_alter_field",
        "arcgis_pro_gp_import_csv_to_table",
        "arcgis_pro_gp_table_to_table",
        "arcgis_pro_gp_xy_table_to_point",
        "arcgis_pro_gp_json_to_features",
        "arcgis_pro_gp_features_to_json",
        "arcgis_pro_gp_kml_to_layer",
        "arcgis_pro_gp_excel_to_table",
        "arcgis_pro_gp_table_to_excel",
        "arcgis_pro_gp_feature_class_to_shapefile",
        "arcgis_pro_gp_multiple_ring_buffer",
        "arcgis_pro_gp_feature_to_point",
        "arcgis_pro_gp_feature_to_line",
        "arcgis_pro_gp_points_to_line",
        "arcgis_pro_gp_polygon_to_line",
        "arcgis_pro_gp_minimum_bounding_geometry",
        "arcgis_pro_gp_convex_hull",
        "arcgis_pro_gp_split_by_attributes",
        "arcgis_pro_gp_identity",
        "arcgis_pro_gp_symmetrical_difference",
        "arcgis_pro_gp_count_overlapping_features",
        "arcgis_pro_gp_repair_geometry",
        "arcgis_pro_gp_check_geometry",
        "arcgis_pro_gp_eliminate",
        "arcgis_pro_gp_multipart_to_singlepart",
        "arcgis_pro_gp_aggregate_polygons",
        "arcgis_pro_gp_slope",
        "arcgis_pro_gp_aspect",
        "arcgis_pro_gp_hillshade",
        "arcgis_pro_gp_reclassify",
        "arcgis_pro_gp_extract_by_mask",
        "arcgis_pro_gp_extract_by_attributes",
        "arcgis_pro_gp_zonal_statistics_as_table",
        "arcgis_pro_gp_kernel_density",
        "arcgis_pro_gp_point_density",
        "arcgis_pro_gp_idw",
        "arcgis_pro_gp_kriging",
        "arcgis_pro_gp_topo_to_raster",
        "arcgis_pro_gp_raster_to_polygon",
        "arcgis_pro_gp_polygon_to_raster",
        "arcgis_pro_gp_feature_to_raster",
        "arcgis_pro_gp_raster_calculator",
        "arcgis_pro_gp_mosaic_to_new_raster",
        "arcgis_pro_gp_clip_raster",
        "arcgis_pro_gp_resample",
        "arcgis_pro_gp_project_raster",
        "arcgis_pro_gp_nibble",
        "arcgis_pro_set_unique_value_renderer",
        "arcgis_pro_set_graduated_colors_renderer",
        "arcgis_pro_set_graduated_symbols_renderer",
        "arcgis_pro_set_simple_renderer",
        "arcgis_pro_set_heatmap_renderer",
        "arcgis_pro_update_label_expression",
        "arcgis_pro_set_label_font",
        "arcgis_pro_set_layout_element_position",
        "arcgis_pro_set_layout_element_visible",
        "arcgis_pro_update_legend_items",
        "arcgis_pro_create_layout",
        "arcgis_pro_zoom_to_layer",
        "arcgis_pro_zoom_to_selection",
        "arcgis_pro_layer_add_field_alias",
        "arcgis_pro_update_layer_cim",
        "arcgis_pro_repair_layer_source",
        "arcgis_pro_create_db_connection",
        "arcgis_pro_add_basemap",
        "arcgis_pro_create_map",
        "arcgis_pro_remove_map",
        "arcgis_pro_duplicate_map",
        "arcgis_pro_map_pan_to_extent",
        "arcgis_pro_set_time_slider",
        "arcgis_pro_gp_run_tool",
        "arcgis_pro_na_create_route_layer",
        "arcgis_pro_na_add_locations",
        "arcgis_pro_na_solve",
        "arcgis_pro_na_service_area",
        "arcgis_pro_na_od_matrix",
        "arcgis_pro_set_metadata",
        "arcgis_pro_gp_validate_topology",
    ]
    tools_export = [
        "arcgis_pro_export_layout_pdf",
        "arcgis_pro_export_layout_image",
        "arcgis_pro_export_report_pdf",
        "arcgis_pro_export_map_to_image",
    ]
    return _json_dumps(
        {
            "allow_write": write,
            "writes_required_env": "ARCGIS_PRO_MCP_ALLOW_WRITE=1",
            "export_root_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_EXPORT_ROOT", "").strip(),
            ),
            "gp_output_root_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT", "").strip(),
            ),
            "input_roots_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_INPUT_ROOTS", "").strip(),
            ),
            "tools_read_only": tools_read,
            "tools_require_allow_write": tools_write,
            "tools_export": tools_export,
            "note": (
                "无法通过 MCP 覆盖 Esri 全功能清单中的每一项；本服务仅封装部分 arcpy/arcpy.da/arcpy.mp 能力，"
                "发布/共享、深度学习、完整编辑会话等需专用方案或未实现。"
            ),
        },
    )


@mcp.tool(
    name="arcgis_pro_list_maps",
    description="",
)
def arcgis_pro_list_maps(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    names = [m.name for m in project.listMaps()]
    return json.dumps({"aprx_path": path, "maps": names}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_layouts",
    description="",
)
def arcgis_pro_list_layouts(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    names = [lyt.name for lyt in project.listLayouts()]
    return json.dumps({"aprx_path": path, "layouts": names}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_reports",
    description="",
)
def arcgis_pro_list_reports(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    if not hasattr(project, "listReports"):
        return json.dumps(
            {
                "aprx_path": path,
                "reports": [],
                "note": "当前 arcpy.mp.ArcGISProject 无 listReports，可能为较旧 Pro 版本。",
            },
            ensure_ascii=False,
            indent=2,
        )
    names = [r.name for r in project.listReports()]  # type: ignore[attr-defined]
    return json.dumps({"aprx_path": path, "reports": names}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_describe",
    description="",
)
def arcgis_pro_describe(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = dataset_path.strip().strip('"')
    if not p:
        raise RuntimeError("dataset_path 不能为空")
    try:
        summary = _describe_summary(arcpy, p)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(str(ex)[:800]) from ex
    return _json_dumps({"dataset_path": p, "describe": summary})


@mcp.tool(
    name="arcgis_pro_list_fields",
    description="",
)
def arcgis_pro_list_fields(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = dataset_path.strip().strip('"')
    if not p:
        raise RuntimeError("dataset_path 不能为空")
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
        raise RuntimeError(str(ex)[:800]) from ex
    return _json_dumps({"dataset_path": p, "fields": rows, "field_count": len(rows)})


@mcp.tool(
    name="arcgis_pro_project_connections",
    description="",
)
def arcgis_pro_project_connections(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    out: dict[str, Any] = {"aprx_path": path}
    specs = (
        ("listFolderConnections", "folder_connections"),
        ("listDatabases", "databases"),
        ("listToolboxes", "toolboxes"),
        ("listWorkspaces", "workspaces"),
    )
    for meth, key in specs:
        if not hasattr(project, meth):
            out[key] = []
            continue
        try:
            items = getattr(project, meth)()
            out[key] = [str(x) for x in (items or [])]
        except Exception as ex:  # noqa: BLE001
            out[key] = []
            out[f"{key}_error"] = str(ex)[:500]
    return _json_dumps(out)


@mcp.tool(
    name="arcgis_pro_project_summary",
    description="",
)
def arcgis_pro_project_summary(
    aprx_path: str,
    max_broken_list: int = 50,
) -> str:
    _, project, path = _open_project(aprx_path)
    maps = [m.name for m in project.listMaps()]
    layouts = [lyt.name for lyt in project.listLayouts()]
    reports: list[str] = []
    if hasattr(project, "listReports"):
        reports = [r.name for r in project.listReports()]  # type: ignore[attr-defined]

    try:
        cap = max(0, min(int(max_broken_list), 500))
    except (TypeError, ValueError):
        cap = 50
    broken_items: list[dict[str, Any]] = []
    try:
        broken_layers = project.listBrokenDataSources()
        for lyr in broken_layers[:cap] if cap else broken_layers:
            item: dict[str, Any] = {"name": getattr(lyr, "name", str(lyr))}
            try:
                item["long_name"] = lyr.longName
            except Exception:  # noqa: BLE001
                pass
            try:
                item["data_source"] = lyr.dataSource
            except Exception as ex:  # noqa: BLE001
                item["data_source_error"] = str(ex)[:300]
            broken_items.append(item)
        broken_total = len(broken_layers)
    except Exception as ex:  # noqa: BLE001
        broken_total = -1
        broken_error = str(ex)[:500]

    payload: dict[str, Any] = {
        "aprx_path": path,
        "map_count": len(maps),
        "layout_count": len(layouts),
        "report_count": len(reports),
        "maps": maps,
        "layouts": layouts,
        "reports": reports,
        "broken_data_source_total": broken_total,
        "broken_data_sources_sample": broken_items,
    }
    if broken_total < 0:
        payload["broken_data_sources_error"] = broken_error  # type: ignore[name-defined]

    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_layers",
    description="",
)
def arcgis_pro_list_layers(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)

    layers_out: list[dict[str, Any]] = []
    for lyr in target.listLayers():
        entry: dict[str, Any] = {
            "name": lyr.name,
            "long_name": getattr(lyr, "longName", None),
            "is_group_layer": lyr.isGroupLayer,
            "visible": lyr.visible,
        }
        try:
            entry["is_feature_layer"] = lyr.isFeatureLayer
        except Exception:  # noqa: BLE001
            pass
        try:
            entry["is_raster_layer"] = lyr.isRasterLayer
        except Exception:  # noqa: BLE001
            pass
        if not lyr.isGroupLayer:
            try:
                entry["data_source"] = lyr.dataSource
            except Exception as ex:  # noqa: BLE001
                entry["data_source_error"] = str(ex)[:500]
        layers_out.append(entry)

    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "layers": layers_out},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_list_tables",
    description="",
)
def arcgis_pro_list_tables(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    if not hasattr(target, "listTables"):
        return json.dumps(
            {
                "aprx_path": path,
                "map_name": map_name,
                "tables": [],
                "note": "当前 Map 对象无 listTables。",
            },
            ensure_ascii=False,
            indent=2,
        )
    tables_out: list[dict[str, Any]] = []
    for tbl in target.listTables():  # type: ignore[attr-defined]
        row: dict[str, Any] = {"name": tbl.name}
        try:
            row["visible"] = tbl.isVisible
        except Exception:  # noqa: BLE001
            pass
        try:
            row["data_source"] = tbl.dataSource
        except Exception as ex:  # noqa: BLE001
            row["data_source_error"] = str(ex)[:500]
        tables_out.append(row)

    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "tables": tables_out},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_map_spatial_reference",
    description="",
)
def arcgis_pro_map_spatial_reference(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    sr = target.spatialReference
    return json.dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "spatial_reference": _spatial_ref_dict(sr),
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_map_camera",
    description="",
)
def arcgis_pro_map_camera(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    cam = getattr(target, "defaultCamera", None)
    if cam is None:
        return json.dumps(
            {"aprx_path": path, "map_name": map_name, "camera": None},
            ensure_ascii=False,
            indent=2,
        )
    out: dict[str, Any] = {}
    for attr in ("scale", "heading", "pitch", "roll"):
        try:
            v = getattr(cam, attr, None)
            if v is not None:
                out[attr] = float(v)
        except Exception:  # noqa: BLE001
            pass
    try:
        if hasattr(cam, "getExtent"):
            out["extent"] = _extent_dict(cam.getExtent())
    except Exception as ex:  # noqa: BLE001
        out["extent_error"] = str(ex)[:300]
    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "camera": out},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_list_bookmarks",
    description="",
)
def arcgis_pro_list_bookmarks(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    bookmarks: list[dict[str, Any]] = []
    for bkmk in target.listBookmarks():
        row: dict[str, Any] = {"name": bkmk.name}
        try:
            row["description"] = getattr(bkmk, "description", None)
        except Exception:  # noqa: BLE001
            pass
        try:
            row["has_thumbnail"] = getattr(bkmk, "hasThumbnail", None)
        except Exception:  # noqa: BLE001
            pass
        try:
            bm = getattr(bkmk, "map", None)
            if bm is not None:
                row["map_name"] = getattr(bm, "name", str(bm))
        except Exception:  # noqa: BLE001
            pass
        try:
            mf = bkmk.mapFrame
            if mf is not None:
                row["map_frame"] = getattr(mf, "name", str(mf))
        except Exception:  # noqa: BLE001
            pass
        bookmarks.append(row)

    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "bookmarks": bookmarks},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_layer_properties",
    description="",
)
def arcgis_pro_layer_properties(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    lyr = _find_layer(target, layer_name)

    props: dict[str, Any] = {
        "aprx_path": path,
        "map_name": map_name,
        "name": lyr.name,
        "long_name": getattr(lyr, "longName", None),
        "visible": lyr.visible,
    }
    for attr in (
        "isFeatureLayer",
        "isRasterLayer",
        "isGroupLayer",
        "brightness",
        "contrast",
        "transparency",
        "showLabels",
    ):
        try:
            props[attr] = getattr(lyr, attr)
        except Exception:  # noqa: BLE001
            pass
    try:
        props["definition_query"] = lyr.definitionQuery
    except Exception:  # noqa: BLE001
        pass
    try:
        props["symbology_type"] = lyr.symbology.type
    except Exception as ex:  # noqa: BLE001
        props["symbology_error"] = str(ex)[:300]
    try:
        props["data_source"] = lyr.dataSource
    except Exception as ex:  # noqa: BLE001
        props["data_source_error"] = str(ex)[:500]
    try:
        props["is_snappable"] = lyr.isSnappable
    except Exception:  # noqa: BLE001
        pass
    try:
        props["is_selectable"] = lyr.isSelectable
    except Exception:  # noqa: BLE001
        pass

    return json.dumps(props, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_layout_elements",
    description="",
)
def arcgis_pro_list_layout_elements(
    aprx_path: str,
    layout_name: str,
    element_type: str = "",
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    et = element_type.strip() or None
    elements = layout.listElements(et) if et else layout.listElements()
    rows: list[dict[str, Any]] = []
    for elm in elements:
        row: dict[str, Any] = {
            "type": getattr(elm, "type", type(elm).__name__),
            "name": getattr(elm, "name", ""),
        }
        rows.append(row)

    return json.dumps(
        {
            "aprx_path": path,
            "layout_name": layout_name,
            "element_type_filter": et or "(all)",
            "elements": rows,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_mapframe_extent",
    description="",
)
def arcgis_pro_mapframe_extent(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        raise RuntimeError("Invalid arguments")

    out: dict[str, Any] = {
        "aprx_path": path,
        "layout_name": layout_name,
        "mapframe_name": mapframe_name,
    }
    try:
        out["map_name"] = mf.map.name
    except Exception as ex:  # noqa: BLE001
        out["map_name_error"] = str(ex)[:200]
    try:
        out["scale"] = float(mf.camera.scale)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["extent"] = _extent_dict(mf.getExtent())
    except Exception as ex:  # noqa: BLE001
        out["extent_error"] = str(ex)[:500]

    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_export_layout_pdf",
    description="",
)
def arcgis_pro_export_layout_pdf(
    aprx_path: str,
    layout_name: str,
    output_pdf_path: str,
    resolution_dpi: int = 300,
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    out_path = validate_output_in_export_root(output_pdf_path, "output_pdf_path")
    if not out_path.lower().endswith(".pdf"):
        raise RuntimeError("output_pdf_path 应以 .pdf 结尾")
    try:
        dpi = max(72, min(int(resolution_dpi), 960))
    except (TypeError, ValueError) as e:
        raise RuntimeError("resolution_dpi 必须为整数") from e
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    layout.exportToPDF(out_path, resolution=dpi)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "output_pdf_path": out_path,
            "resolution_dpi": dpi,
        },
    )


_SELECTION_TYPES = frozenset(
    {
        "NEW_SELECTION",
        "ADD_TO_SELECTION",
        "REMOVE_FROM_SELECTION",
        "SUBSET_SELECTION",
        "SWITCH_SELECTION",
        "CLEAR_SELECTION",
    },
)


def _clamp_dpi(resolution_dpi: int) -> int:
    try:
        return max(72, min(int(resolution_dpi), 960))
    except (TypeError, ValueError) as e:
        raise RuntimeError("resolution_dpi 必须为整数") from e


@mcp.tool(
    name="arcgis_pro_export_layout_image",
    description="",
)
def arcgis_pro_export_layout_image(
    aprx_path: str,
    layout_name: str,
    output_path: str,
    image_format: str = "png",
    resolution_dpi: int = 300,
    jpeg_quality: int = 90,
    transparent_background: bool = False,
    world_file: bool = False,
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    out_path = validate_output_in_export_root(output_path, "output_path")
    fmt = image_format.strip().lower()
    dpi = _clamp_dpi(resolution_dpi)
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if fmt == "png":
        if not out_path.lower().endswith(".png"):
            raise RuntimeError("PNG 输出路径应以 .png 结尾")
        layout.exportToPNG(  # type: ignore[attr-defined]
            out_path,
            resolution=dpi,
            transparent_background=transparent_background,
        )
    elif fmt in ("jpg", "jpeg"):
        if not out_path.lower().endswith((".jpg", ".jpeg")):
            raise RuntimeError("JPEG 输出路径应以 .jpg 或 .jpeg 结尾")
        try:
            jq = max(1, min(int(jpeg_quality), 100))
        except (TypeError, ValueError) as e:
            raise RuntimeError("jpeg_quality 须为 1–100 的整数") from e
        layout.exportToJPEG(out_path, resolution=dpi, jpeg_quality=jq)  # type: ignore[attr-defined]
    elif fmt == "tiff":
        if not out_path.lower().endswith((".tif", ".tiff")):
            raise RuntimeError("TIFF 输出路径应以 .tif 或 .tiff 结尾")
        layout.exportToTIFF(  # type: ignore[attr-defined]
            out_path,
            resolution=dpi,
            world_file=world_file,
        )
    else:
        raise RuntimeError('image_format 须为 "png"、"jpeg" 或 "tiff"')

    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "output_path": out_path,
            "image_format": fmt,
            "resolution_dpi": dpi,
        },
    )


@mcp.tool(
    name="arcgis_pro_save_project",
    description="",
)
def arcgis_pro_save_project(aprx_path: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    project.save()
    return _json_dumps({"ok": True, "aprx_path": path, "saved": True})


@mcp.tool(
    name="arcgis_pro_save_project_copy",
    description="",
)
def arcgis_pro_save_project_copy(aprx_path: str, output_aprx_path: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    out_path = validate_output_in_export_root(output_aprx_path, "output_aprx_path")
    if not out_path.lower().endswith(".aprx"):
        raise RuntimeError("output_aprx_path 应以 .aprx 结尾")
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    project.saveACopy(out_path)
    return _json_dumps(
        {"ok": True, "source_aprx": path, "output_aprx": out_path},
    )


@mcp.tool(
    name="arcgis_pro_set_layer_visible",
    description="",
)
def arcgis_pro_set_layer_visible(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    visible: bool,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    lyr.visible = bool(visible)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "visible": lyr.visible,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_layer_transparency",
    description="",
)
def arcgis_pro_set_layer_transparency(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    transparency_percent: int,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    t = max(0, min(int(transparency_percent), 100))
    lyr.transparency = t
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "transparency": t,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_definition_query",
    description="",
)
def arcgis_pro_set_definition_query(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    definition_query: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    sql = definition_query.strip()
    if len(sql) > 8000:
        raise RuntimeError("definition_query 过长（>8000）")
    lyr.definitionQuery = sql
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "definition_query": sql,
        },
    )


@mcp.tool(
    name="arcgis_pro_select_layer_by_attribute",
    description="",
)
def arcgis_pro_select_layer_by_attribute(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    selection_type: str,
    where_clause: str = "",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    st = selection_type.strip().upper()
    if st not in _SELECTION_TYPES:
        raise RuntimeError("Invalid arguments")
    wc = where_clause.strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause 过长")
    arcpy.management.SelectLayerByAttribute(lyr, st, wc or "")
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "selection_type": st,
        },
    )


@mcp.tool(
    name="arcgis_pro_make_feature_layer",
    description="",
)
def arcgis_pro_make_feature_layer(
    dataset_path: str,
    out_layer_name: str,
    where_clause: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    name = _validate_view_name(out_layer_name, "out_layer_name")
    wc = (where_clause or "").strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause too long")
    result = arcpy.management.MakeFeatureLayer(p, name, wc or None)
    created_name = name
    try:
        created_name = str(result.getOutput(0))
    except Exception:  # noqa: BLE001
        pass
    count = gp_allowlist.gp_get_count_layer(arcpy, created_name)
    return _json_dumps({"ok": True, "dataset_path": p, "layer_name": created_name, "count": count})


@mcp.tool(
    name="arcgis_pro_make_table_view",
    description="",
)
def arcgis_pro_make_table_view(
    dataset_path: str,
    out_view_name: str,
    where_clause: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    name = _validate_view_name(out_view_name, "out_view_name")
    wc = (where_clause or "").strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause too long")
    result = arcpy.management.MakeTableView(p, name, wc or None)
    created_name = name
    try:
        created_name = str(result.getOutput(0))
    except Exception:  # noqa: BLE001
        pass
    count = gp_allowlist.gp_get_count(arcpy, created_name)
    return _json_dumps({"ok": True, "dataset_path": p, "view_name": created_name, "count": count})


@mcp.tool(
    name="arcgis_pro_mapframe_zoom_to_bookmark",
    description="",
)
def arcgis_pro_mapframe_zoom_to_bookmark(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    bookmark_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        raise RuntimeError("Invalid arguments")
    bkmk = None
    try:
        for b in mf.map.listBookmarks():
            if b.name == bookmark_name:
                bkmk = b
                break
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError("Invalid arguments") from e
    if bkmk is None:
        names = [b.name for b in mf.map.listBookmarks()]
        raise RuntimeError("Invalid arguments")
    mf.zoomToBookmark(bkmk)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "mapframe_name": mapframe_name,
            "bookmark_name": bookmark_name,
        },
    )


@mcp.tool(
    name="arcgis_pro_add_layer_from_path",
    description="",
)
def arcgis_pro_add_layer_from_path(
    aprx_path: str,
    map_name: str,
    data_path: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    dp = validate_input_path_optional(data_path, "data_path")
    m.addDataFromPath(dp)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "data_path": dp},
    )


@mcp.tool(
    name="arcgis_pro_remove_layer",
    description="",
)
def arcgis_pro_remove_layer(aprx_path: str, map_name: str, layer_name: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    m.removeLayer(lyr)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "removed": layer_name},
    )


@mcp.tool(
    name="arcgis_pro_add_table_from_path",
    description="",
)
def arcgis_pro_add_table_from_path(
    aprx_path: str,
    map_name: str,
    table_path: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    tp = validate_input_path_optional(table_path, "table_path")
    if not hasattr(m, "addDataFromPath"):
        raise RuntimeError("当前 Map 对象不支持 addDataFromPath")
    m.addDataFromPath(tp)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "table_path": tp},
    )


@mcp.tool(
    name="arcgis_pro_remove_table",
    description="",
)
def arcgis_pro_remove_table(aprx_path: str, map_name: str, table_name: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    if not hasattr(m, "listTables"):
        raise RuntimeError("当前 Map 对象不支持 listTables")
    target = None
    for tbl in m.listTables():  # type: ignore[attr-defined]
        if tbl.name == table_name:
            target = tbl
            break
    if target is None:
        names = [tbl.name for tbl in m.listTables()]  # type: ignore[attr-defined]
        raise RuntimeError("Invalid arguments")
    if hasattr(m, "removeTable"):
        m.removeTable(target)  # type: ignore[attr-defined]
    elif hasattr(m, "removeItem"):
        m.removeItem(target)  # type: ignore[attr-defined]
    else:
        raise RuntimeError("当前 Map 对象不支持移除独立表")
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "removed": table_name},
    )


@mcp.tool(
    name="arcgis_pro_gp_list_registered",
    description="",
)
def arcgis_pro_gp_list_registered() -> str:
    return _json_dumps({"gp_tools": gp_allowlist.list_registered_gp_tools()})


@mcp.tool(
    name="arcgis_pro_gp_get_count",
    description="",
)
def arcgis_pro_gp_get_count(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    cnt = gp_allowlist.gp_get_count(arcpy, p)
    return _json_dumps({"dataset_path": p, "count": cnt})


@mcp.tool(
    name="arcgis_pro_gp_get_raster_property",
    description="",
)
def arcgis_pro_gp_get_raster_property(
    raster_path: str,
    property_type: str,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(raster_path, "raster_path")
    val = gp_allowlist.gp_get_raster_property(arcpy, p, property_type)
    return _json_dumps(
        {"raster_path": p, "property_type": property_type.strip().upper(), "value": val},
    )


@mcp.tool(
    name="arcgis_pro_gp_get_cell_value",
    description="",
)
def arcgis_pro_gp_get_cell_value(
    raster_path: str,
    location_xy: str,
    band_index: int | None = None,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(raster_path, "raster_path")
    val = gp_allowlist.gp_get_cell_value(arcpy, p, location_xy, band_index)
    return _json_dumps(
        {"raster_path": p, "location_xy": location_xy.strip(), "value": val},
    )


@mcp.tool(
    name="arcgis_pro_gp_test_schema_lock",
    description="",
)
def arcgis_pro_gp_test_schema_lock(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    val = gp_allowlist.gp_test_schema_lock(arcpy, p)
    return _json_dumps({"dataset_path": p, "schema_lock": val})


@mcp.tool(
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
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = workspace_listing.list_feature_classes(
        arcpy, ws, feature_dataset, feature_type, wild_card, max_items
    )
    return _json_dumps({"workspace_path": ws, "feature_classes": names})


@mcp.tool(
    name="arcgis_pro_workspace_list_rasters",
    description="",
)
def arcgis_pro_workspace_list_rasters(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = workspace_listing.list_rasters(arcpy, ws, wild_card, max_items)
    return _json_dumps({"workspace_path": ws, "rasters": names})


@mcp.tool(
    name="arcgis_pro_workspace_list_tables",
    description="",
)
def arcgis_pro_workspace_list_tables(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = workspace_listing.list_tables(arcpy, ws, wild_card, max_items)
    return _json_dumps({"workspace_path": ws, "tables": names})


@mcp.tool(
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
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    rows = da_read.table_sample(
        arcpy, p, fields, where_clause, max_rows, include_shape_wkt
    )
    return _json_dumps({"dataset_path": p, "row_count": len(rows), "rows": rows})


@mcp.tool(
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
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    rows = _query_rows(
        arcpy,
        p,
        fields,
        where_clause,
        order_by,
        max_rows,
        offset,
        include_shape_wkt,
    )
    return _json_dumps(
        {
            "dataset_path": p,
            "field_count": len([f for f in fields if f.strip()]),
            "row_count": len(rows),
            "rows": rows,
        },
    )


@mcp.tool(
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
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    vals = da_read.distinct_values(
        arcpy, p, field_name, where_clause, max_values, max_rows_scanned
    )
    return _json_dumps(
        {
            "dataset_path": p,
            "field_name": field_name.strip(),
            "value_count": len(vals),
            "values": vals,
        },
    )


_PLACE_LAYER = frozenset({"BEFORE", "AFTER"})


@mcp.tool(
    name="arcgis_pro_create_group_layer",
    description="",
)
def arcgis_pro_create_group_layer(aprx_path: str, map_name: str, group_layer_name: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    gn = group_layer_name.strip()
    if not gn:
        raise RuntimeError("group_layer_name 不能为空")
    group = m.createGroupLayer(gn)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "group_layer_name": getattr(group, "name", gn),
        },
    )


@mcp.tool(
    name="arcgis_pro_move_layer",
    description="",
)
def arcgis_pro_move_layer(
    aprx_path: str,
    map_name: str,
    reference_layer_name: str,
    layer_to_move_name: str,
    placement: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    ref = _find_layer(m, reference_layer_name)
    mov = _find_layer(m, layer_to_move_name)
    pl = placement.strip().upper()
    if pl not in _PLACE_LAYER:
        raise RuntimeError("Invalid arguments")
    m.moveLayer(ref, mov, pl)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "reference_layer_name": reference_layer_name,
            "layer_to_move_name": layer_to_move_name,
            "placement": pl,
        },
    )


@mcp.tool(
    name="arcgis_pro_rename_layer",
    description="",
)
def arcgis_pro_rename_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    new_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    nn = new_name.strip()
    if not nn:
        raise RuntimeError("new_name 不能为空")
    lyr.name = nn
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "new_name": nn,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_map_reference_scale",
    description="",
)
def arcgis_pro_set_map_reference_scale(
    aprx_path: str,
    map_name: str,
    reference_scale: float,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    rs = float(reference_scale)
    if rs < 0:
        raise RuntimeError("reference_scale 不能为负")
    m.referenceScale = rs  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "reference_scale": rs,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_map_default_camera",
    description="",
)
def arcgis_pro_set_map_default_camera(
    aprx_path: str,
    map_name: str,
    scale: float | None = None,
    heading: float | None = None,
    pitch: float | None = None,
    roll: float | None = None,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    cam = m.defaultCamera
    if cam is None:
        raise RuntimeError("地图无 defaultCamera")
    updated: dict[str, float] = {}
    if scale is not None:
        cam.scale = float(scale)
        updated["scale"] = float(scale)
    if heading is not None:
        cam.heading = float(heading)
        updated["heading"] = float(heading)
    if pitch is not None:
        cam.pitch = float(pitch)
        updated["pitch"] = float(pitch)
    if roll is not None:
        cam.roll = float(roll)
        updated["roll"] = float(roll)
    if not updated:
        raise RuntimeError("至少提供一个 scale/heading/pitch/roll")
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "updated": updated},
    )


_OVERLAP_LOCATION = frozenset(
    {
        "INTERSECT",
        "WITHIN_A_DISTANCE",
        "WITHIN_A_DISTANCE_GEODESIC",
        "WITHIN_A_DISTANCE_3D",
        "CONTAINS",
        "COMPLETELY_CONTAINS",
        "COMPLETELY_WITHIN",
        "HAVE_THEIR_CENTER_IN",
        "SHARE_A_LINE_SEGMENT_WITH",
        "CROSSED_BY_THE_OUTLINE_OF",
        "BOUNDARY_TOUCHES",
        "ARE_IDENTICAL_TO",
        "TOUCHES",
        "OVERLAP",
        "CROSSES",
        "WITHIN",
    },
)
_DISTANCE_OVERLAP = frozenset(
    {"WITHIN_A_DISTANCE", "WITHIN_A_DISTANCE_GEODESIC", "WITHIN_A_DISTANCE_3D"},
)
_JOIN_TYPES = frozenset({"KEEP_ALL", "KEEP_COMMON"})


@mcp.tool(
    name="arcgis_pro_select_layer_by_location",
    description="",
)
def arcgis_pro_select_layer_by_location(
    aprx_path: str,
    map_name: str,
    input_layer_name: str,
    overlap_type: str,
    selecting_layer_name: str,
    search_distance: str = "",
    selection_type: str = "NEW_SELECTION",
    invert_spatial_relationship: bool = False,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    input_lyr = _find_layer(m, input_layer_name)
    sel_lyr = _find_layer(m, selecting_layer_name)
    ov = overlap_type.strip().upper()
    if ov not in _OVERLAP_LOCATION:
        raise RuntimeError("Invalid arguments")
    sd = (search_distance or "").strip()
    if ov in _DISTANCE_OVERLAP and not sd:
        raise RuntimeError("当前 overlap_type 必须提供 search_distance")
    st = selection_type.strip().upper()
    if st not in _SELECTION_TYPES:
        raise RuntimeError("Invalid arguments")
    inv = "INVERT" if invert_spatial_relationship else "NOT_INVERT"
    arcpy.management.SelectLayerByLocation(
        input_lyr,
        ov,
        sel_lyr,
        sd,
        st,
        inv,
    )
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "input_layer_name": input_layer_name,
            "selecting_layer_name": selecting_layer_name,
            "overlap_type": ov,
            "selection_type": st,
        },
    )


@mcp.tool(
    name="arcgis_pro_clear_map_selection",
    description="",
)
def arcgis_pro_clear_map_selection(
    aprx_path: str,
    map_name: str,
    scope: str,
    layer_name: str = "",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    sc = scope.strip().lower()
    cleared = 0
    if sc == "layer":
        ln = layer_name.strip()
        if not ln:
            raise RuntimeError("scope=layer 时必须提供 layer_name")
        lyr = _find_layer(m, ln)
        arcpy.management.SelectLayerByAttribute(lyr, "CLEAR_SELECTION", "")
        cleared = 1
    elif sc == "all_layers":
        for lyr in m.listLayers():
            if lyr.isGroupLayer:
                continue
            try:
                if lyr.isFeatureLayer:
                    arcpy.management.SelectLayerByAttribute(lyr, "CLEAR_SELECTION", "")
                    cleared += 1
            except Exception:  # noqa: BLE001
                pass
    else:
        raise RuntimeError('scope 须为 "layer" 或 "all_layers"')
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layers_cleared": cleared},
    )


@mcp.tool(
    name="arcgis_pro_layer_selection_count",
    description="",
)
def arcgis_pro_layer_selection_count(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    cnt = gp_allowlist.gp_get_count_layer(arcpy, lyr)
    return _json_dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "selected_or_total_count": cnt,
        },
    )


@mcp.tool(
    name="arcgis_pro_layer_selection_fids",
    description="",
)
def arcgis_pro_layer_selection_fids(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    max_fids: int = 2000,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    cap = max(1, min(int(max_fids), 50_000))
    fids: list[Any] = []
    truncated = False
    with arcpy.da.SearchCursor(lyr, ["OID@"], None) as cur:  # type: ignore[attr-defined]
        for i, row in enumerate(cur):
            if i >= cap:
                truncated = True
                break
            fids.append(row[0])
    return _json_dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "fids": fids,
            "truncated": truncated,
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_buffer",
    description="",
)
def arcgis_pro_gp_buffer(
    in_features: str,
    out_feature_class: str,
    buffer_distance_or_field: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_buffer(arcpy, in_features, out_feature_class, buffer_distance_or_field)
    return _json_dumps(
        {
            "ok": True,
            "in_features": validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_clip",
    description="",
)
def arcgis_pro_gp_clip(
    in_features: str,
    clip_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_clip(arcpy, in_features, clip_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_features": validate_input_path_optional(in_features, "in_features"),
            "clip_features": validate_input_path_optional(clip_features, "clip_features"),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_analysis_select",
    description="",
)
def arcgis_pro_gp_analysis_select(
    in_features: str,
    out_feature_class: str,
    where_clause: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_select(arcpy, in_features, out_feature_class, where_clause)
    return _json_dumps(
        {
            "ok": True,
            "in_features": validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_copy_features",
    description="",
)
def arcgis_pro_gp_copy_features(in_features: str, out_feature_class: str) -> str:
    arcpy = _arcpy()
    gp_write.run_copy_features(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_features": validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_add_join",
    description="",
)
def arcgis_pro_add_join(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    layer_field: str,
    join_table_path: str,
    join_field: str,
    join_type: str = "KEEP_ALL",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    jt = join_type.strip().upper()
    if jt not in _JOIN_TYPES:
        raise RuntimeError("Invalid arguments")
    jpath = validate_input_path_optional(join_table_path, "join_table_path")
    arcpy.management.AddJoin(lyr, layer_field.strip(), jpath, join_field.strip(), jt)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "join_table_path": jpath,
        },
    )


@mcp.tool(
    name="arcgis_pro_remove_join",
    description="",
)
def arcgis_pro_remove_join(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    join_name: str = "",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    jn = join_name.strip()
    arcpy.management.RemoveJoin(lyr, jn)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "join_name": jn or "(all)",
        },
    )


def _find_layout_text_element(layout: Any, element_name: str, element_type: str) -> Any:
    en = element_name.strip()
    et = element_type.strip().upper()
    order: list[str]
    if et == "TEXT_ELEMENT":
        order = ["TEXT_ELEMENT"]
    elif et in ("TEXT_GRAPHIC_ELEMENT", "GRAPHIC_ELEMENT"):
        order = ["TEXT_GRAPHIC_ELEMENT"]
    elif not et:
        order = ["TEXT_ELEMENT", "TEXT_GRAPHIC_ELEMENT"]
    else:
        raise RuntimeError(
            'element_type 须为空、TEXT_ELEMENT 或 TEXT_GRAPHIC_ELEMENT（也可用 GRAPHIC_ELEMENT）'
        )
    for tt in order:
        for elm in layout.listElements(tt):
            if getattr(elm, "name", "") == en:
                return elm
    raise RuntimeError("Invalid arguments")


@mcp.tool(
    name="arcgis_pro_update_layout_text_element",
    description="",
)
def arcgis_pro_update_layout_text_element(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    text: str,
    element_type: str = "",
    allow_dynamic_text_overwrite: bool = False,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    elm = _find_layout_text_element(layout, element_name, element_type)
    old = getattr(elm, "text", "") or ""
    if "<dyn" in old.lower() and not allow_dynamic_text_overwrite:
        raise RuntimeError("检测到动态文本，若需覆盖请设置 allow_dynamic_text_overwrite=true")
    elm.text = text
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "element_name": element_name.strip(),
        },
    )


@mcp.tool(
    name="arcgis_pro_set_mapframe_extent",
    description="",
)
def arcgis_pro_set_mapframe_extent(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    spatial_reference_wkid: int | None = None,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        raise RuntimeError("Invalid arguments")
    ext = arcpy.Extent(float(xmin), float(ymin), float(xmax), float(ymax))  # type: ignore[attr-defined]
    if spatial_reference_wkid is not None:
        ext.spatialReference = arcpy.SpatialReference(int(spatial_reference_wkid))  # type: ignore[attr-defined]
    else:
        try:
            ext.spatialReference = mf.map.spatialReference
        except Exception:  # noqa: BLE001
            pass
    mf.setExtent(ext)  # type: ignore[attr-defined]
    out: dict[str, Any] = {
        "ok": True,
        "aprx_path": path,
        "layout_name": layout_name,
        "mapframe_name": mapframe_name,
    }
    try:
        out["extent_after"] = _extent_dict(mf.getExtent())
    except Exception as ex:  # noqa: BLE001
        out["extent_read_error"] = str(ex)[:300]
    return _json_dumps(out)


def _symbology_template_path(path: str) -> str:
    p = validate_input_path_optional(path, "symbology_layer_path")
    pl = p.lower()
    if not (pl.endswith(".lyrx") or pl.endswith(".lyr")):
        raise RuntimeError("symbology_layer_path 须为 .lyrx 或 .lyr")
    return p


@mcp.tool(
    name="arcgis_pro_set_map_spatial_reference",
    description="",
)
def arcgis_pro_set_map_spatial_reference(
    aprx_path: str,
    map_name: str,
    wkid: int,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    m.spatialReference = arcpy.SpatialReference(int(wkid))  # type: ignore[attr-defined]
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "wkid": int(wkid)},
    )


@mcp.tool(
    name="arcgis_pro_layer_replace_data_source",
    description="",
)
def arcgis_pro_layer_replace_data_source(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    workspace_path: str,
    dataset_name: str,
    dataset_type: str,
    validate: bool = True,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    dt = dataset_type.strip()
    if not dt:
        raise RuntimeError("dataset_type 不能为空")
    dn = dataset_name.strip()
    if not dn:
        raise RuntimeError("dataset_name 不能为空")
    lyr.replaceDataSource(ws, dt, dn, bool(validate))  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "workspace_path": ws,
            "dataset_name": dn,
            "dataset_type": dt,
        },
    )


@mcp.tool(
    name="arcgis_pro_apply_symbology_from_layer",
    description="",
)
def arcgis_pro_apply_symbology_from_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    symbology_layer_path: str,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    sp = _symbology_template_path(symbology_layer_path)
    arcpy.management.ApplySymbologyFromLayer(lyr, sp)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "symbology_layer_path": sp,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_layer_scale_range",
    description="",
)
def arcgis_pro_set_layer_scale_range(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    minimum_scale: float | None = None,
    maximum_scale: float | None = None,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    updated: dict[str, float] = {}
    if minimum_scale is not None:
        lyr.minimumScale = float(minimum_scale)  # type: ignore[attr-defined]
        updated["minimum_scale"] = float(minimum_scale)
    if maximum_scale is not None:
        lyr.maximumScale = float(maximum_scale)  # type: ignore[attr-defined]
        updated["maximum_scale"] = float(maximum_scale)
    if not updated:
        raise RuntimeError("至少提供 minimum_scale 或 maximum_scale")
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "updated": updated,
        },
    )


@mcp.tool(
    name="arcgis_pro_toggle_layer_labels",
    description="",
)
def arcgis_pro_toggle_layer_labels(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    show_labels: bool,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    lyr.showLabels = bool(show_labels)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "show_labels": lyr.showLabels,
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_dissolve",
    description="",
)
def arcgis_pro_gp_dissolve(
    in_features: str,
    out_feature_class: str,
    dissolve_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_dissolve(arcpy, in_features, out_feature_class, dissolve_field)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_intersect",
    description="",
)
def arcgis_pro_gp_intersect(
    in_feature_paths: list[str],
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_intersect(arcpy, in_feature_paths, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_feature_paths": [normalize_path(p) for p in in_feature_paths],
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_union",
    description="",
)
def arcgis_pro_gp_union(
    in_feature_paths: list[str],
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_union(arcpy, in_feature_paths, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_feature_paths": [normalize_path(p) for p in in_feature_paths],
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_erase",
    description="",
)
def arcgis_pro_gp_erase(
    in_features: str,
    erase_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_erase(arcpy, in_features, erase_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "erase_features": normalize_path(erase_features),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_spatial_join",
    description="",
)
def arcgis_pro_gp_spatial_join(
    target_features: str,
    join_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_spatial_join(arcpy, target_features, join_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "target_features": normalize_path(target_features),
            "join_features": normalize_path(join_features),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_statistics",
    description="",
)
def arcgis_pro_gp_statistics(
    in_table: str,
    out_table: str,
    statistics_fields: str,
    case_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_statistics(arcpy, in_table, out_table, statistics_fields, case_field)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "out_table": normalize_path(out_table),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_frequency",
    description="",
)
def arcgis_pro_gp_frequency(
    in_table: str,
    out_table: str,
    frequency_fields: str,
    summary_fields: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_frequency(arcpy, in_table, out_table, frequency_fields, summary_fields)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "out_table": normalize_path(out_table),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_table_select",
    description="",
)
def arcgis_pro_gp_table_select(
    in_table: str,
    out_table: str,
    where_clause: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_table_select(arcpy, in_table, out_table, where_clause)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "out_table": normalize_path(out_table),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_merge",
    description="",
)
def arcgis_pro_gp_merge(
    in_feature_paths: list[str],
    output_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_merge(arcpy, in_feature_paths, output_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_feature_paths": [normalize_path(p) for p in in_feature_paths],
            "output_feature_class": normalize_path(output_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_project",
    description="",
)
def arcgis_pro_gp_project(
    in_dataset: str,
    out_dataset: str,
    out_wkid: int,
    transform_method: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_project(arcpy, in_dataset, out_dataset, out_wkid, transform_method)
    return _json_dumps(
        {
            "ok": True,
            "in_dataset": normalize_path(in_dataset),
            "out_dataset": normalize_path(out_dataset),
            "out_wkid": int(out_wkid),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_add_field",
    description="",
)
def arcgis_pro_gp_add_field(
    in_table: str,
    field_name: str,
    field_type: str,
    field_length: int | None = None,
) -> str:
    arcpy = _arcpy()
    gp_schema.run_add_field(arcpy, in_table, field_name, field_type, field_length)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "field_name": field_name.strip(),
            "field_type": field_type.strip().upper(),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_delete_field",
    description="",
)
def arcgis_pro_gp_delete_field(in_table: str, drop_field: str) -> str:
    arcpy = _arcpy()
    gp_schema.run_delete_field(arcpy, in_table, drop_field)
    return _json_dumps(
        {"ok": True, "in_table": normalize_path(in_table), "drop_field": drop_field.strip()},
    )


@mcp.tool(
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
    arcpy = _arcpy()
    n, truncated = da_write.update_field_constant(
        arcpy,
        dataset_path,
        field_name,
        value_string,
        where_clause,
        max_rows_updated,
    )
    return _json_dumps(
        {
            "ok": True,
            "dataset_path": normalize_path(dataset_path),
            "field_name": field_name.strip(),
            "rows_updated": n,
            "truncated": truncated,
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_export_features",
    description="",
)
def arcgis_pro_gp_export_features(in_features: str, out_path: str) -> str:
    arcpy = _arcpy()
    gp_write.run_export_features(arcpy, in_features, out_path)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "out_path": normalize_path(out_path),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_export_table",
    description="",
)
def arcgis_pro_gp_export_table(in_table: str, out_path: str) -> str:
    arcpy = _arcpy()
    gp_write.run_export_table(arcpy, in_table, out_path)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "out_path": normalize_path(out_path),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_near",
    description="",
)
def arcgis_pro_gp_near(in_features: str, near_features: str) -> str:
    arcpy = _arcpy()
    gp_write.run_near(arcpy, in_features, near_features)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "near_features": normalize_path(near_features),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_generate_near_table",
    description="",
)
def arcgis_pro_gp_generate_near_table(
    in_features: str,
    near_features: str,
    out_table: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_generate_near_table(arcpy, in_features, near_features, out_table)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "near_features": normalize_path(near_features),
            "out_table": normalize_path(out_table),
        },
    )


# ---------------------------------------------------------------------------
# Phase 1: Data Write Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_da_insert_features",
    description="",
)
def arcgis_pro_da_insert_features(
    dataset_path: str,
    fields: list[str],
    rows: list[list[Any]],
) -> str:
    arcpy = _arcpy()
    n = da_write.insert_features(arcpy, dataset_path, fields, rows)
    return _json_dumps(
        {"ok": True, "dataset_path": normalize_path(dataset_path), "rows_inserted": n},
    )


@mcp.tool(
    name="arcgis_pro_da_update_features",
    description="",
)
def arcgis_pro_da_update_features(
    dataset_path: str,
    field_name: str,
    updates: dict[str, Any],
    where_clause: str = "",
    max_rows_updated: int = 1000,
) -> str:
    arcpy = _arcpy()
    n, truncated = da_write.update_features(
        arcpy, dataset_path, field_name, updates, where_clause, max_rows_updated
    )
    return _json_dumps(
        {
            "ok": True,
            "dataset_path": normalize_path(dataset_path),
            "rows_updated": n,
            "truncated": truncated,
        },
    )


@mcp.tool(
    name="arcgis_pro_da_delete_selected",
    description="",
)
def arcgis_pro_da_delete_selected(
    dataset_path: str,
    where_clause: str,
    max_rows_deleted: int = 1000,
) -> str:
    arcpy = _arcpy()
    n, truncated = da_write.delete_selected(arcpy, dataset_path, where_clause, max_rows_deleted)
    return _json_dumps(
        {
            "ok": True,
            "dataset_path": normalize_path(dataset_path),
            "rows_deleted": n,
            "truncated": truncated,
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_calculate_field",
    description="",
)
def arcgis_pro_gp_calculate_field(
    in_table: str,
    field_name: str,
    expression: str,
    expression_type: str = "PYTHON3",
    code_block: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_table, "in_table")
    fn = field_name.strip()
    if not fn:
        raise RuntimeError("field_name 不能为空")
    expr = expression.strip()
    if not expr:
        raise RuntimeError("expression 不能为空")
    et = expression_type.strip().upper()
    if et not in ("PYTHON3", "ARCADE", "VB", "PYTHON", "PYTHON_9.3"):
        raise RuntimeError("expression_type 须为 PYTHON3、ARCADE 或 VB")
    cb = (code_block or "").strip()
    if cb:
        arcpy.management.CalculateField(p, fn, expr, et, cb)
    else:
        arcpy.management.CalculateField(p, fn, expr, et)
    return _json_dumps({"ok": True, "in_table": normalize_path(in_table), "field_name": fn})


@mcp.tool(
    name="arcgis_pro_gp_calculate_geometry",
    description="",
)
def arcgis_pro_gp_calculate_geometry(
    in_features: str,
    geometry_property: list[list[str]],
    length_unit: str = "",
    area_unit: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_features, "in_features")
    if not geometry_property:
        raise RuntimeError("geometry_property 不能为空")
    lu = (length_unit or "").strip()
    au = (area_unit or "").strip()
    kwargs: dict[str, Any] = {}
    if lu:
        kwargs["length_unit"] = lu
    if au:
        kwargs["area_unit"] = au
    arcpy.management.CalculateGeometryAttributes(p, geometry_property, **kwargs)
    return _json_dumps({"ok": True, "in_features": normalize_path(in_features)})


@mcp.tool(
    name="arcgis_pro_gp_append",
    description="",
)
def arcgis_pro_gp_append(
    inputs: list[str],
    target: str,
    schema_type: str = "TEST",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    if not inputs:
        raise RuntimeError("inputs 不能为空")
    ins = [validate_input_path_optional(p, f"input_{i}") for i, p in enumerate(inputs)]
    tgt = validate_input_path_optional(target, "target")
    st = schema_type.strip().upper()
    if st not in ("TEST", "NO_TEST"):
        raise RuntimeError("schema_type 须为 TEST 或 NO_TEST")
    arcpy.management.Append(ins, tgt, st)
    return _json_dumps({"ok": True, "target": normalize_path(target), "input_count": len(ins)})


@mcp.tool(
    name="arcgis_pro_gp_delete_features",
    description="",
)
def arcgis_pro_gp_delete_features(in_features: str) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_features, "in_features")
    arcpy.management.DeleteFeatures(p)
    return _json_dumps({"ok": True, "in_features": normalize_path(in_features)})


@mcp.tool(
    name="arcgis_pro_gp_truncate_table",
    description="",
)
def arcgis_pro_gp_truncate_table(in_table: str) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_table, "in_table")
    arcpy.management.TruncateTable(p)
    return _json_dumps({"ok": True, "in_table": normalize_path(in_table)})


# ---------------------------------------------------------------------------
# Phase 2: Create Data
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_create_feature_class",
    description="",
)
def arcgis_pro_gp_create_feature_class(
    out_path: str,
    out_name: str,
    geometry_type: str,
    spatial_reference_wkid: int | None = None,
) -> str:
    arcpy = _arcpy()
    result_path = gp_create.run_create_feature_class(
        arcpy, out_path, out_name, geometry_type, spatial_reference_wkid
    )
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_create_table",
    description="",
)
def arcgis_pro_gp_create_table(out_path: str, out_name: str) -> str:
    arcpy = _arcpy()
    result_path = gp_create.run_create_table(arcpy, out_path, out_name)
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_create_file_gdb",
    description="",
)
def arcgis_pro_gp_create_file_gdb(out_folder_path: str, out_name: str) -> str:
    arcpy = _arcpy()
    result_path = gp_create.run_create_file_gdb(arcpy, out_folder_path, out_name)
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_create_feature_dataset",
    description="",
)
def arcgis_pro_gp_create_feature_dataset(
    out_dataset_path: str,
    out_name: str,
    spatial_reference_wkid: int,
) -> str:
    arcpy = _arcpy()
    result_path = gp_create.run_create_feature_dataset(
        arcpy, out_dataset_path, out_name, spatial_reference_wkid
    )
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_copy_feature_class",
    description="",
)
def arcgis_pro_gp_copy_feature_class(in_features: str, out_feature_class: str) -> str:
    arcpy = _arcpy()
    gp_create.run_copy_feature_class(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_rename_dataset",
    description="",
)
def arcgis_pro_gp_rename_dataset(in_data: str, out_data: str) -> str:
    arcpy = _arcpy()
    gp_create.run_rename_dataset(arcpy, in_data, out_data)
    return _json_dumps(
        {"ok": True, "in_data": normalize_path(in_data), "out_data": out_data.strip()},
    )


@mcp.tool(
    name="arcgis_pro_gp_delete_dataset",
    description="",
)
def arcgis_pro_gp_delete_dataset(in_data: str) -> str:
    arcpy = _arcpy()
    gp_create.run_delete_dataset(arcpy, in_data)
    return _json_dumps({"ok": True, "deleted": normalize_path(in_data)})


@mcp.tool(
    name="arcgis_pro_gp_alter_field",
    description="",
)
def arcgis_pro_gp_alter_field(
    in_table: str,
    field_name: str,
    new_field_name: str = "",
    new_field_alias: str = "",
) -> str:
    arcpy = _arcpy()
    gp_create.run_alter_field(arcpy, in_table, field_name, new_field_name, new_field_alias)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "field_name": field_name.strip(),
            "new_field_name": new_field_name.strip() or "(unchanged)",
            "new_field_alias": new_field_alias.strip() or "(unchanged)",
        },
    )


# ---------------------------------------------------------------------------
# Phase 3: Import / Export Conversion
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_import_csv_to_table",
    description="",
)
def arcgis_pro_gp_import_csv_to_table(
    in_rows: str,
    out_path: str,
    out_name: str,
) -> str:
    arcpy = _arcpy()
    result_path = gp_convert.run_table_to_table(arcpy, in_rows, out_path, out_name)
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
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
    arcpy = _arcpy()
    gp_convert.run_xy_table_to_point(
        arcpy, in_table, out_feature_class, x_field, y_field, spatial_reference_wkid
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_json_to_features",
    description="",
)
def arcgis_pro_gp_json_to_features(in_json_file: str, out_features: str) -> str:
    arcpy = _arcpy()
    gp_convert.run_json_to_features(arcpy, in_json_file, out_features)
    return _json_dumps({"ok": True, "out_features": normalize_path(out_features)})


@mcp.tool(
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
    arcpy = _arcpy()
    gp_convert.run_features_to_json(
        arcpy, in_features, out_json_file, format_json, include_z_values, include_m_values
    )
    return _json_dumps({"ok": True, "out_json_file": normalize_path(out_json_file)})


@mcp.tool(
    name="arcgis_pro_gp_kml_to_layer",
    description="",
)
def arcgis_pro_gp_kml_to_layer(in_kml_file: str, output_folder: str) -> str:
    arcpy = _arcpy()
    gp_convert.run_kml_to_layer(arcpy, in_kml_file, output_folder)
    return _json_dumps({"ok": True, "output_folder": normalize_path(output_folder)})


@mcp.tool(
    name="arcgis_pro_gp_excel_to_table",
    description="",
)
def arcgis_pro_gp_excel_to_table(
    input_excel: str,
    out_table: str,
    sheet: str = "",
) -> str:
    arcpy = _arcpy()
    gp_convert.run_excel_to_table(arcpy, input_excel, out_table, sheet)
    return _json_dumps({"ok": True, "out_table": normalize_path(out_table)})


@mcp.tool(
    name="arcgis_pro_gp_table_to_excel",
    description="",
)
def arcgis_pro_gp_table_to_excel(in_table: str, output_excel: str) -> str:
    arcpy = _arcpy()
    gp_convert.run_table_to_excel(arcpy, in_table, output_excel)
    return _json_dumps({"ok": True, "output_excel": normalize_path(output_excel)})


@mcp.tool(
    name="arcgis_pro_gp_feature_class_to_shapefile",
    description="",
)
def arcgis_pro_gp_feature_class_to_shapefile(
    input_features: list[str],
    output_folder: str,
) -> str:
    arcpy = _arcpy()
    gp_convert.run_feature_class_to_shapefile(arcpy, input_features, output_folder)
    return _json_dumps({"ok": True, "output_folder": normalize_path(output_folder)})


# ---------------------------------------------------------------------------
# Phase 4: Core GP Analysis Tools
# ---------------------------------------------------------------------------


@mcp.tool(
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
    arcpy = _arcpy()
    gp_analysis.run_multiple_ring_buffer(
        arcpy, in_features, out_feature_class, distances, buffer_unit, dissolve_option
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_feature_to_point",
    description="",
)
def arcgis_pro_gp_feature_to_point(
    in_features: str,
    out_feature_class: str,
    point_location: str = "CENTROID",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_feature_to_point(arcpy, in_features, out_feature_class, point_location)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_feature_to_line",
    description="",
)
def arcgis_pro_gp_feature_to_line(in_features: str, out_feature_class: str) -> str:
    arcpy = _arcpy()
    gp_analysis.run_feature_to_line(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_points_to_line",
    description="",
)
def arcgis_pro_gp_points_to_line(
    in_features: str,
    out_feature_class: str,
    line_field: str = "",
    sort_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_points_to_line(
        arcpy, in_features, out_feature_class, line_field, sort_field
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_polygon_to_line",
    description="",
)
def arcgis_pro_gp_polygon_to_line(
    in_features: str,
    out_feature_class: str,
    neighbor_option: str = "IDENTIFY_NEIGHBORS",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_polygon_to_line(arcpy, in_features, out_feature_class, neighbor_option)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_minimum_bounding_geometry",
    description="",
)
def arcgis_pro_gp_minimum_bounding_geometry(
    in_features: str,
    out_feature_class: str,
    geometry_type: str = "ENVELOPE",
    group_option: str = "NONE",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_minimum_bounding_geometry(
        arcpy, in_features, out_feature_class, geometry_type, group_option
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_convex_hull",
    description="",
)
def arcgis_pro_gp_convex_hull(
    in_features: str,
    out_feature_class: str,
    group_option: str = "ALL",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_convex_hull(arcpy, in_features, out_feature_class, group_option)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_split_by_attributes",
    description="",
)
def arcgis_pro_gp_split_by_attributes(
    in_table: str,
    target_workspace: str,
    split_fields: list[str],
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_split_by_attributes(arcpy, in_table, target_workspace, split_fields)
    return _json_dumps(
        {"ok": True, "target_workspace": normalize_path(target_workspace)},
    )


@mcp.tool(
    name="arcgis_pro_gp_identity",
    description="",
)
def arcgis_pro_gp_identity(
    in_features: str,
    identity_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_identity(arcpy, in_features, identity_features, out_feature_class)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_symmetrical_difference",
    description="",
)
def arcgis_pro_gp_symmetrical_difference(
    in_features: str,
    update_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_symmetrical_difference(
        arcpy, in_features, update_features, out_feature_class
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_count_overlapping_features",
    description="",
)
def arcgis_pro_gp_count_overlapping_features(
    in_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_count_overlapping_features(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_repair_geometry",
    description="",
)
def arcgis_pro_gp_repair_geometry(in_features: str) -> str:
    arcpy = _arcpy()
    gp_analysis.run_repair_geometry(arcpy, in_features)
    return _json_dumps({"ok": True, "in_features": normalize_path(in_features)})


@mcp.tool(
    name="arcgis_pro_gp_check_geometry",
    description="",
)
def arcgis_pro_gp_check_geometry(in_features: str, out_table: str) -> str:
    arcpy = _arcpy()
    gp_analysis.run_check_geometry(arcpy, in_features, out_table)
    return _json_dumps({"ok": True, "out_table": normalize_path(out_table)})


@mcp.tool(
    name="arcgis_pro_gp_eliminate",
    description="",
)
def arcgis_pro_gp_eliminate(
    in_features: str,
    out_feature_class: str,
    selection_type: str = "LENGTH",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_eliminate(arcpy, in_features, out_feature_class, selection_type)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_multipart_to_singlepart",
    description="",
)
def arcgis_pro_gp_multipart_to_singlepart(
    in_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_multipart_to_singlepart(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_aggregate_polygons",
    description="",
)
def arcgis_pro_gp_aggregate_polygons(
    in_features: str,
    out_feature_class: str,
    aggregation_distance: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_aggregate_polygons(
        arcpy, in_features, out_feature_class, aggregation_distance
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


# ---------------------------------------------------------------------------
# Phase 5: Raster Analysis
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_slope",
    description="",
)
def arcgis_pro_gp_slope(
    in_raster: str,
    out_raster: str,
    output_measurement: str = "DEGREE",
    z_factor: float = 1.0,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_slope(arcpy, in_raster, out_raster, output_measurement, z_factor)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_aspect",
    description="",
)
def arcgis_pro_gp_aspect(in_raster: str, out_raster: str) -> str:
    arcpy = _arcpy()
    gp_raster.run_aspect(arcpy, in_raster, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
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
    arcpy = _arcpy()
    gp_raster.run_hillshade(arcpy, in_raster, out_raster, azimuth, altitude, z_factor)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_reclassify",
    description="",
)
def arcgis_pro_gp_reclassify(
    in_raster: str,
    reclass_field: str,
    remap: str,
    out_raster: str,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_reclassify(arcpy, in_raster, reclass_field, remap, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_extract_by_mask",
    description="",
)
def arcgis_pro_gp_extract_by_mask(
    in_raster: str,
    in_mask_data: str,
    out_raster: str,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_extract_by_mask(arcpy, in_raster, in_mask_data, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_extract_by_attributes",
    description="",
)
def arcgis_pro_gp_extract_by_attributes(
    in_raster: str,
    where_clause: str,
    out_raster: str,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_extract_by_attributes(arcpy, in_raster, where_clause, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_zonal_statistics_as_table",
    description="",
)
def arcgis_pro_gp_zonal_statistics_as_table(
    in_zone_data: str,
    zone_field: str,
    in_value_raster: str,
    out_table: str,
    statistics_type: str = "ALL",
) -> str:
    arcpy = _arcpy()
    gp_raster.run_zonal_statistics_as_table(
        arcpy, in_zone_data, zone_field, in_value_raster, out_table, statistics_type
    )
    return _json_dumps({"ok": True, "out_table": normalize_path(out_table)})


@mcp.tool(
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
    arcpy = _arcpy()
    gp_raster.run_kernel_density(
        arcpy, in_features, population_field, out_raster, cell_size, search_radius
    )
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_point_density",
    description="",
)
def arcgis_pro_gp_point_density(
    in_features: str,
    population_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_point_density(arcpy, in_features, population_field, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
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
    arcpy = _arcpy()
    gp_raster.run_idw(arcpy, in_point_features, z_field, out_raster, cell_size, power)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_kriging",
    description="",
)
def arcgis_pro_gp_kriging(
    in_point_features: str,
    z_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_kriging(arcpy, in_point_features, z_field, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_topo_to_raster",
    description="",
)
def arcgis_pro_gp_topo_to_raster(
    in_topo_features: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_topo_to_raster(arcpy, in_topo_features, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_raster_to_polygon",
    description="",
)
def arcgis_pro_gp_raster_to_polygon(
    in_raster: str,
    out_polygon_features: str,
    simplify: bool = True,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_raster_to_polygon(arcpy, in_raster, out_polygon_features, simplify)
    return _json_dumps(
        {"ok": True, "out_polygon_features": normalize_path(out_polygon_features)},
    )


@mcp.tool(
    name="arcgis_pro_gp_polygon_to_raster",
    description="",
)
def arcgis_pro_gp_polygon_to_raster(
    in_features: str,
    value_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_polygon_to_raster(arcpy, in_features, value_field, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_feature_to_raster",
    description="",
)
def arcgis_pro_gp_feature_to_raster(
    in_features: str,
    field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_feature_to_raster(arcpy, in_features, field, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_raster_calculator",
    description="",
)
def arcgis_pro_gp_raster_calculator(expression: str, out_raster: str) -> str:
    arcpy = _arcpy()
    gp_raster.run_raster_calculator(arcpy, expression, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
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
    arcpy = _arcpy()
    gp_raster.run_mosaic_to_new_raster(
        arcpy, input_rasters, output_location, raster_dataset_name, number_of_bands, pixel_type
    )
    return _json_dumps(
        {"ok": True, "output": f"{normalize_path(output_location)}\\{raster_dataset_name}"},
    )


@mcp.tool(
    name="arcgis_pro_gp_clip_raster",
    description="",
)
def arcgis_pro_gp_clip_raster(
    in_raster: str,
    out_raster: str,
    rectangle: str = "",
    in_template_dataset: str = "",
    clipping_geometry: bool = False,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_clip_raster(
        arcpy, in_raster, out_raster, rectangle, in_template_dataset, clipping_geometry
    )
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_resample",
    description="",
)
def arcgis_pro_gp_resample(
    in_raster: str,
    out_raster: str,
    cell_size: str,
    resampling_type: str = "NEAREST",
) -> str:
    arcpy = _arcpy()
    gp_raster.run_resample(arcpy, in_raster, out_raster, cell_size, resampling_type)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_project_raster",
    description="",
)
def arcgis_pro_gp_project_raster(
    in_raster: str,
    out_raster: str,
    out_wkid: int,
    resampling_type: str = "NEAREST",
) -> str:
    arcpy = _arcpy()
    gp_raster.run_project_raster(arcpy, in_raster, out_raster, out_wkid, resampling_type)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_nibble",
    description="",
)
def arcgis_pro_gp_nibble(
    in_raster: str,
    in_mask_raster: str,
    out_raster: str,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_nibble(arcpy, in_raster, in_mask_raster, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


# ---------------------------------------------------------------------------
# Phase 6: Symbology Control & Layout Enhancement
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_set_unique_value_renderer",
    description="",
)
def arcgis_pro_set_unique_value_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    fields: list[str],
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_unique_value_renderer(arcpy, project, m, lyr, fields)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "UniqueValueRenderer"},
    )


@mcp.tool(
    name="arcgis_pro_set_graduated_colors_renderer",
    description="",
)
def arcgis_pro_set_graduated_colors_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    classification_field: str,
    num_classes: int = 5,
    classification_method: str = "NaturalBreaks",
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_graduated_colors_renderer(
        arcpy, project, m, lyr, classification_field, num_classes, classification_method
    )
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "GraduatedColorsRenderer"},
    )


@mcp.tool(
    name="arcgis_pro_set_graduated_symbols_renderer",
    description="",
)
def arcgis_pro_set_graduated_symbols_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    classification_field: str,
    num_classes: int = 5,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_graduated_symbols_renderer(
        arcpy, project, m, lyr, classification_field, num_classes
    )
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "GraduatedSymbolsRenderer"},
    )


@mcp.tool(
    name="arcgis_pro_set_simple_renderer",
    description="",
)
def arcgis_pro_set_simple_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_simple_renderer(arcpy, project, m, lyr)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "SimpleRenderer"},
    )


@mcp.tool(
    name="arcgis_pro_set_heatmap_renderer",
    description="",
)
def arcgis_pro_set_heatmap_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_heatmap_renderer(arcpy, project, m, lyr)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "HeatMapRenderer"},
    )


@mcp.tool(
    name="arcgis_pro_update_label_expression",
    description="",
)
def arcgis_pro_update_label_expression(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    expression: str,
    label_class_name: str = "",
    expression_engine: str = "Arcade",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.update_label_expression(arcpy, lyr, expression, label_class_name, expression_engine)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name},
    )


@mcp.tool(
    name="arcgis_pro_set_label_font",
    description="",
)
def arcgis_pro_set_label_font(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    font_name: str = "",
    font_size: float | None = None,
    font_color: str = "",
    bold: bool | None = None,
    italic: bool | None = None,
    label_class_name: str = "",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_label_font(arcpy, lyr, font_name, font_size, font_color, bold, italic, label_class_name)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name},
    )


@mcp.tool(
    name="arcgis_pro_export_report_pdf",
    description="",
)
def arcgis_pro_export_report_pdf(
    aprx_path: str,
    report_name: str,
    output_pdf_path: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    symbology.export_report_pdf(arcpy, project, report_name, output_pdf_path)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "report_name": report_name,
         "output_pdf_path": normalize_path(output_pdf_path)},
    )


@mcp.tool(
    name="arcgis_pro_list_layout_map_frames",
    description="",
)
def arcgis_pro_list_layout_map_frames(
    aprx_path: str,
    layout_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    frames: list[dict[str, Any]] = []
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        entry: dict[str, Any] = {"name": elm.name}
        try:
            entry["map_name"] = elm.map.name
        except Exception:  # noqa: BLE001
            pass
        frames.append(entry)
    return _json_dumps(
        {"aprx_path": path, "layout_name": layout_name, "map_frames": frames},
    )


@mcp.tool(
    name="arcgis_pro_set_layout_element_position",
    description="",
)
def arcgis_pro_set_layout_element_position(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    en = element_name.strip()
    elm = None
    for e in layout.listElements():
        if getattr(e, "name", "") == en:
            elm = e
            break
    if elm is None:
        names = [getattr(e, "name", "") for e in layout.listElements()]
        raise RuntimeError("Invalid arguments")
    updated: dict[str, float] = {}
    if x is not None:
        elm.elementPositionX = float(x)
        updated["x"] = float(x)
    if y is not None:
        elm.elementPositionY = float(y)
        updated["y"] = float(y)
    if width is not None:
        elm.elementWidth = float(width)
        updated["width"] = float(width)
    if height is not None:
        elm.elementHeight = float(height)
        updated["height"] = float(height)
    if not updated:
        raise RuntimeError("至少提供 x/y/width/height 之一")
    return _json_dumps({"ok": True, "aprx_path": path, "element_name": en, "updated": updated})


@mcp.tool(
    name="arcgis_pro_set_layout_element_visible",
    description="",
)
def arcgis_pro_set_layout_element_visible(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    visible: bool,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    en = element_name.strip()
    elm = None
    for e in layout.listElements():
        if getattr(e, "name", "") == en:
            elm = e
            break
    if elm is None:
        names = [getattr(e, "name", "") for e in layout.listElements()]
        raise RuntimeError("Invalid arguments")
    elm.visible = bool(visible)
    return _json_dumps({"ok": True, "aprx_path": path, "element_name": en, "visible": bool(visible)})


@mcp.tool(
    name="arcgis_pro_update_legend_items",
    description="",
)
def arcgis_pro_update_legend_items(
    aprx_path: str,
    layout_name: str,
    legend_name: str,
    layer_visibility: dict[str, bool],
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    legend = None
    for elm in layout.listElements("LEGEND_ELEMENT"):
        if elm.name == legend_name:
            legend = elm
            break
    if legend is None:
        names = [e.name for e in layout.listElements("LEGEND_ELEMENT")]
        raise RuntimeError("Invalid arguments")
    items = legend.items
    updated_count = 0
    for item in items:
        ln = getattr(item, "name", "")
        if ln in layer_visibility:
            try:
                item.visible = bool(layer_visibility[ln])
                updated_count += 1
            except Exception:  # noqa: BLE001
                pass
    return _json_dumps(
        {"ok": True, "aprx_path": path, "legend_name": legend_name, "items_updated": updated_count},
    )


@mcp.tool(
    name="arcgis_pro_create_layout",
    description="",
)
def arcgis_pro_create_layout(
    aprx_path: str,
    layout_name: str,
    page_width: float = 11.0,
    page_height: float = 8.5,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    ln = layout_name.strip()
    if not ln:
        raise RuntimeError("layout_name 不能为空")
    w = max(1.0, min(float(page_width), 200.0))
    h = max(1.0, min(float(page_height), 200.0))
    lyt = project.createLayout(w, h, "INCH")
    lyt.name = ln
    return _json_dumps({"ok": True, "aprx_path": path, "layout_name": ln})


@mcp.tool(
    name="arcgis_pro_rename_layout",
    description="",
)
def arcgis_pro_rename_layout(
    aprx_path: str,
    layout_name: str,
    new_layout_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    new_name = new_layout_name.strip()
    if not new_name:
        raise RuntimeError("new_layout_name cannot be empty")
    layout.name = new_name
    return _json_dumps({"ok": True, "aprx_path": path, "layout_name": layout_name, "new_layout_name": new_name})


@mcp.tool(
    name="arcgis_pro_export_map_to_image",
    description="",
)
def arcgis_pro_export_map_to_image(
    aprx_path: str,
    map_name: str,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    resolution_dpi: int = 96,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    symbology.export_map_to_image(arcpy, m, output_path, width, height, resolution_dpi)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name,
         "output_path": normalize_path(output_path)},
    )


# ---------------------------------------------------------------------------
# Phase 7: Layer Advanced Operations
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_get_layer_extent",
    description="",
)
def arcgis_pro_get_layer_extent(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    desc = arcpy.Describe(lyr)
    ext = _extent_dict(desc.extent)
    return _json_dumps(
        {"aprx_path": path, "map_name": map_name, "layer_name": layer_name, "extent": ext},
    )


@mcp.tool(
    name="arcgis_pro_zoom_to_layer",
    description="",
)
def arcgis_pro_zoom_to_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        raise RuntimeError("Invalid arguments")
    desc = arcpy.Describe(lyr)
    ext = desc.extent
    mf.setExtent(ext)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "mapframe_name": mapframe_name},
    )


@mcp.tool(
    name="arcgis_pro_zoom_to_selection",
    description="",
)
def arcgis_pro_zoom_to_selection(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        raise RuntimeError("Invalid arguments")
    mf.zoomToAllLayers(True)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "mapframe_name": mapframe_name},
    )


@mcp.tool(
    name="arcgis_pro_layer_add_field_alias",
    description="",
)
def arcgis_pro_layer_add_field_alias(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    field_name: str,
    field_alias: str,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    ds = lyr.dataSource
    fn = field_name.strip()
    fa = field_alias.strip()
    if not fn or not fa:
        raise RuntimeError("field_name 和 field_alias 不能为空")
    arcpy.management.AlterField(ds, fn, new_field_alias=fa)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name,
         "field_name": fn, "field_alias": fa},
    )


@mcp.tool(
    name="arcgis_pro_update_layer_cim",
    description="",
)
def arcgis_pro_update_layer_cim(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    cim_path: str,
    value: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    cp = cim_path.strip()
    if not cp:
        raise RuntimeError("cim_path 不能为空")
    import json as _json
    try:
        val = _json.loads(value)
    except Exception:
        val = value
    cim_def = lyr.getDefinition("V3")
    parts = cp.split(".")
    obj = cim_def
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], val)
    lyr.setDefinition(cim_def)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "cim_path": cp},
    )


@mcp.tool(
    name="arcgis_pro_list_layer_renderers",
    description="",
)
def arcgis_pro_list_layer_renderers(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    info: dict[str, Any] = {"layer_name": layer_name}
    try:
        sym = lyr.symbology
        info["renderer_type"] = getattr(sym.renderer, "type", str(type(sym.renderer).__name__))
        try:
            info["fields"] = sym.renderer.fields
        except Exception:  # noqa: BLE001
            pass
        try:
            info["classification_field"] = sym.renderer.classificationField
        except Exception:  # noqa: BLE001
            pass
        try:
            info["break_count"] = sym.renderer.breakCount
        except Exception:  # noqa: BLE001
            pass
    except Exception as ex:  # noqa: BLE001
        info["error"] = str(ex)[:500]
    return _json_dumps(info)


# ---------------------------------------------------------------------------
# Phase 8: Database Connection Operations
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_list_broken_sources",
    description="",
)
def arcgis_pro_list_broken_sources(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    broken: list[dict[str, Any]] = []
    try:
        for lyr in project.listBrokenDataSources():
            item: dict[str, Any] = {"name": getattr(lyr, "name", str(lyr))}
            try:
                item["long_name"] = lyr.longName
            except Exception:  # noqa: BLE001
                pass
            try:
                item["data_source"] = lyr.dataSource
            except Exception as ex:  # noqa: BLE001
                item["data_source_error"] = str(ex)[:300]
            broken.append(item)
    except Exception as ex:  # noqa: BLE001
        return _json_dumps({"aprx_path": path, "error": str(ex)[:500]})
    return _json_dumps({"aprx_path": path, "broken_count": len(broken), "broken_sources": broken})


@mcp.tool(
    name="arcgis_pro_repair_layer_source",
    description="",
)
def arcgis_pro_repair_layer_source(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    new_workspace_path: str,
    new_dataset_name: str = "",
    workspace_type: str = "FILEGDB_WORKSPACE",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    nwp = validate_input_path_optional(new_workspace_path, "new_workspace_path")
    ndn = new_dataset_name.strip()
    wt = workspace_type.strip()
    if ndn:
        lyr.replaceDataSource(nwp, wt, ndn, True)
    else:
        lyr.replaceDataSource(nwp, wt, lyr.name, True)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name,
         "new_workspace_path": nwp},
    )


@mcp.tool(
    name="arcgis_pro_create_db_connection",
    description="",
)
def arcgis_pro_create_db_connection(
    out_folder_path: str,
    out_name: str,
    database_platform: str,
    instance: str,
    database: str = "",
    authentication: str = "DATABASE_AUTH",
    username: str = "",
    password: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    from arcgis_pro_mcp.paths import require_gp_output_root_mandatory, validate_gp_output_path
    require_gp_output_root_mandatory()
    ofp = validate_gp_output_path(out_folder_path, "out_folder_path")
    on = out_name.strip()
    if not on:
        raise RuntimeError("out_name 不能为空")
    if not on.lower().endswith(".sde"):
        on += ".sde"
    kwargs: dict[str, str] = {
        "database_platform": database_platform.strip(),
        "instance": instance.strip(),
    }
    if database:
        kwargs["database"] = database.strip()
    auth = authentication.strip().upper()
    kwargs["account_authentication"] = auth
    if auth == "DATABASE_AUTH":
        kwargs["username"] = username
        kwargs["password"] = password
    arcpy.management.CreateDatabaseConnection(ofp, on, **kwargs)
    return _json_dumps({"ok": True, "connection_file": f"{ofp}\\{on}"})


@mcp.tool(
    name="arcgis_pro_list_sde_datasets",
    description="",
)
def arcgis_pro_list_sde_datasets(
    sde_connection_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(sde_connection_path, "sde_connection_path")
    fcs = workspace_listing.list_feature_classes(arcpy, p, "", "", wild_card, max_items)
    tables = workspace_listing.list_tables(arcpy, p, wild_card, max_items)
    return _json_dumps(
        {"sde_connection_path": p, "feature_classes": fcs, "tables": tables},
    )


# ---------------------------------------------------------------------------
# Phase 9: Map Operations Enhancement
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_add_basemap",
    description="",
)
def arcgis_pro_add_basemap(
    aprx_path: str,
    map_name: str,
    basemap_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    bn = basemap_name.strip()
    if not bn:
        raise RuntimeError("basemap_name 不能为空")
    m.addBasemap(bn)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "basemap_name": bn},
    )


@mcp.tool(
    name="arcgis_pro_create_map",
    description="",
)
def arcgis_pro_create_map(
    aprx_path: str,
    map_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    mn = map_name.strip()
    if not mn:
        raise RuntimeError("map_name 不能为空")
    new_map = project.createMap(mn)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": getattr(new_map, "name", mn)},
    )


@mcp.tool(
    name="arcgis_pro_remove_map",
    description="",
)
def arcgis_pro_remove_map(aprx_path: str, map_name: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    project.removeItem(m)
    return _json_dumps({"ok": True, "aprx_path": path, "removed": map_name})


@mcp.tool(
    name="arcgis_pro_duplicate_map",
    description="",
)
def arcgis_pro_duplicate_map(
    aprx_path: str,
    map_name: str,
    new_map_name: str = "",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    new_map = project.copyItem(m)
    nmn = new_map_name.strip()
    if nmn:
        new_map.name = nmn
    return _json_dumps(
        {"ok": True, "aprx_path": path, "source_map": map_name,
         "new_map": getattr(new_map, "name", "")},
    )


@mcp.tool(
    name="arcgis_pro_rename_map",
    description="",
)
def arcgis_pro_rename_map(
    aprx_path: str,
    map_name: str,
    new_map_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    map_obj = _get_map(project, map_name)
    new_name = new_map_name.strip()
    if not new_name:
        raise RuntimeError("new_map_name cannot be empty")
    map_obj.name = new_name
    return _json_dumps({"ok": True, "aprx_path": path, "map_name": map_name, "new_map_name": new_name})


@mcp.tool(
    name="arcgis_pro_map_pan_to_extent",
    description="",
)
def arcgis_pro_map_pan_to_extent(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    spatial_reference_wkid: int | None = None,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        raise RuntimeError("Invalid arguments")
    ext = arcpy.Extent(float(xmin), float(ymin), float(xmax), float(ymax))
    if spatial_reference_wkid is not None:
        ext.spatialReference = arcpy.SpatialReference(int(spatial_reference_wkid))
    else:
        try:
            ext.spatialReference = mf.map.spatialReference
        except Exception:  # noqa: BLE001
            pass
    mf.panToExtent(ext)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "mapframe_name": mapframe_name},
    )


@mcp.tool(
    name="arcgis_pro_set_time_slider",
    description="",
)
def arcgis_pro_set_time_slider(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    start_time: str = "",
    end_time: str = "",
    time_field: str = "",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    updated: dict[str, str] = {}
    try:
        if time_field:
            lyr.time.isTimeEnabled = True
            lyr.time.startTimeField = time_field
            updated["time_field"] = time_field
        if start_time:
            lyr.time.startTime = start_time
            updated["start_time"] = start_time
        if end_time:
            lyr.time.endTime = end_time
            updated["end_time"] = end_time
    except Exception as ex:  # noqa: BLE001
        return _json_dumps({"ok": False, "error": str(ex)[:500]})
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "updated": updated},
    )


# ---------------------------------------------------------------------------
# Phase 10: Generic GP Engine
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_run_tool",
    description="",
)
def arcgis_pro_gp_run_tool(
    tool_name: str,
    parameters: dict[str, Any] | None = None,
) -> str:
    arcpy = _arcpy()
    msgs = gp_generic.run_tool(arcpy, tool_name, parameters)
    return _json_dumps({"ok": True, "tool_name": tool_name, "messages": msgs})


@mcp.tool(
    name="arcgis_pro_gp_get_messages",
    description="",
)
def arcgis_pro_gp_get_messages() -> str:
    arcpy = _arcpy()
    msgs = gp_generic.get_messages(arcpy)
    return _json_dumps({"messages": msgs})


@mcp.tool(
    name="arcgis_pro_gp_list_toolboxes",
    description="",
)
def arcgis_pro_gp_list_toolboxes() -> str:
    arcpy = _arcpy()
    toolboxes = gp_generic.list_toolboxes(arcpy)
    return _json_dumps({"toolboxes": toolboxes, "count": len(toolboxes)})


@mcp.tool(
    name="arcgis_pro_gp_list_tools_in_toolbox",
    description="",
)
def arcgis_pro_gp_list_tools_in_toolbox(toolbox: str) -> str:
    arcpy = _arcpy()
    tools = gp_generic.list_tools_in_toolbox(arcpy, toolbox)
    return _json_dumps({"toolbox": toolbox, "tools": tools, "count": len(tools)})


# ---------------------------------------------------------------------------
# Phase 11: Network Analysis
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_na_create_route_layer",
    description="",
)
def arcgis_pro_na_create_route_layer(
    network_data_source: str,
    layer_name: str = "Route",
    travel_mode: str = "",
) -> str:
    arcpy = _arcpy()
    result = gp_network.run_make_route_analysis_layer(
        arcpy, network_data_source, layer_name, travel_mode
    )
    return _json_dumps({"ok": True, "layer": result})


@mcp.tool(
    name="arcgis_pro_na_add_locations",
    description="",
)
def arcgis_pro_na_add_locations(
    in_network_analysis_layer: str,
    sub_layer: str,
    in_table: str,
    field_mappings: str = "",
) -> str:
    arcpy = _arcpy()
    gp_network.run_add_locations(
        arcpy, in_network_analysis_layer, sub_layer, in_table, field_mappings
    )
    return _json_dumps({"ok": True, "sub_layer": sub_layer})


@mcp.tool(
    name="arcgis_pro_na_solve",
    description="",
)
def arcgis_pro_na_solve(
    in_network_analysis_layer: str,
    ignore_invalids: bool = True,
) -> str:
    arcpy = _arcpy()
    result = gp_network.run_solve(arcpy, in_network_analysis_layer, ignore_invalids)
    return _json_dumps({"ok": True, "result": result})


@mcp.tool(
    name="arcgis_pro_na_service_area",
    description="",
)
def arcgis_pro_na_service_area(
    network_data_source: str,
    layer_name: str = "ServiceArea",
    travel_mode: str = "",
    cutoffs: list[float] | None = None,
) -> str:
    arcpy = _arcpy()
    result = gp_network.run_make_service_area_analysis_layer(
        arcpy, network_data_source, layer_name, travel_mode, cutoffs
    )
    return _json_dumps({"ok": True, "layer": result})


@mcp.tool(
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
    arcpy = _arcpy()
    result = gp_network.run_make_od_cost_matrix_layer(
        arcpy, network_data_source, layer_name, travel_mode, cutoff, number_of_destinations_to_find
    )
    return _json_dumps({"ok": True, "layer": result})


# ---------------------------------------------------------------------------
# Phase 12: Metadata & Data Quality
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_get_metadata",
    description="",
)
def arcgis_pro_get_metadata(dataset_path: str) -> str:
    arcpy = _arcpy()
    md = metadata.get_metadata(arcpy, dataset_path)
    return _json_dumps({"dataset_path": normalize_path(dataset_path), "metadata": md})


@mcp.tool(
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
    arcpy = _arcpy()
    metadata.set_metadata(arcpy, dataset_path, title, tags, summary, description, credits, access_constraints)
    return _json_dumps({"ok": True, "dataset_path": normalize_path(dataset_path)})


@mcp.tool(
    name="arcgis_pro_gp_validate_topology",
    description="",
)
def arcgis_pro_gp_validate_topology(in_topology: str) -> str:
    arcpy = _arcpy()
    metadata.validate_topology(arcpy, in_topology)
    return _json_dumps({"ok": True, "in_topology": normalize_path(in_topology)})


@mcp.tool(
    name="arcgis_pro_workspace_list_datasets",
    description="",
)
def arcgis_pro_workspace_list_datasets(
    workspace_path: str,
    dataset_type: str = "",
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = _list_workspace_datasets(arcpy, ws, dataset_type, wild_card, max_items)
    return _json_dumps(
        {
            "workspace_path": ws,
            "dataset_type": dataset_type.strip(),
            "datasets": names,
        },
    )


@mcp.tool(
    name="arcgis_pro_workspace_list_feature_datasets",
    description="",
)
def arcgis_pro_workspace_list_feature_datasets(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = _list_workspace_datasets(arcpy, ws, "Feature", wild_card, max_items)
    return _json_dumps({"workspace_path": ws, "feature_datasets": names})


@mcp.tool(
    name="arcgis_pro_workspace_list_domains",
    description="",
)
def arcgis_pro_workspace_list_domains(
    workspace_path: str,
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    domains = _list_workspace_domains(arcpy, ws, max_items)
    return _json_dumps({"workspace_path": ws, "domains": domains})


@mcp.tool(
    name="arcgis_pro_gp_table_to_table",
    description="",
)
def arcgis_pro_gp_table_to_table(
    in_rows: str,
    out_path: str,
    out_name: str,
) -> str:
    arcpy = _arcpy()
    result_path = gp_convert.run_table_to_table(arcpy, in_rows, out_path, out_name)
    return _json_dumps({"ok": True, "created": result_path})
