"""MCP tools for ArcGIS Pro via arcpy.mp (mapping module)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP

from arcgis_pro_mcp import (
    da_read,
    da_write,
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
    metadata,
    symbology,
    workspace_listing,
)
from arcgis_pro_mcp.paths import (
    inline_db_password_allowed,
    is_current_project_token,
    normalize_path,
    project_roots,
    require_allow_write,
    validate_input_path_optional,
    validate_output_in_export_root,
    validate_project_path,
    writes_allowed,
)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _window_status_fields(*, confirm_target: bool = False) -> dict[str, Any]:
    try:
        from arcgis_pro_mcp.pro_attach import host_status

        return host_status(confirm_target=confirm_target)
    except Exception as ex:  # noqa: BLE001
        return {"window_attached": False, "window_status_error": str(ex)[:300]}


mcp = FastMCP(
    "arcgis-pro",
    instructions=(
        "通过 ArcPy 自动化 ArcGIS Pro 工程：读/写地图与图层、布局与导出、白名单地理处理。"
        "无法替代 Pro 全部 UI；写入与选择需 ARCGIS_PRO_MCP_ALLOW_WRITE=1。"
        "导出路径受 ARCGIS_PRO_MCP_EXPORT_ROOT 约束（若设置）；"
        "写入型 GP（Buffer/Clip/叠加分析/统计/投影等）必须设置 ARCGIS_PRO_MCP_GP_OUTPUT_ROOT 且输出位于其下。"
        "实时窗口工具只接受 aprx_path=CURRENT，并在宿主失联或目标工程改变时失败关闭。"
        "须在 Windows 上使用 Pro 捆绑的 Python。"
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
                f"原始错误：{str(ex)[:400]}"
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
        raise RuntimeError(f"打开工程失败：{path}；{str(ex)[:500]}") from ex
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
    if hasattr(mf, "setExtent"):
        mf.setExtent(ext)
        return
    raise RuntimeError("当前 MapFrame 不支持 camera.setExtent/setExtent")


def _mapframe_extent(mf: Any) -> Any:
    cam = getattr(mf, "camera", None)
    if cam is not None and hasattr(cam, "getExtent"):
        return cam.getExtent()
    if hasattr(mf, "getExtent"):
        return mf.getExtent()
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


def _find_layer(map_obj: Any, layer_name: str) -> Any:
    return _select_named_object(
        "layer",
        layer_name,
        list(map_obj.listLayers()),
        allow_long_name=True,
    )


def _layer_selection_set(layer: Any) -> set[Any]:
    """Return the actual ArcGIS Pro layer selection without falling back to total rows."""
    getter = getattr(layer, "getSelectionSet", None)
    if not callable(getter):
        raise RuntimeError("当前图层不支持 getSelectionSet，无法验证选择结果")
    try:
        values = getter()
        return set(values or ())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"读取图层选择集失败：{str(exc)[:300]}") from exc


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
            f"{str(exc)[:300]}"
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
        out["extent_error"] = str(ex)[:300]
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
        info["install_info_error"] = str(ex)[:500]
    try:
        info["product_info"] = arcpy.ProductInfo()
    except Exception as ex:  # noqa: BLE001
        info["product_info_error"] = str(ex)[:500]
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
    ]
    tools_export = [
        "arcgis_pro_export_layout_pdf",
        "arcgis_pro_export_layout_image",
        "arcgis_pro_export_report_pdf",
        "arcgis_pro_export_map_to_image",
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
        "arcgis_pro_refresh_layer",
    ]
    return _json_dumps(
        {
            "allow_write": write,
            "writes_required_env": "ARCGIS_PRO_MCP_ALLOW_WRITE=1",
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
            "tools_require_window": tools_require_window,
            "note": (
                "无法通过 MCP 覆盖 Esri 全功能清单中的每一项；本服务仅封装部分 arcpy/arcpy.da/arcpy.mp 能力，"
                "发布/共享、深度学习、完整编辑会话等需专用方案或未实现。"
                "若要让 Pro 窗口跟着 MCP 变化，先在 Pro 的 Python 窗口运行仓库根目录的 接入当前窗口.py。"
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
    name="arcgis_pro_describe",
    description="",
)
def arcgis_pro_describe(dataset_path: str) -> str:
    arcpy = _arcpy()
    p = validate_input_path_optional(dataset_path, "dataset_path")
    try:
        summary = _describe_summary(arcpy, p)
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError(str(ex)[:800]) from ex
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
        raise RuntimeError(str(ex)[:800]) from ex
    return _json_dumps({"dataset_path": p, "fields": rows, "field_count": len(rows)})


@mcp.tool(
    name="arcgis_pro_project_connections",
    description="",
)
def arcgis_pro_project_connections(aprx_path: str) -> str:
    _, project, path = _open_project(aprx_path)
    out: dict[str, Any] = {"aprx_path": path}
    specs = (
        ("listFolderConnections", "folder_connections"),
        ("listDatabases", "databases"),
        ("listToolboxes", "toolboxes"),
        ("listWorkspaces", "workspaces"),
    )
    for meth, key in specs:
        if not hasattr(project, meth):
            out[key] = []
            continue
        try:
            items = getattr(project, meth)()
            out[key] = [str(x) for x in (items or [])]
        except Exception as ex:  # noqa: BLE001
            out[key] = []
            out[f"{key}_error"] = str(ex)[:500]
    return _json_dumps(out)


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
                item["data_source"] = lyr.dataSource
            except Exception as ex:  # noqa: BLE001
                item["data_source_error"] = str(ex)[:300]
            broken_items.append(item)
        broken_total = len(broken_layers)
    except Exception as ex:  # noqa: BLE001
        broken_total = -1
        broken_error = str(ex)[:500]

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
                entry["data_source"] = lyr.dataSource
            except Exception as ex:  # noqa: BLE001
                entry["data_source_error"] = str(ex)[:500]
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
            row["data_source"] = tbl.dataSource
        except Exception as ex:  # noqa: BLE001
            row["data_source_error"] = str(ex)[:500]
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
        props["symbology_error"] = str(ex)[:300]
    try:
        props["data_source"] = lyr.dataSource
    except Exception as ex:  # noqa: BLE001
        props["data_source_error"] = str(ex)[:500]
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
        out["map_name_error"] = str(ex)[:200]
    try:
        out["scale"] = float(mf.camera.scale)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["extent"] = _extent_dict(mf.getExtent())
    except Exception as ex:  # noqa: BLE001
        out["extent_error"] = str(ex)[:500]

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
    out_path = validate_output_in_export_root(output_pdf_path, "output_pdf_path")
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
    out_path = validate_output_in_export_root(output_path, "output_path")
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
    out_path = validate_output_in_export_root(output_aprx_path, "output_aprx_path")
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
    created_name = name
    try:
        created_name = str(result.getOutput(0))
    except Exception:  # noqa: BLE001
        pass
    count = gp_allowlist.gp_get_count_layer(arcpy, created_name)
    return _json_dumps({"ok": True, "dataset_path": p, "layer_name": created_name, "count": count})


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
    created_name = name
    try:
        created_name = str(result.getOutput(0))
    except Exception:  # noqa: BLE001
        pass
    count = gp_allowlist.gp_get_count(arcpy, created_name)
    return _json_dumps({"ok": True, "dataset_path": p, "view_name": created_name, "count": count})


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
        raise RuntimeError(f"读取书签失败：{str(ex)[:500]}") from ex
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


@mcp.tool(
    name="arcgis_pro_remove_layer",
    description="",
)
def arcgis_pro_remove_layer(aprx_path: str, map_name: str, layer_name: str) -> str:
    require_allow_write()
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
def arcgis_pro_remove_table(aprx_path: str, map_name: str, table_name: str) -> str:
    require_allow_write()
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
                        f"此前已清除 {cleared} 个图层：{str(exc)[:300]}"
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
    return _json_dumps(
        {
            "aprx_path": path,
            "map_name": map_name,
            "layer_name": layer_name,
            "fids": fids,
            "selected_count": len(ordered),
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
    join_name: str = "",
) -> str:
    require_allow_write()
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
            "join_name": jn or "(all)",
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
        out["extent_read_error"] = str(ex)[:300]
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
    description="",
)
def arcgis_pro_gp_delete_field(in_table: str, drop_field: str) -> str:
    arcpy = _arcpy()
    gp_schema.run_delete_field(arcpy, in_table, drop_field)
    return _json_dumps(
        {"ok": True, "in_table": normalize_path(in_table), "drop_field": drop_field.strip()},
    )


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
) -> str:
    arcpy = _arcpy()
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
    description="",
)
def arcgis_pro_gp_calculate_field(
    in_table: str,
    field_name: str,
    expression: str,
    expression_type: str = "PYTHON3",
    code_block: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_table, "in_table")
    fn = field_name.strip()
    if not fn:
        raise RuntimeError("field_name 不能为空")
    expr = expression.strip()
    if not expr:
        raise RuntimeError("expression 不能为空")
    et = expression_type.strip().upper()
    if et not in ("PYTHON3", "ARCADE", "VB", "PYTHON", "PYTHON_9.3"):
        raise RuntimeError("expression_type 须为 PYTHON3、ARCADE 或 VB")
    cb = (code_block or "").strip()
    if cb:
        arcpy.management.CalculateField(p, fn, expr, et, cb)
    else:
        arcpy.management.CalculateField(p, fn, expr, et)
    return _json_dumps({"ok": True, "in_table": normalize_path(in_table), "field_name": fn})


@mcp.tool(
    name="arcgis_pro_gp_calculate_geometry",
    description="",
)
def arcgis_pro_gp_calculate_geometry(
    in_features: str,
    geometry_property: list[list[str]],
    length_unit: str = "",
    area_unit: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_features, "in_features")
    if not geometry_property:
        raise RuntimeError("geometry_property 不能为空")
    aliases = {
        "AREA": "AREA_GEODESIC",
        "LENGTH": "PERIMETER_LENGTH_GEODESIC",
        "PERIMETER": "PERIMETER_LENGTH_GEODESIC",
    }
    mapped: list[list[str]] = []
    for pair in geometry_property:
        if len(pair) < 2:
            raise RuntimeError("geometry_property 每项须为 [字段名, 几何属性]")
        prop = aliases.get(str(pair[1]).strip().upper(), str(pair[1]).strip().upper())
        mapped.append([str(pair[0]), prop])
    geometry_property = mapped
    lu = (length_unit or "").strip()
    au = (area_unit or "").strip()
    kwargs: dict[str, Any] = {}
    if lu:
        kwargs["length_unit"] = lu
    if au:
        kwargs["area_unit"] = au
    arcpy.management.CalculateGeometryAttributes(p, geometry_property, **kwargs)
    return _json_dumps({"ok": True, "in_features": normalize_path(in_features)})


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
def arcgis_pro_gp_delete_features(in_features: str) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_features, "in_features")
    arcpy.management.DeleteFeatures(p)
    return _json_dumps({"ok": True, "in_features": normalize_path(in_features)})


@mcp.tool(
    name="arcgis_pro_gp_truncate_table",
    description="",
)
def arcgis_pro_gp_truncate_table(in_table: str) -> str:
    require_allow_write()
    arcpy = _arcpy()
    p = validate_input_path_optional(in_table, "in_table")
    arcpy.management.TruncateTable(p)
    return _json_dumps({"ok": True, "in_table": normalize_path(in_table)})


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
def arcgis_pro_gp_delete_dataset(in_data: str) -> str:
    arcpy = _arcpy()
    gp_create.run_delete_dataset(arcpy, in_data)
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
    description="",
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
) -> str:
    arcpy = _arcpy()
    gp_raster.run_topo_to_raster(arcpy, in_topo_features, out_raster, cell_size)
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
) -> str:
    require_allow_write()
    arcpy, project, path = _open_project(aprx_path)
    ln = layout_name.strip()
    if not ln:
        raise RuntimeError("layout_name 不能为空")
    w = max(1.0, min(float(page_width), 200.0))
    h = max(1.0, min(float(page_height), 200.0))
    lyt = project.createLayout(w, h, "INCH")
    lyt.name = ln
    maps = list(project.listMaps())
    if maps and hasattr(lyt, "createMapFrame"):
        try:
            mf = lyt.createMapFrame(arcpy.Extent(0.5, 0.5, max(w - 0.5, 1.0), max(h - 0.5, 1.0)), maps[0])
            if hasattr(mf, "name"):
                mf.name = "Map Frame"
        except Exception:  # noqa: BLE001
            pass
    return _json_dumps({"ok": True, "aprx_path": path, "layout_name": ln})


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
def arcgis_pro_remove_layout(aprx_path: str, layout_name: str) -> str:
    require_allow_write()
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
    fn = field_name.strip()
    fa = field_alias.strip()
    if not fn or not fa:
        raise RuntimeError("field_name 和 field_alias 不能为空")
    arcpy.management.AlterField(ds, fn, new_field_alias=fa)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name,
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
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    cp = cim_path.strip()
    if not cp:
        raise RuntimeError("cim_path 不能为空")
    import json as _json
    try:
        val = _json.loads(value)
    except Exception:
        val = value
    cim_def = lyr.getDefinition("V3")
    parts = cp.split(".")
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
    info: dict[str, Any] = {"layer_name": layer_name}
    try:
        sym = lyr.symbology
        info["renderer_type"] = getattr(sym.renderer, "type", str(type(sym.renderer).__name__))
        try:
            info["fields"] = sym.renderer.fields
        except Exception:  # noqa: BLE001
            pass
        try:
            info["classification_field"] = sym.renderer.classificationField
        except Exception:  # noqa: BLE001
            pass
        try:
            info["break_count"] = sym.renderer.breakCount
        except Exception:  # noqa: BLE001
            pass
    except Exception as ex:  # noqa: BLE001
        info["error"] = str(ex)[:500]
    return _json_dumps(info)


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
                item["data_source"] = lyr.dataSource
            except Exception as ex:  # noqa: BLE001
                item["data_source_error"] = str(ex)[:300]
            broken.append(item)
    except Exception as ex:  # noqa: BLE001
        return _json_dumps({"aprx_path": path, "error": str(ex)[:500]})
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
    username_env_var: str = "",
    password_env_var: str = "",
) -> str:
    require_allow_write()
    arcpy = _arcpy()
    from arcgis_pro_mcp.paths import require_gp_output_root_mandatory, validate_gp_output_path
    require_gp_output_root_mandatory()
    ofp = validate_gp_output_path(out_folder_path, "out_folder_path")
    os.makedirs(ofp, exist_ok=True)
    on = out_name.strip()
    if not on:
        raise RuntimeError("out_name 不能为空")
    if not on.lower().endswith(".sde"):
        on += ".sde"
    kwargs: dict[str, str] = {
        "database_platform": database_platform.strip(),
        "instance": instance.strip(),
    }
    if database:
        kwargs["database"] = database.strip()
    auth = authentication.strip().upper()
    kwargs["account_authentication"] = auth
    user = username
    pwd = password
    uev = username_env_var.strip()
    pev = password_env_var.strip()
    if user and uev:
        raise RuntimeError("username 与 username_env_var 只能二选一")
    if pwd and pev:
        raise RuntimeError("password 与 password_env_var 只能二选一")
    if uev:
        user = os.environ.get(uev, "").strip()
        if not user:
            raise RuntimeError(f"环境变量 {uev!r} 未设置或为空")
    if pev:
        pwd = os.environ.get(pev, "")
        if not pwd:
            raise RuntimeError(f"环境变量 {pev!r} 未设置或为空")
    if auth == "DATABASE_AUTH":
        if not user:
            raise RuntimeError("DATABASE_AUTH 需要 username 或 username_env_var")
        if password and not inline_db_password_allowed():
            raise RuntimeError(
                "默认不允许通过 MCP 直接传入数据库密码。请改用 password_env_var，"
                "或在受控环境下设置 ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD=1。"
            )
        if not pwd:
            raise RuntimeError("DATABASE_AUTH 需要 password_env_var，或显式允许内联 password")
        kwargs["username"] = user
        kwargs["password"] = pwd
    arcpy.management.CreateDatabaseConnection(ofp, on, **kwargs)
    return _json_dumps(
        {
            "ok": True,
            "connection_file": os.path.join(ofp, on),
            "username_source": "env" if uev else ("inline" if user else ""),
            "password_source": "env" if pev else ("inline" if pwd else ""),
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
) -> str:
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    mn = map_name.strip()
    if not mn:
        raise RuntimeError("map_name 不能为空")
    new_map = project.createMap(mn)
    return _json_dumps(
        {"ok": True, "aprx_path": path, "map_name": getattr(new_map, "name", mn)},
    )


@mcp.tool(
    name="arcgis_pro_remove_map",
    description="",
)
def arcgis_pro_remove_map(aprx_path: str, map_name: str) -> str:
    require_allow_write()
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
    require_allow_write()
    _, project, path = _open_project(aprx_path)
    m = _get_map(project, map_name)
    lyr = _find_layer(m, layer_name)
    updated: dict[str, str] = {}
    try:
        if time_field:
            lyr.time.isTimeEnabled = True
            lyr.time.startTimeField = time_field
            updated["time_field"] = time_field
        if start_time:
            lyr.time.startTime = start_time
            updated["start_time"] = start_time
        if end_time:
            lyr.time.endTime = end_time
            updated["end_time"] = end_time
    except Exception as ex:  # noqa: BLE001
        return _json_dumps({"ok": False, "error": str(ex)[:500]})
    return _json_dumps(
        {"ok": True, "aprx_path": path, "layer_name": layer_name, "updated": updated},
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
