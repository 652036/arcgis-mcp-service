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
        {
            "name": "arcgis_pro_gp_get_count",
            "role": "返回要素类/表等的记录数（GetCount）",
        },
        {
            "name": "arcgis_pro_gp_get_raster_property",
            "role": "读取栅格属性（GetRasterProperties，property_type 已白名单）",
        },
        {
            "name": "arcgis_pro_gp_get_cell_value",
            "role": "栅格像元值（GetCellValue，location_xy 为地图单位坐标）",
        },
        {
            "name": "arcgis_pro_gp_test_schema_lock",
            "role": "测试数据集是否可获取方案锁（TestSchemaLock）",
        },
        {
            "name": "arcgis_pro_gp_buffer",
            "role": "Buffer 输出至 GP_OUTPUT_ROOT（须 ALLOW_WRITE）",
        },
        {
            "name": "arcgis_pro_gp_clip",
            "role": "Clip 输出至 GP_OUTPUT_ROOT",
        },
        {
            "name": "arcgis_pro_gp_analysis_select",
            "role": "analysis.Select 子集输出至 GP_OUTPUT_ROOT",
        },
        {
            "name": "arcgis_pro_gp_copy_features",
            "role": "CopyFeatures 输出至 GP_OUTPUT_ROOT",
        },
    ]
