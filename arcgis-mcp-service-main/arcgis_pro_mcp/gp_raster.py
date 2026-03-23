"""Raster analysis tools: surface, density, interpolation, conversion, algebra."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)


def run_slope(
    arcpy: Any,
    in_raster: str,
    out_raster: str,
    output_measurement: str = "DEGREE",
    z_factor: float = 1.0,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    om = output_measurement.strip().upper()
    if om not in ("DEGREE", "PERCENT_RISE"):
        raise RuntimeError("output_measurement 须为 DEGREE 或 PERCENT_RISE")
    arcpy.ddd.Slope(inf, out, om, float(z_factor))


def run_aspect(arcpy: Any, in_raster: str, out_raster: str) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    arcpy.ddd.Aspect(inf, out)


def run_hillshade(
    arcpy: Any,
    in_raster: str,
    out_raster: str,
    azimuth: float = 315.0,
    altitude: float = 45.0,
    z_factor: float = 1.0,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    arcpy.ddd.HillShade(inf, out, float(azimuth), float(altitude), "NO_SHADOWS", float(z_factor))


def run_reclassify(
    arcpy: Any,
    in_raster: str,
    reclass_field: str,
    remap: str,
    out_raster: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    rf = reclass_field.strip()
    if not rf:
        raise RuntimeError("reclass_field 不能为空")
    rm = remap.strip()
    if not rm:
        raise RuntimeError("remap 不能为空（如 \"0 10 1;10 20 2;20 30 3\"）")
    arcpy.sa.Reclassify(inf, rf, arcpy.sa.RemapRange(rm), out)


def run_extract_by_mask(
    arcpy: Any,
    in_raster: str,
    in_mask_data: str,
    out_raster: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    mask = validate_input_path_optional(in_mask_data, "in_mask_data")
    out = validate_gp_output_path(out_raster, "out_raster")
    result = arcpy.sa.ExtractByMask(inf, mask)
    result.save(out)


def run_extract_by_attributes(
    arcpy: Any,
    in_raster: str,
    where_clause: str,
    out_raster: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    wc = where_clause.strip()
    if not wc:
        raise RuntimeError("where_clause 不能为空")
    result = arcpy.sa.ExtractByAttributes(inf, wc)
    result.save(out)


def run_zonal_statistics_as_table(
    arcpy: Any,
    in_zone_data: str,
    zone_field: str,
    in_value_raster: str,
    out_table: str,
    statistics_type: str = "ALL",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    zd = validate_input_path_optional(in_zone_data, "in_zone_data")
    vr = validate_input_path_optional(in_value_raster, "in_value_raster")
    out = validate_gp_output_path(out_table, "out_table")
    zf = zone_field.strip()
    if not zf:
        raise RuntimeError("zone_field 不能为空")
    st = statistics_type.strip().upper()
    arcpy.sa.ZonalStatisticsAsTable(zd, zf, vr, out, "DATA", st)


def run_kernel_density(
    arcpy: Any,
    in_features: str,
    population_field: str,
    out_raster: str,
    cell_size: float | None = None,
    search_radius: float | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_raster, "out_raster")
    pf = population_field.strip()
    if not pf:
        pf = "NONE"
    result = arcpy.sa.KernelDensity(inf, pf, cell_size, search_radius)
    result.save(out)


def run_point_density(
    arcpy: Any,
    in_features: str,
    population_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_raster, "out_raster")
    pf = population_field.strip() or "NONE"
    result = arcpy.sa.PointDensity(inf, pf, cell_size)
    result.save(out)


def run_idw(
    arcpy: Any,
    in_point_features: str,
    z_field: str,
    out_raster: str,
    cell_size: float | None = None,
    power: float = 2.0,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_point_features, "in_point_features")
    out = validate_gp_output_path(out_raster, "out_raster")
    zf = z_field.strip()
    if not zf:
        raise RuntimeError("z_field 不能为空")
    result = arcpy.sa.Idw(inf, zf, cell_size, float(power))
    result.save(out)


def run_kriging(
    arcpy: Any,
    in_point_features: str,
    z_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_point_features, "in_point_features")
    out = validate_gp_output_path(out_raster, "out_raster")
    zf = z_field.strip()
    if not zf:
        raise RuntimeError("z_field 不能为空")
    result = arcpy.sa.Kriging(inf, zf, cell_size=cell_size)
    result.save(out)


def run_topo_to_raster(
    arcpy: Any,
    in_topo_features: str,
    out_raster: str,
    cell_size: float | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_topo_features, "in_topo_features")
    out = validate_gp_output_path(out_raster, "out_raster")
    if cell_size:
        arcpy.ddd.TopoToRaster(inf, out, cell_size)
    else:
        arcpy.ddd.TopoToRaster(inf, out)


def run_raster_to_polygon(
    arcpy: Any,
    in_raster: str,
    out_polygon_features: str,
    simplify: bool = True,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_polygon_features, "out_polygon_features")
    s = "SIMPLIFY" if simplify else "NO_SIMPLIFY"
    arcpy.conversion.RasterToPolygon(inf, out, s)


def run_polygon_to_raster(
    arcpy: Any,
    in_features: str,
    value_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_raster, "out_raster")
    vf = value_field.strip()
    if not vf:
        raise RuntimeError("value_field 不能为空")
    if cell_size:
        arcpy.conversion.PolygonToRaster(inf, vf, out, cellsize=float(cell_size))
    else:
        arcpy.conversion.PolygonToRaster(inf, vf, out)


def run_feature_to_raster(
    arcpy: Any,
    in_features: str,
    field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_raster, "out_raster")
    f = field.strip()
    if not f:
        raise RuntimeError("field 不能为空")
    if cell_size:
        arcpy.conversion.FeatureToRaster(inf, f, out, float(cell_size))
    else:
        arcpy.conversion.FeatureToRaster(inf, f, out)


def run_raster_calculator(
    arcpy: Any,
    expression: str,
    out_raster: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    out = validate_gp_output_path(out_raster, "out_raster")
    expr = expression.strip()
    if not expr:
        raise RuntimeError("expression 不能为空")
    if len(expr) > 8000:
        raise RuntimeError("expression 过长")
    arcpy.ia.RasterCalculator(expr, out)


def run_mosaic_to_new_raster(
    arcpy: Any,
    input_rasters: list[str],
    output_location: str,
    raster_dataset_name: str,
    number_of_bands: int = 1,
    pixel_type: str = "32_BIT_FLOAT",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    if not input_rasters:
        raise RuntimeError("input_rasters 不能为空")
    ins = [validate_input_path_optional(p, f"raster_{i}") for i, p in enumerate(input_rasters)]
    ol = validate_gp_output_path(output_location, "output_location")
    rn = raster_dataset_name.strip()
    if not rn:
        raise RuntimeError("raster_dataset_name 不能为空")
    arcpy.management.MosaicToNewRaster(
        ";".join(ins), ol, rn, number_of_bands=int(number_of_bands), pixel_type=pixel_type
    )


def run_clip_raster(
    arcpy: Any,
    in_raster: str,
    out_raster: str,
    rectangle: str = "",
    in_template_dataset: str = "",
    clipping_geometry: bool = False,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    rect = (rectangle or "").strip()
    tmpl = ""
    if in_template_dataset:
        tmpl = validate_input_path_optional(in_template_dataset, "in_template_dataset")
    cg = "ClippingGeometry" if clipping_geometry else "NONE"
    arcpy.management.Clip(inf, rect or "#", out, tmpl or "#", "#", cg)


def run_resample(
    arcpy: Any,
    in_raster: str,
    out_raster: str,
    cell_size: str,
    resampling_type: str = "NEAREST",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    cs = cell_size.strip()
    if not cs:
        raise RuntimeError("cell_size 不能为空（如 \"10 10\"）")
    rt = resampling_type.strip().upper()
    valid = {"NEAREST", "BILINEAR", "CUBIC", "MAJORITY"}
    if rt not in valid:
        raise RuntimeError(f"resampling_type 须为 {sorted(valid)}")
    arcpy.management.Resample(inf, out, cs, rt)


def run_project_raster(
    arcpy: Any,
    in_raster: str,
    out_raster: str,
    out_wkid: int,
    resampling_type: str = "NEAREST",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    sr = arcpy.SpatialReference(int(out_wkid))
    rt = resampling_type.strip().upper()
    valid = {"NEAREST", "BILINEAR", "CUBIC", "MAJORITY"}
    if rt not in valid:
        raise RuntimeError(f"resampling_type 须为 {sorted(valid)}")
    arcpy.management.ProjectRaster(inf, out, sr, rt)


def run_nibble(
    arcpy: Any,
    in_raster: str,
    in_mask_raster: str,
    out_raster: str,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    mask = validate_input_path_optional(in_mask_raster, "in_mask_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    result = arcpy.sa.Nibble(inf, mask)
    result.save(out)
