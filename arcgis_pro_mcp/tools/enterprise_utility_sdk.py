"""Enterprise GDB versioning/maintenance, utility network, and ArcGIS Pro SDK bridge tools."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool

SERVER_EXPORTS: tuple[str, ...] = ("_sdk_edit_command", "_utility_network_source",)

@tool(
    name="arcgis_pro_list_versions",
    description="列出受控 .sde 或精确白名单 FeatureServer 中当前用户可见的版本及属性。",
)
def arcgis_pro_list_versions(workspace_path: str) -> str:
    result = _srv.enterprise_gdb.list_versions(_srv._arcpy(), workspace_path)
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
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
    result = _srv.enterprise_gdb.create_version(
        _srv._arcpy(),
        workspace_path,
        parent_version,
        version_name,
        access_permission=access_permission,
        description=description,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
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
    kind = _srv._strict_member_type(member_type, {"LAYER", "TABLE"})
    arcpy, project, opened_path = _srv._open_project(aprx_path)
    _map_obj, member = _srv._get_map_member(project, map_name, member_name, kind)
    result = _srv.enterprise_gdb.change_version(
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
    return _srv._json_dumps(result)


@tool(
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
    result = _srv.enterprise_gdb.reconcile_versions(
        _srv._arcpy(),
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
    return _srv._json_dumps(result)


@tool(
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
    result = _srv.enterprise_gdb.post_version(
        _srv._arcpy(),
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
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_delete_version",
    description="删除精确命名版本并复查其消失；需要企业写门、破坏性门及版本名精确回显。",
)
def arcgis_pro_delete_version(
    workspace_path: str,
    version_name: str,
    confirm_version_name: str = "",
) -> str:
    result = _srv.enterprise_gdb.delete_version(
        _srv._arcpy(),
        workspace_path,
        version_name,
        confirm_version_name=confirm_version_name,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_register_as_versioned",
    description="将受控 .sde 数据集注册为版本化并通过 Describe 复核；需要企业写门。",
)
def arcgis_pro_register_as_versioned(
    dataset_path: str,
    edit_to_base: str = "NO_EDITS_TO_BASE",
) -> str:
    result = _srv.enterprise_gdb.register_as_versioned(
        _srv._arcpy(),
        dataset_path,
        edit_to_base=edit_to_base,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
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
    result = _srv.enterprise_gdb.unregister_as_versioned(
        _srv._arcpy(),
        dataset_path,
        keep_edit=keep_edit,
        compress_default=compress_default,
        confirm_dataset_path=confirm_dataset_path,
        confirm_discard_edits=confirm_discard_edits,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_dataset_maintenance_info",
    description="读取受控 .sde 数据集的索引、编辑者追踪、GlobalID 和版本化状态。",
)
def arcgis_pro_dataset_maintenance_info(dataset_path: str) -> str:
    result = _srv.schema_maintenance.dataset_maintenance_info(_srv._arcpy(), dataset_path)
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
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
    result = _srv.schema_maintenance.add_index(
        _srv._arcpy(),
        dataset_path,
        fields,
        index_name,
        unique=unique,
        ascending=ascending,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_remove_index",
    description="删除受控 .sde 数据集的一个精确索引并复核；需要企业写门、破坏性门及索引名回显。",
)
def arcgis_pro_remove_index(
    dataset_path: str,
    index_name: str,
    confirm_index_name: str = "",
) -> str:
    result = _srv.schema_maintenance.remove_index(
        _srv._arcpy(),
        dataset_path,
        index_name,
        confirm_index_name=confirm_index_name,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_rebuild_indexes",
    description="重建企业地理数据库指定数据集或系统表索引；数据集名必须相对 .sde workspace。",
)
def arcgis_pro_rebuild_indexes(
    workspace_path: str,
    datasets: list[str],
    include_system: bool = False,
    delta_only: str = "ONLY_DELTAS",
) -> str:
    result = _srv.schema_maintenance.rebuild_indexes(
        _srv._arcpy(),
        workspace_path,
        datasets,
        include_system=include_system,
        delta_only=delta_only,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
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
    result = _srv.schema_maintenance.analyze_datasets(
        _srv._arcpy(),
        workspace_path,
        datasets,
        include_system=include_system,
        analyze_base=analyze_base,
        analyze_delta=analyze_delta,
        analyze_archive=analyze_archive,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
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
    result = _srv.schema_maintenance.enable_editor_tracking(
        _srv._arcpy(),
        dataset_path,
        creator_field,
        creation_date_field,
        last_editor_field,
        last_edit_date_field,
        add_fields=add_fields,
        record_dates_in=record_dates_in,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
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
    result = _srv.schema_maintenance.disable_editor_tracking(
        _srv._arcpy(),
        dataset_path,
        disable_creator=disable_creator,
        disable_creation_date=disable_creation_date,
        disable_last_editor=disable_last_editor,
        disable_last_edit_date=disable_last_edit_date,
        confirm_dataset_path=confirm_dataset_path,
    )
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_add_global_ids",
    description="为最多 50 个受控 .sde 数据集补充 GlobalID 并逐一复核；已有 GlobalID 时跳过。",
)
def arcgis_pro_add_global_ids(dataset_paths: list[str]) -> str:
    result = _srv.schema_maintenance.add_global_ids(_srv._arcpy(), dataset_paths)
    result["ok"] = True
    return _srv._json_dumps(result)


@tool(
    name="arcgis_pro_sdk_bridge_status",
    description="发现可选 ArcGIS Pro SDK Add-In 并读取脱敏状态；不会返回 bearer token 或 lease secret。",
)
def arcgis_pro_sdk_bridge_status(process_id: int = 0) -> str:
    return _srv._json_dumps(_srv.sdk_bridge.bridge_status(process_id))


@tool(
    name="arcgis_pro_sdk_acquire_project_lease",
    description="为一个精确已保存 .aprx 获取 SDK 独占短租约，返回不透明的进程内 session 引用。",
)
def arcgis_pro_sdk_acquire_project_lease(
    expected_project_uri: str,
    process_id: int = 0,
    ttl_seconds: int = 45,
) -> str:
    return _srv._json_dumps(
        _srv.sdk_bridge.acquire_lease(
            expected_project_uri,
            process_id=process_id,
            ttl_seconds=ttl_seconds,
        )
    )


@tool(
    name="arcgis_pro_sdk_renew_project_lease",
    description="续期 SDK 工程租约；工程切换、过期或 Add-In 重启会失败关闭。",
)
def arcgis_pro_sdk_renew_project_lease(sdk_session_ref: str) -> str:
    return _srv._json_dumps(_srv.sdk_bridge.renew_lease(sdk_session_ref))


@tool(
    name="arcgis_pro_sdk_release_project_lease",
    description="释放 SDK 工程租约并销毁 MCP 进程内的不透明会话引用。",
)
def arcgis_pro_sdk_release_project_lease(sdk_session_ref: str) -> str:
    return _srv._json_dumps(_srv.sdk_bridge.release_lease(sdk_session_ref))


@tool(
    name="arcgis_pro_sdk_wait_events",
    description="长轮询 SDK 原生事件：活动视图、相机、选择、DrawComplete、编辑和工程开关。",
)
def arcgis_pro_sdk_wait_events(
    sdk_session_ref: str,
    after: int = 0,
    limit: int = 128,
    wait_ms: int = 30_000,
) -> str:
    return _srv._json_dumps(
        _srv.sdk_bridge.wait_events(
            sdk_session_ref,
            after=after,
            limit=limit,
            wait_ms=wait_ms,
        )
    )


@tool(
    name="arcgis_pro_sdk_context",
    description="读取 SDK 原生活动视图、相机、活动图层、选择摘要、时间与 CAS generations。",
)
def arcgis_pro_sdk_context(sdk_session_ref: str) -> str:
    return _srv._json_dumps(_srv.sdk_bridge.context_snapshot(sdk_session_ref))


@tool(
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
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.set_camera(
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


@tool(
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
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.zoom_layer(
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


@tool(
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
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.refresh_view(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            clear_cache=clear_cache,
            wait_milliseconds=wait_milliseconds,
            confirm=confirm,
        )
    )


@tool(
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
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.set_active_time(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            start_time=start_time,
            end_time=end_time,
            confirm=confirm,
        )
    )


@tool(
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
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.open_table(
            sdk_session_ref,
            expected_map_uri,
            expected_context_generation,
            table_uri,
            confirm=confirm,
        )
    )


@tool(
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
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.create_feature(
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


@tool(
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
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.modify_selected_features(
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


@tool(
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
    _srv.require_allow_destructive()
    return _srv._json_dumps(
        _srv.sdk_bridge.delete_selected_features(
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


@tool(
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
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.start_gp_job(
            sdk_session_ref,
            tool_name,
            parameters,
            environments,
            confirm=confirm,
        )
    )


@tool(
    name="arcgis_pro_sdk_gp_job_status",
    description="读取 SDK 异步 GP job 的排队、进度、结果或错误状态。",
)
def arcgis_pro_sdk_gp_job_status(sdk_session_ref: str, job_id: str) -> str:
    return _srv._json_dumps(_srv.sdk_bridge.gp_job_status(sdk_session_ref, job_id))


@tool(
    name="arcgis_pro_sdk_gp_job_cancel",
    description="通过 SDK cancellation token 请求取消一个异步 GP job；需要写入开关和 confirm=true。",
)
def arcgis_pro_sdk_gp_job_cancel(
    sdk_session_ref: str,
    job_id: str,
    confirm: bool = False,
) -> str:
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.cancel_gp_job(
            sdk_session_ref,
            job_id,
            confirm=confirm,
        )
    )


@tool(
    name="arcgis_pro_sdk_edit_status",
    description="读取 SDK 原生未保存编辑及活动地图 Undo/Redo 可用状态。",
)
def arcgis_pro_sdk_edit_status(sdk_session_ref: str) -> str:
    return _srv._json_dumps(_srv.sdk_bridge.edit_status(sdk_session_ref))


def _sdk_edit_command(
    sdk_session_ref: str,
    command: str,
    expected_edit_generation: int,
    expected_map_uri: str,
    confirm: bool,
) -> str:
    _srv.require_allow_write()
    return _srv._json_dumps(
        _srv.sdk_bridge.edit_command(
            sdk_session_ref,
            command,
            expected_edit_generation=expected_edit_generation,
            expected_map_uri=expected_map_uri,
            confirm=confirm,
        )
    )


@tool(
    name="arcgis_pro_sdk_edit_undo",
    description="执行活动地图原生 Undo；需要 SDK 编辑开关、有效租约和 confirm=true。",
)
def arcgis_pro_sdk_edit_undo(
    sdk_session_ref: str,
    expected_edit_generation: int,
    expected_map_uri: str,
    confirm: bool = False,
) -> str:
    return _srv._sdk_edit_command(
        sdk_session_ref,
        "undo",
        expected_edit_generation,
        expected_map_uri,
        confirm,
    )


@tool(
    name="arcgis_pro_sdk_edit_redo",
    description="执行活动地图原生 Redo；需要 SDK 编辑开关、有效租约和 confirm=true。",
)
def arcgis_pro_sdk_edit_redo(
    sdk_session_ref: str,
    expected_edit_generation: int,
    expected_map_uri: str,
    confirm: bool = False,
) -> str:
    return _srv._sdk_edit_command(
        sdk_session_ref,
        "redo",
        expected_edit_generation,
        expected_map_uri,
        confirm,
    )


@tool(
    name="arcgis_pro_sdk_edit_save",
    description="保存当前工程中的待提交数据编辑；需要 SDK 编辑开关和 confirm=true。",
)
def arcgis_pro_sdk_edit_save(
    sdk_session_ref: str,
    expected_edit_generation: int,
    confirm: bool = False,
) -> str:
    return _srv._sdk_edit_command(
        sdk_session_ref,
        "save",
        expected_edit_generation,
        "",
        confirm,
    )


@tool(
    name="arcgis_pro_sdk_edit_discard",
    description="丢弃当前工程中的全部待保存数据编辑；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_sdk_edit_discard(
    sdk_session_ref: str,
    expected_edit_generation: int,
    confirm: bool = False,
    confirm_discard_all: bool = False,
) -> str:
    _srv.require_allow_destructive()
    return _srv._json_dumps(
        _srv.sdk_bridge.edit_command(
            sdk_session_ref,
            "discard",
            expected_edit_generation=expected_edit_generation,
            confirm=confirm,
            confirm_discard_all=confirm_discard_all,
        )
    )


def _utility_network_source(
    project: Any,
    map_name: str,
    source_kind: str,
    source_identifier: str,
) -> Any:
    kind = source_kind.strip().upper()
    if kind == "PATH":
        return _srv.validate_input_path_optional(source_identifier, "source_identifier")
    if kind == "LAYER":
        return _srv._find_layer(_srv._get_map(project, map_name), source_identifier)
    raise RuntimeError("source_kind 须为 PATH 或 LAYER")


@tool(
    name="arcgis_pro_utility_network_info",
    description="读取 Utility Network 版本、拓扑状态、domain networks 和 tiers。",
)
def arcgis_pro_utility_network_info(
    aprx_path: str,
    map_name: str,
    source_kind: str,
    source_identifier: str,
) -> str:
    arcpy, project, path = _srv._open_project(aprx_path)
    result = _srv.utility_network.describe_utility_network(
        arcpy,
        _srv._utility_network_source(project, map_name, source_kind, source_identifier),
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
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
    arcpy, project, path = _srv._open_project(aprx_path)
    result = _srv.utility_network.validate_network_topology(
        arcpy,
        _srv._utility_network_source(project, map_name, source_kind, source_identifier),
        expected_network=expected_network,
        extent=extent,
        extent_keyword=extent_keyword,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
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
    arcpy, project, path = _srv._open_project(aprx_path)
    result = _srv.utility_network.trace_named_configuration(
        arcpy,
        _srv._utility_network_source(project, map_name, source_kind, source_identifier),
        trace_type,
        trace_config_name,
        starting_points=starting_points,
        barriers=barriers,
        selection_type=selection_type,
        clear_previous_results=clear_previous_results,
        trace_name=trace_name,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
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
    arcpy, project, path = _srv._open_project(aprx_path)
    result = _srv.utility_network.update_subnetwork(
        arcpy,
        _srv._utility_network_source(project, map_name, source_kind, source_identifier),
        domain_network,
        tier,
        subnetwork_name=subnetwork_name,
        all_subnetworks=all_subnetworks,
        continue_on_failure=continue_on_failure,
        expected_network=expected_network,
        confirm_all=confirm_all,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)


@tool(
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
    arcpy, project, path = _srv._open_project(aprx_path)
    result = _srv.utility_network.export_subnetwork(
        arcpy,
        _srv._utility_network_source(project, map_name, source_kind, source_identifier),
        domain_network,
        tier,
        subnetwork_name,
        output_json,
        include_geometry=include_geometry,
        include_domain_descriptions=include_domain_descriptions,
    )
    result.update({"ok": True, "aprx_path": path})
    return _srv._json_dumps(result)
