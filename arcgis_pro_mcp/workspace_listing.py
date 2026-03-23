"""Read-only workspace listing (ListFeatureClasses / ListRasters / ListTables)."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any

_WILD_RE = re.compile(r"^[\w\*\?.\\/-]+$")
_FEATURE_TYPES = frozenset(
    {
        "",
        "All",
        "Point",
        "Polyline",
        "Polygon",
        "Multipoint",
        "Annotation",
        "Dimension",
    },
)


@contextmanager
def _workspace_ctx(arcpy: Any, workspace: str):
    old = arcpy.env.workspace
    arcpy.env.workspace = workspace
    try:
        yield
    finally:
        arcpy.env.workspace = old


def _sanitize_wildcard(w: str, max_len: int = 120) -> str:
    s = w.strip()
    if len(s) > max_len:
        raise RuntimeError("wild_card 过长")
    if s and not _WILD_RE.match(s):
        raise RuntimeError("wild_card 仅允许字母数字、* ? . _ - / \\")
    return s or "*"


def list_feature_classes(
    arcpy: Any,
    workspace_path: str,
    feature_dataset: str = "",
    feature_type: str = "",
    wild_card: str = "*",
    max_items: int = 200,
) -> list[str]:
    ft = (feature_type or "").strip()
    if ft not in _FEATURE_TYPES:
        raise RuntimeError(f"不支持的 feature_type，可选：{sorted(_FEATURE_TYPES)}")
    cap = max(1, min(int(max_items), 2000))
    wc = _sanitize_wildcard(wild_card)
    fd = feature_dataset.strip()
    ft_arg = None if ft in ("", "All") else ft
    with _workspace_ctx(arcpy, workspace_path):
        names = arcpy.ListFeatureClasses(wc, ft_arg, fd or None) or []
    return [str(n) for n in names[:cap]]


def list_rasters(
    arcpy: Any,
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> list[str]:
    cap = max(1, min(int(max_items), 2000))
    wc = _sanitize_wildcard(wild_card)
    with _workspace_ctx(arcpy, workspace_path):
        names = arcpy.ListRasters(wc) or []
    return [str(n) for n in names[:cap]]


def list_tables(
    arcpy: Any,
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> list[str]:
    cap = max(1, min(int(max_items), 2000))
    wc = _sanitize_wildcard(wild_card)
    with _workspace_ctx(arcpy, workspace_path):
        names = arcpy.ListTables(wc) or []
    return [str(n) for n in names[:cap]]
