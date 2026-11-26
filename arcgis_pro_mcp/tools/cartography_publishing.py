"""Layouts, map series, reports, symbology, labeling, charts, and portal publishing tools."""

from __future__ import annotations

import json
import os
from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool

SERVER_EXPORTS: tuple[str, ...] = ("_clamp_dpi", "_find_layout_text_element", "_symbology_template_path", "_publishing_arcpy", "_chart_member",)

@tool(
    name="arcgis_pro_list_layouts",
    description="",
)
def arcgis_pro_list_layouts(aprx_path: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layouts = list(project.listLayouts())
    names = [lyt.name for lyt in layouts]
    items = [{"name": lyt.name, "uri": _srv._object_uri(lyt)} for lyt in layouts]
    return json.dumps(
        {"aprx_path": path, "layouts": names, "layout_items": items},
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="arcgis_pro_list_reports",
    description="",
)
def arcgis_pro_list_reports(aprx_path: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    if not hasattr(project, "listReports"):
        return json.dumps(
            {
                "aprx_path": path,
                "reports": [],
                "note": "当前 arcpy.mp.ArcGISProject 无 listReports，可能为较旧 Pro 版本。",
            },
            ensure_ascii=False,
            indent=2,
        )
    names = [r.name for r in project.listReports()]  # type: ignore[attr-defined]
    return json.dumps({"aprx_path": path, "reports": names}, ensure_ascii=False, indent=2)


@tool(
    name="arcgis_pro_list_layout_elements",
    description="",
)
def arcgis_pro_list_layout_elements(
    aprx_path: str,
    layout_name: str,
    element_type: str = "",
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    et = element_type.strip() or None
    elements = layout.listElements(et) if et else layout.listElements()
    rows: list[dict[str, Any]] = []
    for elm in elements:
        row: dict[str, Any] = {
            "type": getattr(elm, "type", type(elm).__name__),
            "name": getattr(elm, "name", ""),
        }
        rows.append(row)

    return json.dumps(
        {
            "aprx_path": path,
            "layout_name": layout_name,
            "element_type_filter": et or "(all)",
            "elements": rows,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool(
    name="arcgis_pro_mapframe_extent",
    description="",
)
def arcgis_pro_mapframe_extent(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _srv._raise_not_found("map frame", mapframe_name, names)

    out: dict[str, Any] = {
        "aprx_path": path,
        "layout_name": layout_name,
        "mapframe_name": mapframe_name,
    }
    try:
        out["map_name"] = mf.map.name
    except Exception as ex:  # noqa: BLE001
        out["map_name_error"] = _srv._safe_error(ex, 200)
    try:
        out["scale"] = float(mf.camera.scale)
    except Exception:  # noqa: BLE001
        pass
    try:
        extent = _srv._mapframe_extent(mf)
        if extent is not None:
            out["extent"] = _srv._extent_dict(extent)
        else:
            out["extent_error"] = "当前 MapFrame/Camera 不支持 getExtent"
    except Exception as ex:  # noqa: BLE001
        out["extent_error"] = _srv._safe_error(ex, 500)

    return json.dumps(out, ensure_ascii=False, indent=2)


@tool(
    name="arcgis_pro_export_layout_pdf",
    description="",
)
def arcgis_pro_export_layout_pdf(
    aprx_path: str,
    layout_name: str,
    output_pdf_path: str,
    resolution_dpi: int = 300,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    out_path = _srv.validate_new_output_in_export_root(output_pdf_path, "output_pdf_path")
    if not out_path.lower().endswith(".pdf"):
        raise RuntimeError("output_pdf_path 应以 .pdf 结尾")
    try:
        dpi = max(72, min(int(resolution_dpi), 960))
    except (TypeError, ValueError) as e:
        raise RuntimeError("resolution_dpi 必须为整数") from e
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    layout.exportToPDF(out_path, resolution=dpi)  # type: ignore[attr-defined]
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "output_pdf_path": out_path,
            "resolution_dpi": dpi,
        },
    )


def _clamp_dpi(resolution_dpi: int) -> int:
    try:
        return max(72, min(int(resolution_dpi), 960))
    except (TypeError, ValueError) as e:
        raise RuntimeError("resolution_dpi 必须为整数") from e


@tool(
    name="arcgis_pro_export_layout_image",
    description="",
)
def arcgis_pro_export_layout_image(
    aprx_path: str,
    layout_name: str,
    output_path: str,
    image_format: str = "png",
    resolution_dpi: int = 300,
    jpeg_quality: int = 90,
    transparent_background: bool = False,
    world_file: bool = False,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    out_path = _srv.validate_new_output_in_export_root(output_path, "output_path")
    fmt = image_format.strip().lower()
    dpi = _srv._clamp_dpi(resolution_dpi)
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if fmt == "png":
        if not out_path.lower().endswith(".png"):
            raise RuntimeError("PNG 输出路径应以 .png 结尾")
        layout.exportToPNG(  # type: ignore[attr-defined]
            out_path,
            resolution=dpi,
            transparent_background=transparent_background,
        )
    elif fmt in ("jpg", "jpeg"):
        if not out_path.lower().endswith((".jpg", ".jpeg")):
            raise RuntimeError("JPEG 输出路径应以 .jpg 或 .jpeg 结尾")
        try:
            jq = max(1, min(int(jpeg_quality), 100))
        except (TypeError, ValueError) as e:
            raise RuntimeError("jpeg_quality 须为 1–100 的整数") from e
        layout.exportToJPEG(out_path, resolution=dpi, jpeg_quality=jq)  # type: ignore[attr-defined]
    elif fmt == "tiff":
        if not out_path.lower().endswith((".tif", ".tiff")):
            raise RuntimeError("TIFF 输出路径应以 .tif 或 .tiff 结尾")
        layout.exportToTIFF(  # type: ignore[attr-defined]
            out_path,
            resolution=dpi,
            world_file=world_file,
        )
    else:
        raise RuntimeError('image_format 须为 "png"、"jpeg" 或 "tiff"')

    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "output_path": out_path,
            "image_format": fmt,
            "resolution_dpi": dpi,
        },
    )


def _find_layout_text_element(layout: Any, element_name: str, element_type: str) -> Any:
    en = element_name.strip()
    et = element_type.strip().upper()
    order: list[str]
    if et == "TEXT_ELEMENT":
        order = ["TEXT_ELEMENT", "GRAPHIC_ELEMENT"]
    elif et in ("TEXT_GRAPHIC_ELEMENT", "GRAPHIC_ELEMENT"):
        order = ["GRAPHIC_ELEMENT", "TEXT_ELEMENT"]
    elif not et:
        order = ["GRAPHIC_ELEMENT", "TEXT_ELEMENT"]
    else:
        raise RuntimeError(
            "element_type 须为空、TEXT_ELEMENT 或 GRAPHIC_ELEMENT"
        )
    available: list[str] = []
    for tt in order:
        for elm in layout.listElements(tt):
            nm = getattr(elm, "name", "")
            if nm == en:
                return elm
            if nm:
                available.append(nm)
    _srv._raise_not_found("text element", en, available)


@tool(
    name="arcgis_pro_update_layout_text_element",
    description="",
)
def arcgis_pro_update_layout_text_element(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    text: str,
    element_type: str = "",
    allow_dynamic_text_overwrite: bool = False,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    elm = _srv._find_layout_text_element(layout, element_name, element_type)
    old = getattr(elm, "text", "") or ""
    if "<dyn" in old.lower() and not allow_dynamic_text_overwrite:
        raise RuntimeError("检测到动态文本，若需覆盖请设置 allow_dynamic_text_overwrite=true")
    elm.text = text
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "element_name": element_name.strip(),
        },
    )


@tool(
    name="arcgis_pro_set_mapframe_extent",
    description="",
)
def arcgis_pro_set_mapframe_extent(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    spatial_reference_wkid: int | None = None,
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _srv._raise_not_found("map frame", mapframe_name, names)
    ext = arcpy.Extent(float(xmin), float(ymin), float(xmax), float(ymax))  # type: ignore[attr-defined]
    if spatial_reference_wkid is not None:
        ext.spatialReference = arcpy.SpatialReference(int(spatial_reference_wkid))  # type: ignore[attr-defined]
    else:
        try:
            ext.spatialReference = mf.map.spatialReference
        except Exception:  # noqa: BLE001
            pass
    _srv._set_mapframe_extent(mf, ext)
    out: dict[str, Any] = {
        "ok": True,
        "aprx_path": path,
        "layout_name": layout_name,
        "mapframe_name": mapframe_name,
    }
    try:
        out["extent_after"] = _srv._extent_dict(_srv._mapframe_extent(mf))
    except Exception as ex:  # noqa: BLE001
        out["extent_read_error"] = _srv._safe_error(ex, 300)
    return _srv._json_dumps(out)


def _symbology_template_path(path: str) -> str:
    p = _srv.validate_input_path_optional(path, "symbology_layer_path")
    pl = p.lower()
    if not (pl.endswith(".lyrx") or pl.endswith(".lyr")):
        raise RuntimeError("symbology_layer_path 须为 .lyrx 或 .lyr")
    return p


@tool(
    name="arcgis_pro_apply_symbology_from_layer",
    description="",
)
def arcgis_pro_apply_symbology_from_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    symbology_layer_path: str,
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    sp = _srv._symbology_template_path(symbology_layer_path)
    arcpy.management.ApplySymbologyFromLayer(lyr, sp)  # type: ignore[attr-defined]
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "symbology_layer_path": sp,
        },
    )


@tool(
    name="arcgis_pro_toggle_layer_labels",
    description="",
)
def arcgis_pro_toggle_layer_labels(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    show_labels: bool,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    lyr.showLabels = bool(show_labels)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "show_labels": lyr.showLabels,
        },
    )


@tool(
    name="arcgis_pro_set_unique_value_renderer",
    description="",
)
def arcgis_pro_set_unique_value_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    fields: list[str],
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    _srv.symbology.set_unique_value_renderer(arcpy, project, m, lyr, fields)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "UniqueValueRenderer"},
    )


@tool(
    name="arcgis_pro_set_graduated_colors_renderer",
    description="",
)
def arcgis_pro_set_graduated_colors_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    classification_field: str,
    num_classes: int = 5,
    classification_method: str = "NaturalBreaks",
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    _srv.symbology.set_graduated_colors_renderer(
        arcpy, project, m, lyr, classification_field, num_classes, classification_method
    )
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "GraduatedColorsRenderer"},
    )


@tool(
    name="arcgis_pro_set_graduated_symbols_renderer",
    description="",
)
def arcgis_pro_set_graduated_symbols_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    classification_field: str,
    num_classes: int = 5,
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    _srv.symbology.set_graduated_symbols_renderer(
        arcpy, project, m, lyr, classification_field, num_classes
    )
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "GraduatedSymbolsRenderer"},
    )


@tool(
    name="arcgis_pro_set_simple_renderer",
    description="",
)
def arcgis_pro_set_simple_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    _srv.symbology.set_simple_renderer(arcpy, project, m, lyr)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "SimpleRenderer"},
    )


@tool(
    name="arcgis_pro_set_heatmap_renderer",
    description="",
)
def arcgis_pro_set_heatmap_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    _srv.symbology.set_heatmap_renderer(arcpy, project, m, lyr)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "HeatMapRenderer"},
    )


@tool(
    name="arcgis_pro_update_label_expression",
    description="",
)
def arcgis_pro_update_label_expression(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    expression: str,
    label_class_name: str = "",
    expression_engine: str = "Arcade",
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    _srv.symbology.update_label_expression(arcpy, lyr, expression, label_class_name, expression_engine)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name},
    )


@tool(
    name="arcgis_pro_set_label_font",
    description="",
)
def arcgis_pro_set_label_font(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    font_name: str = "",
    font_size: float | None = None,
    font_color: str = "",
    bold: bool | None = None,
    italic: bool | None = None,
    label_class_name: str = "",
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    _srv.symbology.set_label_font(arcpy, lyr, font_name, font_size, font_color, bold, italic, label_class_name)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name},
    )


@tool(
    name="arcgis_pro_export_report_pdf",
    description="",
)
def arcgis_pro_export_report_pdf(
    aprx_path: str,
    report_name: str,
    output_pdf_path: str,
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    _srv.symbology.export_report_pdf(arcpy, project, report_name, output_pdf_path)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "report_name": report_name,
         "output_pdf_path": _srv.normalize_path(output_pdf_path)},
    )


@tool(
    name="arcgis_pro_list_layout_map_frames",
    description="",
)
def arcgis_pro_list_layout_map_frames(
    aprx_path: str,
    layout_name: str,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    frames: list[dict[str, Any]] = []
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        entry: dict[str, Any] = {"name": elm.name}
        try:
            entry["map_name"] = elm.map.name
        except Exception:  # noqa: BLE001
            pass
        frames.append(entry)
    return _srv._json_dumps(
        {"aprx_path": path, "layout_name": layout_name, "map_frames": frames},
    )


@tool(
    name="arcgis_pro_set_layout_element_position",
    description="",
)
def arcgis_pro_set_layout_element_position(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    en = element_name.strip()
    elm = None
    for e in layout.listElements():
        if getattr(e, "name", "") == en:
            elm = e
            break
    if elm is None:
        names = [getattr(e, "name", "") for e in layout.listElements() if getattr(e, "name", "")]
        _srv._raise_not_found("layout element", en, names)
    updated: dict[str, float] = {}
    if x is not None:
        elm.elementPositionX = float(x)
        updated["x"] = float(x)
    if y is not None:
        elm.elementPositionY = float(y)
        updated["y"] = float(y)
    if width is not None:
        elm.elementWidth = float(width)
        updated["width"] = float(width)
    if height is not None:
        elm.elementHeight = float(height)
        updated["height"] = float(height)
    if not updated:
        raise RuntimeError("至少提供 x/y/width/height 之一")
    return _srv._json_dumps({"ok": True, "aprx_path": path, "element_name": en, "updated": updated})


@tool(
    name="arcgis_pro_set_layout_element_visible",
    description="",
)
def arcgis_pro_set_layout_element_visible(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    visible: bool,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    en = element_name.strip()
    elm = None
    for e in layout.listElements():
        if getattr(e, "name", "") == en:
            elm = e
            break
    if elm is None:
        names = [getattr(e, "name", "") for e in layout.listElements() if getattr(e, "name", "")]
        _srv._raise_not_found("layout element", en, names)
    elm.visible = bool(visible)
    return _srv._json_dumps({"ok": True, "aprx_path": path, "element_name": en, "visible": bool(visible)})


@tool(
    name="arcgis_pro_update_legend_items",
    description="",
)
def arcgis_pro_update_legend_items(
    aprx_path: str,
    layout_name: str,
    legend_name: str,
    layer_visibility: dict[str, bool],
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    legend = None
    for elm in layout.listElements("LEGEND_ELEMENT"):
        if elm.name == legend_name:
            legend = elm
            break
    if legend is None:
        names = [e.name for e in layout.listElements("LEGEND_ELEMENT")]
        _srv._raise_not_found("legend element", legend_name, names)
    items = legend.items
    updated_count = 0
    for item in items:
        ln = getattr(item, "name", "")
        if ln in layer_visibility:
            try:
                item.visible = bool(layer_visibility[ln])
                updated_count += 1
            except Exception as ex:  # noqa: BLE001
                raise RuntimeError(f"无法更新图例项 {ln!r}：{ex}") from ex
    missing = [name for name in layer_visibility if name not in {getattr(item, "name", "") for item in items}]
    if updated_count == 0:
        names = [getattr(item, "name", "") for item in items]
        raise RuntimeError(f"未找到可更新的图例项；请求 {list(layer_visibility)}，现有 {names}")
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "legend_name": legend_name,
            "items_updated": updated_count,
            "missing": missing,
        },
    )


@tool(
    name="arcgis_pro_create_layout",
    description="",
)
def arcgis_pro_create_layout(
    aprx_path: str,
    layout_name: str,
    page_width: float = 11.0,
    page_height: float = 8.5,
    page_units: str = "INCH",
    map_name: str = "",
    create_map_frame: bool = True,
    mapframe_name: str = "Map Frame",
) -> str:
    _srv.require_allow_write()
    arcpy, project, path = _srv._open_project(aprx_path)
    ln = layout_name.strip()
    if not ln:
        raise RuntimeError("layout_name 不能为空")
    w = max(1.0, min(float(page_width), 200.0))
    h = max(1.0, min(float(page_height), 200.0))
    units = (page_units or "INCH").strip().upper()
    if units not in {"INCH", "CENTIMETER", "MILLIMETER", "POINT"}:
        raise RuntimeError("page_units 须为 INCH/CENTIMETER/MILLIMETER/POINT")
    lyt = project.createLayout(w, h, units)
    lyt.name = ln
    map_frame = None
    maps = list(project.listMaps())
    target_map = _srv._get_map(project, map_name) if map_name.strip() else (maps[0] if maps else None)
    if create_map_frame and target_map is None:
        _srv._delete_project_item(project, lyt)
        raise RuntimeError("工程中没有可绑定的地图；请先创建地图或设置 create_map_frame=false")
    if create_map_frame:
        if not hasattr(lyt, "createMapFrame"):
            _srv._delete_project_item(project, lyt)
            raise RuntimeError("当前 ArcGIS Pro 不支持 Layout.createMapFrame")
        try:
            margin = min(0.5, w / 10, h / 10)
            points = [
                arcpy.Point(margin, margin),
                arcpy.Point(w - margin, margin),
                arcpy.Point(w - margin, h - margin),
                arcpy.Point(margin, h - margin),
                arcpy.Point(margin, margin),
            ]
            page_polygon = arcpy.Polygon(arcpy.Array(points))
            map_frame = lyt.createMapFrame(page_polygon, target_map, mapframe_name.strip() or "Map Frame")
            if hasattr(map_frame, "name"):
                map_frame.name = mapframe_name.strip() or "Map Frame"
        except Exception as ex:  # noqa: BLE001
            try:
                _srv._delete_project_item(project, lyt)
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"创建地图框失败，布局已回滚：{_srv._safe_error(ex, 500)}") from ex
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": ln,
            "layout_uri": _srv._object_uri(lyt),
            "page_width": w,
            "page_height": h,
            "page_units": units,
            "map_name": str(getattr(target_map, "name", "")) if target_map is not None else None,
            "mapframe_name": str(getattr(map_frame, "name", "")) if map_frame is not None else None,
            "verified": lyt in list(project.listLayouts()),
        }
    )


@tool(
    name="arcgis_pro_rename_layout",
    description="",
)
def arcgis_pro_rename_layout(
    aprx_path: str,
    layout_name: str,
    new_layout_name: str,
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    new_name = new_layout_name.strip()
    if not new_name:
        raise RuntimeError("new_layout_name cannot be empty")
    layout.name = new_name
    return _srv._json_dumps({"ok": True, "aprx_path": path, "layout_name": layout_name, "new_layout_name": new_name})


@tool(
    name="arcgis_pro_remove_layout",
    description="",
)
def arcgis_pro_remove_layout(aprx_path: str, layout_name: str, confirm: bool = False) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除布局需要 confirm=true")
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    _srv._delete_project_item(project, layout)
    return _srv._json_dumps({"ok": True, "aprx_path": path, "removed": layout_name})


@tool(
    name="arcgis_pro_export_map_to_image",
    description="",
)
def arcgis_pro_export_map_to_image(
    aprx_path: str,
    map_name: str,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    resolution_dpi: int = 96,
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    out = _srv.symbology.export_map_to_image(arcpy, m, output_path, width, height, resolution_dpi)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "output_path": out},
    )


@tool(
    name="arcgis_pro_list_layer_renderers",
    description="",
)
def arcgis_pro_list_layer_renderers(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    m = _srv._get_map(project, map_name)
    lyr = _srv._find_layer(m, layer_name)
    info = _srv.symbology.symbology_info(lyr)
    info.update({"aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _srv._json_dumps(info)


@tool(
    name="arcgis_pro_set_raster_stretch_colorizer",
    description="为栅格图层设置 Stretch colorizer、波段、gamma、裁剪比例和可选色带。",
)
def arcgis_pro_set_raster_stretch_colorizer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    stretch_type: str = "MinimumMaximum",
    band: int = 0,
    gamma: float = 1.0,
    invert_color_ramp: bool = False,
    min_percent: float = 0.0,
    max_percent: float = 0.0,
    standard_deviation: float = 2.0,
    color_ramp_name: str = "",
    color_ramp_index: int = 0,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layer = _srv._find_layer(_srv._get_map(project, map_name), layer_name)
    result = _srv.symbology.set_raster_stretch_colorizer(
        project,
        layer,
        stretch_type=stretch_type,
        band=band,
        gamma=gamma,
        invert_color_ramp=invert_color_ramp,
        min_percent=min_percent,
        max_percent=max_percent,
        standard_deviation=standard_deviation,
        color_ramp_name=color_ramp_name,
        color_ramp_index=color_ramp_index,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_set_raster_classify_colorizer",
    description="为栅格图层设置分级字段、分级数、分类方法和可选色带。",
)
def arcgis_pro_set_raster_classify_colorizer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    classification_field: str,
    break_count: int = 5,
    classification_method: str = "NaturalBreaks",
    color_ramp_name: str = "",
    color_ramp_index: int = 0,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layer = _srv._find_layer(_srv._get_map(project, map_name), layer_name)
    result = _srv.symbology.set_raster_classify_colorizer(
        project,
        layer,
        classification_field,
        break_count=break_count,
        classification_method=classification_method,
        color_ramp_name=color_ramp_name,
        color_ramp_index=color_ramp_index,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_set_raster_unique_value_colorizer",
    description="为栅格图层设置唯一值字段和可选色带。",
)
def arcgis_pro_set_raster_unique_value_colorizer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    field: str,
    color_ramp_name: str = "",
    color_ramp_index: int = 0,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layer = _srv._find_layer(_srv._get_map(project, map_name), layer_name)
    result = _srv.symbology.set_raster_unique_value_colorizer(
        project,
        layer,
        field,
        color_ramp_name=color_ramp_name,
        color_ramp_index=color_ramp_index,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_apply_gallery_symbol",
    description="把项目符号库中的匹配符号应用到 SimpleRenderer，并以 index 消除重名歧义。",
)
def arcgis_pro_apply_gallery_symbol(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    wildcard: str,
    index: int = 0,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    layer = _srv._find_layer(_srv._get_map(project, map_name), layer_name)
    result = _srv.symbology.apply_gallery_symbol(layer, wildcard, index)
    result.update({"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_layout_element_info",
    description="读取布局元素的 URI、重复序号、位置、尺寸、旋转、文本、父组及地图绑定。",
)
def arcgis_pro_layout_element_info(
    aprx_path: str,
    layout_name: str,
    element_type: str = "",
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    elements = _srv.cartography.layout_element_info(_srv._get_layout(project, layout_name), element_type)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "layout_name": layout_name, "count": len(elements), "elements": elements}
    )


@tool(
    name="arcgis_pro_create_layout_from_template",
    description="从受控输入根内的 .pagx 模板导入一个布局，并唯一核验新布局。",
)
def arcgis_pro_create_layout_from_template(
    aprx_path: str,
    template_path: str,
    layout_name: str = "",
    reuse_existing_maps: bool = True,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.create_layout_from_template(
        project,
        template_path,
        layout_name,
        reuse_existing_maps,
    )
    result.pop("layout", None)
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_bind_map_frame", description="将布局地图框显式绑定到地图并读回 URI 进行核验。")
def arcgis_pro_bind_map_frame(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    map_name: str,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.bind_map_frame(
        _srv._get_layout(project, layout_name),
        mapframe_name,
        _srv._get_map(project, map_name),
    )
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_layout_upsert_element",
    description="语义化创建或更新文本、图片、地图框、图例、比例尺、指北针、网格或表框元素。",
)
def arcgis_pro_layout_upsert_element(
    aprx_path: str,
    layout_name: str,
    element_type: str,
    name: str,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
    rotation: float | None = None,
    visible: bool | None = None,
    text: str | None = None,
    text_size: float = 10,
    font_family_name: str = "Arial",
    font_style_name: str = "Regular",
    picture_path: str = "",
    map_name: str = "",
    mapframe_name: str = "",
    table_name: str = "",
    fields: list[str] | None = None,
    style: str = "",
    style_category: str = "",
    style_name: str = "",
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    map_obj = _srv._get_map(project, map_name) if map_name.strip() else None
    map_frame = _srv._get_mapframe(layout, mapframe_name) if mapframe_name.strip() else None
    table = _srv._get_table(map_frame.map if map_frame is not None else map_obj, table_name) if table_name.strip() else None
    style_item = _srv._get_style_item(project, style, style_category, style_name)
    result = _srv.cartography.upsert_layout_element(
        arcpy,
        project,
        layout,
        element_type,
        name,
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        visible=visible,
        text=text,
        text_size=text_size,
        font_family_name=font_family_name,
        font_style_name=font_style_name,
        picture_path=picture_path,
        map_obj=map_obj,
        map_frame=map_frame,
        table=table,
        fields=fields,
        style_item=style_item,
    )
    result.pop("element", None)
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_delete_layout_element", description="按唯一名称/URI 删除布局元素；需要破坏性门禁和 confirm=true。")
def arcgis_pro_delete_layout_element(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    element_type: str = "",
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除布局元素需要 confirm=true")
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.delete_layout_element(
        _srv._get_layout(project, layout_name), element_name, element_type
    )
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_map_series_info", description="读取布局 MapSeries 类型、页数、当前页、索引图层和选择页。")
def arcgis_pro_map_series_info(aprx_path: str, layout_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.map_series_info(_srv._get_layout(project, layout_name))
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_set_map_series_page", description="按页码或页名切换布局 MapSeries 当前页。")
def arcgis_pro_set_map_series_page(
    aprx_path: str,
    layout_name: str,
    page_number: int | None = None,
    page_name: str = "",
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.set_map_series_page(
        _srv._get_layout(project, layout_name), page_number=page_number, page_name=page_name
    )
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_refresh_map_series", description="刷新 MapSeries 索引状态并返回刷新后的页信息。")
def arcgis_pro_refresh_map_series(aprx_path: str, layout_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.refresh_map_series(_srv._get_layout(project, layout_name))
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_export_map_series_pdf", description="将 MapSeries 的全部、当前、范围或选择页导出为受控 PDF。")
def arcgis_pro_export_map_series_pdf(
    aprx_path: str,
    layout_name: str,
    output_path: str,
    page_range_type: str = "ALL",
    page_range_string: str = "",
    multiple_files: str = "PDF_SINGLE_FILE",
    resolution_dpi: int = 300,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.export_map_series_pdf(
        _srv._get_layout(project, layout_name),
        output_path,
        page_range_type=page_range_type,
        page_range_string=page_range_string,
        multiple_files=multiple_files,
        resolution_dpi=resolution_dpi,
    )
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_list_label_classes", description="列出图层标注类的名称、表达式、SQL、语言和可见状态。")
def arcgis_pro_list_label_classes(aprx_path: str, map_name: str, layer_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    values = _srv.cartography.list_label_classes(_srv._find_layer(_srv._get_map(project, map_name), layer_name))
    return _srv._json_dumps({"ok": True, "aprx_path": path, "label_classes": values, "count": len(values)})


@tool(name="arcgis_pro_upsert_label_class", description="创建或更新标注类的表达式、过滤 SQL、语言和可见状态。")
def arcgis_pro_upsert_label_class(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    name: str,
    expression: str,
    sql_query: str = "",
    language: str = "ARCADE",
    visible: bool | None = None,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.upsert_label_class(
        _srv._find_layer(_srv._get_map(project, map_name), layer_name),
        name,
        expression,
        sql_query=sql_query,
        language=language,
        visible=visible,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_mapframe_time_info",
    description="读取布局地图框的 MapTime 状态和当前时间范围；MapTime 不代表活动 MapView 时间滑块。",
)
def arcgis_pro_mapframe_time_info(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    mapframe = _srv._get_mapframe(_srv._get_layout(project, layout_name), mapframe_name)
    map_time = getattr(mapframe, "time", None)
    if map_time is None:
        raise RuntimeError("该地图框没有可用的 MapTime")
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "mapframe_name": str(getattr(mapframe, "name", mapframe_name)),
            "is_time_enabled": bool(getattr(map_time, "isTimeEnabled", False)),
            "current_time_start": getattr(map_time, "currentTimeStart", None),
            "current_time_end": getattr(map_time, "currentTimeEnd", None),
            "current_time_span": getattr(map_time, "currentTimeSpan", None),
            "current_time_span_units": getattr(map_time, "currentTimeSpanUnits", None),
            "time_inclusion": getattr(map_time, "timeInclusion", None),
        }
    )


@tool(
    name="arcgis_pro_set_mapframe_time",
    description="设置布局地图框的 MapTime 开关、ISO-8601 起止时间和端点包含方式。",
)
def arcgis_pro_set_mapframe_time(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    enabled: bool = True,
    start_time: str = "",
    end_time: str = "",
    time_inclusion: str = "INCLUDE_START_AND_END",
) -> str:
    _srv.require_allow_write()
    _, project, path = _srv._open_project(aprx_path)
    mapframe = _srv._get_mapframe(_srv._get_layout(project, layout_name), mapframe_name)
    map_time = getattr(mapframe, "time", None)
    if map_time is None:
        raise RuntimeError("该地图框没有可用的 MapTime")
    inclusion = time_inclusion.strip().upper()
    valid = {
        "INCLUDE_START_AND_END",
        "INCLUDE_ONLY_START",
        "INCLUDE_ONLY_END",
        "EXCLUDE_START_AND_END",
    }
    if inclusion not in valid:
        raise RuntimeError(f"time_inclusion 须为 {sorted(valid)}")
    start = _srv._parse_iso_datetime(start_time, "start_time") if start_time else None
    end = _srv._parse_iso_datetime(end_time, "end_time") if end_time else None
    if start is not None and end is not None and start > end:
        raise RuntimeError("start_time 不能晚于 end_time")
    map_time.isTimeEnabled = bool(enabled)
    # Esri recommends moving the end first when advancing to a future span.
    if end is not None:
        map_time.currentTimeEnd = end
    if start is not None:
        map_time.currentTimeStart = start
    if hasattr(map_time, "setTimeInclusion"):
        map_time.setTimeInclusion(inclusion)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "mapframe_name": str(getattr(mapframe, "name", mapframe_name)),
            "is_time_enabled": bool(getattr(map_time, "isTimeEnabled", enabled)),
            "current_time_start": getattr(map_time, "currentTimeStart", start),
            "current_time_end": getattr(map_time, "currentTimeEnd", end),
            "time_inclusion": getattr(map_time, "timeInclusion", inclusion),
        }
    )


def _publishing_arcpy(aprx_path: str) -> tuple[Any, str]:
    target = str(aprx_path or "").strip()
    if not target:
        return _srv._arcpy(), ""
    arcpy, _project, opened_path = _srv._open_project(target)
    return arcpy, opened_path


@tool(
    name="arcgis_pro_portal_status",
    description="读取活动 Portal、登录状态和白名单匹配结果；不返回用户名、密码或 token。",
)
def arcgis_pro_portal_status(aprx_path: str = "") -> str:
    arcpy, opened_path = _srv._publishing_arcpy(aprx_path)
    result = _srv.publishing.portal_status(arcpy)
    result["ok"] = True
    if opened_path:
        result["aprx_path"] = opened_path
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_get_artifact_digest",
    description="读取受控导出根内发布工件的 SHA-256 和字节数，用于阶段间完整性核验。",
)
def arcgis_pro_get_artifact_digest(artifact_path: str) -> str:
    result = _srv.publishing.artifact_digest(artifact_path)
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_create_sharing_draft",
    description=(
        "为地图、单一图层/表、数据集或 GP 结果引用创建受控 sharing draft，"
        "可同时运行该 draft 类型支持的分析器。"
    ),
)
def arcgis_pro_create_sharing_draft(
    aprx_path: str,
    map_name: str,
    service_name: str,
    output_sddraft_path: str,
    member_name: str = "",
    member_type: str = "MAP",
    server_type: str = "HOSTING_SERVER",
    service_type: str = "FEATURE",
    portal_url: str = "",
    federated_server_url: str = "",
    server_connection: str = "",
    sharing_level: str = "OWNER",
    groups: list[str] | None = None,
    overwrite_existing_service: bool = False,
    portal_folder: str = "",
    server_folder: str = "",
    summary: str = "",
    tags: list[str] | None = None,
    description: str = "",
    credits: str = "",
    use_limitations: str = "",
    copy_data_to_server: bool | None = None,
    analyze: bool = True,
) -> str:
    source_kind = _srv._strict_member_type(
        member_type,
        {"MAP", "LAYER", "TABLE", "DATASET", "GP_RESULT_REFERENCE"},
    )
    service_kind = str(service_type or "").strip().upper()
    web_layer_services = {"FEATURE", "TILE", "VECTOR_TILE", "SCENE_LAYER", "MAP_IMAGE"}
    if service_kind in web_layer_services and source_kind not in {"MAP", "LAYER", "TABLE"}:
        raise RuntimeError(f"{service_kind} 的 member_type 须为 MAP、LAYER 或 TABLE")
    if service_kind == "MAP_SERVICE" and source_kind != "MAP":
        raise RuntimeError("MAP_SERVICE 的 member_type 必须为 MAP")
    if service_kind in {"IMAGE_SERVICE", "WEB_IMAGERY_LAYER"} and source_kind != "DATASET":
        raise RuntimeError(f"{service_kind} 的 member_type 必须为 DATASET")
    if service_kind in {"GP_SERVICE", "WEB_TOOL"} and source_kind != "GP_RESULT_REFERENCE":
        raise RuntimeError(f"{service_kind} 的 member_type 必须为 GP_RESULT_REFERENCE")
    supported_services = web_layer_services | {
        "MAP_SERVICE",
        "IMAGE_SERVICE",
        "WEB_IMAGERY_LAYER",
        "GP_SERVICE",
        "WEB_TOOL",
    }
    if service_kind not in supported_services:
        raise RuntimeError(f"service_type 须为 {sorted(supported_services)}")

    arcpy, project, opened_path = _srv._open_project(aprx_path)
    if source_kind == "MAP":
        source = _srv._get_map(project, map_name)
    elif source_kind in {"LAYER", "TABLE"}:
        map_obj, member = _srv._get_map_member(project, map_name, member_name, source_kind)
        source = _srv._ScopedWebLayerSharingSource(map_obj, member)
    elif source_kind == "DATASET":
        source = member_name
    else:
        source = _srv.session_refs.resolve(member_name, expected_kinds={"gp_result"})

    result = _srv.publishing.create_sharing_draft(
        arcpy,
        source,
        service_name,
        output_sddraft_path,
        server_type=server_type,
        service_type=service_kind,
        portal_url=portal_url,
        federated_server_url=federated_server_url,
        server_connection=server_connection,
        sharing_level=sharing_level,
        groups=groups,
        overwrite_existing_service=overwrite_existing_service,
        portal_folder=portal_folder,
        server_folder=server_folder,
        summary=summary,
        tags=tags,
        description=description,
        credits=credits,
        use_limitations=use_limitations,
        copy_data_to_server=copy_data_to_server,
        analyze=analyze,
    )
    result.update(
        {
            "ok": True,
            "aprx_path": opened_path,
            "source": {
                "member_type": source_kind,
                "map_name": map_name if source_kind in {"MAP", "LAYER", "TABLE"} else "",
                "member_name": member_name if source_kind in {"LAYER", "TABLE"} else "",
            },
        }
    )
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_stage_service_definition",
    description="校验 SDDraft 摘要后执行 StageService 分析与暂存，输出受控 .sd 及新摘要。",
)
def arcgis_pro_stage_service_definition(
    sddraft_path: str,
    output_sd_path: str,
    expected_sha256: str,
    sharing_level: str = "OWNER",
    overwrite_existing_service: bool = False,
    staging_version: int | None = None,
    aprx_path: str = "",
) -> str:
    arcpy, opened_path = _srv._publishing_arcpy(aprx_path)
    result = _srv.publishing.stage_service_definition(
        arcpy,
        sddraft_path,
        output_sd_path,
        expected_sha256=expected_sha256,
        sharing_level=sharing_level,
        overwrite_existing_service=overwrite_existing_service,
        staging_version=staging_version,
    )
    result["ok"] = True
    if opened_path:
        result["aprx_path"] = opened_path
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_publish_service_definition",
    description="校验 .sd 摘要后仅向精确白名单 Portal/Server 上传，不接受或返回凭据。",
)
def arcgis_pro_publish_service_definition(
    sd_path: str,
    expected_sha256: str,
    server_type: str = "HOSTING_SERVER",
    portal_url: str = "",
    federated_server_url: str = "",
    server_connection: str = "",
    sharing_level: str = "OWNER",
    overwrite_existing_service: bool = False,
    aprx_path: str = "",
) -> str:
    arcpy, opened_path = _srv._publishing_arcpy(aprx_path)
    result = _srv.publishing.publish_service_definition(
        arcpy,
        sd_path,
        expected_sha256=expected_sha256,
        server_type=server_type,
        portal_url=portal_url,
        federated_server_url=federated_server_url,
        server_connection=server_connection,
        sharing_level=sharing_level,
        overwrite_existing_service=overwrite_existing_service,
    )
    result["ok"] = True
    if opened_path:
        result["aprx_path"] = opened_path
    return _srv._json_dumps(result)


def _chart_member(
    aprx_path: str,
    map_name: str,
    member_name: str,
    member_type: str,
) -> tuple[Any, Any, str, str]:
    kind = _srv._strict_member_type(member_type, {"LAYER", "TABLE"})
    arcpy, project, opened_path = _srv._open_project(aprx_path)
    _map_obj, member = _srv._get_map_member(project, map_name, member_name, kind)
    return arcpy, member, opened_path, kind


@tool(name="arcgis_pro_list_charts", description="列出图层或独立表上的 typed ArcPy 图表摘要。")
def arcgis_pro_list_charts(
    aprx_path: str,
    map_name: str,
    member_name: str,
    member_type: str = "LAYER",
) -> str:
    _arcpy_module, member, opened_path, kind = _srv._chart_member(
        aprx_path, map_name, member_name, member_type
    )
    values = _srv.charts.list_charts(member)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": opened_path,
            "map_name": map_name,
            "member_type": kind,
            "member_name": member_name,
            "count": len(values),
            "charts": values,
        }
    )


@tool(name="arcgis_pro_chart_info", description="按唯一标题读取一个 typed ArcPy 图表的字段、轴和显示属性。")
def arcgis_pro_chart_info(
    aprx_path: str,
    map_name: str,
    member_name: str,
    title: str,
    member_type: str = "LAYER",
) -> str:
    _arcpy_module, member, opened_path, kind = _srv._chart_member(
        aprx_path, map_name, member_name, member_type
    )
    requested = str(title or "").strip()
    if not requested:
        raise RuntimeError("title 不能为空")
    matches = [value for value in _srv.charts.list_charts(member) if value.get("title") == requested]
    if not matches:
        raise RuntimeError(f"未找到图表 {requested!r}")
    if len(matches) > 1:
        raise RuntimeError(f"图表标题不唯一：{requested!r}")
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": opened_path,
            "map_name": map_name,
            "member_type": kind,
            "member_name": member_name,
            "chart": matches[0],
        }
    )


@tool(name="arcgis_pro_upsert_chart", description="按标题创建或更新 Bar/Line/Scatter/Histogram/Pie 图表；不做 CIM 猜测删除。")
def arcgis_pro_upsert_chart(
    aprx_path: str,
    map_name: str,
    member_name: str,
    chart_type: str,
    title: str,
    member_type: str = "LAYER",
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
) -> str:
    arcpy, member, opened_path, kind = _srv._chart_member(
        aprx_path, map_name, member_name, member_type
    )
    result = _srv.charts.upsert_chart(
        arcpy,
        member,
        chart_type,
        title,
        x=x,
        y=y,
        category_field=category_field,
        number_fields=number_fields,
        aggregation=aggregation,
        split_category=split_category,
        description=description,
        x_title=x_title,
        y_title=y_title,
        theme=theme,
        rotated=rotated,
        show_trend_line=show_trend_line,
        bin_count=bin_count,
        show_mean=show_mean,
        show_median=show_median,
        show_standard_deviation=show_standard_deviation,
        donut_size=donut_size,
        grouping_percent=grouping_percent,
        show_data_labels=show_data_labels,
        validate_fields=validate_fields,
    )
    result.pop("chart", None)
    result.update(
        {
            "ok": True,
            "aprx_path": opened_path,
            "map_name": map_name,
            "member_type": kind,
            "member_name": member_name,
        }
    )
    return _srv._json_dumps(result)


@tool(name="arcgis_pro_export_chart", description="将唯一标题的图表导出为受控 SVG、PNG 或 JPEG。")
def arcgis_pro_export_chart(
    aprx_path: str,
    map_name: str,
    member_name: str,
    title: str,
    output_path: str,
    member_type: str = "LAYER",
    width: int = 800,
    height: int = 600,
) -> str:
    _arcpy_module, member, opened_path, kind = _srv._chart_member(
        aprx_path, map_name, member_name, member_type
    )
    result = _srv.charts.export_chart(member, title, output_path, width=width, height=height)
    result.update(
        {
            "ok": True,
            "aprx_path": opened_path,
            "map_name": map_name,
            "member_type": kind,
            "member_name": member_name,
        }
    )
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_chart_mutation_capabilities",
    description="读取图表变更边界；当前明确拒绝没有受支持 ArcPy API 的图表删除。",
)
def arcgis_pro_chart_mutation_capabilities() -> str:
    result = _srv.charts.chart_delete_capability()
    return _srv._json_dumps({"ok": True, "delete": result})


@tool(
    name="arcgis_pro_create_report",
    description="使用 typed ArcGISProject.createReport 从图层、表或受控路径创建报表。",
)
def arcgis_pro_create_report(
    aprx_path: str,
    map_name: str,
    source_kind: str,
    source_identifier: str,
    report_name: str,
    fields: list[dict[str, Any]],
    statistics: list[dict[str, Any]] | None = None,
    width: float = 8.5,
    height: float = 11.0,
    units: str = "INCH",
    margins: str = "NORMAL",
    template: str = "ATTR_LIST",
    styling: str = "BLACK_AND_WHITE",
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    source = _srv._project_data_source(project, map_name, source_kind, source_identifier)
    result = _srv.project_io.create_report(
        project,
        source,
        name=report_name,
        fields=fields,
        statistics=statistics,
        width=width,
        height=height,
        units=units,
        margins=margins,
        template=template,
        styling=styling,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_report_sections",
    description="读取报表的报告节/布局节、字段、统计、过滤器、可见性和脱敏数据源。",
)
def arcgis_pro_report_sections(aprx_path: str, report_name: str) -> str:
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.project_io.report_sections(_srv._get_report(project, report_name))
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_update_report_section",
    description="更新唯一 REPORT_SECTION 的数据源、definitionQuery 或可见性并读回全部节。",
)
def arcgis_pro_update_report_section(
    aprx_path: str,
    report_name: str,
    section_name: str,
    map_name: str = "",
    source_kind: str = "",
    source_identifier: str = "",
    definition_query: str | None = None,
    visible: bool | None = None,
) -> str:
    _, project, path = _srv._open_project(aprx_path)
    source = None
    if source_kind.strip() or source_identifier.strip():
        if not source_kind.strip() or not source_identifier.strip():
            raise RuntimeError("更新报表数据源时 source_kind/source_identifier 必须同时提供")
        source = _srv._project_data_source(project, map_name, source_kind, source_identifier)
    result = _srv.project_io.update_report_section(
        _srv._get_report(project, report_name),
        section_name,
        data_source=source,
        definition_query=definition_query,
        visible=visible,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)
