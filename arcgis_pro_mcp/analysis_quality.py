"""Small, dependency-free quality contracts shared by analysis adapters.

Execution, readable output, and scientific suitability are deliberately separate.
No AOI, CRS, resolution, coverage threshold, or model is a global default.
"""

from __future__ import annotations

import math
from typing import Any

CHECKER_VERSION = "1.0"


def finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise RuntimeError(f"{label} must be {'positive and ' if positive else ''}finite")
    return result


def rectangle_values(values: Any) -> tuple[float, float, float, float]:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise RuntimeError("rectangle must contain xmin, ymin, xmax, ymax")
    x0, y0, x1, y1 = (finite_number(v, "rectangle") for v in values)
    if x0 >= x1 or y0 >= y1:
        raise RuntimeError("rectangle bounds must be strictly increasing")
    return x0, y0, x1, y1


def grid_offset(reference: dict[str, Any], actual: dict[str, Any], *, same_window: bool = False) -> tuple[int, int]:
    """Validate north-up grids; return actual origin offset in reference cells.

    CRS equivalence must be established by the format adapter before this call.
    Tolerance is one millionth of a pixel, not a permissive distance in meters.
    """
    tol = 1e-6
    for key in ("dx", "dy"):
        a = finite_number(reference[key], key, positive=True)
        b = finite_number(actual[key], key, positive=True)
        if not math.isclose(a, b, rel_tol=1e-10, abs_tol=a * tol):
            raise RuntimeError("GRID_CELL_SIZE_MISMATCH")
    offsets = [(actual["x0"] - reference["x0"]) / reference["dx"], (reference["y0"] - actual["y0"]) / reference["dy"]]
    if any(not math.isfinite(v) or abs(v - round(v)) > tol for v in offsets):
        raise RuntimeError("GRID_ORIGIN_MISMATCH: fractional-cell shift")
    col, row = (round(v) for v in offsets)
    if same_window and (col or row or any(actual[k] != reference[k] for k in ("rows", "cols"))):
        raise RuntimeError("GRID_WINDOW_MISMATCH")
    return col, row


def coverage_counts(expected: int, covered: int) -> float:
    if expected <= 0:
        raise RuntimeError("EMPTY_EXPECTED_DOMAIN")
    if covered < 0 or covered > expected:
        raise RuntimeError("INVALID_COVERAGE_COUNTS")
    return covered / expected


def check(name: str, passed: bool, **metrics: Any) -> dict[str, Any]:
    return {"name": name, "status": "PASSED" if passed else "FAILED", "metrics": metrics}


def assess(execution_status: str, checks: list[dict[str, Any]], required: set[str]) -> dict[str, Any]:
    """Compute eligibility, never accept a caller-supplied eligibility boolean."""
    by_name = {item["name"]: item for item in checks}
    if len(by_name) != len(checks):
        raise RuntimeError("DUPLICATE_CHECK_NAME")
    if any(item["status"] not in {"PASSED", "FAILED", "WARNING", "NOT_CHECKED", "NOT_APPLICABLE"} for item in checks):
        raise RuntimeError("INVALID_CHECK_STATUS")
    warning = any(item["status"] == "WARNING" for item in checks)
    failed = any(item["status"] == "FAILED" for item in checks)
    complete = all(name in by_name and by_name[name]["status"] == "PASSED" for name in required)
    qa = "FAILED" if failed else "WARNING" if warning else "PASSED" if complete and required else "NOT_CHECKED"
    return {
        "execution_status": execution_status,
        "qa_status": qa,
        "eligible_for_downstream": execution_status == "SUCCEEDED" and qa == "PASSED",
        "scientific_suitability": "NOT_ASSESSED",
        "checker_version": CHECKER_VERSION,
    }


def existence_evidence(exists: bool | None) -> dict[str, Any]:
    """Retain historical verified as an existence-only flag, with an explicit scope."""
    return {
        "exists": exists,
        "verified": exists is True,
        "verification_scope": "EXISTENCE_ONLY",
        "execution_status": "SUCCEEDED",
        "qa_status": "NOT_CHECKED",
        "eligible_for_downstream": False,
        "scientific_suitability": "NOT_ASSESSED",
    }
