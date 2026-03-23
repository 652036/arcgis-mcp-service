"""Allowlisted geoprocessing tools that write new datasets (sandboxed under GP_OUTPUT_ROOT)."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)


def run_buffer(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    buffer_distance_or_field: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.Buffer(inf, out, buffer_distance_or_field)  # type: ignore[attr-defined]


def run_clip(
    arcpy: Any,
    in_features: str,
    clip_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    clipf = validate_input_path_optional(clip_features, "clip_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.Clip(inf, clipf, out)  # type: ignore[attr-defined]


def run_select(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    where_clause: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    wc = (where_clause or "").strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause 过长")
    arcpy.analysis.Select(inf, out, wc or None)  # type: ignore[attr-defined]


def run_copy_features(arcpy: Any, in_features: str, out_feature_class: str) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.management.CopyFeatures(inf, out)  # type: ignore[attr-defined]
