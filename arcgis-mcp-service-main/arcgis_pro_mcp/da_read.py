"""Read-only arcpy.da SearchCursor helpers."""

from __future__ import annotations

from typing import Any

_MAX_WHERE = 8000
_MAX_ORDER_BY = 2000
_MAX_CELL = 2000
_SKIP_TYPES = frozenset({"Geometry", "Raster"})


def _field_names_exist(arcpy: Any, dataset_path: str, fields: list[str]) -> set[str]:
    valid = {f.name for f in arcpy.ListFields(dataset_path)}
    missing = [x for x in fields if x not in valid]
    if missing:
        raise RuntimeError(f"未知字段：{missing}；可用字段示例：{sorted(valid)[:40]}...")
    return valid


def _field_type_map(arcpy: Any, dataset_path: str) -> dict[str, str]:
    return {f.name: f.type for f in arcpy.ListFields(dataset_path)}


def table_sample(
    arcpy: Any,
    dataset_path: str,
    fields: list[str],
    where_clause: str = "",
    max_rows: int = 50,
    include_shape_wkt: bool = False,
) -> list[dict[str, Any]]:
    if not fields:
        raise RuntimeError("fields 不能为空，且不允许使用 *")
    w = (where_clause or "").strip()
    if len(w) > _MAX_WHERE:
        raise RuntimeError("where_clause 过长")
    cap = max(1, min(int(max_rows), 500))
    fnames = [f.strip() for f in fields if f.strip()]
    if not fnames:
        raise RuntimeError("fields 无效")
    for f in fnames:
        if f.upper().startswith("SHAPE@"):
            raise RuntimeError("几何请使用参数 include_shape_wkt=true，勿在 fields 中传入 SHAPE@*")
    _field_names_exist(arcpy, dataset_path, fnames)
    types = _field_type_map(arcpy, dataset_path)
    cursor_fields: list[str] = []
    for f in fnames:
        t = types.get(f, "")
        if t in _SKIP_TYPES:
            raise RuntimeError(f"字段 {f!r} 类型 {t} 不允许在此工具中读取")
        cursor_fields.append(f)
    if include_shape_wkt:
        cursor_fields.append("SHAPE@WKT")
    rows_out: list[dict[str, Any]] = []
    with arcpy.da.SearchCursor(dataset_path, cursor_fields, w or None) as cur:  # type: ignore[attr-defined]
        for i, row in enumerate(cur):
            if i >= cap:
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
                    d[name] = s if len(s) <= _MAX_CELL else s[: _MAX_CELL] + "…"
            rows_out.append(d)
    return rows_out


def query_rows(
    arcpy: Any,
    dataset_path: str,
    fields: list[str],
    where_clause: str = "",
    order_by: str = "",
    max_rows: int = 100,
    offset: int = 0,
    include_shape_wkt: bool = False,
) -> list[dict[str, Any]]:
    if not fields:
        raise RuntimeError("fields 涓嶈兘涓虹┖锛屼笖涓嶅厑璁镐娇鐢?*")
    w = (where_clause or "").strip()
    if len(w) > _MAX_WHERE:
        raise RuntimeError("where_clause 杩囬暱")
    ob = (order_by or "").strip()
    if len(ob) > _MAX_ORDER_BY:
        raise RuntimeError("order_by 杩囬暱")
    cap = max(1, min(int(max_rows), 1000))
    skip = max(0, min(int(offset), 1_000_000))
    fnames = [f.strip() for f in fields if f.strip()]
    if not fnames:
        raise RuntimeError("fields 鏃犳晥")
    for f in fnames:
        if f.upper().startswith("SHAPE@"):
            raise RuntimeError("鍑犱綍璇蜂娇鐢ㄥ弬鏁?include_shape_wkt=true锛屽嬁鍦?fields 涓紶鍏?SHAPE@*")
    _field_names_exist(arcpy, dataset_path, fnames)
    types = _field_type_map(arcpy, dataset_path)
    cursor_fields: list[str] = []
    for f in fnames:
        t = types.get(f, "")
        if t in _SKIP_TYPES:
            raise RuntimeError(f"瀛楁 {f!r} 绫诲瀷 {t} 涓嶅厑璁稿湪姝ゅ伐鍏蜂腑璇诲彇")
        cursor_fields.append(f)
    if include_shape_wkt:
        cursor_fields.append("SHAPE@WKT")
    sql_clause = (None, ob) if ob else None
    rows_out: list[dict[str, Any]] = []
    with arcpy.da.SearchCursor(  # type: ignore[attr-defined]
        dataset_path,
        cursor_fields,
        w or None,
        sql_clause=sql_clause,
    ) as cur:
        for i, row in enumerate(cur):
            if i < skip:
                continue
            d: dict[str, Any] = {}
            for j, name in enumerate(cursor_fields):
                val = row[j]
                if val is None:
                    d[name] = None
                elif isinstance(val, (int, float, bool)):
                    d[name] = val
                else:
                    s = str(val)
                    d[name] = s if len(s) <= _MAX_CELL else s[: _MAX_CELL] + "..."
            rows_out.append(d)
            if len(rows_out) >= cap:
                break
    return rows_out


def distinct_values(
    arcpy: Any,
    dataset_path: str,
    field_name: str,
    where_clause: str = "",
    max_values: int = 100,
    max_rows_scanned: int = 50_000,
) -> list[Any]:
    fn = field_name.strip()
    if not fn:
        raise RuntimeError("field_name 不能为空")
    w = (where_clause or "").strip()
    if len(w) > _MAX_WHERE:
        raise RuntimeError("where_clause 过长")
    mv = max(1, min(int(max_values), 1000))
    mscan = max(1, min(int(max_rows_scanned), 500_000))
    _field_names_exist(arcpy, dataset_path, [fn])
    types = _field_type_map(arcpy, dataset_path)
    if types.get(fn) in _SKIP_TYPES:
        raise RuntimeError("不支持对几何/栅格字段做 distinct")
    seen: set[Any] = set()
    ordered: list[Any] = []
    scanned = 0
    with arcpy.da.SearchCursor(dataset_path, [fn], w or None) as cur:  # type: ignore[attr-defined]
        for row in cur:
            scanned += 1
            if scanned > mscan:
                break
            v = row[0]
            if v in seen:
                continue
            seen.add(v)
            ordered.append(v)
            if len(ordered) >= mv:
                break
    return ordered
