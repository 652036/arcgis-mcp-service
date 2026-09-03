"""MCP discovery metadata and structured-result adaptation.

The public Python functions intentionally keep returning JSON strings for backwards
compatibility with direct callers.  The FastMCP registry, however, exposes wrappers
that decode those strings into JSON objects so MCP clients receive useful
``structuredContent`` and an object output schema.
"""

from __future__ import annotations

import inspect
import json
import re
from functools import wraps
from typing import Any

from mcp.types import ToolAnnotations

from arcgis_pro_mcp.redaction import redact_text_values, safe_error

_ACTION_LABELS = {
    "add": "添加",
    "alter": "修改",
    "apply": "应用",
    "check": "检查",
    "clear": "清除",
    "clip": "裁剪",
    "close": "关闭",
    "copy": "复制",
    "create": "创建",
    "delete": "删除",
    "describe": "描述",
    "detach": "断开",
    "distinct": "读取唯一值",
    "duplicate": "复制",
    "enable": "启用",
    "environment": "读取环境",
    "exists": "检查存在性",
    "export": "导出",
    "get": "读取",
    "import": "导入",
    "insert": "插入",
    "list": "列出",
    "make": "创建",
    "move": "移动",
    "open": "打开",
    "pan": "平移",
    "properties": "读取属性",
    "query": "查询",
    "refresh": "刷新",
    "release": "释放",
    "reload": "重新加载",
    "remove": "移除",
    "rename": "重命名",
    "repair": "修复",
    "run": "运行",
    "save": "保存",
    "select": "选择",
    "set": "设置",
    "solve": "求解",
    "stage": "暂存",
    "status": "读取状态",
    "summary": "读取摘要",
    "submit": "提交",
    "toggle": "切换",
    "truncate": "清空",
    "update": "更新",
    "validate": "验证",
    "verify": "核验",
    "wait": "等待",
    "zoom": "缩放",
}

_OBJECT_LABELS = {
    "active_view": "活动视图",
    "bookmark": "书签",
    "bookmarks": "书签",
    "capabilities": "服务能力",
    "chart": "图表",
    "cim": "CIM 定义",
    "dataset": "数据集",
    "domain": "属性域",
    "environment": "地理处理环境",
    "extension": "扩展许可",
    "feature": "要素",
    "features": "要素",
    "field": "字段",
    "fields": "字段",
    "job": "任务",
    "label": "标注",
    "layer": "图层",
    "layout": "布局",
    "map": "地图",
    "mapframe": "地图框",
    "map_series": "地图系列",
    "metadata": "元数据",
    "portal": "Portal",
    "project": "工程",
    "raster": "栅格",
    "report": "报表",
    "selection": "选择集",
    "service": "服务",
    "subtype": "子类型",
    "table": "表",
    "time": "时间范围",
    "tool": "工具",
    "topology": "拓扑",
    "travel_modes": "出行模式",
    "view": "视图",
    "workspace": "工作空间",
}

_READ_ACTIONS = {
    "active",
    "camera",
    "capabilities",
    "check",
    "connections",
    "context",
    "count",
    "describe",
    "distinct",
    "environment",
    "exists",
    "get",
    "info",
    "list",
    "properties",
    "query",
    "status",
    "summary",
    "verify",
    "wait",
}

_READ_ONLY_EXACT = frozenset(
    {
        "arcgis_pro_calculate_distance_band",
        "arcgis_pro_connection_repair_preflight",
        "arcgis_pro_da_table_sample",
        "arcgis_pro_dataset_schema",
        "arcgis_pro_edit_geometry_preflight",
        "arcgis_pro_edit_preflight",
        "arcgis_pro_edit_workspace_preflight",
        "arcgis_pro_map_spatial_reference",
        "arcgis_pro_mapframe_extent",
    }
)

_WRITE_EXACT = frozenset(
    {
        "arcgis_pro_export_active_view_image",
        "arcgis_pro_export_bookmarks",
        "arcgis_pro_export_chart",
        "arcgis_pro_export_map_series_pdf",
        "arcgis_pro_export_map_to_image",
        "arcgis_pro_export_mapx",
        "arcgis_pro_export_report_pdf",
        "arcgis_pro_export_topology_errors",
        "arcgis_pro_gp_check_geometry",
        "arcgis_pro_gp_count_overlapping_features",
        "arcgis_pro_gp_export_features",
        "arcgis_pro_gp_export_table",
        "arcgis_pro_gp_validate_topology",
        "arcgis_pro_paste_layer_properties",
        "arcgis_pro_save_layer_file",
        "arcgis_pro_upsert_definition_query",
        "arcgis_pro_validate_topology",
        "arcgis_pro_validate_utility_network_topology",
    }
)

_DESTRUCTIVE_EXACT = frozenset(
    {
        "arcgis_pro_add_attribute_rule",
        "arcgis_pro_add_contingent_value",
        "arcgis_pro_clear_subtype_field",
        "arcgis_pro_create_field_group",
        "arcgis_pro_delete_attribute_rules",
        "arcgis_pro_gp_calculate_field",
        "arcgis_pro_gp_calculate_geometry",
        "arcgis_pro_gp_delete_field",
        "arcgis_pro_delete_field_group",
        "arcgis_pro_disable_attachments",
        "arcgis_pro_disable_editor_tracking",
        "arcgis_pro_edit_apply",
        "arcgis_pro_import_attribute_rules",
        "arcgis_pro_import_contingent_values",
        "arcgis_pro_post_version",
        "arcgis_pro_reconcile_versions",
        "arcgis_pro_release_project",
        "arcgis_pro_remove_contingent_value",
        "arcgis_pro_reload_project",
        "arcgis_pro_unregister_as_versioned",
        "arcgis_pro_edit_workspace_apply",
    }
)

_NON_DESTRUCTIVE_EXACT = frozenset({"arcgis_pro_detach_window"})

_INPUT_ROOT_TOOLS = frozenset(
    {
        "arcgis_pro_import_attribute_rules",
        "arcgis_pro_import_contingent_values",
    }
)

_CIM_WRITE_TOOLS = frozenset(
    {
        "arcgis_pro_set_heatmap_renderer",
        "arcgis_pro_set_label_font",
        "arcgis_pro_update_label_expression",
        "arcgis_pro_upsert_label_class",
    }
)

_EXPORT_ROOT_TOOLS = frozenset(
    {
        "arcgis_pro_create_sharing_draft",
        "arcgis_pro_get_artifact_digest",
        "arcgis_pro_publish_service_definition",
        "arcgis_pro_stage_service_definition",
    }
)

_DESTRUCTIVE_PARTS = (
    "_delete_",
    "_remove_",
    "_truncate_",
    "_discard_",
    "_detach_",
)

_NON_IDEMPOTENT_PARTS = (
    "_add_",
    "_append",
    "_copy_",
    "_create_",
    "_duplicate_",
    "_import_",
    "_insert_",
    "_publish",
    "_run_",
    "_solve",
    "_submit",
)

_ENTERPRISE_WRITE_TOOLS = frozenset(
    {
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
    }
)

_GP_OUTPUT_ROOT_TOOLS = frozenset(
    {
        "arcgis_pro_add_feature_class_to_topology",
        "arcgis_pro_add_rule_to_topology",
        "arcgis_pro_create_db_connection",
        "arcgis_pro_create_las_dataset",
        "arcgis_pro_create_mosaic_dataset",
        "arcgis_pro_create_relationship_class",
        "arcgis_pro_create_space_time_cube",
        "arcgis_pro_create_topology",
        "arcgis_pro_curve_fit_forecast",
        "arcgis_pro_delete_relationship_class",
        "arcgis_pro_emerging_hot_spot_analysis",
        "arcgis_pro_evaluate_forecasts_by_location",
        "arcgis_pro_exponential_smoothing_forecast",
        "arcgis_pro_export_topology_errors",
        "arcgis_pro_find_point_clusters",
        "arcgis_pro_forest_based_forecast",
        "arcgis_pro_generalized_linear_regression",
        "arcgis_pro_generate_spatial_weights_matrix",
        "arcgis_pro_geocode_addresses",
        "arcgis_pro_gp_aggregate_polygons",
        "arcgis_pro_gp_analysis_select",
        "arcgis_pro_gp_aspect",
        "arcgis_pro_gp_buffer",
        "arcgis_pro_gp_central_feature",
        "arcgis_pro_gp_check_geometry",
        "arcgis_pro_gp_clip",
        "arcgis_pro_gp_clip_raster",
        "arcgis_pro_gp_cluster_outlier",
        "arcgis_pro_gp_convex_hull",
        "arcgis_pro_gp_copy_feature_class",
        "arcgis_pro_gp_copy_features",
        "arcgis_pro_gp_count_overlapping_features",
        "arcgis_pro_gp_create_feature_class",
        "arcgis_pro_gp_create_feature_dataset",
        "arcgis_pro_gp_create_file_gdb",
        "arcgis_pro_gp_create_random_points",
        "arcgis_pro_gp_create_table",
        "arcgis_pro_gp_directional_distribution",
        "arcgis_pro_gp_dissolve",
        "arcgis_pro_gp_eliminate",
        "arcgis_pro_gp_erase",
        "arcgis_pro_gp_excel_to_table",
        "arcgis_pro_gp_export_features",
        "arcgis_pro_gp_export_table",
        "arcgis_pro_gp_extract_by_attributes",
        "arcgis_pro_gp_extract_by_mask",
        "arcgis_pro_gp_feature_class_to_shapefile",
        "arcgis_pro_gp_feature_to_line",
        "arcgis_pro_gp_feature_to_point",
        "arcgis_pro_gp_feature_to_raster",
        "arcgis_pro_gp_features_to_json",
        "arcgis_pro_gp_forest",
        "arcgis_pro_gp_frequency",
        "arcgis_pro_gp_generate_near_table",
        "arcgis_pro_gp_generate_tessellation",
        "arcgis_pro_gp_gwr",
        "arcgis_pro_gp_hillshade",
        "arcgis_pro_gp_hot_spots",
        "arcgis_pro_gp_identity",
        "arcgis_pro_gp_idw",
        "arcgis_pro_gp_import_csv_to_table",
        "arcgis_pro_gp_intersect",
        "arcgis_pro_gp_json_to_features",
        "arcgis_pro_gp_kernel_density",
        "arcgis_pro_gp_kml_to_layer",
        "arcgis_pro_gp_kriging",
        "arcgis_pro_gp_mean_center",
        "arcgis_pro_gp_merge",
        "arcgis_pro_gp_minimum_bounding_geometry",
        "arcgis_pro_gp_mosaic_to_new_raster",
        "arcgis_pro_gp_multi_distance_spatial_clustering",
        "arcgis_pro_gp_multipart_to_singlepart",
        "arcgis_pro_gp_multiple_ring_buffer",
        "arcgis_pro_gp_nibble",
        "arcgis_pro_gp_optimized_hot_spots",
        "arcgis_pro_gp_ordinary_least_squares",
        "arcgis_pro_gp_point_density",
        "arcgis_pro_gp_points_to_line",
        "arcgis_pro_gp_polygon_to_line",
        "arcgis_pro_gp_polygon_to_raster",
        "arcgis_pro_gp_project",
        "arcgis_pro_gp_project_raster",
        "arcgis_pro_gp_raster_calculator",
        "arcgis_pro_gp_raster_to_polygon",
        "arcgis_pro_gp_reclassify",
        "arcgis_pro_gp_resample",
        "arcgis_pro_gp_slope",
        "arcgis_pro_gp_spatial_join",
        "arcgis_pro_gp_split_by_attributes",
        "arcgis_pro_gp_statistics",
        "arcgis_pro_gp_symmetrical_difference",
        "arcgis_pro_gp_table_select",
        "arcgis_pro_gp_table_to_excel",
        "arcgis_pro_gp_table_to_table",
        "arcgis_pro_gp_topo_to_raster",
        "arcgis_pro_gp_union",
        "arcgis_pro_gp_xy_table_to_point",
        "arcgis_pro_gp_zonal_statistics_as_table",
        "arcgis_pro_multivariate_clustering",
        "arcgis_pro_network_solve_closest_facility",
        "arcgis_pro_network_solve_od_cost_matrix",
        "arcgis_pro_network_solve_route",
        "arcgis_pro_network_solve_service_area",
        "arcgis_pro_raster_basin",
        "arcgis_pro_raster_cell_statistics",
        "arcgis_pro_raster_con",
        "arcgis_pro_raster_copy",
        "arcgis_pro_raster_distance_accumulation",
        "arcgis_pro_raster_euclidean_distance",
        "arcgis_pro_raster_fill",
        "arcgis_pro_raster_flow_accumulation",
        "arcgis_pro_raster_flow_direction",
        "arcgis_pro_raster_focal_statistics",
        "arcgis_pro_raster_optimal_path_as_line",
        "arcgis_pro_raster_set_null",
        "arcgis_pro_raster_snap_pour_point",
        "arcgis_pro_raster_stream_order",
        "arcgis_pro_raster_stream_to_feature",
        "arcgis_pro_raster_watershed",
        "arcgis_pro_remove_feature_class_from_topology",
        "arcgis_pro_remove_rule_from_topology",
        "arcgis_pro_reverse_geocode",
        "arcgis_pro_spatially_constrained_multivariate_clustering",
        "arcgis_pro_time_series_clustering",
        "arcgis_pro_validate_topology",
        "arcgis_pro_verify_output_dataset",
    }
)


def _tokens(name: str) -> list[str]:
    short = name.removeprefix("arcgis_pro_")
    return [part for part in short.split("_") if part]


def _action(name: str) -> str:
    tokens = _tokens(name)
    if tokens and tokens[0] in {"da", "gp", "na"}:
        tokens = tokens[1:]
    known = set(_ACTION_LABELS) | _READ_ACTIONS
    for token in tokens:
        if token in known:
            return token
    return tokens[0] if tokens else "run"


def _object_text(name: str) -> str:
    short = name.removeprefix("arcgis_pro_")
    for key in sorted(_OBJECT_LABELS, key=len, reverse=True):
        if re.search(rf"(?:^|_){re.escape(key)}(?:_|$)", short):
            return _OBJECT_LABELS[key]
    words = short.replace("_", " ")
    return words if words else "操作"


def generated_description(name: str) -> str:
    """Create a concise fallback description for legacy tools without one."""
    action = _ACTION_LABELS.get(_action(name), _action(name).replace("_", " "))
    return f"ArcGIS Pro：{action}{_object_text(name)}。返回可验证的结构化结果；写入和路径限制以服务能力为准。"


def _read_only(name: str) -> bool:
    if name in _WRITE_EXACT:
        return False
    if name in _READ_ONLY_EXACT:
        return True
    if name == "arcgis_pro_validate_analysis_environment":
        return True
    action = _action(name)
    if action in _READ_ACTIONS:
        return True
    return any(
        marker in name
        for marker in (
            "_report_sections",
            "_selection_count",
            "_selection_fids",
            "_spatial_autocorrelation",
            "_average_nearest_neighbor",
            "_test_schema_lock",
            "_relationship_classes",
            "_travel_modes",
        )
    )


def _destructive(name: str) -> bool:
    if name in _NON_DESTRUCTIVE_EXACT:
        return False
    return name in _DESTRUCTIVE_EXACT or any(
        part in f"_{name.removeprefix('arcgis_pro_')}_" for part in _DESTRUCTIVE_PARTS
    )


def _idempotent(name: str) -> bool:
    if _read_only(name):
        return True
    return not any(part in f"_{name.removeprefix('arcgis_pro_')}_" for part in _NON_IDEMPOTENT_PARTS)


def tool_policy(name: str) -> dict[str, Any]:
    """Return conservative, machine-readable policy hints for one tool."""
    read_only = _read_only(name)
    destructive = _destructive(name)
    requires_current = any(
        part in name
        for part in (
            "active_view",
            "window_",
            "open_map_view",
            "open_layout_view",
            "open_report_view",
            "open_layer_table_view",
            "close_views",
            "refresh_layer",
            "clip_map_layers",
            "current_layer_",
            "current_map_",
            "delete_layer_selection",
            "set_active_view_camera",
        )
    ) or name.startswith(
        (
            "arcgis_pro_sdk_context",
            "arcgis_pro_sdk_set_",
            "arcgis_pro_sdk_zoom_",
            "arcgis_pro_sdk_refresh_",
            "arcgis_pro_sdk_open_",
            "arcgis_pro_sdk_create_feature",
            "arcgis_pro_sdk_modify_selected_features",
            "arcgis_pro_sdk_delete_selected_features",
        )
    )
    gates: list[str] = []
    no_write_gate = {
        "arcgis_pro_sdk_acquire_project_lease",
        "arcgis_pro_sdk_release_project_lease",
        "arcgis_pro_sdk_renew_project_lease",
        "arcgis_pro_window_job_cancel",
        "arcgis_pro_window_job_submit",
    }
    if ("_export_" in name or name in {
        "arcgis_pro_save_layer_file",
    }) and name not in _WRITE_EXACT:
        no_write_gate.add(name)
    if not read_only and name not in no_write_gate:
        gates.append("ARCGIS_PRO_MCP_ALLOW_WRITE")
    if destructive:
        gates.append("ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE")
    if (
        "_publish" in name
        or "_sharing" in name
        or name == "arcgis_pro_stage_service_definition"
    ):
        gates.append("ARCGIS_PRO_MCP_ALLOW_PUBLISH")
    if "_cim" in name or name in _CIM_WRITE_TOOLS:
        gates.append("ARCGIS_PRO_MCP_ALLOW_CIM_WRITE")
    if "_export" in name or name in _EXPORT_ROOT_TOOLS or name in {
        "arcgis_pro_save_layer_file",
        "arcgis_pro_save_project_copy",
    }:
        gates.append("ARCGIS_PRO_MCP_EXPORT_ROOT")
    if name == "arcgis_pro_sdk_gp_job_submit":
        gates.extend(
            [
                "ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST",
                "ARCGIS_PRO_MCP_SDK_GP_ENV_ALLOWLIST",
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT",
            ]
        )
    if name == "arcgis_pro_gp_run_tool":
        gates.extend(
            [
                "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP",
                "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST",
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT",
            ]
        )
    if name == "arcgis_pro_current_map_run_analysis":
        gates.append("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT")
    if name in _GP_OUTPUT_ROOT_TOOLS:
        gates.append("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT")
    if name in _INPUT_ROOT_TOOLS:
        gates.append("ARCGIS_PRO_MCP_INPUT_ROOTS")
    if name.startswith("arcgis_pro_sdk_edit_") and not name.endswith("_status"):
        gates.append("ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS")
    if name in {
        "arcgis_pro_sdk_create_feature",
        "arcgis_pro_sdk_modify_selected_features",
        "arcgis_pro_sdk_delete_selected_features",
    }:
        gates.append("ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS")
    if name == "arcgis_pro_sdk_edit_discard":
        gates.append("ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS")
    if name in _ENTERPRISE_WRITE_TOOLS:
        gates.append("ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE")
    if name == "arcgis_pro_create_db_connection":
        gates.append("ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST")
    conditional_gates: list[dict[str, str]] = []
    if name == "arcgis_pro_update_subnetwork":
        conditional_gates.append(
            {
                "when": "all_subnetworks=true",
                "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
            }
        )
    if name == "arcgis_pro_create_las_dataset":
        conditional_gates.append(
            {
                "when": "create_las_prj=ALL_FILES",
                "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
            }
        )
    if name == "arcgis_pro_calculate_las_statistics":
        conditional_gates.extend(
            [
                {
                    "when": "calculation_type=OVERWRITE_EXISTING_STATS",
                    "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
                },
                {
                    "when": "out_report is provided",
                    "gate": "ARCGIS_PRO_MCP_EXPORT_ROOT",
                },
            ]
        )
    if name == "arcgis_pro_import_document":
        conditional_gates.append(
            {
                "when": "log_files=true",
                "gate": "ARCGIS_PRO_MCP_EXPORT_ROOT",
            }
        )
    if name in {"arcgis_pro_reconcile_versions", "arcgis_pro_post_version"}:
        conditional_gates.append(
            {
                "when": "out_log_path is provided",
                "gate": "ARCGIS_PRO_MCP_EXPORT_ROOT",
            }
        )
    if name == "arcgis_pro_add_rasters_to_mosaic_dataset":
        conditional_gates.append(
            {
                "when": "duplicate_items_action=OVERWRITE_DUPLICATES",
                "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
            }
        )
    if name == "arcgis_pro_create_db_connection":
        conditional_gates.extend(
            [
                {
                    "when": "authentication=DATABASE_AUTH and username is omitted",
                    "gate": "ARCGIS_PRO_MCP_DB_USERNAME",
                },
                {
                    "when": "authentication=DATABASE_AUTH and password is omitted",
                    "gate": "ARCGIS_PRO_MCP_DB_PASSWORD",
                },
                {
                    "when": "password is provided inline",
                    "gate": "ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD",
                },
            ]
        )
    if name == "arcgis_pro_raster_build_pyramids":
        conditional_gates.append(
            {
                "when": "pyramid_level=0",
                "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
            }
        )
    if name == "arcgis_pro_network_solve_route":
        conditional_gates.append(
            {
                "when": "overwrite=true",
                "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
            }
        )
    if name in {
        "arcgis_pro_edit_apply",
        "arcgis_pro_edit_workspace_apply",
    }:
        conditional_gates.append(
            {
                "when": "operations contains delete",
                "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
            }
        )
    if name in {
        "arcgis_pro_export_mapx",
        "arcgis_pro_save_layer_file",
    }:
        conditional_gates.append(
            {
                "when": "output exists and overwrite=true",
                "gate": "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
            }
        )
    return {
        "read_only": read_only,
        "destructive": destructive,
        "idempotent": _idempotent(name),
        "open_world": any(part in name for part in ("portal", "publish", "sharing"))
        or name in {"arcgis_pro_create_db_connection", "arcgis_pro_stage_service_definition"},
        "requires_current": requires_current,
        "requires_sdk_bridge": name.startswith("arcgis_pro_sdk_")
        and name != "arcgis_pro_sdk_bridge_status",
        "gates": gates,
        "conditional_gates": conditional_gates,
    }


def _coerce_structured_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return redact_text_values(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {"ok": True, "result": redact_text_values(value)}
        if isinstance(decoded, dict):
            return redact_text_values(decoded)
        return {"ok": True, "result": redact_text_values(decoded)}
    if value is None:
        return {"ok": True}
    return {"ok": True, "result": redact_text_values(value)}


def _structured_callable(fn: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return _coerce_structured_result(await fn(*args, **kwargs))
            except Exception as exc:
                raise RuntimeError(safe_error(exc, 2000)) from exc

        wrapper: Any = async_wrapper
    else:
        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return _coerce_structured_result(fn(*args, **kwargs))
            except Exception as exc:
                raise RuntimeError(safe_error(exc, 2000)) from exc

        wrapper = sync_wrapper

    annotations = dict(getattr(fn, "__annotations__", {}))
    annotations["return"] = dict[str, Any]
    wrapper.__annotations__ = annotations
    try:
        signature = inspect.signature(fn)
        wrapper.__signature__ = signature.replace(return_annotation=dict[str, Any])
    except (TypeError, ValueError):
        pass
    wrapper.__arcgis_structured_output__ = True
    return wrapper


def finalize_tool_registry(mcp: Any) -> None:
    """Upgrade every registered tool without changing direct Python call behavior."""
    manager = mcp._tool_manager
    for old in list(manager.list_tools()):
        name = old.name
        policy = tool_policy(name)
        description = (old.description or "").strip() or generated_description(name)
        title = old.title or name.removeprefix("arcgis_pro_").replace("_", " ").title()
        annotations = ToolAnnotations(
            title=title,
            readOnlyHint=policy["read_only"],
            destructiveHint=policy["destructive"],
            idempotentHint=policy["idempotent"],
            openWorldHint=policy["open_world"],
        )
        meta = dict(old.meta or {})
        meta["arcgisPro"] = {
            "requiresCurrent": policy["requires_current"],
            "requiresSdkBridge": policy["requires_sdk_bridge"],
            "gates": policy["gates"],
        }
        wrapper = _structured_callable(old.fn)
        replacement = type(old).from_function(
            wrapper,
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=old.icons,
            meta=meta,
            structured_output=True,
        )
        manager._tools[name] = replacement


def registered_tool_info(mcp: Any, name: str = "") -> dict[str, Any]:
    """Return the discoverable schema and policy for one tool or the full catalog."""
    tools = list(mcp._tool_manager.list_tools())
    if name:
        tool = mcp._tool_manager.get_tool(name)
        if tool is None:
            raise RuntimeError(f"未知工具：{name}")
        tools = [tool]
    items = []
    for tool in tools:
        policy = tool_policy(tool.name)
        items.append(
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "input_schema": tool.parameters,
                "output_schema": tool.output_schema,
                "policy": policy,
            }
        )
    return {"ok": True, "tool_count": len(items), "tools": items}
