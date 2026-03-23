"""MCP tools for ArcGIS Pro via arcpy.mp (mapping module)."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "arcgis-pro",
    instructions=(
        "通过 ArcPy 读取与导出本机 ArcGIS Pro 工程（.aprx）：地图、表、书签、坐标系、布局元素、"
        "地图框范围、损坏数据源，以及将布局导出为 PDF。"
        "必须在已安装 ArcGIS Pro 的 Windows 上使用 Pro 捆绑的 Python 运行。"
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
    return json.dumps(info, ensure_ascii=False, indent=2)


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

    cap = max(0, min(int(max_broken_list), 500))
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
                out[attr] = float(v) if attr == "scale" else v
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
    lyr = None
    for x in target.listLayers():
        if x.name == layer_name:
            lyr = x
            break
    if lyr is None:
        names = [x.name for x in target.listLayers()]
        raise RuntimeError(f"未找到图层 {layer_name!r}，可选：{names}")

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
        "output_pdf_path 必须为可写的本地绝对路径；若文件已存在可能被覆盖。"
        "建议在导出前关闭正在独占打开该 .aprx 的 Pro 会话，或确认无写入冲突。"
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
    out_path = os.path.normpath(output_pdf_path.strip().strip('"'))
    if not out_path.lower().endswith(".pdf"):
        raise RuntimeError("output_pdf_path 应以 .pdf 结尾")
    dpi = max(72, min(int(resolution_dpi), 960))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    layout.exportToPDF(out_path, resolution=dpi)  # type: ignore[attr-defined]
    return json.dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "output_pdf_path": out_path,
            "resolution_dpi": dpi,
        },
        ensure_ascii=False,
        indent=2,
    )
