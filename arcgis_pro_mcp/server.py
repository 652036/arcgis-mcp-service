"""MCP tools for ArcGIS Pro via arcpy.mp (mapping module)."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from arcgis_pro_mcp import gp_allowlist
from arcgis_pro_mcp.paths import (
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
        "导出路径受 ARCGIS_PRO_MCP_EXPORT_ROOT 约束（若设置）；GP 输出可受 ARCGIS_PRO_MCP_GP_OUTPUT_ROOT 约束。"
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
    raise RuntimeError(f"未找到地图 {map_name!r}，可选：{available}")


def _get_layout(project: Any, layout_name: str) -> Any:
    for lyt in project.listLayouts():
        if lyt.name == layout_name:
            return lyt
    available = [lyt.name for lyt in project.listLayouts()]
    raise RuntimeError(f"未找到布局 {layout_name!r}，可选：{available}")


def _find_layer(map_obj: Any, layer_name: str) -> Any:
    for lyr in map_obj.listLayers():
        if lyr.name == layer_name:
            return lyr
    names = [x.name for x in map_obj.listLayers()]
    raise RuntimeError(f"未找到图层 {layer_name!r}，可选：{names}")


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
    description=(
        "返回当前 ArcPy / ArcGIS Pro 安装与产品信息（无需打开 .aprx）。"
        "用于确认是否使用了 Pro 自带的 Python。"
    ),
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
    description=(
        "返回当前进程的安全开关与已注册工具分类摘要：是否允许写入、导出根目录是否配置、"
        "白名单 GP 工具列表等。调用方可据此决定可用能力。"
    ),
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
        "arcgis_pro_gp_list_registered",
    ]
    tools_write = [
        "arcgis_pro_save_project",
        "arcgis_pro_save_project_copy",
        "arcgis_pro_set_layer_visible",
        "arcgis_pro_set_layer_transparency",
        "arcgis_pro_set_definition_query",
        "arcgis_pro_select_layer_by_attribute",
        "arcgis_pro_mapframe_zoom_to_bookmark",
        "arcgis_pro_add_layer_from_path",
        "arcgis_pro_remove_layer",
    ]
    tools_export = [
        "arcgis_pro_export_layout_pdf",
        "arcgis_pro_export_layout_image",
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
            "note": "无法通过 MCP 驱动 Pro 的每一个菜单/窗格；未列出的能力可能需手动或使用其他自动化。",
        },
    )


@mcp.tool(
    name="arcgis_pro_list_maps",
    description="列出指定 .aprx 工程内的所有地图名称。必须在 ArcGIS Pro 的 Python 环境中运行。",
)
def arcgis_pro_list_maps(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    names = [m.name for m in project.listMaps()]
    return json.dumps({"aprx_path": path, "maps": names}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_layouts",
    description="列出 .aprx 中所有布局（Layout）名称。需 ArcGIS Pro Python。",
)
def arcgis_pro_list_layouts(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    names = [lyt.name for lyt in project.listLayouts()]
    return json.dumps({"aprx_path": path, "layouts": names}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_reports",
    description="列出工程中的报表（Report）名称。部分旧版本 Pro 可能不支持 listReports。",
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
    description=(
        "对数据集、图层数据源或工作空间路径执行 arcpy.Describe，返回常用属性子集（类型、范围、空间参考等）。"
        "dataset_path 可为要素类、栅格、图层数据源字符串、GDB 路径等。"
    ),
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
    description="列出表或要素类的字段：名称、类型、别名、长度、精度、是否可空等（arcpy.ListFields）。",
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
    description=(
        "列出 .aprx 工程中的文件夹连接、数据库连接、工具箱等（取决于当前 Pro 版本的 API）。"
        "用于检查工程引用的外部路径。"
    ),
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
    description="工程概览：地图数、布局数、报表数（若支持）、损坏数据源条目（可限制条数）。",
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
    description=(
        "列出某一地图中的图层：名称、是否组/栅格/要素图层、可见性，以及非组图层的数据源路径（若可读取）。"
    ),
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
    description="列出地图中打开的表（独立表、非图层表视图等，取决于工程内容）。",
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
    description="读取地图的空间参考（名称、WKID、类型、WKT 片段）。",
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
    description="读取地图默认视图（Camera）信息：比例尺、XY 等（若 API 可用）。",
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
    description="列出地图中的书签名称（及可获取的关联信息）。",
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
    description=(
        "查询单个图层的常用制图与数据属性：可见性、透明度、定义查询、符号系统类型、"
        "是否可编辑等（字段因图层类型而异）。"
    ),
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
    description=(
        "列出布局中的元素：类型（如 MAPFRAME_ELEMENT、LEGEND_ELEMENT）、名称。"
        "可选 element_type 过滤，例如仅列出 MAPFRAME_ELEMENT。"
    ),
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
    description="读取布局中指定地图框（Map Frame）的当前范围与比例尺、所关联的地图名。",
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
        raise RuntimeError(f"未找到地图框 {mapframe_name!r}，可选：{names}")

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
    description=(
        "将指定布局导出为 PDF 文件（对应 Pro 中布局的 exportToPDF）。"
        "output_pdf_path 必须为绝对路径；若设置环境变量 ARCGIS_PRO_MCP_EXPORT_ROOT，"
        "则导出路径解析后必须位于该目录下。若文件已存在可能被覆盖。"
        "建议在导出前确认 .aprx 无独占写入冲突。"
    ),
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
    description=(
        "将布局导出为光栅图：format=png|jpeg|tiff，须使用绝对路径。"
        "受 ARCGIS_PRO_MCP_EXPORT_ROOT 约束（若设置）。TIFF 可写 world_file。"
    ),
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
    description="保存对当前 .aprx 的内存修改到原文件。需 ARCGIS_PRO_MCP_ALLOW_WRITE=1；注意 Pro 独占打开时可能失败。",
)
def arcgis_pro_save_project(aprx_path: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    project.save()
    return _json_dumps({"ok": True, "aprx_path": path, "saved": True})


@mcp.tool(
    name="arcgis_pro_save_project_copy",
    description="将工程另存为新 .aprx（saveACopy）。输出路径须满足 ARCGIS_PRO_MCP_EXPORT_ROOT（若设置）。需 ALLOW_WRITE。",
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
    description="设置图层可见性。修改在 save_project 之前仅存在于内存。需 ALLOW_WRITE。",
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
    description="设置图层透明度 0–100。需 ALLOW_WRITE。",
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
    description="设置要素图层的定义查询 SQL 字符串。错误 SQL 可能导致图层无要素。需 ALLOW_WRITE。",
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
    description=(
        "对地图中已存在图层执行 SelectLayerByAttribute。"
        "selection_type 为固定枚举；where_clause 最长 8000。"
        "会改变当前地图选择，需 ALLOW_WRITE。"
    ),
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
        raise RuntimeError(f"非法 selection_type，可选：{sorted(_SELECTION_TYPES)}")
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
    name="arcgis_pro_mapframe_zoom_to_bookmark",
    description=(
        "将布局中的地图框缩放到指定书签视图（会改变布局中地图框状态，非纯只读）。"
        "书签来自该地图框所关联的地图。需 ALLOW_WRITE。"
    ),
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
        raise RuntimeError(f"未找到地图框 {mapframe_name!r}，可选：{names}")
    bkmk = None
    try:
        for b in mf.map.listBookmarks():
            if b.name == bookmark_name:
                bkmk = b
                break
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"读取书签失败：{ex!s}") from ex
    if bkmk is None:
        names = [b.name for b in mf.map.listBookmarks()]
        raise RuntimeError(f"未找到书签 {bookmark_name!r}，可选：{names}")
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
    description="向地图添加数据（addDataFromPath）。data_path 若配置 ARCGIS_PRO_MCP_INPUT_ROOTS 则须在其下。需 ALLOW_WRITE。",
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
    description="从地图中移除指定图层（removeLayer）。需 ALLOW_WRITE。",
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
    name="arcgis_pro_gp_list_registered",
    description="列出本服务白名单内的地理处理类 MCP 工具及说明。",
)
def arcgis_pro_gp_list_registered() -> str:
    return _json_dumps({"gp_tools": gp_allowlist.list_registered_gp_tools()})


@mcp.tool(
    name="arcgis_pro_gp_get_count",
    description="白名单 GP：GetCount。dataset_path 可受 ARCGIS_PRO_MCP_INPUT_ROOTS 约束。",
)
def arcgis_pro_gp_get_count(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    cnt = gp_allowlist.gp_get_count(arcpy, p)
    return _json_dumps({"dataset_path": p, "count": cnt})


@mcp.tool(
    name="arcgis_pro_gp_get_raster_property",
    description="白名单 GP：GetRasterProperties，property_type 仅允许预置枚举。",
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
