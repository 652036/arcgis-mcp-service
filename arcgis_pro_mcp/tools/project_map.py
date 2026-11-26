"""Project catalog, map, layer, table, bookmark, join/relate, and connection tools."""

from __future__ import annotations

import gc
import hashlib
import json
import os
from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool

SERVER_EXPORTS: tuple[str, ...] = ("_layer_position", "_project_item", "_project_data_source",)

@tool(
    name="arcgis_pro_list_projects",
    description="",
)
def arcgis_pro_list_projects(max_items: int = 100) -> str:
    roots = _srv.project_roots()
    if not roots:
        return _srv._json_dumps(
            {
                "roots": [],
                "project_count": 0,
                "projects": [],
                "note": (
                    "未配置 ARCGIS_PRO_MCP_PROJECT_ROOTS（或 INPUT_ROOTS 回退）。"
                    "无法扫描工程；请设置根目录，或直接传入绝对 aprx_path。"
                    "若已在 Pro 运行接入当前窗口.py，可对工程工具使用 aprx_path=CURRENT。"
                ),
            },
        )
    cap = max(1, min(int(max_items), 1000))
    projects: list[str] = []
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if not filename.lower().endswith(".aprx"):
                    continue
                projects.append(_srv.normalize_path(os.path.join(dirpath, filename)))
                if len(projects) >= cap:
                    return _srv._json_dumps({"roots": roots, "project_count": len(projects), "projects": projects})
    return _srv._json_dumps({"roots": roots, "project_count": len(projects), "projects": projects})


@tool(
    name="arcgis_pro_list_maps",
    description="",
)
def arcgis_pro_list_maps(aprx_path: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    maps = list(project.listMaps())
    names = [m.name for m in maps]
    items = [{"name": m.name, "uri": _srv._object_uri(m)} for m in maps]
    return json.dumps(
        {"aprx_path": path, "maps": names, "map_items": items},
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="arcgis_pro_project_connections",
    description="",
)
def arcgis_pro_project_connections(aprx_path: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    out: dict[str, Any] = {
        "aprx_path": path,
        "folder_connections": _srv._redact_sensitive(
            list(getattr(project, "folderConnections", None) or [])
        ),
        "databases": _srv._redact_sensitive(
            list(getattr(project, "databases", None) or [])
        ),
        "toolboxes": _srv._redact_sensitive(
            list(getattr(project, "toolboxes", None) or [])
        ),
        "styles": list(getattr(project, "styles", None) or []),
        "default_geodatabase": getattr(project, "defaultGeodatabase", None),
        "default_toolbox": getattr(project, "defaultToolbox", None),
        "home_folder": getattr(project, "homeFolder", None),
    }
    return _srv._json_dumps(out)


@tool(
    name="arcgis_pro_add_folder_connection",
    description="向工程目录追加一个受输入根约束的文件夹连接，可设为 home folder。",
)
def arcgis_pro_add_folder_connection(
    aprx_path: str,
    folder_path: str,
    alias: str = "",
    make_home: bool = False,
    validate: bool = True,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.add_folder_connection(
        project,
        folder_path,
        alias,
        make_home=make_home,
        validate=validate,
    )
    result["aprx_path"] = path
    return _srv._json_dumps(_srv._redact_sensitive(result))


@tool(
    name="arcgis_pro_remove_folder_connection",
    description="移除精确文件夹连接；拒绝当前 home folder，并要求破坏性开关和路径确认。",
)
def arcgis_pro_remove_folder_connection(
    aprx_path: str,
    folder_path: str,
    confirm_folder_path: str,
) -> str:
    _srv.require_allow_destructive()
    if confirm_folder_path != folder_path:
        raise RuntimeError("confirm_folder_path 必须与 folder_path 完全一致")
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.remove_folder_connection(project, folder_path)
    result["aprx_path"] = path
    return _srv._json_dumps(_srv._redact_sensitive(result))


@tool(
    name="arcgis_pro_add_database_connection",
    description="向工程目录追加一个受输入根约束的数据库连接，可设为默认地理数据库。",
)
def arcgis_pro_add_database_connection(
    aprx_path: str,
    database_path: str,
    make_default: bool = False,
    validate: bool = True,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.add_database(
        project,
        database_path,
        make_default=make_default,
        validate=validate,
    )
    result["aprx_path"] = path
    return _srv._json_dumps(_srv._redact_sensitive(result))


@tool(
    name="arcgis_pro_remove_database_connection",
    description="移除精确数据库连接；拒绝默认数据库，并要求破坏性开关和路径确认。",
)
def arcgis_pro_remove_database_connection(
    aprx_path: str,
    database_path: str,
    confirm_database_path: str,
) -> str:
    _srv.require_allow_destructive()
    if confirm_database_path != database_path:
        raise RuntimeError("confirm_database_path 必须与 database_path 完全一致")
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.remove_database(project, database_path)
    result["aprx_path"] = path
    return _srv._json_dumps(_srv._redact_sensitive(result))


@tool(
    name="arcgis_pro_add_project_toolbox",
    description="向工程目录追加一个受控 .atbx/.tbx 工具箱，可设为默认工具箱；拒绝可执行 .pyt。",
)
def arcgis_pro_add_project_toolbox(
    aprx_path: str,
    toolbox_path: str,
    make_default: bool = False,
    validate: bool = True,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.add_toolbox(
        project,
        toolbox_path,
        make_default=make_default,
        validate=validate,
    )
    result["aprx_path"] = path
    return _srv._json_dumps(_srv._redact_sensitive(result))


@tool(
    name="arcgis_pro_remove_project_toolbox",
    description="移除精确工程工具箱；拒绝默认工具箱，并要求破坏性开关和路径确认。",
)
def arcgis_pro_remove_project_toolbox(
    aprx_path: str,
    toolbox_path: str,
    confirm_toolbox_path: str,
) -> str:
    _srv.require_allow_destructive()
    if confirm_toolbox_path != toolbox_path:
        raise RuntimeError("confirm_toolbox_path 必须与 toolbox_path 完全一致")
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.remove_toolbox(project, toolbox_path)
    result["aprx_path"] = path
    return _srv._json_dumps(_srv._redact_sensitive(result))


@tool(
    name="arcgis_pro_add_project_style",
    description="向工程追加系统样式名称或受输入根约束的 .stylx 文件。",
)
def arcgis_pro_add_project_style(aprx_path: str, style: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.add_style(project, style)
    result["aprx_path"] = path
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_remove_project_style",
    description="移除一个精确工程样式；需要破坏性开关和 confirm_style 精确匹配。",
)
def arcgis_pro_remove_project_style(
    aprx_path: str,
    style: str,
    confirm_style: str,
) -> str:
    _srv.require_allow_destructive()
    if confirm_style != style:
        raise RuntimeError("confirm_style 必须与 style 完全一致")
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.remove_style(project, style)
    result["aprx_path"] = path
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_list_style_items",
    description="按样式、style class 和 wildcard 列出项目可用符号/颜色等 StyleItem。",
)
def arcgis_pro_list_style_items(
    aprx_path: str,
    style: str,
    style_class: str,
    wildcard: str = "*",
    max_items: int = 200,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_catalog.list_style_items(
        project, style, style_class, wildcard, max_items
    )
    result["aprx_path"] = path
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_list_basemaps",
    description="列出工程当前可用的 basemap 名称。",
)
def arcgis_pro_list_basemaps(aprx_path: str, wildcard: str = "*") -> str:
    _, project, path = _srv._open_project(aprx_path)
    pattern = _srv._sanitize_wild_card(wildcard)
    values = [str(value) for value in project.listBasemaps(pattern)]
    return _srv._json_dumps({"aprx_path": path, "count": len(values), "basemaps": values})


@tool(
    name="arcgis_pro_list_color_ramps",
    description="列出工程当前可用色带的名称和对象标识。",
)
def arcgis_pro_list_color_ramps(aprx_path: str, wildcard: str = "*") -> str:
    _, project, path = _srv._open_project(aprx_path)
    pattern = _srv._sanitize_wild_card(wildcard)
    rows = [
        {"name": getattr(item, "name", str(item))}
        for item in project.listColorRamps(pattern)
    ]
    return _srv._json_dumps({"aprx_path": path, "count": len(rows), "color_ramps": rows})


@tool(
    name="arcgis_pro_set_project_defaults",
    description="设置工程 home folder、默认地理数据库或默认工具箱；至少提供一项。",
)
def arcgis_pro_set_project_defaults(
    aprx_path: str,
    home_folder: str = "",
    default_geodatabase: str = "",
    default_toolbox: str = "",
) -> str:
    _srv.require_allow_write()
    if not any((home_folder, default_geodatabase, default_toolbox)):
        raise RuntimeError("至少提供 home_folder、default_geodatabase 或 default_toolbox")
    _, project, path = _srv._open_project(aprx_path)
    if home_folder:
        project.homeFolder = _srv.validate_input_path_optional(home_folder, "home_folder")
    if default_geodatabase:
        project.defaultGeodatabase = _srv.validate_input_path_optional(
            default_geodatabase, "default_geodatabase"
        )
    if default_toolbox:
        project.defaultToolbox = _srv.project_catalog.validate_safe_toolbox_path(
            default_toolbox, "default_toolbox"
        )
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "home_folder": getattr(project, "homeFolder", None),
            "default_geodatabase": getattr(project, "defaultGeodatabase", None),
            "default_toolbox": getattr(project, "defaultToolbox", None),
        }
    )


@tool(
    name="arcgis_pro_project_summary",
    description="",
)
def arcgis_pro_project_summary(
    aprx_path: str,
    max_broken_list: int = 50,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
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
                item["data_source"] = _srv._redact_sensitive(str(lyr.dataSource))
            except Exception as ex:  # noqa: BLE001
                item["data_source_error"] = _srv._safe_error(ex, 300)
            broken_items.append(item)
        broken_total = len(broken_layers)
    except Exception as ex:  # noqa: BLE001
        broken_total = -1
        broken_error = _srv._safe_error(ex, 500)

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


@tool(
    name="arcgis_pro_list_layers",
    description="",
)
def arcgis_pro_list_layers(aprx_path: str, map_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    target = _srv._get_map(project, map_name)

    layers_out: list[dict[str, Any]] = []
    for lyr in target.listLayers():
        entry: dict[str, Any] = {
            "name": lyr.name,
            "long_name": getattr(lyr, "longName", None),
            "uri": _srv._object_uri(lyr),
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
                entry["data_source"] = _srv._redact_sensitive(str(lyr.dataSource))
            except Exception as ex:  # noqa: BLE001
                entry["data_source_error"] = _srv._safe_error(ex, 500)
        layers_out.append(entry)

    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "layers": layers_out},
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="arcgis_pro_list_tables",
    description="",
)
def arcgis_pro_list_tables(aprx_path: str, map_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    target = _srv._get_map(project, map_name)
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
            row["data_source"] = _srv._redact_sensitive(str(tbl.dataSource))
        except Exception as ex:  # noqa: BLE001
            row["data_source_error"] = _srv._safe_error(ex, 500)
        tables_out.append(row)

    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "tables": tables_out},
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="arcgis_pro_map_spatial_reference",
    description="",
)
def arcgis_pro_map_spatial_reference(aprx_path: str, map_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    target = _srv._get_map(project, map_name)
    sr = target.spatialReference
    return json.dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "spatial_reference": _srv._spatial_ref_dict(sr),
        },
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="arcgis_pro_map_camera",
    description="",
)
def arcgis_pro_map_camera(aprx_path: str, map_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    target = _srv._get_map(project, map_name)
    cam = getattr(target, "defaultCamera", None)
    if cam is None:
        return json.dumps(
            {"aprx_path": path, "map_name": map_name, "camera": None},
            ensure_ascii=False,
            indent=2,
        )
    out = _srv._camera_dict(cam)
    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "camera": out},
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="arcgis_pro_list_bookmarks",
    description="",
)
def arcgis_pro_list_bookmarks(aprx_path: str, map_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    target = _srv._get_map(project, map_name)
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


@tool(
    name="arcgis_pro_layer_properties",
    description="",
)
def arcgis_pro_layer_properties(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    target = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(target, layer_name)

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
        sym = lyr.symbology
        renderer = getattr(sym, "renderer", None)
        colorizer = getattr(sym, "colorizer", None)
        if renderer is not None:
            props["symbology_kind"] = "renderer"
            props["symbology_type"] = str(
                getattr(renderer, "type", None) or type(renderer).__name__
            )
        elif colorizer is not None:
            props["symbology_kind"] = "colorizer"
            props["symbology_type"] = str(
                getattr(colorizer, "type", None) or type(colorizer).__name__
            )
        else:
            props["symbology_supported"] = False
    except Exception as ex:  # noqa: BLE001
        props["symbology_error"] = _srv._safe_error(ex, 300)
    try:
        props["data_source"] = _srv._redact_sensitive(str(lyr.dataSource))
    except Exception as ex:  # noqa: BLE001
        props["data_source_error"] = _srv._safe_error(ex, 500)
    try:
        props["is_snappable"] = lyr.isSnappable
    except Exception:  # noqa: BLE001
        pass
    try:
        props["is_selectable"] = lyr.isSelectable
    except Exception:  # noqa: BLE001
        pass

    return json.dumps(props, ensure_ascii=False, indent=2)


@tool(
    name="arcgis_pro_save_project",
    description="",
)
def arcgis_pro_save_project(aprx_path: str) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    if bool(getattr(project, "isReadOnly", False)):
        raise RuntimeError("当前工程引用为只读，不能直接保存；请使用 arcgis_pro_save_project_copy")
    project.save()
    return _srv._json_dumps({"ok": True, "aprx_path": path, "saved": True})


@tool(
    name="arcgis_pro_save_project_copy",
    description="",
)
def arcgis_pro_save_project_copy(aprx_path: str, output_aprx_path: str) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    out_path = _srv.validate_new_output_in_export_root(output_aprx_path, "output_aprx_path")
    if not out_path.lower().endswith(".aprx"):
        raise RuntimeError("output_aprx_path 应以 .aprx 结尾")
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    project.saveACopy(out_path)
    return _srv._json_dumps(
        {"ok": True, "source_aprx": path, "output_aprx": out_path},
    )


@tool(
    name="arcgis_pro_set_layer_visible",
    description="",
)
def arcgis_pro_set_layer_visible(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    visible: bool,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    lyr.visible = bool(visible)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "visible": lyr.visible,
        },
    )


@tool(
    name="arcgis_pro_set_layer_transparency",
    description="",
)
def arcgis_pro_set_layer_transparency(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    transparency_percent: int,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    t = max(0, min(int(transparency_percent), 100))
    lyr.transparency = t
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "transparency": t,
        },
    )


@tool(
    name="arcgis_pro_set_definition_query",
    description="",
)
def arcgis_pro_set_definition_query(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    definition_query: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    sql = definition_query.strip()
    if len(sql) > 8000:
        raise RuntimeError("definition_query 过长（>8000）")
    lyr.definitionQuery = sql
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "definition_query": sql,
        },
    )


@tool(
    name="arcgis_pro_select_layer_by_attribute",
    description="按属性更新图层选择集，并核验 ArcPy 派生计数与实际选择集一致。",
)
def arcgis_pro_select_layer_by_attribute(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    selection_type: str,
    where_clause: str = "",
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    st = selection_type.strip().upper()
    if st not in _srv._SELECTION_TYPES:
        raise RuntimeError(f"selection_type 须为 {sorted(_srv._SELECTION_TYPES)}")
    wc = where_clause.strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause 过长")
    result = arcpy.management.SelectLayerByAttribute(lyr, st, wc or "")
    selected_count, result_count = _srv._verify_selection_result(
        lyr,
        result,
        count_output_index=1,
    )
    refreshed = _srv._refresh_layer_after_window_change(arcpy, aprx_path, lyr)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "selection_type": st,
            "selected_count": selected_count,
            "result_count": result_count,
            "selection_verified": True,
            "ui_refresh_requested": refreshed,
        },
    )


@tool(
    name="arcgis_pro_make_feature_layer",
    description="",
)
def arcgis_pro_make_feature_layer(
    dataset_path: str,
    out_layer_name: str,
    where_clause: str = "",
) -> str:
    _srv.require_allow_write()
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    name = _srv._validate_view_name(out_layer_name, "out_layer_name")
    wc = (where_clause or "").strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause too long")
    result = arcpy.management.MakeFeatureLayer(p, name, wc or None)
    created_value: Any = name
    try:
        created_value = result.getOutput(0)
    except Exception:  # noqa: BLE001
        pass
    created_name = str(created_value)
    count = _srv.gp_allowlist.gp_get_count_layer(arcpy, created_value)
    layer_ref = _srv.session_refs.register(
        created_value,
        kind="feature_layer",
        name=created_name,
        source=p,
    )
    return _srv._json_dumps(
        {
            "ok": True,
            "dataset_path": p,
            "layer_name": created_name,
            "layer_ref": layer_ref,
            "count": count,
        }
    )


@tool(
    name="arcgis_pro_make_table_view",
    description="",
)
def arcgis_pro_make_table_view(
    dataset_path: str,
    out_view_name: str,
    where_clause: str = "",
) -> str:
    _srv.require_allow_write()
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(dataset_path, "dataset_path")
    name = _srv._validate_view_name(out_view_name, "out_view_name")
    wc = (where_clause or "").strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause too long")
    result = arcpy.management.MakeTableView(p, name, wc or None)
    created_value: Any = name
    try:
        created_value = result.getOutput(0)
    except Exception:  # noqa: BLE001
        pass
    created_name = str(created_value)
    count = _srv.gp_allowlist.gp_get_count(arcpy, created_value)
    view_ref = _srv.session_refs.register(
        created_value,
        kind="table_view",
        name=created_name,
        source=p,
    )
    return _srv._json_dumps(
        {
            "ok": True,
            "dataset_path": p,
            "view_name": created_name,
            "view_ref": view_ref,
            "count": count,
        }
    )


@tool(
    name="arcgis_pro_mapframe_zoom_to_bookmark",
    description="",
)
def arcgis_pro_mapframe_zoom_to_bookmark(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    bookmark_name: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _srv._raise_not_found("mapframe", mapframe_name, names)
    bkmk = None
    try:
        for b in mf.map.listBookmarks():
            if b.name == bookmark_name:
                bkmk = b
                break
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"读取书签失败：{_srv._safe_error(ex, 500)}") from ex
    if bkmk is None:
        names = [b.name for b in mf.map.listBookmarks()]
        _srv._raise_not_found("bookmark", bookmark_name, names)
    mf.zoomToBookmark(bkmk)  # type: ignore[attr-defined]
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "mapframe_name": mapframe_name,
            "bookmark_name": bookmark_name,
        },
    )


@tool(
    name="arcgis_pro_add_layer_from_path",
    description="",
)
def arcgis_pro_add_layer_from_path(
    aprx_path: str,
    map_name: str,
    data_path: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    dp = _srv.validate_input_path_optional(data_path, "data_path")
    m.addDataFromPath(dp)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "data_path": dp},
    )


def _layer_position(value: str) -> str:
    position = (value or "AUTO_ARRANGE").strip().upper()
    if position not in {"AUTO_ARRANGE", "TOP", "BOTTOM"}:
        raise RuntimeError("add_position 须为 AUTO_ARRANGE、TOP 或 BOTTOM")
    return position


@tool(
    name="arcgis_pro_copy_layer",
    description="把工程中现有图层复制到另一地图的顶层，并返回新图层标识。",
)
def arcgis_pro_copy_layer(
    aprx_path: str,
    source_map_name: str,
    source_layer_name: str,
    target_map_name: str,
    add_position: str = "AUTO_ARRANGE",
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    source = _srv._find_layer(_srv._get_map(project, source_map_name), source_layer_name)
    target_map = _srv._get_map(project, target_map_name)
    added = list(target_map.addLayer(source, _srv._layer_position(add_position)) or [])
    if not added:
        raise RuntimeError("Map.addLayer 未返回已添加图层")
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "source_map_name": source_map_name,
            "target_map_name": target_map_name,
            "layers": [
                {"name": getattr(item, "name", None), "uri": _srv._object_uri(item)}
                for item in added
            ],
        }
    )


@tool(
    name="arcgis_pro_add_layer_to_group",
    description="把工程中的现有图层加入目标地图的组图层，包括空组图层。",
)
def arcgis_pro_add_layer_to_group(
    aprx_path: str,
    target_map_name: str,
    group_layer_name: str,
    source_layer_name: str,
    source_map_name: str = "",
    add_position: str = "AUTO_ARRANGE",
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    target_map = _srv._get_map(project, target_map_name)
    group = _srv._find_layer(target_map, group_layer_name)
    if not bool(getattr(group, "isGroupLayer", False)):
        raise RuntimeError("group_layer_name 不是组图层")
    source_map = _srv._get_map(project, source_map_name or target_map_name)
    source = _srv._find_layer(source_map, source_layer_name)
    added = list(
        target_map.addLayerToGroup(group, source, _srv._layer_position(add_position)) or []
    )
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "target_map_name": target_map_name,
            "group_layer_name": getattr(group, "name", group_layer_name),
            "added_count": len(added),
            "layers": [
                {"name": getattr(item, "name", None), "uri": _srv._object_uri(item)}
                for item in added
            ],
        }
    )


@tool(
    name="arcgis_pro_insert_layer",
    description="在同一地图内以参考图层为锚点精确插入另一个图层（BEFORE/AFTER）。",
)
def arcgis_pro_insert_layer(
    aprx_path: str,
    map_name: str,
    reference_layer_name: str,
    insert_layer_name: str,
    insert_position: str = "BEFORE",
) -> str:
    _srv.require_allow_write()
    position = (insert_position or "BEFORE").strip().upper()
    if position not in {"BEFORE", "AFTER"}:
        raise RuntimeError("insert_position 须为 BEFORE 或 AFTER")
    _, project, path = _srv._open_project(aprx_path)
    map_obj = _srv._get_map(project, map_name)
    reference = _srv._find_layer(map_obj, reference_layer_name)
    source = _srv._find_layer(map_obj, insert_layer_name)
    inserted = map_obj.insertLayer(reference, source, position)
    if inserted is None:
        added = []
    elif isinstance(inserted, (list, tuple)):
        added = list(inserted)
    else:
        # ArcPy 3.6 may return a single Layer rather than a list.
        added = [inserted]
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "reference_layer_name": getattr(reference, "name", reference_layer_name),
            "insert_position": position,
            "added_count": len(added),
        }
    )


@tool(
    name="arcgis_pro_add_table_to_group",
    description="把地图中的现有独立表加入目标组图层。",
)
def arcgis_pro_add_table_to_group(
    aprx_path: str,
    map_name: str,
    group_layer_name: str,
    table_name: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    map_obj = _srv._get_map(project, map_name)
    group = _srv._find_layer(map_obj, group_layer_name)
    if not bool(getattr(group, "isGroupLayer", False)):
        raise RuntimeError("group_layer_name 不是组图层")
    table = _srv._get_table(map_obj, table_name)
    result = map_obj.addTableToGroup(group, table)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "group_layer_name": getattr(group, "name", group_layer_name),
            "table_name": getattr(table, "name", table_name),
            "result": str(result) if result is not None else None,
        }
    )


@tool(
    name="arcgis_pro_remove_layer",
    description="",
)
def arcgis_pro_remove_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("移除图层需要 confirm=true；源数据不会被删除")
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    m.removeLayer(lyr)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "removed": layer_name},
    )


@tool(
    name="arcgis_pro_add_table_from_path",
    description="",
)
def arcgis_pro_add_table_from_path(
    aprx_path: str,
    map_name: str,
    table_path: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    tp = _srv.validate_input_path_optional(table_path, "table_path")
    if not hasattr(m, "addDataFromPath"):
        raise RuntimeError("当前 Map 对象不支持 addDataFromPath")
    m.addDataFromPath(tp)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "table_path": tp},
    )


@tool(
    name="arcgis_pro_remove_table",
    description="",
)
def arcgis_pro_remove_table(
    aprx_path: str,
    map_name: str,
    table_name: str,
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("移除表需要 confirm=true；源数据不会被删除")
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    if not hasattr(m, "listTables"):
        raise RuntimeError("当前 Map 对象不支持 listTables")
    target = None
    for tbl in m.listTables():  # type: ignore[attr-defined]
        if tbl.name == table_name:
            target = tbl
            break
    if target is None:
        names = [tbl.name for tbl in m.listTables()]  # type: ignore[attr-defined]
        _srv._raise_not_found("table", table_name, names)
    if hasattr(m, "removeTable"):
        m.removeTable(target)  # type: ignore[attr-defined]
    elif hasattr(m, "removeItem"):
        m.removeItem(target)  # type: ignore[attr-defined]
    else:
        raise RuntimeError("当前 Map 对象不支持移除独立表")
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "removed": table_name},
    )


@tool(
    name="arcgis_pro_create_group_layer",
    description="",
)
def arcgis_pro_create_group_layer(aprx_path: str, map_name: str, group_layer_name: str) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    gn = group_layer_name.strip()
    if not gn:
        raise RuntimeError("group_layer_name 不能为空")
    group = m.createGroupLayer(gn)  # type: ignore[attr-defined]
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "group_layer_name": getattr(group, "name", gn),
        },
    )


@tool(
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
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    ref = _srv._find_layer(m, reference_layer_name)
    mov = _srv._find_layer(m, layer_to_move_name)
    pl = placement.strip().upper()
    if pl not in _srv._PLACE_LAYER:
        raise RuntimeError(f"placement 须为 {sorted(_srv._PLACE_LAYER)}")
    m.moveLayer(ref, mov, pl)  # type: ignore[attr-defined]
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "reference_layer_name": reference_layer_name,
            "layer_to_move_name": layer_to_move_name,
            "placement": pl,
        },
    )


@tool(
    name="arcgis_pro_rename_layer",
    description="",
)
def arcgis_pro_rename_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    new_name: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    nn = new_name.strip()
    if not nn:
        raise RuntimeError("new_name 不能为空")
    lyr.name = nn
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "new_name": nn,
        },
    )


@tool(
    name="arcgis_pro_set_map_reference_scale",
    description="",
)
def arcgis_pro_set_map_reference_scale(
    aprx_path: str,
    map_name: str,
    reference_scale: float,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    rs = float(reference_scale)
    if rs < 0:
        raise RuntimeError("reference_scale 不能为负")
    m.referenceScale = rs  # type: ignore[attr-defined]
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "reference_scale": rs,
        },
    )


@tool(
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
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
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
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "updated": updated},
    )


@tool(
    name="arcgis_pro_select_layer_by_location",
    description="按空间关系更新图层选择集，并核验 ArcPy 派生计数与实际选择集一致。",
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
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    input_lyr = _srv._find_layer(m, input_layer_name)
    sel_lyr = _srv._find_layer(m, selecting_layer_name)
    ov = overlap_type.strip().upper()
    if ov not in _srv._OVERLAP_LOCATION:
        raise RuntimeError(f"overlap_type 须为 {sorted(_srv._OVERLAP_LOCATION)}")
    sd = (search_distance or "").strip()
    if ov in _srv._DISTANCE_OVERLAP and not sd:
        raise RuntimeError("当前 overlap_type 必须提供 search_distance")
    st = selection_type.strip().upper()
    if st not in _srv._SELECTION_TYPES:
        raise RuntimeError(f"selection_type 须为 {sorted(_srv._SELECTION_TYPES)}")
    inv = "INVERT" if invert_spatial_relationship else "NOT_INVERT"
    result = arcpy.management.SelectLayerByLocation(
        input_lyr,
        ov,
        sel_lyr,
        sd,
        st,
        inv,
    )
    selected_count, result_count = _srv._verify_selection_result(
        input_lyr,
        result,
        count_output_index=2,
    )
    refreshed = _srv._refresh_layer_after_window_change(arcpy, aprx_path, input_lyr)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "input_layer_name": input_layer_name,
            "selecting_layer_name": selecting_layer_name,
            "overlap_type": ov,
            "selection_type": st,
            "selected_count": selected_count,
            "result_count": result_count,
            "selection_verified": True,
            "ui_refresh_requested": refreshed,
        },
    )


@tool(
    name="arcgis_pro_clear_map_selection",
    description="清除指定图层或地图全部要素图层的选择，并确认实际选择集为空。",
)
def arcgis_pro_clear_map_selection(
    aprx_path: str,
    map_name: str,
    scope: str = "all_layers",
    layer_name: str = "",
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    sc = scope.strip().lower()
    if sc == "map":
        sc = "all_layers"
    cleared = 0
    if sc == "layer":
        ln = layer_name.strip()
        if not ln:
            raise RuntimeError("scope=layer 时必须提供 layer_name")
        lyr = _srv._find_layer(m, ln)
        arcpy.management.SelectLayerByAttribute(lyr, "CLEAR_SELECTION", "")
        if _srv._layer_selection_set(lyr):
            raise RuntimeError("ArcPy 清除选择后图层仍有选中要素，拒绝报告成功")
        _srv._refresh_layer_after_window_change(arcpy, aprx_path, lyr)
        cleared = 1
    elif sc == "all_layers":
        for lyr in m.listLayers():
            if getattr(lyr, "isGroupLayer", False):
                continue
            if getattr(lyr, "isFeatureLayer", False):
                try:
                    arcpy.management.SelectLayerByAttribute(lyr, "CLEAR_SELECTION", "")
                    if _srv._layer_selection_set(lyr):
                        raise RuntimeError("清除后仍有选中要素")
                    _srv._refresh_layer_after_window_change(arcpy, aprx_path, lyr)
                except Exception as exc:  # noqa: BLE001
                    layer_label = str(getattr(lyr, "longName", None) or getattr(lyr, "name", ""))
                    raise RuntimeError(
                        f"清除图层 {layer_label!r} 的选择失败；"
                        f"此前已清除 {cleared} 个图层：{_srv._safe_error(exc, 300)}"
                    ) from exc
                cleared += 1
    else:
        raise RuntimeError('scope 须为 "layer" 或 "all_layers"')
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layers_cleared": cleared,
            "selection_verified": True,
        },
    )


@tool(
    name="arcgis_pro_layer_selection_count",
    description="读取图层 getSelectionSet 返回的准确选中要素数量，不回退为总行数。",
)
def arcgis_pro_layer_selection_count(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    cnt = len(_srv._layer_selection_set(lyr))
    return _srv._json_dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "selected_count": cnt,
            "selected_or_total_count": cnt,
            "selection_verified": True,
        },
    )


@tool(
    name="arcgis_pro_layer_selection_fids",
    description="读取图层 getSelectionSet 返回的准确选中要素 ID，可限制返回数量。",
)
def arcgis_pro_layer_selection_fids(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    max_fids: int = 2000,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    cap = max(1, min(int(max_fids), 50_000))
    selected = _srv._layer_selection_set(lyr)
    try:
        ordered = sorted(selected)
    except TypeError:
        ordered = sorted(selected, key=lambda value: (type(value).__name__, str(value)))
    fids = ordered[:cap]
    truncated = len(ordered) > cap
    digest = hashlib.sha256(
        json.dumps(ordered, ensure_ascii=False, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return _srv._json_dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "fids": fids,
            "selected_count": len(ordered),
            "selection_digest": digest,
            "selection_verified": True,
            "truncated": truncated,
        },
    )


@tool(
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
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    jt = join_type.strip().upper()
    if jt not in _srv._JOIN_TYPES:
        raise RuntimeError(f"join_type 须为 {sorted(_srv._JOIN_TYPES)}")
    jpath = _srv.validate_input_path_optional(join_table_path, "join_table_path")
    arcpy.management.AddJoin(lyr, layer_field.strip(), jpath, join_field.strip(), jt)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "join_table_path": jpath,
        },
    )


@tool(
    name="arcgis_pro_remove_join",
    description="",
)
def arcgis_pro_remove_join(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    join_name: str,
    confirm_join_name: str,
) -> str:
    _srv.require_allow_destructive()
    if not join_name.strip():
        raise RuntimeError("join_name 必须精确指定一个 join；MCP 不提供一次移除全部 join")
    if confirm_join_name != join_name:
        raise RuntimeError("confirm_join_name 必须逐字符精确回显 join_name")
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    jn = join_name.strip()
    arcpy.management.RemoveJoin(lyr, jn)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "join_name": jn,
        },
    )


@tool(
    name="arcgis_pro_set_map_spatial_reference",
    description="",
)
def arcgis_pro_set_map_spatial_reference(
    aprx_path: str,
    map_name: str,
    wkid: int,
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    m.spatialReference = arcpy.SpatialReference(int(wkid))  # type: ignore[attr-defined]
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "wkid": int(wkid)},
    )


@tool(
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
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    ws = _srv.validate_input_path_optional(workspace_path, "workspace_path")
    dt = dataset_type.strip()
    if not dt:
        raise RuntimeError("dataset_type 不能为空")
    dn = dataset_name.strip()
    if not dn:
        raise RuntimeError("dataset_name 不能为空")
    _srv._replace_layer_data_source(lyr, ws, dn, dt, bool(validate))
    return _srv._json_dumps(
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


@tool(
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
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    updated: dict[str, float] = {}
    if minimum_scale is not None:
        lyr.minimumScale = float(minimum_scale)  # type: ignore[attr-defined]
        updated["minimum_scale"] = float(minimum_scale)
    if maximum_scale is not None:
        lyr.maximumScale = float(maximum_scale)  # type: ignore[attr-defined]
        updated["maximum_scale"] = float(maximum_scale)
    if not updated:
        raise RuntimeError("至少提供 minimum_scale 或 maximum_scale")
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "updated": updated,
        },
    )


@tool(
    name="arcgis_pro_get_layer_extent",
    description="",
)
def arcgis_pro_get_layer_extent(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    desc = arcpy.Describe(lyr)
    ext = _srv._extent_dict(desc.extent)
    return _srv._json_dumps(
        {"aprx_path": path, "map_name": map_name, "layer_name": layer_name, "extent": ext},
    )


@tool(
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
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    layout = _srv._get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _srv._raise_not_found("mapframe", mapframe_name, names)
    desc = arcpy.Describe(lyr)
    ext = desc.extent
    _srv._set_mapframe_extent(mf, ext)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "mapframe_name": mapframe_name},
    )


@tool(
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
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    layout = _srv._get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _srv._raise_not_found("map frame", mapframe_name, names)
    sel = None
    try:
        sel = lyr.getSelectionSet()
    except Exception:  # noqa: BLE001
        sel = None
    if not sel:
        raise RuntimeError(f"图层 {layer_name!r} 当前选择集为空")
    xmin = ymin = None
    xmax = ymax = None
    with arcpy.da.SearchCursor(lyr, ["SHAPE@"]) as cur:  # type: ignore[attr-defined]
        for (geom,) in cur:
            if geom is None:
                continue
            e = geom.extent
            xmin = e.XMin if xmin is None else min(xmin, e.XMin)
            ymin = e.YMin if ymin is None else min(ymin, e.YMin)
            xmax = e.XMax if xmax is None else max(xmax, e.XMax)
            ymax = e.YMax if ymax is None else max(ymax, e.YMax)
    if xmin is None:
        raise RuntimeError(f"图层 {layer_name!r} 选择集没有可用几何")
    _srv._set_mapframe_extent(mf, arcpy.Extent(xmin, ymin, xmax, ymax))
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "mapframe_name": mapframe_name},
    )


@tool(
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
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    ds = lyr.dataSource
    resolved_layer_name = str(getattr(lyr, "name", layer_name))
    fn = field_name.strip()
    fa = field_alias.strip()
    if not fn or not fa:
        raise RuntimeError("field_name 和 field_alias 不能为空")
    if not _srv.is_current_project_token(aprx_path):
        # ArcGISProject and Layer references opened in file mode can retain a
        # schema lock on the data source.  They are no longer needed once the
        # dataSource string has been resolved, so release the cached graph before
        # AlterField.  CURRENT deliberately keeps the live project bound.
        _srv._cache_pop_project(_srv._project_cache_key(path))
        del lyr, m, project
        gc.collect()
        clear_workspace_cache = getattr(arcpy.management, "ClearWorkspaceCache", None)
        if callable(clear_workspace_cache):
            clear_workspace_cache()
    arcpy.management.AlterField(ds, fn, new_field_alias=fa)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": resolved_layer_name,
         "field_name": fn, "field_alias": fa},
    )


@tool(
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
    _srv.require_allow_cim_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    cp = cim_path.strip()
    if not cp:
        raise RuntimeError("cim_path 不能为空")
    parts = cp.split(".")
    if len(cp) > 500 or any(
        not part.isidentifier() or part.startswith("_") for part in parts
    ):
        raise RuntimeError(
            "cim_path must contain public dot-separated attribute names and be at most 500 characters"
        )
    if len(value.encode("utf-8")) > 100_000:
        raise RuntimeError("CIM value exceeds 100000 bytes")
    import json as _json
    try:
        val = _json.loads(value)
    except Exception:
        val = value
    cim_def = lyr.getDefinition("V3")
    obj = cim_def
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], val)
    lyr.setDefinition(cim_def)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "cim_path": cp},
    )


@tool(
    name="arcgis_pro_list_broken_sources",
    description="",
)
def arcgis_pro_list_broken_sources(aprx_path: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    broken: list[dict[str, Any]] = []
    try:
        for lyr in project.listBrokenDataSources():
            item: dict[str, Any] = {"name": getattr(lyr, "name", str(lyr))}
            try:
                item["long_name"] = lyr.longName
            except Exception:  # noqa: BLE001
                pass
            try:
                item["data_source"] = _srv._redact_sensitive(str(lyr.dataSource))
            except Exception as ex:  # noqa: BLE001
                item["data_source_error"] = _srv._safe_error(ex, 300)
            broken.append(item)
    except Exception as ex:  # noqa: BLE001
        return _srv._json_dumps({"aprx_path": path, "error": _srv._safe_error(ex, 500)})
    return _srv._json_dumps({"aprx_path": path, "broken_count": len(broken), "broken_sources": broken})


@tool(
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
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    nwp = _srv.validate_input_path_optional(new_workspace_path, "new_workspace_path")
    ndn = new_dataset_name.strip()
    wt = workspace_type.strip()
    _srv._replace_layer_data_source(lyr, nwp, ndn or getattr(lyr, "name", ""), wt, True)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name,
         "new_workspace_path": nwp},
    )


@tool(
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
    save_credentials: bool = False,
    confirm_save_credentials: bool = False,
) -> str:
    _srv.require_allow_write()
    arcpy = _srv._arcpy()
    from arcgis_pro_mcp.paths import require_gp_output_root_mandatory, validate_gp_output_path
    require_gp_output_root_mandatory()
    ofp = validate_gp_output_path(out_folder_path, "out_folder_path")
    os.makedirs(ofp, exist_ok=True)
    on = _srv.validate_output_name(out_name, "out_name")
    if not on.lower().endswith(".sde"):
        on += ".sde"
    platform = database_platform.strip().upper().replace("SAP_HANA", "SAP HANA")
    if platform not in _srv._DB_PLATFORMS:
        raise RuntimeError(f"database_platform 不受支持：{platform!r}；可选：{sorted(_srv._DB_PLATFORMS)}")
    target_instance = instance.strip()
    if not target_instance or len(target_instance) > 1024 or any(
        char in target_instance for char in "\r\n\x00|;"
    ):
        raise RuntimeError("instance 必须是非空、无控制字符/分隔符的数据库实例标识")
    allowlist = {
        item.strip()
        for item in os.environ.get(_srv._DB_INSTANCE_ALLOWLIST_ENV, "").split(";")
        if item.strip()
    }
    if not allowlist:
        raise RuntimeError(f"创建数据库连接必须配置 {_srv._DB_INSTANCE_ALLOWLIST_ENV}")
    target_key = f"{platform}|{target_instance}"
    if target_key not in allowlist:
        raise RuntimeError(f"数据库目标不在 {_srv._DB_INSTANCE_ALLOWLIST_ENV} 精确白名单中")
    connection_file = os.path.join(ofp, on)
    if os.path.lexists(connection_file):
        raise RuntimeError(f"connection_file 已存在；拒绝隐式覆盖：{connection_file}")
    kwargs: dict[str, str] = {
        "database_platform": platform,
        "instance": target_instance,
    }
    if database:
        kwargs["database"] = database.strip()
    auth = authentication.strip().upper()
    if auth not in {"DATABASE_AUTH", "OPERATING_SYSTEM_AUTH"}:
        raise RuntimeError("authentication 仅允许 DATABASE_AUTH 或 OPERATING_SYSTEM_AUTH")
    kwargs["account_authentication"] = auth
    inline_user = username.strip()
    inline_password = password
    user = inline_user or os.environ.get(_srv._DB_USERNAME_ENV, "").strip()
    pwd = inline_password or os.environ.get(_srv._DB_PASSWORD_ENV, "")
    if auth == "DATABASE_AUTH":
        if not user:
            raise RuntimeError(f"DATABASE_AUTH 需要 username 或固定环境变量 {_srv._DB_USERNAME_ENV}")
        if inline_password and not _srv.inline_db_password_allowed():
            raise RuntimeError(
                f"默认不允许通过 MCP 直接传入数据库密码。请改用固定环境变量 {_srv._DB_PASSWORD_ENV}，"
                "或在受控环境下设置 ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD=1。"
            )
        if not pwd:
            raise RuntimeError(f"DATABASE_AUTH 需要固定环境变量 {_srv._DB_PASSWORD_ENV}，或显式允许内联 password")
        kwargs["username"] = user
        kwargs["password"] = pwd
    elif inline_user or inline_password:
        raise RuntimeError("OPERATING_SYSTEM_AUTH 不接受 username/password")
    if save_credentials and not confirm_save_credentials:
        raise RuntimeError("保存数据库凭据时 confirm_save_credentials 必须为 true")
    kwargs["save_user_pass"] = "SAVE_USERNAME" if save_credentials else "DO_NOT_SAVE_USERNAME"
    arcpy.management.CreateDatabaseConnection(ofp, on, **kwargs)
    return _srv._json_dumps(
        {
            "ok": True,
            "connection_file": connection_file,
            "username_source": "inline" if inline_user else ("fixed_env" if user else ""),
            "password_source": "inline" if inline_password else ("fixed_env" if pwd else ""),
            "credentials_saved": bool(save_credentials),
        },
    )


@tool(
    name="arcgis_pro_list_sde_datasets",
    description="",
)
def arcgis_pro_list_sde_datasets(
    sde_connection_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _srv._arcpy()
    p = _srv.validate_input_path_optional(sde_connection_path, "sde_connection_path")
    fcs = _srv.workspace_listing.list_feature_classes(arcpy, p, "", "", wild_card, max_items)
    fds = _srv.workspace_listing.list_feature_datasets(arcpy, p, wild_card, max_items)
    for fd in fds:
        remaining = max_items - len(fcs)
        if remaining <= 0:
            break
        nested = _srv.workspace_listing.list_feature_classes(arcpy, p, fd, "", wild_card, remaining)
        for name in nested:
            qualified = name if ("/" in name or "\\" in name) else f"{fd}/{name}"
            if qualified not in fcs:
                fcs.append(qualified)
    tables = _srv.workspace_listing.list_tables(arcpy, p, wild_card, max_items)
    return _srv._json_dumps(
        {"sde_connection_path": p, "feature_classes": fcs[:max_items], "tables": tables},
    )


@tool(
    name="arcgis_pro_add_basemap",
    description="",
)
def arcgis_pro_add_basemap(
    aprx_path: str,
    map_name: str,
    basemap_name: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    bn = basemap_name.strip()
    if not bn:
        raise RuntimeError("basemap_name 不能为空")
    m.addBasemap(bn)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "basemap_name": bn},
    )


@tool(
    name="arcgis_pro_create_map",
    description="",
)
def arcgis_pro_create_map(
    aprx_path: str,
    map_name: str,
    map_type: str = "MAP",
    basemap_name: str = "",
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    mn = map_name.strip()
    if not mn:
        raise RuntimeError("map_name 不能为空")
    kind = (map_type or "MAP").strip().upper()
    if kind not in {"MAP", "SCENE", "GLOBE"}:
        raise RuntimeError("map_type 须为 MAP、SCENE 或 GLOBE")
    new_map = project.createMap(mn, kind)
    basemap = (basemap_name or "").strip()
    if basemap:
        new_map.addBasemap(basemap)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(new_map, "name", mn),
            "map_type": getattr(new_map, "mapType", kind),
            "basemap_name": basemap or None,
        },
    )


@tool(
    name="arcgis_pro_remove_map",
    description="",
)
def arcgis_pro_remove_map(aprx_path: str, map_name: str, confirm: bool = False) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除地图需要 confirm=true")
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    _srv._delete_project_item(project, m)
    return _srv._json_dumps({"ok": True, "aprx_path": path, "removed": map_name})


@tool(
    name="arcgis_pro_duplicate_map",
    description="",
)
def arcgis_pro_duplicate_map(
    aprx_path: str,
    map_name: str,
    new_map_name: str = "",
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    new_map = project.copyItem(m)
    nmn = new_map_name.strip()
    if nmn:
        new_map.name = nmn
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "source_map": map_name,
         "new_map": getattr(new_map, "name", "")},
    )


@tool(
    name="arcgis_pro_rename_map",
    description="",
)
def arcgis_pro_rename_map(
    aprx_path: str,
    map_name: str,
    new_map_name: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    map_obj = _srv._get_map(project, map_name)
    new_name = new_map_name.strip()
    if not new_name:
        raise RuntimeError("new_map_name cannot be empty")
    map_obj.name = new_name
    return _srv._json_dumps({"ok": True, "aprx_path": path, "map_name": map_name, "new_map_name": new_name})


@tool(
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
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _srv._raise_not_found("mapframe", mapframe_name, names)
    ext = arcpy.Extent(float(xmin), float(ymin), float(xmax), float(ymax))
    if spatial_reference_wkid is not None:
        ext.spatialReference = arcpy.SpatialReference(int(spatial_reference_wkid))
    else:
        try:
            ext.spatialReference = mf.map.spatialReference
        except Exception:  # noqa: BLE001
            pass
    mf.panToExtent(ext)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "mapframe_name": mapframe_name},
    )


@tool(
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
    """Compatibility alias for layer time enablement.

    ArcPy cannot control the active MapView time slider.  Use
    ``arcgis_pro_set_mapframe_time`` for a layout map frame or the SDK bridge for
    the live MapView.
    """
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    if start_time or end_time:
        raise RuntimeError(
            "该兼容工具不再把字符串写入只读 LayerTime 范围。"
            "请使用 arcgis_pro_set_mapframe_time；活动 MapView 时间滑块需 SDK Add-in。"
        )
    if not hasattr(lyr, "enableTime"):
        raise RuntimeError("当前图层不支持 enableTime")
    kwargs: dict[str, Any] = {"autoCalculateTimeRange": True}
    if time_field:
        kwargs["startTimeField"] = time_field
    lyr.enableTime(**kwargs)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layer_name": layer_name,
            "time_enabled": bool(getattr(lyr, "isTimeEnabled", True)),
            "start_time_field": time_field or None,
            "deprecated_alias": True,
            "active_mapview_slider_changed": False,
        },
    )


@tool(
    name="arcgis_pro_create_bookmark",
    description="从 CURRENT 活动 MapView 或指定布局地图框创建书签，并拒绝重名。",
)
def arcgis_pro_create_bookmark(
    aprx_path: str,
    name: str,
    description: str = "",
    source_type: str = "ACTIVE_VIEW",
    layout_name: str = "",
    mapframe_name: str = "",
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    source_kind = source_type.strip().upper()
    if source_kind == "ACTIVE_VIEW":
        _srv._require_current_window(aprx_path)
        source = getattr(project, "activeView", None)
        if source is None or getattr(source, "map", None) is None:
            raise RuntimeError("当前活动视图不是 MapView")
    elif source_kind == "MAP_FRAME":
        source = _srv._get_mapframe(_srv._get_layout(project, layout_name), mapframe_name)
    else:
        raise RuntimeError("source_type 须为 ACTIVE_VIEW 或 MAP_FRAME")
    result = _srv.cartography.create_bookmark(source, name, description)
    result.update({"ok": True, "aprx_path": path, "source_type": source_kind})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_update_bookmark", description="更新地图书签的名称、描述或缩略图。")
def arcgis_pro_update_bookmark(
    aprx_path: str,
    map_name: str,
    bookmark_name: str,
    new_name: str | None = None,
    description: str | None = None,
    update_thumbnail: bool = False,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.update_bookmark(
        _srv._get_map(project, map_name),
        bookmark_name,
        new_name=new_name,
        description=description,
        update_thumbnail=update_thumbnail,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": map_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_delete_bookmark", description="删除指定地图书签；需要独立破坏性门禁和 confirm=true。")
def arcgis_pro_delete_bookmark(
    aprx_path: str,
    map_name: str,
    bookmark_name: str,
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除书签需要 confirm=true")
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.delete_bookmark(_srv._get_map(project, map_name), bookmark_name)
    result.update({"ok": True, "aprx_path": path, "map_name": map_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_import_bookmarks", description="从受控输入根内的 .bkmx 文件向地图导入书签。")
def arcgis_pro_import_bookmarks(aprx_path: str, map_name: str, input_path: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.import_bookmarks(_srv._get_map(project, map_name), input_path)
    result.update({"ok": True, "aprx_path": path, "map_name": map_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_export_bookmarks", description="将地图书签导出为受控目录中的 .bkmx 文件。")
def arcgis_pro_export_bookmarks(aprx_path: str, map_name: str, output_path: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.export_bookmarks(_srv._get_map(project, map_name), output_path)
    result.update({"ok": True, "aprx_path": path, "map_name": map_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_list_definition_queries", description="列出图层或独立表的全部命名定义查询及活动状态。")
def arcgis_pro_list_definition_queries(
    aprx_path: str,
    map_name: str,
    member_name: str,
    member_type: str = "LAYER",
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    map_obj = _srv._get_map(project, map_name)
    kind = member_type.strip().upper()
    member = _srv._find_layer(map_obj, member_name) if kind == "LAYER" else _srv._get_table(map_obj, member_name)
    if kind not in {"LAYER", "TABLE"}:
        raise RuntimeError("member_type 须为 LAYER 或 TABLE")
    values = _srv.cartography.list_definition_queries(member)
    return _srv._json_dumps({"ok": True, "aprx_path": path, "queries": values, "count": len(values)})


@tool(name="arcgis_pro_upsert_definition_query", description="创建或更新图层/表的命名定义查询，并可将其设为唯一活动查询。")
def arcgis_pro_upsert_definition_query(
    aprx_path: str,
    map_name: str,
    member_name: str,
    name: str,
    sql: str,
    member_type: str = "LAYER",
    is_active: bool = False,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    map_obj = _srv._get_map(project, map_name)
    kind = member_type.strip().upper()
    member = _srv._find_layer(map_obj, member_name) if kind == "LAYER" else _srv._get_table(map_obj, member_name)
    if kind not in {"LAYER", "TABLE"}:
        raise RuntimeError("member_type 须为 LAYER 或 TABLE")
    result = _srv.cartography.upsert_definition_query(member, name, sql, is_active=is_active)
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_delete_definition_query", description="删除图层/表的唯一命名定义查询；需要破坏性门禁和 confirm=true。")
def arcgis_pro_delete_definition_query(
    aprx_path: str,
    map_name: str,
    member_name: str,
    name: str,
    member_type: str = "LAYER",
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除定义查询需要 confirm=true")
    _, project, path = _srv._open_project(aprx_path)
    map_obj = _srv._get_map(project, map_name)
    kind = member_type.strip().upper()
    member = _srv._find_layer(map_obj, member_name) if kind == "LAYER" else _srv._get_table(map_obj, member_name)
    if kind not in {"LAYER", "TABLE"}:
        raise RuntimeError("member_type 须为 LAYER 或 TABLE")
    result = _srv.cartography.delete_definition_query(member, name)
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_table_properties", description="读取独立表 URI、数据源、定义查询、连接信息和选择集摘要，并脱敏凭据。")
def arcgis_pro_table_properties(
    aprx_path: str,
    map_name: str,
    table_name: str,
    selection_sample_limit: int = 100,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.table_properties(
        _srv._get_table(_srv._get_map(project, map_name), table_name), selection_sample_limit
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_update_table_properties", description="更新独立表名称或 definitionQuery 并读回核验。")
def arcgis_pro_update_table_properties(
    aprx_path: str,
    map_name: str,
    table_name: str,
    new_name: str | None = None,
    definition_query: str | None = None,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.update_table_properties(
        _srv._get_table(_srv._get_map(project, map_name), table_name),
        new_name=new_name,
        definition_query=definition_query,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_select_table_by_attribute", description="按 SQL 修改独立表选择集并将 ArcPy 派生计数与实际选择集交叉核验。")
def arcgis_pro_select_table_by_attribute(
    aprx_path: str,
    map_name: str,
    table_name: str,
    selection_type: str = "NEW_SELECTION",
    where_clause: str = "",
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    table = _srv._get_table(_srv._get_map(project, map_name), table_name)
    selection = selection_type.strip().upper()
    allowed = {"NEW_SELECTION", "ADD_TO_SELECTION", "REMOVE_FROM_SELECTION", "SUBSET_SELECTION", "SWITCH_SELECTION", "CLEAR_SELECTION"}
    if selection not in allowed:
        raise RuntimeError(f"selection_type 须为 {sorted(allowed)}")
    where = (where_clause or "").strip()
    if selection not in {"SWITCH_SELECTION", "CLEAR_SELECTION"} and not where:
        raise RuntimeError("where_clause 不能为空")
    result = arcpy.management.SelectLayerByAttribute(table, selection, where or None)
    selected_count, result_count = _srv._verify_selection_result(
        table, result, count_output_index=1
    )
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "table_name": getattr(table, "name", table_name),
            "selected_count": selected_count,
            "result_count": result_count,
            "selection_verified": True,
        }
    )


@tool(name="arcgis_pro_table_selection_count", description="返回独立表当前选择集的精确行数，不用总记录数代替。")
def arcgis_pro_table_selection_count(aprx_path: str, map_name: str, table_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    table = _srv._get_table(_srv._get_map(project, map_name), table_name)
    selected = _srv._layer_selection_set(table)
    return _srv._json_dumps({"ok": True, "aprx_path": path, "table_name": table_name, "selected_count": len(selected)})


@tool(name="arcgis_pro_table_selection_fids", description="返回独立表当前选择集的有序 OID，带上限和截断标志。")
def arcgis_pro_table_selection_fids(
    aprx_path: str,
    map_name: str,
    table_name: str,
    max_items: int = 1000,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    table = _srv._get_table(_srv._get_map(project, map_name), table_name)
    values = sorted(_srv._layer_selection_set(table))
    limit = max(1, min(int(max_items), 5000))
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "table_name": table_name,
            "selected_count": len(values),
            "fids": values[:limit],
            "truncated": len(values) > limit,
        }
    )


@tool(
    name="arcgis_pro_layer_enable_time",
    description="启用或禁用图层时间属性；这不会控制活动 MapView 的时间滑块。",
)
def arcgis_pro_layer_enable_time(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    enabled: bool = True,
    start_time_field: str = "",
    end_time_field: str = "",
    auto_calculate_time_range: bool = True,
    time_dimension: str = "",
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    layer = _srv._find_layer(_srv._get_map(project, map_name), layer_name)
    if enabled:
        if not hasattr(layer, "enableTime"):
            raise RuntimeError("当前图层不支持 enableTime")
        layer.enableTime(
            startTimeField=start_time_field or None,
            endTimeField=end_time_field or None,
            autoCalculateTimeRange=bool(auto_calculate_time_range),
            timeDimension=time_dimension or None,
        )
    else:
        if not hasattr(layer, "disableTime"):
            raise RuntimeError("当前图层不支持 disableTime")
        layer.disableTime()
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layer_name": str(getattr(layer, "name", layer_name)),
            "time_enabled": bool(getattr(layer, "isTimeEnabled", enabled)),
            "active_mapview_slider_changed": False,
        }
    )


def _project_item(
    project: Any,
    map_name: str,
    item_kind: str,
    item_identifier: str,
) -> Any:
    map_object = _srv._get_map(project, map_name)
    kind = item_kind.strip().upper()
    if kind == "LAYER":
        return _srv._find_layer(map_object, item_identifier)
    if kind == "TABLE":
        return _srv._get_table(map_object, item_identifier)
    raise RuntimeError("item_kind 须为 LAYER 或 TABLE")


def _project_data_source(
    project: Any,
    map_name: str,
    source_kind: str,
    source_identifier: str,
) -> Any:
    kind = source_kind.strip().upper()
    if kind == "PATH":
        return _srv.validate_input_path_optional(source_identifier, "source_identifier")
    return _srv._project_item(project, map_name, kind, source_identifier)


@tool(
    name="arcgis_pro_import_document",
    description="把受控输入根内的 MXD/3DD/SXD/MAPX/PAGX/RPTX 导入工程，并返回新增项目清单。",
)
def arcgis_pro_import_document(
    aprx_path: str,
    document_path: str,
    include_layout: bool = True,
    reuse_existing_maps: bool = False,
    log_files: bool = False,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_io.import_document(
        project,
        document_path,
        include_layout=include_layout,
        reuse_existing_maps=reuse_existing_maps,
        log_files=log_files,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_export_mapx",
    description="将唯一地图导出为受控 .mapx；已有输出必须使用破坏性门禁并精确确认路径。",
)
def arcgis_pro_export_mapx(
    aprx_path: str,
    map_name: str,
    output_path: str,
    overwrite: bool = False,
    confirm_overwrite_path: str = "",
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_io.export_mapx(
        _srv._get_map(project, map_name),
        output_path,
        overwrite=overwrite,
        confirm_overwrite_path=confirm_overwrite_path,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_save_layer_file",
    description="将唯一图层或独立表保存为受控 .lyrx；已有输出必须使用破坏性门禁并精确确认路径。",
)
def arcgis_pro_save_layer_file(
    aprx_path: str,
    map_name: str,
    item_kind: str,
    item_identifier: str,
    output_path: str,
    overwrite: bool = False,
    confirm_overwrite_path: str = "",
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_io.save_layer_file(
        _srv._project_item(project, map_name, item_kind, item_identifier),
        output_path,
        overwrite=overwrite,
        confirm_overwrite_path=confirm_overwrite_path,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_paste_layer_properties",
    description="按显式语义类别复制图层属性，支持 POPUPS、DISPLAY_FILTERS、SYMBOLOGY 等；禁止 ALL。",
)
def arcgis_pro_paste_layer_properties(
    aprx_path: str,
    target_map_name: str,
    target_layer_identifier: str,
    source_map_name: str,
    source_layer_identifier: str,
    properties: list[str],
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    target = _srv._find_layer(_srv._get_map(project, target_map_name), target_layer_identifier)
    source = _srv._find_layer(_srv._get_map(project, source_map_name), source_layer_identifier)
    result = _srv.project_io.paste_layer_properties(target, source, properties)
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_table_replace_data_source",
    description="使用 typed Table.updateConnectionProperties 替换独立表数据源并读回 broken 状态。",
)
def arcgis_pro_table_replace_data_source(
    aprx_path: str,
    map_name: str,
    table_identifier: str,
    current_workspace: str,
    new_workspace: str,
    new_dataset_name: str = "",
    auto_update_joins_and_relates: bool = True,
    validate: bool = True,
    ignore_case: bool = False,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    table = _srv._get_table(_srv._get_map(project, map_name), table_identifier)
    result = _srv.project_io.replace_item_connection(
        table,
        current_workspace,
        new_workspace,
        new_dataset_name=new_dataset_name,
        auto_update_joins_and_relates=auto_update_joins_and_relates,
        validate=validate,
        ignore_case=ignore_case,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_connection_repair_preflight",
    description="对最多 100 个图层/表数据源修复生成绑定当前连接摘要的短期签名预检令牌。",
)
def arcgis_pro_connection_repair_preflight(
    aprx_path: str,
    repairs: list[dict[str, Any]],
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_io.connection_repair_preflight(project, repairs)
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_connection_repair_apply",
    description="仅在修复计划和目标连接未改变时应用批量数据源修复；拒绝陈旧或篡改的预检令牌。",
)
def arcgis_pro_connection_repair_apply(
    aprx_path: str,
    repairs: list[dict[str, Any]],
    repair_token: str,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_io.connection_repair_apply(project, repairs, repair_token)
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_add_relate",
    description="向唯一图层或表视图添加临时 relate；永久关系仍使用 CreateRelationshipClass。",
)
def arcgis_pro_add_relate(
    aprx_path: str,
    map_name: str,
    item_kind: str,
    item_identifier: str,
    input_field: str,
    relate_table_path: str,
    relate_field: str,
    relate_name: str,
    cardinality: str = "ONE_TO_MANY",
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    item = _srv._project_item(project, map_name, item_kind, item_identifier)
    relate_table = _srv.validate_input_path_optional(relate_table_path, "relate_table_path")
    result = _srv.project_io.add_relate(
        arcpy,
        item,
        input_field,
        relate_table,
        relate_field,
        relate_name,
        cardinality,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_remove_relate",
    description="从唯一图层或表视图移除命名 relate；需要破坏性门禁和精确名称确认。",
)
def arcgis_pro_remove_relate(
    aprx_path: str,
    map_name: str,
    item_kind: str,
    item_identifier: str,
    relate_name: str,
    confirm_relate_name: str,
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    result = _srv.project_io.remove_relate(
        arcpy,
        _srv._project_item(project, map_name, item_kind, item_identifier),
        relate_name,
        confirm_relate_name=confirm_relate_name,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_list_transformations",
    description="按源/目标 WKID 与可选范围列出 ArcPy 有效坐标转换；首项为 ArcPy 推荐顺序。",
)
def arcgis_pro_list_transformations(
    from_wkid: int,
    to_wkid: int,
    extent: list[float] | None = None,
    vertical: bool = False,
    first_only: bool = False,
) -> str:
    return _srv._json_dumps(
        _srv.project_io.list_transformations(
            _srv._arcpy(),
            from_wkid,
            to_wkid,
            extent=extent,
            vertical=vertical,
            first_only=first_only,
        )
    )
