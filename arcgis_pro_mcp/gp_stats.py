"""Spatial statistics, regression, and sampling tools for research workflows.

Wraps the ArcGIS Pro Spatial Statistics toolbox (``arcpy.stats``) plus a few
``arcpy.management`` sampling helpers. These tools are licensed with the core
ArcGIS Pro Standard/Advanced license and do not require a separate extension
checkout, but heavier models (GWR, Forest) are version sensitive and are
wrapped to surface a friendly message on older Pro builds.

Diagnostic tools whose primary output is a statistic (Global Moran's I,
Average Nearest Neighbor, OLS, GWR, Ripley's K) return the geoprocessing
messages so the indices/diagnostics can be captured for reporting.
"""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp import gp_generic
from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)

_MAX_FIELD = 128

_CONCEPTUALIZATIONS = frozenset(
    {
        "INVERSE_DISTANCE",
        "INVERSE_DISTANCE_SQUARED",
        "FIXED_DISTANCE_BAND",
        "ZONE_OF_INDIFFERENCE",
        "CONTIGUITY_EDGES_ONLY",
        "CONTIGUITY_EDGES_CORNERS",
        "GET_SPATIAL_WEIGHTS_FROM_FILE",
    },
)
_DISTANCE_METHODS = frozenset({"EUCLIDEAN_DISTANCE", "MANHATTAN_DISTANCE"})
_STANDARDIZATIONS = frozenset({"NONE", "ROW"})


def _field(name: str, label: str) -> str:
    f = (name or "").strip()
    if not f:
        raise RuntimeError(f"{label} 不能为空")
    if len(f) > _MAX_FIELD:
        raise RuntimeError(f"{label} 过长")
    if any(ch in f for ch in (";", "\r", "\n")):
        raise RuntimeError(f"{label} 含非法字符")
    return f


def _field_list(fields: list[str], label: str) -> str:
    if not fields:
        raise RuntimeError(f"{label} 不能为空")
    cleaned = [_field(f, label) for f in fields]
    return ";".join(cleaned)


def _check_conceptualization(value: str) -> str:
    v = (value or "").strip().upper()
    if v not in _CONCEPTUALIZATIONS:
        raise RuntimeError(f"conceptualization 须为 {sorted(_CONCEPTUALIZATIONS)}")
    return v


def _check_distance_method(value: str) -> str:
    v = (value or "").strip().upper()
    if v not in _DISTANCE_METHODS:
        raise RuntimeError(f"distance_method 须为 {sorted(_DISTANCE_METHODS)}")
    return v


def _bool_flag(value: bool, true_token: str, false_token: str) -> str:
    return true_token if value else false_token


# ---------------------------------------------------------------------------
# Batch 1: Spatial statistics core
# ---------------------------------------------------------------------------


def run_hot_spots(
    arcpy: Any,
    in_features: str,
    input_field: str,
    out_feature_class: str,
    conceptualization: str = "FIXED_DISTANCE_BAND",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "NONE",
    distance_band: float | None = None,
    apply_fdr: bool = False,
) -> list[str]:
    """Getis-Ord Gi* hot spot analysis -> output feature class with z/p/Gi_Bin."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    fld = _field(input_field, "input_field")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    con = _check_conceptualization(conceptualization)
    dm = _check_distance_method(distance_method)
    std = (standardization or "NONE").strip().upper()
    if std not in _STANDARDIZATIONS:
        raise RuntimeError(f"standardization 须为 {sorted(_STANDARDIZATIONS)}")
    db = float(distance_band) if distance_band else None
    fdr = _bool_flag(apply_fdr, "APPLY_FDR", "NO_FDR")
    arcpy.stats.HotSpots(inf, fld, out, con, dm, std, db, None, None, fdr)
    return gp_generic.get_messages(arcpy)


def run_optimized_hot_spots(
    arcpy: Any,
    in_features: str,
    out_features: str,
    analysis_field: str = "",
    aggregation_method: str = "",
    cell_size: float | None = None,
    distance_band: float | None = None,
) -> list[str]:
    """Optimized Hot Spot Analysis with automatic parameter selection."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_features, "out_features")
    af = (analysis_field or "").strip()
    if af:
        af = _field(af, "analysis_field")
    agg = (aggregation_method or "").strip().upper() or None
    cs = float(cell_size) if cell_size else None
    db = float(distance_band) if distance_band else None
    arcpy.stats.OptimizedHotSpotAnalysis(
        inf, out, af or None, agg, None, None, None, cs, db,
    )
    return gp_generic.get_messages(arcpy)


def run_cluster_outlier(
    arcpy: Any,
    in_features: str,
    input_field: str,
    out_feature_class: str,
    conceptualization: str = "FIXED_DISTANCE_BAND",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "NONE",
    distance_band: float | None = None,
    apply_fdr: bool = False,
    number_of_permutations: int | None = None,
) -> list[str]:
    """Anselin Local Moran's I cluster and outlier analysis (COType: HH/LL/HL/LH)."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    fld = _field(input_field, "input_field")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    con = _check_conceptualization(conceptualization)
    dm = _check_distance_method(distance_method)
    std = (standardization or "NONE").strip().upper()
    if std not in _STANDARDIZATIONS:
        raise RuntimeError(f"standardization 须为 {sorted(_STANDARDIZATIONS)}")
    db = float(distance_band) if distance_band else None
    fdr = _bool_flag(apply_fdr, "APPLY_FDR", "NO_FDR")
    perms = int(number_of_permutations) if number_of_permutations else None
    arcpy.stats.ClustersOutliers(inf, fld, out, con, dm, std, db, None, fdr, perms)
    return gp_generic.get_messages(arcpy)


def run_spatial_autocorrelation(
    arcpy: Any,
    in_features: str,
    input_field: str,
    conceptualization: str = "INVERSE_DISTANCE",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "ROW",
    distance_band: float | None = None,
    generate_report: bool = False,
) -> list[str]:
    """Global Moran's I spatial autocorrelation. Read-only; returns the index,
    z-score and p-value via geoprocessing messages."""
    inf = validate_input_path_optional(in_features, "in_features")
    fld = _field(input_field, "input_field")
    con = _check_conceptualization(conceptualization)
    dm = _check_distance_method(distance_method)
    std = (standardization or "ROW").strip().upper()
    if std not in _STANDARDIZATIONS:
        raise RuntimeError(f"standardization 须为 {sorted(_STANDARDIZATIONS)}")
    db = float(distance_band) if distance_band else None
    report = _bool_flag(generate_report, "GENERATE_REPORT", "NO_REPORT")
    arcpy.stats.SpatialAutocorrelation(inf, fld, report, con, dm, std, db)
    return gp_generic.get_messages(arcpy)


def run_average_nearest_neighbor(
    arcpy: Any,
    in_features: str,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    generate_report: bool = False,
    area: float | None = None,
) -> list[str]:
    """Average Nearest Neighbor. Read-only; returns the NN ratio, z-score and
    p-value via geoprocessing messages."""
    inf = validate_input_path_optional(in_features, "in_features")
    dm = _check_distance_method(distance_method)
    report = _bool_flag(generate_report, "GENERATE_REPORT", "NO_REPORT")
    ar = float(area) if area else None
    arcpy.stats.AverageNearestNeighbor(inf, dm, report, ar)
    return gp_generic.get_messages(arcpy)


def run_multi_distance_spatial_clustering(
    arcpy: Any,
    in_features: str,
    out_table: str,
    number_of_distance_bands: int,
    compute_confidence_envelope: str = "0_PERMUTATIONS_-_NO_CONFIDENCE_ENVELOPE",
    weight_field: str = "",
    beginning_distance: float | None = None,
    distance_increment: float | None = None,
) -> list[str]:
    """Ripley's K-function multi-distance spatial cluster analysis -> output table."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_table, "out_table")
    try:
        bands = int(number_of_distance_bands)
    except (TypeError, ValueError) as e:
        raise RuntimeError("number_of_distance_bands 必须为整数") from e
    if bands < 1:
        raise RuntimeError("number_of_distance_bands 必须 >= 1")
    env = (compute_confidence_envelope or "0_PERMUTATIONS_-_NO_CONFIDENCE_ENVELOPE").strip().upper()
    if env in {"0_PERMUTATIONS", "0"}:
        env = "0_PERMUTATIONS_-_NO_CONFIDENCE_ENVELOPE"
    valid_env = {
        "0_PERMUTATIONS_-_NO_CONFIDENCE_ENVELOPE",
        "9_PERMUTATIONS",
        "99_PERMUTATIONS",
        "999_PERMUTATIONS",
    }
    if env not in valid_env:
        raise RuntimeError(f"compute_confidence_envelope 须为 {sorted(valid_env)}")
    wf = (weight_field or "").strip()
    if wf:
        wf = _field(wf, "weight_field")
    bd = float(beginning_distance) if beginning_distance else None
    di = float(distance_increment) if distance_increment else None
    arcpy.stats.MultiDistanceSpatialClustering(
        inf, out, bands, env, "NO_DISPLAY", wf or None, bd, di,
    )
    return gp_generic.get_messages(arcpy)


# ---------------------------------------------------------------------------
# Batch 2: Regression / modeling
# ---------------------------------------------------------------------------


def run_ordinary_least_squares(
    arcpy: Any,
    in_features: str,
    unique_id_field: str,
    out_feature_class: str,
    dependent_variable: str,
    explanatory_variables: list[str],
    coefficient_output_table: str = "",
    diagnostic_output_table: str = "",
) -> list[str]:
    """OLS global linear regression. Returns R^2/AIC and diagnostics via messages."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    uid = _field(unique_id_field, "unique_id_field")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    dep = _field(dependent_variable, "dependent_variable")
    expl = _field_list(explanatory_variables, "explanatory_variables")
    coef = validate_gp_output_path(coefficient_output_table, "coefficient_output_table") if coefficient_output_table.strip() else None
    diag = validate_gp_output_path(diagnostic_output_table, "diagnostic_output_table") if diagnostic_output_table.strip() else None
    arcpy.stats.OrdinaryLeastSquares(inf, uid, out, dep, expl, coef, diag)
    return gp_generic.get_messages(arcpy)


def run_gwr(
    arcpy: Any,
    in_features: str,
    dependent_variable: str,
    explanatory_variables: list[str],
    out_features: str,
    model_type: str = "CONTINUOUS",
    neighborhood_type: str = "NUMBER_OF_NEIGHBORS",
    neighborhood_selection_method: str = "GOLDEN_SEARCH",
    number_of_neighbors: int | None = None,
    distance_band: float | None = None,
) -> list[str]:
    """Geographically Weighted Regression (modern arcpy.stats.GWR)."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    dep = _field(dependent_variable, "dependent_variable")
    expl = _field_list(explanatory_variables, "explanatory_variables")
    out = validate_gp_output_path(out_features, "out_features")
    mt = (model_type or "CONTINUOUS").strip().upper()
    valid_mt = {"CONTINUOUS", "BINARY", "COUNT"}
    if mt not in valid_mt:
        raise RuntimeError(f"model_type 须为 {sorted(valid_mt)}")
    nt = (neighborhood_type or "NUMBER_OF_NEIGHBORS").strip().upper()
    valid_nt = {"NUMBER_OF_NEIGHBORS", "DISTANCE_BAND"}
    if nt not in valid_nt:
        raise RuntimeError(f"neighborhood_type 须为 {sorted(valid_nt)}")
    nsm = (neighborhood_selection_method or "GOLDEN_SEARCH").strip().upper()
    valid_nsm = {"GOLDEN_SEARCH", "MANUAL_INTERVALS", "USER_DEFINED"}
    if nsm not in valid_nsm:
        raise RuntimeError(f"neighborhood_selection_method 须为 {sorted(valid_nsm)}")
    kwargs: dict[str, Any] = {
        "neighborhood_type": nt,
        "neighborhood_selection_method": nsm,
    }
    if nsm == "USER_DEFINED":
        if nt == "NUMBER_OF_NEIGHBORS":
            if not number_of_neighbors:
                raise RuntimeError("USER_DEFINED + NUMBER_OF_NEIGHBORS 须提供 number_of_neighbors")
            kwargs["number_of_neighbors"] = int(number_of_neighbors)
        else:
            if not distance_band:
                raise RuntimeError("USER_DEFINED + DISTANCE_BAND 须提供 distance_band")
            kwargs["distance_band"] = float(distance_band)
    gwr = getattr(arcpy.stats, "GWR", None)
    if gwr is None:
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 arcpy.stats.GWR（需 Pro 2.3+）")
    gwr(inf, dep, mt, expl, out, **kwargs)
    return gp_generic.get_messages(arcpy)


def run_forest(
    arcpy: Any,
    in_features: str,
    variable_predict: str,
    explanatory_variables: list[str],
    prediction_type: str = "TRAIN",
    explanatory_variables_categorical: list[str] | None = None,
    treat_variable_as_categorical: bool = False,
    number_of_trees: int = 100,
    output_trained_features: str = "",
) -> list[str]:
    """Forest-based (random forest) classification and regression, TRAIN mode."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    pred = (prediction_type or "TRAIN").strip().upper()
    if pred != "TRAIN":
        raise RuntimeError("当前仅支持 prediction_type=TRAIN")
    var = _field(variable_predict, "variable_predict")
    continuous = [_field(f, "explanatory_variables") for f in (explanatory_variables or [])]
    categorical = [_field(f, "explanatory_variables_categorical") for f in (explanatory_variables_categorical or [])]
    if not continuous and not categorical:
        raise RuntimeError("explanatory_variables 不能为空")
    expl_value_table = [[f, "false"] for f in continuous] + [[f, "true"] for f in categorical]
    tvc = _bool_flag(treat_variable_as_categorical, "true", "false")
    try:
        ntrees = max(1, min(int(number_of_trees), 10000))
    except (TypeError, ValueError) as e:
        raise RuntimeError("number_of_trees 必须为整数") from e
    out_trained = (
        validate_gp_output_path(output_trained_features, "output_trained_features")
        if output_trained_features.strip()
        else None
    )
    forest = getattr(arcpy.stats, "Forest", None)
    if forest is None:
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 arcpy.stats.Forest（需 Pro 2.2+）")
    forest(
        pred,
        inf,
        var,
        tvc,
        expl_value_table,
        None,
        None,
        None,
        None,
        None,
        output_trained_features=out_trained,
        number_of_trees=ntrees,
    )
    return gp_generic.get_messages(arcpy)


# ---------------------------------------------------------------------------
# Batch 3: Distribution description + sampling
# ---------------------------------------------------------------------------


def run_central_feature(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    weight_field: str = "",
    case_field: str = "",
) -> None:
    """Central Feature: the most centrally located feature."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    dm = _check_distance_method(distance_method)
    wf = _field(weight_field, "weight_field") if weight_field.strip() else None
    cf = _field(case_field, "case_field") if case_field.strip() else None
    arcpy.stats.CentralFeature(inf, out, dm, wf, None, cf)


def run_mean_center(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    weight_field: str = "",
    case_field: str = "",
) -> None:
    """Mean Center: the geographic center (mean of x/y) of features."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    wf = _field(weight_field, "weight_field") if weight_field.strip() else None
    cf = _field(case_field, "case_field") if case_field.strip() else None
    arcpy.stats.MeanCenter(inf, out, wf, cf, None)


def run_directional_distribution(
    arcpy: Any,
    in_features: str,
    out_feature_class: str,
    ellipse_size: str = "1_STANDARD_DEVIATION",
    weight_field: str = "",
    case_field: str = "",
) -> None:
    """Directional Distribution: standard deviational ellipse of features."""
    require_allow_write()
    require_gp_output_root_mandatory()
    inf = validate_input_path_optional(in_features, "in_features")
    out = validate_gp_output_path(out_feature_class, "out_feature_class")
    es = (ellipse_size or "1_STANDARD_DEVIATION").strip().upper()
    valid_es = {"1_STANDARD_DEVIATION", "2_STANDARD_DEVIATIONS", "3_STANDARD_DEVIATIONS"}
    if es not in valid_es:
        raise RuntimeError(f"ellipse_size 须为 {sorted(valid_es)}")
    wf = _field(weight_field, "weight_field") if weight_field.strip() else None
    cf = _field(case_field, "case_field") if case_field.strip() else None
    arcpy.stats.DirectionalDistribution(inf, out, es, wf, cf)


def run_create_random_points(
    arcpy: Any,
    out_path: str,
    out_name: str,
    number_of_points: int,
    constraining_feature_class: str = "",
    minimum_allowed_distance: str = "",
) -> str:
    """Create random sampling points within an optional constraining polygon."""
    require_allow_write()
    require_gp_output_root_mandatory()
    op = validate_gp_output_path(out_path, "out_path")
    name = _field(out_name, "out_name")
    try:
        npts = int(number_of_points)
    except (TypeError, ValueError) as e:
        raise RuntimeError("number_of_points 必须为整数") from e
    if npts < 1:
        raise RuntimeError("number_of_points 必须 >= 1")
    constraint = (
        validate_input_path_optional(constraining_feature_class, "constraining_feature_class")
        if constraining_feature_class.strip()
        else ""
    )
    mad = (minimum_allowed_distance or "").strip()
    result = arcpy.management.CreateRandomPoints(
        op, name, constraint or "", "", npts, mad or "0", "POINT", "0",
    )
    try:
        return str(result.getOutput(0))
    except Exception:  # noqa: BLE001
        return f"{op}\\{name}"


def run_generate_tessellation(
    arcpy: Any,
    output_feature_class: str,
    extent: str,
    shape_type: str = "HEXAGON",
    size: str = "",
    spatial_reference_wkid: int | None = None,
) -> str:
    """Generate a tessellated grid (hexbins/squares/etc.) for spatial binning."""
    require_allow_write()
    require_gp_output_root_mandatory()
    out = validate_gp_output_path(output_feature_class, "output_feature_class")
    ext = (extent or "").strip()
    if not ext:
        raise RuntimeError("extent 不能为空（如 \"xmin ymin xmax ymax\" 或数据集路径）")
    st = (shape_type or "HEXAGON").strip().upper()
    valid_st = {"HEXAGON", "SQUARE", "DIAMOND", "TRIANGLE", "TRANSVERSE_HEXAGON"}
    if st not in valid_st:
        raise RuntimeError(f"shape_type 须为 {sorted(valid_st)}")
    sz = (size or "").strip()
    sr = arcpy.SpatialReference(int(spatial_reference_wkid)) if spatial_reference_wkid else None
    tess = getattr(arcpy.management, "GenerateTessellation", None)
    if tess is None:
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 GenerateTessellation（需 Pro 2.1+）")
    if sz:
        tess(out, ext, st, sz, sr)
    else:
        tess(out, ext, st, spatial_reference=sr)
    return out
