"""Create data: feature classes, tables, GDBs, feature datasets, copy/rename/delete datasets."""

from __future__ import annotations

import re
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)

_GEOMETRY_TYPES = frozenset({"POINT", "MULTIPOINT", "POLYLINE", "POLYGON"})
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def run_create_feature_class(
    arcpy: Any,
    out_path: str,
    out_name: str,
    geometry_type: str,
    spatial_reference_wkid: int | None = None,
) -> str:
    require_allow_write()
    require_gp_output_root_mandatory()
    op = validate_gp_output_path(out_path, "out_path")
    on = out_name.strip()
    if not on:
        raise RuntimeError("out_name 不能为空")
    gt = geometry_type.strip().upper()
    if gt not in _GEOMETRY_TYPES:
        raise RuntimeError(f"geometry_type 须为 {sorted(_GEOMETRY_TYPES)}")
    sr = None
    if spatial_reference_wkid is not None:
        sr = arcpy.SpatialReference(int(spatial_reference_wkid))
    if sr:
        arcpy.management.CreateFeatureclass(op, on, gt, spatial_reference=sr)
    else:
        arcpy.management.CreateFeatureclass(op, on, gt)
    return f"{op}\\{on}"


def run_create_table(
    arcpy: Any,
    out_path: str,
    out_name: str,
) -> str:
    require_allow_write()
    require_gp_output_root_mandatory()
    op = validate_gp_output_path(out_path, "out_path")
    on = out_name.strip()
    if not on:
        raise RuntimeError("out_name 不能为空")
    arcpy.management.CreateTable(op, on)
    return f"{op}\\{on}"


def run_create_file_gdb(
    arcpy: Any,
    out_folder_path: str,
    out_name: str,
) -> str:
    require_allow_write()
    require_gp_output_root_mandatory()
    ofp = validate_gp_output_path(out_folder_path, "out_folder_path")
    on = out_name.strip()
    if not on:
        raise RuntimeError("out_name 不能为空")
    if not on.lower().endswith(".gdb"):
        on += ".gdb"
    arcpy.management.CreateFileGDB(ofp, on)
    return f"{ofp}\\{on}"


def run_create_feature_dataset(
    arcpy: Any,
    out_dataset_path: str,
    out_name: str,
    spatial_reference_wkid: int,
) -> str:
    require_allow_write()
    require_gp_output_root_mandatory()
    odp = validate_gp_output_path(out_dataset_path, "out_dataset_path")
    on = out_name.strip()
    if not on:
        raise RuntimeError("out_name 不能为空")
    sr = arcpy.SpatialReference(int(spatial_reference_wkid))
    arcpy.management.CreateFeatureDataset(odp, on, sr)
    return f"{odp}\\{on}"


def run_copy_feature_class(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    arcpy.management.CopyFeatures(inf, out)


def run_rename_dataset(
    arcpy: Any,
    in_data: str,
    out_data: str,
) -> None:
    require_allow_write()
    ind = validate_input_path_optional(in_data, "in_data")
    od = out_data.strip()
    if not od:
        raise RuntimeError("out_data 不能为空")
    arcpy.management.Rename(ind, od)


def run_delete_dataset(
    arcpy: Any,
    in_data: str,
) -> None:
    require_allow_write()
    ind = validate_input_path_optional(in_data, "in_data")
    arcpy.management.Delete(ind)


def run_alter_field(
    arcpy: Any,
    in_table: str,
    field_name: str,
    new_field_name: str = "",
    new_field_alias: str = "",
) -> None:
    require_allow_write()
    p = validate_input_path_optional(in_table, "in_table")
    fn = field_name.strip()
    if not fn:
        raise RuntimeError("field_name 不能为空")
    nfn = new_field_name.strip()
    nfa = new_field_alias.strip()
    if not nfn and not nfa:
        raise RuntimeError("至少提供 new_field_name 或 new_field_alias")
    kwargs: dict[str, str] = {}
    if nfn:
        if not _FIELD_RE.match(nfn):
            raise RuntimeError("new_field_name 格式不合法")
        kwargs["new_field_name"] = nfn
    if nfa:
        kwargs["new_field_alias"] = nfa
    arcpy.management.AlterField(p, fn, **kwargs)
