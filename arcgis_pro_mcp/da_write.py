"""Controlled arcpy.da UpdateCursor / InsertCursor / DeleteCursor writes."""

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


def _parse_value(field_type: str, raw: Any) -> Any:
    """Parse a JSON value into a Python value appropriate for the field type."""
    if raw is None:
        return None
    t = field_type
    if t in ("String", "GUID"):
        return str(raw)
    if t in ("Integer", "SmallInteger", "OID"):
        return int(raw)
    if t in ("Double", "Single"):
        return float(raw)
    if t == "Date":
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return raw
        return raw
    return raw


def insert_features(
    arcpy: Any,
    dataset_path: str,
    fields: list[str],
    rows: list[list[Any]],
) -> int:
    require_allow_write()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    if not fields:
        raise RuntimeError("fields 不能为空")
    if not rows:
        raise RuntimeError("rows 不能为空")
    if len(rows) > _MAX_ROWS_CAP:
        raise RuntimeError(f"单次插入最多 {_MAX_ROWS_CAP} 行")
    fnames = [f.strip() for f in fields if f.strip()]
    if not fnames:
        raise RuntimeError("fields 无效")
    type_map = {f.name: f.type for f in arcpy.ListFields(p)}
    cursor_fields: list[str] = []
    for f in fnames:
        if f.upper().startswith("SHAPE@"):
            cursor_fields.append(f)
        else:
            if f not in type_map:
                raise RuntimeError(f"未知字段: {f!r}")
            cursor_fields.append(f)
    n = 0
    with arcpy.da.InsertCursor(p, cursor_fields) as cur:
        for row_data in rows:
            if len(row_data) != len(cursor_fields):
                raise RuntimeError(
                    f"行字段数不匹配：期望 {len(cursor_fields)} 个值，得到 {len(row_data)}"
                )
            parsed = []
            for i, val in enumerate(row_data):
                fname = cursor_fields[i]
                if fname.upper().startswith("SHAPE@"):
                    parsed.append(val)
                else:
                    ft = type_map.get(fname, "String")
                    parsed.append(_parse_value(ft, val))
            cur.insertRow(parsed)
            n += 1
    return n


def update_features(
    arcpy: Any,
    dataset_path: str,
    updates: dict[str, Any],
    where_clause: str = "",
    max_rows_updated: int = 1000,
) -> tuple[int, bool]:
    require_allow_write()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    wc = (where_clause or "").strip()
    if len(wc) > _MAX_WHERE:
        raise RuntimeError("where_clause 过长")
    cap = max(1, min(int(max_rows_updated), _MAX_ROWS_CAP))
    if not updates:
        raise RuntimeError("updates 不能为空")
    update_fields = list(updates.keys())
    type_map = {f.name: f.type for f in arcpy.ListFields(p)}
    for uf in update_fields:
        if uf not in type_map:
            raise RuntimeError(f"未知字段: {uf!r}")
        if type_map[uf] in _SKIP_TYPES:
            raise RuntimeError(f"不支持更新 {type_map[uf]} 类型字段")
    n = 0
    truncated = False
    with arcpy.da.UpdateCursor(p, update_fields, wc or None) as cur:
        for row in cur:
            if n >= cap:
                truncated = True
                break
            for i, uf in enumerate(update_fields):
                ft = type_map[uf]
                row[i] = _parse_value(ft, updates[uf])
            cur.updateRow(row)
            n += 1
    return n, truncated


def delete_selected(
    arcpy: Any,
    dataset_path: str,
    where_clause: str,
    max_rows_deleted: int = 1000,
) -> tuple[int, bool]:
    require_allow_write()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    wc = (where_clause or "").strip()
    if not wc:
        raise RuntimeError("where_clause 不能为空（防止误删全部数据），如需清空请用 truncate_table")
    if len(wc) > _MAX_WHERE:
        raise RuntimeError("where_clause 过长")
    cap = max(1, min(int(max_rows_deleted), _MAX_ROWS_CAP))
    n = 0
    truncated = False
    with arcpy.da.UpdateCursor(p, ["OID@"], wc) as cur:
        for _ in cur:
            if n >= cap:
                truncated = True
                break
            cur.deleteRow()
            n += 1
    return n, truncated
