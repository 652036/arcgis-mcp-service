"""Controlled arcpy.da UpdateCursor writes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from arcgis_pro_mcp.paths import require_allow_write, validate_input_path_optional

_MAX_WHERE = 8000
_MAX_ROWS_CAP = 5000
_SKIP_TYPES = frozenset({"Geometry", "Raster"})


def _field_by_name(arcpy: Any, dataset_path: str, field_name: str) -> Any:
    fn = field_name.strip()
    if not fn:
        raise RuntimeError("field_name 不能为空")
    if fn.upper().startswith("SHAPE@"):
        raise RuntimeError("不支持几何字段")
    for f in arcpy.ListFields(dataset_path):
        if f.name == fn:
            return f
    names = [x.name for x in arcpy.ListFields(dataset_path)]
    raise RuntimeError(f"未找到字段 {fn!r}，示例：{names[:30]}")


def _parse_value_for_field(field: Any, value_string: str) -> Any | None:
    raw = value_string
    if raw.strip() == "":
        return None
    t = field.type
    s = raw.strip()
    if t in ("String", "GUID"):
        if len(s) > (getattr(field, "length", 8000) or 8000):
            raise RuntimeError("字符串长度超过字段长度")
        return s
    if t in ("Integer", "SmallInteger", "OID"):
        return int(s)
    if t in ("Double", "Single"):
        return float(s)
    if t == "Date":
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return s
    raise RuntimeError(f"暂不支持的字段类型用于常量更新：{t}")


def update_field_constant(
    arcpy: Any,
    dataset_path: str,
    field_name: str,
    value_string: str,
    where_clause: str = "",
    max_rows_updated: int = 1000,
) -> tuple[int, bool]:
    require_allow_write()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    wc = (where_clause or "").strip()
    if len(wc) > _MAX_WHERE:
        raise RuntimeError("where_clause 过长")
    cap = max(1, min(int(max_rows_updated), _MAX_ROWS_CAP))
    fobj = _field_by_name(arcpy, p, field_name)
    if fobj.type in _SKIP_TYPES:
        raise RuntimeError("不支持更新几何/栅格字段")
    if fobj.type == "OID":
        raise RuntimeError("不建议通过 MCP 更新 OID 字段")
    val = _parse_value_for_field(fobj, value_string)
    fn = fobj.name
    n = 0
    truncated = False
    with arcpy.da.UpdateCursor(p, [fn], wc or None) as cur:  # type: ignore[attr-defined]
        for row in cur:
            if n >= cap:
                truncated = True
                break
            row[0] = val
            cur.updateRow(row)
            n += 1
    return n, truncated
