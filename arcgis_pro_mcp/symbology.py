"""Symbology control: renderers, labels, and layout enhancement tools."""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    validate_output_in_export_root,
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
    require_allow_write()
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


def update_label_expression(
    arcpy: Any,
    layer: Any,
    expression: str,
    label_class_name: str = "",
    expression_engine: str = "Arcade",
) -> None:
    require_allow_write()
    expr = expression.strip()
    if not expr:
        raise RuntimeError("expression 不能为空")
    ee = expression_engine.strip()
    if ee not in ("Arcade", "Python", "VBScript"):
        raise RuntimeError("expression_engine 须为 Arcade、Python 或 VBScript")
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
    require_allow_write()
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
        symbol = getattr(ts, "symbol", None) if ts is not None else None
        layers = getattr(symbol, "symbolLayers", None) if symbol is not None else None
        if not layers:
            raise RuntimeError("标注类没有可用的 CIM 文本符号，无法设置字体")
        text_layer = layers[0]
        if font_name:
            if hasattr(text_layer, "fontFamilyName"):
                text_layer.fontFamilyName = font_name.strip()
            elif hasattr(text_layer, "fontName"):
                text_layer.fontName = font_name.strip()
        if font_size is not None and hasattr(text_layer, "height"):
            text_layer.height = float(font_size)
        if bold is not None and hasattr(text_layer, "fontStyleName"):
            style = str(getattr(text_layer, "fontStyleName", "") or "")
            lowered = style.lower()
            if bold and "bold" not in lowered:
                text_layer.fontStyleName = ("Bold Italic" if italic or "italic" in lowered else "Bold")
            if not bold:
                text_layer.fontStyleName = ("Italic" if italic or "italic" in lowered else "Regular")
        if italic is not None and hasattr(text_layer, "fontStyleName") and bold is None:
            style = str(getattr(text_layer, "fontStyleName", "") or "")
            if italic and "italic" not in style.lower():
                text_layer.fontStyleName = ("Bold Italic" if "bold" in style.lower() else "Italic")
            if not italic and "italic" in style.lower():
                text_layer.fontStyleName = ("Bold" if "bold" in style.lower() else "Regular")
        if rgb is not None and hasattr(text_layer, "color"):
            color = getattr(text_layer, "color", None)
            if color is not None and hasattr(color, "values"):
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
    out = validate_output_in_export_root(output_pdf_path, "output_pdf_path")
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
    out = validate_output_in_export_root(output_path, "output_path")
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
