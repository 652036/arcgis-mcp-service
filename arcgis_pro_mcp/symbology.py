"""Symbology control: renderers, labels, and layout enhancement tools."""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    validate_output_in_export_root,
)


def set_unique_value_renderer(
    arcpy: Any,
    project: Any,
    map_obj: Any,
    layer: Any,
    fields: list[str],
) -> None:
    require_allow_write()
    if not fields:
        raise RuntimeError("fields 不能为空")
    sym = layer.symbology
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
    cf = classification_field.strip()
    if not cf:
        raise RuntimeError("classification_field 不能为空")
    sym = layer.symbology
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
    cf = classification_field.strip()
    if not cf:
        raise RuntimeError("classification_field 不能为空")
    sym = layer.symbology
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
    sym = layer.symbology
    sym.updateRenderer("SimpleRenderer")
    layer.symbology = sym


def set_heatmap_renderer(
    arcpy: Any,
    project: Any,
    map_obj: Any,
    layer: Any,
) -> None:
    require_allow_write()
    sym = layer.symbology
    sym.updateRenderer("HeatMapRenderer")
    layer.symbology = sym


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
                lc.expressionEngine = ee
                found = True
                break
        if not found:
            names = [c.name for c in lbl_cls_list]
            raise RuntimeError(f"未找到标注类 {lcn!r}，可选：{names}")
    else:
        for lc in lbl_cls_list:
            lc.expression = expr
            lc.expressionEngine = ee


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

    for lc in targets:
        try:
            ts = lc.getTextSymbol()
            if font_name:
                ts.fontName = font_name.strip()
            if font_size is not None:
                ts.fontSize = float(font_size)
            if bold is not None:
                ts.bold = bool(bold)
            if italic is not None:
                ts.italic = bool(italic)
            lc.setTextSymbol(ts)
        except Exception:
            pass


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
) -> None:
    require_allow_write()
    out = validate_output_in_export_root(output_path, "output_path")
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    w = max(100, min(int(width), 8192))
    h = max(100, min(int(height), 8192))
    dpi = max(72, min(int(resolution_dpi), 600))
    ol = out.lower()
    if ol.endswith(".png"):
        map_obj.exportToPNG(out, width=w, height=h, resolution=dpi)
    elif ol.endswith((".jpg", ".jpeg")):
        map_obj.exportToJPEG(out, width=w, height=h, resolution=dpi)
    elif ol.endswith((".tif", ".tiff")):
        map_obj.exportToTIFF(out, width=w, height=h, resolution=dpi)
    else:
        raise RuntimeError("output_path 须以 .png/.jpg/.tif 结尾")
