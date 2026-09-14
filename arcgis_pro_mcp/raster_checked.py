"""Versioned, file-mode clipping with independent full-domain raster QA.

First complete adapter: local north-up, single-band GeoTIFF + polygon files.
Unsupported formats, selected GUI layers, reprojection and rotated grids fail
closed. Legacy tools remain explicitly unqualified; this is not a global claim
that every GIS model or every MCP tool has a scientific validation adapter.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from arcgis_pro_mcp import analysis_quality as qa
from arcgis_pro_mcp import private_state
from arcgis_pro_mcp.paths import (
    path_under_root,
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)
from arcgis_pro_mcp.redaction import redact_sensitive, safe_error

_LOCK = threading.RLock()
_REQUIRED = {"grid", "coverage", "outside_aoi", "values_preserved", "input_versions", "environment_restored"}
_ASSET_KEYS = {"asset_id", "path", "role", "identity_basis", "vector_layer", "fingerprint", "files", "metadata"}
_ASSET_REQUIRED = {"asset_id", "path", "role", "identity_basis", "fingerprint"}


def _backend():
    try:
        import numpy as np
        from osgeo import gdal, ogr, osr
    except ImportError as exc:
        raise RuntimeError("Checked raster adapter requires NumPy and GDAL in the executing ArcPy environment") from exc
    return np, gdal, ogr, osr


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _file_version(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Fingerprint the dataset family, including sidecars; primary rename is neutral."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("ASSET_NOT_LOCAL_FILE")
    ext = path.suffix.lower()
    if ext not in {".tif", ".tiff", ".shp", ".gpkg", ".geojson"}:
        raise RuntimeError("UNSUPPORTED_ASSET_FORMAT: use a controlled file snapshot")
    if ext == ".gpkg" and any(Path(str(path) + x).exists() for x in ("-wal", "-journal")):
        raise RuntimeError("ACTIVE_GEOPACKAGE: create a supported consistent snapshot first")
    files = {"main": path}
    if ext == ".shp":
        for suffix in (".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".qix", ".shp.xml"):
            p = path.with_suffix(suffix)
            if p.exists():
                files[suffix] = p
        if not {".shx", ".dbf"}.issubset(files):
            raise RuntimeError("INCOMPLETE_SHAPEFILE")
    elif ext in {".tif", ".tiff"}:
        for suffix in (".aux.xml", ".ovr", ".msk", ".xml"):
            p = Path(str(path) + suffix)
            if p.exists():
                files[suffix] = p
        for suffix in (".tfw", ".tfwx", ".wld", ".prj"):
            p = path.with_suffix(suffix)
            if p.exists():
                files[suffix] = p
    records = []
    for key, p in sorted(files.items()):
        if p.is_symlink() or not path_under_root(str(p), str(path.parent)):
            raise RuntimeError("UNSAFE_ASSET_SIDECAR")
        before = p.stat()
        digest = _hash(p)
        after = p.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("ASSET_CHANGED_DURING_HASH")
        records.append({"component": key, "name": p.name, "size": after.st_size, "sha256": digest})
    identity = [{k: row[k] for k in ("component", "size", "sha256")} for row in records]
    return _digest(identity), records


def _srs(text: str):
    _np, _gdal, _ogr, osr = _backend()
    if not text or not text.strip():
        raise RuntimeError("UNKNOWN_CRS")
    s = osr.SpatialReference()
    if s.ImportFromWkt(text) != 0 or not (s.IsProjected() or s.IsGeographic()):
        raise RuntimeError("INVALID_OR_UNSUPPORTED_CRS")
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


def _same_crs(a: str, b: str) -> None:
    if not _srs(a).IsSame(_srs(b)):
        raise RuntimeError("CRS_MISMATCH: an explicit projection step is required")


def _raster(path: str):
    _np, gdal, _ogr, _osr = _backend()
    ds = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY)
    if ds is None:
        raise RuntimeError("RASTER_UNREADABLE")
    if ds.GetDriver().ShortName != "GTiff" or ds.RasterCount != 1:
        raise RuntimeError("UNSUPPORTED_RASTER: checked clipping currently supports single-band GeoTIFF")
    if gdal.GetDataTypeName(ds.GetRasterBand(1).DataType) not in {
        "Byte", "Int8", "UInt16", "Int16", "UInt32", "Int32", "Float32", "Float64"
    }:
        raise RuntimeError("UNSUPPORTED_PIXEL_TYPE: complex and 64-bit integer samples require a dedicated adapter")
    gt = ds.GetGeoTransform(can_return_null=True)
    if gt is None or any(not math.isfinite(v) for v in gt):
        raise RuntimeError("UNKNOWN_GRID")
    if gt[1] <= 0 or gt[5] >= 0 or gt[2] != 0 or gt[4] != 0:
        raise RuntimeError("UNSUPPORTED_ROTATED_OR_NON_NORTH_UP_GRID")
    wkt = ds.GetProjectionRef()
    s = _srs(wkt)
    family = {str(Path(path).resolve())}
    for suffix in (".aux.xml", ".ovr", ".msk", ".xml"):
        family.add(str(Path(path + suffix).resolve()))
    for suffix in (".tfw", ".tfwx", ".wld", ".prj"):
        family.add(str(Path(path).with_suffix(suffix).resolve()))
    if any(str(Path(p).resolve()) not in family for p in ds.GetFileList() or []):
        raise RuntimeError("UNTRACKED_RASTER_COMPONENT: materialize a self-contained GeoTIFF first")
    band = ds.GetRasterBand(1)
    for label, v in (("scale", band.GetScale()), ("offset", band.GetOffset())):
        if v is not None:
            qa.finite_number(v, label)
    nodata = band.GetNoDataValue()
    grid = {
        "x0": gt[0],
        "y0": gt[3],
        "dx": gt[1],
        "dy": -gt[5],
        "cols": ds.RasterXSize,
        "rows": ds.RasterYSize,
        "crs_wkt": wkt,
    }
    metadata = {
        "grid": grid,
        "band_count": 1,
        "pixel_type": gdal.GetDataTypeName(band.DataType),
        "scale": band.GetScale() if band.GetScale() is not None else 1,
        "offset": band.GetOffset() if band.GetOffset() is not None else 0,
        "nodata": str(nodata) if nodata is not None and not math.isfinite(nodata) else nodata,
        "unit": band.GetUnitType() or "UNKNOWN",
        "crs_name": s.GetName(),
        "validity": "GDAL mask AND finite values; real zero remains valid",
    }
    return ds, metadata


def _vector(path: str, name: str):
    _np, gdal, ogr, _osr = _backend()
    ds = gdal.OpenEx(path, gdal.OF_VECTOR | gdal.OF_READONLY)
    if ds is None:
        raise RuntimeError("BOUNDARY_UNREADABLE")
    if not name and ds.GetLayerCount() != 1:
        raise RuntimeError("VECTOR_LAYER_REQUIRED")
    layer = ds.GetLayerByName(name) if name else ds.GetLayer(0)
    if layer is None:
        raise RuntimeError("VECTOR_LAYER_NOT_FOUND")
    sr = layer.GetSpatialRef()
    if sr is None:
        raise RuntimeError("UNKNOWN_CRS")
    _srs(sr.ExportToWkt())
    count, parts, rings = 0, 0, 0
    ids = hashlib.sha256()
    layer.ResetReading()
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None or geom.IsEmpty() or not geom.IsValid():
            raise RuntimeError("INVALID_OR_EMPTY_BOUNDARY_GEOMETRY")
        kind = ogr.GT_Flatten(geom.GetGeometryType())
        if kind not in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
            raise RuntimeError("POLYGON_BOUNDARY_REQUIRED")
        polys = [geom] if kind == ogr.wkbPolygon else [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]
        parts += len(polys)
        rings += sum(max(0, p.GetGeometryCount() - 1) for p in polys)
        count += 1
        ids.update(str(feat.GetFID()).encode() + b"\0")
    if not count:
        raise RuntimeError("EMPTY_BOUNDARY")
    layer.ResetReading()
    ex = layer.GetExtent()
    return (
        ds,
        layer,
        {
            "crs_wkt": sr.ExportToWkt(),
            "feature_count": count,
            "parts": parts,
            "holes": rings,
            "selection_mode": "ALL_RECORDS",
            "definition_query": None,
            "fid_digest": ids.hexdigest(),
            "bounds": [ex[0], ex[2], ex[1], ex[3]],
            "layer_name": layer.GetName(),
        },
    )


def asset_info(dataset_path: str, role: str, identity_basis: str, vector_layer: str = "") -> dict[str, Any]:
    """Inspect physical file data, not GUI layers; no implicit selection or cache writes."""
    if role not in {"CONTINUOUS", "CATEGORICAL", "GRID_REFERENCE", "REPORTING_AOI"}:
        raise RuntimeError("UNKNOWN_ASSET_ROLE")
    if not isinstance(identity_basis, str) or not identity_basis.strip() or len(identity_basis) > 1000:
        raise RuntimeError("IDENTITY_BASIS_REQUIRED: declare intended dataset or business boundary identity")
    path = validate_input_path_optional(dataset_path, "dataset_path")
    if not isinstance(path, str):
        raise RuntimeError("PHYSICAL_SNAPSHOT_REQUIRED: GUI selection references are unsupported here")
    version, files = _file_version(Path(path))
    if role == "REPORTING_AOI":
        _ds, _layer, metadata = _vector(path, vector_layer)
    else:
        _ds, metadata = _raster(path)
    _ds = None
    after, _files = _file_version(Path(path))
    if after != version:
        raise RuntimeError("INPUT_CHANGED_DURING_INSPECTION")
    return {
        "asset_id": "sha256:" + version,
        "path": path,
        "role": role,
        "identity_basis": identity_basis,
        "vector_layer": vector_layer,
        "fingerprint": version,
        "files": files,
        "metadata": metadata,
    }


def _verify_asset(asset: dict[str, Any], roles: set[str]) -> dict[str, Any]:
    if not isinstance(asset, dict) or set(asset) - _ASSET_KEYS:
        raise RuntimeError("INVALID_ASSET_CONTRACT")
    missing = _ASSET_REQUIRED - set(asset)
    if missing:
        raise RuntimeError(f"INVALID_ASSET_CONTRACT: missing required fields: {', '.join(sorted(missing))}")
    if any(not isinstance(asset[key], str) or not asset[key].strip() for key in _ASSET_REQUIRED):
        raise RuntimeError("INVALID_ASSET_CONTRACT: required fields must be nonempty strings")
    if not isinstance(asset.get("vector_layer", ""), str):
        raise RuntimeError("INVALID_ASSET_CONTRACT: vector_layer must be a string")
    if asset.get("role") not in roles:
        raise RuntimeError("ASSET_ROLE_MISMATCH")
    fresh = asset_info(asset["path"], asset["role"], asset["identity_basis"], asset.get("vector_layer", ""))
    if fresh["asset_id"] != asset.get("asset_id") or fresh["fingerprint"] != asset.get("fingerprint"):
        raise RuntimeError("ASSET_VERSION_MISMATCH")
    # Metadata submitted by a caller is never authoritative.
    _require_known_output_eligible(fresh)
    return fresh


def _require_known_output_eligible(asset: dict[str, Any]) -> None:
    """Renaming/copying a known quarantined output cannot turn it into a raw input."""
    root = Path(require_gp_output_root_mandatory()) / "_analysis_runs"
    main_hash = next(item["sha256"] for item in asset["files"] if item["component"] == "main")
    for path in root.glob("*/report.json"):
        report = private_state.read_private_json(path, max_bytes=1_048_576)
        if not report or not report.get("output_raster"):
            continue
        candidate = Path(report["output_raster"])
        known_hash = report.get("output_main_sha256")
        if not known_hash and candidate.is_file():
            known_hash = _hash(candidate)
        if known_hash == main_hash and not result_status(report["run_id"])["eligible_for_downstream"]:
            raise RuntimeError("INELIGIBLE_UPSTREAM_ASSET: known diagnostic output requires a new qualified run")


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str):
        raise RuntimeError("INVALID_RUN_ID: run_id must be a canonical lower-case UUID")
    try:
        parsed = str(uuid.UUID(run_id))
    except ValueError as exc:
        raise RuntimeError("INVALID_RUN_ID: run_id must be a canonical lower-case UUID") from exc
    if parsed != run_id:
        raise RuntimeError("INVALID_RUN_ID: run_id must be a canonical lower-case UUID")


def _run_dir(run_id: str) -> Path:
    _validate_run_id(run_id)
    root = Path(require_gp_output_root_mandatory())
    return Path(
        validate_gp_output_path(
            str(root / "_analysis_runs" / run_id / "report.json"), "run_report", create_parent=False
        )
    ).parent


def _store(path: Path, report: dict[str, Any]) -> None:
    private_state.write_private_json(path, report, temp_tag=uuid.uuid4().hex, max_bytes=1_048_576)


def result_status(run_id: str) -> dict[str, Any]:
    """Read authoritative evidence and invalidate it when an asset version changes."""
    # No directory creation on the read path.
    _validate_run_id(run_id)
    root = Path(require_gp_output_root_mandatory())
    path = root / "_analysis_runs" / run_id / "report.json"
    if not path_under_root(str(path), str(root)):
        raise RuntimeError("INVALID_RUN_PATH")
    report = private_state.read_private_json(path, max_bytes=1_048_576)
    if not report:
        raise RuntimeError("RUN_NOT_FOUND_OR_UNTRUSTED")
    if not isinstance(report, dict) or report.get("run_id") != run_id:
        raise RuntimeError("INVALID_RUN_REPORT: run_id does not match the requested run")
    checks = report.get("checks", [])
    if not isinstance(checks, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("name"), str)
        or not isinstance(item.get("status"), str)
        for item in checks
    ):
        raise RuntimeError("INVALID_RUN_REPORT: checks must contain named status records")
    evidence = list(checks)
    status = report.get("execution_status", "UNKNOWN")
    if status == "RUNNING":
        # A synchronous request may have disconnected or its worker may have died.
        # This adapter does not claim to know process liveness from an old file.
        status = "UNKNOWN"
    if report.get("output_fingerprint"):
        try:
            actual, _files = _file_version(Path(report["output_raster"]))
            unchanged = actual == report["output_fingerprint"]
            for asset in report.get("assets", {}).values():
                if _file_version(Path(asset["path"]))[0] != asset["fingerprint"]:
                    unchanged = False
        except (OSError, RuntimeError, KeyError, TypeError, AttributeError):
            unchanged = False
        evidence.append(qa.check("current_asset_versions", unchanged))
    elif status == "SUCCEEDED":
        evidence.append(qa.check("current_asset_versions", False, reason="missing output fingerprint"))
    result = {
        k: report.get(k) for k in ("run_id", "output_raster", "diagnostic_raster", "error", "coverage", "plan_digest")
    }
    result.update(qa.assess(status, evidence, _REQUIRED))
    result["checks"] = evidence
    return redact_sensitive(result)


def _snapshot(asset: dict[str, Any], folder: Path) -> str:
    folder.mkdir()
    for item in asset["files"]:
        shutil.copy2(Path(asset["path"]).parent / item["name"], folder / item["name"])
    path = folder / Path(asset["path"]).name
    if _file_version(path)[0] != asset["fingerprint"]:
        raise RuntimeError("SNAPSHOT_VERSION_MISMATCH")
    return str(path)


def _window(reference: dict[str, Any], bounds: list[float]) -> dict[str, Any]:
    x0, y0, x1, y1 = qa.rectangle_values(bounds)
    col0 = math.floor((x0 - reference["x0"]) / reference["dx"] + 1e-8)
    col1 = math.ceil((x1 - reference["x0"]) / reference["dx"] - 1e-8)
    row0 = math.floor((reference["y0"] - y1) / reference["dy"] + 1e-8)
    row1 = math.ceil((reference["y0"] - y0) / reference["dy"] - 1e-8)
    return dict(
        reference,
        x0=reference["x0"] + col0 * reference["dx"],
        y0=reference["y0"] - row0 * reference["dy"],
        cols=col1 - col0,
        rows=row1 - row0,
    )


def _read_on_grid(ds: Any, grid: dict[str, Any], col: int, row: int, width: int, height: int, validity: dict[str, Any]):
    np, _gdal, _ogr, _osr = _backend()
    gt = ds.GetGeoTransform()
    dc = round((gt[0] - grid["x0"]) / grid["dx"])
    dr = round((grid["y0"] - gt[3]) / grid["dy"])
    left, top = max(col, dc), max(row, dr)
    right, bottom = min(col + width, dc + ds.RasterXSize), min(row + height, dr + ds.RasterYSize)
    values = np.zeros((height, width), dtype="float64")
    valid = np.zeros((height, width), dtype=bool)
    if right <= left or bottom <= top:
        return values, valid
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray(left - dc, top - dr, right - left, bottom - top)
    flags = band.GetMaskBand().ReadAsArray(left - dc, top - dr, right - left, bottom - top)
    if data is None or flags is None:
        raise RuntimeError("RASTER_BLOCK_READ_FAILED")
    ok = (flags != 0) & np.isfinite(data)
    physical = data.astype("float64") * (band.GetScale() if band.GetScale() is not None else 1)
    physical += band.GetOffset() if band.GetOffset() is not None else 0
    tested = physical if validity.get("value_space") == "PHYSICAL" else data
    for value in validity.get("invalid_values", []):
        ok &= tested != value
    if "minimum" in validity:
        ok &= tested >= validity["minimum"]
    if "maximum" in validity:
        ok &= tested <= validity["maximum"]
    sl = (slice(top - row, bottom - row), slice(left - col, right - col))
    values[sl], valid[sl] = physical, ok
    return values, valid


def _validate_output(
    source: str,
    output: str,
    grid: dict[str, Any],
    layer: Any,
    bounds: list[float],
    validity: dict[str, Any],
    minimum: float,
    diagnostic: str,
):
    np, gdal, _ogr, _osr = _backend()
    src, sm = _raster(source)
    out, om = _raster(output)
    _same_crs(grid["crs_wkt"], om["grid"]["crs_wkt"])
    col_offset, row_offset = qa.grid_offset(grid, om["grid"])
    if (
        col_offset < 0
        or row_offset < 0
        or col_offset + om["grid"]["cols"] > grid["cols"]
        or row_offset + om["grid"]["rows"] > grid["rows"]
    ):
        raise RuntimeError("OUTPUT_EXTENDS_BEYOND_DECLARED_WINDOW")
    checks = [qa.check("grid", True, expected=grid, actual=om["grid"])]
    gt = (grid["x0"], grid["dx"], 0, grid["y0"], 0, -grid["dy"])
    diag = gdal.GetDriverByName("GTiff").Create(
        diagnostic, grid["cols"], grid["rows"], 1, gdal.GDT_Byte, options=["COMPRESS=LZW", "TILED=YES"]
    )
    if diag is None:
        raise RuntimeError("DIAGNOSTIC_CREATE_FAILED")
    diag.SetGeoTransform(gt)
    diag.SetProjection(grid["crs_wkt"])
    diag.GetRasterBand(1).SetNoDataValue(255)
    expected = covered = outside = different = blocks = 0
    for row in range(0, grid["rows"], 512):
        for col in range(0, grid["cols"], 512):
            w, h = min(512, grid["cols"] - col), min(512, grid["rows"] - row)
            x, y = gt[0] + col * gt[1], gt[3] + row * gt[5]
            if layer is not None:
                mem = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Byte)
                mem.SetGeoTransform((x, gt[1], 0, y, 0, gt[5]))
                mem.SetProjection(grid["crs_wkt"])
                layer.SetSpatialFilterRect(x, y - h * grid["dy"], x + w * grid["dx"], y)
                if gdal.RasterizeLayer(mem, [1], layer, burn_values=[1], options=["ALL_TOUCHED=FALSE"]) != 0:
                    raise RuntimeError("EXPECTED_MASK_RASTERIZATION_FAILED")
                e = mem.ReadAsArray() == 1
                mem = None
            else:
                xx = x + (np.arange(w) + 0.5) * grid["dx"]
                yy = y - (np.arange(h) + 0.5) * grid["dy"]
                e = ((xx >= bounds[0]) & (xx < bounds[2]))[None, :] & ((yy > bounds[1]) & (yy <= bounds[3]))[:, None]
            a, v = _read_on_grid(out, grid, col, row, w, h, validity)
            b, bv = _read_on_grid(src, grid, col, row, w, h, validity)
            expected += int(e.sum())
            covered += int((e & v).sum())
            outside += int((~e & v).sum())
            different += int((e & v & (~bv | (a != b))).sum())
            # 0 = covered expected cell; 1 = missing expected cell; 255 = outside AOI.
            diagnostic_block = np.where(e, np.where(v, 0, 1), 255).astype("uint8")
            diag.GetRasterBand(1).WriteArray(diagnostic_block, col, row)
            blocks += 1
    diag.FlushCache()
    diag = None
    if layer is not None:
        layer.SetSpatialFilter(None)
    fraction = qa.coverage_counts(expected, covered)
    checks += [
        qa.check(
            "coverage",
            covered > 0 and fraction >= minimum,
            expected_cells=expected,
            valid_cells=covered,
            missing_cells=expected - covered,
            coverage=fraction,
            minimum=minimum,
            complete_blocks_read=blocks,
            failed_blocks=0,
        ),
        qa.check("outside_aoi", outside == 0, valid_outside_cells=outside),
        qa.check(
            "values_preserved",
            different == 0 and sm["scale"] == om["scale"] and sm["offset"] == om["offset"],
            changed_cells=different,
            source_scale=sm["scale"],
            output_scale=om["scale"],
        ),
    ]
    return checks, fraction


def run_clip_checked(
    arcpy: Any,
    run_id: str,
    source_asset: dict[str, Any],
    grid_asset: dict[str, Any],
    clip_mode: str,
    minimum_coverage: float,
    boundary_asset: dict[str, Any] | None = None,
    rectangle: list[float] | None = None,
    rectangle_crs: str = "",
    missing_data_reason: str = "",
    validity: dict[str, Any] | None = None,
    maximum_output_cells: int = 100_000_000,
) -> dict[str, Any]:
    """Freeze a clip plan, run against snapshots, QA, and qualify only matching evidence."""
    require_allow_write()
    mode = clip_mode.upper()
    if mode not in {"POLYGON_MASK", "RECTANGLE"}:
        raise RuntimeError("EXPLICIT_CLIP_MODE_REQUIRED")
    minimum = qa.finite_number(minimum_coverage, "minimum_coverage")
    if not 0 <= minimum <= 1 or (minimum < 1 and not missing_data_reason.strip()):
        raise RuntimeError("COVERAGE_POLICY_REQUIRED: [0,1], with a reason when missing data are allowed")
    if not isinstance(maximum_output_cells, int) or isinstance(maximum_output_cells, bool) or maximum_output_cells <= 0:
        raise RuntimeError("INVALID_RESOURCE_BUDGET")
    validity = {"value_space": "RAW"} if validity is None else validity
    if not isinstance(validity, dict) or set(validity) - {"value_space", "invalid_values", "minimum", "maximum"}:
        raise RuntimeError("INVALID_VALIDITY_POLICY")
    if validity.get("value_space") not in {"RAW", "PHYSICAL"}:
        raise RuntimeError("VALIDITY_VALUE_SPACE_REQUIRED")
    if not isinstance(validity.get("invalid_values", []), list):
        raise RuntimeError("INVALID_VALIDITY_VALUES")
    for v in validity.get("invalid_values", []) + [validity[k] for k in ("minimum", "maximum") if k in validity]:
        qa.finite_number(v, "validity")
    if validity.get("minimum", -math.inf) > validity.get("maximum", math.inf):
        raise RuntimeError("INVALID_VALIDITY_RANGE")
    if mode == "POLYGON_MASK" and (not boundary_asset or rectangle is not None or rectangle_crs):
        raise RuntimeError("CONFLICTING_CLIP_GEOMETRY")
    if mode == "RECTANGLE" and (boundary_asset is not None or rectangle is None or not rectangle_crs):
        raise RuntimeError("RECTANGLE_AND_CRS_REQUIRED_WITHOUT_POLYGON")
    if mode == "RECTANGLE":
        qa.rectangle_values(rectangle)
    # Freeze the request before any analysis; a reused run_id never repeats the write.
    plan = {
        "source_asset": source_asset,
        "grid_asset": grid_asset,
        "boundary_asset": boundary_asset,
        "clip_mode": mode,
        "rectangle": rectangle,
        "rectangle_crs": rectangle_crs,
        "minimum_coverage": minimum,
        "missing_data_reason": missing_data_reason,
        "validity": validity,
        "maximum_output_cells": maximum_output_cells,
    }
    plan_digest = _digest(plan)
    with _LOCK:
        folder = _run_dir(run_id)
        report_path = folder / "report.json"
        if report_path.exists():
            old = private_state.read_private_json(report_path, max_bytes=1_048_576)
            if old.get("plan_digest") != plan_digest:
                raise RuntimeError("RUN_ID_PLAN_CONFLICT")
            return result_status(run_id)
        folder.mkdir(parents=True, exist_ok=False)
        report: dict[str, Any] = {
            "run_id": run_id,
            "plan_digest": plan_digest,
            "plan": plan,
            "execution_status": "NOT_STARTED",
            "checks": [],
            "started_at": time.time(),
        }
        _store(report_path, report)
        try:
            source = _verify_asset(source_asset, {"CONTINUOUS", "CATEGORICAL"})
            reference = _verify_asset(grid_asset, {"GRID_REFERENCE"})
            assets = {"source": source, "grid": reference}
            _same_crs(source["metadata"]["grid"]["crs_wkt"], reference["metadata"]["grid"]["crs_wkt"])
            qa.grid_offset(reference["metadata"]["grid"], source["metadata"]["grid"])
            if mode == "POLYGON_MASK":
                boundary = _verify_asset(boundary_asset, {"REPORTING_AOI"})
                assets["boundary"] = boundary
                bounds = boundary["metadata"]["bounds"]
                _same_crs(reference["metadata"]["grid"]["crs_wkt"], boundary["metadata"]["crs_wkt"])
            else:
                bounds = list(qa.rectangle_values(rectangle))
                _same_crs(reference["metadata"]["grid"]["crs_wkt"], rectangle_crs)
            grid = _window(reference["metadata"]["grid"], bounds)
            cells = grid["cols"] * grid["rows"]
            if cells <= 0 or cells > maximum_output_cells:
                raise RuntimeError("RESOURCE_OR_EMPTY_WINDOW: change the execution budget, not the analysis resolution")
            needed = sum(f["size"] for a in assets.values() for f in a["files"]) + cells * 24
            if shutil.disk_usage(folder).free < needed:
                raise RuntimeError("INSUFFICIENT_SCRATCH_SPACE")
            report.update(assets=assets, expected_grid=grid, estimated_disk_bytes=needed)
            snapshots = {k: _snapshot(a, folder / k) for k, a in assets.items()}
            for a in assets.values():
                if _file_version(Path(a["path"]))[0] != a["fingerprint"]:
                    raise RuntimeError("INPUT_CHANGED_BEFORE_EXECUTION")
            _source_ds = None
            layer = None
            template_path = "#"
            if "boundary" in snapshots:
                _source_ds, layer, vm = _vector(snapshots["boundary"], boundary.get("vector_layer", ""))
                template_path = snapshots["boundary"]
                if Path(template_path).suffix.lower() == ".gpkg":
                    template_path = os.path.join(template_path, "main." + vm["layer_name"])
                if not arcpy.Exists(template_path):
                    raise RuntimeError("ARCPY_BOUNDARY_ADAPTER_UNSUPPORTED")
            output = str(folder / "result.tif")
            if arcpy.Exists(output) or Path(output).exists():
                raise RuntimeError("OUTPUT_ALREADY_EXISTS")
            # Clear inherited state locally. Clip does not use mask, but stale state
            # must also not leak into future extraction adapters in this scope.
            sr = arcpy.SpatialReference()
            sr.loadFromString(grid["crs_wkt"])
            env = {
                "mask": None,
                "extent": None,
                "snapRaster": snapshots["grid"],
                "outputCoordinateSystem": sr,
                "geographicTransformations": "",
                "parallelProcessingFactor": "0",
                "overwriteOutput": False,
                "pyramid": "NONE",
                "rasterStatistics": "NONE",
                "resamplingMethod": "NEAREST",
            }
            before = {k: str(getattr(arcpy.env, k)) for k in env}
            report.update(
                execution_status="RUNNING",
                output_raster=output,
                environment_before=before,
                planned_environment={k: str(v) for k, v in env.items()},
            )
            _store(report_path, report)
            try:
                with arcpy.EnvManager(**env):
                    report["effective_environment"] = {k: str(getattr(arcpy.env, k)) for k in env}
                    rect = "#" if mode == "POLYGON_MASK" else " ".join(map(str, bounds))
                    result = arcpy.management.Clip(
                        snapshots["source"],
                        rect,
                        output,
                        template_path,
                        "#",
                        "ClippingGeometry" if mode == "POLYGON_MASK" else "NONE",
                        "NO_MAINTAIN_EXTENT",
                    )
                    report["gp_messages"] = safe_error(result.getMessages(), 4000)
            finally:
                after = {k: str(getattr(arcpy.env, k)) for k in env}
                report["environment_after"] = after
                report["checks"].append(qa.check("environment_restored", before == after))
            report["execution_status"] = "SUCCEEDED"
            report["diagnostic_raster"] = str(folder / "missing_cells.tif")
            checks, coverage = _validate_output(
                snapshots["source"], output, grid, layer, bounds, validity, minimum, report["diagnostic_raster"]
            )
            report["checks"].extend(checks)
            unchanged = all(_file_version(Path(a["path"]))[0] == a["fingerprint"] for a in assets.values())
            report["checks"].append(qa.check("input_versions", unchanged))
            report["coverage"] = coverage
            report["output_fingerprint"], _files = _file_version(Path(output))
            report.update(qa.assess("SUCCEEDED", report["checks"], _REQUIRED))
            # The immutable run directory is the asset location. Failed runs remain
            # diagnostic-only; a server-generated certificate is the promotion.
            if report["eligible_for_downstream"]:
                _store(
                    folder / "qualified_asset.json",
                    {
                        "run_id": run_id,
                        "plan_digest": plan_digest,
                        "fingerprint": report["output_fingerprint"],
                        "checker_version": qa.CHECKER_VERSION,
                    },
                )
            _source_ds = None
        except Exception as exc:
            if report["execution_status"] == "RUNNING":
                report["execution_status"] = "FAILED"
            report["error"] = safe_error(exc)
            report["checks"].append(qa.check("adapter_completed", False, error=safe_error(exc)))
            report.update(qa.assess(report["execution_status"], report["checks"], _REQUIRED))
        report["finished_at"] = time.time()
        if report.get("output_raster") and Path(report["output_raster"]).is_file():
            report["output_main_sha256"] = _hash(Path(report["output_raster"]))
        report["arcgis_version"] = arcpy.GetInstallInfo().get("Version", "UNKNOWN")
        _store(report_path, report)
        return result_status(run_id)
