"""Typed ArcGIS Pro chart helpers with a deliberately small mutation surface.

ArcPy is injected by the caller.  Chart deletion is intentionally not exposed:
the public ArcPy chart API documents creation, update, listing, and export, but
does not provide a stable semantic delete operation.
"""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.paths import require_allow_write, validate_new_output_in_export_root

_CHART_TYPES = {"BAR": "Bar", "LINE": "Line", "SCATTER": "Scatter", "HISTOGRAM": "Histogram", "PIE": "Pie"}
_AGGREGATIONS = {"COUNT", "SUM", "MEAN", "MEDIAN", "MIN", "MAX"}
_THEMES = {"LIGHT": "Light", "MEDIUM": "Medium", "DARK": "Dark"}


def _required_text(value: Any, label: str, max_length: int = 512) -> str:
    result = str(value or "").strip()
    if not result:
        raise RuntimeError(f"{label} 不能为空")
    if len(result) > max_length:
        raise RuntimeError(f"{label} 不能超过 {max_length} 个字符")
    return result


def _bounded_int(value: Any, label: str, low: int, high: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 必须为整数") from exc
    if not low <= result <= high:
        raise RuntimeError(f"{label} 必须在 {low} 到 {high} 之间")
    return result


def _chart_title(chart: Any) -> str:
    return str(getattr(chart, "title", "") or getattr(chart, "name", "") or "")


def _chart_type(chart: Any) -> str:
    raw = str(getattr(chart, "type", "") or chart.__class__.__name__).upper().replace("_", "")
    for kind in _CHART_TYPES:
        if kind.replace("_", "") in raw:
            return kind
    return raw


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


def _axis_title(chart: Any, axis_name: str) -> str:
    axis = getattr(chart, axis_name, None)
    return str(getattr(axis, "title", "") or "") if axis is not None else ""


def chart_info(chart: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "title": _chart_title(chart),
        "type": _chart_type(chart),
        "description": str(getattr(chart, "description", "") or ""),
        "x_title": _axis_title(chart, "xAxis"),
        "y_title": _axis_title(chart, "yAxis"),
        "has_data_source": getattr(chart, "dataSource", None) is not None,
    }
    for property_name in (
        "x",
        "y",
        "categoryField",
        "numberFields",
        "aggregation",
        "splitCategory",
        "theme",
        "rotated",
        "showTrendLine",
        "binCount",
        "showMean",
        "showMedian",
        "showStandardDeviation",
        "donutSize",
        "groupingPercent",
        "showDataLabels",
    ):
        if hasattr(chart, property_name):
            result[property_name] = _json_value(getattr(chart, property_name))
    return result


def list_charts(layer_or_table: Any) -> list[dict[str, Any]]:
    method = getattr(layer_or_table, "listCharts", None)
    if not callable(method):
        raise RuntimeError("目标对象不支持 listCharts")
    return [chart_info(chart) for chart in list(method() or [])]


def _select_chart(layer_or_table: Any, title: str) -> Any:
    chart_title = _required_text(title, "title", 512)
    method = getattr(layer_or_table, "listCharts", None)
    if not callable(method):
        raise RuntimeError("目标对象不支持 listCharts")
    matches = [chart for chart in list(method() or []) if _chart_title(chart) == chart_title]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"图表标题不唯一：{chart_title!r}")
    choices = [_chart_title(chart) for chart in list(method() or [])]
    raise RuntimeError(f"未找到图表 {chart_title!r}，可选：{choices}")


def _as_fields(value: str | list[str] | tuple[str, ...] | None, label: str) -> list[str]:
    if value is None:
        return []
    raw = [value] if isinstance(value, str) else list(value)
    result = [_required_text(item, label, 256) for item in raw]
    if len(set(item.lower() for item in result)) != len(result):
        raise RuntimeError(f"{label} 包含重复字段")
    return result


def _canonicalize_fields(arcpy: Any, target: Any, fields: list[str], validate_fields: bool) -> list[str]:
    if not fields or not validate_fields:
        return fields
    list_fields = getattr(arcpy, "ListFields", None)
    if not callable(list_fields):
        raise RuntimeError("注入的 ArcPy 对象不支持 ListFields；无法验证图表字段")
    available = {str(field.name).lower(): str(field.name) for field in list(list_fields(target) or [])}
    missing = [field for field in fields if field.lower() not in available]
    if missing:
        raise RuntimeError(f"图表字段不存在：{missing}")
    return [available[field.lower()] for field in fields]


def _single(fields: list[str], label: str, *, required: bool) -> str:
    if len(fields) > 1 or (required and not fields):
        qualifier = "必须且只能有一个字段" if required else "最多只能有一个字段"
        raise RuntimeError(f"{label} {qualifier}")
    return fields[0] if fields else ""


def _set_property(chart: Any, name: str, value: Any) -> None:
    if not hasattr(chart, name):
        raise RuntimeError(f"当前 {_chart_type(chart)} 图表不支持属性 {name}")
    setattr(chart, name, value)


def _set_axis_title(chart: Any, axis_name: str, value: str) -> None:
    if not value:
        return
    axis = getattr(chart, axis_name, None)
    if axis is None or not hasattr(axis, "title"):
        raise RuntimeError(f"当前图表不支持 {axis_name}.title")
    axis.title = value


def upsert_chart(
    arcpy: Any,
    layer_or_table: Any,
    chart_type: str,
    title: str,
    *,
    x: str | list[str] | tuple[str, ...] | None = None,
    y: str | list[str] | tuple[str, ...] | None = None,
    category_field: str = "",
    number_fields: list[str] | tuple[str, ...] | None = None,
    aggregation: str = "",
    split_category: str = "",
    description: str = "",
    x_title: str = "",
    y_title: str = "",
    theme: str = "Light",
    rotated: bool | None = None,
    show_trend_line: bool | None = None,
    bin_count: int | None = None,
    show_mean: bool | None = None,
    show_median: bool | None = None,
    show_standard_deviation: bool | None = None,
    donut_size: int | None = None,
    grouping_percent: int | None = None,
    show_data_labels: bool | None = None,
    validate_fields: bool = True,
) -> dict[str, Any]:
    """Create or update one Bar/Line/Scatter/Histogram/Pie chart by title."""

    require_allow_write()
    kind = _required_text(chart_type, "chart_type", 32).upper()
    if kind not in _CHART_TYPES:
        raise RuntimeError(f"chart_type 须为 {sorted(_CHART_TYPES)}")
    chart_title = _required_text(title, "title", 512)
    list_method = getattr(layer_or_table, "listCharts", None)
    if not callable(list_method):
        raise RuntimeError("目标对象不支持 listCharts")
    existing = [chart for chart in list(list_method() or []) if _chart_title(chart) == chart_title]
    if len(existing) > 1:
        raise RuntimeError(f"图表标题不唯一：{chart_title!r}")
    if existing and _chart_type(existing[0]) != kind:
        raise RuntimeError(
            f"同名图表类型为 {_chart_type(existing[0])}，不能原地改为 {kind}；"
            "ArcPy 没有安全的图表删除 API。"
        )

    x_fields = _as_fields(x, "x")
    y_fields = _as_fields(y, "y")
    category_fields = _as_fields(category_field or None, "category_field")
    numeric_fields = _as_fields(number_fields, "number_fields")
    split_fields = _as_fields(split_category or None, "split_category")
    all_fields = x_fields + y_fields + category_fields + numeric_fields + split_fields
    canonical = _canonicalize_fields(arcpy, layer_or_table, all_fields, validate_fields)
    cursor = 0

    def take(count: int) -> list[str]:
        nonlocal cursor
        values = canonical[cursor : cursor + count]
        cursor += count
        return values

    x_fields = take(len(x_fields))
    y_fields = take(len(y_fields))
    category_fields = take(len(category_fields))
    numeric_fields = take(len(numeric_fields))
    split_fields = take(len(split_fields))

    x_value: str | list[str]
    y_value: str | list[str]
    if kind in {"BAR", "LINE", "SCATTER"}:
        x_value = _single(x_fields, "x", required=True)
    elif kind == "HISTOGRAM":
        if not x_fields:
            raise RuntimeError("HISTOGRAM 必须提供 x")
        x_value = x_fields[0] if len(x_fields) == 1 else x_fields
    else:
        if x_fields or y_fields:
            raise RuntimeError("PIE 请使用 category_field/number_fields，不使用 x/y")
        x_value = ""

    if kind == "SCATTER":
        y_value = _single(y_fields, "y", required=True)
    else:
        y_value = y_fields[0] if len(y_fields) == 1 else y_fields
    category_value = _single(category_fields, "category_field", required=kind == "PIE")
    split_value = _single(split_fields, "split_category", required=False)

    aggregation_value = str(aggregation or "").strip().upper()
    if aggregation_value and aggregation_value not in _AGGREGATIONS:
        raise RuntimeError(f"aggregation 须为 {sorted(_AGGREGATIONS)}")
    if aggregation_value and kind not in {"BAR", "LINE"}:
        raise RuntimeError("aggregation 仅适用于 BAR/LINE")
    theme_key = _required_text(theme, "theme", 32).upper()
    if theme_key not in _THEMES:
        raise RuntimeError(f"theme 须为 {sorted(_THEMES.values())}")
    if kind == "PIE" and (x_title or y_title):
        raise RuntimeError("PIE 不支持 x_title/y_title")
    if rotated is not None and kind not in {"BAR", "LINE"}:
        raise RuntimeError("rotated 仅适用于 BAR/LINE")
    if show_trend_line is not None and kind != "SCATTER":
        raise RuntimeError("show_trend_line 仅适用于 SCATTER")
    if any(value is not None for value in (bin_count, show_mean, show_median, show_standard_deviation)) and kind != "HISTOGRAM":
        raise RuntimeError("bin_count/show_mean/show_median/show_standard_deviation 仅适用于 HISTOGRAM")
    if any(value is not None for value in (donut_size, grouping_percent, show_data_labels)) and kind != "PIE":
        raise RuntimeError("donut_size/grouping_percent/show_data_labels 仅适用于 PIE")

    created = not existing
    if existing:
        chart = existing[0]
    else:
        charts_module = getattr(arcpy, "charts", None)
        factory = getattr(charts_module, _CHART_TYPES[kind], None)
        if not callable(factory):
            raise RuntimeError(f"当前 ArcPy 不支持 arcpy.charts.{_CHART_TYPES[kind]}")
        constructor: dict[str, Any] = {"title": chart_title, "dataSource": layer_or_table}
        if kind == "PIE":
            constructor["categoryField"] = category_value
            if numeric_fields:
                constructor["numberFields"] = numeric_fields[0] if len(numeric_fields) == 1 else numeric_fields
        else:
            constructor["x"] = x_value
            if kind in {"BAR", "LINE", "SCATTER"} and y_fields:
                constructor["y"] = y_value
        chart = factory(**constructor)
        if chart is None:
            raise RuntimeError("ArcPy 未返回 chart 对象")

    _set_property(chart, "title", chart_title)
    _set_property(chart, "dataSource", layer_or_table)
    if hasattr(chart, "description"):
        chart.description = str(description or "")
    _set_property(chart, "theme", _THEMES[theme_key])
    if kind in {"BAR", "LINE", "SCATTER"}:
        _set_property(chart, "x", x_value)
        if y_fields:
            _set_property(chart, "y", y_value)
    elif kind == "HISTOGRAM":
        _set_property(chart, "x", x_value)
    else:
        _set_property(chart, "categoryField", category_value)
        if numeric_fields:
            _set_property(chart, "numberFields", numeric_fields[0] if len(numeric_fields) == 1 else numeric_fields)
    if split_value:
        _set_property(chart, "splitCategory", split_value)
    if aggregation_value:
        _set_property(chart, "aggregation", aggregation_value)
    _set_axis_title(chart, "xAxis", str(x_title or ""))
    _set_axis_title(chart, "yAxis", str(y_title or ""))
    if rotated is not None:
        _set_property(chart, "rotated", bool(rotated))
    if show_trend_line is not None:
        _set_property(chart, "showTrendLine", bool(show_trend_line))
    if bin_count is not None:
        _set_property(chart, "binCount", _bounded_int(bin_count, "bin_count", 1, 10000))
    if show_mean is not None:
        _set_property(chart, "showMean", bool(show_mean))
    if show_median is not None:
        _set_property(chart, "showMedian", bool(show_median))
    if show_standard_deviation is not None:
        _set_property(chart, "showStandardDeviation", bool(show_standard_deviation))
    if donut_size is not None:
        _set_property(chart, "donutSize", _bounded_int(donut_size, "donut_size", 0, 100))
    if grouping_percent is not None:
        _set_property(chart, "groupingPercent", _bounded_int(grouping_percent, "grouping_percent", 0, 100))
    if show_data_labels is not None:
        _set_property(chart, "showDataLabels", bool(show_data_labels))

    if created:
        add = getattr(chart, "addToLayer", None)
        if not callable(add):
            raise RuntimeError("当前 chart 不支持 addToLayer")
        add(layer_or_table)
    else:
        update = getattr(chart, "updateChart", None)
        if not callable(update):
            raise RuntimeError("当前 chart 不支持 updateChart")
        update()
    return {"created": created, "chart": chart, "info": chart_info(chart)}


def export_chart(
    layer_or_table: Any,
    title: str,
    output_path: str,
    *,
    width: int = 800,
    height: int = 600,
) -> dict[str, Any]:
    require_allow_write()
    chart = _select_chart(layer_or_table, title)
    out = validate_new_output_in_export_root(output_path, "output_path")
    lower = out.lower()
    methods = {
        ".svg": ("SVG", "exportToSVG"),
        ".png": ("PNG", "exportToPNG"),
        ".jpg": ("JPEG", "exportToJPEG"),
        ".jpeg": ("JPEG", "exportToJPEG"),
    }
    match = next((value for suffix, value in methods.items() if lower.endswith(suffix)), None)
    if match is None:
        raise RuntimeError("output_path 须以 .svg/.png/.jpg/.jpeg 结尾")
    output_format, method_name = match
    method = getattr(chart, method_name, None)
    if not callable(method):
        raise RuntimeError(f"当前 {_chart_type(chart)} 图表不支持 {method_name}")
    out_width = _bounded_int(width, "width", 100, 16384)
    out_height = _bounded_int(height, "height", 100, 16384)
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    method(out, out_width, out_height)
    return {
        "output_path": out,
        "format": output_format,
        "width": out_width,
        "height": out_height,
        "chart": chart_info(chart),
    }


def chart_delete_capability() -> dict[str, Any]:
    return {
        "supported": False,
        "reason": "ArcPy 3.x 没有稳定、受支持的语义化 chart delete API；本模块不会用 CIM 猜测删除。",
    }


__all__ = [
    "chart_delete_capability",
    "chart_info",
    "export_chart",
    "list_charts",
    "upsert_chart",
]
