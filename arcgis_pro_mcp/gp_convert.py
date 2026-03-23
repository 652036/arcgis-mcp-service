"""Import/export conversion tools: CSV, Excel, JSON, KML, Shapefile."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)


def run_table_to_table(arcpy: Any, in_rows: str, out_path: str, out_name: str) -> str:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_rows, "in_rows")
    op = validate_gp_output_path(out_path, "out_path")
    on = out_name.strip()
    if not on:
        raise RuntimeError("out_name 不能为空")
    arcpy.conversion.TableToTable(inf, op, on)
    return f"{op}\\{on}"


def run_excel_to_table(arcpy: Any, input_excel: str, out_table: str, sheet: str = "") -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(input_excel, "input_excel")
    out = validate_gp_output_path(out_table, "out_table")
    sh = (sheet or "").strip()
    if sh:
        arcpy.conversion.ExcelToTable(inf, out, sh)
    else:
        arcpy.conversion.ExcelToTable(inf, out)


def run_table_to_excel(arcpy: Any, in_table: str, output_excel: str) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_table, "in_table")
    out = validate_gp_output_path(output_excel, "output_excel")
    arcpy.conversion.TableToExcel(inf, out)


def run_xy_table_to_point(
    arcpy: Any,
    in_table: str,
    out_feature_class: str,
    x_field: str,
    y_field: str,
    spatial_reference_wkid: int = 4326,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_table, "in_table")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    xf = x_field.strip()
    yf = y_field.strip()
    if not xf or not yf:
        raise RuntimeError("x_field 和 y_field 不能为空")
    sr = arcpy.SpatialReference(int(spatial_reference_wkid))
    arcpy.management.XYTableToPoint(inf, out, xf, yf, coordinate_system=sr)


def run_json_to_features(arcpy: Any, in_json_file: str, out_features: str) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_json_file, "in_json_file")
    out = validate_gp_output_path(out_features, "out_features")
    arcpy.conversion.JSONToFeatures(inf, out)


def run_features_to_json(
    arcpy: Any,
    in_features: str,
    out_json_file: str,
    format_json: bool = True,
    include_z_values: bool = False,
    include_m_values: bool = False,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_json_file, "out_json_file")
    arcpy.conversion.FeaturesToJSON(
        inf, out,
        format_json="FORMATTED" if format_json else "NOT_FORMATTED",
        include_z_values="Z_VALUES" if include_z_values else "NO_Z_VALUES",
        include_m_values="M_VALUES" if include_m_values else "NO_M_VALUES",
    )


def run_kml_to_layer(arcpy: Any, in_kml_file: str, output_folder: str) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_kml_file, "in_kml_file")
    out = validate_gp_output_path(output_folder, "output_folder")
    arcpy.conversion.KMLToLayer(inf, out)


def run_feature_class_to_shapefile(
    arcpy: Any,
    input_features: list[str],
    output_folder: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    if not input_features:
        raise RuntimeError("input_features 不能为空")
    ins = [validate_input_path_optional(p, f"input_{i}") for i, p in enumerate(input_features)]
    out = validate_gp_output_path(output_folder, "output_folder")
    arcpy.conversion.FeatureClassToShapefile(ins, out)
