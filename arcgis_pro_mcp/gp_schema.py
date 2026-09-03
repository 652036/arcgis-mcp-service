"""In-place schema changes (AddField / DeleteField) — no GP_OUTPUT_ROOT."""

from __future__ import annotations

import re
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_destructive,
    require_allow_write,
    validate_input_path_optional,
)

_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

_BLOCKED_NAMES = frozenset(
    {
        "OBJECTID",
        "OID",
        "FID",
        "SHAPE",
        "SHAPE_LENGTH",
        "SHAPE_AREA",
        "GLOBALID",
    },
)

_FIELD_TYPES = frozenset({"TEXT", "SHORT", "LONG", "FLOAT", "DOUBLE", "DATE"})


def _field_name_ok(name: str, *, for_add: bool) -> str:
    fn = name.strip()
    if not fn:
        raise RuntimeError("field_name 不能为空")
    if not _FIELD_RE.match(fn):
        raise RuntimeError("field_name 须匹配 ^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    if fn.upper() in _BLOCKED_NAMES:
        raise RuntimeError(f"保留/系统字段名不可使用：{fn!r}")
    return fn


def _drop_names_ok(raw: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[;,]", raw) if p.strip()]
    if not parts:
        raise RuntimeError("drop_field 不能为空")
    out: list[str] = []
    for p in parts:
        out.append(_field_name_ok(p, for_add=False))
    for p in out:
        if p.upper() in _BLOCKED_NAMES:
            raise RuntimeError(f"禁止删除系统字段：{p!r}")
    return out


def run_add_field(
    arcpy: Any,
    in_table: str,
    field_name: str,
    field_type: str,
    field_length: int | None = None,
) -> None:
    require_allow_write()
    p = validate_input_path_optional(in_table, "in_table")
    fn = _field_name_ok(field_name, for_add=True)
    ft = field_type.strip().upper()
    if ft not in _FIELD_TYPES:
        raise RuntimeError(f"field_type 须为 {sorted(_FIELD_TYPES)}")
    if ft == "TEXT":
        fl = max(1, min(int(field_length or 255), 8000))
        arcpy.management.AddField(p, fn, ft, field_length=fl)  # type: ignore[attr-defined]
    else:
        arcpy.management.AddField(p, fn, ft)  # type: ignore[attr-defined]


def run_delete_field(
    arcpy: Any,
    in_table: str,
    drop_field: str,
    *,
    confirm_in_table: str,
    confirm_drop_fields: list[str],
) -> dict[str, Any]:
    require_allow_destructive()
    if confirm_in_table != in_table:
        raise RuntimeError("confirm_in_table 必须逐字符精确回显 in_table")
    p = validate_input_path_optional(in_table, "in_table")
    names = _drop_names_ok(drop_field)
    if not isinstance(confirm_drop_fields, list) or confirm_drop_fields != names:
        raise RuntimeError("confirm_drop_fields 必须按相同顺序精确回显待删除字段")
    schema_lock = getattr(arcpy, "TestSchemaLock", None)
    if not callable(schema_lock) or not bool(schema_lock(p)):
        raise RuntimeError(f"无法获取方案锁：{p}")
    list_fields = getattr(arcpy, "ListFields", None)
    if not callable(list_fields):
        raise RuntimeError("当前 ArcPy 不支持 ListFields，无法核验删除范围")
    before = {str(field.name).casefold() for field in list_fields(p)}
    missing = [name for name in names if name.casefold() not in before]
    if missing:
        raise RuntimeError(f"待删除字段不存在：{missing}")
    result = arcpy.management.DeleteField(p, names)  # type: ignore[attr-defined]
    after = {str(field.name).casefold() for field in list_fields(p)}
    remaining = [name for name in names if name.casefold() in after]
    if remaining:
        raise RuntimeError(f"DeleteField 返回后字段仍存在：{remaining}；不要自动重试")
    return {
        "in_table": p,
        "deleted_fields": names,
        "verified": True,
        "messages": str(getattr(result, "getMessages", lambda: "")() or "")[:4000],
    }
