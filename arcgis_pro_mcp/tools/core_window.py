"""Environment, capabilities, window management, live view, and cache tools."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool

SERVER_EXPORTS: tuple[str, ...] = ("_detach_selected_window", "_selection_state",)

@tool(
    name="arcgis_pro_environment_info",
    description="",
)
def arcgis_pro_environment_info() -> str:
    arcpy = _srv._arcpy()
    info: dict[str, Any] = {}
    try:
        inst = arcpy.GetInstallInfo()
        info["install_info"] = inst
        if isinstance(inst, dict):
            info["pro_version"] = inst.get("Version")
    except Exception as ex:  # noqa: BLE001
        info["install_info_error"] = _srv._safe_error(ex, 500)
    try:
        info["product_info"] = arcpy.ProductInfo()
    except Exception as ex:  # noqa: BLE001
        info["product_info_error"] = _srv._safe_error(ex, 500)
    info["allow_write"] = _srv.writes_allowed()
    info["project_roots_configured"] = bool(_srv.project_roots())
    info["generic_gp_enabled"] = _srv.gp_generic.generic_gp_enabled()
    info["generic_gp_allowlist"] = _srv.gp_generic.generic_gp_allowlist()
    info["generic_gp_allowlist_required"] = False
    info["preferred_gp_execution"] = "native_generic"
    info["tool_profile"] = _srv._ACTIVE_TOOL_PROFILE
    info["available_tool_profiles"] = list(_srv._AVAILABLE_TOOL_PROFILES)
    info["registered_tool_count"] = len(_srv.mcp._tool_manager._tools)
    info.update(_srv._window_status_fields())
    return _srv._json_dumps(info)


@tool(
    name="arcgis_pro_window_status",
    description="读取实时窗口宿主的工程、活动视图、队列、忙碌状态和会话标识，不修改工程。",
)
def arcgis_pro_window_status(process_id: int = 0, window_id: str = "") -> str:
    if not process_id and not window_id:
        return _srv._json_dumps(_srv._window_status_fields(confirm_target=True))
    from arcgis_pro_mcp.pro_attach import host_status

    return _srv._json_dumps(host_status(confirm_target=True, process_id=process_id, window_id=window_id))


@tool(name="arcgis_pro_list_windows", description="列出所有已接入的 ArcGIS Pro 窗口、进程号、工程和就绪状态；不泄露鉴权材料。")
def arcgis_pro_list_windows() -> str:
    from arcgis_pro_mcp.pro_attach import list_windows

    return _srv._json_dumps(list_windows())


@tool(name="arcgis_pro_select_window", description="为 CURRENT 选择并确认唯一窗口；支持进程号、窗口标识和预期工程匹配，不修改工程。")
def arcgis_pro_select_window(process_id: int = 0, window_id: str = "", expected_project: str = "") -> str:
    from arcgis_pro_mcp.pro_attach import select_window

    if expected_project:
        expected_project = _srv.validate_project_path(expected_project, "expected_project")
    return _srv._json_dumps(select_window(process_id, window_id, expected_project))


@tool(
    name="arcgis_pro_detach_window",
    description="安全停止当前 ArcGIS Pro 窗口宿主；需要开启写入权限。",
)
def arcgis_pro_detach_window(process_id: int = 0) -> str:
    from arcgis_pro_mcp.pro_attach import window_scope

    with window_scope(process_id=process_id):
        return _srv._detach_selected_window()


def _detach_selected_window() -> str:
    _srv.require_allow_write()
    import time

    from arcgis_pro_mcp.pro_attach import (
        _WINDOW_SCOPE,
        _WindowResolutionError,
        clear_window_target,
        host_health,
        stop_host,
    )

    scope = _WINDOW_SCOPE.get()
    try:
        health = host_health()
    except _WindowResolutionError as exc:
        if exc.reason == "ambiguous":
            raise
        if scope:
            clear_window_target(process_id=scope[0], window_id=scope[1])
        elif exc.selected:
            clear_window_target(window_id=exc.selected)
        return _srv._json_dumps({"ok": True, "detached": False, "reason": "window host is not attached"})
    if not health or not health.get("ok"):
        if scope:
            clear_window_target(process_id=scope[0], window_id=scope[1])
        return _srv._json_dumps({"ok": True, "detached": False, "reason": "window host is not attached"})
    result = stop_host(health)
    session_id = str(health.get("session_id") or "")
    raw_pid = health.get("pid")
    target_pid = raw_pid if type(raw_pid) is int else 0
    target_base = str(health.get("_endpoint_base") or "")
    deadline = time.monotonic() + 3.0
    missing_state_reads = 0
    try:
        while time.monotonic() < deadline:
            try:
                current_health = host_health()
            except RuntimeError:
                current_health = None
            state_session_id = (current_health or {}).get("session_id")
            if state_session_id and state_session_id != session_id:
                return _srv._json_dumps(
                    {"ok": True, "detach_requested": True, "detached": True, "host_response": result}
                )
            if not state_session_id:
                missing_state_reads += 1
                if missing_state_reads >= 2:
                    return _srv._json_dumps(
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
        return _srv._json_dumps(
            {
                "ok": True,
                "detach_requested": True,
                "detached": False,
                "state": "STOPPING",
                "reason": "宿主仍在完成已开始的调用；不要自动重试该写操作",
                "host_response": result,
            }
        )
    finally:
        clear_window_target(
            process_id=target_pid,
            session_id=session_id,
            endpoint_base=target_base,
        )


@tool(
    name="arcgis_pro_server_capabilities",
    description="",
)
def arcgis_pro_server_capabilities() -> str:
    write = _srv.writes_allowed()
    tools_read = [
        "arcgis_pro_list_windows",
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
    registered_names = sorted(_srv.mcp._tool_manager._tools)
    registered_set = set(registered_names)
    tools_read = [name for name in tools_read if name in registered_set]
    tools_write = [name for name in tools_write if name in registered_set]
    tools_export = [name for name in tools_export if name in registered_set]
    tools_require_allow_publish = [
        name for name in tools_require_allow_publish if name in registered_set
    ]
    tools_require_allow_public_share_when_everyone = [
        name
        for name in tools_require_allow_public_share_when_everyone
        if name in registered_set
    ]
    tools_require_allow_publish_overwrite_when_overwrite = [
        name
        for name in tools_require_allow_publish_overwrite_when_overwrite
        if name in registered_set
    ]
    tools_require_allow_enterprise_write = [
        name for name in tools_require_allow_enterprise_write if name in registered_set
    ]
    tools_require_allow_destructive = [
        name for name in tools_require_allow_destructive if name in registered_set
    ]
    tools_require_window = [name for name in tools_require_window if name in registered_set]
    policy_catalog = {
        tool_name: _srv.tool_protocol.tool_policy(tool_name)
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
        gates = set(policy["gates"])
        conditional_gates = {
            item["gate"] for item in policy.get("conditional_gates", [])
        }
        all_gates = gates | conditional_gates
        if policy["read_only"] and tool_name not in read_set and tool_name not in write_set:
            tools_read.append(tool_name)
            read_set.add(tool_name)
        if "ARCGIS_PRO_MCP_ALLOW_WRITE" in all_gates and tool_name not in write_set:
            tools_write.append(tool_name)
            write_set.add(tool_name)
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
    return _srv._json_dumps(
        {
            "allow_write": write,
            "writes_enabled_by_default": True,
            "writes_disable_env": "ARCGIS_PRO_MCP_ALLOW_WRITE=0",
            "writes_required_env": None,
            "allow_destructive": _srv.destructive_allowed(),
            "destructive_required_env": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=1",
            "allow_cim_write": _srv.cim_write_allowed(),
            "cim_write_required_env": "ARCGIS_PRO_MCP_ALLOW_CIM_WRITE=1",
            "allow_publish": _srv.publish_allowed(),
            "publish_required_env": "ARCGIS_PRO_MCP_ALLOW_PUBLISH=1",
            "allow_public_share": _srv.public_share_allowed(),
            "public_share_required_env": "ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE=1",
            "allow_publish_overwrite": _srv.publish_overwrite_allowed(),
            "publish_overwrite_required_env": (
                "ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE=1"
            ),
            "allow_enterprise_write": _srv.enterprise_write_allowed(),
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
                os.environ.get(_srv._DB_INSTANCE_ALLOWLIST_ENV, "").strip(),
            ),
            "db_username_configured": bool(os.environ.get(_srv._DB_USERNAME_ENV, "")),
            "db_password_configured": bool(os.environ.get(_srv._DB_PASSWORD_ENV, "")),
            "export_root_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_EXPORT_ROOT", "").strip(),
            ),
            "gp_output_root_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT", "").strip(),
            ),
            "input_roots_configured": bool(
                os.environ.get("ARCGIS_PRO_MCP_INPUT_ROOTS", "").strip(),
            ),
            "project_roots_configured": bool(_srv.project_roots()),
            "generic_gp_enabled": _srv.gp_generic.generic_gp_enabled(),
            "analysis_quality": {
                "checker_version": _srv.analysis_quality.CHECKER_VERSION, "checked_operations": ["clip_raster"],
                "scope": "file-mode single-band north-up GeoTIFF; explicit rectangle or polygon file",
                "asset_versions": "SHA256 dataset families", "runtime_mode": "FILE",
                "required_checks": sorted(_srv.raster_checked._REQUIRED),
                "unsupported": ["GUI selections", "multiband", "rotated grids", "implicit reprojection", "scientific model suitability"],
                "legacy_tool_success_is_quality_qualification": False,
            },
            "generic_gp_allowlist": _srv.gp_generic.generic_gp_allowlist(),
            "generic_gp_allowlist_required": False,
            "generic_gp_parameter_adapters": {
                "WeightedOverlay_sa.in_weighted_overlay_table": {
                    "format": "JSON object with rasters and evaluation_scale",
                    "remap_type": "numeric RemapValue",
                    "embedded_input_roots_enforced": True,
                },
            },
            "preferred_gp_execution": "native_generic",
            "multi_window": {
                "supported": True,
                "automatic_port_assignment": True,
                "discovery_tool": "arcgis_pro_list_windows",
                "selection_tool": "arcgis_pro_select_window",
                "explicit_target_format": "CURRENT:<process_id>",
                "concurrent_explicit_targets": True,
            },
            **_srv._window_status_fields(),
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
            "tool_profile": _srv._ACTIVE_TOOL_PROFILE,
            "available_tool_profiles": list(_srv._AVAILABLE_TOOL_PROFILES),
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


@tool(
    name="arcgis_pro_open_report_view",
    description="在已接入的 CURRENT ArcGIS Pro 窗口中打开并激活一个报表视图。",
)
def arcgis_pro_open_report_view(aprx_path: str, report_name: str) -> str:
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    report = _srv._get_report(project, report_name)
    opener = getattr(report, "openView", None)
    if not callable(opener):
        raise RuntimeError("当前 Report 不支持 openView")
    result = opener()
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "report_name": getattr(report, "name", report_name),
            "view_opened": True,
            "view": str(result) if result is not None else None,
        }
    )


@tool(
    name="arcgis_pro_active_view_info",
    description="读取 ArcGIS Pro 当前窗口的活动地图/布局视图和实时相机范围；仅支持 aprx_path=CURRENT。",
)
def arcgis_pro_active_view_info(aprx_path: str) -> str:
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    return _srv._json_dumps(_srv._active_view_payload(project, path))


@tool(
    name="arcgis_pro_open_map_view",
    description="在 ArcGIS Pro 当前窗口打开并聚焦指定地图视图；仅支持 aprx_path=CURRENT。",
)
def arcgis_pro_open_map_view(
    aprx_path: str,
    map_name: str,
    close_other_views: bool = False,
) -> str:
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    target = _srv._get_map(project, map_name)
    if close_other_views:
        project.closeViews("MAPS_AND_LAYOUTS")
    if not hasattr(target, "openView"):
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 Map.openView")
    target.openView()
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "opened_map": map_name,
            "closed_other_views": bool(close_other_views),
        }
    )


@tool(
    name="arcgis_pro_open_layout_view",
    description="在 ArcGIS Pro 当前窗口打开并聚焦指定布局视图；仅支持 aprx_path=CURRENT。",
)
def arcgis_pro_open_layout_view(
    aprx_path: str,
    layout_name: str,
    close_other_views: bool = False,
) -> str:
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    layout = _srv._get_layout(project, layout_name)
    if close_other_views:
        project.closeViews("MAPS_AND_LAYOUTS")
    if not hasattr(layout, "openView"):
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 Layout.openView")
    layout.openView()
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "opened_layout": layout_name,
            "closed_other_views": bool(close_other_views),
        }
    )


@tool(
    name="arcgis_pro_close_views",
    description="关闭 ArcGIS Pro 当前窗口中的地图、布局、报表或表视图；仅支持 aprx_path=CURRENT。",
)
def arcgis_pro_close_views(aprx_path: str, view_type: str = "MAPS_AND_LAYOUTS") -> str:
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
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
    return _srv._json_dumps({"ok": True, "aprx_path": path, "closed_view_type": normalized})


@tool(
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
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    arcpy, project, path = _srv._open_project(aprx_path)
    view, active_map = _srv._active_map_view(project)
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
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(active_map, "name", None),
            "preserve_scale": bool(preserve_scale),
            "camera": _srv._camera_dict(view.camera),
        }
    )


@tool(
    name="arcgis_pro_zoom_active_view_to_layer",
    description="实时将 ArcGIS Pro 当前地图视图缩放到指定图层或其选择集。",
)
def arcgis_pro_zoom_active_view_to_layer(
    aprx_path: str,
    layer_name: str,
    selection_only: bool = False,
    symbolized_extent: bool = True,
) -> str:
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    view, active_map = _srv._active_map_view(project)
    layer = _srv._find_layer(active_map, layer_name)
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
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(active_map, "name", None),
            "layer_name": layer_name,
            "selection_only": bool(selection_only),
            "camera": _srv._camera_dict(view.camera),
        }
    )


@tool(
    name="arcgis_pro_clip_map_layers",
    description="按多边形图层轮廓裁剪当前地图的图层显示（含底图），不改数据。layer_name 为空则取消裁剪。",
)
def arcgis_pro_clip_map_layers(
    aprx_path: str,
    layer_name: str = "",
    selection: str = "ALL",
) -> str:
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    _view, active_map = _srv._active_map_view(project)
    sel = (selection or "ALL").strip().upper()
    if sel not in {"ALL", "SELECTED"}:
        raise RuntimeError("selection 须为 ALL 或 SELECTED")
    clip_name = (layer_name or "").strip()
    if not clip_name:
        if not hasattr(active_map, "clipLayers"):
            raise RuntimeError("当前 Map 不支持 clipLayers")
        active_map.clipLayers(None)
        return _srv._json_dumps({"ok": True, "aprx_path": path, "map_name": active_map.name, "clipped": False})
    layer = _srv._find_layer(active_map, clip_name)
    if not hasattr(active_map, "clipLayers"):
        raise RuntimeError("当前 Map 不支持 clipLayers")
    active_map.clipLayers(layer, sel)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": active_map.name,
            "layer_name": layer.name,
            "selection": sel,
            "clipped": True,
        }
    )


@tool(
    name="arcgis_pro_zoom_active_view_to_all_layers",
    description="实时将 ArcGIS Pro 当前地图视图缩放到全部图层或已有选择集。",
)
def arcgis_pro_zoom_active_view_to_all_layers(
    aprx_path: str,
    selection_only: bool = False,
    symbolized_extent: bool = True,
) -> str:
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    view, active_map = _srv._active_map_view(project)
    if not hasattr(view, "zoomToAllLayers"):
        raise RuntimeError("当前活动视图不支持 zoomToAllLayers")
    view.zoomToAllLayers(bool(selection_only), bool(symbolized_extent))
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": path,
            "map_name": getattr(active_map, "name", None),
            "selection_only": bool(selection_only),
            "camera": _srv._camera_dict(view.camera),
        }
    )


@tool(
    name="arcgis_pro_refresh_layer",
    description="刷新 ArcGIS Pro 当前窗口中包含指定图层的可见地图视图。",
)
def arcgis_pro_refresh_layer(aprx_path: str, map_name: str, layer_name: str) -> str:
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
    arcpy, project, path = _srv._open_project(aprx_path)
    target_map = _srv._get_map(project, map_name)
    layer = _srv._find_layer(target_map, layer_name)
    refresh = getattr(arcpy, "RefreshLayer", None)
    if not callable(refresh):
        raise RuntimeError("当前 ArcGIS Pro 版本不支持 arcpy.RefreshLayer")
    actual_name = str(getattr(layer, "name", layer_name))
    refresh(actual_name)
    return _srv._json_dumps(
        {"ok": True, "aprx_path": path, "map_name": map_name, "layer_name": actual_name}
    )


@tool(
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
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    result = _srv.cartography.export_active_view(
        project,
        output_path,
        width,
        height,
        resolution_dpi,
        jpeg_quality,
        transparent_background,
    )
    result.update({"ok": True, "aprx_path": path, "rendered": True})
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_open_layer_table_view",
    description="在 CURRENT ArcGIS Pro 中打开并激活图层属性表，可仅显示当前选择记录。",
)
def arcgis_pro_open_layer_table_view(
    aprx_path: str,
    layer_name: str,
    show_selected: bool = False,
) -> str:
    _srv._require_current_window(aprx_path)
    _, project, path = _srv._open_project(aprx_path)
    _view, active_map = _srv._active_map_view(project)
    result = _srv.cartography.open_table_view(
        _srv._find_layer(active_map, layer_name),
        show_selected=show_selected,
    )
    result.update({"ok": True, "aprx_path": path, "map_name": getattr(active_map, "name", "")})
    return _srv._json_dumps(result)


def _selection_state(layer: Any) -> tuple[list[int], str]:
    try:
        values = sorted(int(value) for value in (layer.getSelectionSet() or ()))
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError("无法读取当前图层选择集") from ex
    digest = hashlib.sha256(
        json.dumps(values, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return values, digest


@tool(
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
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
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
    _, project, path = _srv._open_project(aprx_path)
    _view, active_map = _srv._active_map_view(project)
    layer = _srv._find_layer(active_map, layer_name)
    before, before_digest = _srv._selection_state(layer)
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
    after, after_digest = _srv._selection_state(layer)
    if after != sorted(expected_after):
        raise RuntimeError("ArcGIS Pro 返回的最终选择集与预期不一致")
    refreshed = _srv._refresh_layer_after_window_change(_srv._arcpy(), aprx_path, layer)
    return _srv._json_dumps(
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


@tool(
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
    _srv.require_allow_write()
    _srv._require_current_window(aprx_path)
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
    _, project, path = _srv._open_project(aprx_path)
    view, active_map = _srv._active_map_view(project)
    map_uri = _srv._object_uri(active_map)
    if expected_map_uri != map_uri:
        raise RuntimeError("活动地图 URI 已变化；未修改相机")
    camera = view.camera
    before = _srv._camera_dict(camera)
    for attribute, value in updates.items():
        if value is not None:
            setattr(camera, attribute, float(value))
    view.camera = camera
    after = _srv._camera_dict(view.camera)
    return _srv._json_dumps(
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


@tool(
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
    _srv._require_current_window(aprx_path)
    arcpy, project, path = _srv._open_project(aprx_path)
    _view, active_map = _srv._active_map_view(project)
    result = _srv.live_analysis.query_current_layer(
        arcpy,
        active_map,
        layer_name,
        fields,
        where_clause=where_clause,
        selected_only=selected_only,
        max_rows=max_rows,
    )
    result["aprx_path"] = path
    return _srv._json_dumps(result)


@tool(
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
    _srv._require_current_window(aprx_path)
    arcpy, project, path = _srv._open_project(aprx_path)
    _view, active_map = _srv._active_map_view(project)
    result = _srv.live_analysis.run_current_analysis(
        arcpy,
        active_map,
        tool_name,
        parameters,
        environment=environment,
        add_outputs_to_map=add_outputs_to_map,
    )
    result["aprx_path"] = path
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_delete_layer_selection",
    description="删除 CURRENT 图层当前选择集中的要素；拒绝空选择且必须匹配 expected_count，不进入 Pro UI Undo 栈。",
)
def arcgis_pro_delete_layer_selection(
    aprx_path: str,
    layer_name: str,
    expected_count: int,
) -> str:
    _srv.require_allow_destructive()
    _srv._require_current_window(aprx_path)
    arcpy, project, path = _srv._open_project(aprx_path)
    _view, active_map = _srv._active_map_view(project)
    layer = _srv._find_layer(active_map, layer_name)
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
    refreshed = _srv._refresh_layer_after_window_change(arcpy, aprx_path, layer)
    return _srv._json_dumps(
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


@tool(
    name="arcgis_pro_list_temporary_views",
    description="列出当前 MCP 或窗口宿主进程创建的临时 feature layer/table view 及其不透明引用。",
)
def arcgis_pro_list_temporary_views() -> str:
    values = _srv.session_refs.list_public()
    return _srv._json_dumps({"ok": True, "count": len(values), "temporary_views": values})


@tool(
    name="arcgis_pro_project_cache_status",
    description="读取文件模式 ArcGISProject 缓存中的工程路径；CURRENT 工程不会跨请求缓存。",
)
def arcgis_pro_project_cache_status() -> str:
    values = _srv._cache_keys_snapshot()
    return _srv._json_dumps(
        {
            "ok": True,
            "cached_project_count": len(values),
            "max_cache_size": _srv._PROJECT_CACHE_MAX_SIZE,
            "cache_keys": values,
        }
    )


@tool(
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

    return _srv._json_dumps(submit_host_job(tool_name, arguments, idempotency_key))


@tool(
    name="arcgis_pro_window_job_status",
    description="读取窗口宿主保留的异步或超时任务终态、错误及结构化结果。",
)
def arcgis_pro_window_job_status(request_id: str, process_id: int = 0) -> str:
    from arcgis_pro_mcp.pro_attach import host_job_status, window_scope

    with window_scope(process_id=process_id):
        return _srv._json_dumps(host_job_status(request_id))


@tool(
    name="arcgis_pro_window_job_cancel",
    description="取消尚未开始的窗口任务；Python 宿主对已运行本地 GP 仅报告 cancel_requested，可靠取消需 SDK。",
)
def arcgis_pro_window_job_cancel(request_id: str, process_id: int = 0) -> str:
    from arcgis_pro_mcp.pro_attach import cancel_host_job, window_scope

    with window_scope(process_id=process_id):
        return _srv._json_dumps(cancel_host_job(request_id))


@tool(
    name="arcgis_pro_window_wait_for_change",
    description="长轮询 CURRENT 工程、活动视图、相机和选择集 revision；Python 源不声称提供 DrawComplete。",
)
def arcgis_pro_window_wait_for_change(
    after_revision: int = 0,
    timeout_ms: int = 30_000,
    process_id: int = 0,
) -> str:
    from arcgis_pro_mcp.pro_attach import wait_for_window_change, window_scope

    with window_scope(process_id=process_id):
        return _srv._json_dumps(wait_for_window_change(after_revision, timeout_ms))


@tool(
    name="arcgis_pro_release_project",
    description="释放一个文件模式工程的进程缓存引用，以便外部修改或方案锁及时可见；不删除或保存工程。",
)
def arcgis_pro_release_project(aprx_path: str, confirm_aprx_path: str) -> str:
    _srv.require_allow_destructive()
    if confirm_aprx_path != aprx_path:
        raise RuntimeError("confirm_aprx_path 必须与 aprx_path 完全一致")
    path = _srv.validate_project_path(aprx_path, "aprx_path")
    if _srv.is_current_project_token(path):
        raise RuntimeError("CURRENT 不跨请求缓存，无需释放")
    key = _srv._project_cache_key(path)
    project = _srv._cache_pop_project(key)
    released = project is not None
    if project is not None:
        del project
    return _srv._json_dumps({"ok": True, "aprx_path": path, "released": released})


@tool(
    name="arcgis_pro_reload_project",
    description="释放并重新打开一个文件模式 .aprx，返回新的工程摘要；不会保存旧缓存引用中的未保存修改。",
)
def arcgis_pro_reload_project(aprx_path: str, confirm_aprx_path: str) -> str:
    _srv.require_allow_destructive()
    if confirm_aprx_path != aprx_path:
        raise RuntimeError("confirm_aprx_path 必须与 aprx_path 完全一致")
    path = _srv.validate_project_path(aprx_path, "aprx_path")
    if _srv.is_current_project_token(path):
        raise RuntimeError("CURRENT 由窗口宿主逐请求绑定，不能通过文件缓存重载")
    key = _srv._project_cache_key(path)
    previous = _srv._cache_pop_project(key)
    if previous is not None:
        del previous
    _, project, opened = _srv._open_project(path)
    return _srv._json_dumps(
        {
            "ok": True,
            "aprx_path": opened,
            "reloaded": True,
            "map_count": len(list(project.listMaps())),
            "layout_count": len(list(project.listLayouts())),
        }
    )


@tool(
    name="arcgis_pro_release_temporary_view",
    description="释放一个由本进程创建的临时 feature layer/table view 引用，不删除其源数据。",
)
def arcgis_pro_release_temporary_view(reference: str) -> str:
    _srv.require_allow_write()
    return _srv._json_dumps(_srv.session_refs.release(_srv._arcpy(), reference))


@tool(
    name="arcgis_pro_tool_info",
    description="读取一个工具或完整工具目录的描述、输入输出 schema、执行模式和安全门，不调用 ArcPy。",
)
def arcgis_pro_tool_info(name: str = "") -> str:
    return _srv._json_dumps(_srv.tool_protocol.registered_tool_info(_srv.mcp, name))
