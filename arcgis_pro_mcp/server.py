"""MCP tools for ArcGIS Pro via ArcPy."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "arcgis-pro",
    instructions=(
        "通过 ArcPy 读取本机 ArcGIS Pro 工程（.aprx）中的地图、布局与图层。"
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


@mcp.tool(
    name="arcgis_pro_list_maps",
    description="列出指定 .aprx 工程内的所有地图名称。必须在 ArcGIS Pro 的 Python 环境中运行。",
)
def arcgis_pro_list_maps(aprx_path: str) -> str:
    arcpy = _arcpy()
    path = aprx_path.strip().strip('"')
    project = arcpy.mp.ArcGISProject(path)
    names = [m.name for m in project.listMaps()]
    return json.dumps({"aprx_path": path, "maps": names}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_layouts",
    description="列出 .aprx 中所有布局（Layout）名称。需 ArcGIS Pro Python。",
)
def arcgis_pro_list_layouts(aprx_path: str) -> str:
    arcpy = _arcpy()
    path = aprx_path.strip().strip('"')
    project = arcpy.mp.ArcGISProject(path)
    names = [lyt.name for lyt in project.listLayouts()]
    return json.dumps({"aprx_path": path, "layouts": names}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_layers",
    description=(
        "列出某一地图中的图层：名称、是否组图层、可见性，以及非组图层的数据源路径（若可读取）。"
        "需 ArcGIS Pro Python。"
    ),
)
def arcgis_pro_list_layers(aprx_path: str, map_name: str) -> str:
    arcpy = _arcpy()
    path = aprx_path.strip().strip('"')
    project = arcpy.mp.ArcGISProject(path)
    target = None
    for m in project.listMaps():
        if m.name == map_name:
            target = m
            break
    if target is None:
        available = [m.name for m in project.listMaps()]
        raise RuntimeError(f"未找到地图 {map_name!r}，可选：{available}")

    layers_out: list[dict[str, Any]] = []
    for lyr in target.listLayers():
        entry: dict[str, Any] = {
            "name": lyr.name,
            "is_group_layer": lyr.isGroupLayer,
            "visible": lyr.visible,
        }
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
