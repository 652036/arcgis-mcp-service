"""Raster analysis tools: surface, density, interpolation, conversion, algebra."""

from __future__ import annotations

import ast
import math
import os
import re
from typing import Any

from arcgis_pro_mcp.analysis_quality import finite_number, rectangle_values
from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
    validate_output_name,
)
from arcgis_pro_mcp.raster_runtime import scoped_environment


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
    missing_values: str = "DATA",
    remap_mode: str = "RANGE",
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
    mode = remap_mode.upper()
    if mode not in {"RANGE", "VALUE"}:
        raise RuntimeError("remap_mode must be RANGE or VALUE")
    ranges: list[list[int | float]] = []
    for part in rm.split(";"):
        nums = [p for p in part.replace(",", " ").split() if p]
        if len(nums) != (3 if mode == "RANGE" else 2):
            raise RuntimeError("remap 每段须为 start end new_value，例如 \"0 10 1;10 20 2\"")
        try:
            # ArcPy 3.6 rejects floating-point output classes for integer rasters in
            # some Reclassify workflows.  Preserve integer tokens instead of
            # eagerly converting every value to float.
            ranges.append(
                [
                    int(value) if value.lstrip("+-").isdigit() else float(value)
                    for value in nums
                ]
            )
        except ValueError as e:
            raise RuntimeError("remap 数值无效") from e
    intervals = ranges if mode == "RANGE" else [[v, v, n] for v, n in ranges]
    for start, end, new_value in intervals:
        if not all(math.isfinite(v) for v in (start, end, new_value)) or start > end:
            raise RuntimeError("remap bounds must be finite and increasing")
        if int(new_value) != new_value:
            raise RuntimeError("remap output categories must be integers")
    ordered = sorted(intervals)
    if any(b[0] < a[1] or (a[0] == a[1] == b[0]) for a, b in zip(ordered, ordered[1:], strict=False)):
        raise RuntimeError("remap ranges overlap or values are duplicated")
    mv = missing_values.upper()
    if mv not in {"DATA", "NODATA", "ERROR"}:
        raise RuntimeError("missing_values must be DATA, NODATA or ERROR")
    if mv == "ERROR":
        if rf.casefold() != "value":
            raise RuntimeError("ERROR_RECLASS_FIELD_UNSUPPORTED: missing_values=ERROR requires reclass_field=Value")
        # Full input scan before ArcPy: output class checks alone cannot find
        # unclassified values that accidentally equal an allowed output class.
        from arcgis_pro_mcp.raster_checked import _backend, _raster
        np, _gdal, _ogr, _osr = _backend()
        ds, meta = _raster(inf)
        band = ds.GetRasterBand(1)
        mask = band.GetMaskBand()
        missing = 0
        for row in range(0, ds.RasterYSize, 512):
            for col in range(0, ds.RasterXSize, 512):
                w, h = min(512, ds.RasterXSize-col), min(512, ds.RasterYSize-row)
                data = band.ReadAsArray(col, row, w, h)
                valid = mask.ReadAsArray(col, row, w, h)
                if data is None or valid is None:
                    raise RuntimeError("REMAP_INPUT_READ_FAILED")
                valid = (valid != 0) & np.isfinite(data)
                matched = np.zeros(data.shape, dtype=bool)
                for start, end, _new in ordered:
                    matched |= (data >= start) & (data <= end)
                missing += int((valid & ~matched).sum())
        ds = None
        if missing:
            raise RuntimeError(f"UNMAPPED_INPUT_VALUES: {missing} valid cells have no mapping")
        if meta["scale"] != 1 or meta["offset"] != 0:
            raise RuntimeError("SCALED_RECLASS_INPUT_UNSUPPORTED: declare and materialize the value space first")
    exists = getattr(arcpy, "Exists", None)
    if os.path.lexists(out) or (callable(exists) and exists(out)):
        raise RuntimeError("OUTPUT_ALREADY_EXISTS")
    mapping = arcpy.sa.RemapRange(sorted(ranges)) if mode == "RANGE" else arcpy.sa.RemapValue(ranges)
    # RemapRange assigns a shared endpoint to the lower interval (ArcGIS semantics).
    result = arcpy.sa.Reclassify(inf, rf, mapping, "NODATA" if mv == "ERROR" else mv)
    result.save(out)



def run_extract_by_mask(
    arcpy: Any,
    in_raster: str,
    in_mask_data: str,
    out_raster: str,
    environment: dict[str, Any] | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    mask = validate_input_path_optional(in_mask_data, "in_mask_data")
    out = validate_gp_output_path(out_raster, "out_raster")
    with scoped_environment(arcpy, environment):
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
    ignore_nodata: str = "DATA",
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
    nd = ignore_nodata.upper()
    if nd not in {"DATA", "NODATA"}:
        raise RuntimeError("ignore_nodata 须为 DATA 或 NODATA")
    arcpy.sa.ZonalStatisticsAsTable(zd, zf, vr, out, nd, st)


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
    model = arcpy.sa.KrigingModelOrdinary()
    if cell_size:
        result = arcpy.sa.Kriging(inf, zf, model, cell_size)
    else:
        result = arcpy.sa.Kriging(inf, zf, model)
    result.save(out)


def run_topo_to_raster(
    arcpy: Any,
    in_topo_features: str,
    out_raster: str,
    cell_size: float | None = None,
    elevation_field: str = "VALUE",
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_topo_features, "in_topo_features")
    out = validate_gp_output_path(out_raster, "out_raster")
    field = elevation_field.strip()
    if not field:
        raise RuntimeError("elevation_field 不能为空")
    topo_input = arcpy.sa.TopoPointElevation([[inf, field]])
    if cell_size:
        result = arcpy.sa.TopoToRaster([topo_input], float(cell_size), data_type="SPOT")
    else:
        result = arcpy.sa.TopoToRaster([topo_input], data_type="SPOT")
    result.save(out)


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
    input_rasters: dict[str, str] | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    out = validate_gp_output_path(out_raster, "out_raster")
    expr = expression.strip()
    if not expr:
        raise RuntimeError("expression 不能为空")
    if len(expr) > 8000:
        raise RuntimeError("expression 过长")
    bindings = input_rasters or {}
    if not isinstance(bindings, dict) or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", k) for k in bindings):
        raise RuntimeError("input_rasters 须为变量名到栅格路径的映射")
    inputs = {k: validate_input_path_optional(v, k) for k, v in bindings.items()}
    functions = {"Con", "IsNull", "Abs", "Int", "Float", "Square", "SquareRoot", "Exp", "Ln", "Log10", "Sin", "Cos", "Tan"}
    if set(inputs) & functions:
        raise RuntimeError("变量名不能与函数名称冲突")
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise RuntimeError("expression 语法无效") from exc
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Call, ast.Name, ast.Load,
               ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
               ast.USub, ast.UAdd, ast.Invert, ast.BitAnd, ast.BitOr,
               ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise RuntimeError("expression 含不支持的语法；不允许任意代码、属性访问或索引")
        if isinstance(node, ast.Constant):
            finite_number(node.value, "expression constant")
        if isinstance(node, ast.Name) and node.id not in inputs and node.id not in functions:
            raise RuntimeError("expression 只能引用显式绑定的变量和受控函数")
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in functions or node.keywords):
            raise RuntimeError("expression 含不支持的函数调用")
    if callable(getattr(arcpy, "Exists", None)) and arcpy.Exists(out) or os.path.lexists(out):
        raise RuntimeError("out_raster 已存在；拒绝隐式覆盖")
    if inputs:
        ia = getattr(arcpy, "ia", None)
        calculator = getattr(ia, "RasterCalculator", None)
        if not callable(calculator):
            raise RuntimeError("当前版本不支持对象式 IA RasterCalculator；不会猜测备用签名")
        result = calculator(list(inputs.values()), list(inputs), expr)
        result.save(out)
        return
    gp = getattr(arcpy, "gp", None)
    if gp is not None and hasattr(gp, "RasterCalculator_sa"):
        gp.RasterCalculator_sa(expr, out)
        return
    raise RuntimeError("请显式提供 input_rasters 变量绑定；当前无受支持的旧式 GP 接口")


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
    rn = validate_output_name(raster_dataset_name, "raster_dataset_name")
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
    environment: dict[str, Any] | None = None,
) -> None:
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_raster, "in_raster")
    out = validate_gp_output_path(out_raster, "out_raster")
    rect = (rectangle or "").strip()
    if rect:
        try:
            rectangle_values([float(x) for x in rect.split()])
        except ValueError as exc:
            raise RuntimeError("rectangle 须为四个有限数值") from exc
    tmpl = ""
    if in_template_dataset:
        tmpl = validate_input_path_optional(in_template_dataset, "in_template_dataset")
    if clipping_geometry and not tmpl:
        raise RuntimeError("多边形裁剪需要 in_template_dataset")
    cg = "ClippingGeometry" if clipping_geometry else "NONE"
    with scoped_environment(arcpy, environment):
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
