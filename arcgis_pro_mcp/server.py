"""MCP tools facade and runtime orchestrator for ArcGIS Pro via arcpy.mp."""

from __future__ import annotations

import gc  # noqa: F401 - kept for backwards-compatible attribute access on server
import hashlib  # noqa: F401 - kept for backwards-compatible attribute access on server
import json
import os
import re  # noqa: F401 - kept for backwards-compatible attribute access on server
import sys
import threading
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from arcgis_pro_mcp import (
    analysis_quality,  # noqa: F401
    cartography,  # noqa: F401
    charts,  # noqa: F401
    da_read,  # noqa: F401
    da_write,  # noqa: F401
    data_integrity,  # noqa: F401
    dataset_management,  # noqa: F401
    editing,  # noqa: F401
    enterprise_gdb,  # noqa: F401
    geocoding,  # noqa: F401
    gp_allowlist,  # noqa: F401
    gp_analysis,  # noqa: F401
    gp_convert,  # noqa: F401
    gp_create,  # noqa: F401
    gp_generic,  # noqa: F401
    gp_network,  # noqa: F401
    gp_raster,  # noqa: F401
    gp_schema,  # noqa: F401
    gp_stats,  # noqa: F401
    gp_write,  # noqa: F401
    lidar,  # noqa: F401
    live_analysis,  # noqa: F401
    metadata,  # noqa: F401
    network_analysis,  # noqa: F401
    project_catalog,  # noqa: F401
    project_io,  # noqa: F401
    publishing,  # noqa: F401
    raster_advanced,  # noqa: F401
    raster_checked,  # noqa: F401
    raster_runtime,  # noqa: F401
    schema_maintenance,  # noqa: F401
    sdk_bridge,  # noqa: F401
    session_refs,  # noqa: F401
    spatial_modeling,  # noqa: F401
    symbology,  # noqa: F401
    tool_protocol,
    utility_network,  # noqa: F401
    workspace_listing,  # noqa: F401
)
from arcgis_pro_mcp.arcade import validate_safe_arcade_expression  # noqa: F401
from arcgis_pro_mcp.paths import (
    cim_write_allowed,  # noqa: F401
    destructive_allowed,  # noqa: F401
    enterprise_write_allowed,  # noqa: F401
    inline_db_password_allowed,  # noqa: F401
    is_current_project_token,
    normalize_path,  # noqa: F401
    project_roots,  # noqa: F401
    public_share_allowed,  # noqa: F401
    publish_allowed,  # noqa: F401
    publish_overwrite_allowed,  # noqa: F401
    require_allow_cim_write,  # noqa: F401
    require_allow_destructive,  # noqa: F401
    require_allow_write,  # noqa: F401
    validate_input_path_optional,  # noqa: F401
    validate_new_output_in_export_root,  # noqa: F401
    validate_output_name,  # noqa: F401
    validate_project_path,
    writes_allowed,  # noqa: F401
)
from arcgis_pro_mcp.redaction import redact_sensitive as _redact_sensitive  # noqa: F401
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
_PLACE_LAYER = frozenset({"BEFORE", "AFTER"})
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

DEFAULT_MAX_PROJECT_CACHE = 16


def _json_dumps(data: Any) -> Any:
    """Redact once and return pre-redacted object inside MCP calls or indented JSON string."""
    redacted = _redact_text_values(data)
    if tool_protocol.is_in_structured_tool():
        return tool_protocol.pre_redacted_payload(redacted)
    return json.dumps(
        redacted,
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
        "必须遵守各自路径根；GP 优先使用原生工具通用入口，默认开启且无需工具白名单；"
        "仍须遵守新输出与操作类型限制。实时宿主、租约、generation"
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


def _initial_project_cache_limit() -> int:
    raw = os.environ.get("ARCGIS_PRO_MCP_MAX_PROJECT_CACHE", "").strip()
    if not raw:
        return DEFAULT_MAX_PROJECT_CACHE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PROJECT_CACHE
    return max(1, value)


_PROJECT_CACHE_LOCK = threading.RLock()
_PROJECT_CACHE: OrderedDict[str, Any] = OrderedDict()
_PROJECT_CACHE_MAX_SIZE: int = _initial_project_cache_limit()
_CURRENT_PROJECT_CONTEXT: ContextVar[tuple[Any, str] | None] = ContextVar(
    "arcgis_pro_current_project_context",
    default=None,
)


def configure_project_cache(max_size: int) -> int:
    """Configure the bounded LRU cache capacity and evict oldest entries if needed."""
    global _PROJECT_CACHE_MAX_SIZE
    limit = int(max_size)
    if limit < 1:
        raise ValueError("max_size 必须为大于等于 1 的整数")
    with _PROJECT_CACHE_LOCK:
        _PROJECT_CACHE_MAX_SIZE = limit
        while len(_PROJECT_CACHE) > _PROJECT_CACHE_MAX_SIZE:
            _, evicted = _PROJECT_CACHE.popitem(last=False)
            del evicted
        return _PROJECT_CACHE_MAX_SIZE


def clear_project_cache() -> int:
    """Clear all cached file-mode ArcGISProject handles in a thread-safe manner."""
    with _PROJECT_CACHE_LOCK:
        count = len(_PROJECT_CACHE)
        _PROJECT_CACHE.clear()
        return count


def project_cache_stats() -> dict[str, Any]:
    """Return a thread-safe snapshot of LRU project cache occupancy and ordering."""
    with _PROJECT_CACHE_LOCK:
        return {
            "size": len(_PROJECT_CACHE),
            "max_size": _PROJECT_CACHE_MAX_SIZE,
            "keys": list(_PROJECT_CACHE.keys()),
        }


def _cache_pop_project(key: str) -> Any:
    with _PROJECT_CACHE_LOCK:
        return _PROJECT_CACHE.pop(key, None)


def _cache_keys_snapshot() -> list[str]:
    with _PROJECT_CACHE_LOCK:
        return sorted(_PROJECT_CACHE)


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
    from arcgis_pro_mcp.pro_attach import current_process_id

    explicit_pid = current_process_id(aprx_path)
    if explicit_pid and explicit_pid != os.getpid():
        raise RuntimeError("指定窗口的 CURRENT 调用必须通过 MCP 代理路由，不能在另一进程内直接打开")
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
    with _PROJECT_CACHE_LOCK:
        project = _PROJECT_CACHE.get(key)
        if project is not None:
            _PROJECT_CACHE.move_to_end(key)
            return arcpy, project, path
    try:
        project = arcpy.mp.ArcGISProject(path)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(f"打开工程失败：{path}；{_safe_error(ex, 500)}") from ex
    with _PROJECT_CACHE_LOCK:
        existing = _PROJECT_CACHE.get(key)
        if existing is not None:
            _PROJECT_CACHE.move_to_end(key)
            return arcpy, existing, path
        _PROJECT_CACHE[key] = project
        _PROJECT_CACHE.move_to_end(key)
        while len(_PROJECT_CACHE) > _PROJECT_CACHE_MAX_SIZE:
            _, evicted = _PROJECT_CACHE.popitem(last=False)
            del evicted
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


def _list_workspace_domains(
    arcpy: Any,
    workspace_path: str,
    max_items: int = 200,
) -> list[dict[str, Any]]:
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


_ACTIVE_TOOL_PROFILE: str = "full"
_AVAILABLE_TOOL_PROFILES: list[str] = []


def _register_server_tools(profile: str | None = None) -> dict[str, Any]:
    """Load domain tool modules, export callables onto server, and register profile tools."""
    from arcgis_pro_mcp.tools import register_tools

    return register_tools(mcp, sys.modules[__name__], profile=profile)


# Single-pass structured registration: direct Python callers retain the historical
# JSON-string API on arcgis_pro_mcp.server, while MCP clients receive object-shaped
# structured content, complete descriptions, and conservative policy annotations
# without a second Pydantic schema build.
_register_server_tools()
