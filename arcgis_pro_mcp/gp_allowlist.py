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


def list_registered_gp_tools() -> list[dict[str, str]]:
    return [
        {
            "name": "arcgis_pro_gp_get_count",
            "role": "返回要素类/表等的记录数（GetCount）",
        },
        {
            "name": "arcgis_pro_gp_get_raster_property",
            "role": "读取栅格属性（GetRasterProperties，property_type 已白名单）",
        },
    ]
