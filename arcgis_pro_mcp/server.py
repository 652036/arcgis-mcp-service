"""MCP tools for ArcGIS Pro via arcpy.mp (mapping module)."""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from arcgis_pro_mcp import (
    cartography,
    charts,
    da_read,
    da_write,
    data_integrity,
    dataset_management,
    editing,
    enterprise_gdb,
    geocoding,
    gp_allowlist,
    gp_analysis,
    gp_convert,
    gp_create,
    gp_generic,
    gp_network,
    gp_raster,
    gp_schema,
    gp_stats,
    gp_write,
    lidar,
    live_analysis,
    metadata,
    network_analysis,
    project_catalog,
    project_io,
    publishing,
    raster_advanced,
    raster_runtime,
    schema_maintenance,
    sdk_bridge,
    session_refs,
    spatial_modeling,
    symbology,
    tool_protocol,
    utility_network,
    workspace_listing,
)
from arcgis_pro_mcp.arcade import validate_safe_arcade_expression
from arcgis_pro_mcp.paths import (
    cim_write_allowed,
    destructive_allowed,
    enterprise_write_allowed,
    inline_db_password_allowed,
    is_current_project_token,
    normalize_path,
    project_roots,
    public_share_allowed,
    publish_allowed,
    publish_overwrite_allowed,
    require_allow_cim_write,
    require_allow_destructive,
    require_allow_write,
    validate_input_path_optional,
    validate_new_output_in_export_root,
    validate_output_name,
    validate_project_path,
    writes_allowed,
)
from arcgis_pro_mcp.redaction import redact_sensitive as _redact_sensitive
from arcgis_pro_mcp.redaction import redact_text_values as _redact_text_values
from arcgis_pro_mcp.redaction import safe_error as _safe_error

_DB_INSTANCE_ALLOWLIST_ENV = "ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST"
_DB_USERNAME_ENV = "ARCGIS_PRO_MCP_DB_USERNAME"
_DB_PASSWORD_ENV = "ARCGIS_PRO_MCP_DB_PASSWORD"
_DB_PLATFORMS = frozenset(
    {
        "DAMENG",
        "DB2",
        "ORACLE",
        "POSTGRESQL",
        "SAP HANA",
        "SQL_SERVER",
        "TERADATA",
    }
)


def _json_dumps(data: Any) -> str:
    return json.dumps(
        _redact_text_values(data),
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _parse_iso_datetime(value: str, label: str) -> datetime:
    text = (value or "").strip()
    if not text:
        raise RuntimeError(f"{label} 不能为空")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as ex:
        raise RuntimeError(f"{label} 必须为 ISO-8601 日期时间") from ex


def _window_status_fields(*, confirm_target: bool = False) -> dict[str, Any]:
    try:
        from arcgis_pro_mcp.pro_attach import host_status

        return host_status(confirm_target=confirm_target)
    except Exception as ex:  # noqa: BLE001
        return {"window_attached": False, "window_status_error": _safe_error(ex, 300)}


mcp = FastMCP(
    "arcgis-pro",
    instructions=(
        "在显式策略边界内自动化 ArcGIS Pro：绝对 .aprx 使用文件模式，"
        "aprx_path=CURRENT 使用 Pro 内 Python v4 宿主，arcgis_pro_sdk_* 使用可选原生 Add-In。"
        "先调用 environment_info、server_capabilities 和 tool_info；写入、破坏性操作、"
        "CIM、企业维护、发布及 SDK 编辑分别受独立门禁控制。输入、工程、导出和 GP 输出"
        "必须遵守各自路径根；通用 GP 默认关闭且须精确 allowlist。实时宿主、租约、generation"
        "或目标变化时失败关闭，不得自动切换模式或盲目重试未知结果。真实执行须在 Windows"
        "上使用能导入 arcpy 的 ArcGIS Pro Python。"
    ),
)


def _arcpy():
    try:
        import arcpy  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError(
            "未检测到 arcpy。请在已安装 ArcGIS Pro 的 Windows 上，使用 Pro 自带的 Python 启动本 MCP，"
            '例如："C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" '
            "-m arcgis_pro_mcp"
        ) from e
    return arcpy


_PROJECT_CACHE: dict[str, Any] = {}
_CURRENT_PROJECT_CONTEXT: ContextVar[tuple[Any, str] | None] = ContextVar(
    "arcgis_pro_current_project_context",
    default=None,
)


@contextmanager
def _bind_current_project(project: Any, path: str):
    """Reuse one CURRENT ArcGISProject reference for the duration of a host request."""
    token = _CURRENT_PROJECT_CONTEXT.set((project, path))
    try:
        yield
    finally:
        _CURRENT_PROJECT_CONTEXT.reset(token)


def _project_cache_key(aprx_path: str) -> str:
    p = aprx_path.strip().strip('"')
    if p.upper() == "CURRENT":
        return "CURRENT"
    return os.path.realpath(os.path.abspath(os.path.expanduser(p))).lower()


def _open_project(aprx_path: str) -> tuple[Any, Any, str]:
    arcpy = _arcpy()
    path = validate_project_path(aprx_path, "aprx_path")
    if is_current_project_token(path):
        bound = _CURRENT_PROJECT_CONTEXT.get()
        if bound is not None:
            return arcpy, bound[0], bound[1]
        try:
            project = arcpy.mp.ArcGISProject("CURRENT")
        except Exception as ex:  # noqa: BLE001
            raise RuntimeError(
                "无法接入 ArcGIS Pro 当前窗口。请先打开一个工程，然后在 Pro 的 Python 窗口运行：\n"
                "  接入当前窗口.py\n"
                f"原始错误：{_safe_error(ex, 400)}"
            ) from ex
        file_path = getattr(project, "filePath", None) or "CURRENT"
        return arcpy, project, str(file_path)
    key = _project_cache_key(path)
    project = _PROJECT_CACHE.get(key)
    if project is not None:
        return arcpy, project, path
    try:
        project = arcpy.mp.ArcGISProject(path)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"打开工程失败：{path}；{_safe_error(ex, 500)}") from ex
    _PROJECT_CACHE[key] = project
    return arcpy, project, path


def _delete_project_item(project: Any, item: Any) -> None:
    if not hasattr(project, "deleteItem"):
        raise RuntimeError("当前 ArcGISProject 不支持 deleteItem，无法删除地图或布局")
    project.deleteItem(item)


def _replace_layer_data_source(
    lyr: Any,
    workspace_path: str,
    dataset_name: str,
    workspace_type: str,
    validate: bool,
) -> None:
    if hasattr(lyr, "updateConnectionProperties"):
        old_cp = lyr.connectionProperties
        if isinstance(old_cp, dict):
            new_cp = dict(old_cp)
            info = new_cp.get("connection_info")
            if isinstance(info, dict):
                new_cp["connection_info"] = dict(info)
                new_cp["connection_info"]["database"] = workspace_path
            if dataset_name:
                new_cp["dataset"] = dataset_name
            if workspace_type:
                factory = {
                    "FILEGDB_WORKSPACE": "File Geodatabase",
                    "SHAPEFILE_WORKSPACE": "Shape File",
                    "RASTER_WORKSPACE": "Raster",
                    "OLEDB_WORKSPACE": "OLE DB",
                    "SDE_WORKSPACE": "SDE",
                }.get(workspace_type.upper(), workspace_type)
                new_cp["workspace_factory"] = factory
            lyr.updateConnectionProperties(old_cp, new_cp, validate)
            return
        lyr.updateConnectionProperties(str(old_cp), workspace_path)
        return
    if hasattr(lyr, "replaceDataSource"):
        lyr.replaceDataSource(workspace_path, workspace_type, dataset_name, validate)
        return
    raise RuntimeError("当前 Layer 不支持 updateConnectionProperties/replaceDataSource")


def _set_mapframe_extent(mf: Any, ext: Any) -> None:
    cam = getattr(mf, "camera", None)
    if cam is not None and hasattr(cam, "setExtent"):
        cam.setExtent(ext)
        return
    raise RuntimeError("当前 MapFrame 不支持官方 camera.setExtent API")


def _mapframe_extent(mf: Any) -> Any:
    cam = getattr(mf, "camera", None)
    if cam is not None and hasattr(cam, "getExtent"):
        return cam.getExtent()
    return None


def _raise_not_found(label: str, value: str, available: list[str]) -> None:
    preview = ", ".join(available[:20])
    if len(available) > 20:
        preview += ", ..."
    suffix = f"；可选值：{preview}" if preview else ""
    raise RuntimeError(f"未找到 {label}：{value!r}{suffix}")


def _object_uri(value: Any) -> str | None:
    for attr in ("URI", "uri"):
        uri = getattr(value, attr, None)
        if uri:
            return str(uri)
    return None


def _object_choice(value: Any) -> str:
    name = str(getattr(value, "name", value))
    long_name = getattr(value, "longName", None)
    uri = _object_uri(value)
    details = [str(item) for item in (long_name, uri) if item and str(item) != name]
    return f"{name} ({' | '.join(details)})" if details else name


def _select_named_object(
    label: str,
    requested: str,
    values: list[Any],
    *,
    allow_long_name: bool = False,
) -> Any:
    identity_matches = [value for value in values if _object_uri(value) == requested]
    if allow_long_name:
        for value in values:
            if getattr(value, "longName", None) == requested and not any(
                value is existing for existing in identity_matches
            ):
                identity_matches.append(value)
    if len(identity_matches) == 1:
        return identity_matches[0]
    if len(identity_matches) > 1:
        raise RuntimeError(f"{label} 标识不唯一：{requested!r}")

    name_matches = [value for value in values if getattr(value, "name", None) == requested]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        choices = ", ".join(_object_choice(value) for value in name_matches[:20])
        raise RuntimeError(
            f"{label} 名称不唯一：{requested!r}；请使用精确 longName/URI。候选：{choices}"
        )
    _raise_not_found(label, requested, [_object_choice(value) for value in values])


def _get_map(project: Any, map_name: str) -> Any:
    return _select_named_object("map", map_name, list(project.listMaps()))


def _get_layout(project: Any, layout_name: str) -> Any:
    return _select_named_object("layout", layout_name, list(project.listLayouts()))


def _get_report(project: Any, report_name: str) -> Any:
    if not hasattr(project, "listReports"):
        raise RuntimeError("当前 ArcGISProject 不支持 reports")
    return _select_named_object("report", report_name, list(project.listReports()))


def _get_mapframe(layout: Any, mapframe_name: str) -> Any:
    return _select_named_object(
        "map frame",
        mapframe_name,
        list(layout.listElements("MAPFRAME_ELEMENT")),
    )


def _find_layer(map_obj: Any, layer_name: str) -> Any:
    return _select_named_object(
        "layer",
        layer_name,
        list(map_obj.listLayers()),
        allow_long_name=True,
    )


def _get_table(map_obj: Any, table_name: str) -> Any:
    if not hasattr(map_obj, "listTables"):
        raise RuntimeError("当前 Map 不支持 listTables")
    return _select_named_object("table", table_name, list(map_obj.listTables()))


def _strict_member_type(member_type: str, allowed: set[str]) -> str:
    kind = str(member_type or "").strip().upper()
    if kind not in allowed:
        raise RuntimeError(f"member_type 须为 {sorted(allowed)}")
    return kind


def _get_map_member(
    project: Any,
    map_name: str,
    member_name: str,
    member_type: str,
) -> tuple[Any, Any]:
    """Resolve an already-validated LAYER/TABLE member with unique selectors."""

    map_obj = _get_map(project, map_name)
    if member_type == "LAYER":
        return map_obj, _find_layer(map_obj, member_name)
    if member_type == "TABLE":
        return map_obj, _get_table(map_obj, member_name)
    raise RuntimeError("member_type 必须先校验为 LAYER 或 TABLE")


class _ScopedWebLayerSharingSource:
    """Adapt one map member to Map.getWebLayerSharingDraft's subset overload."""

    def __init__(self, map_obj: Any, member: Any) -> None:
        self._map = map_obj
        self._member = member

    def getWebLayerSharingDraft(
        self,
        server_type: str,
        service_type: str,
        service_name: str,
    ) -> Any:
        factory = getattr(self._map, "getWebLayerSharingDraft", None)
        if not callable(factory):
            raise RuntimeError("当前 Map 不支持 getWebLayerSharingDraft")
        return factory(server_type, service_type, service_name, [self._member])


def _get_style_item(
    project: Any,
    style: str,
    category: str,
    style_name: str,
) -> Any | None:
    if not style_name.strip():
        return None
    if not hasattr(project, "listStyleItems"):
        raise RuntimeError("当前 ArcGISProject 不支持 listStyleItems")
    style_value = style.strip()
    category_value = category.strip()
    if not style_value or not category_value:
        raise RuntimeError("使用 style_name 时必须同时提供 style 和 style_category")
    items = list(
        project.listStyleItems(
            style_value,
            category_value,
            style_name.strip(),
        )
    )
    return _select_named_object("style item", style_name.strip(), items)


def _layer_selection_set(layer: Any) -> set[Any]:
    """Return the actual ArcGIS Pro layer selection without falling back to total rows."""
    getter = getattr(layer, "getSelectionSet", None)
    if not callable(getter):
        raise RuntimeError("当前图层不支持 getSelectionSet，无法验证选择结果")
    try:
        values = getter()
        return set(values or ())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"读取图层选择集失败：{_safe_error(exc, 300)}") from exc


def _result_count(result: Any, output_index: int) -> int | None:
    """Read the optional derived Count output from ArcPy selection results."""
    try:
        value = result.getOutput(output_index)
    except Exception:  # noqa: BLE001
        return None
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _verify_selection_result(
    layer: Any,
    result: Any,
    *,
    count_output_index: int,
) -> tuple[int, int | None]:
    selected_count = len(_layer_selection_set(layer))
    result_count = _result_count(result, count_output_index)
    if result_count is not None and result_count != selected_count:
        raise RuntimeError(
            "ArcPy 返回的选择计数与图层实际选择集不一致，拒绝报告成功："
            f"result_count={result_count}, selected_count={selected_count}。"
            "请先重新读取图层状态，不要自动重试写操作"
        )
    return selected_count, result_count


def _refresh_layer_after_window_change(arcpy: Any, aprx_path: str, layer: Any) -> bool:
    if not is_current_project_token(aprx_path):
        return False
    refresh = getattr(arcpy, "RefreshLayer", None)
    if not callable(refresh):
        return False
    layer_name = str(getattr(layer, "name", "")).strip()
    if not layer_name:
        return False
    try:
        refresh(layer_name)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "选择已应用并验证，但 ArcGIS Pro 图层刷新失败；"
            "结果可能已经生效，请先检查窗口状态，不要自动重试："
            f"{_safe_error(exc, 300)}"
        ) from exc
    return True


@contextmanager
def _workspace_ctx(arcpy: Any, workspace_path: str):
    old = arcpy.env.workspace
    arcpy.env.workspace = workspace_path
    try:
        yield
    finally:
        arcpy.env.workspace = old


def _sanitize_wild_card(wild_card: str, max_len: int = 120) -> str:
    wc = wild_card.strip()
    if len(wc) > max_len:
        raise RuntimeError("wild_card 过长")
    return wc or "*"


def _list_workspace_datasets(
    arcpy: Any,
    workspace_path: str,
    dataset_type: str = "",
    wild_card: str = "*",
    max_items: int = 200,
) -> list[str]:
    cap = max(1, min(int(max_items), 2000))
    wc = _sanitize_wild_card(wild_card)
    dt = dataset_type.strip()
    with _workspace_ctx(arcpy, workspace_path):
        names = arcpy.ListDatasets(wc, dt or "") or []
    return [str(n) for n in names[:cap]]


def _list_workspace_domains(arcpy: Any, workspace_path: str, max_items: int = 200) -> list[dict[str, Any]]:
    cap = max(1, min(int(max_items), 2000))
    try:
        list_domains = arcpy.da.ListDomains  # type: ignore[attr-defined]
    except AttributeError:
        list_domains = getattr(arcpy, "ListDomains", None)
    if list_domains is None:
        raise RuntimeError("当前 arcpy 版本不支持 ListDomains")
    domains = list_domains(workspace_path) or []
    rows: list[dict[str, Any]] = []
    for dom in domains[:cap]:
        row: dict[str, Any] = {}
        for attr in (
            "name",
            "domainType",
            "fieldType",
            "splitPolicy",
            "mergePolicy",
            "description",
            "owner",
        ):
            try:
                v = getattr(dom, attr, None)
                if v is not None:
                    row[attr] = v
            except Exception:  # noqa: BLE001
                pass
        try:
            coded_values = getattr(dom, "codedValues", None)
            if coded_values:
                row["coded_values"] = coded_values
        except Exception:  # noqa: BLE001
            pass
        rows.append(row)
    return rows


def _validate_view_name(name: str, label: str) -> str:
    out = (name or "").strip()
    if not out:
        raise RuntimeError(f"{label} cannot be empty")
    if len(out) > 128:
        raise RuntimeError(f"{label} too long")
    if any(ch in out for ch in ("\r", "\n", ";", "\\", "/")):
        raise RuntimeError(f"{label} contains invalid characters")
    return out


def _spatial_ref_dict(sr: Any) -> dict[str, Any] | None:
    if sr is None:
        return None
    out: dict[str, Any] = {}
    try:
        out["name"] = sr.name
    except Exception:  # noqa: BLE001
        pass
    try:
        out["factory_code"] = int(sr.factoryCode)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["type"] = sr.type
    except Exception:  # noqa: BLE001
        pass
    try:
        wkt = sr.exportToString()
        if wkt:
            out["wkt"] = wkt[:2000]
    except Exception:  # noqa: BLE001
        pass
    return out or None


def _extent_dict(ext: Any) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for key in ("XMin", "YMin", "XMax", "YMax", "ZMin", "ZMax", "MMin", "MMax"):
        try:
            v = getattr(ext, key, None)
            if v is not None:
                d[key.lower()] = float(v)
        except Exception:  # noqa: BLE001
            pass
    try:
        d["spatial_reference"] = _spatial_ref_dict(ext.spatialReference)
    except Exception:  # noqa: BLE001
        pass
    return d


def _camera_dict(camera: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attr in ("scale", "heading", "pitch", "roll", "mode"):
        try:
            value = getattr(camera, attr, None)
            if value is None:
                continue
            out[attr] = float(value) if attr != "mode" else str(value)
        except Exception:  # noqa: BLE001
            pass
    try:
        if hasattr(camera, "getExtent"):
            out["extent"] = _extent_dict(camera.getExtent())
    except Exception as ex:  # noqa: BLE001
        out["extent_error"] = _safe_error(ex, 300)
    return out


def _require_current_window(aprx_path: str) -> None:
    if not is_current_project_token(aprx_path):
        raise RuntimeError(
            "此工具只控制 ArcGIS Pro 当前窗口，请传 aprx_path=CURRENT；"
            "绝对 .aprx 路径没有可控制的活动视图"
        )


def _active_view_payload(project: Any, path: str) -> dict[str, Any]:
    view = getattr(project, "activeView", None)
    active_map = getattr(project, "activeMap", None)
    out: dict[str, Any] = {
        "aprx_path": path,
        "project_is_read_only": bool(getattr(project, "isReadOnly", False)),
        "active_map_name": getattr(active_map, "name", None) if active_map is not None else None,
        "active_view": None,
    }
    if view is None:
        return out
    details: dict[str, Any] = {
        "python_type": type(view).__name__,
        "name": getattr(view, "name", None),
    }
    view_map = getattr(view, "map", None)
    camera = getattr(view, "camera", None)
    if view_map is not None and camera is not None:
        details["type"] = "MAP_VIEW"
        details["map_name"] = getattr(view_map, "name", None)
        details["camera"] = _camera_dict(camera)
    elif hasattr(view, "listElements"):
        details["type"] = "LAYOUT_VIEW"
    else:
        details["type"] = type(view).__name__.upper()
    out["active_view"] = details
    return out


def _active_map_view(project: Any) -> tuple[Any, Any]:
    view = getattr(project, "activeView", None)
    view_map = getattr(view, "map", None) if view is not None else None
    if view is None or view_map is None or getattr(view, "camera", None) is None:
        raise RuntimeError(
            "当前焦点不是地图视图。请先激活地图标签页，或调用 arcgis_pro_open_map_view"
        )
    return view, view_map


def _describe_summary(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    dobj = arcpy.Describe(dataset_path)
    out: dict[str, Any] = {}
    for attr in (
        "name",
        "baseName",
        "catalogPath",
        "path",
        "file",
        "dataType",
        "category",
        "workspacePath",
        "shapeType",
        "hasSpatialIndex",
        "hasM",
        "hasZ",
        "length",
        "areaFieldName",
        "geometryType",
    ):
        try:
            v = getattr(dobj, attr, None)
            if v is not None and not callable(v):
                if isinstance(v, (str, int, float, bool)):
                    out[attr] = v
                else:
                    out[attr] = str(v)[:1000]
        except Exception:  # noqa: BLE001
            pass
    try:
        out["extent"] = _extent_dict(dobj.extent)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["spatial_reference"] = _spatial_ref_dict(dobj.spatialReference)
    except Exception:  # noqa: BLE001
        pass
    return out


@mcp.tool(
    name="arcgis_pro_environment_info",
    description="",
)
def arcgis_pro_environment_info() -> str:
    arcpy = _arcpy()
    info: dict[str, Any] = {}
    try:
        inst = arcpy.GetInstallInfo()
        info["install_info"] = inst
        if isinstance(inst, dict):
            info["pro_version"] = inst.get("Version")
    except Exception as ex:  # noqa: BLE001
        info["install_info_error"] = _safe_error(ex, 500)
    try:
        info["product_info"] = arcpy.ProductInfo()
    except Exception as ex:  # noqa: BLE001
        info["product_info_error"] = _safe_error(ex, 500)
    info["allow_write"] = writes_allowed()
    info["project_roots_configured"] = bool(project_roots())
    info["generic_gp_enabled"] = gp_generic.generic_gp_enabled()
    info["generic_gp_allowlist"] = gp_generic.generic_gp_allowlist()
    info.update(_window_status_fields())
    return _json_dumps(info)


@mcp.tool(
    name="arcgis_pro_window_status",
    description="读取实时窗口宿主的工程、活动视图、队列、忙碌状态和会话标识，不修改工程。",
)
def arcgis_pro_window_status() -> str:
    return _json_dumps(_window_status_fields(confirm_target=True))


@mcp.tool(
    name="arcgis_pro_detach_window",
    description="安全停止当前 ArcGIS Pro 窗口宿主；需要开启写入权限。",
)
def arcgis_pro_detach_window() -> str:
    require_allow_write()
    import time

    from arcgis_pro_mcp.pro_attach import host_health, read_state, stop_host

    health = host_health()
    if not health or not health.get("ok"):
        return _json_dumps({"ok": True, "detached": False, "reason": "window host is not attached"})
    result = stop_host(health)
    session_id = health.get("session_id")
    deadline = time.monotonic() + 3.0
    missing_state_reads = 0
    while time.monotonic() < deadline:
        state_session_id = read_state().get("session_id")
        if state_session_id and state_session_id != session_id:
            return _json_dumps(
                {"ok": True, "detach_requested": True, "detached": True, "host_response": result}
            )
        if not state_session_id:
            missing_state_reads += 1
            if missing_state_reads >= 2:
                return _json_dumps(
                    {
                        "ok": True,
                        "detach_requested": True,
                        "detached": True,
                        "host_response": result,
                    }
                )
        else:
            missing_state_reads = 0
        time.sleep(0.1)
    return _json_dumps(
        {
            "ok": True,
            "detach_requested": True,
            "detached": False,
            "state": "STOPPING",
            "reason": "宿主仍在完成已开始的调用；不要自动重试该写操作",
            "host_response": result,
        }
    )


@mcp.tool(
    name="arcgis_pro_server_capabilities",
    description="",
)
def arcgis_pro_server_capabilities() -> str:
    write = writes_allowed()
    tools_read = [
        "arcgis_pro_environment_info",
        "arcgis_pro_server_capabilities",
        "arcgis_pro_window_status",
        "arcgis_pro_portal_status",
        "arcgis_pro_get_artifact_digest",
        "arcgis_pro_list_projects",
        "arcgis_pro_describe",
        "arcgis_pro_list_fields",
        "arcgis_pro_project_connections",
        "arcgis_pro_project_summary",
        "arcgis_pro_list_maps",
        "arcgis_pro_list_layouts",
        "arcgis_pro_list_reports",
        "arcgis_pro_list_layers",
        "arcgis_pro_list_tables",
        "arcgis_pro_map_spatial_reference",
        "arcgis_pro_map_camera",
        "arcgis_pro_active_view_info",
        "arcgis_pro_list_bookmarks",
        "arcgis_pro_layer_properties",
        "arcgis_pro_list_layout_elements",
        "arcgis_pro_mapframe_extent",
        "arcgis_pro_gp_get_count",
        "arcgis_pro_gp_get_raster_property",
        "arcgis_pro_gp_get_cell_value",
        "arcgis_pro_gp_test_schema_lock",
        "arcgis_pro_gp_list_registered",
        "arcgis_pro_workspace_list_datasets",
        "arcgis_pro_workspace_list_feature_datasets",
        "arcgis_pro_workspace_list_domains",
        "arcgis_pro_workspace_list_feature_classes",
        "arcgis_pro_workspace_list_rasters",
        "arcgis_pro_workspace_list_tables",
        "arcgis_pro_da_table_sample",
        "arcgis_pro_da_query_rows",
        "arcgis_pro_da_distinct_values",
        "arcgis_pro_layer_selection_count",
        "arcgis_pro_layer_selection_fids",
        "arcgis_pro_get_layer_extent",
        "arcgis_pro_list_layer_renderers",
        "arcgis_pro_list_layout_map_frames",
        "arcgis_pro_list_broken_sources",
        "arcgis_pro_list_sde_datasets",
        "arcgis_pro_gp_get_messages",
        "arcgis_pro_gp_list_toolboxes",
        "arcgis_pro_gp_list_tools_in_toolbox",
        "arcgis_pro_get_metadata",
        "arcgis_pro_gp_spatial_autocorrelation",
        "arcgis_pro_gp_average_nearest_neighbor",
        "arcgis_pro_chart_info",
        "arcgis_pro_list_charts",
        "arcgis_pro_chart_mutation_capabilities",
        "arcgis_pro_list_versions",
        "arcgis_pro_dataset_maintenance_info",
    ]
    tools_write = [
        "arcgis_pro_detach_window",
        "arcgis_pro_save_project",
        "arcgis_pro_save_project_copy",
        "arcgis_pro_set_layer_visible",
        "arcgis_pro_set_layer_transparency",
        "arcgis_pro_set_definition_query",
        "arcgis_pro_select_layer_by_attribute",
        "arcgis_pro_make_feature_layer",
        "arcgis_pro_make_table_view",
        "arcgis_pro_mapframe_zoom_to_bookmark",
        "arcgis_pro_add_layer_from_path",
        "arcgis_pro_remove_layer",
        "arcgis_pro_add_table_from_path",
        "arcgis_pro_remove_table",
        "arcgis_pro_rename_map",
        "arcgis_pro_rename_layout",
        "arcgis_pro_create_group_layer",
        "arcgis_pro_move_layer",
        "arcgis_pro_rename_layer",
        "arcgis_pro_set_map_reference_scale",
        "arcgis_pro_set_map_default_camera",
        "arcgis_pro_open_map_view",
        "arcgis_pro_open_layout_view",
        "arcgis_pro_close_views",
        "arcgis_pro_set_active_view_extent",
        "arcgis_pro_zoom_active_view_to_layer",
        "arcgis_pro_zoom_active_view_to_all_layers",
        "arcgis_pro_clip_map_layers",
        "arcgis_pro_refresh_layer",
        "arcgis_pro_select_layer_by_location",
        "arcgis_pro_clear_map_selection",
        "arcgis_pro_add_join",
        "arcgis_pro_remove_join",
        "arcgis_pro_update_layout_text_element",
        "arcgis_pro_set_mapframe_extent",
        "arcgis_pro_set_map_spatial_reference",
        "arcgis_pro_layer_replace_data_source",
        "arcgis_pro_apply_symbology_from_layer",
        "arcgis_pro_set_layer_scale_range",
        "arcgis_pro_toggle_layer_labels",
        "arcgis_pro_da_update_field_constant",
        "arcgis_pro_da_insert_features",
        "arcgis_pro_da_update_features",
        "arcgis_pro_da_delete_selected",
        "arcgis_pro_gp_buffer",
        "arcgis_pro_gp_clip",
        "arcgis_pro_gp_analysis_select",
        "arcgis_pro_gp_copy_features",
        "arcgis_pro_gp_dissolve",
        "arcgis_pro_gp_intersect",
        "arcgis_pro_gp_union",
        "arcgis_pro_gp_erase",
        "arcgis_pro_gp_spatial_join",
        "arcgis_pro_gp_statistics",
        "arcgis_pro_gp_frequency",
        "arcgis_pro_gp_table_select",
        "arcgis_pro_gp_merge",
        "arcgis_pro_gp_project",
        "arcgis_pro_gp_add_field",
        "arcgis_pro_gp_delete_field",
        "arcgis_pro_gp_export_features",
        "arcgis_pro_gp_export_table",
        "arcgis_pro_gp_near",
        "arcgis_pro_gp_generate_near_table",
        "arcgis_pro_gp_calculate_field",
        "arcgis_pro_gp_calculate_geometry",
        "arcgis_pro_gp_append",
        "arcgis_pro_gp_delete_features",
        "arcgis_pro_gp_truncate_table",
        "arcgis_pro_gp_create_feature_class",
        "arcgis_pro_gp_create_table",
        "arcgis_pro_gp_create_file_gdb",
        "arcgis_pro_gp_create_feature_dataset",
        "arcgis_pro_gp_copy_feature_class",
        "arcgis_pro_gp_rename_dataset",
        "arcgis_pro_gp_delete_dataset",
        "arcgis_pro_gp_alter_field",
        "arcgis_pro_gp_import_csv_to_table",
        "arcgis_pro_gp_table_to_table",
        "arcgis_pro_gp_xy_table_to_point",
        "arcgis_pro_gp_json_to_features",
        "arcgis_pro_gp_features_to_json",
        "arcgis_pro_gp_kml_to_layer",
        "arcgis_pro_gp_excel_to_table",
        "arcgis_pro_gp_table_to_excel",
        "arcgis_pro_gp_feature_class_to_shapefile",
        "arcgis_pro_gp_multiple_ring_buffer",
        "arcgis_pro_gp_feature_to_point",
        "arcgis_pro_gp_feature_to_line",
        "arcgis_pro_gp_points_to_line",
        "arcgis_pro_gp_polygon_to_line",
        "arcgis_pro_gp_minimum_bounding_geometry",
        "arcgis_pro_gp_convex_hull",
        "arcgis_pro_gp_split_by_attributes",
        "arcgis_pro_gp_identity",
        "arcgis_pro_gp_symmetrical_difference",
        "arcgis_pro_gp_count_overlapping_features",
        "arcgis_pro_gp_repair_geometry",
        "arcgis_pro_gp_check_geometry",
        "arcgis_pro_gp_eliminate",
        "arcgis_pro_gp_multipart_to_singlepart",
        "arcgis_pro_gp_aggregate_polygons",
        "arcgis_pro_gp_slope",
        "arcgis_pro_gp_aspect",
        "arcgis_pro_gp_hillshade",
        "arcgis_pro_gp_reclassify",
        "arcgis_pro_gp_extract_by_mask",
        "arcgis_pro_gp_extract_by_attributes",
        "arcgis_pro_gp_zonal_statistics_as_table",
        "arcgis_pro_gp_kernel_density",
        "arcgis_pro_gp_point_density",
        "arcgis_pro_gp_idw",
        "arcgis_pro_gp_kriging",
        "arcgis_pro_gp_topo_to_raster",
        "arcgis_pro_gp_raster_to_polygon",
        "arcgis_pro_gp_polygon_to_raster",
        "arcgis_pro_gp_feature_to_raster",
        "arcgis_pro_gp_raster_calculator",
        "arcgis_pro_gp_mosaic_to_new_raster",
        "arcgis_pro_gp_clip_raster",
        "arcgis_pro_gp_resample",
        "arcgis_pro_gp_project_raster",
        "arcgis_pro_gp_nibble",
        "arcgis_pro_set_unique_value_renderer",
        "arcgis_pro_set_graduated_colors_renderer",
        "arcgis_pro_set_graduated_symbols_renderer",
        "arcgis_pro_set_simple_renderer",
        "arcgis_pro_set_heatmap_renderer",
        "arcgis_pro_update_label_expression",
        "arcgis_pro_set_label_font",
        "arcgis_pro_set_layout_element_position",
        "arcgis_pro_set_layout_element_visible",
        "arcgis_pro_update_legend_items",
        "arcgis_pro_create_layout",
        "arcgis_pro_remove_layout",
        "arcgis_pro_zoom_to_layer",
        "arcgis_pro_zoom_to_selection",
        "arcgis_pro_layer_add_field_alias",
        "arcgis_pro_update_layer_cim",
        "arcgis_pro_repair_layer_source",
        "arcgis_pro_create_db_connection",
        "arcgis_pro_add_basemap",
        "arcgis_pro_create_map",
        "arcgis_pro_remove_map",
        "arcgis_pro_duplicate_map",
        "arcgis_pro_map_pan_to_extent",
        "arcgis_pro_set_time_slider",
        "arcgis_pro_gp_run_tool",
        "arcgis_pro_na_create_route_layer",
        "arcgis_pro_na_add_locations",
        "arcgis_pro_na_solve",
        "arcgis_pro_na_service_area",
        "arcgis_pro_na_od_matrix",
        "arcgis_pro_set_metadata",
        "arcgis_pro_gp_validate_topology",
        "arcgis_pro_gp_hot_spots",
        "arcgis_pro_gp_optimized_hot_spots",
        "arcgis_pro_gp_cluster_outlier",
        "arcgis_pro_gp_multi_distance_spatial_clustering",
        "arcgis_pro_gp_ordinary_least_squares",
        "arcgis_pro_gp_gwr",
        "arcgis_pro_gp_forest",
        "arcgis_pro_gp_central_feature",
        "arcgis_pro_gp_mean_center",
        "arcgis_pro_gp_directional_distribution",
        "arcgis_pro_gp_create_random_points",
        "arcgis_pro_gp_generate_tessellation",
        "arcgis_pro_create_sharing_draft",
        "arcgis_pro_stage_service_definition",
        "arcgis_pro_publish_service_definition",
        "arcgis_pro_upsert_chart",
        "arcgis_pro_export_chart",
        "arcgis_pro_create_version",
        "arcgis_pro_change_version",
        "arcgis_pro_reconcile_versions",
        "arcgis_pro_post_version",
        "arcgis_pro_delete_version",
        "arcgis_pro_register_as_versioned",
        "arcgis_pro_unregister_as_versioned",
        "arcgis_pro_add_index",
        "arcgis_pro_remove_index",
        "arcgis_pro_rebuild_indexes",
        "arcgis_pro_analyze_datasets",
        "arcgis_pro_enable_editor_tracking",
        "arcgis_pro_disable_editor_tracking",
        "arcgis_pro_add_global_ids",
    ]
    tools_export = [
        "arcgis_pro_save_project_copy",
        "arcgis_pro_export_layout_pdf",
        "arcgis_pro_export_layout_image",
        "arcgis_pro_export_report_pdf",
        "arcgis_pro_export_map_to_image",
        "arcgis_pro_export_active_view_image",
        "arcgis_pro_export_bookmarks",
        "arcgis_pro_export_map_series_pdf",
        "arcgis_pro_export_mapx",
        "arcgis_pro_save_layer_file",
        "arcgis_pro_export_subnetwork",
        "arcgis_pro_get_artifact_digest",
        "arcgis_pro_create_sharing_draft",
        "arcgis_pro_stage_service_definition",
        "arcgis_pro_export_chart",
    ]
    tools_require_allow_publish = [
        "arcgis_pro_create_sharing_draft",
        "arcgis_pro_stage_service_definition",
        "arcgis_pro_publish_service_definition",
    ]
    tools_require_allow_public_share_when_everyone = list(tools_require_allow_publish)
    tools_require_allow_publish_overwrite_when_overwrite = list(tools_require_allow_publish)
    tools_require_allow_enterprise_write = [
        "arcgis_pro_create_version",
        "arcgis_pro_change_version",
        "arcgis_pro_reconcile_versions",
        "arcgis_pro_post_version",
        "arcgis_pro_delete_version",
        "arcgis_pro_register_as_versioned",
        "arcgis_pro_unregister_as_versioned",
        "arcgis_pro_add_index",
        "arcgis_pro_remove_index",
        "arcgis_pro_rebuild_indexes",
        "arcgis_pro_analyze_datasets",
        "arcgis_pro_enable_editor_tracking",
        "arcgis_pro_disable_editor_tracking",
        "arcgis_pro_add_global_ids",
        "arcgis_pro_validate_utility_network_topology",
        "arcgis_pro_update_subnetwork",
    ]
    tools_require_allow_destructive = [
        "arcgis_pro_reconcile_versions",
        "arcgis_pro_post_version",
        "arcgis_pro_delete_version",
        "arcgis_pro_unregister_as_versioned",
        "arcgis_pro_remove_index",
        "arcgis_pro_disable_editor_tracking",
    ]
    tools_require_window = [
        "arcgis_pro_detach_window",
        "arcgis_pro_active_view_info",
        "arcgis_pro_open_map_view",
        "arcgis_pro_open_layout_view",
        "arcgis_pro_close_views",
        "arcgis_pro_set_active_view_extent",
        "arcgis_pro_zoom_active_view_to_layer",
        "arcgis_pro_zoom_active_view_to_all_layers",
        "arcgis_pro_clip_map_layers",
        "arcgis_pro_refresh_layer",
        "arcgis_pro_open_report_view",
        "arcgis_pro_open_layer_table_view",
        "arcgis_pro_current_layer_query_rows",
        "arcgis_pro_current_map_run_analysis",
        "arcgis_pro_delete_layer_selection",
        "arcgis_pro_current_layer_set_selection",
        "arcgis_pro_set_active_view_camera",
    ]
    registered_names = sorted(mcp._tool_manager._tools)
    policy_catalog = {
        tool_name: tool_protocol.tool_policy(tool_name)
        for tool_name in registered_names
    }
    read_set = set(tools_read)
    write_set = set(tools_write)
    export_set = set(tools_export)
    publish_set = set(tools_require_allow_publish)
    enterprise_set = set(tools_require_allow_enterprise_write)
    destructive_set = set(tools_require_allow_destructive)
    window_set = set(tools_require_window)
    sdk_bridge_tools: list[str] = []
    sdk_feature_edit_tools: list[str] = []
    sdk_edit_command_tools: list[str] = []
    sdk_discard_tools: list[str] = []
    gp_output_tools: list[str] = []
    input_root_tools: list[str] = []
    for tool_name in registered_names:
        policy = policy_catalog[tool_name]
        if tool_name in read_set or tool_name in write_set or tool_name in export_set:
            pass
        elif policy["read_only"]:
            tools_read.append(tool_name)
            read_set.add(tool_name)
        else:
            tools_write.append(tool_name)
            write_set.add(tool_name)
        gates = set(policy["gates"])
        conditional_gates = {
            item["gate"] for item in policy.get("conditional_gates", [])
        }
        all_gates = gates | conditional_gates
        if "ARCGIS_PRO_MCP_EXPORT_ROOT" in all_gates and tool_name not in export_set:
            tools_export.append(tool_name)
            export_set.add(tool_name)
        if "ARCGIS_PRO_MCP_ALLOW_PUBLISH" in all_gates and tool_name not in publish_set:
            tools_require_allow_publish.append(tool_name)
            publish_set.add(tool_name)
        if (
            "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE" in all_gates
            and tool_name not in enterprise_set
        ):
            tools_require_allow_enterprise_write.append(tool_name)
            enterprise_set.add(tool_name)
        if policy["destructive"] and tool_name not in destructive_set:
            tools_require_allow_destructive.append(tool_name)
            destructive_set.add(tool_name)
        if policy["requires_current"] and tool_name not in window_set:
            tools_require_window.append(tool_name)
            window_set.add(tool_name)
        if policy["requires_sdk_bridge"]:
            sdk_bridge_tools.append(tool_name)
        if "ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS" in all_gates:
            sdk_feature_edit_tools.append(tool_name)
        if "ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS" in all_gates:
            sdk_edit_command_tools.append(tool_name)
        if "ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS" in all_gates:
            sdk_discard_tools.append(tool_name)
        if "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT" in all_gates:
            gp_output_tools.append(tool_name)
        if "ARCGIS_PRO_MCP_INPUT_ROOTS" in all_gates:
            input_root_tools.append(tool_name)
    return _json_dumps(
        {
            "allow_write": write,
            "writes_enabled_by_default": True,
            "writes_disable_env": "ARCGIS_PRO_MCP_ALLOW_WRITE=0",
            "writes_required_env": None,
            "allow_destructive": destructive_allowed(),
            "destructive_required_env": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=1",
            "allow_cim_write": cim_write_allowed(),
            "cim_write_required_env": "ARCGIS_PRO_MCP_ALLOW_CIM_WRITE=1",
            "allow_publish": publish_allowed(),
            "publish_required_env": "ARCGIS_PRO_MCP_ALLOW_PUBLISH=1",
            "allow_public_share": public_share_allowed(),
            "public_share_required_env": "ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE=1",
            "allow_publish_overwrite": publish_overwrite_allowed(),
            "publish_overwrite_required_env": (
                "ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE=1"
            ),
            "allow_enterprise_write": enterprise_write_allowed(),
            "enterprise_write_required_env": (
                "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE=1"
            ),
            "portal_allowlist_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_PORTAL_ALLOWLIST", "").strip(),
            ),
            "server_allowlist_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_SERVER_ALLOWLIST", "").strip(),
            ),
            "db_instance_allowlist_configured": bool(
                os.environ.get(_DB_INSTANCE_ALLOWLIST_ENV, "").strip(),
            ),
            "db_username_configured": bool(os.environ.get(_DB_USERNAME_ENV, "")),
            "db_password_configured": bool(os.environ.get(_DB_PASSWORD_ENV, "")),
            "export_root_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_EXPORT_ROOT", "").strip(),
            ),
            "gp_output_root_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT", "").strip(),
            ),
            "input_roots_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_INPUT_ROOTS", "").strip(),
            ),
            "project_roots_configured": bool(project_roots()),
            "generic_gp_enabled": gp_generic.generic_gp_enabled(),
            "generic_gp_allowlist": gp_generic.generic_gp_allowlist(),
            **_window_status_fields(),
            "tools_read_only": tools_read,
            "tools_require_allow_write": tools_write,
            "tools_export": tools_export,
            "tools_require_allow_publish": tools_require_allow_publish,
            "tools_require_allow_public_share_when_everyone": (
                tools_require_allow_public_share_when_everyone
            ),
            "tools_require_allow_publish_overwrite_when_overwrite": (
                tools_require_allow_publish_overwrite_when_overwrite
            ),
            "tools_require_allow_enterprise_write": (
                tools_require_allow_enterprise_write
            ),
            "tools_require_allow_destructive": tools_require_allow_destructive,
            "tools_require_window": tools_require_window,
            "tools_require_sdk_bridge": sdk_bridge_tools,
            "tools_require_sdk_feature_edits": sdk_feature_edit_tools,
            "tools_require_sdk_edit_commands": sdk_edit_command_tools,
            "tools_require_sdk_discard_edits": sdk_discard_tools,
            "tools_require_gp_output_root": gp_output_tools,
            "tools_require_input_roots": input_root_tools,
            "registered_tool_count": len(registered_names),
            "registered_tools": registered_names,
            "tool_policy": policy_catalog,
            "note": (
                "Python 宿主覆盖 CURRENT 工程、活动视图、选择、布局、图表、数据管理、栅格/网络分析和发布；"
                "原生 DrawComplete、Pro UI Undo/Redo 与可取消 QueuedTask 由可选 ArcGIS Pro SDK Add-In 提供。"
                "若要接管当前 Pro 窗口，请在 Pro 中运行仓库根目录的 接入当前窗口.pyt 或 接入当前窗口.py。"
            ),
        },
    )


@mcp.tool(
    name="arcgis_pro_list_projects",
    description="",
)
def arcgis_pro_list_projects(max_items: int = 100) -> str:
    roots = project_roots()
    if not roots:
        return _json_dumps(
            {
                "roots": [],
                "project_count": 0,
                "projects": [],
                "note": (
                    "未配置 ARCGIS_PRO_MCP_PROJECT_ROOTS（或 INPUT_ROOTS 回退）。"
                    "无法扫描工程；请设置根目录，或直接传入绝对 aprx_path。"
                    "若已在 Pro 运行接入当前窗口.py，可对工程工具使用 aprx_path=CURRENT。"
                ),
            },
        )
    cap = max(1, min(int(max_items), 1000))
    projects: list[str] = []
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if not filename.lower().endswith(".aprx"):
                    continue
                projects.append(normalize_path(os.path.join(dirpath, filename)))
                if len(projects) >= cap:
                    return _json_dumps({"roots": roots, "project_count": len(projects), "projects": projects})
    return _json_dumps({"roots": roots, "project_count": len(projects), "projects": projects})


@mcp.tool(
    name="arcgis_pro_list_maps",
    description="",
)
def arcgis_pro_list_maps(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    maps = list(project.listMaps())
    names = [m.name for m in maps]
    items = [{"name": m.name, "uri": _object_uri(m)} for m in maps]
    return json.dumps(
        {"aprx_path": path, "maps": names, "map_items": items},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_list_layouts",
    description="",
)
def arcgis_pro_list_layouts(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    layouts = list(project.listLayouts())
    names = [lyt.name for lyt in layouts]
    items = [{"name": lyt.name, "uri": _object_uri(lyt)} for lyt in layouts]
    return json.dumps(
        {"aprx_path": path, "layouts": names, "layout_items": items},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_list_reports",
    description="",
)
def arcgis_pro_list_reports(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
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


@mcp.tool(
    name="arcgis_pro_open_report_view",
    description="在已接入的 CURRENT ArcGIS Pro 窗口中打开并激活一个报表视图。",
)
def arcgis_pro_open_report_view(aprx_path: str, report_name: str) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    report = _get_report(project, report_name)
    opener = getattr(report, "openView", None)
    if not callable(opener):
        raise RuntimeError("当前 Report 不支持 openView")
    result = opener()
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "report_name": getattr(report, "name", report_name),
            "view_opened": True,
            "view": str(result) if result is not None else None,
        }
    )


@mcp.tool(
    name="arcgis_pro_describe",
    description="",
)
def arcgis_pro_describe(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    try:
        summary = _describe_summary(arcpy, p)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(_safe_error(ex, 800)) from ex
    return _json_dumps({"dataset_path": p, "describe": summary})


@mcp.tool(
    name="arcgis_pro_list_fields",
    description="",
)
def arcgis_pro_list_fields(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    rows: list[dict[str, Any]] = []
    try:
        for f in arcpy.ListFields(p):
            row: dict[str, Any] = {"name": f.name, "type": f.type}
            for attr in ("aliasName", "length", "precision", "scale", "isNullable", "editable", "required"):
                try:
                    row[attr] = getattr(f, attr, None)
                except Exception:  # noqa: BLE001
                    pass
            try:
                dom = f.domain
                if dom:
                    row["domain"] = str(dom)[:500]
            except Exception:  # noqa: BLE001
                pass
            rows.append(row)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(_safe_error(ex, 800)) from ex
    return _json_dumps({"dataset_path": p, "fields": rows, "field_count": len(rows)})


@mcp.tool(
    name="arcgis_pro_project_connections",
    description="",
)
def arcgis_pro_project_connections(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    out: dict[str, Any] = {
        "aprx_path": path,
        "folder_connections": _redact_sensitive(
            list(getattr(project, "folderConnections", None) or [])
        ),
        "databases": _redact_sensitive(
            list(getattr(project, "databases", None) or [])
        ),
        "toolboxes": _redact_sensitive(
            list(getattr(project, "toolboxes", None) or [])
        ),
        "styles": list(getattr(project, "styles", None) or []),
        "default_geodatabase": getattr(project, "defaultGeodatabase", None),
        "default_toolbox": getattr(project, "defaultToolbox", None),
        "home_folder": getattr(project, "homeFolder", None),
    }
    return _json_dumps(out)


@mcp.tool(
    name="arcgis_pro_add_folder_connection",
    description="向工程目录追加一个受输入根约束的文件夹连接，可设为 home folder。",
)
def arcgis_pro_add_folder_connection(
    aprx_path: str,
    folder_path: str,
    alias: str = "",
    make_home: bool = False,
    validate: bool = True,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_catalog.add_folder_connection(
        project,
        folder_path,
        alias,
        make_home=make_home,
        validate=validate,
    )
    result["aprx_path"] = path
    return _json_dumps(_redact_sensitive(result))


@mcp.tool(
    name="arcgis_pro_remove_folder_connection",
    description="移除精确文件夹连接；拒绝当前 home folder，并要求破坏性开关和路径确认。",
)
def arcgis_pro_remove_folder_connection(
    aprx_path: str,
    folder_path: str,
    confirm_folder_path: str,
) -> str:
    require_allow_destructive()
    if confirm_folder_path != folder_path:
        raise RuntimeError("confirm_folder_path 必须与 folder_path 完全一致")
    _, project, path = _open_project(aprx_path)
    result = project_catalog.remove_folder_connection(project, folder_path)
    result["aprx_path"] = path
    return _json_dumps(_redact_sensitive(result))


@mcp.tool(
    name="arcgis_pro_add_database_connection",
    description="向工程目录追加一个受输入根约束的数据库连接，可设为默认地理数据库。",
)
def arcgis_pro_add_database_connection(
    aprx_path: str,
    database_path: str,
    make_default: bool = False,
    validate: bool = True,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_catalog.add_database(
        project,
        database_path,
        make_default=make_default,
        validate=validate,
    )
    result["aprx_path"] = path
    return _json_dumps(_redact_sensitive(result))


@mcp.tool(
    name="arcgis_pro_remove_database_connection",
    description="移除精确数据库连接；拒绝默认数据库，并要求破坏性开关和路径确认。",
)
def arcgis_pro_remove_database_connection(
    aprx_path: str,
    database_path: str,
    confirm_database_path: str,
) -> str:
    require_allow_destructive()
    if confirm_database_path != database_path:
        raise RuntimeError("confirm_database_path 必须与 database_path 完全一致")
    _, project, path = _open_project(aprx_path)
    result = project_catalog.remove_database(project, database_path)
    result["aprx_path"] = path
    return _json_dumps(_redact_sensitive(result))


@mcp.tool(
    name="arcgis_pro_add_project_toolbox",
    description="向工程目录追加一个受控 .atbx/.tbx 工具箱，可设为默认工具箱；拒绝可执行 .pyt。",
)
def arcgis_pro_add_project_toolbox(
    aprx_path: str,
    toolbox_path: str,
    make_default: bool = False,
    validate: bool = True,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_catalog.add_toolbox(
        project,
        toolbox_path,
        make_default=make_default,
        validate=validate,
    )
    result["aprx_path"] = path
    return _json_dumps(_redact_sensitive(result))


@mcp.tool(
    name="arcgis_pro_remove_project_toolbox",
    description="移除精确工程工具箱；拒绝默认工具箱，并要求破坏性开关和路径确认。",
)
def arcgis_pro_remove_project_toolbox(
    aprx_path: str,
    toolbox_path: str,
    confirm_toolbox_path: str,
) -> str:
    require_allow_destructive()
    if confirm_toolbox_path != toolbox_path:
        raise RuntimeError("confirm_toolbox_path 必须与 toolbox_path 完全一致")
    _, project, path = _open_project(aprx_path)
    result = project_catalog.remove_toolbox(project, toolbox_path)
    result["aprx_path"] = path
    return _json_dumps(_redact_sensitive(result))


@mcp.tool(
    name="arcgis_pro_add_project_style",
    description="向工程追加系统样式名称或受输入根约束的 .stylx 文件。",
)
def arcgis_pro_add_project_style(aprx_path: str, style: str) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_catalog.add_style(project, style)
    result["aprx_path"] = path
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_remove_project_style",
    description="移除一个精确工程样式；需要破坏性开关和 confirm_style 精确匹配。",
)
def arcgis_pro_remove_project_style(
    aprx_path: str,
    style: str,
    confirm_style: str,
) -> str:
    require_allow_destructive()
    if confirm_style != style:
        raise RuntimeError("confirm_style 必须与 style 完全一致")
    _, project, path = _open_project(aprx_path)
    result = project_catalog.remove_style(project, style)
    result["aprx_path"] = path
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_list_style_items",
    description="按样式、style class 和 wildcard 列出项目可用符号/颜色等 StyleItem。",
)
def arcgis_pro_list_style_items(
    aprx_path: str,
    style: str,
    style_class: str,
    wildcard: str = "*",
    max_items: int = 200,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_catalog.list_style_items(
        project, style, style_class, wildcard, max_items
    )
    result["aprx_path"] = path
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_list_basemaps",
    description="列出工程当前可用的 basemap 名称。",
)
def arcgis_pro_list_basemaps(aprx_path: str, wildcard: str = "*") -> str:
    _, project, path = _open_project(aprx_path)
    pattern = _sanitize_wild_card(wildcard)
    values = [str(value) for value in project.listBasemaps(pattern)]
    return _json_dumps({"aprx_path": path, "count": len(values), "basemaps": values})


@mcp.tool(
    name="arcgis_pro_list_color_ramps",
    description="列出工程当前可用色带的名称和对象标识。",
)
def arcgis_pro_list_color_ramps(aprx_path: str, wildcard: str = "*") -> str:
    _, project, path = _open_project(aprx_path)
    pattern = _sanitize_wild_card(wildcard)
    rows = [
        {"name": getattr(item, "name", str(item))}
        for item in project.listColorRamps(pattern)
    ]
    return _json_dumps({"aprx_path": path, "count": len(rows), "color_ramps": rows})


@mcp.tool(
    name="arcgis_pro_set_project_defaults",
    description="设置工程 home folder、默认地理数据库或默认工具箱；至少提供一项。",
)
def arcgis_pro_set_project_defaults(
    aprx_path: str,
    home_folder: str = "",
    default_geodatabase: str = "",
    default_toolbox: str = "",
) -> str:
    require_allow_write()
    if not any((home_folder, default_geodatabase, default_toolbox)):
        raise RuntimeError("至少提供 home_folder、default_geodatabase 或 default_toolbox")
    _, project, path = _open_project(aprx_path)
    if home_folder:
        project.homeFolder = validate_input_path_optional(home_folder, "home_folder")
    if default_geodatabase:
        project.defaultGeodatabase = validate_input_path_optional(
            default_geodatabase, "default_geodatabase"
        )
    if default_toolbox:
        project.defaultToolbox = project_catalog.validate_safe_toolbox_path(
            default_toolbox, "default_toolbox"
        )
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "home_folder": getattr(project, "homeFolder", None),
            "default_geodatabase": getattr(project, "defaultGeodatabase", None),
            "default_toolbox": getattr(project, "defaultToolbox", None),
        }
    )


@mcp.tool(
    name="arcgis_pro_project_summary",
    description="",
)
def arcgis_pro_project_summary(
    aprx_path: str,
    max_broken_list: int = 50,
) -> str:
    _, project, path = _open_project(aprx_path)
    maps = [m.name for m in project.listMaps()]
    layouts = [lyt.name for lyt in project.listLayouts()]
    reports: list[str] = []
    if hasattr(project, "listReports"):
        reports = [r.name for r in project.listReports()]  # type: ignore[attr-defined]

    try:
        cap = max(0, min(int(max_broken_list), 500))
    except (TypeError, ValueError):
        cap = 50
    broken_items: list[dict[str, Any]] = []
    try:
        broken_layers = project.listBrokenDataSources()
        for lyr in broken_layers[:cap] if cap else broken_layers:
            item: dict[str, Any] = {"name": getattr(lyr, "name", str(lyr))}
            try:
                item["long_name"] = lyr.longName
            except Exception:  # noqa: BLE001
                pass
            try:
                item["data_source"] = _redact_sensitive(str(lyr.dataSource))
            except Exception as ex:  # noqa: BLE001
                item["data_source_error"] = _safe_error(ex, 300)
            broken_items.append(item)
        broken_total = len(broken_layers)
    except Exception as ex:  # noqa: BLE001
        broken_total = -1
        broken_error = _safe_error(ex, 500)

    payload: dict[str, Any] = {
        "aprx_path": path,
        "map_count": len(maps),
        "layout_count": len(layouts),
        "report_count": len(reports),
        "maps": maps,
        "layouts": layouts,
        "reports": reports,
        "broken_data_source_total": broken_total,
        "broken_data_sources_sample": broken_items,
    }
    if broken_total < 0:
        payload["broken_data_sources_error"] = broken_error  # type: ignore[name-defined]

    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_layers",
    description="",
)
def arcgis_pro_list_layers(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)

    layers_out: list[dict[str, Any]] = []
    for lyr in target.listLayers():
        entry: dict[str, Any] = {
            "name": lyr.name,
            "long_name": getattr(lyr, "longName", None),
            "uri": _object_uri(lyr),
            "is_group_layer": lyr.isGroupLayer,
            "visible": lyr.visible,
        }
        try:
            entry["is_feature_layer"] = lyr.isFeatureLayer
        except Exception:  # noqa: BLE001
            pass
        try:
            entry["is_raster_layer"] = lyr.isRasterLayer
        except Exception:  # noqa: BLE001
            pass
        if not lyr.isGroupLayer:
            try:
                entry["data_source"] = _redact_sensitive(str(lyr.dataSource))
            except Exception as ex:  # noqa: BLE001
                entry["data_source_error"] = _safe_error(ex, 500)
        layers_out.append(entry)

    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "layers": layers_out},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_list_tables",
    description="",
)
def arcgis_pro_list_tables(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    if not hasattr(target, "listTables"):
        return json.dumps(
            {
                "aprx_path": path,
                "map_name": map_name,
                "tables": [],
                "note": "当前 Map 对象无 listTables。",
            },
            ensure_ascii=False,
            indent=2,
        )
    tables_out: list[dict[str, Any]] = []
    for tbl in target.listTables():  # type: ignore[attr-defined]
        row: dict[str, Any] = {"name": tbl.name}
        try:
            row["visible"] = tbl.isVisible
        except Exception:  # noqa: BLE001
            pass
        try:
            row["data_source"] = _redact_sensitive(str(tbl.dataSource))
        except Exception as ex:  # noqa: BLE001
            row["data_source_error"] = _safe_error(ex, 500)
        tables_out.append(row)

    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "tables": tables_out},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_map_spatial_reference",
    description="",
)
def arcgis_pro_map_spatial_reference(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    sr = target.spatialReference
    return json.dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "spatial_reference": _spatial_ref_dict(sr),
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_map_camera",
    description="",
)
def arcgis_pro_map_camera(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    cam = getattr(target, "defaultCamera", None)
    if cam is None:
        return json.dumps(
            {"aprx_path": path, "map_name": map_name, "camera": None},
            ensure_ascii=False,
            indent=2,
        )
    out = _camera_dict(cam)
    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "camera": out},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_active_view_info",
    description="读取 ArcGIS Pro 当前窗口的活动地图/布局视图和实时相机范围；仅支持 aprx_path=CURRENT。",
)
def arcgis_pro_active_view_info(aprx_path: str) -> str:
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    return _json_dumps(_active_view_payload(project, path))


@mcp.tool(
    name="arcgis_pro_list_bookmarks",
    description="",
)
def arcgis_pro_list_bookmarks(aprx_path: str, map_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    bookmarks: list[dict[str, Any]] = []
    for bkmk in target.listBookmarks():
        row: dict[str, Any] = {"name": bkmk.name}
        try:
            row["description"] = getattr(bkmk, "description", None)
        except Exception:  # noqa: BLE001
            pass
        try:
            row["has_thumbnail"] = getattr(bkmk, "hasThumbnail", None)
        except Exception:  # noqa: BLE001
            pass
        try:
            bm = getattr(bkmk, "map", None)
            if bm is not None:
                row["map_name"] = getattr(bm, "name", str(bm))
        except Exception:  # noqa: BLE001
            pass
        try:
            mf = bkmk.mapFrame
            if mf is not None:
                row["map_frame"] = getattr(mf, "name", str(mf))
        except Exception:  # noqa: BLE001
            pass
        bookmarks.append(row)

    return json.dumps(
        {"aprx_path": path, "map_name": map_name, "bookmarks": bookmarks},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="arcgis_pro_layer_properties",
    description="",
)
def arcgis_pro_layer_properties(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    lyr = _find_layer(target, layer_name)

    props: dict[str, Any] = {
        "aprx_path": path,
        "map_name": map_name,
        "name": lyr.name,
        "long_name": getattr(lyr, "longName", None),
        "visible": lyr.visible,
    }
    for attr in (
        "isFeatureLayer",
        "isRasterLayer",
        "isGroupLayer",
        "brightness",
        "contrast",
        "transparency",
        "showLabels",
    ):
        try:
            props[attr] = getattr(lyr, attr)
        except Exception:  # noqa: BLE001
            pass
    try:
        props["definition_query"] = lyr.definitionQuery
    except Exception:  # noqa: BLE001
        pass
    try:
        sym = lyr.symbology
        renderer = getattr(sym, "renderer", None)
        colorizer = getattr(sym, "colorizer", None)
        if renderer is not None:
            props["symbology_kind"] = "renderer"
            props["symbology_type"] = str(
                getattr(renderer, "type", None) or type(renderer).__name__
            )
        elif colorizer is not None:
            props["symbology_kind"] = "colorizer"
            props["symbology_type"] = str(
                getattr(colorizer, "type", None) or type(colorizer).__name__
            )
        else:
            props["symbology_supported"] = False
    except Exception as ex:  # noqa: BLE001
        props["symbology_error"] = _safe_error(ex, 300)
    try:
        props["data_source"] = _redact_sensitive(str(lyr.dataSource))
    except Exception as ex:  # noqa: BLE001
        props["data_source_error"] = _safe_error(ex, 500)
    try:
        props["is_snappable"] = lyr.isSnappable
    except Exception:  # noqa: BLE001
        pass
    try:
        props["is_selectable"] = lyr.isSelectable
    except Exception:  # noqa: BLE001
        pass

    return json.dumps(props, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_list_layout_elements",
    description="",
)
def arcgis_pro_list_layout_elements(
    aprx_path: str,
    layout_name: str,
    element_type: str = "",
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
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


@mcp.tool(
    name="arcgis_pro_mapframe_extent",
    description="",
)
def arcgis_pro_mapframe_extent(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _raise_not_found("map frame", mapframe_name, names)

    out: dict[str, Any] = {
        "aprx_path": path,
        "layout_name": layout_name,
        "mapframe_name": mapframe_name,
    }
    try:
        out["map_name"] = mf.map.name
    except Exception as ex:  # noqa: BLE001
        out["map_name_error"] = _safe_error(ex, 200)
    try:
        out["scale"] = float(mf.camera.scale)
    except Exception:  # noqa: BLE001
        pass
    try:
        extent = _mapframe_extent(mf)
        if extent is not None:
            out["extent"] = _extent_dict(extent)
        else:
            out["extent_error"] = "当前 MapFrame/Camera 不支持 getExtent"
    except Exception as ex:  # noqa: BLE001
        out["extent_error"] = _safe_error(ex, 500)

    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool(
    name="arcgis_pro_export_layout_pdf",
    description="",
)
def arcgis_pro_export_layout_pdf(
    aprx_path: str,
    layout_name: str,
    output_pdf_path: str,
    resolution_dpi: int = 300,
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    out_path = validate_new_output_in_export_root(output_pdf_path, "output_pdf_path")
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
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "output_pdf_path": out_path,
            "resolution_dpi": dpi,
        },
    )


_SELECTION_TYPES = frozenset(
    {
        "NEW_SELECTION",
        "ADD_TO_SELECTION",
        "REMOVE_FROM_SELECTION",
        "SUBSET_SELECTION",
        "SWITCH_SELECTION",
        "CLEAR_SELECTION",
    },
)


def _clamp_dpi(resolution_dpi: int) -> int:
    try:
        return max(72, min(int(resolution_dpi), 960))
    except (TypeError, ValueError) as e:
        raise RuntimeError("resolution_dpi 必须为整数") from e


@mcp.tool(
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
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    out_path = validate_new_output_in_export_root(output_path, "output_path")
    fmt = image_format.strip().lower()
    dpi = _clamp_dpi(resolution_dpi)
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

    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "output_path": out_path,
            "image_format": fmt,
            "resolution_dpi": dpi,
        },
    )


@mcp.tool(
    name="arcgis_pro_save_project",
    description="",
)
def arcgis_pro_save_project(aprx_path: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    if bool(getattr(project, "isReadOnly", False)):
        raise RuntimeError("当前工程引用为只读，不能直接保存；请使用 arcgis_pro_save_project_copy")
    project.save()
    return _json_dumps({"ok": True, "aprx_path": path, "saved": True})


@mcp.tool(
    name="arcgis_pro_save_project_copy",
    description="",
)
def arcgis_pro_save_project_copy(aprx_path: str, output_aprx_path: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    out_path = validate_new_output_in_export_root(output_aprx_path, "output_aprx_path")
    if not out_path.lower().endswith(".aprx"):
        raise RuntimeError("output_aprx_path 应以 .aprx 结尾")
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    project.saveACopy(out_path)
    return _json_dumps(
        {"ok": True, "source_aprx": path, "output_aprx": out_path},
    )


@mcp.tool(
    name="arcgis_pro_set_layer_visible",
    description="",
)
def arcgis_pro_set_layer_visible(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    visible: bool,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    lyr.visible = bool(visible)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "visible": lyr.visible,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_layer_transparency",
    description="",
)
def arcgis_pro_set_layer_transparency(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    transparency_percent: int,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    t = max(0, min(int(transparency_percent), 100))
    lyr.transparency = t
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "transparency": t,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_definition_query",
    description="",
)
def arcgis_pro_set_definition_query(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    definition_query: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    sql = definition_query.strip()
    if len(sql) > 8000:
        raise RuntimeError("definition_query 过长（>8000）")
    lyr.definitionQuery = sql
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "definition_query": sql,
        },
    )


@mcp.tool(
    name="arcgis_pro_select_layer_by_attribute",
    description="按属性更新图层选择集，并核验 ArcPy 派生计数与实际选择集一致。",
)
def arcgis_pro_select_layer_by_attribute(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    selection_type: str,
    where_clause: str = "",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    st = selection_type.strip().upper()
    if st not in _SELECTION_TYPES:
        raise RuntimeError(f"selection_type 须为 {sorted(_SELECTION_TYPES)}")
    wc = where_clause.strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause 过长")
    result = arcpy.management.SelectLayerByAttribute(lyr, st, wc or "")
    selected_count, result_count = _verify_selection_result(
        lyr,
        result,
        count_output_index=1,
    )
    refreshed = _refresh_layer_after_window_change(arcpy, aprx_path, lyr)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "selection_type": st,
            "selected_count": selected_count,
            "result_count": result_count,
            "selection_verified": True,
            "ui_refresh_requested": refreshed,
        },
    )


@mcp.tool(
    name="arcgis_pro_make_feature_layer",
    description="",
)
def arcgis_pro_make_feature_layer(
    dataset_path: str,
    out_layer_name: str,
    where_clause: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    name = _validate_view_name(out_layer_name, "out_layer_name")
    wc = (where_clause or "").strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause too long")
    result = arcpy.management.MakeFeatureLayer(p, name, wc or None)
    created_value: Any = name
    try:
        created_value = result.getOutput(0)
    except Exception:  # noqa: BLE001
        pass
    created_name = str(created_value)
    count = gp_allowlist.gp_get_count_layer(arcpy, created_value)
    layer_ref = session_refs.register(
        created_value,
        kind="feature_layer",
        name=created_name,
        source=p,
    )
    return _json_dumps(
        {
            "ok": True,
            "dataset_path": p,
            "layer_name": created_name,
            "layer_ref": layer_ref,
            "count": count,
        }
    )


@mcp.tool(
    name="arcgis_pro_make_table_view",
    description="",
)
def arcgis_pro_make_table_view(
    dataset_path: str,
    out_view_name: str,
    where_clause: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    name = _validate_view_name(out_view_name, "out_view_name")
    wc = (where_clause or "").strip()
    if len(wc) > 8000:
        raise RuntimeError("where_clause too long")
    result = arcpy.management.MakeTableView(p, name, wc or None)
    created_value: Any = name
    try:
        created_value = result.getOutput(0)
    except Exception:  # noqa: BLE001
        pass
    created_name = str(created_value)
    count = gp_allowlist.gp_get_count(arcpy, created_value)
    view_ref = session_refs.register(
        created_value,
        kind="table_view",
        name=created_name,
        source=p,
    )
    return _json_dumps(
        {
            "ok": True,
            "dataset_path": p,
            "view_name": created_name,
            "view_ref": view_ref,
            "count": count,
        }
    )


@mcp.tool(
    name="arcgis_pro_mapframe_zoom_to_bookmark",
    description="",
)
def arcgis_pro_mapframe_zoom_to_bookmark(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    bookmark_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _raise_not_found("mapframe", mapframe_name, names)
    bkmk = None
    try:
        for b in mf.map.listBookmarks():
            if b.name == bookmark_name:
                bkmk = b
                break
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"读取书签失败：{_safe_error(ex, 500)}") from ex
    if bkmk is None:
        names = [b.name for b in mf.map.listBookmarks()]
        _raise_not_found("bookmark", bookmark_name, names)
    mf.zoomToBookmark(bkmk)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "mapframe_name": mapframe_name,
            "bookmark_name": bookmark_name,
        },
    )


@mcp.tool(
    name="arcgis_pro_add_layer_from_path",
    description="",
)
def arcgis_pro_add_layer_from_path(
    aprx_path: str,
    map_name: str,
    data_path: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    dp = validate_input_path_optional(data_path, "data_path")
    m.addDataFromPath(dp)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "data_path": dp},
    )


def _layer_position(value: str) -> str:
    position = (value or "AUTO_ARRANGE").strip().upper()
    if position not in {"AUTO_ARRANGE", "TOP", "BOTTOM"}:
        raise RuntimeError("add_position 须为 AUTO_ARRANGE、TOP 或 BOTTOM")
    return position


@mcp.tool(
    name="arcgis_pro_copy_layer",
    description="把工程中现有图层复制到另一地图的顶层，并返回新图层标识。",
)
def arcgis_pro_copy_layer(
    aprx_path: str,
    source_map_name: str,
    source_layer_name: str,
    target_map_name: str,
    add_position: str = "AUTO_ARRANGE",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    source = _find_layer(_get_map(project, source_map_name), source_layer_name)
    target_map = _get_map(project, target_map_name)
    added = list(target_map.addLayer(source, _layer_position(add_position)) or [])
    if not added:
        raise RuntimeError("Map.addLayer 未返回已添加图层")
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "source_map_name": source_map_name,
            "target_map_name": target_map_name,
            "layers": [
                {"name": getattr(item, "name", None), "uri": _object_uri(item)}
                for item in added
            ],
        }
    )


@mcp.tool(
    name="arcgis_pro_add_layer_to_group",
    description="把工程中的现有图层加入目标地图的组图层，包括空组图层。",
)
def arcgis_pro_add_layer_to_group(
    aprx_path: str,
    target_map_name: str,
    group_layer_name: str,
    source_layer_name: str,
    source_map_name: str = "",
    add_position: str = "AUTO_ARRANGE",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    target_map = _get_map(project, target_map_name)
    group = _find_layer(target_map, group_layer_name)
    if not bool(getattr(group, "isGroupLayer", False)):
        raise RuntimeError("group_layer_name 不是组图层")
    source_map = _get_map(project, source_map_name or target_map_name)
    source = _find_layer(source_map, source_layer_name)
    added = list(
        target_map.addLayerToGroup(group, source, _layer_position(add_position)) or []
    )
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "target_map_name": target_map_name,
            "group_layer_name": getattr(group, "name", group_layer_name),
            "added_count": len(added),
            "layers": [
                {"name": getattr(item, "name", None), "uri": _object_uri(item)}
                for item in added
            ],
        }
    )


@mcp.tool(
    name="arcgis_pro_insert_layer",
    description="在同一地图内以参考图层为锚点精确插入另一个图层（BEFORE/AFTER）。",
)
def arcgis_pro_insert_layer(
    aprx_path: str,
    map_name: str,
    reference_layer_name: str,
    insert_layer_name: str,
    insert_position: str = "BEFORE",
) -> str:
    require_allow_write()
    position = (insert_position or "BEFORE").strip().upper()
    if position not in {"BEFORE", "AFTER"}:
        raise RuntimeError("insert_position 须为 BEFORE 或 AFTER")
    _, project, path = _open_project(aprx_path)
    map_obj = _get_map(project, map_name)
    reference = _find_layer(map_obj, reference_layer_name)
    source = _find_layer(map_obj, insert_layer_name)
    inserted = map_obj.insertLayer(reference, source, position)
    if inserted is None:
        added = []
    elif isinstance(inserted, (list, tuple)):
        added = list(inserted)
    else:
        # ArcPy 3.6 may return a single Layer rather than a list.
        added = [inserted]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "reference_layer_name": getattr(reference, "name", reference_layer_name),
            "insert_position": position,
            "added_count": len(added),
        }
    )


@mcp.tool(
    name="arcgis_pro_add_table_to_group",
    description="把地图中的现有独立表加入目标组图层。",
)
def arcgis_pro_add_table_to_group(
    aprx_path: str,
    map_name: str,
    group_layer_name: str,
    table_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    map_obj = _get_map(project, map_name)
    group = _find_layer(map_obj, group_layer_name)
    if not bool(getattr(group, "isGroupLayer", False)):
        raise RuntimeError("group_layer_name 不是组图层")
    table = _get_table(map_obj, table_name)
    result = map_obj.addTableToGroup(group, table)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "group_layer_name": getattr(group, "name", group_layer_name),
            "table_name": getattr(table, "name", table_name),
            "result": str(result) if result is not None else None,
        }
    )


@mcp.tool(
    name="arcgis_pro_remove_layer",
    description="",
)
def arcgis_pro_remove_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("移除图层需要 confirm=true；源数据不会被删除")
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    m.removeLayer(lyr)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "removed": layer_name},
    )


@mcp.tool(
    name="arcgis_pro_add_table_from_path",
    description="",
)
def arcgis_pro_add_table_from_path(
    aprx_path: str,
    map_name: str,
    table_path: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    tp = validate_input_path_optional(table_path, "table_path")
    if not hasattr(m, "addDataFromPath"):
        raise RuntimeError("当前 Map 对象不支持 addDataFromPath")
    m.addDataFromPath(tp)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "table_path": tp},
    )


@mcp.tool(
    name="arcgis_pro_remove_table",
    description="",
)
def arcgis_pro_remove_table(
    aprx_path: str,
    map_name: str,
    table_name: str,
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("移除表需要 confirm=true；源数据不会被删除")
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    if not hasattr(m, "listTables"):
        raise RuntimeError("当前 Map 对象不支持 listTables")
    target = None
    for tbl in m.listTables():  # type: ignore[attr-defined]
        if tbl.name == table_name:
            target = tbl
            break
    if target is None:
        names = [tbl.name for tbl in m.listTables()]  # type: ignore[attr-defined]
        _raise_not_found("table", table_name, names)
    if hasattr(m, "removeTable"):
        m.removeTable(target)  # type: ignore[attr-defined]
    elif hasattr(m, "removeItem"):
        m.removeItem(target)  # type: ignore[attr-defined]
    else:
        raise RuntimeError("当前 Map 对象不支持移除独立表")
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "removed": table_name},
    )


@mcp.tool(
    name="arcgis_pro_gp_list_registered",
    description="",
)
def arcgis_pro_gp_list_registered() -> str:
    return _json_dumps({"gp_tools": gp_allowlist.list_registered_gp_tools()})


@mcp.tool(
    name="arcgis_pro_gp_get_count",
    description="",
)
def arcgis_pro_gp_get_count(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    cnt = gp_allowlist.gp_get_count(arcpy, p)
    return _json_dumps({"dataset_path": p, "count": cnt})


@mcp.tool(
    name="arcgis_pro_gp_get_raster_property",
    description="",
)
def arcgis_pro_gp_get_raster_property(
    raster_path: str,
    property_type: str,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(raster_path, "raster_path")
    val = gp_allowlist.gp_get_raster_property(arcpy, p, property_type)
    return _json_dumps(
        {"raster_path": p, "property_type": property_type.strip().upper(), "value": val},
    )


@mcp.tool(
    name="arcgis_pro_gp_get_cell_value",
    description="",
)
def arcgis_pro_gp_get_cell_value(
    raster_path: str,
    location_xy: str,
    band_index: int | None = None,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(raster_path, "raster_path")
    val = gp_allowlist.gp_get_cell_value(arcpy, p, location_xy, band_index)
    return _json_dumps(
        {"raster_path": p, "location_xy": location_xy.strip(), "value": val},
    )


@mcp.tool(
    name="arcgis_pro_gp_test_schema_lock",
    description="",
)
def arcgis_pro_gp_test_schema_lock(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    val = gp_allowlist.gp_test_schema_lock(arcpy, p)
    return _json_dumps({"dataset_path": p, "schema_lock": val})


@mcp.tool(
    name="arcgis_pro_workspace_list_feature_classes",
    description="",
)
def arcgis_pro_workspace_list_feature_classes(
    workspace_path: str,
    feature_dataset: str = "",
    feature_type: str = "",
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = workspace_listing.list_feature_classes(
        arcpy, ws, feature_dataset, feature_type, wild_card, max_items
    )
    return _json_dumps({"workspace_path": ws, "feature_classes": names})


@mcp.tool(
    name="arcgis_pro_workspace_list_rasters",
    description="",
)
def arcgis_pro_workspace_list_rasters(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = workspace_listing.list_rasters(arcpy, ws, wild_card, max_items)
    return _json_dumps({"workspace_path": ws, "rasters": names})


@mcp.tool(
    name="arcgis_pro_workspace_list_tables",
    description="",
)
def arcgis_pro_workspace_list_tables(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = workspace_listing.list_tables(arcpy, ws, wild_card, max_items)
    return _json_dumps({"workspace_path": ws, "tables": names})


@mcp.tool(
    name="arcgis_pro_da_table_sample",
    description="",
)
def arcgis_pro_da_table_sample(
    dataset_path: str,
    fields: list[str],
    where_clause: str = "",
    max_rows: int = 50,
    include_shape_wkt: bool = False,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    rows = da_read.table_sample(
        arcpy, p, fields, where_clause, max_rows, include_shape_wkt
    )
    return _json_dumps({"dataset_path": p, "row_count": len(rows), "rows": rows})


@mcp.tool(
    name="arcgis_pro_da_query_rows",
    description="",
)
def arcgis_pro_da_query_rows(
    dataset_path: str,
    fields: list[str],
    where_clause: str = "",
    order_by: str = "",
    max_rows: int = 100,
    offset: int = 0,
    include_shape_wkt: bool = False,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    rows = da_read.query_rows(
        arcpy, p, fields, where_clause, order_by, max_rows, offset, include_shape_wkt
    )
    return _json_dumps(
        {
            "dataset_path": p,
            "field_count": len([f for f in fields if f.strip()]),
            "row_count": len(rows),
            "rows": rows,
        },
    )


@mcp.tool(
    name="arcgis_pro_da_distinct_values",
    description="",
)
def arcgis_pro_da_distinct_values(
    dataset_path: str,
    field_name: str,
    where_clause: str = "",
    max_values: int = 100,
    max_rows_scanned: int = 50000,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    vals = da_read.distinct_values(
        arcpy, p, field_name, where_clause, max_values, max_rows_scanned
    )
    return _json_dumps(
        {
            "dataset_path": p,
            "field_name": field_name.strip(),
            "value_count": len(vals),
            "values": vals,
        },
    )


_PLACE_LAYER = frozenset({"BEFORE", "AFTER"})


@mcp.tool(
    name="arcgis_pro_create_group_layer",
    description="",
)
def arcgis_pro_create_group_layer(aprx_path: str, map_name: str, group_layer_name: str) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    gn = group_layer_name.strip()
    if not gn:
        raise RuntimeError("group_layer_name 不能为空")
    group = m.createGroupLayer(gn)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "group_layer_name": getattr(group, "name", gn),
        },
    )


@mcp.tool(
    name="arcgis_pro_move_layer",
    description="",
)
def arcgis_pro_move_layer(
    aprx_path: str,
    map_name: str,
    reference_layer_name: str,
    layer_to_move_name: str,
    placement: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    ref = _find_layer(m, reference_layer_name)
    mov = _find_layer(m, layer_to_move_name)
    pl = placement.strip().upper()
    if pl not in _PLACE_LAYER:
        raise RuntimeError(f"placement 须为 {sorted(_PLACE_LAYER)}")
    m.moveLayer(ref, mov, pl)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "reference_layer_name": reference_layer_name,
            "layer_to_move_name": layer_to_move_name,
            "placement": pl,
        },
    )


@mcp.tool(
    name="arcgis_pro_rename_layer",
    description="",
)
def arcgis_pro_rename_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    new_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    nn = new_name.strip()
    if not nn:
        raise RuntimeError("new_name 不能为空")
    lyr.name = nn
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "new_name": nn,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_map_reference_scale",
    description="",
)
def arcgis_pro_set_map_reference_scale(
    aprx_path: str,
    map_name: str,
    reference_scale: float,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    rs = float(reference_scale)
    if rs < 0:
        raise RuntimeError("reference_scale 不能为负")
    m.referenceScale = rs  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "reference_scale": rs,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_map_default_camera",
    description="",
)
def arcgis_pro_set_map_default_camera(
    aprx_path: str,
    map_name: str,
    scale: float | None = None,
    heading: float | None = None,
    pitch: float | None = None,
    roll: float | None = None,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    cam = m.defaultCamera
    if cam is None:
        raise RuntimeError("地图无 defaultCamera")
    updated: dict[str, float] = {}
    if scale is not None:
        cam.scale = float(scale)
        updated["scale"] = float(scale)
    if heading is not None:
        cam.heading = float(heading)
        updated["heading"] = float(heading)
    if pitch is not None:
        cam.pitch = float(pitch)
        updated["pitch"] = float(pitch)
    if roll is not None:
        cam.roll = float(roll)
        updated["roll"] = float(roll)
    if not updated:
        raise RuntimeError("至少提供一个 scale/heading/pitch/roll")
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "updated": updated},
    )


@mcp.tool(
    name="arcgis_pro_open_map_view",
    description="在 ArcGIS Pro 当前窗口打开并聚焦指定地图视图；仅支持 aprx_path=CURRENT。",
)
def arcgis_pro_open_map_view(
    aprx_path: str,
    map_name: str,
    close_other_views: bool = False,
) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    target = _get_map(project, map_name)
    if close_other_views:
        project.closeViews("MAPS_AND_LAYOUTS")
    if not hasattr(target, "openView"):
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 Map.openView")
    target.openView()
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "opened_map": map_name,
            "closed_other_views": bool(close_other_views),
        }
    )


@mcp.tool(
    name="arcgis_pro_open_layout_view",
    description="在 ArcGIS Pro 当前窗口打开并聚焦指定布局视图；仅支持 aprx_path=CURRENT。",
)
def arcgis_pro_open_layout_view(
    aprx_path: str,
    layout_name: str,
    close_other_views: bool = False,
) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    if close_other_views:
        project.closeViews("MAPS_AND_LAYOUTS")
    if not hasattr(layout, "openView"):
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 Layout.openView")
    layout.openView()
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "opened_layout": layout_name,
            "closed_other_views": bool(close_other_views),
        }
    )


@mcp.tool(
    name="arcgis_pro_close_views",
    description="关闭 ArcGIS Pro 当前窗口中的地图、布局、报表或表视图；仅支持 aprx_path=CURRENT。",
)
def arcgis_pro_close_views(aprx_path: str, view_type: str = "MAPS_AND_LAYOUTS") -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    normalized = view_type.strip().upper()
    allowed = {"MAPS", "LAYOUTS", "MAPS_AND_LAYOUTS", "REPORTS", "TABLES"}
    if normalized not in allowed:
        raise RuntimeError(f"view_type 须为 {sorted(allowed)} 之一")
    active_view = getattr(project, "activeView", None)
    active_category = ""
    if active_view is not None:
        if getattr(active_view, "map", None) is not None and getattr(active_view, "camera", None) is not None:
            active_category = "MAPS"
        elif hasattr(active_view, "listElements"):
            active_category = "LAYOUTS"
        else:
            active_type = type(active_view).__name__.upper()
            if "REPORT" in active_type:
                active_category = "REPORTS"
            elif "TABLE" in active_type:
                active_category = "TABLES"
    targeted_categories = {
        "MAPS": {"MAPS"},
        "LAYOUTS": {"LAYOUTS"},
        "MAPS_AND_LAYOUTS": {"MAPS", "LAYOUTS"},
        "REPORTS": {"REPORTS"},
        "TABLES": {"TABLES"},
    }[normalized]
    if active_category in targeted_categories:
        raise RuntimeError(
            "为保护前台 CURRENT 宿主，拒绝关闭当前活动视图。"
            "请先打开不属于 view_type 的另一类视图，再重试关闭非活动视图类别"
        )
    project.closeViews(normalized)
    return _json_dumps({"ok": True, "aprx_path": path, "closed_view_type": normalized})


@mcp.tool(
    name="arcgis_pro_set_active_view_extent",
    description="实时设置或平移 ArcGIS Pro 当前地图视图范围；preserve_scale=true 时保持比例尺。",
)
def arcgis_pro_set_active_view_extent(
    aprx_path: str,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    spatial_reference_wkid: int | None = None,
    preserve_scale: bool = False,
) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    arcpy, project, path = _open_project(aprx_path)
    view, active_map = _active_map_view(project)
    x0, y0, x1, y1 = float(xmin), float(ymin), float(xmax), float(ymax)
    if x0 >= x1 or y0 >= y1:
        raise RuntimeError("范围必须满足 xmin < xmax 且 ymin < ymax")
    spatial_reference = None
    if spatial_reference_wkid is not None:
        spatial_reference = arcpy.SpatialReference(int(spatial_reference_wkid))
    else:
        try:
            spatial_reference = active_map.spatialReference
        except Exception:  # noqa: BLE001
            pass
    if spatial_reference is None:
        extent = arcpy.Extent(x0, y0, x1, y1)
    else:
        extent = arcpy.Extent(
            x0,
            y0,
            x1,
            y1,
            None,
            None,
            None,
            None,
            spatial_reference,
        )
    if preserve_scale:
        if not hasattr(view, "panToExtent"):
            raise RuntimeError("当前活动视图不支持 panToExtent")
        view.panToExtent(extent)
    else:
        view.camera.setExtent(extent)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(active_map, "name", None),
            "preserve_scale": bool(preserve_scale),
            "camera": _camera_dict(view.camera),
        }
    )


@mcp.tool(
    name="arcgis_pro_zoom_active_view_to_layer",
    description="实时将 ArcGIS Pro 当前地图视图缩放到指定图层或其选择集。",
)
def arcgis_pro_zoom_active_view_to_layer(
    aprx_path: str,
    layer_name: str,
    selection_only: bool = False,
    symbolized_extent: bool = True,
) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    view, active_map = _active_map_view(project)
    layer = _find_layer(active_map, layer_name)
    if selection_only:
        try:
            if not layer.getSelectionSet():
                raise RuntimeError(f"图层 {layer_name!r} 当前选择集为空")
        except AttributeError as exc:
            raise RuntimeError(f"图层 {layer_name!r} 不支持选择集") from exc
    if not hasattr(view, "getLayerExtent"):
        raise RuntimeError("当前活动视图不支持 getLayerExtent")
    extent = view.getLayerExtent(layer, bool(selection_only), bool(symbolized_extent))
    if extent is None:
        raise RuntimeError(f"无法读取图层 {layer_name!r} 的视图范围")
    view.camera.setExtent(extent)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(active_map, "name", None),
            "layer_name": layer_name,
            "selection_only": bool(selection_only),
            "camera": _camera_dict(view.camera),
        }
    )


@mcp.tool(
    name="arcgis_pro_clip_map_layers",
    description="按多边形图层轮廓裁剪当前地图的图层显示（含底图），不改数据。layer_name 为空则取消裁剪。",
)
def arcgis_pro_clip_map_layers(
    aprx_path: str,
    layer_name: str = "",
    selection: str = "ALL",
) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    _view, active_map = _active_map_view(project)
    sel = (selection or "ALL").strip().upper()
    if sel not in {"ALL", "SELECTED"}:
        raise RuntimeError("selection 须为 ALL 或 SELECTED")
    clip_name = (layer_name or "").strip()
    if not clip_name:
        if not hasattr(active_map, "clipLayers"):
            raise RuntimeError("当前 Map 不支持 clipLayers")
        active_map.clipLayers(None)
        return _json_dumps({"ok": True, "aprx_path": path, "map_name": active_map.name, "clipped": False})
    layer = _find_layer(active_map, clip_name)
    if not hasattr(active_map, "clipLayers"):
        raise RuntimeError("当前 Map 不支持 clipLayers")
    active_map.clipLayers(layer, sel)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": active_map.name,
            "layer_name": layer.name,
            "selection": sel,
            "clipped": True,
        }
    )


@mcp.tool(
    name="arcgis_pro_zoom_active_view_to_all_layers",
    description="实时将 ArcGIS Pro 当前地图视图缩放到全部图层或已有选择集。",
)
def arcgis_pro_zoom_active_view_to_all_layers(
    aprx_path: str,
    selection_only: bool = False,
    symbolized_extent: bool = True,
) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    view, active_map = _active_map_view(project)
    if not hasattr(view, "zoomToAllLayers"):
        raise RuntimeError("当前活动视图不支持 zoomToAllLayers")
    view.zoomToAllLayers(bool(selection_only), bool(symbolized_extent))
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(active_map, "name", None),
            "selection_only": bool(selection_only),
            "camera": _camera_dict(view.camera),
        }
    )


@mcp.tool(
    name="arcgis_pro_refresh_layer",
    description="刷新 ArcGIS Pro 当前窗口中包含指定图层的可见地图视图。",
)
def arcgis_pro_refresh_layer(aprx_path: str, map_name: str, layer_name: str) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    arcpy, project, path = _open_project(aprx_path)
    target_map = _get_map(project, map_name)
    layer = _find_layer(target_map, layer_name)
    refresh = getattr(arcpy, "RefreshLayer", None)
    if not callable(refresh):
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 arcpy.RefreshLayer")
    actual_name = str(getattr(layer, "name", layer_name))
    refresh(actual_name)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": actual_name}
    )


_OVERLAP_LOCATION = frozenset(
    {
        "INTERSECT",
        "WITHIN_A_DISTANCE",
        "WITHIN_A_DISTANCE_GEODESIC",
        "WITHIN_A_DISTANCE_3D",
        "CONTAINS",
        "COMPLETELY_CONTAINS",
        "COMPLETELY_WITHIN",
        "HAVE_THEIR_CENTER_IN",
        "SHARE_A_LINE_SEGMENT_WITH",
        "CROSSED_BY_THE_OUTLINE_OF",
        "BOUNDARY_TOUCHES",
        "ARE_IDENTICAL_TO",
        "TOUCHES",
        "OVERLAP",
        "CROSSES",
        "WITHIN",
    },
)
_DISTANCE_OVERLAP = frozenset(
    {"WITHIN_A_DISTANCE", "WITHIN_A_DISTANCE_GEODESIC", "WITHIN_A_DISTANCE_3D"},
)
_JOIN_TYPES = frozenset({"KEEP_ALL", "KEEP_COMMON"})


@mcp.tool(
    name="arcgis_pro_select_layer_by_location",
    description="按空间关系更新图层选择集，并核验 ArcPy 派生计数与实际选择集一致。",
)
def arcgis_pro_select_layer_by_location(
    aprx_path: str,
    map_name: str,
    input_layer_name: str,
    overlap_type: str,
    selecting_layer_name: str,
    search_distance: str = "",
    selection_type: str = "NEW_SELECTION",
    invert_spatial_relationship: bool = False,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    input_lyr = _find_layer(m, input_layer_name)
    sel_lyr = _find_layer(m, selecting_layer_name)
    ov = overlap_type.strip().upper()
    if ov not in _OVERLAP_LOCATION:
        raise RuntimeError(f"overlap_type 须为 {sorted(_OVERLAP_LOCATION)}")
    sd = (search_distance or "").strip()
    if ov in _DISTANCE_OVERLAP and not sd:
        raise RuntimeError("当前 overlap_type 必须提供 search_distance")
    st = selection_type.strip().upper()
    if st not in _SELECTION_TYPES:
        raise RuntimeError(f"selection_type 须为 {sorted(_SELECTION_TYPES)}")
    inv = "INVERT" if invert_spatial_relationship else "NOT_INVERT"
    result = arcpy.management.SelectLayerByLocation(
        input_lyr,
        ov,
        sel_lyr,
        sd,
        st,
        inv,
    )
    selected_count, result_count = _verify_selection_result(
        input_lyr,
        result,
        count_output_index=2,
    )
    refreshed = _refresh_layer_after_window_change(arcpy, aprx_path, input_lyr)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "input_layer_name": input_layer_name,
            "selecting_layer_name": selecting_layer_name,
            "overlap_type": ov,
            "selection_type": st,
            "selected_count": selected_count,
            "result_count": result_count,
            "selection_verified": True,
            "ui_refresh_requested": refreshed,
        },
    )


@mcp.tool(
    name="arcgis_pro_clear_map_selection",
    description="清除指定图层或地图全部要素图层的选择，并确认实际选择集为空。",
)
def arcgis_pro_clear_map_selection(
    aprx_path: str,
    map_name: str,
    scope: str = "all_layers",
    layer_name: str = "",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    sc = scope.strip().lower()
    if sc == "map":
        sc = "all_layers"
    cleared = 0
    if sc == "layer":
        ln = layer_name.strip()
        if not ln:
            raise RuntimeError("scope=layer 时必须提供 layer_name")
        lyr = _find_layer(m, ln)
        arcpy.management.SelectLayerByAttribute(lyr, "CLEAR_SELECTION", "")
        if _layer_selection_set(lyr):
            raise RuntimeError("ArcPy 清除选择后图层仍有选中要素，拒绝报告成功")
        _refresh_layer_after_window_change(arcpy, aprx_path, lyr)
        cleared = 1
    elif sc == "all_layers":
        for lyr in m.listLayers():
            if getattr(lyr, "isGroupLayer", False):
                continue
            if getattr(lyr, "isFeatureLayer", False):
                try:
                    arcpy.management.SelectLayerByAttribute(lyr, "CLEAR_SELECTION", "")
                    if _layer_selection_set(lyr):
                        raise RuntimeError("清除后仍有选中要素")
                    _refresh_layer_after_window_change(arcpy, aprx_path, lyr)
                except Exception as exc:  # noqa: BLE001
                    layer_label = str(getattr(lyr, "longName", None) or getattr(lyr, "name", ""))
                    raise RuntimeError(
                        f"清除图层 {layer_label!r} 的选择失败；"
                        f"此前已清除 {cleared} 个图层：{_safe_error(exc, 300)}"
                    ) from exc
                cleared += 1
    else:
        raise RuntimeError('scope 须为 "layer" 或 "all_layers"')
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layers_cleared": cleared,
            "selection_verified": True,
        },
    )


@mcp.tool(
    name="arcgis_pro_layer_selection_count",
    description="读取图层 getSelectionSet 返回的准确选中要素数量，不回退为总行数。",
)
def arcgis_pro_layer_selection_count(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    cnt = len(_layer_selection_set(lyr))
    return _json_dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "selected_count": cnt,
            "selected_or_total_count": cnt,
            "selection_verified": True,
        },
    )


@mcp.tool(
    name="arcgis_pro_layer_selection_fids",
    description="读取图层 getSelectionSet 返回的准确选中要素 ID，可限制返回数量。",
)
def arcgis_pro_layer_selection_fids(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    max_fids: int = 2000,
) -> str:
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    cap = max(1, min(int(max_fids), 50_000))
    selected = _layer_selection_set(lyr)
    try:
        ordered = sorted(selected)
    except TypeError:
        ordered = sorted(selected, key=lambda value: (type(value).__name__, str(value)))
    fids = ordered[:cap]
    truncated = len(ordered) > cap
    digest = hashlib.sha256(
        json.dumps(ordered, ensure_ascii=False, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return _json_dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "fids": fids,
            "selected_count": len(ordered),
            "selection_digest": digest,
            "selection_verified": True,
            "truncated": truncated,
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_buffer",
    description="",
)
def arcgis_pro_gp_buffer(
    in_features: str,
    out_feature_class: str,
    buffer_distance_or_field: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_buffer(arcpy, in_features, out_feature_class, buffer_distance_or_field)
    return _json_dumps(
        {
            "ok": True,
            "in_features": validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_clip",
    description="",
)
def arcgis_pro_gp_clip(
    in_features: str,
    clip_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_clip(arcpy, in_features, clip_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_features": validate_input_path_optional(in_features, "in_features"),
            "clip_features": validate_input_path_optional(clip_features, "clip_features"),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_analysis_select",
    description="",
)
def arcgis_pro_gp_analysis_select(
    in_features: str,
    out_feature_class: str,
    where_clause: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_select(arcpy, in_features, out_feature_class, where_clause)
    return _json_dumps(
        {
            "ok": True,
            "in_features": validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_copy_features",
    description="",
)
def arcgis_pro_gp_copy_features(in_features: str, out_feature_class: str) -> str:
    arcpy = _arcpy()
    gp_write.run_copy_features(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_features": validate_input_path_optional(in_features, "in_features"),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_add_join",
    description="",
)
def arcgis_pro_add_join(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    layer_field: str,
    join_table_path: str,
    join_field: str,
    join_type: str = "KEEP_ALL",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    jt = join_type.strip().upper()
    if jt not in _JOIN_TYPES:
        raise RuntimeError(f"join_type 须为 {sorted(_JOIN_TYPES)}")
    jpath = validate_input_path_optional(join_table_path, "join_table_path")
    arcpy.management.AddJoin(lyr, layer_field.strip(), jpath, join_field.strip(), jt)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "join_table_path": jpath,
        },
    )


@mcp.tool(
    name="arcgis_pro_remove_join",
    description="",
)
def arcgis_pro_remove_join(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    join_name: str,
    confirm_join_name: str,
) -> str:
    require_allow_destructive()
    if not join_name.strip():
        raise RuntimeError("join_name 必须精确指定一个 join；MCP 不提供一次移除全部 join")
    if confirm_join_name != join_name:
        raise RuntimeError("confirm_join_name 必须逐字符精确回显 join_name")
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    jn = join_name.strip()
    arcpy.management.RemoveJoin(lyr, jn)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "join_name": jn,
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
    _raise_not_found("text element", en, available)


@mcp.tool(
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
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    elm = _find_layout_text_element(layout, element_name, element_type)
    old = getattr(elm, "text", "") or ""
    if "<dyn" in old.lower() and not allow_dynamic_text_overwrite:
        raise RuntimeError("检测到动态文本，若需覆盖请设置 allow_dynamic_text_overwrite=true")
    elm.text = text
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": layout_name,
            "element_name": element_name.strip(),
        },
    )


@mcp.tool(
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
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _raise_not_found("map frame", mapframe_name, names)
    ext = arcpy.Extent(float(xmin), float(ymin), float(xmax), float(ymax))  # type: ignore[attr-defined]
    if spatial_reference_wkid is not None:
        ext.spatialReference = arcpy.SpatialReference(int(spatial_reference_wkid))  # type: ignore[attr-defined]
    else:
        try:
            ext.spatialReference = mf.map.spatialReference
        except Exception:  # noqa: BLE001
            pass
    _set_mapframe_extent(mf, ext)
    out: dict[str, Any] = {
        "ok": True,
        "aprx_path": path,
        "layout_name": layout_name,
        "mapframe_name": mapframe_name,
    }
    try:
        out["extent_after"] = _extent_dict(_mapframe_extent(mf))
    except Exception as ex:  # noqa: BLE001
        out["extent_read_error"] = _safe_error(ex, 300)
    return _json_dumps(out)


def _symbology_template_path(path: str) -> str:
    p = validate_input_path_optional(path, "symbology_layer_path")
    pl = p.lower()
    if not (pl.endswith(".lyrx") or pl.endswith(".lyr")):
        raise RuntimeError("symbology_layer_path 须为 .lyrx 或 .lyr")
    return p


@mcp.tool(
    name="arcgis_pro_set_map_spatial_reference",
    description="",
)
def arcgis_pro_set_map_spatial_reference(
    aprx_path: str,
    map_name: str,
    wkid: int,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    m.spatialReference = arcpy.SpatialReference(int(wkid))  # type: ignore[attr-defined]
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "wkid": int(wkid)},
    )


@mcp.tool(
    name="arcgis_pro_layer_replace_data_source",
    description="",
)
def arcgis_pro_layer_replace_data_source(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    workspace_path: str,
    dataset_name: str,
    dataset_type: str,
    validate: bool = True,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    dt = dataset_type.strip()
    if not dt:
        raise RuntimeError("dataset_type 不能为空")
    dn = dataset_name.strip()
    if not dn:
        raise RuntimeError("dataset_name 不能为空")
    _replace_layer_data_source(lyr, ws, dn, dt, bool(validate))
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "workspace_path": ws,
            "dataset_name": dn,
            "dataset_type": dt,
        },
    )


@mcp.tool(
    name="arcgis_pro_apply_symbology_from_layer",
    description="",
)
def arcgis_pro_apply_symbology_from_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    symbology_layer_path: str,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    sp = _symbology_template_path(symbology_layer_path)
    arcpy.management.ApplySymbologyFromLayer(lyr, sp)  # type: ignore[attr-defined]
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "symbology_layer_path": sp,
        },
    )


@mcp.tool(
    name="arcgis_pro_set_layer_scale_range",
    description="",
)
def arcgis_pro_set_layer_scale_range(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    minimum_scale: float | None = None,
    maximum_scale: float | None = None,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    updated: dict[str, float] = {}
    if minimum_scale is not None:
        lyr.minimumScale = float(minimum_scale)  # type: ignore[attr-defined]
        updated["minimum_scale"] = float(minimum_scale)
    if maximum_scale is not None:
        lyr.maximumScale = float(maximum_scale)  # type: ignore[attr-defined]
        updated["maximum_scale"] = float(maximum_scale)
    if not updated:
        raise RuntimeError("至少提供 minimum_scale 或 maximum_scale")
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "updated": updated,
        },
    )


@mcp.tool(
    name="arcgis_pro_toggle_layer_labels",
    description="",
)
def arcgis_pro_toggle_layer_labels(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    show_labels: bool,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    lyr.showLabels = bool(show_labels)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "show_labels": lyr.showLabels,
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_dissolve",
    description="",
)
def arcgis_pro_gp_dissolve(
    in_features: str,
    out_feature_class: str,
    dissolve_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_dissolve(arcpy, in_features, out_feature_class, dissolve_field)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_intersect",
    description="",
)
def arcgis_pro_gp_intersect(
    in_feature_paths: list[str],
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_intersect(arcpy, in_feature_paths, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_feature_paths": [normalize_path(p) for p in in_feature_paths],
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_union",
    description="",
)
def arcgis_pro_gp_union(
    in_feature_paths: list[str],
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_union(arcpy, in_feature_paths, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_feature_paths": [normalize_path(p) for p in in_feature_paths],
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_erase",
    description="",
)
def arcgis_pro_gp_erase(
    in_features: str,
    erase_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_erase(arcpy, in_features, erase_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "erase_features": normalize_path(erase_features),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_spatial_join",
    description="",
)
def arcgis_pro_gp_spatial_join(
    target_features: str,
    join_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_spatial_join(arcpy, target_features, join_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "target_features": normalize_path(target_features),
            "join_features": normalize_path(join_features),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_statistics",
    description="",
)
def arcgis_pro_gp_statistics(
    in_table: str,
    out_table: str,
    statistics_fields: str,
    case_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_statistics(arcpy, in_table, out_table, statistics_fields, case_field)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "out_table": normalize_path(out_table),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_frequency",
    description="",
)
def arcgis_pro_gp_frequency(
    in_table: str,
    out_table: str,
    frequency_fields: str,
    summary_fields: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_frequency(arcpy, in_table, out_table, frequency_fields, summary_fields)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "out_table": normalize_path(out_table),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_table_select",
    description="",
)
def arcgis_pro_gp_table_select(
    in_table: str,
    out_table: str,
    where_clause: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_table_select(arcpy, in_table, out_table, where_clause)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "out_table": normalize_path(out_table),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_merge",
    description="",
)
def arcgis_pro_gp_merge(
    in_feature_paths: list[str],
    output_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_merge(arcpy, in_feature_paths, output_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_feature_paths": [normalize_path(p) for p in in_feature_paths],
            "output_feature_class": normalize_path(output_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_project",
    description="",
)
def arcgis_pro_gp_project(
    in_dataset: str,
    out_dataset: str,
    out_wkid: int,
    transform_method: str = "",
) -> str:
    arcpy = _arcpy()
    gp_write.run_project(arcpy, in_dataset, out_dataset, out_wkid, transform_method)
    return _json_dumps(
        {
            "ok": True,
            "in_dataset": normalize_path(in_dataset),
            "out_dataset": normalize_path(out_dataset),
            "out_wkid": int(out_wkid),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_add_field",
    description="",
)
def arcgis_pro_gp_add_field(
    in_table: str,
    field_name: str,
    field_type: str,
    field_length: int | None = None,
) -> str:
    arcpy = _arcpy()
    gp_schema.run_add_field(arcpy, in_table, field_name, field_type, field_length)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "field_name": field_name.strip(),
            "field_type": field_type.strip().upper(),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_delete_field",
    description=(
        "删除精确字段集合并读回核验；需要破坏性门禁、方案锁、目标路径与字段列表精确回显。"
    ),
)
def arcgis_pro_gp_delete_field(
    in_table: str,
    drop_field: str,
    confirm_in_table: str,
    confirm_drop_fields: list[str],
) -> str:
    arcpy = _arcpy()
    result = gp_schema.run_delete_field(
        arcpy,
        in_table,
        drop_field,
        confirm_in_table=confirm_in_table,
        confirm_drop_fields=confirm_drop_fields,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_da_update_field_constant",
    description="",
)
def arcgis_pro_da_update_field_constant(
    dataset_path: str,
    field_name: str,
    value_string: str,
    where_clause: str = "",
    max_rows_updated: int = 1000,
) -> str:
    arcpy = _arcpy()
    n, truncated = da_write.update_field_constant(
        arcpy,
        dataset_path,
        field_name,
        value_string,
        where_clause,
        max_rows_updated,
    )
    return _json_dumps(
        {
            "ok": True,
            "dataset_path": normalize_path(dataset_path),
            "field_name": field_name.strip(),
            "rows_updated": n,
            "truncated": truncated,
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_export_features",
    description="",
)
def arcgis_pro_gp_export_features(in_features: str, out_path: str) -> str:
    arcpy = _arcpy()
    gp_write.run_export_features(arcpy, in_features, out_path)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "out_path": normalize_path(out_path),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_export_table",
    description="",
)
def arcgis_pro_gp_export_table(in_table: str, out_path: str) -> str:
    arcpy = _arcpy()
    gp_write.run_export_table(arcpy, in_table, out_path)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "out_path": normalize_path(out_path),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_near",
    description="",
)
def arcgis_pro_gp_near(in_features: str, near_features: str) -> str:
    arcpy = _arcpy()
    gp_write.run_near(arcpy, in_features, near_features)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "near_features": normalize_path(near_features),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_generate_near_table",
    description="",
)
def arcgis_pro_gp_generate_near_table(
    in_features: str,
    near_features: str,
    out_table: str,
) -> str:
    arcpy = _arcpy()
    gp_write.run_generate_near_table(arcpy, in_features, near_features, out_table)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "near_features": normalize_path(near_features),
            "out_table": normalize_path(out_table),
        },
    )


# ---------------------------------------------------------------------------
# Phase 1: Data Write Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_da_insert_features",
    description="",
)
def arcgis_pro_da_insert_features(
    dataset_path: str,
    fields: list[str],
    rows: list[list[Any]],
) -> str:
    arcpy = _arcpy()
    n = da_write.insert_features(arcpy, dataset_path, fields, rows)
    return _json_dumps(
        {"ok": True, "dataset_path": normalize_path(dataset_path), "rows_inserted": n},
    )


@mcp.tool(
    name="arcgis_pro_da_update_features",
    description="",
)
def arcgis_pro_da_update_features(
    dataset_path: str,
    updates: dict[str, Any],
    where_clause: str = "",
    max_rows_updated: int = 1000,
) -> str:
    arcpy = _arcpy()
    n, truncated = da_write.update_features(
        arcpy, dataset_path, updates, where_clause, max_rows_updated
    )
    return _json_dumps(
        {
            "ok": True,
            "dataset_path": normalize_path(dataset_path),
            "rows_updated": n,
            "truncated": truncated,
        },
    )


@mcp.tool(
    name="arcgis_pro_da_delete_selected",
    description="",
)
def arcgis_pro_da_delete_selected(
    dataset_path: str,
    where_clause: str,
    max_rows_deleted: int = 1000,
    expected_count: int | None = None,
) -> str:
    require_allow_destructive()
    if expected_count is None:
        raise RuntimeError(
            "该工具实际按 where_clause 删除，不读取 Pro 图层选择集；必须提供 expected_count。"
            "新工作流请使用 arcgis_pro_edit_preflight/apply。"
        )
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    actual_count = sum(1 for _ in arcpy.da.SearchCursor(p, ["OID@"], where_clause))
    if actual_count != int(expected_count):
        raise RuntimeError(f"expected_count={expected_count}，实际命中 {actual_count} 行；未删除")
    if actual_count > int(max_rows_deleted):
        raise RuntimeError("实际命中行数超过 max_rows_deleted；未删除")
    n, truncated = da_write.delete_selected(arcpy, dataset_path, where_clause, max_rows_deleted)
    return _json_dumps(
        {
            "ok": True,
            "dataset_path": normalize_path(dataset_path),
            "rows_deleted": n,
            "truncated": truncated,
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_calculate_field",
    description=(
        "以受限 Arcade 表达式重算现有字段；禁止 Python/VB/code block/Portal FeatureSet，"
        "并要求破坏性门禁、目标路径和记录数精确确认。"
    ),
)
def arcgis_pro_gp_calculate_field(
    in_table: str,
    field_name: str,
    expression: str,
    expected_count: int,
    confirm_in_table: str,
    expression_type: str = "ARCADE",
) -> str:
    require_allow_destructive()
    if confirm_in_table != in_table:
        raise RuntimeError("confirm_in_table 必须逐字符精确回显 in_table")
    arcpy = _arcpy()
    p = validate_input_path_optional(in_table, "in_table")
    fn = field_name.strip()
    if not fn:
        raise RuntimeError("field_name 不能为空")
    list_fields = getattr(arcpy, "ListFields", None)
    if not callable(list_fields):
        raise RuntimeError("当前 ArcPy 不支持 ListFields，无法核验目标字段")
    fields = [str(field.name) for field in list_fields(p)]
    matches = [name for name in fields if name.casefold() == fn.casefold()]
    if len(matches) != 1:
        raise RuntimeError(f"field_name 必须唯一匹配现有字段；可用字段：{fields[:50]}")
    fn = matches[0]
    expr = validate_safe_arcade_expression(expression)
    et = expression_type.strip().upper()
    if et != "ARCADE":
        raise RuntimeError("expression_type 仅允许 ARCADE；Python/VB/code block 不通过 MCP 暴露")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise RuntimeError("expected_count 必须为非负整数")
    actual_count = int(arcpy.management.GetCount(p).getOutput(0))
    if actual_count != expected_count:
        raise RuntimeError(
            f"expected_count={expected_count}，实际记录数为 {actual_count}；未计算字段"
        )
    result = arcpy.management.CalculateField(p, fn, expr, "ARCADE")
    after_count = int(arcpy.management.GetCount(p).getOutput(0))
    if after_count != actual_count:
        raise RuntimeError("CalculateField 返回后记录数改变；结果异常，请勿自动重试")
    return _json_dumps(
        {
            "ok": True,
            "in_table": p,
            "field_name": fn,
            "expression_type": "ARCADE",
            "rows_targeted": actual_count,
            "count_verified": True,
            "messages": str(getattr(result, "getMessages", lambda: "")() or "")[:4000],
        }
    )


@mcp.tool(
    name="arcgis_pro_gp_calculate_geometry",
    description=(
        "对精确确认的现有字段整表计算几何属性；要求破坏性门禁、目标/字段映射回显和记录数确认。"
    ),
)
def arcgis_pro_gp_calculate_geometry(
    in_features: str,
    geometry_property: list[list[str]],
    expected_count: int,
    confirm_in_features: str,
    confirm_geometry_property: list[list[str]],
    length_unit: str = "",
    area_unit: str = "",
) -> str:
    require_allow_destructive()
    if confirm_in_features != in_features:
        raise RuntimeError("confirm_in_features 必须逐字符精确回显 in_features")
    if confirm_geometry_property != geometry_property:
        raise RuntimeError("confirm_geometry_property 必须精确回显 geometry_property")
    arcpy = _arcpy()
    p = validate_input_path_optional(in_features, "in_features")
    if not geometry_property or len(geometry_property) > 64:
        raise RuntimeError("geometry_property 必须为 1–64 项")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise RuntimeError("expected_count 必须为非负整数")
    list_fields = getattr(arcpy, "ListFields", None)
    if not callable(list_fields):
        raise RuntimeError("当前 ArcPy 不支持 ListFields，无法核验目标字段")
    available = [str(field.name) for field in list_fields(p)]
    aliases = {
        "AREA": "AREA_GEODESIC",
        "LENGTH": "PERIMETER_LENGTH_GEODESIC",
        "PERIMETER": "PERIMETER_LENGTH_GEODESIC",
    }
    mapped: list[list[str]] = []
    for pair in geometry_property:
        if not isinstance(pair, list) or len(pair) != 2:
            raise RuntimeError("geometry_property 每项须为 [字段名, 几何属性]")
        requested_field = str(pair[0]).strip()
        matches = [name for name in available if name.casefold() == requested_field.casefold()]
        if len(matches) != 1:
            raise RuntimeError(
                f"几何属性目标字段必须唯一匹配现有字段：{requested_field!r}；"
                f"可用字段：{available[:50]}"
            )
        prop = aliases.get(str(pair[1]).strip().upper(), str(pair[1]).strip().upper())
        if not prop or len(prop) > 80 or not re.fullmatch(r"[A-Z][A-Z0-9_]*", prop):
            raise RuntimeError(f"几何属性枚举无效：{prop!r}")
        mapped.append([matches[0], prop])
    geometry_property = mapped
    lu = (length_unit or "").strip()
    au = (area_unit or "").strip()
    kwargs: dict[str, Any] = {}
    if lu:
        kwargs["length_unit"] = lu
    if au:
        kwargs["area_unit"] = au
    actual_count = int(arcpy.management.GetCount(p).getOutput(0))
    if actual_count != expected_count:
        raise RuntimeError(
            f"expected_count={expected_count}，实际记录数为 {actual_count}；未计算几何属性"
        )
    result = arcpy.management.CalculateGeometryAttributes(p, mapped, **kwargs)
    after_count = int(arcpy.management.GetCount(p).getOutput(0))
    if after_count != actual_count:
        raise RuntimeError("CalculateGeometryAttributes 返回后记录数改变；请勿自动重试")
    return _json_dumps(
        {
            "ok": True,
            "in_features": p,
            "geometry_property": mapped,
            "rows_targeted": actual_count,
            "count_verified": True,
            "messages": str(getattr(result, "getMessages", lambda: "")() or "")[:4000],
        }
    )


@mcp.tool(
    name="arcgis_pro_gp_append",
    description="",
)
def arcgis_pro_gp_append(
    inputs: list[str],
    target: str,
    schema_type: str = "TEST",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    if not inputs:
        raise RuntimeError("inputs 不能为空")
    ins = [validate_input_path_optional(p, f"input_{i}") for i, p in enumerate(inputs)]
    tgt = validate_input_path_optional(target, "target")
    st = schema_type.strip().upper()
    if st not in ("TEST", "NO_TEST"):
        raise RuntimeError("schema_type 须为 TEST 或 NO_TEST")
    arcpy.management.Append(ins, tgt, st)
    return _json_dumps({"ok": True, "target": normalize_path(target), "input_count": len(ins)})


@mcp.tool(
    name="arcgis_pro_gp_delete_features",
    description="",
)
def arcgis_pro_gp_delete_features(
    in_features: str,
    expected_count: int | None = None,
) -> str:
    require_allow_destructive()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_features, "in_features")
    actual_count = int(arcpy.management.GetCount(p).getOutput(0))
    if expected_count is None or actual_count != int(expected_count):
        raise RuntimeError(
            f"必须提供匹配的 expected_count；当前记录数为 {actual_count}，本次未删除"
        )
    arcpy.management.DeleteFeatures(p)
    remaining = int(arcpy.management.GetCount(p).getOutput(0))
    if remaining != 0:
        raise RuntimeError(f"DeleteFeatures 返回后仍有 {remaining} 条记录")
    return _json_dumps(
        {"ok": True, "in_features": normalize_path(in_features), "deleted_count": actual_count}
    )


@mcp.tool(
    name="arcgis_pro_gp_truncate_table",
    description="",
)
def arcgis_pro_gp_truncate_table(
    in_table: str,
    expected_count: int | None = None,
    confirm_all: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm_all:
        raise RuntimeError("清空表需要 confirm_all=true")
    arcpy = _arcpy()
    p = validate_input_path_optional(in_table, "in_table")
    actual_count = int(arcpy.management.GetCount(p).getOutput(0))
    if expected_count is None or actual_count != int(expected_count):
        raise RuntimeError(
            f"必须提供匹配的 expected_count；当前记录数为 {actual_count}，本次未清空"
        )
    arcpy.management.TruncateTable(p)
    remaining = int(arcpy.management.GetCount(p).getOutput(0))
    if remaining != 0:
        raise RuntimeError(f"TruncateTable 返回后仍有 {remaining} 条记录")
    return _json_dumps(
        {"ok": True, "in_table": normalize_path(in_table), "deleted_count": actual_count}
    )


# ---------------------------------------------------------------------------
# Phase 2: Create Data
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_create_feature_class",
    description="",
)
def arcgis_pro_gp_create_feature_class(
    out_path: str,
    out_name: str,
    geometry_type: str,
    spatial_reference_wkid: int | None = None,
) -> str:
    arcpy = _arcpy()
    result_path = gp_create.run_create_feature_class(
        arcpy, out_path, out_name, geometry_type, spatial_reference_wkid
    )
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_create_table",
    description="",
)
def arcgis_pro_gp_create_table(out_path: str, out_name: str) -> str:
    arcpy = _arcpy()
    result_path = gp_create.run_create_table(arcpy, out_path, out_name)
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_create_file_gdb",
    description="",
)
def arcgis_pro_gp_create_file_gdb(out_folder_path: str, out_name: str) -> str:
    arcpy = _arcpy()
    result_path = gp_create.run_create_file_gdb(arcpy, out_folder_path, out_name)
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_create_feature_dataset",
    description="",
)
def arcgis_pro_gp_create_feature_dataset(
    out_dataset_path: str,
    out_name: str,
    spatial_reference_wkid: int,
) -> str:
    arcpy = _arcpy()
    result_path = gp_create.run_create_feature_dataset(
        arcpy, out_dataset_path, out_name, spatial_reference_wkid
    )
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_copy_feature_class",
    description="",
)
def arcgis_pro_gp_copy_feature_class(in_features: str, out_feature_class: str) -> str:
    arcpy = _arcpy()
    gp_create.run_copy_feature_class(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {
            "ok": True,
            "in_features": normalize_path(in_features),
            "out_feature_class": normalize_path(out_feature_class),
        },
    )


@mcp.tool(
    name="arcgis_pro_gp_rename_dataset",
    description="",
)
def arcgis_pro_gp_rename_dataset(in_data: str, out_data: str) -> str:
    arcpy = _arcpy()
    gp_create.run_rename_dataset(arcpy, in_data, out_data)
    return _json_dumps(
        {"ok": True, "in_data": normalize_path(in_data), "out_data": out_data.strip()},
    )


@mcp.tool(
    name="arcgis_pro_gp_delete_dataset",
    description="",
)
def arcgis_pro_gp_delete_dataset(in_data: str, confirm: bool = False) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除数据集需要 confirm=true")
    arcpy = _arcpy()
    target = validate_input_path_optional(in_data, "in_data")
    gp_create.run_delete_dataset(arcpy, in_data)
    if arcpy.Exists(target):
        raise RuntimeError("Delete 返回后数据集仍存在")
    return _json_dumps({"ok": True, "deleted": normalize_path(in_data)})


@mcp.tool(
    name="arcgis_pro_gp_alter_field",
    description="",
)
def arcgis_pro_gp_alter_field(
    in_table: str,
    field_name: str,
    new_field_name: str = "",
    new_field_alias: str = "",
) -> str:
    arcpy = _arcpy()
    gp_create.run_alter_field(arcpy, in_table, field_name, new_field_name, new_field_alias)
    return _json_dumps(
        {
            "ok": True,
            "in_table": normalize_path(in_table),
            "field_name": field_name.strip(),
            "new_field_name": new_field_name.strip() or "(unchanged)",
            "new_field_alias": new_field_alias.strip() or "(unchanged)",
        },
    )


# ---------------------------------------------------------------------------
# Phase 3: Import / Export Conversion
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_import_csv_to_table",
    description="",
)
def arcgis_pro_gp_import_csv_to_table(
    in_csv: str,
    out_path: str,
    out_name: str,
) -> str:
    arcpy = _arcpy()
    result_path = gp_convert.run_import_csv_to_table(arcpy, in_csv, out_path, out_name)
    return _json_dumps({"ok": True, "created": result_path})


@mcp.tool(
    name="arcgis_pro_gp_xy_table_to_point",
    description="",
)
def arcgis_pro_gp_xy_table_to_point(
    in_table: str,
    out_feature_class: str,
    x_field: str,
    y_field: str,
    spatial_reference_wkid: int = 4326,
) -> str:
    arcpy = _arcpy()
    gp_convert.run_xy_table_to_point(
        arcpy, in_table, out_feature_class, x_field, y_field, spatial_reference_wkid
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_json_to_features",
    description="",
)
def arcgis_pro_gp_json_to_features(in_json_file: str, out_features: str) -> str:
    arcpy = _arcpy()
    gp_convert.run_json_to_features(arcpy, in_json_file, out_features)
    return _json_dumps({"ok": True, "out_features": normalize_path(out_features)})


@mcp.tool(
    name="arcgis_pro_gp_features_to_json",
    description="",
)
def arcgis_pro_gp_features_to_json(
    in_features: str,
    out_json_file: str,
    format_json: bool = True,
    include_z_values: bool = False,
    include_m_values: bool = False,
) -> str:
    arcpy = _arcpy()
    gp_convert.run_features_to_json(
        arcpy, in_features, out_json_file, format_json, include_z_values, include_m_values
    )
    return _json_dumps({"ok": True, "out_json_file": normalize_path(out_json_file)})


@mcp.tool(
    name="arcgis_pro_gp_kml_to_layer",
    description="",
)
def arcgis_pro_gp_kml_to_layer(in_kml_file: str, output_folder: str) -> str:
    arcpy = _arcpy()
    gp_convert.run_kml_to_layer(arcpy, in_kml_file, output_folder)
    return _json_dumps({"ok": True, "output_folder": normalize_path(output_folder)})


@mcp.tool(
    name="arcgis_pro_gp_excel_to_table",
    description="",
)
def arcgis_pro_gp_excel_to_table(
    input_excel: str,
    out_table: str,
    sheet: str = "",
) -> str:
    arcpy = _arcpy()
    gp_convert.run_excel_to_table(arcpy, input_excel, out_table, sheet)
    return _json_dumps({"ok": True, "out_table": normalize_path(out_table)})


@mcp.tool(
    name="arcgis_pro_gp_table_to_excel",
    description="",
)
def arcgis_pro_gp_table_to_excel(in_table: str, output_excel: str) -> str:
    arcpy = _arcpy()
    gp_convert.run_table_to_excel(arcpy, in_table, output_excel)
    return _json_dumps({"ok": True, "output_excel": normalize_path(output_excel)})


@mcp.tool(
    name="arcgis_pro_gp_feature_class_to_shapefile",
    description="",
)
def arcgis_pro_gp_feature_class_to_shapefile(
    input_features: list[str],
    output_folder: str,
) -> str:
    arcpy = _arcpy()
    gp_convert.run_feature_class_to_shapefile(arcpy, input_features, output_folder)
    return _json_dumps({"ok": True, "output_folder": normalize_path(output_folder)})


# ---------------------------------------------------------------------------
# Phase 4: Core GP Analysis Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_multiple_ring_buffer",
    description="",
)
def arcgis_pro_gp_multiple_ring_buffer(
    in_features: str,
    out_feature_class: str,
    distances: list[float],
    buffer_unit: str = "Meters",
    dissolve_option: str = "ALL",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_multiple_ring_buffer(
        arcpy, in_features, out_feature_class, distances, buffer_unit, dissolve_option
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_feature_to_point",
    description="",
)
def arcgis_pro_gp_feature_to_point(
    in_features: str,
    out_feature_class: str,
    point_location: str = "CENTROID",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_feature_to_point(arcpy, in_features, out_feature_class, point_location)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_feature_to_line",
    description="",
)
def arcgis_pro_gp_feature_to_line(in_features: str, out_feature_class: str) -> str:
    arcpy = _arcpy()
    gp_analysis.run_feature_to_line(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_points_to_line",
    description="",
)
def arcgis_pro_gp_points_to_line(
    in_features: str,
    out_feature_class: str,
    line_field: str = "",
    sort_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_points_to_line(
        arcpy, in_features, out_feature_class, line_field, sort_field
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_polygon_to_line",
    description="",
)
def arcgis_pro_gp_polygon_to_line(
    in_features: str,
    out_feature_class: str,
    neighbor_option: str = "IDENTIFY_NEIGHBORS",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_polygon_to_line(arcpy, in_features, out_feature_class, neighbor_option)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_minimum_bounding_geometry",
    description="",
)
def arcgis_pro_gp_minimum_bounding_geometry(
    in_features: str,
    out_feature_class: str,
    geometry_type: str = "ENVELOPE",
    group_option: str = "NONE",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_minimum_bounding_geometry(
        arcpy, in_features, out_feature_class, geometry_type, group_option
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_convex_hull",
    description="",
)
def arcgis_pro_gp_convex_hull(
    in_features: str,
    out_feature_class: str,
    group_option: str = "ALL",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_convex_hull(arcpy, in_features, out_feature_class, group_option)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_split_by_attributes",
    description="",
)
def arcgis_pro_gp_split_by_attributes(
    in_table: str,
    target_workspace: str,
    split_fields: list[str],
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_split_by_attributes(arcpy, in_table, target_workspace, split_fields)
    return _json_dumps(
        {"ok": True, "target_workspace": normalize_path(target_workspace)},
    )


@mcp.tool(
    name="arcgis_pro_gp_identity",
    description="",
)
def arcgis_pro_gp_identity(
    in_features: str,
    identity_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_identity(arcpy, in_features, identity_features, out_feature_class)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_symmetrical_difference",
    description="",
)
def arcgis_pro_gp_symmetrical_difference(
    in_features: str,
    update_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_symmetrical_difference(
        arcpy, in_features, update_features, out_feature_class
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_count_overlapping_features",
    description="",
)
def arcgis_pro_gp_count_overlapping_features(
    in_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_count_overlapping_features(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_repair_geometry",
    description="以固定 KEEP_NULL 模式就地修复几何；不会删除空几何记录。",
)
def arcgis_pro_gp_repair_geometry(in_features: str) -> str:
    arcpy = _arcpy()
    gp_analysis.run_repair_geometry(arcpy, in_features)
    return _json_dumps({"ok": True, "in_features": normalize_path(in_features)})


@mcp.tool(
    name="arcgis_pro_gp_check_geometry",
    description="",
)
def arcgis_pro_gp_check_geometry(in_features: str, out_table: str) -> str:
    arcpy = _arcpy()
    gp_analysis.run_check_geometry(arcpy, in_features, out_table)
    return _json_dumps({"ok": True, "out_table": normalize_path(out_table)})


@mcp.tool(
    name="arcgis_pro_gp_eliminate",
    description="",
)
def arcgis_pro_gp_eliminate(
    in_features: str,
    out_feature_class: str,
    condition: str = "AREA",
    part_area: float = 0.0,
    part_area_percent: float = 0.0,
    part_option: str = "CONTAINED_ONLY",
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_eliminate(
        arcpy,
        in_features,
        out_feature_class,
        condition,
        part_area,
        part_area_percent,
        part_option,
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_multipart_to_singlepart",
    description="",
)
def arcgis_pro_gp_multipart_to_singlepart(
    in_features: str,
    out_feature_class: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_multipart_to_singlepart(arcpy, in_features, out_feature_class)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_aggregate_polygons",
    description="",
)
def arcgis_pro_gp_aggregate_polygons(
    in_features: str,
    out_feature_class: str,
    aggregation_distance: str,
) -> str:
    arcpy = _arcpy()
    gp_analysis.run_aggregate_polygons(
        arcpy, in_features, out_feature_class, aggregation_distance
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


# ---------------------------------------------------------------------------
# Phase 5: Raster Analysis
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_slope",
    description="",
)
def arcgis_pro_gp_slope(
    in_raster: str,
    out_raster: str,
    output_measurement: str = "DEGREE",
    z_factor: float = 1.0,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_slope(arcpy, in_raster, out_raster, output_measurement, z_factor)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_aspect",
    description="",
)
def arcgis_pro_gp_aspect(in_raster: str, out_raster: str) -> str:
    arcpy = _arcpy()
    gp_raster.run_aspect(arcpy, in_raster, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_hillshade",
    description="",
)
def arcgis_pro_gp_hillshade(
    in_raster: str,
    out_raster: str,
    azimuth: float = 315.0,
    altitude: float = 45.0,
    z_factor: float = 1.0,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_hillshade(arcpy, in_raster, out_raster, azimuth, altitude, z_factor)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_reclassify",
    description="",
)
def arcgis_pro_gp_reclassify(
    in_raster: str,
    reclass_field: str,
    remap: str,
    out_raster: str,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_reclassify(arcpy, in_raster, reclass_field, remap, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_extract_by_mask",
    description="",
)
def arcgis_pro_gp_extract_by_mask(
    in_raster: str,
    in_mask_data: str,
    out_raster: str,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_extract_by_mask(arcpy, in_raster, in_mask_data, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_extract_by_attributes",
    description="",
)
def arcgis_pro_gp_extract_by_attributes(
    in_raster: str,
    where_clause: str,
    out_raster: str,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_extract_by_attributes(arcpy, in_raster, where_clause, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_zonal_statistics_as_table",
    description="",
)
def arcgis_pro_gp_zonal_statistics_as_table(
    in_zone_data: str,
    zone_field: str,
    in_value_raster: str,
    out_table: str,
    statistics_type: str = "ALL",
) -> str:
    arcpy = _arcpy()
    gp_raster.run_zonal_statistics_as_table(
        arcpy, in_zone_data, zone_field, in_value_raster, out_table, statistics_type
    )
    return _json_dumps({"ok": True, "out_table": normalize_path(out_table)})


@mcp.tool(
    name="arcgis_pro_gp_kernel_density",
    description="",
)
def arcgis_pro_gp_kernel_density(
    in_features: str,
    population_field: str,
    out_raster: str,
    cell_size: float | None = None,
    search_radius: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_kernel_density(
        arcpy, in_features, population_field, out_raster, cell_size, search_radius
    )
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_point_density",
    description="",
)
def arcgis_pro_gp_point_density(
    in_features: str,
    population_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_point_density(arcpy, in_features, population_field, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_idw",
    description="",
)
def arcgis_pro_gp_idw(
    in_point_features: str,
    z_field: str,
    out_raster: str,
    cell_size: float | None = None,
    power: float = 2.0,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_idw(arcpy, in_point_features, z_field, out_raster, cell_size, power)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_kriging",
    description="",
)
def arcgis_pro_gp_kriging(
    in_point_features: str,
    z_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_kriging(arcpy, in_point_features, z_field, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_topo_to_raster",
    description="",
)
def arcgis_pro_gp_topo_to_raster(
    in_topo_features: str,
    out_raster: str,
    cell_size: float | None = None,
    elevation_field: str = "VALUE",
) -> str:
    arcpy = _arcpy()
    gp_raster.run_topo_to_raster(
        arcpy,
        in_topo_features,
        out_raster,
        cell_size,
        elevation_field,
    )
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_raster_to_polygon",
    description="",
)
def arcgis_pro_gp_raster_to_polygon(
    in_raster: str,
    out_polygon_features: str,
    simplify: bool = True,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_raster_to_polygon(arcpy, in_raster, out_polygon_features, simplify)
    return _json_dumps(
        {"ok": True, "out_polygon_features": normalize_path(out_polygon_features)},
    )


@mcp.tool(
    name="arcgis_pro_gp_polygon_to_raster",
    description="",
)
def arcgis_pro_gp_polygon_to_raster(
    in_features: str,
    value_field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_polygon_to_raster(arcpy, in_features, value_field, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_feature_to_raster",
    description="",
)
def arcgis_pro_gp_feature_to_raster(
    in_features: str,
    field: str,
    out_raster: str,
    cell_size: float | None = None,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_feature_to_raster(arcpy, in_features, field, out_raster, cell_size)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_raster_calculator",
    description="",
)
def arcgis_pro_gp_raster_calculator(expression: str, out_raster: str) -> str:
    arcpy = _arcpy()
    gp_raster.run_raster_calculator(arcpy, expression, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_mosaic_to_new_raster",
    description="",
)
def arcgis_pro_gp_mosaic_to_new_raster(
    input_rasters: list[str],
    output_location: str,
    raster_dataset_name: str,
    number_of_bands: int = 1,
    pixel_type: str = "32_BIT_FLOAT",
) -> str:
    arcpy = _arcpy()
    gp_raster.run_mosaic_to_new_raster(
        arcpy, input_rasters, output_location, raster_dataset_name, number_of_bands, pixel_type
    )
    return _json_dumps(
        {"ok": True, "output": f"{normalize_path(output_location)}\\{raster_dataset_name}"},
    )


@mcp.tool(
    name="arcgis_pro_gp_clip_raster",
    description="",
)
def arcgis_pro_gp_clip_raster(
    in_raster: str,
    out_raster: str,
    rectangle: str = "",
    in_template_dataset: str = "",
    clipping_geometry: bool = False,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_clip_raster(
        arcpy, in_raster, out_raster, rectangle, in_template_dataset, clipping_geometry
    )
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_resample",
    description="",
)
def arcgis_pro_gp_resample(
    in_raster: str,
    out_raster: str,
    cell_size: str,
    resampling_type: str = "NEAREST",
) -> str:
    arcpy = _arcpy()
    gp_raster.run_resample(arcpy, in_raster, out_raster, cell_size, resampling_type)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_project_raster",
    description="",
)
def arcgis_pro_gp_project_raster(
    in_raster: str,
    out_raster: str,
    out_wkid: int,
    resampling_type: str = "NEAREST",
) -> str:
    arcpy = _arcpy()
    gp_raster.run_project_raster(arcpy, in_raster, out_raster, out_wkid, resampling_type)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


@mcp.tool(
    name="arcgis_pro_gp_nibble",
    description="",
)
def arcgis_pro_gp_nibble(
    in_raster: str,
    in_mask_raster: str,
    out_raster: str,
) -> str:
    arcpy = _arcpy()
    gp_raster.run_nibble(arcpy, in_raster, in_mask_raster, out_raster)
    return _json_dumps({"ok": True, "out_raster": normalize_path(out_raster)})


# ---------------------------------------------------------------------------
# Phase 6: Symbology Control & Layout Enhancement
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_set_unique_value_renderer",
    description="",
)
def arcgis_pro_set_unique_value_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    fields: list[str],
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_unique_value_renderer(arcpy, project, m, lyr, fields)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "UniqueValueRenderer"},
    )


@mcp.tool(
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
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_graduated_colors_renderer(
        arcpy, project, m, lyr, classification_field, num_classes, classification_method
    )
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "GraduatedColorsRenderer"},
    )


@mcp.tool(
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
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_graduated_symbols_renderer(
        arcpy, project, m, lyr, classification_field, num_classes
    )
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "GraduatedSymbolsRenderer"},
    )


@mcp.tool(
    name="arcgis_pro_set_simple_renderer",
    description="",
)
def arcgis_pro_set_simple_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_simple_renderer(arcpy, project, m, lyr)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "SimpleRenderer"},
    )


@mcp.tool(
    name="arcgis_pro_set_heatmap_renderer",
    description="",
)
def arcgis_pro_set_heatmap_renderer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_heatmap_renderer(arcpy, project, m, lyr)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name,
         "renderer": "HeatMapRenderer"},
    )


@mcp.tool(
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
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.update_label_expression(arcpy, lyr, expression, label_class_name, expression_engine)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name},
    )


@mcp.tool(
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
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    symbology.set_label_font(arcpy, lyr, font_name, font_size, font_color, bold, italic, label_class_name)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name},
    )


@mcp.tool(
    name="arcgis_pro_export_report_pdf",
    description="",
)
def arcgis_pro_export_report_pdf(
    aprx_path: str,
    report_name: str,
    output_pdf_path: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    symbology.export_report_pdf(arcpy, project, report_name, output_pdf_path)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "report_name": report_name,
         "output_pdf_path": normalize_path(output_pdf_path)},
    )


@mcp.tool(
    name="arcgis_pro_list_layout_map_frames",
    description="",
)
def arcgis_pro_list_layout_map_frames(
    aprx_path: str,
    layout_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    frames: list[dict[str, Any]] = []
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        entry: dict[str, Any] = {"name": elm.name}
        try:
            entry["map_name"] = elm.map.name
        except Exception:  # noqa: BLE001
            pass
        frames.append(entry)
    return _json_dumps(
        {"aprx_path": path, "layout_name": layout_name, "map_frames": frames},
    )


@mcp.tool(
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
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    en = element_name.strip()
    elm = None
    for e in layout.listElements():
        if getattr(e, "name", "") == en:
            elm = e
            break
    if elm is None:
        names = [getattr(e, "name", "") for e in layout.listElements() if getattr(e, "name", "")]
        _raise_not_found("layout element", en, names)
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
    return _json_dumps({"ok": True, "aprx_path": path, "element_name": en, "updated": updated})


@mcp.tool(
    name="arcgis_pro_set_layout_element_visible",
    description="",
)
def arcgis_pro_set_layout_element_visible(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    visible: bool,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    en = element_name.strip()
    elm = None
    for e in layout.listElements():
        if getattr(e, "name", "") == en:
            elm = e
            break
    if elm is None:
        names = [getattr(e, "name", "") for e in layout.listElements() if getattr(e, "name", "")]
        _raise_not_found("layout element", en, names)
    elm.visible = bool(visible)
    return _json_dumps({"ok": True, "aprx_path": path, "element_name": en, "visible": bool(visible)})


@mcp.tool(
    name="arcgis_pro_update_legend_items",
    description="",
)
def arcgis_pro_update_legend_items(
    aprx_path: str,
    layout_name: str,
    legend_name: str,
    layer_visibility: dict[str, bool],
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    legend = None
    for elm in layout.listElements("LEGEND_ELEMENT"):
        if elm.name == legend_name:
            legend = elm
            break
    if legend is None:
        names = [e.name for e in layout.listElements("LEGEND_ELEMENT")]
        _raise_not_found("legend element", legend_name, names)
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
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "legend_name": legend_name,
            "items_updated": updated_count,
            "missing": missing,
        },
    )


@mcp.tool(
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
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
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
    target_map = _get_map(project, map_name) if map_name.strip() else (maps[0] if maps else None)
    if create_map_frame and target_map is None:
        _delete_project_item(project, lyt)
        raise RuntimeError("工程中没有可绑定的地图；请先创建地图或设置 create_map_frame=false")
    if create_map_frame:
        if not hasattr(lyt, "createMapFrame"):
            _delete_project_item(project, lyt)
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
                _delete_project_item(project, lyt)
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"创建地图框失败，布局已回滚：{_safe_error(ex, 500)}") from ex
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layout_name": ln,
            "layout_uri": _object_uri(lyt),
            "page_width": w,
            "page_height": h,
            "page_units": units,
            "map_name": str(getattr(target_map, "name", "")) if target_map is not None else None,
            "mapframe_name": str(getattr(map_frame, "name", "")) if map_frame is not None else None,
            "verified": lyt in list(project.listLayouts()),
        }
    )


@mcp.tool(
    name="arcgis_pro_rename_layout",
    description="",
)
def arcgis_pro_rename_layout(
    aprx_path: str,
    layout_name: str,
    new_layout_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    new_name = new_layout_name.strip()
    if not new_name:
        raise RuntimeError("new_layout_name cannot be empty")
    layout.name = new_name
    return _json_dumps({"ok": True, "aprx_path": path, "layout_name": layout_name, "new_layout_name": new_name})


@mcp.tool(
    name="arcgis_pro_remove_layout",
    description="",
)
def arcgis_pro_remove_layout(aprx_path: str, layout_name: str, confirm: bool = False) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除布局需要 confirm=true")
    _, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    _delete_project_item(project, layout)
    return _json_dumps({"ok": True, "aprx_path": path, "removed": layout_name})


@mcp.tool(
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
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    out = symbology.export_map_to_image(arcpy, m, output_path, width, height, resolution_dpi)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "output_path": out},
    )


# ---------------------------------------------------------------------------
# Phase 7: Layer Advanced Operations
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_get_layer_extent",
    description="",
)
def arcgis_pro_get_layer_extent(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    desc = arcpy.Describe(lyr)
    ext = _extent_dict(desc.extent)
    return _json_dumps(
        {"aprx_path": path, "map_name": map_name, "layer_name": layer_name, "extent": ext},
    )


@mcp.tool(
    name="arcgis_pro_zoom_to_layer",
    description="",
)
def arcgis_pro_zoom_to_layer(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _raise_not_found("mapframe", mapframe_name, names)
    desc = arcpy.Describe(lyr)
    ext = desc.extent
    _set_mapframe_extent(mf, ext)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "mapframe_name": mapframe_name},
    )


@mcp.tool(
    name="arcgis_pro_zoom_to_selection",
    description="",
)
def arcgis_pro_zoom_to_selection(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _raise_not_found("map frame", mapframe_name, names)
    sel = None
    try:
        sel = lyr.getSelectionSet()
    except Exception:  # noqa: BLE001
        sel = None
    if not sel:
        raise RuntimeError(f"图层 {layer_name!r} 当前选择集为空")
    xmin = ymin = None
    xmax = ymax = None
    with arcpy.da.SearchCursor(lyr, ["SHAPE@"]) as cur:  # type: ignore[attr-defined]
        for (geom,) in cur:
            if geom is None:
                continue
            e = geom.extent
            xmin = e.XMin if xmin is None else min(xmin, e.XMin)
            ymin = e.YMin if ymin is None else min(ymin, e.YMin)
            xmax = e.XMax if xmax is None else max(xmax, e.XMax)
            ymax = e.YMax if ymax is None else max(ymax, e.YMax)
    if xmin is None:
        raise RuntimeError(f"图层 {layer_name!r} 选择集没有可用几何")
    _set_mapframe_extent(mf, arcpy.Extent(xmin, ymin, xmax, ymax))
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "mapframe_name": mapframe_name},
    )


@mcp.tool(
    name="arcgis_pro_layer_add_field_alias",
    description="",
)
def arcgis_pro_layer_add_field_alias(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    field_name: str,
    field_alias: str,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    ds = lyr.dataSource
    resolved_layer_name = str(getattr(lyr, "name", layer_name))
    fn = field_name.strip()
    fa = field_alias.strip()
    if not fn or not fa:
        raise RuntimeError("field_name 和 field_alias 不能为空")
    if not is_current_project_token(aprx_path):
        # ArcGISProject and Layer references opened in file mode can retain a
        # schema lock on the data source.  They are no longer needed once the
        # dataSource string has been resolved, so release the cached graph before
        # AlterField.  CURRENT deliberately keeps the live project bound.
        _PROJECT_CACHE.pop(_project_cache_key(path), None)
        del lyr, m, project
        gc.collect()
        clear_workspace_cache = getattr(arcpy.management, "ClearWorkspaceCache", None)
        if callable(clear_workspace_cache):
            clear_workspace_cache()
    arcpy.management.AlterField(ds, fn, new_field_alias=fa)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": resolved_layer_name,
         "field_name": fn, "field_alias": fa},
    )


@mcp.tool(
    name="arcgis_pro_update_layer_cim",
    description="",
)
def arcgis_pro_update_layer_cim(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    cim_path: str,
    value: str,
) -> str:
    require_allow_cim_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    cp = cim_path.strip()
    if not cp:
        raise RuntimeError("cim_path 不能为空")
    parts = cp.split(".")
    if len(cp) > 500 or any(
        not part.isidentifier() or part.startswith("_") for part in parts
    ):
        raise RuntimeError(
            "cim_path must contain public dot-separated attribute names and be at most 500 characters"
        )
    if len(value.encode("utf-8")) > 100_000:
        raise RuntimeError("CIM value exceeds 100000 bytes")
    import json as _json
    try:
        val = _json.loads(value)
    except Exception:
        val = value
    cim_def = lyr.getDefinition("V3")
    obj = cim_def
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], val)
    lyr.setDefinition(cim_def)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "cim_path": cp},
    )


@mcp.tool(
    name="arcgis_pro_list_layer_renderers",
    description="",
)
def arcgis_pro_list_layer_renderers(
    aprx_path: str,
    map_name: str,
    layer_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    info = symbology.symbology_info(lyr)
    info.update({"aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _json_dumps(info)


@mcp.tool(
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
    _, project, path = _open_project(aprx_path)
    layer = _find_layer(_get_map(project, map_name), layer_name)
    result = symbology.set_raster_stretch_colorizer(
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
    return _json_dumps(result)


@mcp.tool(
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
    _, project, path = _open_project(aprx_path)
    layer = _find_layer(_get_map(project, map_name), layer_name)
    result = symbology.set_raster_classify_colorizer(
        project,
        layer,
        classification_field,
        break_count=break_count,
        classification_method=classification_method,
        color_ramp_name=color_ramp_name,
        color_ramp_index=color_ramp_index,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _json_dumps(result)


@mcp.tool(
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
    _, project, path = _open_project(aprx_path)
    layer = _find_layer(_get_map(project, map_name), layer_name)
    result = symbology.set_raster_unique_value_colorizer(
        project,
        layer,
        field,
        color_ramp_name=color_ramp_name,
        color_ramp_index=color_ramp_index,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _json_dumps(result)


@mcp.tool(
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
    _, project, path = _open_project(aprx_path)
    layer = _find_layer(_get_map(project, map_name), layer_name)
    result = symbology.apply_gallery_symbol(layer, wildcard, index)
    result.update({"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": layer_name})
    return _json_dumps(result)


# ---------------------------------------------------------------------------
# Phase 8: Database Connection Operations
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_list_broken_sources",
    description="",
)
def arcgis_pro_list_broken_sources(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    broken: list[dict[str, Any]] = []
    try:
        for lyr in project.listBrokenDataSources():
            item: dict[str, Any] = {"name": getattr(lyr, "name", str(lyr))}
            try:
                item["long_name"] = lyr.longName
            except Exception:  # noqa: BLE001
                pass
            try:
                item["data_source"] = _redact_sensitive(str(lyr.dataSource))
            except Exception as ex:  # noqa: BLE001
                item["data_source_error"] = _safe_error(ex, 300)
            broken.append(item)
    except Exception as ex:  # noqa: BLE001
        return _json_dumps({"aprx_path": path, "error": _safe_error(ex, 500)})
    return _json_dumps({"aprx_path": path, "broken_count": len(broken), "broken_sources": broken})


@mcp.tool(
    name="arcgis_pro_repair_layer_source",
    description="",
)
def arcgis_pro_repair_layer_source(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    new_workspace_path: str,
    new_dataset_name: str = "",
    workspace_type: str = "FILEGDB_WORKSPACE",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    nwp = validate_input_path_optional(new_workspace_path, "new_workspace_path")
    ndn = new_dataset_name.strip()
    wt = workspace_type.strip()
    _replace_layer_data_source(lyr, nwp, ndn or getattr(lyr, "name", ""), wt, True)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name,
         "new_workspace_path": nwp},
    )


@mcp.tool(
    name="arcgis_pro_create_db_connection",
    description="",
)
def arcgis_pro_create_db_connection(
    out_folder_path: str,
    out_name: str,
    database_platform: str,
    instance: str,
    database: str = "",
    authentication: str = "DATABASE_AUTH",
    username: str = "",
    password: str = "",
    save_credentials: bool = False,
    confirm_save_credentials: bool = False,
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    from arcgis_pro_mcp.paths import require_gp_output_root_mandatory, validate_gp_output_path
    require_gp_output_root_mandatory()
    ofp = validate_gp_output_path(out_folder_path, "out_folder_path")
    os.makedirs(ofp, exist_ok=True)
    on = validate_output_name(out_name, "out_name")
    if not on.lower().endswith(".sde"):
        on += ".sde"
    platform = database_platform.strip().upper().replace("SAP_HANA", "SAP HANA")
    if platform not in _DB_PLATFORMS:
        raise RuntimeError(f"database_platform 不受支持：{platform!r}；可选：{sorted(_DB_PLATFORMS)}")
    target_instance = instance.strip()
    if not target_instance or len(target_instance) > 1024 or any(
        char in target_instance for char in "\r\n\x00|;"
    ):
        raise RuntimeError("instance 必须是非空、无控制字符/分隔符的数据库实例标识")
    allowlist = {
        item.strip()
        for item in os.environ.get(_DB_INSTANCE_ALLOWLIST_ENV, "").split(";")
        if item.strip()
    }
    if not allowlist:
        raise RuntimeError(f"创建数据库连接必须配置 {_DB_INSTANCE_ALLOWLIST_ENV}")
    target_key = f"{platform}|{target_instance}"
    if target_key not in allowlist:
        raise RuntimeError(f"数据库目标不在 {_DB_INSTANCE_ALLOWLIST_ENV} 精确白名单中")
    connection_file = os.path.join(ofp, on)
    if os.path.lexists(connection_file):
        raise RuntimeError(f"connection_file 已存在；拒绝隐式覆盖：{connection_file}")
    kwargs: dict[str, str] = {
        "database_platform": platform,
        "instance": target_instance,
    }
    if database:
        kwargs["database"] = database.strip()
    auth = authentication.strip().upper()
    if auth not in {"DATABASE_AUTH", "OPERATING_SYSTEM_AUTH"}:
        raise RuntimeError("authentication 仅允许 DATABASE_AUTH 或 OPERATING_SYSTEM_AUTH")
    kwargs["account_authentication"] = auth
    inline_user = username.strip()
    inline_password = password
    user = inline_user or os.environ.get(_DB_USERNAME_ENV, "").strip()
    pwd = inline_password or os.environ.get(_DB_PASSWORD_ENV, "")
    if auth == "DATABASE_AUTH":
        if not user:
            raise RuntimeError(f"DATABASE_AUTH 需要 username 或固定环境变量 {_DB_USERNAME_ENV}")
        if inline_password and not inline_db_password_allowed():
            raise RuntimeError(
                f"默认不允许通过 MCP 直接传入数据库密码。请改用固定环境变量 {_DB_PASSWORD_ENV}，"
                "或在受控环境下设置 ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD=1。"
            )
        if not pwd:
            raise RuntimeError(f"DATABASE_AUTH 需要固定环境变量 {_DB_PASSWORD_ENV}，或显式允许内联 password")
        kwargs["username"] = user
        kwargs["password"] = pwd
    elif inline_user or inline_password:
        raise RuntimeError("OPERATING_SYSTEM_AUTH 不接受 username/password")
    if save_credentials and not confirm_save_credentials:
        raise RuntimeError("保存数据库凭据时 confirm_save_credentials 必须为 true")
    kwargs["save_user_pass"] = "SAVE_USERNAME" if save_credentials else "DO_NOT_SAVE_USERNAME"
    arcpy.management.CreateDatabaseConnection(ofp, on, **kwargs)
    return _json_dumps(
        {
            "ok": True,
            "connection_file": connection_file,
            "username_source": "inline" if inline_user else ("fixed_env" if user else ""),
            "password_source": "inline" if inline_password else ("fixed_env" if pwd else ""),
            "credentials_saved": bool(save_credentials),
        },
    )


@mcp.tool(
    name="arcgis_pro_list_sde_datasets",
    description="",
)
def arcgis_pro_list_sde_datasets(
    sde_connection_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(sde_connection_path, "sde_connection_path")
    fcs = workspace_listing.list_feature_classes(arcpy, p, "", "", wild_card, max_items)
    fds = workspace_listing.list_feature_datasets(arcpy, p, wild_card, max_items)
    for fd in fds:
        remaining = max_items - len(fcs)
        if remaining <= 0:
            break
        nested = workspace_listing.list_feature_classes(arcpy, p, fd, "", wild_card, remaining)
        for name in nested:
            qualified = name if ("/" in name or "\\" in name) else f"{fd}/{name}"
            if qualified not in fcs:
                fcs.append(qualified)
    tables = workspace_listing.list_tables(arcpy, p, wild_card, max_items)
    return _json_dumps(
        {"sde_connection_path": p, "feature_classes": fcs[:max_items], "tables": tables},
    )


# ---------------------------------------------------------------------------
# Phase 9: Map Operations Enhancement
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_add_basemap",
    description="",
)
def arcgis_pro_add_basemap(
    aprx_path: str,
    map_name: str,
    basemap_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    bn = basemap_name.strip()
    if not bn:
        raise RuntimeError("basemap_name 不能为空")
    m.addBasemap(bn)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "basemap_name": bn},
    )


@mcp.tool(
    name="arcgis_pro_create_map",
    description="",
)
def arcgis_pro_create_map(
    aprx_path: str,
    map_name: str,
    map_type: str = "MAP",
    basemap_name: str = "",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    mn = map_name.strip()
    if not mn:
        raise RuntimeError("map_name 不能为空")
    kind = (map_type or "MAP").strip().upper()
    if kind not in {"MAP", "SCENE", "GLOBE"}:
        raise RuntimeError("map_type 须为 MAP、SCENE 或 GLOBE")
    new_map = project.createMap(mn, kind)
    basemap = (basemap_name or "").strip()
    if basemap:
        new_map.addBasemap(basemap)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(new_map, "name", mn),
            "map_type": getattr(new_map, "mapType", kind),
            "basemap_name": basemap or None,
        },
    )


@mcp.tool(
    name="arcgis_pro_remove_map",
    description="",
)
def arcgis_pro_remove_map(aprx_path: str, map_name: str, confirm: bool = False) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除地图需要 confirm=true")
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    _delete_project_item(project, m)
    return _json_dumps({"ok": True, "aprx_path": path, "removed": map_name})


@mcp.tool(
    name="arcgis_pro_duplicate_map",
    description="",
)
def arcgis_pro_duplicate_map(
    aprx_path: str,
    map_name: str,
    new_map_name: str = "",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    new_map = project.copyItem(m)
    nmn = new_map_name.strip()
    if nmn:
        new_map.name = nmn
    return _json_dumps(
        {"ok": True, "aprx_path": path, "source_map": map_name,
         "new_map": getattr(new_map, "name", "")},
    )


@mcp.tool(
    name="arcgis_pro_rename_map",
    description="",
)
def arcgis_pro_rename_map(
    aprx_path: str,
    map_name: str,
    new_map_name: str,
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    map_obj = _get_map(project, map_name)
    new_name = new_map_name.strip()
    if not new_name:
        raise RuntimeError("new_map_name cannot be empty")
    map_obj.name = new_name
    return _json_dumps({"ok": True, "aprx_path": path, "map_name": map_name, "new_map_name": new_name})


@mcp.tool(
    name="arcgis_pro_map_pan_to_extent",
    description="",
)
def arcgis_pro_map_pan_to_extent(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    spatial_reference_wkid: int | None = None,
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    mf = None
    for elm in layout.listElements("MAPFRAME_ELEMENT"):
        if elm.name == mapframe_name:
            mf = elm
            break
    if mf is None:
        names = [e.name for e in layout.listElements("MAPFRAME_ELEMENT")]
        _raise_not_found("mapframe", mapframe_name, names)
    ext = arcpy.Extent(float(xmin), float(ymin), float(xmax), float(ymax))
    if spatial_reference_wkid is not None:
        ext.spatialReference = arcpy.SpatialReference(int(spatial_reference_wkid))
    else:
        try:
            ext.spatialReference = mf.map.spatialReference
        except Exception:  # noqa: BLE001
            pass
    mf.panToExtent(ext)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "mapframe_name": mapframe_name},
    )


@mcp.tool(
    name="arcgis_pro_set_time_slider",
    description="",
)
def arcgis_pro_set_time_slider(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    start_time: str = "",
    end_time: str = "",
    time_field: str = "",
) -> str:
    """Compatibility alias for layer time enablement.

    ArcPy cannot control the active MapView time slider.  Use
    ``arcgis_pro_set_mapframe_time`` for a layout map frame or the SDK bridge for
    the live MapView.
    """
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    if start_time or end_time:
        raise RuntimeError(
            "该兼容工具不再把字符串写入只读 LayerTime 范围。"
            "请使用 arcgis_pro_set_mapframe_time；活动 MapView 时间滑块需 SDK Add-in。"
        )
    if not hasattr(lyr, "enableTime"):
        raise RuntimeError("当前图层不支持 enableTime")
    kwargs: dict[str, Any] = {"autoCalculateTimeRange": True}
    if time_field:
        kwargs["startTimeField"] = time_field
    lyr.enableTime(**kwargs)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layer_name": layer_name,
            "time_enabled": bool(getattr(lyr, "isTimeEnabled", True)),
            "start_time_field": time_field or None,
            "deprecated_alias": True,
            "active_mapview_slider_changed": False,
        },
    )


# ---------------------------------------------------------------------------
# Phase 10: Generic GP Engine
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_run_tool",
    description="",
)
def arcgis_pro_gp_run_tool(
    tool_name: str,
    parameters: dict[str, Any] | None = None,
) -> str:
    arcpy = _arcpy()
    msgs = gp_generic.run_tool(arcpy, tool_name, parameters)
    return _json_dumps({"ok": True, "tool_name": tool_name, "messages": msgs})


@mcp.tool(
    name="arcgis_pro_gp_get_messages",
    description="",
)
def arcgis_pro_gp_get_messages() -> str:
    arcpy = _arcpy()
    msgs = gp_generic.get_messages(arcpy)
    return _json_dumps({"messages": msgs})


@mcp.tool(
    name="arcgis_pro_gp_list_toolboxes",
    description="",
)
def arcgis_pro_gp_list_toolboxes() -> str:
    arcpy = _arcpy()
    toolboxes = gp_generic.list_toolboxes(arcpy)
    return _json_dumps({"toolboxes": toolboxes, "count": len(toolboxes)})


@mcp.tool(
    name="arcgis_pro_gp_list_tools_in_toolbox",
    description="",
)
def arcgis_pro_gp_list_tools_in_toolbox(toolbox: str) -> str:
    arcpy = _arcpy()
    tools = gp_generic.list_tools_in_toolbox(arcpy, toolbox)
    return _json_dumps({"toolbox": toolbox, "tools": tools, "count": len(tools)})


# ---------------------------------------------------------------------------
# Phase 11: Network Analysis
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_na_create_route_layer",
    description="",
)
def arcgis_pro_na_create_route_layer(
    network_data_source: str,
    layer_name: str = "Route",
    travel_mode: str = "",
) -> str:
    arcpy = _arcpy()
    result = gp_network.run_make_route_analysis_layer(
        arcpy, network_data_source, layer_name, travel_mode
    )
    return _json_dumps({"ok": True, "layer": result})


@mcp.tool(
    name="arcgis_pro_na_add_locations",
    description="",
)
def arcgis_pro_na_add_locations(
    in_network_analysis_layer: str,
    sub_layer: str,
    in_table: str,
    field_mappings: str = "",
) -> str:
    arcpy = _arcpy()
    gp_network.run_add_locations(
        arcpy, in_network_analysis_layer, sub_layer, in_table, field_mappings
    )
    return _json_dumps({"ok": True, "sub_layer": sub_layer})


@mcp.tool(
    name="arcgis_pro_na_solve",
    description="",
)
def arcgis_pro_na_solve(
    in_network_analysis_layer: str,
    ignore_invalids: bool = True,
) -> str:
    arcpy = _arcpy()
    result = gp_network.run_solve(arcpy, in_network_analysis_layer, ignore_invalids)
    return _json_dumps({"ok": True, "result": result})


@mcp.tool(
    name="arcgis_pro_na_service_area",
    description="",
)
def arcgis_pro_na_service_area(
    network_data_source: str,
    layer_name: str = "ServiceArea",
    travel_mode: str = "",
    cutoffs: list[float] | None = None,
) -> str:
    arcpy = _arcpy()
    result = gp_network.run_make_service_area_analysis_layer(
        arcpy, network_data_source, layer_name, travel_mode, cutoffs
    )
    return _json_dumps({"ok": True, "layer": result})


@mcp.tool(
    name="arcgis_pro_na_od_matrix",
    description="",
)
def arcgis_pro_na_od_matrix(
    network_data_source: str,
    layer_name: str = "ODMatrix",
    travel_mode: str = "",
    cutoff: float | None = None,
    number_of_destinations_to_find: int | None = None,
) -> str:
    arcpy = _arcpy()
    result = gp_network.run_make_od_cost_matrix_layer(
        arcpy, network_data_source, layer_name, travel_mode, cutoff, number_of_destinations_to_find
    )
    return _json_dumps({"ok": True, "layer": result})


# ---------------------------------------------------------------------------
# Phase 12: Metadata & Data Quality
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_get_metadata",
    description="",
)
def arcgis_pro_get_metadata(dataset_path: str) -> str:
    arcpy = _arcpy()
    md = metadata.get_metadata(arcpy, dataset_path)
    return _json_dumps({"dataset_path": normalize_path(dataset_path), "metadata": md})


@mcp.tool(
    name="arcgis_pro_set_metadata",
    description="",
)
def arcgis_pro_set_metadata(
    dataset_path: str,
    title: str = "",
    tags: str = "",
    summary: str = "",
    description: str = "",
    credits: str = "",
    access_constraints: str = "",
) -> str:
    arcpy = _arcpy()
    metadata.set_metadata(arcpy, dataset_path, title, tags, summary, description, credits, access_constraints)
    return _json_dumps({"ok": True, "dataset_path": normalize_path(dataset_path)})


@mcp.tool(
    name="arcgis_pro_gp_validate_topology",
    description="",
)
def arcgis_pro_gp_validate_topology(in_topology: str) -> str:
    arcpy = _arcpy()
    metadata.validate_topology(arcpy, in_topology)
    return _json_dumps({"ok": True, "in_topology": normalize_path(in_topology)})


@mcp.tool(
    name="arcgis_pro_workspace_list_datasets",
    description="",
)
def arcgis_pro_workspace_list_datasets(
    workspace_path: str,
    dataset_type: str = "",
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = _list_workspace_datasets(arcpy, ws, dataset_type, wild_card, max_items)
    return _json_dumps(
        {
            "workspace_path": ws,
            "dataset_type": dataset_type.strip(),
            "datasets": names,
        },
    )


@mcp.tool(
    name="arcgis_pro_workspace_list_feature_datasets",
    description="",
)
def arcgis_pro_workspace_list_feature_datasets(
    workspace_path: str,
    wild_card: str = "*",
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    names = _list_workspace_datasets(arcpy, ws, "Feature", wild_card, max_items)
    return _json_dumps({"workspace_path": ws, "feature_datasets": names})


@mcp.tool(
    name="arcgis_pro_workspace_list_domains",
    description="",
)
def arcgis_pro_workspace_list_domains(
    workspace_path: str,
    max_items: int = 200,
) -> str:
    arcpy = _arcpy()
    ws = validate_input_path_optional(workspace_path, "workspace_path")
    domains = _list_workspace_domains(arcpy, ws, max_items)
    return _json_dumps({"workspace_path": ws, "domains": domains})


@mcp.tool(
    name="arcgis_pro_gp_table_to_table",
    description="",
)
def arcgis_pro_gp_table_to_table(
    in_rows: str,
    out_path: str,
    out_name: str,
) -> str:
    arcpy = _arcpy()
    result_path = gp_convert.run_table_to_table(arcpy, in_rows, out_path, out_name)
    return _json_dumps({"ok": True, "created": result_path})


# ---------------------------------------------------------------------------
# Phase 5: Spatial statistics, regression, and sampling (research workflows)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="arcgis_pro_gp_hot_spots",
    description="",
)
def arcgis_pro_gp_hot_spots(
    in_features: str,
    input_field: str,
    out_feature_class: str,
    conceptualization: str = "FIXED_DISTANCE_BAND",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "NONE",
    distance_band: float | None = None,
    apply_fdr: bool = False,
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_hot_spots(
        arcpy,
        in_features,
        input_field,
        out_feature_class,
        conceptualization,
        distance_method,
        standardization,
        distance_band,
        apply_fdr,
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class), "messages": msgs},
    )


@mcp.tool(
    name="arcgis_pro_gp_optimized_hot_spots",
    description="",
)
def arcgis_pro_gp_optimized_hot_spots(
    in_features: str,
    out_features: str,
    analysis_field: str = "",
    aggregation_method: str = "",
    cell_size: float | None = None,
    distance_band: float | None = None,
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_optimized_hot_spots(
        arcpy, in_features, out_features, analysis_field, aggregation_method, cell_size, distance_band
    )
    return _json_dumps(
        {"ok": True, "out_features": normalize_path(out_features), "messages": msgs},
    )


@mcp.tool(
    name="arcgis_pro_gp_cluster_outlier",
    description="",
)
def arcgis_pro_gp_cluster_outlier(
    in_features: str,
    input_field: str,
    out_feature_class: str,
    conceptualization: str = "FIXED_DISTANCE_BAND",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "NONE",
    distance_band: float | None = None,
    apply_fdr: bool = False,
    number_of_permutations: int | None = None,
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_cluster_outlier(
        arcpy,
        in_features,
        input_field,
        out_feature_class,
        conceptualization,
        distance_method,
        standardization,
        distance_band,
        apply_fdr,
        number_of_permutations,
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class), "messages": msgs},
    )


@mcp.tool(
    name="arcgis_pro_gp_spatial_autocorrelation",
    description="",
)
def arcgis_pro_gp_spatial_autocorrelation(
    in_features: str,
    input_field: str,
    conceptualization: str = "INVERSE_DISTANCE",
    distance_method: str = "EUCLIDEAN_DISTANCE",
    standardization: str = "ROW",
    distance_band: float | None = None,
    generate_report: bool = False,
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_spatial_autocorrelation(
        arcpy,
        in_features,
        input_field,
        conceptualization,
        distance_method,
        standardization,
        distance_band,
        generate_report,
    )
    return _json_dumps({"ok": True, "messages": msgs})


@mcp.tool(
    name="arcgis_pro_gp_average_nearest_neighbor",
    description="",
)
def arcgis_pro_gp_average_nearest_neighbor(
    in_features: str,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    generate_report: bool = False,
    area: float | None = None,
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_average_nearest_neighbor(
        arcpy, in_features, distance_method, generate_report, area
    )
    return _json_dumps({"ok": True, "messages": msgs})


@mcp.tool(
    name="arcgis_pro_gp_multi_distance_spatial_clustering",
    description="",
)
def arcgis_pro_gp_multi_distance_spatial_clustering(
    in_features: str,
    out_table: str,
    number_of_distance_bands: int,
    compute_confidence_envelope: str = "0_PERMUTATIONS_-_NO_CONFIDENCE_ENVELOPE",
    weight_field: str = "",
    beginning_distance: float | None = None,
    distance_increment: float | None = None,
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_multi_distance_spatial_clustering(
        arcpy,
        in_features,
        out_table,
        number_of_distance_bands,
        compute_confidence_envelope,
        weight_field,
        beginning_distance,
        distance_increment,
    )
    return _json_dumps(
        {"ok": True, "out_table": normalize_path(out_table), "messages": msgs},
    )


@mcp.tool(
    name="arcgis_pro_gp_ordinary_least_squares",
    description="",
)
def arcgis_pro_gp_ordinary_least_squares(
    in_features: str,
    unique_id_field: str,
    out_feature_class: str,
    dependent_variable: str,
    explanatory_variables: list[str],
    coefficient_output_table: str = "",
    diagnostic_output_table: str = "",
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_ordinary_least_squares(
        arcpy,
        in_features,
        unique_id_field,
        out_feature_class,
        dependent_variable,
        explanatory_variables,
        coefficient_output_table,
        diagnostic_output_table,
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class), "messages": msgs},
    )


@mcp.tool(
    name="arcgis_pro_gp_gwr",
    description="",
)
def arcgis_pro_gp_gwr(
    in_features: str,
    dependent_variable: str,
    explanatory_variables: list[str],
    out_features: str,
    model_type: str = "CONTINUOUS",
    neighborhood_type: str = "NUMBER_OF_NEIGHBORS",
    neighborhood_selection_method: str = "GOLDEN_SEARCH",
    number_of_neighbors: int | None = None,
    distance_band: float | None = None,
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_gwr(
        arcpy,
        in_features,
        dependent_variable,
        explanatory_variables,
        out_features,
        model_type,
        neighborhood_type,
        neighborhood_selection_method,
        number_of_neighbors,
        distance_band,
    )
    return _json_dumps(
        {"ok": True, "out_features": normalize_path(out_features), "messages": msgs},
    )


@mcp.tool(
    name="arcgis_pro_gp_forest",
    description="",
)
def arcgis_pro_gp_forest(
    in_features: str,
    variable_predict: str,
    explanatory_variables: list[str],
    prediction_type: str = "TRAIN",
    explanatory_variables_categorical: list[str] | None = None,
    treat_variable_as_categorical: bool = False,
    number_of_trees: int = 100,
    output_trained_features: str = "",
) -> str:
    arcpy = _arcpy()
    msgs = gp_stats.run_forest(
        arcpy,
        in_features,
        variable_predict,
        explanatory_variables,
        prediction_type,
        explanatory_variables_categorical,
        treat_variable_as_categorical,
        number_of_trees,
        output_trained_features,
    )
    return _json_dumps({"ok": True, "messages": msgs})


@mcp.tool(
    name="arcgis_pro_gp_central_feature",
    description="",
)
def arcgis_pro_gp_central_feature(
    in_features: str,
    out_feature_class: str,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    weight_field: str = "",
    case_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_stats.run_central_feature(
        arcpy, in_features, out_feature_class, distance_method, weight_field, case_field
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_mean_center",
    description="",
)
def arcgis_pro_gp_mean_center(
    in_features: str,
    out_feature_class: str,
    weight_field: str = "",
    case_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_stats.run_mean_center(arcpy, in_features, out_feature_class, weight_field, case_field)
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_directional_distribution",
    description="",
)
def arcgis_pro_gp_directional_distribution(
    in_features: str,
    out_feature_class: str,
    ellipse_size: str = "1_STANDARD_DEVIATION",
    weight_field: str = "",
    case_field: str = "",
) -> str:
    arcpy = _arcpy()
    gp_stats.run_directional_distribution(
        arcpy, in_features, out_feature_class, ellipse_size, weight_field, case_field
    )
    return _json_dumps(
        {"ok": True, "out_feature_class": normalize_path(out_feature_class)},
    )


@mcp.tool(
    name="arcgis_pro_gp_create_random_points",
    description="",
)
def arcgis_pro_gp_create_random_points(
    out_path: str,
    out_name: str,
    number_of_points: int,
    constraining_feature_class: str = "",
    minimum_allowed_distance: str = "",
) -> str:
    arcpy = _arcpy()
    created = gp_stats.run_create_random_points(
        arcpy, out_path, out_name, number_of_points, constraining_feature_class, minimum_allowed_distance
    )
    return _json_dumps({"ok": True, "created": created})


@mcp.tool(
    name="arcgis_pro_gp_generate_tessellation",
    description="",
)
def arcgis_pro_gp_generate_tessellation(
    output_feature_class: str,
    extent: str,
    shape_type: str = "HEXAGON",
    size: str = "",
    spatial_reference_wkid: int | None = None,
) -> str:
    arcpy = _arcpy()
    out = gp_stats.run_generate_tessellation(
        arcpy, output_feature_class, extent, shape_type, size, spatial_reference_wkid
    )
    return _json_dumps({"ok": True, "output_feature_class": normalize_path(out)})


@mcp.tool(
    name="arcgis_pro_export_active_view_image",
    description="导出 CURRENT 用户眼前的活动地图或布局视图，作为窗口操作后的视觉验收快照。",
)
def arcgis_pro_export_active_view_image(
    aprx_path: str,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    resolution_dpi: int = 96,
    jpeg_quality: int = 90,
    transparent_background: bool = False,
) -> str:
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    result = cartography.export_active_view(
        project,
        output_path,
        width,
        height,
        resolution_dpi,
        jpeg_quality,
        transparent_background,
    )
    result.update({"ok": True, "aprx_path": path, "rendered": True})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_open_layer_table_view",
    description="在 CURRENT ArcGIS Pro 中打开并激活图层属性表，可仅显示当前选择记录。",
)
def arcgis_pro_open_layer_table_view(
    aprx_path: str,
    layer_name: str,
    show_selected: bool = False,
) -> str:
    _require_current_window(aprx_path)
    _, project, path = _open_project(aprx_path)
    _view, active_map = _active_map_view(project)
    result = cartography.open_table_view(
        _find_layer(active_map, layer_name),
        show_selected=show_selected,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": getattr(active_map, "name", "")})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_create_bookmark",
    description="从 CURRENT 活动 MapView 或指定布局地图框创建书签，并拒绝重名。",
)
def arcgis_pro_create_bookmark(
    aprx_path: str,
    name: str,
    description: str = "",
    source_type: str = "ACTIVE_VIEW",
    layout_name: str = "",
    mapframe_name: str = "",
) -> str:
    _, project, path = _open_project(aprx_path)
    source_kind = source_type.strip().upper()
    if source_kind == "ACTIVE_VIEW":
        _require_current_window(aprx_path)
        source = getattr(project, "activeView", None)
        if source is None or getattr(source, "map", None) is None:
            raise RuntimeError("当前活动视图不是 MapView")
    elif source_kind == "MAP_FRAME":
        source = _get_mapframe(_get_layout(project, layout_name), mapframe_name)
    else:
        raise RuntimeError("source_type 须为 ACTIVE_VIEW 或 MAP_FRAME")
    result = cartography.create_bookmark(source, name, description)
    result.update({"ok": True, "aprx_path": path, "source_type": source_kind})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_update_bookmark", description="更新地图书签的名称、描述或缩略图。")
def arcgis_pro_update_bookmark(
    aprx_path: str,
    map_name: str,
    bookmark_name: str,
    new_name: str | None = None,
    description: str | None = None,
    update_thumbnail: bool = False,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.update_bookmark(
        _get_map(project, map_name),
        bookmark_name,
        new_name=new_name,
        description=description,
        update_thumbnail=update_thumbnail,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": map_name})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_delete_bookmark", description="删除指定地图书签；需要独立破坏性门禁和 confirm=true。")
def arcgis_pro_delete_bookmark(
    aprx_path: str,
    map_name: str,
    bookmark_name: str,
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除书签需要 confirm=true")
    _, project, path = _open_project(aprx_path)
    result = cartography.delete_bookmark(_get_map(project, map_name), bookmark_name)
    result.update({"ok": True, "aprx_path": path, "map_name": map_name})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_import_bookmarks", description="从受控输入根内的 .bkmx 文件向地图导入书签。")
def arcgis_pro_import_bookmarks(aprx_path: str, map_name: str, input_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.import_bookmarks(_get_map(project, map_name), input_path)
    result.update({"ok": True, "aprx_path": path, "map_name": map_name})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_export_bookmarks", description="将地图书签导出为受控目录中的 .bkmx 文件。")
def arcgis_pro_export_bookmarks(aprx_path: str, map_name: str, output_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.export_bookmarks(_get_map(project, map_name), output_path)
    result.update({"ok": True, "aprx_path": path, "map_name": map_name})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_layout_element_info",
    description="读取布局元素的 URI、重复序号、位置、尺寸、旋转、文本、父组及地图绑定。",
)
def arcgis_pro_layout_element_info(
    aprx_path: str,
    layout_name: str,
    element_type: str = "",
) -> str:
    _, project, path = _open_project(aprx_path)
    elements = cartography.layout_element_info(_get_layout(project, layout_name), element_type)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layout_name": layout_name, "count": len(elements), "elements": elements}
    )


@mcp.tool(
    name="arcgis_pro_create_layout_from_template",
    description="从受控输入根内的 .pagx 模板导入一个布局，并唯一核验新布局。",
)
def arcgis_pro_create_layout_from_template(
    aprx_path: str,
    template_path: str,
    layout_name: str = "",
    reuse_existing_maps: bool = True,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.create_layout_from_template(
        project,
        template_path,
        layout_name,
        reuse_existing_maps,
    )
    result.pop("layout", None)
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_bind_map_frame", description="将布局地图框显式绑定到地图并读回 URI 进行核验。")
def arcgis_pro_bind_map_frame(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
    map_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.bind_map_frame(
        _get_layout(project, layout_name),
        mapframe_name,
        _get_map(project, map_name),
    )
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _json_dumps(result)


@mcp.tool(
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
    arcpy, project, path = _open_project(aprx_path)
    layout = _get_layout(project, layout_name)
    map_obj = _get_map(project, map_name) if map_name.strip() else None
    map_frame = _get_mapframe(layout, mapframe_name) if mapframe_name.strip() else None
    table = _get_table(map_frame.map if map_frame is not None else map_obj, table_name) if table_name.strip() else None
    style_item = _get_style_item(project, style, style_category, style_name)
    result = cartography.upsert_layout_element(
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
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_delete_layout_element", description="按唯一名称/URI 删除布局元素；需要破坏性门禁和 confirm=true。")
def arcgis_pro_delete_layout_element(
    aprx_path: str,
    layout_name: str,
    element_name: str,
    element_type: str = "",
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除布局元素需要 confirm=true")
    _, project, path = _open_project(aprx_path)
    result = cartography.delete_layout_element(
        _get_layout(project, layout_name), element_name, element_type
    )
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_map_series_info", description="读取布局 MapSeries 类型、页数、当前页、索引图层和选择页。")
def arcgis_pro_map_series_info(aprx_path: str, layout_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.map_series_info(_get_layout(project, layout_name))
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_set_map_series_page", description="按页码或页名切换布局 MapSeries 当前页。")
def arcgis_pro_set_map_series_page(
    aprx_path: str,
    layout_name: str,
    page_number: int | None = None,
    page_name: str = "",
) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.set_map_series_page(
        _get_layout(project, layout_name), page_number=page_number, page_name=page_name
    )
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_refresh_map_series", description="刷新 MapSeries 索引状态并返回刷新后的页信息。")
def arcgis_pro_refresh_map_series(aprx_path: str, layout_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.refresh_map_series(_get_layout(project, layout_name))
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_export_map_series_pdf", description="将 MapSeries 的全部、当前、范围或选择页导出为受控 PDF。")
def arcgis_pro_export_map_series_pdf(
    aprx_path: str,
    layout_name: str,
    output_path: str,
    page_range_type: str = "ALL",
    page_range_string: str = "",
    multiple_files: str = "PDF_SINGLE_FILE",
    resolution_dpi: int = 300,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.export_map_series_pdf(
        _get_layout(project, layout_name),
        output_path,
        page_range_type=page_range_type,
        page_range_string=page_range_string,
        multiple_files=multiple_files,
        resolution_dpi=resolution_dpi,
    )
    result.update({"ok": True, "aprx_path": path, "layout_name": layout_name})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_list_definition_queries", description="列出图层或独立表的全部命名定义查询及活动状态。")
def arcgis_pro_list_definition_queries(
    aprx_path: str,
    map_name: str,
    member_name: str,
    member_type: str = "LAYER",
) -> str:
    _, project, path = _open_project(aprx_path)
    map_obj = _get_map(project, map_name)
    kind = member_type.strip().upper()
    member = _find_layer(map_obj, member_name) if kind == "LAYER" else _get_table(map_obj, member_name)
    if kind not in {"LAYER", "TABLE"}:
        raise RuntimeError("member_type 须为 LAYER 或 TABLE")
    values = cartography.list_definition_queries(member)
    return _json_dumps({"ok": True, "aprx_path": path, "queries": values, "count": len(values)})


@mcp.tool(name="arcgis_pro_upsert_definition_query", description="创建或更新图层/表的命名定义查询，并可将其设为唯一活动查询。")
def arcgis_pro_upsert_definition_query(
    aprx_path: str,
    map_name: str,
    member_name: str,
    name: str,
    sql: str,
    member_type: str = "LAYER",
    is_active: bool = False,
) -> str:
    _, project, path = _open_project(aprx_path)
    map_obj = _get_map(project, map_name)
    kind = member_type.strip().upper()
    member = _find_layer(map_obj, member_name) if kind == "LAYER" else _get_table(map_obj, member_name)
    if kind not in {"LAYER", "TABLE"}:
        raise RuntimeError("member_type 须为 LAYER 或 TABLE")
    result = cartography.upsert_definition_query(member, name, sql, is_active=is_active)
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_delete_definition_query", description="删除图层/表的唯一命名定义查询；需要破坏性门禁和 confirm=true。")
def arcgis_pro_delete_definition_query(
    aprx_path: str,
    map_name: str,
    member_name: str,
    name: str,
    member_type: str = "LAYER",
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("删除定义查询需要 confirm=true")
    _, project, path = _open_project(aprx_path)
    map_obj = _get_map(project, map_name)
    kind = member_type.strip().upper()
    member = _find_layer(map_obj, member_name) if kind == "LAYER" else _get_table(map_obj, member_name)
    if kind not in {"LAYER", "TABLE"}:
        raise RuntimeError("member_type 须为 LAYER 或 TABLE")
    result = cartography.delete_definition_query(member, name)
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_list_label_classes", description="列出图层标注类的名称、表达式、SQL、语言和可见状态。")
def arcgis_pro_list_label_classes(aprx_path: str, map_name: str, layer_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    values = cartography.list_label_classes(_find_layer(_get_map(project, map_name), layer_name))
    return _json_dumps({"ok": True, "aprx_path": path, "label_classes": values, "count": len(values)})


@mcp.tool(name="arcgis_pro_upsert_label_class", description="创建或更新标注类的表达式、过滤 SQL、语言和可见状态。")
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
    _, project, path = _open_project(aprx_path)
    result = cartography.upsert_label_class(
        _find_layer(_get_map(project, map_name), layer_name),
        name,
        expression,
        sql_query=sql_query,
        language=language,
        visible=visible,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_table_properties", description="读取独立表 URI、数据源、定义查询、连接信息和选择集摘要，并脱敏凭据。")
def arcgis_pro_table_properties(
    aprx_path: str,
    map_name: str,
    table_name: str,
    selection_sample_limit: int = 100,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.table_properties(
        _get_table(_get_map(project, map_name), table_name), selection_sample_limit
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_update_table_properties", description="更新独立表名称或 definitionQuery 并读回核验。")
def arcgis_pro_update_table_properties(
    aprx_path: str,
    map_name: str,
    table_name: str,
    new_name: str | None = None,
    definition_query: str | None = None,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = cartography.update_table_properties(
        _get_table(_get_map(project, map_name), table_name),
        new_name=new_name,
        definition_query=definition_query,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_select_table_by_attribute", description="按 SQL 修改独立表选择集并将 ArcPy 派生计数与实际选择集交叉核验。")
def arcgis_pro_select_table_by_attribute(
    aprx_path: str,
    map_name: str,
    table_name: str,
    selection_type: str = "NEW_SELECTION",
    where_clause: str = "",
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    table = _get_table(_get_map(project, map_name), table_name)
    selection = selection_type.strip().upper()
    allowed = {"NEW_SELECTION", "ADD_TO_SELECTION", "REMOVE_FROM_SELECTION", "SUBSET_SELECTION", "SWITCH_SELECTION", "CLEAR_SELECTION"}
    if selection not in allowed:
        raise RuntimeError(f"selection_type 须为 {sorted(allowed)}")
    where = (where_clause or "").strip()
    if selection not in {"SWITCH_SELECTION", "CLEAR_SELECTION"} and not where:
        raise RuntimeError("where_clause 不能为空")
    result = arcpy.management.SelectLayerByAttribute(table, selection, where or None)
    selected_count, result_count = _verify_selection_result(
        table, result, count_output_index=1
    )
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "table_name": getattr(table, "name", table_name),
            "selected_count": selected_count,
            "result_count": result_count,
            "selection_verified": True,
        }
    )


@mcp.tool(name="arcgis_pro_table_selection_count", description="返回独立表当前选择集的精确行数，不用总记录数代替。")
def arcgis_pro_table_selection_count(aprx_path: str, map_name: str, table_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    table = _get_table(_get_map(project, map_name), table_name)
    selected = _layer_selection_set(table)
    return _json_dumps({"ok": True, "aprx_path": path, "table_name": table_name, "selected_count": len(selected)})


@mcp.tool(name="arcgis_pro_table_selection_fids", description="返回独立表当前选择集的有序 OID，带上限和截断标志。")
def arcgis_pro_table_selection_fids(
    aprx_path: str,
    map_name: str,
    table_name: str,
    max_items: int = 1000,
) -> str:
    _, project, path = _open_project(aprx_path)
    table = _get_table(_get_map(project, map_name), table_name)
    values = sorted(_layer_selection_set(table))
    limit = max(1, min(int(max_items), 5000))
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "table_name": table_name,
            "selected_count": len(values),
            "fids": values[:limit],
            "truncated": len(values) > limit,
        }
    )


def _selection_state(layer: Any) -> tuple[list[int], str]:
    try:
        values = sorted(int(value) for value in (layer.getSelectionSet() or ()))
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError("无法读取当前图层选择集") from ex
    digest = hashlib.sha256(
        json.dumps(values, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return values, digest


@mcp.tool(
    name="arcgis_pro_current_layer_set_selection",
    description="以 expected_count+digest 乐观并发保护更新 CURRENT 图层选择集，并核验最终 OID 集。",
)
def arcgis_pro_current_layer_set_selection(
    aprx_path: str,
    layer_name: str,
    object_ids: list[int],
    operation: str,
    expected_current_count: int,
    expected_current_digest: str,
) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    if not isinstance(object_ids, list) or len(object_ids) > 50_000:
        raise RuntimeError("object_ids 必须为最多 50000 项的数组")
    try:
        requested = sorted({int(value) for value in object_ids})
    except (TypeError, ValueError) as ex:
        raise RuntimeError("object_ids 必须全部为整数") from ex
    action = (operation or "").strip().upper()
    methods = {
        "REPLACE": "NEW",
        "ADD": "UNION",
        "REMOVE": "DIFFERENCE",
        "KEEP": "INTERSECT",
        "TOGGLE": "SYMDIFFERENCE",
        "CLEAR": "NEW",
    }
    if action not in methods:
        raise RuntimeError(f"operation 须为 {sorted(methods)}")
    if action == "CLEAR" and requested:
        raise RuntimeError("operation=CLEAR 时 object_ids 必须为空")
    _, project, path = _open_project(aprx_path)
    _view, active_map = _active_map_view(project)
    layer = _find_layer(active_map, layer_name)
    before, before_digest = _selection_state(layer)
    if len(before) != int(expected_current_count):
        raise RuntimeError(
            f"选择集已变化：expected_current_count={expected_current_count}，实际={len(before)}"
        )
    if expected_current_digest != before_digest:
        raise RuntimeError("选择集 digest 已变化；请重新读取 selection_fids")
    before_set = set(before)
    requested_set = set(requested)
    if action in {"REPLACE", "CLEAR"}:
        expected_after = requested_set
    elif action == "ADD":
        expected_after = before_set | requested_set
    elif action == "REMOVE":
        expected_after = before_set - requested_set
    elif action == "KEEP":
        expected_after = before_set & requested_set
    else:
        expected_after = before_set ^ requested_set
    layer.setSelectionSet([] if action == "CLEAR" else requested, methods[action])
    after, after_digest = _selection_state(layer)
    if after != sorted(expected_after):
        raise RuntimeError("ArcGIS Pro 返回的最终选择集与预期不一致")
    refreshed = _refresh_layer_after_window_change(_arcpy(), aprx_path, layer)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(active_map, "name", None),
            "layer_name": getattr(layer, "name", layer_name),
            "operation": action,
            "selected_count": len(after),
            "selection_digest": after_digest,
            "oid_sample": after[:100],
            "sample_truncated": len(after) > 100,
            "ui_refresh_requested": refreshed,
        }
    )


@mcp.tool(
    name="arcgis_pro_set_active_view_camera",
    description="在 CURRENT 地图视图中设置相机 X/Y/Z、scale、heading、pitch、roll，并校验活动地图 URI。",
)
def arcgis_pro_set_active_view_camera(
    aprx_path: str,
    expected_map_uri: str,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    scale: float | None = None,
    heading: float | None = None,
    pitch: float | None = None,
    roll: float | None = None,
) -> str:
    require_allow_write()
    _require_current_window(aprx_path)
    updates = {
        "X": x,
        "Y": y,
        "Z": z,
        "scale": scale,
        "heading": heading,
        "pitch": pitch,
        "roll": roll,
    }
    if not any(value is not None for value in updates.values()):
        raise RuntimeError("至少提供一个 camera 参数")
    if scale is not None and float(scale) <= 0:
        raise RuntimeError("scale 必须大于 0")
    _, project, path = _open_project(aprx_path)
    view, active_map = _active_map_view(project)
    map_uri = _object_uri(active_map)
    if expected_map_uri != map_uri:
        raise RuntimeError("活动地图 URI 已变化；未修改相机")
    camera = view.camera
    before = _camera_dict(camera)
    for attribute, value in updates.items():
        if value is not None:
            setattr(camera, attribute, float(value))
    view.camera = camera
    after = _camera_dict(view.camera)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(active_map, "name", None),
            "map_uri": map_uri,
            "before": before,
            "camera": after,
            "ui_updated": True,
            "draw_complete_waited": False,
            "note": "需要确认绘制完成时请使用 SDK refresh/camera 工具并等待 DrawComplete event",
        }
    )


@mcp.tool(
    name="arcgis_pro_current_layer_query_rows",
    description="从 CURRENT 活动地图的图层读取属性，可选择严格使用当前 UI 选择集。",
)
def arcgis_pro_current_layer_query_rows(
    aprx_path: str,
    layer_name: str,
    fields: list[str],
    where_clause: str = "",
    selected_only: bool = True,
    max_rows: int = 200,
) -> str:
    _require_current_window(aprx_path)
    arcpy, project, path = _open_project(aprx_path)
    _view, active_map = _active_map_view(project)
    result = live_analysis.query_current_layer(
        arcpy,
        active_map,
        layer_name,
        fields,
        where_clause=where_clause,
        selected_only=selected_only,
        max_rows=max_rows,
    )
    result["aprx_path"] = path
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_current_map_run_analysis",
    description="在 CURRENT 活动地图中以图层/表 URI 运行受控具名 GP，保留选择集并可把核验后的输出加回地图。",
)
def arcgis_pro_current_map_run_analysis(
    aprx_path: str,
    tool_name: str,
    parameters: dict[str, Any],
    environment: dict[str, Any] | None = None,
    add_outputs_to_map: bool = True,
) -> str:
    _require_current_window(aprx_path)
    arcpy, project, path = _open_project(aprx_path)
    _view, active_map = _active_map_view(project)
    result = live_analysis.run_current_analysis(
        arcpy,
        active_map,
        tool_name,
        parameters,
        environment=environment,
        add_outputs_to_map=add_outputs_to_map,
    )
    result["aprx_path"] = path
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_edit_preflight",
    description="预检同一数据集的一组 update/insert/delete 操作，锁定命中 OID 与数量并签发五分钟编辑令牌。",
)
def arcgis_pro_edit_preflight(
    dataset_path: str,
    operations: list[dict[str, Any]],
) -> str:
    return _json_dumps(editing.edit_preflight(_arcpy(), dataset_path, operations))


@mcp.tool(
    name="arcgis_pro_edit_apply",
    description="在单次 arcpy.da.Editor 会话中原子执行已预检的编辑；失败时回滚，不跨 MCP 请求保留游标。",
)
def arcgis_pro_edit_apply(
    dataset_path: str,
    operations: list[dict[str, Any]],
    edit_token: str,
) -> str:
    return _json_dumps(editing.edit_apply(_arcpy(), dataset_path, operations, edit_token))


@mcp.tool(
    name="arcgis_pro_edit_geometry_preflight",
    description="按精确 OID 预检 SHAPE@WKT/SHAPE@JSON 几何更新，签发绑定几何内容和 OID digest 的令牌。",
)
def arcgis_pro_edit_geometry_preflight(
    dataset_path: str,
    geometry_token: str,
    rows: list[dict[str, Any]],
    expected_count: int,
) -> str:
    operation = {
        "kind": "update_geometry",
        "geometry_token": geometry_token,
        "rows": rows,
        "expected_count": expected_count,
    }
    return _json_dumps(editing.edit_preflight(_arcpy(), dataset_path, [operation]))


@mcp.tool(
    name="arcgis_pro_edit_geometry_apply",
    description="原子应用已预检的精确 OID 几何更新；内容或目标变化即失败并回滚。",
)
def arcgis_pro_edit_geometry_apply(
    dataset_path: str,
    geometry_token: str,
    rows: list[dict[str, Any]],
    expected_count: int,
    edit_token: str,
) -> str:
    operation = {
        "kind": "update_geometry",
        "geometry_token": geometry_token,
        "rows": rows,
        "expected_count": expected_count,
    }
    return _json_dumps(
        editing.edit_apply(_arcpy(), dataset_path, [operation], edit_token)
    )


@mcp.tool(
    name="arcgis_pro_edit_workspace_preflight",
    description="预检同一工作空间中多个数据集的更新、插入、删除或几何修改并签发单一事务令牌。",
)
def arcgis_pro_edit_workspace_preflight(
    operations: list[dict[str, Any]],
) -> str:
    return _json_dumps(editing.workspace_edit_preflight(_arcpy(), operations))


@mcp.tool(
    name="arcgis_pro_edit_workspace_apply",
    description="在一个 arcpy.da.Editor 事务内原子执行多数据集预检计划；任一计数变化即全部回滚。",
)
def arcgis_pro_edit_workspace_apply(
    operations: list[dict[str, Any]],
    edit_token: str,
) -> str:
    return _json_dumps(
        editing.workspace_edit_apply(_arcpy(), operations, edit_token)
    )


@mcp.tool(
    name="arcgis_pro_da_delete_where",
    description="按 where_clause 删除数据集记录；必须启用破坏性门禁并提供精确 expected_count。",
)
def arcgis_pro_da_delete_where(
    dataset_path: str,
    where_clause: str,
    expected_count: int,
    max_rows_deleted: int = 1000,
) -> str:
    return arcgis_pro_da_delete_selected(
        dataset_path,
        where_clause,
        max_rows_deleted=max_rows_deleted,
        expected_count=expected_count,
    )


@mcp.tool(
    name="arcgis_pro_delete_layer_selection",
    description="删除 CURRENT 图层当前选择集中的要素；拒绝空选择且必须匹配 expected_count，不进入 Pro UI Undo 栈。",
)
def arcgis_pro_delete_layer_selection(
    aprx_path: str,
    layer_name: str,
    expected_count: int,
) -> str:
    require_allow_destructive()
    _require_current_window(aprx_path)
    arcpy, project, path = _open_project(aprx_path)
    _view, active_map = _active_map_view(project)
    layer = _find_layer(active_map, layer_name)
    try:
        selected = sorted(int(value) for value in (layer.getSelectionSet() or ()))
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError("无法读取当前图层选择集") from ex
    if not selected:
        raise RuntimeError("当前选择集为空；未删除任何要素")
    if len(selected) != int(expected_count):
        raise RuntimeError(
            f"expected_count={expected_count}，当前选择集为 {len(selected)}；未删除"
        )
    arcpy.management.DeleteFeatures(layer)
    refreshed = _refresh_layer_after_window_change(arcpy, aprx_path, layer)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": str(getattr(active_map, "name", "")),
            "layer_name": str(getattr(layer, "name", layer_name)),
            "deleted_count": len(selected),
            "deleted_oid_digest": hashlib.sha256(
                json.dumps(selected, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "ui_refresh_requested": refreshed,
            "native_pro_undo": False,
        }
    )


@mcp.tool(
    name="arcgis_pro_layer_enable_time",
    description="启用或禁用图层时间属性；这不会控制活动 MapView 的时间滑块。",
)
def arcgis_pro_layer_enable_time(
    aprx_path: str,
    map_name: str,
    layer_name: str,
    enabled: bool = True,
    start_time_field: str = "",
    end_time_field: str = "",
    auto_calculate_time_range: bool = True,
    time_dimension: str = "",
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    layer = _find_layer(_get_map(project, map_name), layer_name)
    if enabled:
        if not hasattr(layer, "enableTime"):
            raise RuntimeError("当前图层不支持 enableTime")
        layer.enableTime(
            startTimeField=start_time_field or None,
            endTimeField=end_time_field or None,
            autoCalculateTimeRange=bool(auto_calculate_time_range),
            timeDimension=time_dimension or None,
        )
    else:
        if not hasattr(layer, "disableTime"):
            raise RuntimeError("当前图层不支持 disableTime")
        layer.disableTime()
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "layer_name": str(getattr(layer, "name", layer_name)),
            "time_enabled": bool(getattr(layer, "isTimeEnabled", enabled)),
            "active_mapview_slider_changed": False,
        }
    )


@mcp.tool(
    name="arcgis_pro_mapframe_time_info",
    description="读取布局地图框的 MapTime 状态和当前时间范围；MapTime 不代表活动 MapView 时间滑块。",
)
def arcgis_pro_mapframe_time_info(
    aprx_path: str,
    layout_name: str,
    mapframe_name: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    mapframe = _get_mapframe(_get_layout(project, layout_name), mapframe_name)
    map_time = getattr(mapframe, "time", None)
    if map_time is None:
        raise RuntimeError("该地图框没有可用的 MapTime")
    return _json_dumps(
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


@mcp.tool(
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
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    mapframe = _get_mapframe(_get_layout(project, layout_name), mapframe_name)
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
    start = _parse_iso_datetime(start_time, "start_time") if start_time else None
    end = _parse_iso_datetime(end_time, "end_time") if end_time else None
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
    return _json_dumps(
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


@mcp.tool(
    name="arcgis_pro_dataset_exists",
    description="在输入根策略内检查一个数据集是否存在，并返回规范化路径。",
)
def arcgis_pro_dataset_exists(dataset_path: str) -> str:
    return _json_dumps(dataset_management.dataset_exists(_arcpy(), dataset_path))


@mcp.tool(
    name="arcgis_pro_verify_output_dataset",
    description="核验 GP 输出根目录内的数据集是否真实创建，并可要求至少一条记录。",
)
def arcgis_pro_verify_output_dataset(
    output_path: str,
    require_nonempty: bool = False,
) -> str:
    return _json_dumps(
        dataset_management.verify_output_dataset(
            _arcpy(),
            output_path,
            require_nonempty=require_nonempty,
        )
    )


@mcp.tool(
    name="arcgis_pro_dataset_schema",
    description="读取字段、索引、空间参考、子类型、附件和关系类的一体化数据集方案快照。",
)
def arcgis_pro_dataset_schema(dataset_path: str) -> str:
    return _json_dumps(dataset_management.dataset_schema(_arcpy(), dataset_path))


@mcp.tool(
    name="arcgis_pro_list_domains",
    description="列出工作空间中的编码值域和范围域及其策略。",
)
def arcgis_pro_list_domains(workspace_path: str) -> str:
    return _json_dumps(dataset_management.list_domains(_arcpy(), workspace_path))


@mcp.tool(
    name="arcgis_pro_list_subtypes",
    description="列出数据集子类型、默认子类型以及逐字段默认值和值域。",
)
def arcgis_pro_list_subtypes(dataset_path: str) -> str:
    return _json_dumps(dataset_management.list_subtypes(_arcpy(), dataset_path))


@mcp.tool(
    name="arcgis_pro_create_domain",
    description="在地理数据库中创建编码值域或范围域；需要写入开关和方案锁。",
)
def arcgis_pro_create_domain(
    workspace_path: str,
    domain_name: str,
    domain_description: str,
    field_type: str,
    domain_type: str = "CODED",
    split_policy: str = "DEFAULT",
    merge_policy: str = "DEFAULT",
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_create_domain(
                _arcpy(),
                workspace_path,
                domain_name,
                domain_description,
                field_type,
                domain_type,
                split_policy,
                merge_policy,
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_delete_domain",
    description="删除指定属性域；需要破坏性开关，且 confirm_domain_name 必须精确匹配。",
)
def arcgis_pro_delete_domain(
    workspace_path: str,
    domain_name: str,
    confirm_domain_name: str,
) -> str:
    require_allow_destructive()
    if confirm_domain_name != domain_name:
        raise RuntimeError("confirm_domain_name 必须与 domain_name 完全一致")
    return _json_dumps(
        {
            "ok": True,
            "domain_name": domain_name,
            "messages": dataset_management.run_delete_domain(
                _arcpy(), workspace_path, domain_name
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_alter_domain",
    description="更新属性域名称、描述、拆分/合并策略或所有者。",
)
def arcgis_pro_alter_domain(
    workspace_path: str,
    domain_name: str,
    new_domain_name: str = "",
    new_domain_description: str = "",
    split_policy: str = "",
    merge_policy: str = "",
    new_domain_owner: str = "",
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_alter_domain(
                _arcpy(),
                workspace_path,
                domain_name,
                new_domain_name,
                new_domain_description,
                split_policy,
                merge_policy,
                new_domain_owner,
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_add_coded_value_to_domain",
    description="向编码值域添加一个代码与描述。",
)
def arcgis_pro_add_coded_value_to_domain(
    workspace_path: str,
    domain_name: str,
    code: Any,
    description: str,
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_add_coded_value_to_domain(
                _arcpy(), workspace_path, domain_name, code, description
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_delete_coded_value_from_domain",
    description="从编码值域移除一个精确代码；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_delete_coded_value_from_domain(
    workspace_path: str,
    domain_name: str,
    code: Any,
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除编码值")
    return _json_dumps(
        {
            "ok": True,
            "messages": dataset_management.run_delete_coded_value_from_domain(
                _arcpy(), workspace_path, domain_name, code
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_set_range_domain",
    description="设置范围域的最小值与最大值。",
)
def arcgis_pro_set_range_domain(
    workspace_path: str,
    domain_name: str,
    minimum_value: Any,
    maximum_value: Any,
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_set_range_domain(
                _arcpy(), workspace_path, domain_name, minimum_value, maximum_value
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_assign_domain_to_field",
    description="把属性域分配给字段，可限定一个子类型代码。",
)
def arcgis_pro_assign_domain_to_field(
    dataset_path: str,
    field_name: str,
    domain_name: str,
    subtype_code: int | None = None,
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_assign_domain_to_field(
                _arcpy(), dataset_path, field_name, domain_name, subtype_code
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_remove_domain_from_field",
    description="移除字段的属性域分配；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_remove_domain_from_field(
    dataset_path: str,
    field_name: str,
    subtype_code: int | None = None,
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除字段值域")
    return _json_dumps(
        {
            "ok": True,
            "messages": dataset_management.run_remove_domain_from_field(
                _arcpy(), dataset_path, field_name, subtype_code
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_set_subtype_field",
    description="把一个字段设为数据集的子类型字段。",
)
def arcgis_pro_set_subtype_field(dataset_path: str, field_name: str) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_set_subtype_field(
                _arcpy(), dataset_path, field_name
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_clear_subtype_field",
    description="清除数据集的子类型字段；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_clear_subtype_field(dataset_path: str, confirm: bool = False) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能清除子类型字段")
    return _json_dumps(
        {
            "ok": True,
            "messages": dataset_management.run_clear_subtype_field(
                _arcpy(), dataset_path
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_add_subtype",
    description="向数据集添加一个子类型代码和名称。",
)
def arcgis_pro_add_subtype(
    dataset_path: str,
    subtype_code: int,
    subtype_name: str,
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_add_subtype(
                _arcpy(), dataset_path, subtype_code, subtype_name
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_remove_subtype",
    description="移除精确子类型代码；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_remove_subtype(
    dataset_path: str,
    subtype_code: int,
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除子类型")
    return _json_dumps(
        {
            "ok": True,
            "messages": dataset_management.run_remove_subtype(
                _arcpy(), dataset_path, subtype_code
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_set_default_subtype",
    description="设置数据集的默认子类型代码。",
)
def arcgis_pro_set_default_subtype(dataset_path: str, subtype_code: int) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_set_default_subtype(
                _arcpy(), dataset_path, subtype_code
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_attachments_info",
    description="读取数据集是否启用附件以及关联关系类名称。",
)
def arcgis_pro_attachments_info(dataset_path: str) -> str:
    return _json_dumps(dataset_management.attachments_info(_arcpy(), dataset_path))


@mcp.tool(
    name="arcgis_pro_enable_attachments",
    description="为数据集启用附件；需要写入开关和方案锁。",
)
def arcgis_pro_enable_attachments(dataset_path: str) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_enable_attachments(
                _arcpy(), dataset_path
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_disable_attachments",
    description="禁用并移除数据集附件能力；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_disable_attachments(dataset_path: str, confirm: bool = False) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能禁用附件")
    return _json_dumps(
        {
            "ok": True,
            "messages": dataset_management.run_disable_attachments(
                _arcpy(), dataset_path
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_add_attachments",
    description="按匹配表批量添加附件；路径受输入根策略约束。",
)
def arcgis_pro_add_attachments(
    dataset_path: str,
    in_join_field: str,
    match_table: str,
    match_join_field: str,
    match_path_field: str,
    working_folder: str = "",
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_add_attachments(
                _arcpy(),
                dataset_path,
                in_join_field,
                match_table,
                match_join_field,
                match_path_field,
                working_folder,
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_remove_attachments",
    description="按匹配表批量移除附件；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_remove_attachments(
    dataset_path: str,
    in_join_field: str,
    match_table: str,
    match_join_field: str,
    match_name_field: str = "",
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除附件")
    return _json_dumps(
        {
            "ok": True,
            "messages": dataset_management.run_remove_attachments(
                _arcpy(),
                dataset_path,
                in_join_field,
                match_table,
                match_join_field,
                match_name_field,
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_relationship_classes",
    description="读取与数据集相关的关系类名称。",
)
def arcgis_pro_relationship_classes(dataset_path: str) -> str:
    return _json_dumps(
        dataset_management.relationship_classes(_arcpy(), dataset_path)
    )


@mcp.tool(
    name="arcgis_pro_create_relationship_class",
    description="在 GP 输出根中创建关系类并核验输出存在。",
)
def arcgis_pro_create_relationship_class(
    origin_table: str,
    destination_table: str,
    out_relationship_class: str,
    relationship_type: str,
    forward_label: str,
    backward_label: str,
    message_direction: str,
    cardinality: str,
    origin_primary_key: str,
    origin_foreign_key: str,
    attributed: bool = False,
    destination_primary_key: str = "",
    destination_foreign_key: str = "",
) -> str:
    return _json_dumps(
        {
            "output_relationship_class": out_relationship_class,
            "messages": dataset_management.run_create_relationship_class(
                _arcpy(),
                origin_table,
                destination_table,
                out_relationship_class,
                relationship_type,
                forward_label,
                backward_label,
                message_direction,
                cardinality,
                origin_primary_key,
                origin_foreign_key,
                attributed,
                destination_primary_key,
                destination_foreign_key,
            ),
            "verified": True,
        }
    )


@mcp.tool(
    name="arcgis_pro_delete_relationship_class",
    description="删除 GP 输出根内的关系类；需要破坏性开关且确认路径必须完全一致。",
)
def arcgis_pro_delete_relationship_class(
    relationship_class_path: str,
    confirm_relationship_class_path: str,
) -> str:
    require_allow_destructive()
    if confirm_relationship_class_path != relationship_class_path:
        raise RuntimeError("confirm_relationship_class_path 必须与目标路径完全一致")
    messages = dataset_management.run_delete_relationship_class(
        _arcpy(), relationship_class_path
    )
    return _json_dumps(
        {
            "ok": True,
            "relationship_class_path": relationship_class_path,
            "messages": messages,
        }
    )


@mcp.tool(
    name="arcgis_pro_topology_info",
    description="读取拓扑名称、聚类容差和参与要素类。",
)
def arcgis_pro_topology_info(topology_path: str) -> str:
    return _json_dumps(dataset_management.topology_info(_arcpy(), topology_path))


@mcp.tool(
    name="arcgis_pro_create_topology",
    description="在 GP 输出根中的要素数据集内创建拓扑并核验输出。",
)
def arcgis_pro_create_topology(
    feature_dataset: str,
    topology_name: str,
    cluster_tolerance: float | None = None,
) -> str:
    return _json_dumps(
        dataset_management.run_create_topology(
            _arcpy(), feature_dataset, topology_name, cluster_tolerance
        )
    )


@mcp.tool(
    name="arcgis_pro_add_feature_class_to_topology",
    description="把要素类加入拓扑，并设置 XY/Z rank。",
)
def arcgis_pro_add_feature_class_to_topology(
    topology_path: str,
    feature_class: str,
    xy_rank: int = 1,
    z_rank: int = 1,
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_add_feature_class_to_topology(
                _arcpy(), topology_path, feature_class, xy_rank, z_rank
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_add_rule_to_topology",
    description="向拓扑添加一个精确规则，可指定目标要素类。",
)
def arcgis_pro_add_rule_to_topology(
    topology_path: str,
    rule_type: str,
    origin_feature_class: str,
    destination_feature_class: str = "",
) -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_add_rule_to_topology(
                _arcpy(),
                topology_path,
                rule_type,
                origin_feature_class,
                destination_feature_class,
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_remove_rule_from_topology",
    description="从拓扑移除一个精确规则；需要破坏性开关及 confirm_rule_name 精确匹配。",
)
def arcgis_pro_remove_rule_from_topology(
    topology_path: str,
    rule_name: str,
    confirm_rule_name: str,
) -> str:
    require_allow_destructive()
    if confirm_rule_name != rule_name:
        raise RuntimeError("confirm_rule_name 必须与 rule_name 完全一致")
    return _json_dumps(
        {
            "ok": True,
            "messages": dataset_management.run_remove_rule_from_topology(
                _arcpy(), topology_path, rule_name
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_remove_feature_class_from_topology",
    description="从拓扑移除参与要素类；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_remove_feature_class_from_topology(
    topology_path: str,
    feature_class: str,
    confirm: bool = False,
) -> str:
    require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除拓扑参与类")
    return _json_dumps(
        {
            "ok": True,
            "messages": dataset_management.run_remove_feature_class_from_topology(
                _arcpy(), topology_path, feature_class
            ),
        }
    )


@mcp.tool(
    name="arcgis_pro_validate_topology",
    description="验证拓扑的全部范围或指定 extent，并返回 GP 消息。",
)
def arcgis_pro_validate_topology(topology_path: str, extent: str = "") -> str:
    return _json_dumps(
        {
            "messages": dataset_management.run_validate_topology(
                _arcpy(), topology_path, extent
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_export_topology_errors",
    description="把拓扑错误导出到 GP 输出根，并报告所创建的点、线、面错误类。",
)
def arcgis_pro_export_topology_errors(
    topology_path: str,
    output_path: str,
    output_name: str,
) -> str:
    return _json_dumps(
        dataset_management.run_export_topology_errors(
            _arcpy(), topology_path, output_path, output_name
        )
    )


@mcp.tool(
    name="arcgis_pro_extension_status",
    description="读取 Spatial、3D、Image Analyst、Network 等 ArcGIS 扩展许可状态，不执行签出。",
)
def arcgis_pro_extension_status(extension_names: list[str] | None = None) -> str:
    return _json_dumps(
        {
            "extensions": raster_runtime.extension_status(
                _arcpy(), extension_names
            )
        }
    )


@mcp.tool(
    name="arcgis_pro_validate_analysis_environment",
    description="校验受支持的 ArcPy 环境映射及其中的输入路径，不改变全局环境。",
)
def arcgis_pro_validate_analysis_environment(
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        {
            "ok": True,
            "arcpy_environment": raster_runtime.validate_environment(environment),
        }
    )


@mcp.tool(
    name="arcgis_pro_raster_info",
    description="读取栅格格式、波段、像元类型、范围、空间参考和基本统计属性。",
)
def arcgis_pro_raster_info(raster_path: str) -> str:
    return _json_dumps(raster_runtime.raster_info(_arcpy(), raster_path))


@mcp.tool(
    name="arcgis_pro_raster_fill",
    description="在一次受控 Spatial Analyst 签出和局部环境中填洼，并核验输出栅格。",
)
def arcgis_pro_raster_fill(
    in_surface_raster: str,
    out_surface_raster: str,
    z_limit: float | None = None,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_runtime.run_fill(
            _arcpy(),
            in_surface_raster,
            out_surface_raster,
            z_limit,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_flow_direction",
    description="计算 D8/MFD/DINF 流向，可选输出 drop raster，并核验所有输出。",
)
def arcgis_pro_raster_flow_direction(
    in_surface_raster: str,
    out_flow_direction_raster: str,
    force_flow: str = "NORMAL",
    flow_direction_type: str = "D8",
    out_drop_raster: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_runtime.run_flow_direction(
            _arcpy(),
            in_surface_raster,
            out_flow_direction_raster,
            force_flow,
            flow_direction_type,
            out_drop_raster,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_flow_accumulation",
    description="计算流量累积栅格，可选权重栅格和受控分析环境，并核验输出。",
)
def arcgis_pro_raster_flow_accumulation(
    in_flow_direction_raster: str,
    out_accumulation_raster: str,
    in_weight_raster: str = "",
    data_type: str = "FLOAT",
    flow_direction_type: str = "D8",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_runtime.run_flow_accumulation(
            _arcpy(),
            in_flow_direction_raster,
            out_accumulation_raster,
            in_weight_raster,
            data_type,
            flow_direction_type,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_snap_pour_point",
    description="把倾泻点吸附到高累积像元，并在 Spatial Analyst 会话中核验输出。",
)
def arcgis_pro_raster_snap_pour_point(
    in_pour_point_data: str,
    in_accumulation_raster: str,
    out_raster: str,
    snap_distance: float,
    pour_point_field: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_runtime.run_snap_pour_point(
            _arcpy(),
            in_pour_point_data,
            in_accumulation_raster,
            out_raster,
            snap_distance,
            pour_point_field,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_watershed",
    description="根据流向栅格和倾泻点生成分水岭栅格，并核验输出。",
)
def arcgis_pro_raster_watershed(
    in_flow_direction_raster: str,
    in_pour_point_data: str,
    out_raster: str,
    pour_point_field: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_runtime.run_watershed(
            _arcpy(),
            in_flow_direction_raster,
            in_pour_point_data,
            out_raster,
            pour_point_field,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_network_travel_modes",
    description="列出网络数据源可用的 travel modes 及阻抗、限制和层级属性。",
)
def arcgis_pro_network_travel_modes(network_data_source: str) -> str:
    return _json_dumps(
        network_analysis.list_travel_modes(_arcpy(), network_data_source)
    )


@mcp.tool(
    name="arcgis_pro_network_solve_route",
    description="使用 arcpy.nax 一次性求解路线并导出、核验 Routes/Stops/Directions，不保留进程内 solver。",
)
def arcgis_pro_network_solve_route(
    network_data_source: str,
    stops: str,
    travel_mode: str,
    out_routes: str,
    out_stops: str,
    out_directions: str = "",
    point_barriers: str = "",
    line_barriers: str = "",
    polygon_barriers: str = "",
    time_of_day: str = "",
    ignore_invalid_locations: bool = True,
    overwrite: bool = False,
    confirm_overwrite: bool = False,
) -> str:
    return _json_dumps(
        network_analysis.solve_route_once(
            _arcpy(),
            network_data_source,
            stops,
            travel_mode,
            out_routes,
            out_stops,
            out_directions,
            point_barriers,
            line_barriers,
            polygon_barriers,
            time_of_day,
            ignore_invalid_locations,
            overwrite,
            confirm_overwrite,
        )
    )


@mcp.tool(
    name="arcgis_pro_network_solve_service_area",
    description="用本地 NetworkDataset 一次性求解 Service Area，导出并核验 polygons/lines/facilities。",
)
def arcgis_pro_network_solve_service_area(
    network_data_source: str,
    facilities: str,
    travel_mode: str,
    cutoffs: list[float],
    out_polygons: str,
    out_lines: str = "",
    out_facilities: str = "",
    point_barriers: str = "",
    line_barriers: str = "",
    polygon_barriers: str = "",
    time_units: str = "MINUTES",
    distance_units: str = "KILOMETERS",
    travel_direction: str = "FROM_FACILITY",
    geometry_at_cutoff: str = "RINGS",
    geometry_at_overlap: str = "OVERLAP",
    polygon_detail: str = "STANDARD",
    time_of_day: str = "",
    time_zone: str = "LOCAL_TIME_AT_LOCATIONS",
    ignore_invalid_locations: bool = True,
) -> str:
    return _json_dumps(
        network_analysis.solve_service_area_once(
            _arcpy(),
            network_data_source,
            facilities,
            travel_mode,
            cutoffs,
            out_polygons,
            out_lines,
            out_facilities,
            point_barriers,
            line_barriers,
            polygon_barriers,
            time_units,
            distance_units,
            travel_direction,
            geometry_at_cutoff,
            geometry_at_overlap,
            polygon_detail,
            time_of_day,
            time_zone,
            ignore_invalid_locations,
        )
    )


@mcp.tool(
    name="arcgis_pro_network_solve_closest_facility",
    description="用本地 NetworkDataset 一次性求解 Closest Facility，并核验所有显式输出。",
)
def arcgis_pro_network_solve_closest_facility(
    network_data_source: str,
    incidents: str,
    facilities: str,
    travel_mode: str,
    out_routes: str,
    out_incidents: str = "",
    out_facilities: str = "",
    out_directions: str = "",
    point_barriers: str = "",
    line_barriers: str = "",
    polygon_barriers: str = "",
    impedance_cutoff: float | None = None,
    target_facility_count: int = 1,
    time_units: str = "MINUTES",
    distance_units: str = "KILOMETERS",
    travel_direction: str = "TO_FACILITY",
    time_of_day: str = "",
    time_of_day_usage: str = "DEPARTURE_TIME",
    time_zone: str = "LOCAL_TIME_AT_LOCATIONS",
    route_shape_type: str = "TRUE_SHAPE_WITH_MEASURES",
    ignore_invalid_locations: bool = True,
) -> str:
    return _json_dumps(
        network_analysis.solve_closest_facility_once(
            _arcpy(),
            network_data_source,
            incidents,
            facilities,
            travel_mode,
            out_routes,
            out_incidents,
            out_facilities,
            out_directions,
            point_barriers,
            line_barriers,
            polygon_barriers,
            impedance_cutoff,
            target_facility_count,
            time_units,
            distance_units,
            travel_direction,
            time_of_day,
            time_of_day_usage,
            time_zone,
            route_shape_type,
            ignore_invalid_locations,
        )
    )


@mcp.tool(
    name="arcgis_pro_network_solve_od_cost_matrix",
    description="用本地 NetworkDataset 一次性求解 Origin-Destination Cost Matrix，拒绝计费 URL 服务。",
)
def arcgis_pro_network_solve_od_cost_matrix(
    network_data_source: str,
    origins: str,
    destinations: str,
    travel_mode: str,
    out_lines: str,
    out_origins: str = "",
    out_destinations: str = "",
    point_barriers: str = "",
    line_barriers: str = "",
    polygon_barriers: str = "",
    impedance_cutoff: float | None = None,
    destination_count: int = 1,
    time_units: str = "MINUTES",
    distance_units: str = "KILOMETERS",
    time_of_day: str = "",
    time_zone: str = "LOCAL_TIME_AT_LOCATIONS",
    line_shape_type: str = "NO_LINE",
    ignore_invalid_locations: bool = True,
) -> str:
    return _json_dumps(
        network_analysis.solve_origin_destination_cost_matrix_once(
            _arcpy(),
            network_data_source,
            origins,
            destinations,
            travel_mode,
            out_lines,
            out_origins,
            out_destinations,
            point_barriers,
            line_barriers,
            polygon_barriers,
            impedance_cutoff,
            destination_count,
            time_units,
            distance_units,
            time_of_day,
            time_zone,
            line_shape_type,
            ignore_invalid_locations,
        )
    )


@mcp.tool(
    name="arcgis_pro_list_temporary_views",
    description="列出当前 MCP 或窗口宿主进程创建的临时 feature layer/table view 及其不透明引用。",
)
def arcgis_pro_list_temporary_views() -> str:
    values = session_refs.list_public()
    return _json_dumps({"ok": True, "count": len(values), "temporary_views": values})


@mcp.tool(
    name="arcgis_pro_project_cache_status",
    description="读取文件模式 ArcGISProject 缓存中的工程路径；CURRENT 工程不会跨请求缓存。",
)
def arcgis_pro_project_cache_status() -> str:
    values = sorted(_PROJECT_CACHE)
    return _json_dumps({"ok": True, "cached_project_count": len(values), "cache_keys": values})


@mcp.tool(
    name="arcgis_pro_window_job_submit",
    description="向已确认的 CURRENT 窗口宿主异步提交一个带 aprx_path=CURRENT 的工具调用，并返回可查询 request_id。",
)
def arcgis_pro_window_job_submit(
    tool_name: str,
    arguments: dict[str, Any],
    idempotency_key: str = "",
) -> str:
    if tool_name in {
        "arcgis_pro_window_job_submit",
        "arcgis_pro_window_job_status",
        "arcgis_pro_window_job_cancel",
        "arcgis_pro_window_wait_for_change",
        "arcgis_pro_detach_window",
    }:
        raise RuntimeError("该宿主控制工具不能作为异步子任务提交")
    from arcgis_pro_mcp.pro_attach import submit_host_job

    return _json_dumps(submit_host_job(tool_name, arguments, idempotency_key))


@mcp.tool(
    name="arcgis_pro_window_job_status",
    description="读取窗口宿主保留的异步或超时任务终态、错误及结构化结果。",
)
def arcgis_pro_window_job_status(request_id: str) -> str:
    from arcgis_pro_mcp.pro_attach import host_job_status

    return _json_dumps(host_job_status(request_id))


@mcp.tool(
    name="arcgis_pro_window_job_cancel",
    description="取消尚未开始的窗口任务；Python 宿主对已运行本地 GP 仅报告 cancel_requested，可靠取消需 SDK。",
)
def arcgis_pro_window_job_cancel(request_id: str) -> str:
    from arcgis_pro_mcp.pro_attach import cancel_host_job

    return _json_dumps(cancel_host_job(request_id))


@mcp.tool(
    name="arcgis_pro_window_wait_for_change",
    description="长轮询 CURRENT 工程、活动视图、相机和选择集 revision；Python 源不声称提供 DrawComplete。",
)
def arcgis_pro_window_wait_for_change(
    after_revision: int = 0,
    timeout_ms: int = 30_000,
) -> str:
    from arcgis_pro_mcp.pro_attach import wait_for_window_change

    return _json_dumps(wait_for_window_change(after_revision, timeout_ms))


@mcp.tool(
    name="arcgis_pro_release_project",
    description="释放一个文件模式工程的进程缓存引用，以便外部修改或方案锁及时可见；不删除或保存工程。",
)
def arcgis_pro_release_project(aprx_path: str, confirm_aprx_path: str) -> str:
    require_allow_destructive()
    if confirm_aprx_path != aprx_path:
        raise RuntimeError("confirm_aprx_path 必须与 aprx_path 完全一致")
    path = validate_project_path(aprx_path, "aprx_path")
    if is_current_project_token(path):
        raise RuntimeError("CURRENT 不跨请求缓存，无需释放")
    key = _project_cache_key(path)
    project = _PROJECT_CACHE.pop(key, None)
    released = project is not None
    if project is not None:
        del project
    return _json_dumps({"ok": True, "aprx_path": path, "released": released})


@mcp.tool(
    name="arcgis_pro_reload_project",
    description="释放并重新打开一个文件模式 .aprx，返回新的工程摘要；不会保存旧缓存引用中的未保存修改。",
)
def arcgis_pro_reload_project(aprx_path: str, confirm_aprx_path: str) -> str:
    require_allow_destructive()
    if confirm_aprx_path != aprx_path:
        raise RuntimeError("confirm_aprx_path 必须与 aprx_path 完全一致")
    path = validate_project_path(aprx_path, "aprx_path")
    if is_current_project_token(path):
        raise RuntimeError("CURRENT 由窗口宿主逐请求绑定，不能通过文件缓存重载")
    key = _project_cache_key(path)
    previous = _PROJECT_CACHE.pop(key, None)
    if previous is not None:
        del previous
    _, project, opened = _open_project(path)
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": opened,
            "reloaded": True,
            "map_count": len(list(project.listMaps())),
            "layout_count": len(list(project.listLayouts())),
        }
    )


@mcp.tool(
    name="arcgis_pro_release_temporary_view",
    description="释放一个由本进程创建的临时 feature layer/table view 引用，不删除其源数据。",
)
def arcgis_pro_release_temporary_view(reference: str) -> str:
    require_allow_write()
    return _json_dumps(session_refs.release(_arcpy(), reference))


def _publishing_arcpy(aprx_path: str) -> tuple[Any, str]:
    target = str(aprx_path or "").strip()
    if not target:
        return _arcpy(), ""
    arcpy, _project, opened_path = _open_project(target)
    return arcpy, opened_path


@mcp.tool(
    name="arcgis_pro_portal_status",
    description="读取活动 Portal、登录状态和白名单匹配结果；不返回用户名、密码或 token。",
)
def arcgis_pro_portal_status(aprx_path: str = "") -> str:
    arcpy, opened_path = _publishing_arcpy(aprx_path)
    result = publishing.portal_status(arcpy)
    result["ok"] = True
    if opened_path:
        result["aprx_path"] = opened_path
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_get_artifact_digest",
    description="读取受控导出根内发布工件的 SHA-256 和字节数，用于阶段间完整性核验。",
)
def arcgis_pro_get_artifact_digest(artifact_path: str) -> str:
    result = publishing.artifact_digest(artifact_path)
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
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
    source_kind = _strict_member_type(
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

    arcpy, project, opened_path = _open_project(aprx_path)
    if source_kind == "MAP":
        source = _get_map(project, map_name)
    elif source_kind in {"LAYER", "TABLE"}:
        map_obj, member = _get_map_member(project, map_name, member_name, source_kind)
        source = _ScopedWebLayerSharingSource(map_obj, member)
    elif source_kind == "DATASET":
        source = member_name
    else:
        source = session_refs.resolve(member_name, expected_kinds={"gp_result"})

    result = publishing.create_sharing_draft(
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
    return _json_dumps(result)


@mcp.tool(
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
    arcpy, opened_path = _publishing_arcpy(aprx_path)
    result = publishing.stage_service_definition(
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
    return _json_dumps(result)


@mcp.tool(
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
    arcpy, opened_path = _publishing_arcpy(aprx_path)
    result = publishing.publish_service_definition(
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
    return _json_dumps(result)


def _chart_member(
    aprx_path: str,
    map_name: str,
    member_name: str,
    member_type: str,
) -> tuple[Any, Any, str, str]:
    kind = _strict_member_type(member_type, {"LAYER", "TABLE"})
    arcpy, project, opened_path = _open_project(aprx_path)
    _map_obj, member = _get_map_member(project, map_name, member_name, kind)
    return arcpy, member, opened_path, kind


@mcp.tool(name="arcgis_pro_list_charts", description="列出图层或独立表上的 typed ArcPy 图表摘要。")
def arcgis_pro_list_charts(
    aprx_path: str,
    map_name: str,
    member_name: str,
    member_type: str = "LAYER",
) -> str:
    _arcpy_module, member, opened_path, kind = _chart_member(
        aprx_path, map_name, member_name, member_type
    )
    values = charts.list_charts(member)
    return _json_dumps(
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


@mcp.tool(name="arcgis_pro_chart_info", description="按唯一标题读取一个 typed ArcPy 图表的字段、轴和显示属性。")
def arcgis_pro_chart_info(
    aprx_path: str,
    map_name: str,
    member_name: str,
    title: str,
    member_type: str = "LAYER",
) -> str:
    _arcpy_module, member, opened_path, kind = _chart_member(
        aprx_path, map_name, member_name, member_type
    )
    requested = str(title or "").strip()
    if not requested:
        raise RuntimeError("title 不能为空")
    matches = [value for value in charts.list_charts(member) if value.get("title") == requested]
    if not matches:
        raise RuntimeError(f"未找到图表 {requested!r}")
    if len(matches) > 1:
        raise RuntimeError(f"图表标题不唯一：{requested!r}")
    return _json_dumps(
        {
            "ok": True,
            "aprx_path": opened_path,
            "map_name": map_name,
            "member_type": kind,
            "member_name": member_name,
            "chart": matches[0],
        }
    )


@mcp.tool(name="arcgis_pro_upsert_chart", description="按标题创建或更新 Bar/Line/Scatter/Histogram/Pie 图表；不做 CIM 猜测删除。")
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
    arcpy, member, opened_path, kind = _chart_member(
        aprx_path, map_name, member_name, member_type
    )
    result = charts.upsert_chart(
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
    return _json_dumps(result)


@mcp.tool(name="arcgis_pro_export_chart", description="将唯一标题的图表导出为受控 SVG、PNG 或 JPEG。")
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
    _arcpy_module, member, opened_path, kind = _chart_member(
        aprx_path, map_name, member_name, member_type
    )
    result = charts.export_chart(member, title, output_path, width=width, height=height)
    result.update(
        {
            "ok": True,
            "aprx_path": opened_path,
            "map_name": map_name,
            "member_type": kind,
            "member_name": member_name,
        }
    )
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_chart_mutation_capabilities",
    description="读取图表变更边界；当前明确拒绝没有受支持 ArcPy API 的图表删除。",
)
def arcgis_pro_chart_mutation_capabilities() -> str:
    result = charts.chart_delete_capability()
    return _json_dumps({"ok": True, "delete": result})


@mcp.tool(
    name="arcgis_pro_list_versions",
    description="列出受控 .sde 或精确白名单 FeatureServer 中当前用户可见的版本及属性。",
)
def arcgis_pro_list_versions(workspace_path: str) -> str:
    result = enterprise_gdb.list_versions(_arcpy(), workspace_path)
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_create_version",
    description="在企业地理数据库或精确白名单分支版本服务中创建命名版本；需要企业写门。",
)
def arcgis_pro_create_version(
    workspace_path: str,
    parent_version: str,
    version_name: str,
    access_permission: str = "PRIVATE",
    description: str = "",
) -> str:
    result = enterprise_gdb.create_version(
        _arcpy(),
        workspace_path,
        parent_version,
        version_name,
        access_permission=access_permission,
        description=description,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_change_version",
    description="把工程中唯一匹配的图层或独立表切换到指定传统、历史或分支版本；需要企业写门。",
)
def arcgis_pro_change_version(
    aprx_path: str,
    map_name: str,
    member_name: str,
    version_type: str,
    member_type: str = "LAYER",
    version_name: str = "",
    history_date: str = "",
    include_participating: bool = True,
) -> str:
    kind = _strict_member_type(member_type, {"LAYER", "TABLE"})
    arcpy, project, opened_path = _open_project(aprx_path)
    _map_obj, member = _get_map_member(project, map_name, member_name, kind)
    result = enterprise_gdb.change_version(
        arcpy,
        member,
        version_type,
        version_name=version_name,
        history_date=history_date,
        include_participating=include_participating,
    )
    result.update(
        {
            "ok": True,
            "aprx_path": opened_path,
            "map_name": map_name,
            "member_name": member_name,
            "member_type": kind,
        }
    )
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_reconcile_versions",
    description=(
        "协调显式版本范围；因冲突处理可能覆盖值，始终需要企业写门、破坏性门和目标/版本列表精确回显。"
    ),
)
def arcgis_pro_reconcile_versions(
    workspace_path: str,
    target_version: str,
    edit_versions: list[str],
    reconcile_mode: str = "ALL_VERSIONS",
    acquire_locks: bool = True,
    abort_on_conflicts: bool = True,
    conflict_definition: str = "BY_OBJECT",
    conflict_resolution: str = "FAVOR_TARGET_VERSION",
    with_post: bool = False,
    with_delete: bool = False,
    out_log_path: str = "",
    confirm_action: str = "",
    confirm_target_version: str = "",
    confirm_edit_versions: list[str] | None = None,
) -> str:
    result = enterprise_gdb.reconcile_versions(
        _arcpy(),
        workspace_path,
        target_version,
        edit_versions,
        reconcile_mode=reconcile_mode,
        acquire_locks=acquire_locks,
        abort_on_conflicts=abort_on_conflicts,
        conflict_definition=conflict_definition,
        conflict_resolution=conflict_resolution,
        with_post=with_post,
        with_delete=with_delete,
        out_log_path=out_log_path,
        confirm_action=confirm_action,
        confirm_target_version=confirm_target_version,
        confirm_edit_versions=confirm_edit_versions,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_post_version",
    description=(
        "用官方 ReconcileVersions 对一个精确编辑版本执行协调并提交；不可撤销，"
        "需要企业/破坏性双门、目标与编辑版本精确回显及固定确认短语。"
    ),
)
def arcgis_pro_post_version(
    workspace_path: str,
    version_name: str,
    target_version: str,
    abort_on_conflicts: bool = True,
    conflict_definition: str = "BY_OBJECT",
    conflict_resolution: str = "FAVOR_TARGET_VERSION",
    out_log_path: str = "",
    confirm_version_name: str = "",
    confirm_target_version: str = "",
    confirm_action: str = "",
) -> str:
    result = enterprise_gdb.post_version(
        _arcpy(),
        workspace_path,
        version_name,
        target_version,
        abort_on_conflicts=abort_on_conflicts,
        conflict_definition=conflict_definition,
        conflict_resolution=conflict_resolution,
        out_log_path=out_log_path,
        confirm_version_name=confirm_version_name,
        confirm_target_version=confirm_target_version,
        confirm_action=confirm_action,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_delete_version",
    description="删除精确命名版本并复查其消失；需要企业写门、破坏性门及版本名精确回显。",
)
def arcgis_pro_delete_version(
    workspace_path: str,
    version_name: str,
    confirm_version_name: str = "",
) -> str:
    result = enterprise_gdb.delete_version(
        _arcpy(),
        workspace_path,
        version_name,
        confirm_version_name=confirm_version_name,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_register_as_versioned",
    description="将受控 .sde 数据集注册为版本化并通过 Describe 复核；需要企业写门。",
)
def arcgis_pro_register_as_versioned(
    dataset_path: str,
    edit_to_base: str = "NO_EDITS_TO_BASE",
) -> str:
    result = enterprise_gdb.register_as_versioned(
        _arcpy(),
        dataset_path,
        edit_to_base=edit_to_base,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_unregister_as_versioned",
    description=(
        "取消受控 .sde 数据集版本化；需要企业写门、破坏性门、路径精确回显，丢弃编辑另需固定确认短语。"
    ),
)
def arcgis_pro_unregister_as_versioned(
    dataset_path: str,
    keep_edit: str = "KEEP_EDIT",
    compress_default: str = "NO_COMPRESS_DEFAULT",
    confirm_dataset_path: str = "",
    confirm_discard_edits: str = "",
) -> str:
    result = enterprise_gdb.unregister_as_versioned(
        _arcpy(),
        dataset_path,
        keep_edit=keep_edit,
        compress_default=compress_default,
        confirm_dataset_path=confirm_dataset_path,
        confirm_discard_edits=confirm_discard_edits,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_dataset_maintenance_info",
    description="读取受控 .sde 数据集的索引、编辑者追踪、GlobalID 和版本化状态。",
)
def arcgis_pro_dataset_maintenance_info(dataset_path: str) -> str:
    result = schema_maintenance.dataset_maintenance_info(_arcpy(), dataset_path)
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_add_index",
    description="为受控 .sde 数据集的精确字段列表添加属性索引并复核；需要企业写门。",
)
def arcgis_pro_add_index(
    dataset_path: str,
    fields: list[str],
    index_name: str,
    unique: str = "NON_UNIQUE",
    ascending: str = "NON_ASCENDING",
) -> str:
    result = schema_maintenance.add_index(
        _arcpy(),
        dataset_path,
        fields,
        index_name,
        unique=unique,
        ascending=ascending,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_remove_index",
    description="删除受控 .sde 数据集的一个精确索引并复核；需要企业写门、破坏性门及索引名回显。",
)
def arcgis_pro_remove_index(
    dataset_path: str,
    index_name: str,
    confirm_index_name: str = "",
) -> str:
    result = schema_maintenance.remove_index(
        _arcpy(),
        dataset_path,
        index_name,
        confirm_index_name=confirm_index_name,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_rebuild_indexes",
    description="重建企业地理数据库指定数据集或系统表索引；数据集名必须相对 .sde workspace。",
)
def arcgis_pro_rebuild_indexes(
    workspace_path: str,
    datasets: list[str],
    include_system: bool = False,
    delta_only: str = "ONLY_DELTAS",
) -> str:
    result = schema_maintenance.rebuild_indexes(
        _arcpy(),
        workspace_path,
        datasets,
        include_system=include_system,
        delta_only=delta_only,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_analyze_datasets",
    description="更新企业地理数据库基表、增量表及归档表统计信息；需要企业写门。",
)
def arcgis_pro_analyze_datasets(
    workspace_path: str,
    datasets: list[str],
    include_system: bool = False,
    analyze_base: bool = True,
    analyze_delta: bool = True,
    analyze_archive: bool = True,
) -> str:
    result = schema_maintenance.analyze_datasets(
        _arcpy(),
        workspace_path,
        datasets,
        include_system=include_system,
        analyze_base=analyze_base,
        analyze_delta=analyze_delta,
        analyze_archive=analyze_archive,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_enable_editor_tracking",
    description="启用受控 .sde 数据集编辑者追踪并复核字段；需要企业写门。",
)
def arcgis_pro_enable_editor_tracking(
    dataset_path: str,
    creator_field: str,
    creation_date_field: str,
    last_editor_field: str,
    last_edit_date_field: str,
    add_fields: bool = False,
    record_dates_in: str = "UTC",
) -> str:
    result = schema_maintenance.enable_editor_tracking(
        _arcpy(),
        dataset_path,
        creator_field,
        creation_date_field,
        last_editor_field,
        last_edit_date_field,
        add_fields=add_fields,
        record_dates_in=record_dates_in,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_disable_editor_tracking",
    description="禁用受控 .sde 数据集选定编辑追踪项但保留字段；需要双门和数据集路径精确回显。",
)
def arcgis_pro_disable_editor_tracking(
    dataset_path: str,
    disable_creator: bool = True,
    disable_creation_date: bool = True,
    disable_last_editor: bool = True,
    disable_last_edit_date: bool = True,
    confirm_dataset_path: str = "",
) -> str:
    result = schema_maintenance.disable_editor_tracking(
        _arcpy(),
        dataset_path,
        disable_creator=disable_creator,
        disable_creation_date=disable_creation_date,
        disable_last_editor=disable_last_editor,
        disable_last_edit_date=disable_last_edit_date,
        confirm_dataset_path=confirm_dataset_path,
    )
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_add_global_ids",
    description="为最多 50 个受控 .sde 数据集补充 GlobalID 并逐一复核；已有 GlobalID 时跳过。",
)
def arcgis_pro_add_global_ids(dataset_paths: list[str]) -> str:
    result = schema_maintenance.add_global_ids(_arcpy(), dataset_paths)
    result["ok"] = True
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_sdk_bridge_status",
    description="发现可选 ArcGIS Pro SDK Add-In 并读取脱敏状态；不会返回 bearer token 或 lease secret。",
)
def arcgis_pro_sdk_bridge_status(process_id: int = 0) -> str:
    return _json_dumps(sdk_bridge.bridge_status(process_id))


@mcp.tool(
    name="arcgis_pro_sdk_acquire_project_lease",
    description="为一个精确已保存 .aprx 获取 SDK 独占短租约，返回不透明的进程内 session 引用。",
)
def arcgis_pro_sdk_acquire_project_lease(
    expected_project_uri: str,
    process_id: int = 0,
    ttl_seconds: int = 45,
) -> str:
    return _json_dumps(
        sdk_bridge.acquire_lease(
            expected_project_uri,
            process_id=process_id,
            ttl_seconds=ttl_seconds,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_renew_project_lease",
    description="续期 SDK 工程租约；工程切换、过期或 Add-In 重启会失败关闭。",
)
def arcgis_pro_sdk_renew_project_lease(sdk_session_ref: str) -> str:
    return _json_dumps(sdk_bridge.renew_lease(sdk_session_ref))


@mcp.tool(
    name="arcgis_pro_sdk_release_project_lease",
    description="释放 SDK 工程租约并销毁 MCP 进程内的不透明会话引用。",
)
def arcgis_pro_sdk_release_project_lease(sdk_session_ref: str) -> str:
    return _json_dumps(sdk_bridge.release_lease(sdk_session_ref))


@mcp.tool(
    name="arcgis_pro_sdk_wait_events",
    description="长轮询 SDK 原生事件：活动视图、相机、选择、DrawComplete、编辑和工程开关。",
)
def arcgis_pro_sdk_wait_events(
    sdk_session_ref: str,
    after: int = 0,
    limit: int = 128,
    wait_ms: int = 30_000,
) -> str:
    return _json_dumps(
        sdk_bridge.wait_events(
            sdk_session_ref,
            after=after,
            limit=limit,
            wait_ms=wait_ms,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_context",
    description="读取 SDK 原生活动视图、相机、活动图层、选择摘要、时间与 CAS generations。",
)
def arcgis_pro_sdk_context(sdk_session_ref: str) -> str:
    return _json_dumps(sdk_bridge.context_snapshot(sdk_session_ref))


@mcp.tool(
    name="arcgis_pro_sdk_set_camera",
    description="用 map/context/WKID compare-and-swap 设置原生 MapView 相机；不猜测或投影坐标。",
)
def arcgis_pro_sdk_set_camera(
    sdk_session_ref: str,
    expected_map_uri: str,
    expected_context_generation: int,
    expected_spatial_reference_wkid: int,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    scale: float | None = None,
    heading: float | None = None,
    pitch: float | None = None,
    roll: float | None = None,
    duration_milliseconds: int = 0,
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.set_camera(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            expected_spatial_reference_wkid,
            x=x,
            y=y,
            z=z,
            scale=scale,
            heading=heading,
            pitch=pitch,
            roll=roll,
            duration_milliseconds=duration_milliseconds,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_zoom_layer",
    description="按精确 layer URI 缩放原生 MapView，可只缩放当前选择并保持观察方向。",
)
def arcgis_pro_sdk_zoom_layer(
    sdk_session_ref: str,
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    selected_only: bool = False,
    duration_milliseconds: int = 0,
    maintain_view_direction: bool = True,
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.zoom_layer(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            layer_uri,
            selected_only=selected_only,
            duration_milliseconds=duration_milliseconds,
            maintain_view_direction=maintain_view_direction,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_refresh_view",
    description="请求原生 MapView redraw，并等待同一地图的后续 DrawComplete 或明确报告超时。",
)
def arcgis_pro_sdk_refresh_view(
    sdk_session_ref: str,
    expected_map_uri: str,
    expected_context_generation: int,
    clear_cache: bool = False,
    wait_milliseconds: int = 10_000,
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.refresh_view(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            clear_cache=clear_cache,
            wait_milliseconds=wait_milliseconds,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_set_active_time",
    description="设置原生活动 MapView 的 offset-aware 时间范围；起止同时留空时禁用时间。",
)
def arcgis_pro_sdk_set_active_time(
    sdk_session_ref: str,
    expected_map_uri: str,
    expected_context_generation: int,
    start_time: str = "",
    end_time: str = "",
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.set_active_time(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            start_time=start_time,
            end_time=end_time,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_open_table",
    description="按预期地图中已加载 standalone table 的精确 URI 打开原生表窗格。",
)
def arcgis_pro_sdk_open_table(
    sdk_session_ref: str,
    expected_map_uri: str,
    expected_context_generation: int,
    table_uri: str,
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.open_table(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            table_uri,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_create_feature",
    description="用 ArcGIS Pro EditOperation 创建一个 2D 要素，形成原生 Undo 项；字段和几何均受限。",
)
def arcgis_pro_sdk_create_feature(
    sdk_session_ref: str,
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    expected_edit_generation: int,
    geometry: dict[str, Any],
    attributes: dict[str, Any] | None = None,
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.create_feature(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            layer_uri,
            expected_edit_generation,
            geometry,
            attributes=attributes,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_modify_selected_features",
    description="用选择 generation/count/OID digest CAS 修改精确选择集，作为一个原生 Undo 编辑。",
)
def arcgis_pro_sdk_modify_selected_features(
    sdk_session_ref: str,
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    expected_edit_generation: int,
    expected_selection_generation: int,
    expected_count: int,
    expected_oid_digest: str,
    attributes: dict[str, Any] | None = None,
    geometry: dict[str, Any] | None = None,
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.modify_selected_features(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            layer_uri,
            expected_edit_generation,
            expected_selection_generation,
            expected_count,
            expected_oid_digest,
            attributes=attributes,
            geometry=geometry,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_delete_selected_features",
    description="删除精确选择集并保留原生 Undo；需要破坏性门禁、四组 CAS 和双重确认。",
)
def arcgis_pro_sdk_delete_selected_features(
    sdk_session_ref: str,
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    expected_edit_generation: int,
    expected_selection_generation: int,
    expected_count: int,
    expected_oid_digest: str,
    confirm: bool = False,
    confirm_delete_selection: bool = False,
) -> str:
    require_allow_destructive()
    return _json_dumps(
        sdk_bridge.delete_selected_features(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            layer_uri,
            expected_edit_generation,
            expected_selection_generation,
            expected_count,
            expected_oid_digest,
            confirm=confirm,
            confirm_delete_selection=confirm_delete_selection,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_gp_job_submit",
    description="向 SDK 提交一个严格白名单、路径契约受控且可取消的异步 GP job。",
)
def arcgis_pro_sdk_gp_job_submit(
    sdk_session_ref: str,
    tool_name: str,
    parameters: list[str],
    environments: dict[str, str] | None = None,
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.start_gp_job(
            sdk_session_ref,
            tool_name,
            parameters,
            environments,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_gp_job_status",
    description="读取 SDK 异步 GP job 的排队、进度、结果或错误状态。",
)
def arcgis_pro_sdk_gp_job_status(sdk_session_ref: str, job_id: str) -> str:
    return _json_dumps(sdk_bridge.gp_job_status(sdk_session_ref, job_id))


@mcp.tool(
    name="arcgis_pro_sdk_gp_job_cancel",
    description="通过 SDK cancellation token 请求取消一个异步 GP job；需要写入开关和 confirm=true。",
)
def arcgis_pro_sdk_gp_job_cancel(
    sdk_session_ref: str,
    job_id: str,
    confirm: bool = False,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.cancel_gp_job(
            sdk_session_ref,
            job_id,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_edit_status",
    description="读取 SDK 原生未保存编辑及活动地图 Undo/Redo 可用状态。",
)
def arcgis_pro_sdk_edit_status(sdk_session_ref: str) -> str:
    return _json_dumps(sdk_bridge.edit_status(sdk_session_ref))


def _sdk_edit_command(
    sdk_session_ref: str,
    command: str,
    expected_edit_generation: int,
    expected_map_uri: str,
    confirm: bool,
) -> str:
    require_allow_write()
    return _json_dumps(
        sdk_bridge.edit_command(
            sdk_session_ref,
            command,
            expected_edit_generation=expected_edit_generation,
            expected_map_uri=expected_map_uri,
            confirm=confirm,
        )
    )


@mcp.tool(
    name="arcgis_pro_sdk_edit_undo",
    description="执行活动地图原生 Undo；需要 SDK 编辑开关、有效租约和 confirm=true。",
)
def arcgis_pro_sdk_edit_undo(
    sdk_session_ref: str,
    expected_edit_generation: int,
    expected_map_uri: str,
    confirm: bool = False,
) -> str:
    return _sdk_edit_command(
        sdk_session_ref,
        "undo",
        expected_edit_generation,
        expected_map_uri,
        confirm,
    )


@mcp.tool(
    name="arcgis_pro_sdk_edit_redo",
    description="执行活动地图原生 Redo；需要 SDK 编辑开关、有效租约和 confirm=true。",
)
def arcgis_pro_sdk_edit_redo(
    sdk_session_ref: str,
    expected_edit_generation: int,
    expected_map_uri: str,
    confirm: bool = False,
) -> str:
    return _sdk_edit_command(
        sdk_session_ref,
        "redo",
        expected_edit_generation,
        expected_map_uri,
        confirm,
    )


@mcp.tool(
    name="arcgis_pro_sdk_edit_save",
    description="保存当前工程中的待提交数据编辑；需要 SDK 编辑开关和 confirm=true。",
)
def arcgis_pro_sdk_edit_save(
    sdk_session_ref: str,
    expected_edit_generation: int,
    confirm: bool = False,
) -> str:
    return _sdk_edit_command(
        sdk_session_ref,
        "save",
        expected_edit_generation,
        "",
        confirm,
    )


@mcp.tool(
    name="arcgis_pro_sdk_edit_discard",
    description="丢弃当前工程中的全部待保存数据编辑；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_sdk_edit_discard(
    sdk_session_ref: str,
    expected_edit_generation: int,
    confirm: bool = False,
    confirm_discard_all: bool = False,
) -> str:
    require_allow_destructive()
    return _json_dumps(
        sdk_bridge.edit_command(
            sdk_session_ref,
            "discard",
            expected_edit_generation=expected_edit_generation,
            confirm=confirm,
            confirm_discard_all=confirm_discard_all,
        )
    )


def _project_item(
    project: Any,
    map_name: str,
    item_kind: str,
    item_identifier: str,
) -> Any:
    map_object = _get_map(project, map_name)
    kind = item_kind.strip().upper()
    if kind == "LAYER":
        return _find_layer(map_object, item_identifier)
    if kind == "TABLE":
        return _get_table(map_object, item_identifier)
    raise RuntimeError("item_kind 须为 LAYER 或 TABLE")


def _project_data_source(
    project: Any,
    map_name: str,
    source_kind: str,
    source_identifier: str,
) -> Any:
    kind = source_kind.strip().upper()
    if kind == "PATH":
        return validate_input_path_optional(source_identifier, "source_identifier")
    return _project_item(project, map_name, kind, source_identifier)


@mcp.tool(
    name="arcgis_pro_import_document",
    description="把受控输入根内的 MXD/3DD/SXD/MAPX/PAGX/RPTX 导入工程，并返回新增项目清单。",
)
def arcgis_pro_import_document(
    aprx_path: str,
    document_path: str,
    include_layout: bool = True,
    reuse_existing_maps: bool = False,
    log_files: bool = False,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_io.import_document(
        project,
        document_path,
        include_layout=include_layout,
        reuse_existing_maps=reuse_existing_maps,
        log_files=log_files,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_export_mapx",
    description="将唯一地图导出为受控 .mapx；已有输出必须使用破坏性门禁并精确确认路径。",
)
def arcgis_pro_export_mapx(
    aprx_path: str,
    map_name: str,
    output_path: str,
    overwrite: bool = False,
    confirm_overwrite_path: str = "",
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_io.export_mapx(
        _get_map(project, map_name),
        output_path,
        overwrite=overwrite,
        confirm_overwrite_path=confirm_overwrite_path,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_save_layer_file",
    description="将唯一图层或独立表保存为受控 .lyrx；已有输出必须使用破坏性门禁并精确确认路径。",
)
def arcgis_pro_save_layer_file(
    aprx_path: str,
    map_name: str,
    item_kind: str,
    item_identifier: str,
    output_path: str,
    overwrite: bool = False,
    confirm_overwrite_path: str = "",
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_io.save_layer_file(
        _project_item(project, map_name, item_kind, item_identifier),
        output_path,
        overwrite=overwrite,
        confirm_overwrite_path=confirm_overwrite_path,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_paste_layer_properties",
    description="按显式语义类别复制图层属性，支持 POPUPS、DISPLAY_FILTERS、SYMBOLOGY 等；禁止 ALL。",
)
def arcgis_pro_paste_layer_properties(
    aprx_path: str,
    target_map_name: str,
    target_layer_identifier: str,
    source_map_name: str,
    source_layer_identifier: str,
    properties: list[str],
) -> str:
    _, project, path = _open_project(aprx_path)
    target = _find_layer(_get_map(project, target_map_name), target_layer_identifier)
    source = _find_layer(_get_map(project, source_map_name), source_layer_identifier)
    result = project_io.paste_layer_properties(target, source, properties)
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_table_replace_data_source",
    description="使用 typed Table.updateConnectionProperties 替换独立表数据源并读回 broken 状态。",
)
def arcgis_pro_table_replace_data_source(
    aprx_path: str,
    map_name: str,
    table_identifier: str,
    current_workspace: str,
    new_workspace: str,
    new_dataset_name: str = "",
    auto_update_joins_and_relates: bool = True,
    validate: bool = True,
    ignore_case: bool = False,
) -> str:
    _, project, path = _open_project(aprx_path)
    table = _get_table(_get_map(project, map_name), table_identifier)
    result = project_io.replace_item_connection(
        table,
        current_workspace,
        new_workspace,
        new_dataset_name=new_dataset_name,
        auto_update_joins_and_relates=auto_update_joins_and_relates,
        validate=validate,
        ignore_case=ignore_case,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_connection_repair_preflight",
    description="对最多 100 个图层/表数据源修复生成绑定当前连接摘要的短期签名预检令牌。",
)
def arcgis_pro_connection_repair_preflight(
    aprx_path: str,
    repairs: list[dict[str, Any]],
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_io.connection_repair_preflight(project, repairs)
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_connection_repair_apply",
    description="仅在修复计划和目标连接未改变时应用批量数据源修复；拒绝陈旧或篡改的预检令牌。",
)
def arcgis_pro_connection_repair_apply(
    aprx_path: str,
    repairs: list[dict[str, Any]],
    repair_token: str,
) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_io.connection_repair_apply(project, repairs, repair_token)
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_add_relate",
    description="向唯一图层或表视图添加临时 relate；永久关系仍使用 CreateRelationshipClass。",
)
def arcgis_pro_add_relate(
    aprx_path: str,
    map_name: str,
    item_kind: str,
    item_identifier: str,
    input_field: str,
    relate_table_path: str,
    relate_field: str,
    relate_name: str,
    cardinality: str = "ONE_TO_MANY",
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    item = _project_item(project, map_name, item_kind, item_identifier)
    relate_table = validate_input_path_optional(relate_table_path, "relate_table_path")
    result = project_io.add_relate(
        arcpy,
        item,
        input_field,
        relate_table,
        relate_field,
        relate_name,
        cardinality,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_remove_relate",
    description="从唯一图层或表视图移除命名 relate；需要破坏性门禁和精确名称确认。",
)
def arcgis_pro_remove_relate(
    aprx_path: str,
    map_name: str,
    item_kind: str,
    item_identifier: str,
    relate_name: str,
    confirm_relate_name: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    result = project_io.remove_relate(
        arcpy,
        _project_item(project, map_name, item_kind, item_identifier),
        relate_name,
        confirm_relate_name=confirm_relate_name,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_list_transformations",
    description="按源/目标 WKID 与可选范围列出 ArcPy 有效坐标转换；首项为 ArcPy 推荐顺序。",
)
def arcgis_pro_list_transformations(
    from_wkid: int,
    to_wkid: int,
    extent: list[float] | None = None,
    vertical: bool = False,
    first_only: bool = False,
) -> str:
    return _json_dumps(
        project_io.list_transformations(
            _arcpy(),
            from_wkid,
            to_wkid,
            extent=extent,
            vertical=vertical,
            first_only=first_only,
        )
    )


@mcp.tool(
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
    _, project, path = _open_project(aprx_path)
    source = _project_data_source(project, map_name, source_kind, source_identifier)
    result = project_io.create_report(
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
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_report_sections",
    description="读取报表的报告节/布局节、字段、统计、过滤器、可见性和脱敏数据源。",
)
def arcgis_pro_report_sections(aprx_path: str, report_name: str) -> str:
    _, project, path = _open_project(aprx_path)
    result = project_io.report_sections(_get_report(project, report_name))
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
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
    _, project, path = _open_project(aprx_path)
    source = None
    if source_kind.strip() or source_identifier.strip():
        if not source_kind.strip() or not source_identifier.strip():
            raise RuntimeError("更新报表数据源时 source_kind/source_identifier 必须同时提供")
        source = _project_data_source(project, map_name, source_kind, source_identifier)
    result = project_io.update_report_section(
        _get_report(project, report_name),
        section_name,
        data_source=source,
        definition_query=definition_query,
        visible=visible,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


def _utility_network_source(
    project: Any,
    map_name: str,
    source_kind: str,
    source_identifier: str,
) -> Any:
    kind = source_kind.strip().upper()
    if kind == "PATH":
        return validate_input_path_optional(source_identifier, "source_identifier")
    if kind == "LAYER":
        return _find_layer(_get_map(project, map_name), source_identifier)
    raise RuntimeError("source_kind 须为 PATH 或 LAYER")


@mcp.tool(
    name="arcgis_pro_locator_info",
    description="读取受控本地 locator 的能力、地址字段、输出字段和空间参考，不访问计费服务。",
)
def arcgis_pro_locator_info(locator_path: str) -> str:
    return _json_dumps(geocoding.locator_info(_arcpy(), locator_path))


@mcp.tool(
    name="arcgis_pro_geocode_addresses",
    description="用受控本地 locator 批量地理编码地址表；typed 字段映射且拒绝远程计费 locator。",
)
def arcgis_pro_geocode_addresses(
    in_table: str,
    locator_path: str,
    address_fields: list[dict[str, str]],
    out_feature_class: str,
    country_codes: list[str] | None = None,
    location_type: str = "ADDRESS_LOCATION",
    categories: list[str] | None = None,
    output_fields: str = "MINIMAL_AND_USER",
) -> str:
    return _json_dumps(
        geocoding.geocode_addresses(
            _arcpy(),
            in_table,
            locator_path,
            address_fields,
            out_feature_class,
            country_codes=country_codes,
            location_type=location_type,
            categories=categories,
            output_fields=output_fields,
        )
    )


@mcp.tool(
    name="arcgis_pro_reverse_geocode",
    description="用受控本地 locator 对点要素反向地理编码；限制 feature/location 类型并核验输出。",
)
def arcgis_pro_reverse_geocode(
    in_features: str,
    locator_path: str,
    out_feature_class: str,
    feature_types: list[str] | None = None,
    location_type: str = "ADDRESS_LOCATION",
) -> str:
    return _json_dumps(
        geocoding.reverse_geocode(
            _arcpy(),
            in_features,
            locator_path,
            out_feature_class,
            feature_types=feature_types,
            location_type=location_type,
        )
    )


@mcp.tool(
    name="arcgis_pro_utility_network_info",
    description="读取 Utility Network 版本、拓扑状态、domain networks 和 tiers。",
)
def arcgis_pro_utility_network_info(
    aprx_path: str,
    map_name: str,
    source_kind: str,
    source_identifier: str,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    result = utility_network.describe_utility_network(
        arcpy,
        _utility_network_source(project, map_name, source_kind, source_identifier),
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_validate_utility_network_topology",
    description="验证 Utility Network dirty areas；需要企业写门和目标身份精确回显。",
)
def arcgis_pro_validate_utility_network_topology(
    aprx_path: str,
    map_name: str,
    source_kind: str,
    source_identifier: str,
    expected_network: str,
    extent: list[float] | None = None,
    extent_keyword: str = "MAXOF",
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    result = utility_network.validate_network_topology(
        arcpy,
        _utility_network_source(project, map_name, source_kind, source_identifier),
        expected_network=expected_network,
        extent=extent,
        extent_keyword=extent_keyword,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_utility_network_trace",
    description="仅使用已有命名 Trace Configuration 执行受约束 Utility Network trace。",
)
def arcgis_pro_utility_network_trace(
    aprx_path: str,
    map_name: str,
    source_kind: str,
    source_identifier: str,
    trace_type: str,
    trace_config_name: str,
    starting_points: str = "",
    barriers: str = "",
    selection_type: str = "NEW_SELECTION",
    clear_previous_results: bool = True,
    trace_name: str = "",
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    result = utility_network.trace_named_configuration(
        arcpy,
        _utility_network_source(project, map_name, source_kind, source_identifier),
        trace_type,
        trace_config_name,
        starting_points=starting_points,
        barriers=barriers,
        selection_type=selection_type,
        clear_previous_results=clear_previous_results,
        trace_name=trace_name,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_update_subnetwork",
    description="更新单个或整个 tier 的 subnetwork；整 tier 操作需要破坏性门禁和固定确认短语。",
)
def arcgis_pro_update_subnetwork(
    aprx_path: str,
    map_name: str,
    source_kind: str,
    source_identifier: str,
    domain_network: str,
    tier: str,
    expected_network: str,
    subnetwork_name: str = "",
    all_subnetworks: bool = False,
    continue_on_failure: bool = False,
    confirm_all: str = "",
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    result = utility_network.update_subnetwork(
        arcpy,
        _utility_network_source(project, map_name, source_kind, source_identifier),
        domain_network,
        tier,
        subnetwork_name=subnetwork_name,
        all_subnetworks=all_subnetworks,
        continue_on_failure=continue_on_failure,
        expected_network=expected_network,
        confirm_all=confirm_all,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_export_subnetwork",
    description="只读导出指定 subnetwork JSON，固定 NO_ACKNOWLEDGE，拒绝覆盖已有文件。",
)
def arcgis_pro_export_subnetwork(
    aprx_path: str,
    map_name: str,
    source_kind: str,
    source_identifier: str,
    domain_network: str,
    tier: str,
    subnetwork_name: str,
    output_json: str,
    include_geometry: bool = False,
    include_domain_descriptions: bool = False,
) -> str:
    arcpy, project, path = _open_project(aprx_path)
    result = utility_network.export_subnetwork(
        arcpy,
        _utility_network_source(project, map_name, source_kind, source_identifier),
        domain_network,
        tier,
        subnetwork_name,
        output_json,
        include_geometry=include_geometry,
        include_domain_descriptions=include_domain_descriptions,
    )
    result.update({"ok": True, "aprx_path": path})
    return _json_dumps(result)


@mcp.tool(
    name="arcgis_pro_las_dataset_info",
    description="读取受控 .lasd 的文件数、点数、统计、金字塔、范围和空间参考。",
)
def arcgis_pro_las_dataset_info(las_dataset: str) -> str:
    return _json_dumps(lidar.las_dataset_info(_arcpy(), las_dataset))


@mcp.tool(
    name="arcgis_pro_create_las_dataset",
    description="从受控 LAS/LAZ/ZLAS 文件或目录创建新 .lasd；源旁路统计/PRJ 写入必须显式请求。",
)
def arcgis_pro_create_las_dataset(
    input_paths: list[str],
    out_las_dataset: str,
    recurse_folders: bool = False,
    surface_constraints: list[dict[str, Any]] | None = None,
    spatial_reference_wkid: int | None = None,
    compute_statistics: bool = False,
    relative_paths: bool = False,
    create_las_prj: str = "NO_FILES",
    processing_extent: list[float] | None = None,
    boundary: str = "",
    contained_files_only: bool = False,
    confirm_all_las_prj: str = "",
) -> str:
    return _json_dumps(
        lidar.create_las_dataset(
            _arcpy(),
            input_paths,
            out_las_dataset,
            recurse_folders=recurse_folders,
            surface_constraints=surface_constraints,
            spatial_reference_wkid=spatial_reference_wkid,
            compute_statistics=compute_statistics,
            relative_paths=relative_paths,
            create_las_prj=create_las_prj,
            processing_extent=processing_extent,
            boundary=boundary,
            contained_files_only=contained_files_only,
            confirm_all_las_prj=confirm_all_las_prj,
        )
    )


@mcp.tool(
    name="arcgis_pro_calculate_las_statistics",
    description="计算 LAS 统计并可导出报告；强制重算需要破坏性门禁、目标回显和固定确认短语。",
)
def arcgis_pro_calculate_las_statistics(
    las_dataset: str,
    expected_las_dataset: str,
    calculation_type: str = "SKIP_EXISTING_STATS",
    out_report: str = "",
    summary_level: str = "DATASET",
    delimiter: str = "SPACE",
    decimal_separator: str = "DECIMAL_POINT",
    confirm_overwrite: str = "",
) -> str:
    return _json_dumps(
        lidar.calculate_las_statistics(
            _arcpy(),
            las_dataset,
            expected_las_dataset=expected_las_dataset,
            calculation_type=calculation_type,
            out_report=out_report,
            summary_level=summary_level,
            delimiter=delimiter,
            decimal_separator=decimal_separator,
            confirm_overwrite=confirm_overwrite,
        )
    )


@mcp.tool(
    name="arcgis_pro_build_las_pyramid",
    description="为精确确认的 .lasd 构建或更新显示金字塔，可使用受约束 class-code 权重。",
)
def arcgis_pro_build_las_pyramid(
    las_dataset: str,
    expected_las_dataset: str,
    point_selection_method: str = "Z_MIN",
    class_code_weights: list[dict[str, int]] | None = None,
) -> str:
    return _json_dumps(
        lidar.build_las_pyramid(
            _arcpy(),
            las_dataset,
            expected_las_dataset=expected_las_dataset,
            point_selection_method=point_selection_method,
            class_code_weights=class_code_weights,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_calculate_statistics",
    description="在原位计算栅格统计，限制 skip factor、忽略值、AOI 和局部环境。",
)
def arcgis_pro_raster_calculate_statistics(
    in_raster_dataset: str,
    x_skip_factor: int = 1,
    y_skip_factor: int = 1,
    ignore_values: list[int] | None = None,
    skip_existing: str = "OVERWRITE",
    area_of_interest: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.calculate_statistics(
            _arcpy(),
            in_raster_dataset,
            x_skip_factor,
            y_skip_factor,
            ignore_values,
            skip_existing,
            area_of_interest,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_build_pyramids",
    description="构建或更新栅格金字塔；删除金字塔需破坏性门禁和源路径精确回显。",
)
def arcgis_pro_raster_build_pyramids(
    in_raster_dataset: str,
    pyramid_level: int = -1,
    skip_first: bool = False,
    resample_technique: str = "NEAREST",
    compression_type: str = "DEFAULT",
    compression_quality: int = 75,
    skip_existing: str = "OVERWRITE",
    confirm_delete_pyramids: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.build_pyramids(
            _arcpy(),
            in_raster_dataset,
            pyramid_level,
            skip_first,
            resample_technique,
            compression_type,
            compression_quality,
            skip_existing,
            confirm_delete_pyramids,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_set_nodata",
    description="按波段设置 NoData；仅作用于精确回显的受控栅格。",
)
def arcgis_pro_raster_set_nodata(
    in_raster: str,
    nodata_values: list[list[Any]],
    confirm_raster_path: str,
    data_type: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.set_raster_nodata(
            _arcpy(),
            in_raster,
            nodata_values,
            confirm_raster_path,
            data_type,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_copy",
    description="用固定签名 CopyRaster 复制到 GP 输出根，拒绝隐式覆盖并核验输出。",
)
def arcgis_pro_raster_copy(
    in_raster: str,
    out_rasterdataset: str,
    background_value: float | None = None,
    nodata_value: str | float | int | None = None,
    onebit_to_eightbit: bool = False,
    colormap_to_rgb: bool = False,
    pixel_type: str = "",
    scale_pixel_value: bool = False,
    rgb_to_colormap: bool = False,
    output_format: str = "",
    apply_transform: bool = False,
    process_as_multidimensional: bool = False,
    build_multidimensional_transpose: bool = False,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.copy_raster(
            _arcpy(),
            in_raster,
            out_rasterdataset,
            background_value,
            nodata_value,
            onebit_to_eightbit,
            colormap_to_rgb,
            pixel_type,
            scale_pixel_value,
            rgb_to_colormap,
            output_format,
            apply_transform,
            process_as_multidimensional,
            build_multidimensional_transpose,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_focal_statistics",
    description="以受约束邻域和统计类型运行 Focal Statistics，并核验新输出。",
)
def arcgis_pro_raster_focal_statistics(
    in_raster: str,
    out_raster: str,
    neighborhood: dict[str, Any] | None = None,
    statistics_type: str = "MEAN",
    ignore_nodata: str = "DATA",
    percentile_value: float = 90,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.focal_statistics(
            _arcpy(),
            in_raster,
            out_raster,
            neighborhood,
            statistics_type,
            ignore_nodata,
            percentile_value,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_cell_statistics",
    description="对受控栅格或常量列表运行 Cell Statistics，限制统计、多波段和百分位参数。",
)
def arcgis_pro_raster_cell_statistics(
    in_rasters_or_constants: list[Any],
    out_raster: str,
    statistics_type: str = "MEAN",
    ignore_nodata: str = "DATA",
    process_as_multiband: str = "SINGLE_BAND",
    percentile_value: float = 90,
    percentile_interpolation_type: str = "AUTO_DETECT",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.cell_statistics(
            _arcpy(),
            in_rasters_or_constants,
            out_raster,
            statistics_type,
            ignore_nodata,
            process_as_multiband,
            percentile_value,
            percentile_interpolation_type,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_con",
    description="以受控栅格/常量和可选 SQL 条件执行 Con 条件计算。",
)
def arcgis_pro_raster_con(
    in_conditional_raster: str,
    in_true_raster_or_constant: Any,
    out_raster: str,
    in_false_raster_or_constant: Any = None,
    where_clause: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.conditional_con(
            _arcpy(),
            in_conditional_raster,
            in_true_raster_or_constant,
            out_raster,
            in_false_raster_or_constant,
            where_clause,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_set_null",
    description="以受控条件栅格、false 值和可选 SQL 条件执行 SetNull。",
)
def arcgis_pro_raster_set_null(
    in_conditional_raster: str,
    in_false_raster_or_constant: Any,
    out_raster: str,
    where_clause: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.set_null(
            _arcpy(),
            in_conditional_raster,
            in_false_raster_or_constant,
            out_raster,
            where_clause,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_euclidean_distance",
    description="计算欧氏距离及可选方向/反向方向栅格，逐项核验全部输出。",
)
def arcgis_pro_raster_euclidean_distance(
    in_source_data: str,
    out_distance_raster: str,
    maximum_distance: float | None = None,
    cell_size: float | str | None = None,
    out_direction_raster: str = "",
    distance_method: str = "PLANAR",
    in_barrier_data: str = "",
    out_back_direction_raster: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.euclidean_distance(
            _arcpy(),
            in_source_data,
            out_distance_raster,
            maximum_distance,
            cell_size,
            out_direction_raster,
            distance_method,
            in_barrier_data,
            out_back_direction_raster,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_distance_accumulation",
    description="计算距离累积及可选 back/source direction/location 派生栅格并核验。",
)
def arcgis_pro_raster_distance_accumulation(
    in_source_data: str,
    out_distance_accumulation_raster: str,
    in_barrier_data: str = "",
    in_surface_raster: str = "",
    in_cost_raster: str = "",
    in_vertical_raster: str = "",
    in_horizontal_raster: str = "",
    out_back_direction_raster: str = "",
    out_source_direction_raster: str = "",
    out_source_location_raster: str = "",
    source_initial_accumulation: float | str | None = None,
    source_maximum_accumulation: float | str | None = None,
    source_cost_multiplier: float | str | None = None,
    source_direction: str = "FROM_SOURCE",
    distance_method: str = "PLANAR",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.distance_accumulation(
            _arcpy(),
            in_source_data,
            out_distance_accumulation_raster,
            in_barrier_data,
            in_surface_raster,
            in_cost_raster,
            in_vertical_raster,
            in_horizontal_raster,
            out_back_direction_raster,
            out_source_direction_raster,
            out_source_location_raster,
            source_initial_accumulation,
            source_maximum_accumulation,
            source_cost_multiplier,
            source_direction,
            distance_method,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_optimal_path_as_line",
    description="从距离累积和反向方向栅格生成最优路径线并核验输出。",
)
def arcgis_pro_raster_optimal_path_as_line(
    in_destination_data: str,
    in_distance_accumulation_raster: str,
    in_back_direction_raster: str,
    out_polyline_features: str,
    destination_field: str = "",
    path_type: str = "EACH_ZONE",
    create_network_paths: str = "DESTINATIONS_TO_SOURCES",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.optimal_path_as_line(
            _arcpy(),
            in_destination_data,
            in_distance_accumulation_raster,
            in_back_direction_raster,
            out_polyline_features,
            destination_field,
            path_type,
            create_network_paths,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_stream_order",
    description="用 STRAHLER/SHREVE 方法计算河网等级栅格。",
)
def arcgis_pro_raster_stream_order(
    in_stream_raster: str,
    in_flow_direction_raster: str,
    out_raster: str,
    order_method: str = "STRAHLER",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.stream_order(
            _arcpy(),
            in_stream_raster,
            in_flow_direction_raster,
            out_raster,
            order_method,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_stream_to_feature",
    description="把河网栅格转换为新折线要素类并核验。",
)
def arcgis_pro_raster_stream_to_feature(
    in_stream_raster: str,
    in_flow_direction_raster: str,
    out_polyline_features: str,
    simplify: bool = True,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.stream_to_feature(
            _arcpy(),
            in_stream_raster,
            in_flow_direction_raster,
            out_polyline_features,
            simplify,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_raster_basin",
    description="从 D8 流向栅格划分流域并核验新输出。",
)
def arcgis_pro_raster_basin(
    in_flow_direction_raster: str,
    out_raster: str,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.basin(
            _arcpy(), in_flow_direction_raster, out_raster, environment
        )
    )


@mcp.tool(
    name="arcgis_pro_create_mosaic_dataset",
    description="在 GP 输出根内的新地理数据库工作空间创建 mosaic dataset，并核验派生输出。",
)
def arcgis_pro_create_mosaic_dataset(
    in_workspace: str,
    in_mosaicdataset_name: str,
    coordinate_system: int | str,
    num_bands: int | None = None,
    pixel_type: str = "",
    product_definition: str = "NONE",
    product_band_definitions: list[list[Any]] | None = None,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.create_mosaic_dataset(
            _arcpy(),
            in_workspace,
            in_mosaicdataset_name,
            coordinate_system,
            num_bands,
            pixel_type,
            product_definition,
            product_band_definitions,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_add_rasters_to_mosaic_dataset",
    description="把受控路径的栅格加入 mosaic dataset，并限制重复项、金字塔、统计和缩略图选项。",
)
def arcgis_pro_add_rasters_to_mosaic_dataset(
    in_mosaic_dataset: str,
    input_paths: list[str],
    raster_type: str = "Raster Dataset",
    update_cellsize_ranges: bool = True,
    update_boundary: bool = True,
    update_overviews: bool = False,
    include_subfolders: bool = False,
    duplicate_items_action: str = "EXCLUDE_DUPLICATES",
    build_pyramids_for_sources: bool = False,
    calculate_statistics_for_sources: bool = False,
    build_thumbnails: bool = False,
    filter_expression: str = "",
    operation_description: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.add_rasters_to_mosaic_dataset(
            _arcpy(),
            in_mosaic_dataset,
            input_paths,
            raster_type,
            update_cellsize_ranges,
            update_boundary,
            update_overviews,
            include_subfolders,
            duplicate_items_action,
            build_pyramids_for_sources,
            calculate_statistics_for_sources,
            build_thumbnails,
            filter_expression,
            operation_description,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_build_mosaic_footprints",
    description="以固定参数更新 mosaic dataset footprints，并读回核验目标。",
)
def arcgis_pro_build_mosaic_footprints(
    in_mosaic_dataset: str,
    where_clause: str = "",
    reset_footprint: str = "RADIOMETRY",
    min_data_value: float | None = None,
    max_data_value: float | None = None,
    approx_num_vertices: int | None = None,
    shrink_distance: float | None = None,
    maintain_edges: bool = False,
    skip_derived_images: bool = True,
    update_boundary: bool = True,
    request_size: int | None = None,
    min_region_size: int | None = None,
    simplification_method: str = "NONE",
    edge_tolerance: float | None = None,
    max_sliver_size: int | None = None,
    min_thinness_ratio: float | None = None,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.build_mosaic_footprints(
            _arcpy(),
            in_mosaic_dataset,
            where_clause,
            reset_footprint,
            min_data_value,
            max_data_value,
            approx_num_vertices,
            shrink_distance,
            maintain_edges,
            skip_derived_images,
            update_boundary,
            request_size,
            min_region_size,
            simplification_method,
            edge_tolerance,
            max_sliver_size,
            min_thinness_ratio,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_build_mosaic_overviews",
    description="定义并生成缺失或过期 mosaic dataset overviews，限制为受控目标。",
)
def arcgis_pro_build_mosaic_overviews(
    in_mosaic_dataset: str,
    where_clause: str = "",
    define_missing_tiles: bool = True,
    generate_overviews: bool = True,
    generate_missing_images: bool = True,
    regenerate_stale_images: bool = True,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.build_mosaic_overviews(
            _arcpy(),
            in_mosaic_dataset,
            where_clause,
            define_missing_tiles,
            generate_overviews,
            generate_missing_images,
            regenerate_stale_images,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_remove_rasters_from_mosaic_dataset",
    description="删除精确 where 命中的 mosaic items；需破坏性门禁、目标/条件回显和预期数量。",
)
def arcgis_pro_remove_rasters_from_mosaic_dataset(
    in_mosaic_dataset: str,
    where_clause: str,
    expected_item_count: int,
    confirm_mosaic_dataset: str,
    confirm_where_clause: str,
    update_boundary: bool = True,
    mark_overview_items: bool = True,
    delete_overview_images: bool = True,
    delete_item_cache: bool = True,
    update_cellsize_ranges: bool = True,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        raster_advanced.remove_rasters_from_mosaic_dataset(
            _arcpy(),
            in_mosaic_dataset,
            where_clause,
            expected_item_count,
            confirm_mosaic_dataset,
            confirm_where_clause,
            update_boundary,
            mark_overview_items,
            delete_overview_images,
            delete_item_cache,
            update_cellsize_ranges,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_calculate_distance_band",
    description="计算使每个要素至少具有指定邻居数的距离阈值，不创建数据集。",
)
def arcgis_pro_calculate_distance_band(
    in_features: str,
    number_of_neighbors: int,
    distance_method: str = "EUCLIDEAN_DISTANCE",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.calculate_distance_band(
            _arcpy(),
            in_features,
            number_of_neighbors,
            distance_method,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_generate_spatial_weights_matrix",
    description="以固定 conceptualization 和参数白名单生成新 .swm 空间权重矩阵。",
)
def arcgis_pro_generate_spatial_weights_matrix(
    in_features: str,
    unique_id_field: str,
    output_swm: str,
    conceptualization: str,
    distance_method: str = "EUCLIDEAN",
    exponent: float | None = None,
    threshold_distance: float | None = None,
    number_of_neighbors: int | None = None,
    row_standardization: str = "ROW_STANDARDIZATION",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.generate_spatial_weights_matrix(
            _arcpy(),
            in_features,
            unique_id_field,
            output_swm,
            conceptualization,
            distance_method,
            exponent,
            threshold_distance,
            number_of_neighbors,
            row_standardization,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_find_point_clusters",
    description="以 DBSCAN/HDBSCAN 聚类点；显式核验 Advanced 许可、距离和时间参数。",
)
def arcgis_pro_find_point_clusters(
    input_points: str,
    output_features: str,
    clustering_method: str,
    minimum_points: int,
    search_distance: str,
    use_time: bool = False,
    search_duration: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.find_point_clusters(
            _arcpy(),
            input_points,
            output_features,
            clustering_method,
            minimum_points,
            search_distance,
            use_time,
            search_duration,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_multivariate_clustering",
    description="运行多变量聚类，限制分析字段、初始化方式、簇数量和可选评估表。",
)
def arcgis_pro_multivariate_clustering(
    in_features: str,
    output_features: str,
    analysis_fields: list[str],
    clustering_method: str = "K_MEANS",
    initialization_method: str = "OPTIMIZED_SEED_LOCATIONS",
    initialization_field: str = "",
    number_of_clusters: int | None = None,
    output_table: str = "",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.multivariate_clustering(
            _arcpy(),
            in_features,
            output_features,
            analysis_fields,
            clustering_method,
            initialization_method,
            initialization_field,
            number_of_clusters,
            output_table,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_spatially_constrained_multivariate_clustering",
    description="运行带空间与规模约束的多变量聚类，并核验要素和评估表输出。",
)
def arcgis_pro_spatially_constrained_multivariate_clustering(
    in_features: str,
    output_features: str,
    output_table: str,
    analysis_fields: list[str],
    size_constraints: str = "NONE",
    constraint_field: str = "",
    min_constraint: float | None = None,
    max_constraint: float | None = None,
    number_of_clusters: int | None = None,
    spatial_constraints: str = "CONTIGUITY_EDGES_CORNERS",
    weights_matrix_file: str = "",
    number_of_permutations: int = 0,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.spatially_constrained_multivariate_clustering(
            _arcpy(),
            in_features,
            output_features,
            output_table,
            analysis_fields,
            size_constraints,
            constraint_field,
            min_constraint,
            max_constraint,
            number_of_clusters,
            spatial_constraints,
            weights_matrix_file,
            number_of_permutations,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_generalized_linear_regression",
    description="用固定 model type 和显式解释变量运行广义线性回归。",
)
def arcgis_pro_generalized_linear_regression(
    in_features: str,
    dependent_variable: str,
    model_type: str,
    output_features: str,
    explanatory_variables: list[str],
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.generalized_linear_regression(
            _arcpy(),
            in_features,
            dependent_variable,
            model_type,
            output_features,
            explanatory_variables,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_create_space_time_cube",
    description="从时空点创建新 .nc cube，限制时间/距离单位、对齐方式与 summary field 表。",
)
def arcgis_pro_create_space_time_cube(
    in_features: str,
    output_cube: str,
    time_field: str,
    time_step_interval: str,
    distance_interval: str,
    template_cube: str = "",
    time_step_alignment: str = "END_TIME",
    reference_time: str = "",
    summary_fields: list[dict[str, str]] | None = None,
    aggregation_shape_type: str = "HEXAGON_GRID",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.create_space_time_cube(
            _arcpy(),
            in_features,
            output_cube,
            time_field,
            time_step_interval,
            distance_interval,
            template_cube,
            time_step_alignment,
            reference_time,
            summary_fields,
            aggregation_shape_type,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_emerging_hot_spot_analysis",
    description="对受控时空 cube 执行新兴热点分析，校验邻域距离、时间窗口和 mask。",
)
def arcgis_pro_emerging_hot_spot_analysis(
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    neighborhood_distance: str = "",
    neighborhood_time_step: int | None = None,
    polygon_mask: str = "",
    conceptualization: str = "FIXED_DISTANCE",
    number_of_neighbors: int | None = None,
    define_global_window: str = "ENTIRE_CUBE",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.emerging_hot_spot_analysis(
            _arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            neighborhood_distance,
            neighborhood_time_step,
            polygon_mask,
            conceptualization,
            number_of_neighbors,
            define_global_window,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_time_series_clustering",
    description="聚类时空 cube 的时间序列，并可生成图表表及受控 popup。",
)
def arcgis_pro_time_series_clustering(
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    characteristic_of_interest: str,
    cluster_count: int | None = None,
    output_table_for_charts: str = "",
    shape_characteristics_to_ignore: list[str] | None = None,
    enable_time_series_popups: bool = False,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.time_series_clustering(
            _arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            characteristic_of_interest,
            cluster_count,
            output_table_for_charts,
            shape_characteristics_to_ignore,
            enable_time_series_popups,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_curve_fit_forecast",
    description="对时空 cube 运行 Curve Fit Forecast，限制曲线、验证、异常值和置信度参数。",
)
def arcgis_pro_curve_fit_forecast(
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    output_cube: str,
    number_of_time_steps_to_forecast: int,
    curve_type: str = "AUTO_DETECT",
    number_for_validation: int = 0,
    outlier_option: str = "NONE",
    level_of_confidence: str = "90%",
    maximum_number_of_outliers: int = 1,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.curve_fit_forecast(
            _arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            output_cube,
            number_of_time_steps_to_forecast,
            curve_type,
            number_for_validation,
            outlier_option,
            level_of_confidence,
            maximum_number_of_outliers,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_exponential_smoothing_forecast",
    description="对时空 cube 运行 Exponential Smoothing Forecast 并核验要素与 cube 输出。",
)
def arcgis_pro_exponential_smoothing_forecast(
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    output_cube: str,
    number_of_time_steps_to_forecast: int,
    season_length: int,
    number_for_validation: int = 0,
    outlier_option: str = "NONE",
    level_of_confidence: str = "90%",
    maximum_number_of_outliers: int = 1,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.exponential_smoothing_forecast(
            _arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            output_cube,
            number_of_time_steps_to_forecast,
            season_length,
            number_for_validation,
            outlier_option,
            level_of_confidence,
            maximum_number_of_outliers,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_forest_based_forecast",
    description="对时空 cube 运行 Forest-based Forecast，白名单化树、窗口、深度和抽样参数。",
)
def arcgis_pro_forest_based_forecast(
    in_cube: str,
    analysis_variable: str,
    output_features: str,
    output_cube: str,
    number_of_time_steps_to_forecast: int,
    time_window: int | None = None,
    number_for_validation: int = 0,
    number_of_trees: int = 100,
    minimum_leaf_size: int | None = None,
    maximum_depth: int | None = None,
    sample_size: int = 100,
    forecast_approach: str = "VALUE",
    outlier_option: str = "NONE",
    level_of_confidence: str = "90%",
    maximum_number_of_outliers: int = 1,
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.forest_based_forecast(
            _arcpy(),
            in_cube,
            analysis_variable,
            output_features,
            output_cube,
            number_of_time_steps_to_forecast,
            time_window,
            number_for_validation,
            number_of_trees,
            minimum_leaf_size,
            maximum_depth,
            sample_size,
            forecast_approach,
            outlier_option,
            level_of_confidence,
            maximum_number_of_outliers,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_evaluate_forecasts_by_location",
    description="按位置比较多个预测 cube，使用 ArcPy 真实 EvaluateForecastsByLocation API。",
)
def arcgis_pro_evaluate_forecasts_by_location(
    in_cubes: list[str],
    output_features: str,
    output_cube: str = "",
    evaluate_using_validation_results: str = "USE_VALIDATION",
    environment: dict[str, Any] | None = None,
) -> str:
    return _json_dumps(
        spatial_modeling.evaluate_forecasts_by_location(
            _arcpy(),
            in_cubes,
            output_features,
            output_cube,
            evaluate_using_validation_results,
            environment,
        )
    )


@mcp.tool(
    name="arcgis_pro_list_attribute_rules",
    description="读取数据集的属性规则清单、类型、触发器、子类型和校验属性，不修改方案。",
)
def arcgis_pro_list_attribute_rules(dataset_path: str) -> str:
    return _json_dumps(data_integrity.list_attribute_rules(_arcpy(), dataset_path))


@mcp.tool(
    name="arcgis_pro_export_attribute_rules",
    description="将属性规则导出为 EXPORT_ROOT 内的新 CSV；拒绝覆盖已有文件。",
)
def arcgis_pro_export_attribute_rules(dataset_path: str, output_csv: str) -> str:
    return _json_dumps(
        data_integrity.export_attribute_rules(_arcpy(), dataset_path, output_csv)
    )


@mcp.tool(
    name="arcgis_pro_import_attribute_rules",
    description=(
        "从 INPUT_ROOTS 内的官方 CSV 导入属性规则，并核验预期名称集合；"
        "需要方案变更双门、目标精确回显和固定确认短语。"
    ),
)
def arcgis_pro_import_attribute_rules(
    dataset_path: str,
    csv_files: list[str],
    expected_rule_names: list[str],
    expected_dataset: str,
    confirmation: str,
) -> str:
    return _json_dumps(
        data_integrity.import_attribute_rules(
            _arcpy(),
            dataset_path,
            csv_files,
            expected_rule_names,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
        )
    )


@mcp.tool(
    name="arcgis_pro_add_attribute_rule",
    description=(
        "用官方 AddAttributeRule 创建受约束属性规则并后验核验；"
        "需要方案变更双门、目标精确回显和固定确认短语。"
    ),
)
def arcgis_pro_add_attribute_rule(
    dataset_path: str,
    rule_name: str,
    rule_type: str,
    script_expression: str,
    expected_dataset: str,
    confirmation: str,
    is_editable: bool = True,
    triggering_events: list[str] | None = None,
    error_number: int | None = None,
    error_message: str = "",
    description: str = "",
    subtypes: list[str] | None = None,
    field: str = "",
    exclude_from_client_evaluation: bool = False,
    batch: bool = False,
    severity: int | None = None,
    tags: list[str] | None = None,
    triggering_fields: list[str] | None = None,
) -> str:
    return _json_dumps(
        data_integrity.add_attribute_rule(
            _arcpy(),
            dataset_path,
            rule_name,
            rule_type,
            script_expression,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
            is_editable=is_editable,
            triggering_events=triggering_events,
            error_number=error_number,
            error_message=error_message,
            description=description,
            subtypes=subtypes,
            field=field,
            exclude_from_client_evaluation=exclude_from_client_evaluation,
            batch=batch,
            severity=severity,
            tags=tags,
            triggering_fields=triggering_fields,
        )
    )


@mcp.tool(
    name="arcgis_pro_delete_attribute_rules",
    description=(
        "删除精确规则名集合并核验前后差分；需要方案变更双门、目标精确回显和固定确认短语。"
    ),
)
def arcgis_pro_delete_attribute_rules(
    dataset_path: str,
    rule_names: list[str],
    expected_dataset: str,
    confirmation: str,
    rule_type: str = "",
) -> str:
    return _json_dumps(
        data_integrity.delete_attribute_rules(
            _arcpy(),
            dataset_path,
            rule_names,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
            rule_type=rule_type,
        )
    )


@mcp.tool(
    name="arcgis_pro_list_field_groups",
    description="读取条件值字段组、字段顺序和 restrictive 状态，不修改方案。",
)
def arcgis_pro_list_field_groups(dataset_path: str) -> str:
    return _json_dumps(data_integrity.list_field_groups(_arcpy(), dataset_path))


@mcp.tool(
    name="arcgis_pro_create_field_group",
    description=(
        "创建条件值字段组并读回核验；需要方案变更双门、目标精确回显和固定确认短语。"
    ),
)
def arcgis_pro_create_field_group(
    dataset_path: str,
    field_group_name: str,
    fields: list[str],
    expected_dataset: str,
    confirmation: str,
    restrictive: bool = True,
) -> str:
    return _json_dumps(
        data_integrity.create_field_group(
            _arcpy(),
            dataset_path,
            field_group_name,
            fields,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
            restrictive=restrictive,
        )
    )


@mcp.tool(
    name="arcgis_pro_delete_field_group",
    description=(
        "删除精确字段组并核验不存在；需要方案变更双门、目标精确回显和固定确认短语。"
    ),
)
def arcgis_pro_delete_field_group(
    dataset_path: str,
    field_group_name: str,
    expected_dataset: str,
    confirmation: str,
) -> str:
    return _json_dumps(
        data_integrity.delete_field_group(
            _arcpy(),
            dataset_path,
            field_group_name,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
        )
    )


@mcp.tool(
    name="arcgis_pro_list_contingent_values",
    description="读取条件值清单，可按精确字段组和 subtype code 筛选，不修改方案。",
)
def arcgis_pro_list_contingent_values(
    dataset_path: str,
    field_group_name: str = "",
    subtype_code: int | None = None,
) -> str:
    return _json_dumps(
        data_integrity.list_contingent_values(
            _arcpy(),
            dataset_path,
            field_group_name,
            subtype_code,
        )
    )


@mcp.tool(
    name="arcgis_pro_add_contingent_value",
    description=(
        "添加 ANY/NULL/CODED_VALUE/RANGE 条件值并核验唯一新增项；"
        "需要方案变更双门、目标精确回显和固定确认短语。"
    ),
)
def arcgis_pro_add_contingent_value(
    dataset_path: str,
    field_group_name: str,
    values: list[dict[str, Any]],
    expected_dataset: str,
    confirmation: str,
    subtype: str = "",
    retired: bool = False,
) -> str:
    return _json_dumps(
        data_integrity.add_contingent_value(
            _arcpy(),
            dataset_path,
            field_group_name,
            values,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
            subtype=subtype,
            retired=retired,
        )
    )


@mcp.tool(
    name="arcgis_pro_remove_contingent_value",
    description=(
        "按精确 CAV id 移除条件值并核验不存在；需要方案变更双门、目标精确回显和固定确认短语。"
    ),
)
def arcgis_pro_remove_contingent_value(
    dataset_path: str,
    contingent_value_id: int,
    expected_dataset: str,
    confirmation: str,
) -> str:
    return _json_dumps(
        data_integrity.remove_contingent_value(
            _arcpy(),
            dataset_path,
            contingent_value_id,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
        )
    )


@mcp.tool(
    name="arcgis_pro_export_contingent_values",
    description="将字段组和条件值导出为 EXPORT_ROOT 内两个新 CSV；任一已存在即拒绝执行。",
)
def arcgis_pro_export_contingent_values(
    dataset_path: str,
    field_groups_csv: str,
    contingent_values_csv: str,
) -> str:
    return _json_dumps(
        data_integrity.export_contingent_values(
            _arcpy(),
            dataset_path,
            field_groups_csv,
            contingent_values_csv,
        )
    )


@mcp.tool(
    name="arcgis_pro_import_contingent_values",
    description=(
        "从 INPUT_ROOTS 内的官方 CSV 以 UNION/REPLACE 导入字段组和条件值，并严格核验清单；"
        "需要方案变更双门、目标精确回显和固定确认短语。"
    ),
)
def arcgis_pro_import_contingent_values(
    dataset_path: str,
    field_groups_csv: str,
    contingent_values_csv: str,
    expected_dataset: str,
    confirmation: str,
    import_type: str = "UNION",
) -> str:
    return _json_dumps(
        data_integrity.import_contingent_values(
            _arcpy(),
            dataset_path,
            field_groups_csv,
            contingent_values_csv,
            import_type,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
        )
    )


@mcp.tool(
    name="arcgis_pro_tool_info",
    description="读取一个工具或完整工具目录的描述、输入输出 schema、执行模式和安全门，不调用 ArcPy。",
)
def arcgis_pro_tool_info(name: str = "") -> str:
    return _json_dumps(tool_protocol.registered_tool_info(mcp, name))


# Direct Python callers retain the historical JSON-string API.  MCP clients receive
# object-shaped structured content, complete descriptions, and conservative policy
# annotations through registry-only wrappers.
tool_protocol.finalize_tool_registry(mcp)
