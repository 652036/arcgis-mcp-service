"""MCP tools for ArcGIS Pro (ArcPy) and Portal / ArcGIS Online web maps."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "arcgis-pro-map",
    instructions=(
        "提供两类能力：(1) 通过 ArcPy 读取本机 ArcGIS Pro 工程 .aprx 中的地图与图层；"
        "(2) 通过 ArcGIS Portal / ArcGIS Online Sharing REST 读取 Web Map 条目与搜索内容。"
        "Pro 相关工具仅在 ArcGIS Pro 捆绑的 Python 中可用。"
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


def _portal_rest_root() -> str:
    base = os.environ.get("ARCGIS_PORTAL_URL", "https://www.arcgis.com/sharing/rest")
    return base.rstrip("/")


def _portal_token() -> str:
    return (
        os.environ.get("ARCGIS_TOKEN", "")
        or os.environ.get("ARCGIS_PORTAL_TOKEN", "")
        or ""
    ).strip()


def _portal_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    root = _portal_rest_root()
    q = dict(params)
    q.setdefault("f", "json")
    tok = _portal_token()
    if tok:
        q.setdefault("token", tok)
    url = f"{root}{path}?{urlencode(q)}"
    with httpx.Client(timeout=60.0) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise RuntimeError(msg)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected JSON response type")
    return data


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


@mcp.tool(
    name="arcgis_portal_webmap_json",
    description=(
        "获取 ArcGIS Online / Portal 上 Web Map 类型条目的 data JSON（operationalLayers、basemap 等）。"
        "item_id 为条目的 ID。公开地图可无 token；私有内容需配置 ARCGIS_TOKEN。"
    ),
)
def arcgis_portal_webmap_json(item_id: str) -> str:
    iid = item_id.strip()
    data = _portal_get(f"/content/items/{iid}/data", {})
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_portal_item_metadata",
    description="获取内容条目的元数据（标题、类型、url、extent 等），对应 /content/items/{id}。",
)
def arcgis_portal_item_metadata(item_id: str) -> str:
    iid = item_id.strip()
    data = _portal_get(f"/content/items/{iid}", {})
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_portal_search_items",
    description='在 Portal / ArcGIS Online 上搜索条目（q 语法与 REST search 一致，如 type:"Web Map"）。',
)
def arcgis_portal_search_items(
    q: str,
    num_results: int = 10,
    start: int = 1,
) -> str:
    n = max(1, min(int(num_results), 100))
    s = max(1, int(start))
    data = _portal_get(
        "/search",
        {"q": q.strip(), "num": n, "start": s},
    )
    return json.dumps(data, ensure_ascii=False, indent=2)
