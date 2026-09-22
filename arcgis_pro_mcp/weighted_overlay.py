"""JSON-to-native WOTable adaptation for the registered WeightedOverlay GP tool."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.paths import validate_input_path_optional


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{label} must be an integer")
    return value


def native_table(arcpy: Any, value: Any) -> Any:
    """Build a native value-remap table without accepting encoded path strings.

    The native GP function still performs raster/license/field checks. Validation
    here covers the JSON contract and every embedded path before serialization.
    This intentionally supports numeric RemapValue only, not arbitrary objects.
    """
    if not isinstance(value, dict) or set(value) != {"rasters", "evaluation_scale"}:
        raise RuntimeError("in_weighted_overlay_table requires rasters and evaluation_scale JSON fields")
    scale = value["evaluation_scale"]
    if not isinstance(scale, list) or len(scale) != 3:
        raise RuntimeError("evaluation_scale must be [minimum, maximum, increment]")
    low, high, step = [_integer(v, "evaluation_scale") for v in scale]
    if low >= high or step <= 0 or (high - low) % step:
        raise RuntimeError("evaluation_scale must be increasing with a positive, evenly dividing increment")
    rows = value["rasters"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise RuntimeError("rasters must contain 1 to 128 entries")
    validated = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"raster", "influence", "field", "remap"}:
            raise RuntimeError("each raster requires raster, influence, field and remap")
        raster = row["raster"]
        if not isinstance(raster, str) or not raster.strip() or any(c in raster for c in "';\r\n"):
            raise RuntimeError("raster must be a path without WOTable delimiters")
        raster = validate_input_path_optional(raster, "weighted_overlay.raster")
        influence = _integer(row["influence"], "influence")
        if not 0 <= influence <= 100:
            raise RuntimeError("influence must be between 0 and 100")
        field = row["field"]
        if not isinstance(field, str) or not field.isidentifier():
            raise RuntimeError("field must be a raster attribute field name")
        remap = row["remap"]
        if not isinstance(remap, list) or not 1 <= len(remap) <= 10000:
            raise RuntimeError("remap must contain 1 to 10000 [input_value, scale_value] pairs")
        pairs = []
        seen = set()
        for pair in remap:
            if not isinstance(pair, list) or len(pair) != 2:
                raise RuntimeError("remap must contain [input_value, scale_value] pairs")
            old = _integer(pair[0], "remap input_value")
            new = pair[1]
            if isinstance(new, str) and new in {"NODATA", "RESTRICTED"}:
                pass
            else:
                new = _integer(new, "remap scale_value")
                if not low <= new <= high or (new - low) % step:
                    raise RuntimeError("remap scale_value must belong to evaluation_scale")
            if old in seen:
                raise RuntimeError("remap contains duplicate input values")
            seen.add(old)
            pairs.append([old, new])
        validated.append([raster, influence, field, pairs])
    if sum(row[1] for row in validated) != 100:
        raise RuntimeError("influence weights must sum to 100")
    # Construct native objects only after all records, weights and paths pass.
    # arcpy.gp is the native dispatcher, not the generated Python wrapper that
    # runs gp_fixargs. Explicit serialization is required for compound objects.
    return str(arcpy.sa.WOTable(
        [[path, weight, field, arcpy.sa.RemapValue(remap)] for path, weight, field, remap in validated],
        [low, high, step],
    ))
