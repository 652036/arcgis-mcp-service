"""ArcGIS Pro cartography helpers with no import-time ArcPy dependency.

The functions in this module deliberately accept ArcPy/project objects from the
caller.  This keeps ordinary Python imports and mock tests usable while making
the in-process-only boundary (for example ``openView``) explicit.
"""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.arcade import validate_safe_arcade_expression
from arcgis_pro_mcp.paths import (
    require_allow_cim_write,
    require_allow_write,
    validate_input_path_optional,
    validate_new_output_in_export_root,
)
from arcgis_pro_mcp.redaction import redact_sensitive as _redact_secrets

_UNSET = object()


def _required_text(value: Any, label: str, max_length: int = 512) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"{label} 不能为空")
    if len(text) > max_length:
        raise RuntimeError(f"{label} 不能超过 {max_length} 个字符")
    return text


def _object_name(value: Any) -> str:
    return str(getattr(value, "name", "") or "")


def _object_uri(value: Any) -> str:
    return str(getattr(value, "URI", "") or getattr(value, "uri", "") or "")


def _object_key(value: Any) -> tuple[str, int]:
    uri = _object_uri(value)
    return (uri, 0) if uri else ("", id(value))


def _select_unique(items: list[Any], identifier: str, label: str) -> Any:
    key = _required_text(identifier, label)
    uri_matches = [item for item in items if _object_uri(item) == key]
    if len(uri_matches) == 1:
        return uri_matches[0]
    if len(uri_matches) > 1:
        raise RuntimeError(f"{label} URI 不唯一：{key!r}")

    name_matches = [
        item
        for item in items
        if key in {_object_name(item), str(getattr(item, "longName", "") or "")}
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        choices = [_object_uri(item) or _object_name(item) for item in name_matches]
        raise RuntimeError(f"{label} 名称不唯一，请改用 URI：{choices}")
    choices = [_object_name(item) for item in items]
    raise RuntimeError(f"未找到{label} {key!r}，可选：{choices}")


def _require_method(value: Any, method_name: str, context: str) -> Any:
    method = getattr(value, method_name, None)
    if not callable(method):
        raise RuntimeError(
            f"{context}需要 ArcGIS Pro 进程内的 {method_name} 能力；"
            "请使用已接入的 CURRENT 窗口，并确认当前 Pro 版本支持该 API。"
        )
    return method


def _existing_input_file(path: str, label: str, suffixes: tuple[str, ...]) -> str:
    resolved = validate_input_path_optional(path, label)
    if not resolved.lower().endswith(suffixes):
        readable = "/".join(suffixes)
        raise RuntimeError(f"{label} 必须使用 {readable} 扩展名")
    if not os.path.isfile(resolved):
        raise RuntimeError(f"{label} 不存在或不是文件：{resolved}")
    return resolved


def _validated_export(path: str, label: str, suffixes: tuple[str, ...]) -> str:
    resolved = validate_new_output_in_export_root(path, label)
    if not resolved.lower().endswith(suffixes):
        readable = "/".join(suffixes)
        raise RuntimeError(f"{label} 必须使用 {readable} 扩展名")
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return resolved


def _bounded_int(value: Any, label: str, low: int, high: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 必须为整数") from exc
    if not low <= result <= high:
        raise RuntimeError(f"{label} 必须在 {low} 到 {high} 之间")
    return result


def _bounded_float(value: Any, label: str, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 必须为数字") from exc
    if not low <= result <= high:
        raise RuntimeError(f"{label} 必须在 {low} 到 {high} 之间")
    return result


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    name = getattr(value, "name", None)
    return str(name) if name is not None else str(value)


# ---------------------------------------------------------------------------
# Map frames and the active Pro view


def find_map_frame(layout: Any, identifier: str) -> Any:
    """Return one map frame selected by URI, name, or longName."""

    return _select_unique(list(layout.listElements("MAPFRAME_ELEMENT") or []), identifier, "地图框")


def extent_info(extent: Any) -> dict[str, Any]:
    if extent is None:
        raise RuntimeError("extent 为空")
    result = {
        "xmin": float(extent.XMin),
        "ymin": float(extent.YMin),
        "xmax": float(extent.XMax),
        "ymax": float(extent.YMax),
    }
    spatial_reference = getattr(extent, "spatialReference", None)
    if spatial_reference is not None:
        result["spatial_reference"] = {
            "name": str(getattr(spatial_reference, "name", "") or ""),
            "wkid": getattr(spatial_reference, "factoryCode", None),
        }
    return result


def get_map_frame_extent(map_frame: Any) -> dict[str, Any]:
    """Read a map frame extent through its camera, with an older-API fallback."""

    camera = getattr(map_frame, "camera", None)
    getter = getattr(camera, "getExtent", None) if camera is not None else None
    if not callable(getter):
        getter = getattr(map_frame, "getExtent", None)
    if not callable(getter):
        raise RuntimeError("当前地图框没有可用的 camera.getExtent/getExtent API")
    return extent_info(getter())


def _new_extent(
    arcpy: Any,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    spatial_reference_wkid: int | None,
) -> Any:
    x_min = float(xmin)
    y_min = float(ymin)
    x_max = float(xmax)
    y_max = float(ymax)
    if x_min >= x_max or y_min >= y_max:
        raise RuntimeError("extent 必须满足 xmin < xmax 且 ymin < ymax")
    extent_factory = getattr(arcpy, "Extent", None)
    if not callable(extent_factory):
        raise RuntimeError("注入的 ArcPy 对象不支持 Extent")
    if spatial_reference_wkid is None:
        return extent_factory(x_min, y_min, x_max, y_max)
    wkid = _bounded_int(spatial_reference_wkid, "spatial_reference_wkid", 1, 999999)
    sr_factory = getattr(arcpy, "SpatialReference", None)
    if not callable(sr_factory):
        raise RuntimeError("注入的 ArcPy 对象不支持 SpatialReference")
    spatial_reference = sr_factory(wkid)
    return extent_factory(
        x_min,
        y_min,
        x_max,
        y_max,
        None,
        None,
        None,
        None,
        spatial_reference,
    )


def apply_map_frame_extent(map_frame: Any, extent: Any) -> dict[str, Any]:
    """Apply a prebuilt extent and read it back."""

    require_allow_write()
    camera = getattr(map_frame, "camera", None)
    setter = getattr(camera, "setExtent", None) if camera is not None else None
    if not callable(setter):
        setter = getattr(map_frame, "setExtent", None)
    if not callable(setter):
        raise RuntimeError("当前地图框没有可用的 camera.setExtent/setExtent API")
    setter(extent)
    return get_map_frame_extent(map_frame)


def set_map_frame_extent(
    arcpy: Any,
    map_frame: Any,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    spatial_reference_wkid: int | None = None,
) -> dict[str, Any]:
    require_allow_write()
    extent = _new_extent(arcpy, xmin, ymin, xmax, ymax, spatial_reference_wkid)
    return apply_map_frame_extent(map_frame, extent)


def export_active_view(
    project: Any,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    resolution_dpi: int = 96,
    jpeg_quality: int = 90,
    transparent_background: bool = False,
) -> dict[str, Any]:
    """Export the active MapView or Layout to PNG/JPEG/TIFF."""

    require_allow_write()
    view = getattr(project, "activeView", None)
    if view is None:
        raise RuntimeError("当前工程没有 activeView；请先在 ArcGIS Pro 中激活地图或布局视图")
    out = _validated_export(output_path, "output_path", (".png", ".jpg", ".jpeg", ".tif", ".tiff"))
    w = _bounded_int(width, "width", 100, 16384)
    h = _bounded_int(height, "height", 100, 16384)
    dpi = _bounded_int(resolution_dpi, "resolution_dpi", 24, 1200)
    quality = _bounded_int(jpeg_quality, "jpeg_quality", 1, 100)
    is_map_view = getattr(view, "camera", None) is not None
    common: dict[str, Any] = {"resolution": dpi}
    if is_map_view:
        common.update({"width": w, "height": h})

    lower = out.lower()
    if lower.endswith(".png"):
        method = _require_method(view, "exportToPNG", "导出当前视图")
        if not is_map_view:
            common["transparent_background"] = bool(transparent_background)
        method(out, **common)
        output_format = "PNG"
    elif lower.endswith((".jpg", ".jpeg")):
        method = _require_method(view, "exportToJPEG", "导出当前视图")
        method(out, jpeg_quality=quality, **common)
        output_format = "JPEG"
    else:
        method = _require_method(view, "exportToTIFF", "导出当前视图")
        if not is_map_view:
            common["transparent_background"] = bool(transparent_background)
        method(out, **common)
        output_format = "TIFF"
    return {
        "output_path": out,
        "format": output_format,
        "view_type": "MAP_VIEW" if is_map_view else "LAYOUT",
        "resolution_dpi": dpi,
    }


def open_table_view(
    target_or_map: Any,
    table_identifier: str = "",
    *,
    show_selected: bool = False,
) -> dict[str, Any]:
    """Open a layer attribute table in the Pro UI (CURRENT/in-process only).

    ArcPy exposes ``Layer.openTableView`` but not the equivalent operation on a
    standalone ``Table``.  A caller can pass a layer directly, or a map plus a
    URI/name.  Selecting a standalone table fails explicitly so a future SDK
    add-in adapter can own that UI-only capability.
    """

    require_allow_write()
    if str(table_identifier or "").strip():
        candidates = []
        list_layers = getattr(target_or_map, "listLayers", None)
        list_tables = getattr(target_or_map, "listTables", None)
        if callable(list_layers):
            candidates.extend(list(list_layers() or []))
        if callable(list_tables):
            candidates.extend(list(list_tables() or []))
        target = _select_unique(candidates, table_identifier, "图层或独立表")
    else:
        target = target_or_map
    method = getattr(target, "openTableView", None)
    if not callable(method):
        raise RuntimeError(
            "ArcPy 仅为 Layer 提供 openTableView；打开独立 Table 需要 ArcGIS Pro SDK Add-in 桥接。"
        )
    method(show_selected=bool(show_selected))
    return {
        "name": _object_name(target),
        "uri": _object_uri(target),
        "opened": True,
        "show_selected": bool(show_selected),
    }


# ---------------------------------------------------------------------------
# Bookmarks


def _bookmark_info(bookmark: Any) -> dict[str, Any]:
    return {
        "name": _object_name(bookmark),
        "description": str(getattr(bookmark, "description", "") or ""),
    }


def list_bookmarks(map_obj: Any) -> list[dict[str, Any]]:
    return [_bookmark_info(item) for item in list(map_obj.listBookmarks() or [])]


def _source_map(source: Any) -> Any | None:
    map_obj = getattr(source, "map", None)
    if map_obj is not None:
        return map_obj
    return source if callable(getattr(source, "listBookmarks", None)) else None


def create_bookmark(source_view_or_map_frame: Any, name: str, description: str = "") -> dict[str, Any]:
    require_allow_write()
    bookmark_name = _required_text(name, "name", 256)
    desc = str(description or "")
    map_obj = _source_map(source_view_or_map_frame)
    if map_obj is not None:
        duplicates = [item for item in map_obj.listBookmarks() or [] if _object_name(item) == bookmark_name]
        if duplicates:
            raise RuntimeError(f"书签名称已存在：{bookmark_name!r}")
    method = _require_method(source_view_or_map_frame, "createBookmark", "创建书签")
    bookmark = method(bookmark_name, desc)
    if bookmark is None and map_obj is not None:
        bookmark = _select_unique(list(map_obj.listBookmarks() or []), bookmark_name, "书签")
    if bookmark is None:
        return {"name": bookmark_name, "description": desc}
    return _bookmark_info(bookmark)


def update_bookmark(
    map_obj: Any,
    bookmark_identifier: str,
    *,
    new_name: str | None = None,
    description: str | None = None,
    update_thumbnail: bool = False,
) -> dict[str, Any]:
    require_allow_write()
    bookmarks = list(map_obj.listBookmarks() or [])
    bookmark = _select_unique(bookmarks, bookmark_identifier, "书签")
    if new_name is not None:
        renamed = _required_text(new_name, "new_name", 256)
        if any(item is not bookmark and _object_name(item) == renamed for item in bookmarks):
            raise RuntimeError(f"书签名称已存在：{renamed!r}")
        bookmark.name = renamed
    if description is not None:
        bookmark.description = str(description)
    if update_thumbnail:
        _require_method(bookmark, "updateThumbnail", "更新书签缩略图")()
    return _bookmark_info(bookmark)


def delete_bookmark(map_obj: Any, bookmark_identifier: str) -> dict[str, Any]:
    require_allow_write()
    bookmark = _select_unique(list(map_obj.listBookmarks() or []), bookmark_identifier, "书签")
    result = _bookmark_info(bookmark)
    _require_method(map_obj, "removeBookmark", "删除书签")(bookmark)
    result["deleted"] = True
    return result


def import_bookmarks(map_obj: Any, input_path: str) -> dict[str, Any]:
    require_allow_write()
    source = _existing_input_file(input_path, "input_path", (".bkmx", ".dat"))
    before = len(list(map_obj.listBookmarks() or []))
    _require_method(map_obj, "importBookmarks", "导入书签")(source)
    after = list(map_obj.listBookmarks() or [])
    return {
        "input_path": source,
        "imported_count": max(0, len(after) - before),
        "bookmarks": [_bookmark_info(item) for item in after],
    }


def export_bookmarks(map_obj: Any, output_path: str) -> dict[str, Any]:
    require_allow_write()
    out = _validated_export(output_path, "output_path", (".bkmx",))
    _require_method(map_obj, "exportBookmarks", "导出书签")(out)
    return {"output_path": out, "bookmark_count": len(list(map_obj.listBookmarks() or []))}


# ---------------------------------------------------------------------------
# Layouts and elements


def _element_type(element: Any) -> str:
    return str(getattr(element, "type", "") or element.__class__.__name__).upper()


def _normalize_element_kind(value: str) -> str:
    raw = _required_text(value, "element_type", 64).upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "TEXT_ELEMENT": "TEXT",
        "PICTURE_ELEMENT": "PICTURE",
        "MAPFRAME": "MAP_FRAME",
        "MAPFRAME_ELEMENT": "MAP_FRAME",
        "LEGEND_ELEMENT": "LEGEND",
        "NORTHARROW": "NORTH_ARROW",
        "NORTH_ARROW_ELEMENT": "NORTH_ARROW",
        "SCALEBAR": "SCALE_BAR",
        "SCALE_BAR_ELEMENT": "SCALE_BAR",
        "DUALSCALEBAR": "DUAL_SCALE_BAR",
        "GRID_ELEMENT": "GRID",
        "TABLEFRAME": "TABLE_FRAME",
        "TABLEFRAME_ELEMENT": "TABLE_FRAME",
    }
    return aliases.get(raw, raw)


def _element_matches_kind(element: Any, kind: str) -> bool:
    element_type = _element_type(element)
    if kind == "MAP_FRAME":
        return "MAPFRAME" in element_type
    if kind == "TEXT":
        return "TEXT" in element_type
    if kind == "PICTURE":
        return "PICTURE" in element_type
    if kind == "TABLE_FRAME":
        return "TABLEFRAME" in element_type
    if kind == "LEGEND":
        return "LEGEND" in element_type
    if kind == "NORTH_ARROW":
        return "NORTH" in element_type or (
            "MAPSURROUND" in element_type and str(getattr(element, "name", "")).lower().find("north") >= 0
        )
    if kind == "SCALE_BAR":
        return "SCALEBAR" in element_type or (
            "MAPSURROUND" in element_type and str(getattr(element, "name", "")).lower().find("scale") >= 0
        )
    if kind == "DUAL_SCALE_BAR":
        return "DUALSCALEBAR" in element_type or "MAPSURROUND" in element_type
    if kind == "GRID":
        return "GRID" in element_type or "MAPSURROUND" in element_type
    return element_type == kind or element_type == f"{kind}_ELEMENT"


def _find_layout_element(layout: Any, identifier: str, element_type: str = "") -> Any:
    elements = list(layout.listElements() or [])
    if element_type:
        kind = _normalize_element_kind(element_type)
        elements = [item for item in elements if _element_matches_kind(item, kind)]
    return _select_unique(elements, identifier, "布局元素")


def _element_snapshot(element: Any, occurrence: int = 1) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": _object_name(element),
        "type": _element_type(element),
        "uri": _object_uri(element),
        "occurrence": occurrence,
    }
    property_names = {
        "visible": "visible",
        "locked": "locked",
        "position_x": "elementPositionX",
        "position_y": "elementPositionY",
        "width": "elementWidth",
        "height": "elementHeight",
        "rotation": "elementRotation",
        "anchor": "anchor",
        "text": "text",
    }
    for output_name, property_name in property_names.items():
        if hasattr(element, property_name):
            result[output_name] = _json_value(getattr(element, property_name))
    map_obj = getattr(element, "map", None)
    if map_obj is not None:
        result["map_name"] = _object_name(map_obj)
        result["map_uri"] = _object_uri(map_obj)
    map_frame = getattr(element, "mapFrame", None)
    if map_frame is not None:
        result["map_frame_name"] = _object_name(map_frame)
        result["map_frame_uri"] = _object_uri(map_frame)
    parent_group = getattr(element, "parentGroupElement", None)
    if parent_group is not None:
        result["parent_group"] = _object_name(parent_group)
    return result


def layout_element_info(layout: Any, element_type: str = "") -> list[dict[str, Any]]:
    """Return stable, JSON-friendly element metadata, including duplicate ordinals."""

    elements = list(layout.listElements() or [])
    if element_type:
        kind = _normalize_element_kind(element_type)
        elements = [item for item in elements if _element_matches_kind(item, kind)]
    seen: dict[tuple[str, str], int] = {}
    result = []
    for element in elements:
        key = (_element_type(element), _object_name(element))
        seen[key] = seen.get(key, 0) + 1
        result.append(_element_snapshot(element, seen[key]))
    return result


def create_layout_from_template(
    project: Any,
    template_path: str,
    layout_name: str = "",
    reuse_existing_maps: bool = True,
) -> dict[str, Any]:
    require_allow_write()
    template = _existing_input_file(template_path, "template_path", (".pagx",))
    before = list(project.listLayouts() or [])
    before_keys = {_object_key(item) for item in before}
    imported = _require_method(project, "importDocument", "从模板创建布局")(
        template,
        include_layout=True,
        reuse_existing_maps=bool(reuse_existing_maps),
    )
    after = list(project.listLayouts() or [])
    candidates = [item for item in after if _object_key(item) not in before_keys]
    if imported is not None:
        imported_items = list(imported) if isinstance(imported, (list, tuple)) else [imported]
        candidates.extend(item for item in imported_items if callable(getattr(item, "listElements", None)))
    unique: list[Any] = []
    keys: set[tuple[str, int]] = set()
    for item in candidates:
        item_key = _object_key(item)
        if item_key not in keys:
            keys.add(item_key)
            unique.append(item)
    if len(unique) != 1:
        raise RuntimeError(f"模板导入后无法唯一确定新布局（候选数：{len(unique)}）")
    layout = unique[0]
    requested_name = str(layout_name or "").strip()
    if requested_name:
        requested_name = _required_text(requested_name, "layout_name", 256)
        if any(item is not layout and _object_name(item) == requested_name for item in after):
            raise RuntimeError(f"布局名称已存在：{requested_name!r}")
        layout.name = requested_name
    return {"layout": layout, "name": _object_name(layout), "uri": _object_uri(layout), "template_path": template}


def bind_map_frame(layout: Any, map_frame_identifier: str, map_obj: Any) -> dict[str, Any]:
    require_allow_write()
    map_frame = find_map_frame(layout, map_frame_identifier)
    map_frame.map = map_obj
    bound = getattr(map_frame, "map", None)
    if bound is not map_obj and _object_uri(bound) != _object_uri(map_obj):
        raise RuntimeError("地图框绑定后校验失败")
    result = _element_snapshot(map_frame)
    result["bound_map_name"] = _object_name(map_obj)
    result["bound_map_uri"] = _object_uri(map_obj)
    return result


def _point(arcpy: Any, x: float | None, y: float | None) -> Any:
    factory = getattr(arcpy, "Point", None)
    if not callable(factory):
        raise RuntimeError("注入的 ArcPy 对象不支持 Point")
    return factory(float(0 if x is None else x), float(0 if y is None else y))


def _apply_element_properties(
    element: Any,
    *,
    x: float | None,
    y: float | None,
    width: float | None,
    height: float | None,
    rotation: float | None,
    visible: bool | None,
) -> None:
    values = {
        "elementPositionX": x,
        "elementPositionY": y,
        "elementWidth": width,
        "elementHeight": height,
        "elementRotation": rotation,
        "visible": visible,
    }
    for property_name, value in values.items():
        if value is None:
            continue
        if property_name in {"elementWidth", "elementHeight"} and float(value) <= 0:
            raise RuntimeError(f"{property_name} 必须大于 0")
        if not hasattr(element, property_name):
            raise RuntimeError(f"当前元素不支持属性 {property_name}")
        setattr(element, property_name, bool(value) if property_name == "visible" else float(value))


def upsert_layout_element(
    arcpy: Any,
    project: Any,
    layout: Any,
    element_type: str,
    name: str,
    *,
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
    map_obj: Any | None = None,
    map_frame: Any | None = None,
    table: Any | None = None,
    fields: list[str] | None = None,
    style_item: Any | None = None,
) -> dict[str, Any]:
    """Create or update a deliberately small, semantic set of layout elements."""

    require_allow_write()
    kind = _normalize_element_kind(element_type)
    supported = {
        "TEXT",
        "PICTURE",
        "MAP_FRAME",
        "LEGEND",
        "NORTH_ARROW",
        "SCALE_BAR",
        "DUAL_SCALE_BAR",
        "GRID",
        "TABLE_FRAME",
    }
    if kind not in supported:
        raise RuntimeError(f"element_type 须为 {sorted(supported)}")
    element_name = _required_text(name, "name", 256)
    matches = [
        item
        for item in list(layout.listElements() or [])
        if _object_name(item) == element_name and _element_matches_kind(item, kind)
    ]
    if len(matches) > 1:
        raise RuntimeError(f"布局元素名称和类型不唯一：{element_name!r}/{kind}")
    created = not matches
    element = matches[0] if matches else None

    image = ""
    if picture_path:
        image = _existing_input_file(
            picture_path,
            "picture_path",
            (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"),
        )

    if element is None:
        geometry = _point(arcpy, x, y)
        if kind == "TEXT":
            value = "" if text is None else str(text)
            element = _require_method(project, "createTextElement", "创建文本元素")(
                layout,
                geometry,
                "POINT",
                value,
                text_size=_bounded_float(text_size, "text_size", 1, 1000),
                font_family_name=_required_text(font_family_name, "font_family_name", 256),
                font_style_name=_required_text(font_style_name, "font_style_name", 128),
                style_item=style_item,
                name=element_name,
            )
        elif kind == "PICTURE":
            if not image:
                raise RuntimeError("创建图片元素必须提供 picture_path")
            element = _require_method(project, "createPictureElement", "创建图片元素")(
                layout,
                geometry,
                image,
                name=element_name,
            )
        elif kind == "MAP_FRAME":
            if map_obj is None:
                raise RuntimeError("创建地图框必须提供 map_obj")
            element = _require_method(layout, "createMapFrame", "创建地图框")(geometry, map_obj, element_name)
        elif kind in {"LEGEND", "NORTH_ARROW", "SCALE_BAR", "DUAL_SCALE_BAR", "GRID"}:
            if map_frame is None:
                raise RuntimeError(f"创建 {kind} 必须提供 map_frame")
            element = _require_method(layout, "createMapSurroundElement", "创建地图整饰元素")(
                geometry,
                kind,
                map_frame,
                style_item,
                element_name,
            )
        else:
            if map_frame is None or table is None:
                raise RuntimeError("创建 TABLE_FRAME 必须提供 map_frame 和 table")
            element = _require_method(layout, "createTableFrameElement", "创建表格框元素")(
                geometry,
                map_frame,
                table,
                fields or [],
                style_item,
                element_name,
            )
        if element is None:
            raise RuntimeError("ArcGIS Pro 创建布局元素后未返回元素对象")
    else:
        if kind == "TEXT" and text is not None:
            if not hasattr(element, "text"):
                raise RuntimeError("当前文本元素不支持 text 属性")
            element.text = str(text)
        elif kind == "PICTURE" and image:
            if not hasattr(element, "sourceImage"):
                raise RuntimeError("当前图片元素不支持 sourceImage 属性")
            element.sourceImage = image
        elif kind == "MAP_FRAME" and map_obj is not None:
            element.map = map_obj
        elif kind in {"LEGEND", "NORTH_ARROW", "SCALE_BAR", "DUAL_SCALE_BAR", "GRID"} and map_frame is not None:
            element.mapFrame = map_frame

    _apply_element_properties(
        element,
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        visible=visible,
    )
    return {"created": created, "element": element, "info": _element_snapshot(element)}


def delete_layout_element(layout: Any, element_identifier: str, element_type: str = "") -> dict[str, Any]:
    require_allow_write()
    element = _find_layout_element(layout, element_identifier, element_type)
    result = _element_snapshot(element)
    _require_method(layout, "deleteElement", "删除布局元素")(element)
    result["deleted"] = True
    return result


# ---------------------------------------------------------------------------
# Map series


def _enabled_map_series(layout: Any) -> Any:
    map_series = getattr(layout, "mapSeries", None)
    if map_series is None or not bool(getattr(map_series, "enabled", False)):
        raise RuntimeError("布局没有已启用的 MapSeries")
    return map_series


def map_series_info(layout: Any) -> dict[str, Any]:
    map_series = _enabled_map_series(layout)
    page_name_field = getattr(map_series, "pageNameField", None)
    map_frame = getattr(map_series, "mapFrame", None)
    index_layer = getattr(map_series, "indexLayer", None)
    return {
        "type": str(getattr(map_series, "type", "") or map_series.__class__.__name__),
        "enabled": True,
        "current_page_number": getattr(map_series, "currentPageNumber", None),
        "current_page_name": str(getattr(map_series, "currentPageName", "") or ""),
        "page_count": int(getattr(map_series, "pageCount", 0) or 0),
        "page_name_field": _object_name(page_name_field) if page_name_field is not None else "",
        "map_frame_name": _object_name(map_frame) if map_frame is not None else "",
        "index_layer_name": _object_name(index_layer) if index_layer is not None else "",
        "selected_index_features": _json_value(getattr(map_series, "selectedIndexFeatures", []) or []),
    }


def set_map_series_page(
    layout: Any,
    *,
    page_number: int | str | None = None,
    page_name: str = "",
) -> dict[str, Any]:
    require_allow_write()
    map_series = _enabled_map_series(layout)
    requested_name = str(page_name or "").strip()
    if (page_number is None) == (not requested_name):
        raise RuntimeError("page_number 与 page_name 必须且只能提供一个")
    if requested_name:
        resolved = _require_method(map_series, "getPageNumberFromName", "按名称定位 MapSeries 页面")(
            requested_name
        )
        if resolved is None:
            raise RuntimeError(f"未找到 MapSeries 页面：{requested_name!r}")
        target = resolved
    else:
        if isinstance(page_number, str):
            target = _required_text(page_number, "page_number", 256)
        else:
            page_count = int(getattr(map_series, "pageCount", 0) or 0)
            target = _bounded_int(page_number, "page_number", 1, page_count)
    map_series.currentPageNumber = target
    result = map_series_info(layout)
    result["requested_page_name"] = requested_name
    return result


def refresh_map_series(layout: Any) -> dict[str, Any]:
    require_allow_write()
    map_series = _enabled_map_series(layout)
    _require_method(map_series, "refresh", "刷新 MapSeries")()
    return map_series_info(layout)


def export_map_series_pdf(
    layout: Any,
    output_path: str,
    *,
    page_range_type: str = "ALL",
    page_range_string: str = "",
    multiple_files: str = "PDF_SINGLE_FILE",
    resolution_dpi: int = 300,
) -> dict[str, Any]:
    require_allow_write()
    map_series = _enabled_map_series(layout)
    out = _validated_export(output_path, "output_path", (".pdf",))
    range_type = _required_text(page_range_type, "page_range_type", 32).upper()
    allowed_ranges = {"ALL", "CURRENT", "RANGE", "SELECTED"}
    if range_type not in allowed_ranges:
        raise RuntimeError(f"page_range_type 须为 {sorted(allowed_ranges)}")
    range_string = str(page_range_string or "").strip()
    if range_type == "RANGE" and not range_string:
        raise RuntimeError("page_range_type=RANGE 时必须提供 page_range_string")
    files_mode = _required_text(multiple_files, "multiple_files", 64).upper()
    allowed_files = {
        "PDF_SINGLE_FILE",
        "PDF_MULTIPLE_FILES_PAGE_NAME",
        "PDF_MULTIPLE_FILES_PAGE_NUMBER",
    }
    if files_mode not in allowed_files:
        raise RuntimeError(f"multiple_files 须为 {sorted(allowed_files)}")
    dpi = _bounded_int(resolution_dpi, "resolution_dpi", 24, 1200)
    _require_method(map_series, "exportToPDF", "导出 MapSeries")(
        out,
        page_range_type=range_type,
        page_range_string=range_string,
        multiple_files=files_mode,
        resolution=dpi,
    )
    return {
        "output_path": out,
        "page_range_type": range_type,
        "page_range_string": range_string,
        "multiple_files": files_mode,
        "resolution_dpi": dpi,
    }


# ---------------------------------------------------------------------------
# Definition queries, label classes, and standalone table properties


def list_definition_queries(member: Any) -> list[dict[str, Any]]:
    queries = _require_method(member, "listDefinitionQueries", "读取定义查询")()
    return [_json_value(dict(item)) for item in list(queries or [])]


def upsert_definition_query(
    member: Any,
    name: str,
    sql: str,
    *,
    is_active: bool = False,
    spatial_clause: Any = _UNSET,
) -> dict[str, Any]:
    require_allow_write()
    query_name = _required_text(name, "name", 256)
    sql_text = _required_text(sql, "sql", 16000)
    queries = [dict(item) for item in list(_require_method(member, "listDefinitionQueries", "读取定义查询")() or [])]
    matches = [index for index, item in enumerate(queries) if str(item.get("name", "")) == query_name]
    if len(matches) > 1:
        raise RuntimeError(f"定义查询名称不唯一：{query_name!r}")
    query = queries[matches[0]] if matches else {"name": query_name}
    query["sql"] = sql_text
    query["isActive"] = bool(is_active)
    if spatial_clause is not _UNSET:
        if spatial_clause is None:
            query.pop("spatialClause", None)
        else:
            query["spatialClause"] = spatial_clause
    if is_active:
        for item in queries:
            item["isActive"] = item is query
    if not matches:
        queries.append(query)
    _require_method(member, "updateDefinitionQueries", "更新定义查询")(queries)
    return _json_value(dict(query))


def delete_definition_query(member: Any, name: str) -> dict[str, Any]:
    require_allow_write()
    query_name = _required_text(name, "name", 256)
    queries = [dict(item) for item in list(_require_method(member, "listDefinitionQueries", "读取定义查询")() or [])]
    matches = [item for item in queries if str(item.get("name", "")) == query_name]
    if len(matches) != 1:
        if not matches:
            raise RuntimeError(f"未找到定义查询：{query_name!r}")
        raise RuntimeError(f"定义查询名称不唯一：{query_name!r}")
    remaining = [item for item in queries if item is not matches[0]]
    _require_method(member, "updateDefinitionQueries", "删除定义查询")(remaining)
    result = _json_value(matches[0])
    result["deleted"] = True
    return result


def _label_class_info(label_class: Any) -> dict[str, Any]:
    result = {
        "name": _object_name(label_class),
        "expression": str(getattr(label_class, "expression", "") or ""),
        "sql_query": str(getattr(label_class, "SQLQuery", "") or ""),
        "visible": bool(getattr(label_class, "visible", False)),
    }
    if hasattr(label_class, "expressionEngine"):
        result["expression_engine"] = str(label_class.expressionEngine or "")
    else:
        get_definition = getattr(label_class, "getDefinition", None)
        if callable(get_definition):
            definition = get_definition("V3")
            result["expression_engine"] = str(getattr(definition, "expressionEngine", "") or "")
    return result


def list_label_classes(layer: Any) -> list[dict[str, Any]]:
    return [_label_class_info(item) for item in list(_require_method(layer, "listLabelClasses", "读取标注类")() or [])]


def upsert_label_class(
    layer: Any,
    name: str,
    expression: str,
    *,
    sql_query: str = "",
    language: str = "Arcade",
    visible: bool | None = None,
) -> dict[str, Any]:
    require_allow_cim_write()
    class_name = _required_text(name, "name", 256)
    language_key = _required_text(language, "language", 32).upper()
    if language_key != "ARCADE":
        raise RuntimeError("language 仅允许 Arcade；Python/JScript/VBScript 不通过 MCP 暴露")
    expression_text = validate_safe_arcade_expression(expression, maximum=16000)
    labels = list(_require_method(layer, "listLabelClasses", "读取标注类")() or [])
    matches = [item for item in labels if _object_name(item) == class_name]
    if len(matches) > 1:
        raise RuntimeError(f"标注类名称不唯一：{class_name!r}")
    cim_expression_engine = "Arcade"
    typed_expression_engine = "ARCADE"
    if matches:
        label_class = matches[0]
        label_class.expression = expression_text
        label_class.SQLQuery = str(sql_query or "")
        if hasattr(label_class, "expressionEngine"):
            label_class.expressionEngine = cim_expression_engine
        else:
            get_definition = getattr(label_class, "getDefinition", None)
            set_definition = getattr(label_class, "setDefinition", None)
            if not callable(get_definition) or not callable(set_definition):
                raise RuntimeError("当前标注类无法通过 CIM 更新 expressionEngine")
            definition = get_definition("V3")
            definition.expression = expression_text
            definition.expressionEngine = cim_expression_engine
            set_definition(definition)
    else:
        label_class = _require_method(layer, "createLabelClass", "创建标注类")(
            class_name,
            expression_text,
            str(sql_query or ""),
            typed_expression_engine,
        )
        if label_class is None:
            label_class = _select_unique(
                list(_require_method(layer, "listLabelClasses", "读取标注类")() or []),
                class_name,
                "标注类",
            )
    if visible is not None:
        label_class.visible = bool(visible)
        if visible and hasattr(layer, "showLabels"):
            layer.showLabels = True
    return _label_class_info(label_class)


def table_properties(table: Any, selection_sample_limit: int = 100) -> dict[str, Any]:
    """Return useful standalone-table properties without leaking credentials."""

    sample_limit = _bounded_int(selection_sample_limit, "selection_sample_limit", 0, 1000)
    result: dict[str, Any] = {
        "name": _object_name(table),
        "uri": _object_uri(table),
        "long_name": str(getattr(table, "longName", "") or ""),
        "data_source": _redact_secrets(str(getattr(table, "dataSource", "") or "")),
        "is_broken": bool(getattr(table, "isBroken", False)),
        "definition_query": str(getattr(table, "definitionQuery", "") or ""),
    }
    if hasattr(table, "visible"):
        result["visible"] = bool(table.visible)
    connection_properties = getattr(table, "connectionProperties", None)
    if connection_properties is not None:
        result["connection_properties"] = _redact_secrets(connection_properties)
    list_queries = getattr(table, "listDefinitionQueries", None)
    if callable(list_queries):
        result["definition_queries"] = [_json_value(dict(item)) for item in list(list_queries() or [])]
    get_selection = getattr(table, "getSelectionSet", None)
    if callable(get_selection):
        selection = list(get_selection() or [])
        try:
            selection.sort()
        except TypeError:
            selection.sort(key=str)
        result["selection_count"] = len(selection)
        result["selection_sample"] = [_json_value(item) for item in selection[:sample_limit]]
        result["selection_truncated"] = len(selection) > sample_limit
    return result


def update_table_properties(
    table: Any,
    *,
    new_name: str | None = None,
    definition_query: str | None = None,
) -> dict[str, Any]:
    """Update the two writable, stable ArcPy Table properties."""

    require_allow_write()
    if new_name is None and definition_query is None:
        raise RuntimeError("至少提供 new_name 或 definition_query")
    if new_name is not None:
        table.name = _required_text(new_name, "new_name", 256)
    if definition_query is not None:
        if not hasattr(table, "definitionQuery"):
            raise RuntimeError("当前表不支持 definitionQuery")
        table.definitionQuery = str(definition_query)
    return table_properties(table)


__all__ = [
    "apply_map_frame_extent",
    "bind_map_frame",
    "create_bookmark",
    "create_layout_from_template",
    "delete_bookmark",
    "delete_definition_query",
    "delete_layout_element",
    "export_active_view",
    "export_bookmarks",
    "export_map_series_pdf",
    "extent_info",
    "find_map_frame",
    "get_map_frame_extent",
    "import_bookmarks",
    "layout_element_info",
    "list_bookmarks",
    "list_definition_queries",
    "list_label_classes",
    "map_series_info",
    "open_table_view",
    "refresh_map_series",
    "set_map_frame_extent",
    "set_map_series_page",
    "table_properties",
    "update_bookmark",
    "update_table_properties",
    "upsert_definition_query",
    "upsert_label_class",
    "upsert_layout_element",
]
