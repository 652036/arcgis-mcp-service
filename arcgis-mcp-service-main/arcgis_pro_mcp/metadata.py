"""Metadata read/write and data quality tools."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.paths import require_allow_write, validate_input_path_optional


def get_metadata(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    p = validate_input_path_optional(dataset_path, "dataset_path")
    md = arcpy.metadata.Metadata(p)
    out: dict[str, Any] = {}
    for attr in (
        "title", "tags", "summary", "description", "credits",
        "accessConstraints", "isReadOnly",
    ):
        try:
            v = getattr(md, attr, None)
            if v is not None:
                out[attr] = v
        except Exception:
            pass
    try:
        out["thumbnailUri"] = md.thumbnailUri
    except Exception:
        pass
    return out


def set_metadata(
    arcpy: Any,
    dataset_path: str,
    title: str = "",
    tags: str = "",
    summary: str = "",
    description: str = "",
    credits: str = "",
    access_constraints: str = "",
) -> None:
    require_allow_write()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    md = arcpy.metadata.Metadata(p)
    if md.isReadOnly:
        raise RuntimeError("元数据为只读，无法修改")
    if title:
        md.title = title
    if tags:
        md.tags = tags
    if summary:
        md.summary = summary
    if description:
        md.description = description
    if credits:
        md.credits = credits
    if access_constraints:
        md.accessConstraints = access_constraints
    md.save()


def validate_topology(arcpy: Any, in_topology: str) -> None:
    require_allow_write()
    p = validate_input_path_optional(in_topology, "in_topology")
    arcpy.management.ValidateTopology(p)
