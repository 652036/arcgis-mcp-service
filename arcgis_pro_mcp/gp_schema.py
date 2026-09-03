"""In-place schema changes (AddField / DeleteField) — no GP_OUTPUT_ROOT."""

from __future__ import annotations

import re
from typing import Any

from arcgis_pro_mcp.paths import require_allow_write, validate_input_path_optional

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


def run_delete_field(arcpy: Any, in_table: str, drop_field: str) -> None:
    require_allow_write()
    p = validate_input_path_optional(in_table, "in_table")
    names = _drop_names_ok(drop_field)
    for nm in names:
        arcpy.management.DeleteField(p, nm)  # type: ignore[attr-defined]
