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


_MAX_MULTI_INPUTS = 20
_MAX_SQL = 8000


def _sql(w: str) -> str | None:
    s = (w or "").strip()
    if len(s) > _MAX_SQL:
        raise RuntimeError("SQL/where 表达式过长")
    return s or None


def _validate_path_list(paths: list[str], label: str) -> list[str]:
    if not paths:
        raise RuntimeError(f"{label} 至少提供 1 个路径")
    if len(paths) > _MAX_MULTI_INPUTS:
        raise RuntimeError(f"{label} 最多 {_MAX_MULTI_INPUTS} 个路径")
    return [validate_input_path_optional(p, f"{label}_{i}") for i, p in enumerate(paths)]


def run_dissolve(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    dissolve_field: str = "",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    df = (dissolve_field or "").strip()
    if df:
        arcpy.analysis.Dissolve(inf, out, df)  # type: ignore[attr-defined]
    else:
        arcpy.analysis.Dissolve(inf, out)  # type: ignore[attr-defined]


def run_intersect(arcpy: Any, in_features: list[str], out_feature_class: str) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    if len(in_features) < 2:
        raise RuntimeError("Intersect 至少需要 2 个输入要素类/图层路径")
    ins = _validate_path_list(in_features, "intersect_in")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.Intersect(ins, out)  # type: ignore[attr-defined]


def run_union(arcpy: Any, in_features: list[str], out_feature_class: str) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    if len(in_features) < 2:
        raise RuntimeError("Union 至少需要 2 个输入")
    ins = _validate_path_list(in_features, "union_in")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.Union(ins, out)  # type: ignore[attr-defined]


def run_erase(
    arcpy: Any,
    in_features: str,
    erase_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    erf = validate_input_path_optional(erase_features, "erase_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.Erase(inf, erf, out)  # type: ignore[attr-defined]


def run_spatial_join(
    arcpy: Any,
    target_features: str,
    join_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    targ = validate_input_path_optional(target_features, "target_features")
    joinf = validate_input_path_optional(join_features, "join_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.analysis.SpatialJoin(targ, joinf, out)  # type: ignore[attr-defined]


def run_statistics(
    arcpy: Any,
    in_table: str,
    out_table: str,
    statistics_fields: str,
    case_field: str = "",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    intable = validate_input_path_optional(in_table, "in_table")
    outt = validate_gp_output_path(out_table, "out_table")
    sf = statistics_fields.strip()
    if not sf:
        raise RuntimeError("statistics_fields 不能为空（如 \"POP SUM;AREA MEAN\"）")
    cf = (case_field or "").strip()
    if cf:
        arcpy.analysis.Statistics(intable, outt, sf, cf)  # type: ignore[attr-defined]
    else:
        arcpy.analysis.Statistics(intable, outt, sf)  # type: ignore[attr-defined]


def run_frequency(
    arcpy: Any,
    in_table: str,
    out_table: str,
    frequency_fields: str,
    summary_fields: str = "",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    intable = validate_input_path_optional(in_table, "in_table")
    outt = validate_gp_output_path(out_table, "out_table")
    ff = frequency_fields.strip()
    if not ff:
        raise RuntimeError("frequency_fields 不能为空")
    summ = (summary_fields or "").strip()
    if summ:
        arcpy.analysis.Frequency(intable, outt, ff, summ)  # type: ignore[attr-defined]
    else:
        arcpy.analysis.Frequency(intable, outt, ff)  # type: ignore[attr-defined]


def run_table_select(
    arcpy: Any,
    in_table: str,
    out_table: str,
    where_clause: str = "",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    intable = validate_input_path_optional(in_table, "in_table")
    outt = validate_gp_output_path(out_table, "out_table")
    wc = _sql(where_clause)
    arcpy.analysis.TableSelect(intable, outt, wc)  # type: ignore[attr-defined]


def run_merge(arcpy: Any, inputs: list[str], output: str) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    if len(inputs) < 2:
        raise RuntimeError("Merge 至少需要 2 个输入")
    ins = _validate_path_list(inputs, "merge_in")
    out = validate_gp_output_path(output, "output")
    arcpy.management.Merge(ins, out)  # type: ignore[attr-defined]


def run_project(
    arcpy: Any,
    in_dataset: str,
    out_dataset: str,
    out_wkid: int,
    transform_method: str = "",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inds = validate_input_path_optional(in_dataset, "in_dataset")
    outd = validate_gp_output_path(out_dataset, "out_dataset")
    sr = arcpy.SpatialReference(int(out_wkid))  # type: ignore[attr-defined]
    tm = (transform_method or "").strip()
    if tm:
        arcpy.management.Project(inds, outd, sr, tm)  # type: ignore[attr-defined]
    else:
        arcpy.management.Project(inds, outd, sr)  # type: ignore[attr-defined]
