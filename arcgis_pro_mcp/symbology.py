"""Symbology control: renderers, labels, and layout enhancement tools."""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.arcade import validate_safe_arcade_expression
from arcgis_pro_mcp.paths import (
    require_allow_cim_write,
    require_allow_write,
    validate_new_output_in_export_root,
)


def _require_feature_layer(layer: Any, action: str) -> None:
    if not getattr(layer, "isFeatureLayer", False):
        raise RuntimeError(f"{action} 仅支持要素图层")


def set_unique_value_renderer(
    arcpy: Any,
    project: Any,
    map_obj: Any,
    layer: Any,
    fields: list[str],
) -> None:
    require_allow_write()
    _require_feature_layer(layer, "唯一值渲染")
    if not fields:
        raise RuntimeError("fields 不能为空")
    if len(fields) > 3:
        raise RuntimeError("UniqueValueRenderer 最多 3 个字段")
    src = getattr(layer, "dataSource", None) or layer
    names = {f.name for f in (arcpy.ListFields(src) or [])}
    missing = [f for f in fields if f not in names]
    if missing:
        raise RuntimeError(f"图层中不存在字段：{missing}")
    sym = layer.symbology
    if not hasattr(sym, "updateRenderer"):
        raise RuntimeError("当前图层不支持 renderer 符号化")
    sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = fields
    layer.symbology = sym


def set_graduated_colors_renderer(
    arcpy: Any,
    project: Any,
    map_obj: Any,
    layer: Any,
    classification_field: str,
    num_classes: int = 5,
    classification_method: str = "NaturalBreaks",
) -> None:
    require_allow_write()
    _require_feature_layer(layer, "分级色彩渲染")
    cf = classification_field.strip()
    if not cf:
        raise RuntimeError("classification_field 不能为空")
    src = getattr(layer, "dataSource", None) or layer
    names = {f.name for f in (arcpy.ListFields(src) or [])}
    if cf not in names:
        raise RuntimeError(f"图层中不存在字段：{cf}")
    sym = layer.symbology
    if not hasattr(sym, "updateRenderer"):
        raise RuntimeError("当前图层不支持 renderer 符号化")
    sym.updateRenderer("GraduatedColorsRenderer")
    sym.renderer.classificationField = cf
    sym.renderer.breakCount = max(2, min(int(num_classes), 32))
    valid_methods = {
        "NaturalBreaks", "EqualInterval", "Quantile",
        "StandardDeviation", "ManualInterval", "GeometricInterval",
    }
    cm = classification_method.strip()
    if cm not in valid_methods:
        raise RuntimeError(f"classification_method 须为 {sorted(valid_methods)}")
    sym.renderer.classificationMethod = cm
    layer.symbology = sym


def set_graduated_symbols_renderer(
    arcpy: Any,
    project: Any,
    map_obj: Any,
    layer: Any,
    classification_field: str,
    num_classes: int = 5,
) -> None:
    require_allow_write()
    _require_feature_layer(layer, "分级符号渲染")
    cf = classification_field.strip()
    if not cf:
        raise RuntimeError("classification_field 不能为空")
    src = getattr(layer, "dataSource", None) or layer
    names = {f.name for f in (arcpy.ListFields(src) or [])}
    if cf not in names:
        raise RuntimeError(f"图层中不存在字段：{cf}")
    sym = layer.symbology
    if not hasattr(sym, "updateRenderer"):
        raise RuntimeError("当前图层不支持 renderer 符号化")
    sym.updateRenderer("GraduatedSymbolsRenderer")
    sym.renderer.classificationField = cf
    sym.renderer.breakCount = max(2, min(int(num_classes), 32))
    layer.symbology = sym


def set_simple_renderer(
    arcpy: Any,
    project: Any,
    map_obj: Any,
    layer: Any,
) -> None:
    require_allow_write()
    _require_feature_layer(layer, "简单渲染")
    sym = layer.symbology
    if not hasattr(sym, "updateRenderer"):
        raise RuntimeError("当前图层不支持 renderer 符号化")
    sym.updateRenderer("SimpleRenderer")
    layer.symbology = sym


def set_heatmap_renderer(
    arcpy: Any,
    project: Any,
    map_obj: Any,
    layer: Any,
) -> None:
    require_allow_cim_write()
    _require_feature_layer(layer, "热力图渲染")
    desc = arcpy.Describe(layer)
    shape = str(getattr(desc, "shapeType", "") or "")
    if shape and shape.upper() not in ("POINT", "MULTIPOINT"):
        raise RuntimeError("热力图仅支持点/多点图层")
    cim = layer.getDefinition("V3")
    renderer = None
    for cls_name in ("CIMHeatMapRenderer", "CIMHeatmapRenderer"):
        try:
            renderer = arcpy.cim.CreateCIMObjectFromClassName(cls_name, "V3")
            break
        except Exception:  # noqa: BLE001
            continue
    if renderer is None:
        raise RuntimeError("当前 ArcGIS Pro 无法创建 CIM 热力图渲染器")
    if hasattr(renderer, "radius"):
        renderer.radius = 25
    cim.renderer = renderer
    layer.setDefinition(cim)


def _color_ramp(project: Any, name: str, index: int = 0) -> Any:
    value = (name or "").strip()
    if not value or len(value) > 200 or "\r" in value or "\n" in value:
        raise RuntimeError("color_ramp_name 无效")
    ramps = list(project.listColorRamps(value))
    position = int(index)
    if position < 0 or position >= len(ramps):
        raise RuntimeError(
            f"color ramp {value!r} 的 index={position} 不存在；匹配数={len(ramps)}"
        )
    return ramps[position]


def symbology_info(layer: Any, max_items: int = 100) -> dict[str, Any]:
    """Return a renderer or raster-colorizer snapshot without raw CIM."""
    cap = max(1, min(int(max_items), 500))
    sym = layer.symbology
    target = getattr(sym, "renderer", None)
    kind = "renderer"
    if target is None:
        target = getattr(sym, "colorizer", None)
        kind = "colorizer"
    if target is None:
        raise RuntimeError("该图层不公开 renderer 或 colorizer")
    payload: dict[str, Any] = {
        "kind": kind,
        "type": getattr(target, "type", type(target).__name__),
    }
    for name in (
        "fields",
        "field",
        "classificationField",
        "classificationMethod",
        "breakCount",
        "lowerBound",
        "stretchType",
        "band",
        "gamma",
        "invertColorRamp",
        "minPercent",
        "maxPercent",
        "standardDeviation",
    ):
        try:
            payload[name] = getattr(target, name)
        except Exception:  # noqa: BLE001
            pass
    ramp = getattr(target, "colorRamp", None)
    if ramp is not None:
        payload["color_ramp"] = getattr(ramp, "name", None)
    breaks = []
    for item in list(getattr(target, "classBreaks", None) or [])[:cap]:
        row = {}
        for name in ("upperBound", "label", "description"):
            try:
                row[name] = getattr(item, name)
            except Exception:  # noqa: BLE001
                pass
        breaks.append(row)
    if breaks:
        payload["class_breaks"] = breaks
    groups = []
    remaining = cap
    for group in getattr(target, "groups", None) or []:
        rows = []
        for item in list(getattr(group, "items", None) or [])[:remaining]:
            rows.append(
                {
                    "values": list(getattr(item, "values", None) or []),
                    "label": getattr(item, "label", None),
                    "description": getattr(item, "description", None),
                }
            )
        groups.append({"heading": getattr(group, "heading", None), "items": rows})
        remaining -= len(rows)
        if remaining <= 0:
            break
    if groups:
        payload["groups"] = groups
    return payload


def set_raster_stretch_colorizer(
    project: Any,
    layer: Any,
    *,
    stretch_type: str = "MinimumMaximum",
    band: int = 0,
    gamma: float = 1.0,
    invert_color_ramp: bool = False,
    min_percent: float = 0.0,
    max_percent: float = 0.0,
    standard_deviation: float = 2.0,
    color_ramp_name: str = "",
    color_ramp_index: int = 0,
) -> dict[str, Any]:
    require_allow_write()
    sym = layer.symbology
    if not hasattr(sym, "updateColorizer"):
        raise RuntimeError("当前图层不支持 raster colorizer")
    sym.updateColorizer("RasterStretchColorizer")
    colorizer = sym.colorizer
    allowed = {
        "Custom",
        "Esri",
        "HistogramEqualize",
        "HistogramSpecification",
        "MinimumMaximum",
        "None",
        "PercentClip",
        "StandardDeviation",
    }
    stretch = (stretch_type or "MinimumMaximum").strip()
    if stretch not in allowed:
        raise RuntimeError(f"stretch_type 须为 {sorted(allowed)}")
    if int(band) < 0:
        raise RuntimeError("band 必须 >= 0")
    if float(gamma) <= 0:
        raise RuntimeError("gamma 必须 > 0")
    for label, value in (("min_percent", min_percent), ("max_percent", max_percent)):
        if float(value) < 0 or float(value) > 100:
            raise RuntimeError(f"{label} 必须在 0–100 之间")
    if float(standard_deviation) <= 0:
        raise RuntimeError("standard_deviation 必须 > 0")
    colorizer.stretchType = stretch
    colorizer.band = int(band)
    colorizer.gamma = float(gamma)
    colorizer.invertColorRamp = bool(invert_color_ramp)
    colorizer.minPercent = float(min_percent)
    colorizer.maxPercent = float(max_percent)
    colorizer.standardDeviation = float(standard_deviation)
    if color_ramp_name:
        colorizer.colorRamp = _color_ramp(project, color_ramp_name, color_ramp_index)
    layer.symbology = sym
    return symbology_info(layer)


def set_raster_classify_colorizer(
    project: Any,
    layer: Any,
    classification_field: str,
    *,
    break_count: int = 5,
    classification_method: str = "NaturalBreaks",
    color_ramp_name: str = "",
    color_ramp_index: int = 0,
) -> dict[str, Any]:
    require_allow_write()
    field = (classification_field or "").strip()
    if not field or len(field) > 256 or "\r" in field or "\n" in field:
        raise RuntimeError("classification_field 无效")
    count = int(break_count)
    if count < 2 or count > 64:
        raise RuntimeError("break_count 必须在 2–64 之间")
    allowed = {
        "DefinedInterval",
        "EqualInterval",
        "GeometricInterval",
        "ManualInterval",
        "NaturalBreaks",
        "Quantile",
        "StandardDeviation",
    }
    method = (classification_method or "NaturalBreaks").strip()
    if method not in allowed:
        raise RuntimeError(f"classification_method 须为 {sorted(allowed)}")
    sym = layer.symbology
    if not hasattr(sym, "updateColorizer"):
        raise RuntimeError("当前图层不支持 raster colorizer")
    sym.updateColorizer("RasterClassifyColorizer")
    colorizer = sym.colorizer
    colorizer.classificationField = field
    colorizer.breakCount = count
    colorizer.classificationMethod = method
    if color_ramp_name:
        colorizer.colorRamp = _color_ramp(project, color_ramp_name, color_ramp_index)
    layer.symbology = sym
    return symbology_info(layer)


def set_raster_unique_value_colorizer(
    project: Any,
    layer: Any,
    field: str,
    *,
    color_ramp_name: str = "",
    color_ramp_index: int = 0,
) -> dict[str, Any]:
    require_allow_write()
    value = (field or "").strip()
    if not value or len(value) > 256 or "\r" in value or "\n" in value:
        raise RuntimeError("field 无效")
    sym = layer.symbology
    if not hasattr(sym, "updateColorizer"):
        raise RuntimeError("当前图层不支持 raster colorizer")
    sym.updateColorizer("RasterUniqueValueColorizer")
    colorizer = sym.colorizer
    colorizer.field = value
    if color_ramp_name:
        colorizer.colorRamp = _color_ramp(project, color_ramp_name, color_ramp_index)
    layer.symbology = sym
    return symbology_info(layer)


def apply_gallery_symbol(layer: Any, wildcard: str, index: int = 0) -> dict[str, Any]:
    require_allow_write()
    value = (wildcard or "").strip()
    if not value or len(value) > 200 or "\r" in value or "\n" in value:
        raise RuntimeError("wildcard 无效")
    position = int(index)
    if position < 0 or position > 10_000:
        raise RuntimeError("index 无效")
    sym = layer.symbology
    renderer = getattr(sym, "renderer", None)
    symbol = getattr(renderer, "symbol", None)
    if symbol is None or not hasattr(symbol, "applySymbolFromGallery"):
        raise RuntimeError("仅支持具有单一 renderer.symbol 的图层；请先设置 SimpleRenderer")
    symbol.applySymbolFromGallery(value, position)
    renderer.symbol = symbol
    layer.symbology = sym
    return {
        "renderer_type": getattr(renderer, "type", type(renderer).__name__),
        "symbol_name": getattr(symbol, "name", None),
        "wildcard": value,
        "index": position,
    }


def update_label_expression(
    arcpy: Any,
    layer: Any,
    expression: str,
    label_class_name: str = "",
    expression_engine: str = "Arcade",
) -> None:
    require_allow_cim_write()
    expr = validate_safe_arcade_expression(expression)
    ee = expression_engine.strip().upper()
    if ee != "ARCADE":
        raise RuntimeError("expression_engine 仅允许 Arcade；Python/VBScript 不通过 MCP 暴露")
    ee = "Arcade"
    lbl_cls_list = layer.listLabelClasses()
    lcn = label_class_name.strip()
    if lcn:
        found = False
        for lc in lbl_cls_list:
            if lc.name == lcn:
                lc.expression = expr
                found = True
                break
        if not found:
            names = [c.name for c in lbl_cls_list]
            raise RuntimeError(f"未找到标注类 {lcn!r}，可选：{names}")
    else:
        for lc in lbl_cls_list:
            lc.expression = expr
    cim = layer.getDefinition("V3")
    classes = getattr(cim, "labelClasses", None) or []
    for lcim in classes:
        if lcn and getattr(lcim, "name", "") != lcn:
            continue
        if hasattr(lcim, "expression"):
            lcim.expression = expr
        if hasattr(lcim, "expressionEngine"):
            lcim.expressionEngine = ee
    layer.setDefinition(cim)


def set_label_font(
    arcpy: Any,
    layer: Any,
    font_name: str = "",
    font_size: float | None = None,
    font_color: str = "",
    bold: bool | None = None,
    italic: bool | None = None,
    label_class_name: str = "",
) -> None:
    require_allow_cim_write()
    if not any([font_name, font_size, font_color, bold is not None, italic is not None]):
        raise RuntimeError("至少提供一个字体属性")
    lbl_cls_list = layer.listLabelClasses()
    lcn = label_class_name.strip()
    targets = []
    if lcn:
        for lc in lbl_cls_list:
            if lc.name == lcn:
                targets.append(lc)
                break
        if not targets:
            names = [c.name for c in lbl_cls_list]
            raise RuntimeError(f"未找到标注类 {lcn!r}，可选：{names}")
    else:
        targets = list(lbl_cls_list)

    cim = layer.getDefinition("V3")
    classes = getattr(cim, "labelClasses", None) or []
    applied = 0
    target_names = {getattr(lc, "name", "") for lc in targets}
    rgb = None
    if font_color.strip():
        parts = [p.strip() for p in font_color.replace(";", ",").split(",") if p.strip()]
        if len(parts) != 3:
            raise RuntimeError("font_color 须为 R,G,B")
        rgb = [max(0, min(int(p), 255)) for p in parts]
    for lcim in classes:
        if target_names and getattr(lcim, "name", "") not in target_names:
            continue
        ts = getattr(lcim, "textSymbol", None)
        if ts is None:
            raise RuntimeError("标注类没有可用的 CIM 文本符号，无法设置字体")
        if font_name:
            if hasattr(ts, "fontFamilyName"):
                ts.fontFamilyName = font_name.strip()
            elif hasattr(ts, "fontName"):
                ts.fontName = font_name.strip()
            else:
                raise RuntimeError("当前 CIMTextSymbol 不支持字体名称")
        if font_size is not None:
            if not hasattr(ts, "height"):
                raise RuntimeError("当前 CIMTextSymbol 不支持字体大小")
            ts.height = float(font_size)
        if bold is not None and hasattr(ts, "fontStyleName"):
            style = str(getattr(ts, "fontStyleName", "") or "")
            lowered = style.lower()
            if bold and "bold" not in lowered:
                ts.fontStyleName = "Bold Italic" if italic or "italic" in lowered else "Bold"
            if not bold:
                ts.fontStyleName = "Italic" if italic or "italic" in lowered else "Regular"
        if italic is not None and hasattr(ts, "fontStyleName") and bold is None:
            style = str(getattr(ts, "fontStyleName", "") or "")
            if italic and "italic" not in style.lower():
                ts.fontStyleName = "Bold Italic" if "bold" in style.lower() else "Italic"
            if not italic and "italic" in style.lower():
                ts.fontStyleName = "Bold" if "bold" in style.lower() else "Regular"
        if rgb is not None:
            symbol = getattr(ts, "symbol", None)
            layers = getattr(symbol, "symbolLayers", None) if symbol is not None else None
            color_layer = next(
                (item for item in list(layers or []) if hasattr(item, "color")),
                None,
            )
            color = getattr(color_layer, "color", None) if color_layer is not None else None
            if color is None or not hasattr(color, "values"):
                raise RuntimeError("当前 CIMTextSymbol 没有可写颜色的符号层")
            color.values = rgb + [100]
        applied += 1
    if applied == 0:
        raise RuntimeError("没有可更新的标注类")
    layer.setDefinition(cim)


def export_report_pdf(
    arcpy: Any,
    project: Any,
    report_name: str,
    output_pdf_path: str,
) -> None:
    require_allow_write()
    out = validate_new_output_in_export_root(output_pdf_path, "output_pdf_path")
    if not out.lower().endswith(".pdf"):
        raise RuntimeError("output_pdf_path 应以 .pdf 结尾")
    if not hasattr(project, "listReports"):
        raise RuntimeError("当前 Pro 版本不支持 listReports")
    rpt = None
    for r in project.listReports():
        if r.name == report_name:
            rpt = r
            break
    if rpt is None:
        names = [r.name for r in project.listReports()]
        raise RuntimeError(f"未找到报表 {report_name!r}，可选：{names}")
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    rpt.exportToPDF(out)


def export_map_to_image(
    arcpy: Any,
    map_obj: Any,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    resolution_dpi: int = 96,
) -> str:
    require_allow_write()
    out = validate_new_output_in_export_root(output_path, "output_path")
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    w = max(100, min(int(width), 8192))
    h = max(100, min(int(height), 8192))
    dpi = max(72, min(int(resolution_dpi), 600))
    view = getattr(map_obj, "defaultView", None)
    if view is None:
        raise RuntimeError("地图无 defaultView，无法导出。请改用布局导出。")
    ol = out.lower()
    if ol.endswith(".png"):
        view.exportToPNG(out, width=w, height=h, resolution=dpi)
    elif ol.endswith((".jpg", ".jpeg")):
        view.exportToJPEG(out, width=w, height=h, resolution=dpi)
    elif ol.endswith((".tif", ".tiff")):
        view.exportToTIFF(out, width=w, height=h, resolution=dpi)
    else:
        raise RuntimeError("output_path 须以 .png/.jpg/.tif 结尾")
    return out
