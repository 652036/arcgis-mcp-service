"""Transactional editing, domains, subtypes, attachments, topology, and data integrity tools."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.tools._registry import SERVER_PROXY as _srv
from arcgis_pro_mcp.tools._registry import tool


@tool(
    name="arcgis_pro_gp_validate_topology",
    description="",
)
def arcgis_pro_gp_validate_topology(in_topology: str) -> str:
    arcpy = _srv._arcpy()
    _srv.metadata.validate_topology(arcpy, in_topology)
    return _srv._json_dumps({"ok": True, "in_topology": _srv.normalize_path(in_topology)})


@tool(
    name="arcgis_pro_edit_preflight",
    description="预检同一数据集的一组 update/insert/delete 操作，锁定命中 OID 与数量并签发五分钟编辑令牌。",
)
def arcgis_pro_edit_preflight(
    dataset_path: str,
    operations: list[dict[str, Any]],
) -> str:
    return _srv._json_dumps(_srv.editing.edit_preflight(_srv._arcpy(), dataset_path, operations))


@tool(
    name="arcgis_pro_edit_apply",
    description="在单次 arcpy.da.Editor 会话中原子执行已预检的编辑；失败时回滚，不跨 MCP 请求保留游标。",
)
def arcgis_pro_edit_apply(
    dataset_path: str,
    operations: list[dict[str, Any]],
    edit_token: str,
) -> str:
    return _srv._json_dumps(_srv.editing.edit_apply(_srv._arcpy(), dataset_path, operations, edit_token))


@tool(
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
    return _srv._json_dumps(_srv.editing.edit_preflight(_srv._arcpy(), dataset_path, [operation]))


@tool(
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
    return _srv._json_dumps(
        _srv.editing.edit_apply(_srv._arcpy(), dataset_path, [operation], edit_token)
    )


@tool(
    name="arcgis_pro_edit_workspace_preflight",
    description="预检同一工作空间中多个数据集的更新、插入、删除或几何修改并签发单一事务令牌。",
)
def arcgis_pro_edit_workspace_preflight(
    operations: list[dict[str, Any]],
) -> str:
    return _srv._json_dumps(_srv.editing.workspace_edit_preflight(_srv._arcpy(), operations))


@tool(
    name="arcgis_pro_edit_workspace_apply",
    description="在一个 arcpy.da.Editor 事务内原子执行多数据集预检计划；任一计数变化即全部回滚。",
)
def arcgis_pro_edit_workspace_apply(
    operations: list[dict[str, Any]],
    edit_token: str,
) -> str:
    return _srv._json_dumps(
        _srv.editing.workspace_edit_apply(_srv._arcpy(), operations, edit_token)
    )


@tool(
    name="arcgis_pro_list_domains",
    description="列出工作空间中的编码值域和范围域及其策略。",
)
def arcgis_pro_list_domains(workspace_path: str) -> str:
    return _srv._json_dumps(_srv.dataset_management.list_domains(_srv._arcpy(), workspace_path))


@tool(
    name="arcgis_pro_list_subtypes",
    description="列出数据集子类型、默认子类型以及逐字段默认值和值域。",
)
def arcgis_pro_list_subtypes(dataset_path: str) -> str:
    return _srv._json_dumps(_srv.dataset_management.list_subtypes(_srv._arcpy(), dataset_path))


@tool(
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
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_create_domain(
                _srv._arcpy(),
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


@tool(
    name="arcgis_pro_delete_domain",
    description="删除指定属性域；需要破坏性开关，且 confirm_domain_name 必须精确匹配。",
)
def arcgis_pro_delete_domain(
    workspace_path: str,
    domain_name: str,
    confirm_domain_name: str,
) -> str:
    _srv.require_allow_destructive()
    if confirm_domain_name != domain_name:
        raise RuntimeError("confirm_domain_name 必须与 domain_name 完全一致")
    return _srv._json_dumps(
        {
            "ok": True,
            "domain_name": domain_name,
            "messages": _srv.dataset_management.run_delete_domain(
                _srv._arcpy(), workspace_path, domain_name
            ),
        }
    )


@tool(
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
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_alter_domain(
                _srv._arcpy(),
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


@tool(
    name="arcgis_pro_add_coded_value_to_domain",
    description="向编码值域添加一个代码与描述。",
)
def arcgis_pro_add_coded_value_to_domain(
    workspace_path: str,
    domain_name: str,
    code: Any,
    description: str,
) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_add_coded_value_to_domain(
                _srv._arcpy(), workspace_path, domain_name, code, description
            )
        }
    )


@tool(
    name="arcgis_pro_delete_coded_value_from_domain",
    description="从编码值域移除一个精确代码；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_delete_coded_value_from_domain(
    workspace_path: str,
    domain_name: str,
    code: Any,
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除编码值")
    return _srv._json_dumps(
        {
            "ok": True,
            "messages": _srv.dataset_management.run_delete_coded_value_from_domain(
                _srv._arcpy(), workspace_path, domain_name, code
            ),
        }
    )


@tool(
    name="arcgis_pro_set_range_domain",
    description="设置范围域的最小值与最大值。",
)
def arcgis_pro_set_range_domain(
    workspace_path: str,
    domain_name: str,
    minimum_value: Any,
    maximum_value: Any,
) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_set_range_domain(
                _srv._arcpy(), workspace_path, domain_name, minimum_value, maximum_value
            )
        }
    )


@tool(
    name="arcgis_pro_assign_domain_to_field",
    description="把属性域分配给字段，可限定一个子类型代码。",
)
def arcgis_pro_assign_domain_to_field(
    dataset_path: str,
    field_name: str,
    domain_name: str,
    subtype_code: int | None = None,
) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_assign_domain_to_field(
                _srv._arcpy(), dataset_path, field_name, domain_name, subtype_code
            )
        }
    )


@tool(
    name="arcgis_pro_remove_domain_from_field",
    description="移除字段的属性域分配；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_remove_domain_from_field(
    dataset_path: str,
    field_name: str,
    subtype_code: int | None = None,
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除字段值域")
    return _srv._json_dumps(
        {
            "ok": True,
            "messages": _srv.dataset_management.run_remove_domain_from_field(
                _srv._arcpy(), dataset_path, field_name, subtype_code
            ),
        }
    )


@tool(
    name="arcgis_pro_set_subtype_field",
    description="把一个字段设为数据集的子类型字段。",
)
def arcgis_pro_set_subtype_field(dataset_path: str, field_name: str) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_set_subtype_field(
                _srv._arcpy(), dataset_path, field_name
            )
        }
    )


@tool(
    name="arcgis_pro_clear_subtype_field",
    description="清除数据集的子类型字段；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_clear_subtype_field(dataset_path: str, confirm: bool = False) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能清除子类型字段")
    return _srv._json_dumps(
        {
            "ok": True,
            "messages": _srv.dataset_management.run_clear_subtype_field(
                _srv._arcpy(), dataset_path
            ),
        }
    )


@tool(
    name="arcgis_pro_add_subtype",
    description="向数据集添加一个子类型代码和名称。",
)
def arcgis_pro_add_subtype(
    dataset_path: str,
    subtype_code: int,
    subtype_name: str,
) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_add_subtype(
                _srv._arcpy(), dataset_path, subtype_code, subtype_name
            )
        }
    )


@tool(
    name="arcgis_pro_remove_subtype",
    description="移除精确子类型代码；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_remove_subtype(
    dataset_path: str,
    subtype_code: int,
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除子类型")
    return _srv._json_dumps(
        {
            "ok": True,
            "messages": _srv.dataset_management.run_remove_subtype(
                _srv._arcpy(), dataset_path, subtype_code
            ),
        }
    )


@tool(
    name="arcgis_pro_set_default_subtype",
    description="设置数据集的默认子类型代码。",
)
def arcgis_pro_set_default_subtype(dataset_path: str, subtype_code: int) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_set_default_subtype(
                _srv._arcpy(), dataset_path, subtype_code
            )
        }
    )


@tool(
    name="arcgis_pro_attachments_info",
    description="读取数据集是否启用附件以及关联关系类名称。",
)
def arcgis_pro_attachments_info(dataset_path: str) -> str:
    return _srv._json_dumps(_srv.dataset_management.attachments_info(_srv._arcpy(), dataset_path))


@tool(
    name="arcgis_pro_enable_attachments",
    description="为数据集启用附件；需要写入开关和方案锁。",
)
def arcgis_pro_enable_attachments(dataset_path: str) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_enable_attachments(
                _srv._arcpy(), dataset_path
            )
        }
    )


@tool(
    name="arcgis_pro_disable_attachments",
    description="禁用并移除数据集附件能力；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_disable_attachments(dataset_path: str, confirm: bool = False) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能禁用附件")
    return _srv._json_dumps(
        {
            "ok": True,
            "messages": _srv.dataset_management.run_disable_attachments(
                _srv._arcpy(), dataset_path
            ),
        }
    )


@tool(
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
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_add_attachments(
                _srv._arcpy(),
                dataset_path,
                in_join_field,
                match_table,
                match_join_field,
                match_path_field,
                working_folder,
            )
        }
    )


@tool(
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
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除附件")
    return _srv._json_dumps(
        {
            "ok": True,
            "messages": _srv.dataset_management.run_remove_attachments(
                _srv._arcpy(),
                dataset_path,
                in_join_field,
                match_table,
                match_join_field,
                match_name_field,
            ),
        }
    )


@tool(
    name="arcgis_pro_relationship_classes",
    description="读取与数据集相关的关系类名称。",
)
def arcgis_pro_relationship_classes(dataset_path: str) -> str:
    return _srv._json_dumps(
        _srv.dataset_management.relationship_classes(_srv._arcpy(), dataset_path)
    )


@tool(
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
    return _srv._json_dumps(
        {
            "output_relationship_class": out_relationship_class,
            "messages": _srv.dataset_management.run_create_relationship_class(
                _srv._arcpy(),
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


@tool(
    name="arcgis_pro_delete_relationship_class",
    description="删除 GP 输出根内的关系类；需要破坏性开关且确认路径必须完全一致。",
)
def arcgis_pro_delete_relationship_class(
    relationship_class_path: str,
    confirm_relationship_class_path: str,
) -> str:
    _srv.require_allow_destructive()
    if confirm_relationship_class_path != relationship_class_path:
        raise RuntimeError("confirm_relationship_class_path 必须与目标路径完全一致")
    messages = _srv.dataset_management.run_delete_relationship_class(
        _srv._arcpy(), relationship_class_path
    )
    return _srv._json_dumps(
        {
            "ok": True,
            "relationship_class_path": relationship_class_path,
            "messages": messages,
        }
    )


@tool(
    name="arcgis_pro_topology_info",
    description="读取拓扑名称、聚类容差和参与要素类。",
)
def arcgis_pro_topology_info(topology_path: str) -> str:
    return _srv._json_dumps(_srv.dataset_management.topology_info(_srv._arcpy(), topology_path))


@tool(
    name="arcgis_pro_create_topology",
    description="在 GP 输出根中的要素数据集内创建拓扑并核验输出。",
)
def arcgis_pro_create_topology(
    feature_dataset: str,
    topology_name: str,
    cluster_tolerance: float | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.dataset_management.run_create_topology(
            _srv._arcpy(), feature_dataset, topology_name, cluster_tolerance
        )
    )


@tool(
    name="arcgis_pro_add_feature_class_to_topology",
    description="把要素类加入拓扑，并设置 XY/Z rank。",
)
def arcgis_pro_add_feature_class_to_topology(
    topology_path: str,
    feature_class: str,
    xy_rank: int = 1,
    z_rank: int = 1,
) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_add_feature_class_to_topology(
                _srv._arcpy(), topology_path, feature_class, xy_rank, z_rank
            )
        }
    )


@tool(
    name="arcgis_pro_add_rule_to_topology",
    description="向拓扑添加一个精确规则，可指定目标要素类。",
)
def arcgis_pro_add_rule_to_topology(
    topology_path: str,
    rule_type: str,
    origin_feature_class: str,
    destination_feature_class: str = "",
) -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_add_rule_to_topology(
                _srv._arcpy(),
                topology_path,
                rule_type,
                origin_feature_class,
                destination_feature_class,
            )
        }
    )


@tool(
    name="arcgis_pro_remove_rule_from_topology",
    description="从拓扑移除一个精确规则；需要破坏性开关及 confirm_rule_name 精确匹配。",
)
def arcgis_pro_remove_rule_from_topology(
    topology_path: str,
    rule_name: str,
    confirm_rule_name: str,
) -> str:
    _srv.require_allow_destructive()
    if confirm_rule_name != rule_name:
        raise RuntimeError("confirm_rule_name 必须与 rule_name 完全一致")
    return _srv._json_dumps(
        {
            "ok": True,
            "messages": _srv.dataset_management.run_remove_rule_from_topology(
                _srv._arcpy(), topology_path, rule_name
            ),
        }
    )


@tool(
    name="arcgis_pro_remove_feature_class_from_topology",
    description="从拓扑移除参与要素类；需要破坏性开关及 confirm=true。",
)
def arcgis_pro_remove_feature_class_from_topology(
    topology_path: str,
    feature_class: str,
    confirm: bool = False,
) -> str:
    _srv.require_allow_destructive()
    if not confirm:
        raise RuntimeError("必须设置 confirm=true 才能移除拓扑参与类")
    return _srv._json_dumps(
        {
            "ok": True,
            "messages": _srv.dataset_management.run_remove_feature_class_from_topology(
                _srv._arcpy(), topology_path, feature_class
            ),
        }
    )


@tool(
    name="arcgis_pro_validate_topology",
    description="验证拓扑的全部范围或指定 extent，并返回 GP 消息。",
)
def arcgis_pro_validate_topology(topology_path: str, extent: str = "") -> str:
    return _srv._json_dumps(
        {
            "messages": _srv.dataset_management.run_validate_topology(
                _srv._arcpy(), topology_path, extent
            )
        }
    )


@tool(
    name="arcgis_pro_export_topology_errors",
    description="把拓扑错误导出到 GP 输出根，并报告所创建的点、线、面错误类。",
)
def arcgis_pro_export_topology_errors(
    topology_path: str,
    output_path: str,
    output_name: str,
) -> str:
    return _srv._json_dumps(
        _srv.dataset_management.run_export_topology_errors(
            _srv._arcpy(), topology_path, output_path, output_name
        )
    )


@tool(
    name="arcgis_pro_list_attribute_rules",
    description="读取数据集的属性规则清单、类型、触发器、子类型和校验属性，不修改方案。",
)
def arcgis_pro_list_attribute_rules(dataset_path: str) -> str:
    return _srv._json_dumps(_srv.data_integrity.list_attribute_rules(_srv._arcpy(), dataset_path))


@tool(
    name="arcgis_pro_export_attribute_rules",
    description="将属性规则导出为 EXPORT_ROOT 内的新 CSV；拒绝覆盖已有文件。",
)
def arcgis_pro_export_attribute_rules(dataset_path: str, output_csv: str) -> str:
    return _srv._json_dumps(
        _srv.data_integrity.export_attribute_rules(_srv._arcpy(), dataset_path, output_csv)
    )


@tool(
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
    return _srv._json_dumps(
        _srv.data_integrity.import_attribute_rules(
            _srv._arcpy(),
            dataset_path,
            csv_files,
            expected_rule_names,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
        )
    )


@tool(
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
    return _srv._json_dumps(
        _srv.data_integrity.add_attribute_rule(
            _srv._arcpy(),
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


@tool(
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
    return _srv._json_dumps(
        _srv.data_integrity.delete_attribute_rules(
            _srv._arcpy(),
            dataset_path,
            rule_names,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
            rule_type=rule_type,
        )
    )


@tool(
    name="arcgis_pro_list_field_groups",
    description="读取条件值字段组、字段顺序和 restrictive 状态，不修改方案。",
)
def arcgis_pro_list_field_groups(dataset_path: str) -> str:
    return _srv._json_dumps(_srv.data_integrity.list_field_groups(_srv._arcpy(), dataset_path))


@tool(
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
    return _srv._json_dumps(
        _srv.data_integrity.create_field_group(
            _srv._arcpy(),
            dataset_path,
            field_group_name,
            fields,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
            restrictive=restrictive,
        )
    )


@tool(
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
    return _srv._json_dumps(
        _srv.data_integrity.delete_field_group(
            _srv._arcpy(),
            dataset_path,
            field_group_name,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
        )
    )


@tool(
    name="arcgis_pro_list_contingent_values",
    description="读取条件值清单，可按精确字段组和 subtype code 筛选，不修改方案。",
)
def arcgis_pro_list_contingent_values(
    dataset_path: str,
    field_group_name: str = "",
    subtype_code: int | None = None,
) -> str:
    return _srv._json_dumps(
        _srv.data_integrity.list_contingent_values(
            _srv._arcpy(),
            dataset_path,
            field_group_name,
            subtype_code,
        )
    )


@tool(
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
    return _srv._json_dumps(
        _srv.data_integrity.add_contingent_value(
            _srv._arcpy(),
            dataset_path,
            field_group_name,
            values,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
            subtype=subtype,
            retired=retired,
        )
    )


@tool(
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
    return _srv._json_dumps(
        _srv.data_integrity.remove_contingent_value(
            _srv._arcpy(),
            dataset_path,
            contingent_value_id,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
        )
    )


@tool(
    name="arcgis_pro_export_contingent_values",
    description="将字段组和条件值导出为 EXPORT_ROOT 内两个新 CSV；任一已存在即拒绝执行。",
)
def arcgis_pro_export_contingent_values(
    dataset_path: str,
    field_groups_csv: str,
    contingent_values_csv: str,
) -> str:
    return _srv._json_dumps(
        _srv.data_integrity.export_contingent_values(
            _srv._arcpy(),
            dataset_path,
            field_groups_csv,
            contingent_values_csv,
        )
    )


@tool(
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
    return _srv._json_dumps(
        _srv.data_integrity.import_contingent_values(
            _srv._arcpy(),
            dataset_path,
            field_groups_csv,
            contingent_values_csv,
            import_type,
            expected_dataset=expected_dataset,
            confirmation=confirmation,
        )
    )
