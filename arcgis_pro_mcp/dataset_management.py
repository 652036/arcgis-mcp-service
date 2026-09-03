"""Safe dataset schema, domain, subtype, attachment, relationship, and topology helpers.

This module deliberately accepts an ``arcpy`` object from the caller so importing
the package remains possible outside ArcGIS Pro.  It contains no MCP registration;
``server.py`` can expose the helpers with whichever public tool schema is chosen.
"""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)

_FIELD_TYPES = frozenset(
    {
        "SHORT",
        "LONG",
        "BIGINTEGER",
        "FLOAT",
        "DOUBLE",
        "TEXT",
        "DATE",
        "DATEONLY",
        "TIMEONLY",
        "TIMESTAMPOFFSET",
        "BLOB",
        "GUID",
    }
)
_DOMAIN_TYPES = frozenset({"CODED", "RANGE"})
_SPLIT_POLICIES = frozenset({"DEFAULT", "DUPLICATE", "GEOMETRY_RATIO"})
_MERGE_POLICIES = frozenset({"DEFAULT", "SUM_VALUES", "AREA_WEIGHTED"})
_CARDINALITIES = frozenset({"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY"})
_RELATIONSHIP_TYPES = frozenset({"SIMPLE", "COMPOSITE"})
_MESSAGE_DIRECTIONS = frozenset({"NONE", "FORWARD", "BACKWARD", "BOTH"})


def _clean_name(value: str, label: str, *, max_length: int = 160) -> str:
    name = (value or "").strip()
    if not name:
        raise RuntimeError(f"{label} 不能为空")
    if len(name) > max_length or any(ch in name for ch in ("\r", "\n", ";")):
        raise RuntimeError(f"{label} 无效")
    return name


def _clean_dataset_name(value: str, label: str) -> str:
    name = _clean_name(value, label)
    if name in {".", ".."} or any(ch in name for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|')):
        raise RuntimeError(f"{label} 必须是不含路径分隔符的名称")
    return name


def _enum(value: str, allowed: frozenset[str], label: str) -> str:
    normalized = _clean_name(value, label).upper()
    if normalized not in allowed:
        raise RuntimeError(f"{label} 须为 {sorted(allowed)}")
    return normalized


def _messages(result: Any) -> list[str]:
    messages: list[str] = []
    try:
        for index in range(int(result.messageCount)):
            messages.append(str(result.getMessage(index)))
    except Exception:  # noqa: BLE001
        pass
    return messages


def _require_exists(arcpy: Any, path: str, label: str) -> None:
    exists = getattr(arcpy, "Exists", None)
    if callable(exists) and not bool(exists(path)):
        raise RuntimeError(f"{label} 不存在：{path}")


def _require_schema_lock(arcpy: Any, path: str) -> None:
    tester = getattr(arcpy, "TestSchemaLock", None)
    if not callable(tester):
        tester = getattr(getattr(arcpy, "management", None), "TestSchemaLock", None)
    if callable(tester) and not bool(tester(path)):
        raise RuntimeError(f"无法获取方案锁：{path}")


def dataset_exists(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    """Return a normalized, root-checked ArcPy existence probe."""
    path = validate_input_path_optional(dataset_path, "dataset_path")
    exists = getattr(arcpy, "Exists", None)
    if not callable(exists):
        raise RuntimeError("当前 arcpy 不支持 Exists")
    return {"dataset_path": path, "exists": bool(exists(path))}


def verify_output_dataset(
    arcpy: Any,
    output_path: str,
    *,
    require_nonempty: bool = False,
) -> dict[str, Any]:
    """Verify a GP output under the configured output root and optionally count rows."""
    require_gp_output_root_mandatory()
    path = validate_gp_output_path(output_path, "output_path")
    exists = getattr(arcpy, "Exists", None)
    if not callable(exists) or not bool(exists(path)):
        raise RuntimeError(f"地理处理输出不存在：{path}")
    payload: dict[str, Any] = {"output_path": path, "exists": True, "verified": True}
    get_count = getattr(getattr(arcpy, "management", None), "GetCount", None)
    if callable(get_count):
        try:
            result = get_count(path)
            count = int(str(result.getOutput(0)))
            payload["count"] = count
            if require_nonempty and count < 1:
                raise RuntimeError(f"地理处理输出为空：{path}")
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            if require_nonempty:
                raise RuntimeError(f"无法验证输出记录数：{str(exc)[:300]}") from exc
            payload["count_available"] = False
    return payload


def dataset_schema(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    """Return a single structured schema snapshot suitable for write preflight."""
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    desc = arcpy.Describe(path)
    payload: dict[str, Any] = {
        "dataset_path": path,
        "data_type": getattr(desc, "dataType", None),
        "catalog_path": getattr(desc, "catalogPath", path),
        "shape_type": getattr(desc, "shapeType", None),
        "oid_field_name": getattr(desc, "OIDFieldName", None),
        "is_versioned": bool(getattr(desc, "isVersioned", False)),
        "editor_tracking_enabled": bool(getattr(desc, "editorTrackingEnabled", False)),
        "has_attachments": bool(getattr(desc, "hasAttachments", False)),
        "relationship_class_names": list(getattr(desc, "relationshipClassNames", None) or []),
    }
    spatial_reference = getattr(desc, "spatialReference", None)
    if spatial_reference is not None:
        payload["spatial_reference"] = {
            "name": getattr(spatial_reference, "name", None),
            "factory_code": getattr(spatial_reference, "factoryCode", None),
        }
    fields: list[dict[str, Any]] = []
    for field in arcpy.ListFields(path) or []:
        fields.append(
            {
                "name": getattr(field, "name", None),
                "alias": getattr(field, "aliasName", None),
                "type": getattr(field, "type", None),
                "length": getattr(field, "length", None),
                "nullable": getattr(field, "isNullable", None),
                "required": getattr(field, "required", None),
                "editable": getattr(field, "editable", None),
                "domain": getattr(field, "domain", None) or None,
                "default_value": getattr(field, "defaultValue", None),
            }
        )
    payload["fields"] = fields
    list_indexes = getattr(arcpy, "ListIndexes", None)
    indexes: list[dict[str, Any]] = []
    if callable(list_indexes):
        for index in list_indexes(path) or []:
            indexes.append(
                {
                    "name": getattr(index, "name", None),
                    "unique": bool(getattr(index, "isUnique", False)),
                    "ascending": bool(getattr(index, "isAscending", False)),
                    "fields": [getattr(field, "name", str(field)) for field in getattr(index, "fields", [])],
                }
            )
    payload["indexes"] = indexes
    list_subtypes_fn = getattr(getattr(arcpy, "da", None), "ListSubtypes", None)
    if callable(list_subtypes_fn):
        payload["subtypes"] = _serialize_subtypes(list_subtypes_fn(path) or {})
    return payload


def _serialize_subtypes(values: dict[Any, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, details in values.items():
        field_values: dict[str, Any] = {}
        for field_name, raw in (details.get("FieldValues", {}) or {}).items():
            default_value: Any = None
            domain_name: str | None = None
            if isinstance(raw, (list, tuple)):
                if raw:
                    default_value = raw[0]
                if len(raw) > 1 and raw[1] is not None:
                    domain_name = getattr(raw[1], "name", str(raw[1]))
            field_values[str(field_name)] = {
                "default_value": default_value,
                "domain": domain_name,
            }
        rows.append(
            {
                "code": code,
                "name": details.get("Name"),
                "is_default": bool(details.get("Default")),
                "subtype_field": details.get("SubtypeField"),
                "field_values": field_values,
            }
        )
    return rows


def list_subtypes(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    list_fn = getattr(getattr(arcpy, "da", None), "ListSubtypes", None)
    if not callable(list_fn):
        raise RuntimeError("当前 arcpy.da 不支持 ListSubtypes")
    return {"dataset_path": path, "subtypes": _serialize_subtypes(list_fn(path) or {})}


def list_domains(arcpy: Any, workspace_path: str) -> dict[str, Any]:
    """Return coded-value and range domains in a JSON-friendly shape."""
    workspace = validate_input_path_optional(workspace_path, "workspace_path")
    _require_exists(arcpy, workspace, "workspace_path")
    list_fn = getattr(getattr(arcpy, "da", None), "ListDomains", None)
    if not callable(list_fn):
        raise RuntimeError("当前 arcpy.da 不支持 ListDomains")
    domains: list[dict[str, Any]] = []
    for domain in list_fn(workspace) or []:
        coded_values = getattr(domain, "codedValues", None)
        value_range = getattr(domain, "range", None)
        domains.append(
            {
                "name": getattr(domain, "name", None),
                "description": getattr(domain, "description", None),
                "field_type": getattr(domain, "type", None),
                "domain_type": getattr(domain, "domainType", None),
                "owner": getattr(domain, "owner", None),
                "split_policy": getattr(domain, "splitPolicy", None),
                "merge_policy": getattr(domain, "mergePolicy", None),
                "coded_values": [
                    {"code": code, "description": description}
                    for code, description in (coded_values or {}).items()
                ],
                "range": list(value_range) if value_range is not None else None,
            }
        )
    return {"workspace_path": workspace, "domains": domains}


def run_create_domain(
    arcpy: Any,
    workspace_path: str,
    domain_name: str,
    domain_description: str,
    field_type: str,
    domain_type: str = "CODED",
    split_policy: str = "DEFAULT",
    merge_policy: str = "DEFAULT",
) -> list[str]:
    require_allow_write()
    workspace = validate_input_path_optional(workspace_path, "workspace_path")
    _require_exists(arcpy, workspace, "workspace_path")
    _require_schema_lock(arcpy, workspace)
    name = _clean_name(domain_name, "domain_name")
    description = _clean_name(domain_description, "domain_description", max_length=1000)
    field = _enum(field_type, _FIELD_TYPES, "field_type")
    kind = _enum(domain_type, _DOMAIN_TYPES, "domain_type")
    split = _enum(split_policy, _SPLIT_POLICIES, "split_policy")
    merge = _enum(merge_policy, _MERGE_POLICIES, "merge_policy")
    result = arcpy.management.CreateDomain(workspace, name, description, field, kind, split, merge)
    return _messages(result)


def run_delete_domain(arcpy: Any, workspace_path: str, domain_name: str) -> list[str]:
    require_allow_write()
    workspace = validate_input_path_optional(workspace_path, "workspace_path")
    _require_exists(arcpy, workspace, "workspace_path")
    _require_schema_lock(arcpy, workspace)
    result = arcpy.management.DeleteDomain(workspace, _clean_name(domain_name, "domain_name"))
    return _messages(result)


def run_alter_domain(
    arcpy: Any,
    workspace_path: str,
    domain_name: str,
    new_domain_name: str = "",
    new_domain_description: str = "",
    split_policy: str = "",
    merge_policy: str = "",
    new_domain_owner: str = "",
) -> list[str]:
    """Update selected properties of an existing attribute domain."""
    require_allow_write()
    workspace = validate_input_path_optional(workspace_path, "workspace_path")
    _require_exists(arcpy, workspace, "workspace_path")
    _require_schema_lock(arcpy, workspace)
    if not any(
        value.strip()
        for value in (
            new_domain_name,
            new_domain_description,
            split_policy,
            merge_policy,
            new_domain_owner,
        )
    ):
        raise RuntimeError("至少提供一个要更新的 domain 属性")
    result = arcpy.management.AlterDomain(
        workspace,
        _clean_name(domain_name, "domain_name"),
        _clean_name(new_domain_name, "new_domain_name") if new_domain_name else "#",
        (
            _clean_name(new_domain_description, "new_domain_description", max_length=1000)
            if new_domain_description
            else "#"
        ),
        _enum(split_policy, _SPLIT_POLICIES, "split_policy") if split_policy else "#",
        _enum(merge_policy, _MERGE_POLICIES, "merge_policy") if merge_policy else "#",
        _clean_name(new_domain_owner, "new_domain_owner") if new_domain_owner else "#",
    )
    return _messages(result)


def run_add_coded_value_to_domain(
    arcpy: Any,
    workspace_path: str,
    domain_name: str,
    code: Any,
    description: str,
) -> list[str]:
    require_allow_write()
    workspace = validate_input_path_optional(workspace_path, "workspace_path")
    _require_exists(arcpy, workspace, "workspace_path")
    _require_schema_lock(arcpy, workspace)
    result = arcpy.management.AddCodedValueToDomain(
        workspace,
        _clean_name(domain_name, "domain_name"),
        code,
        _clean_name(description, "description", max_length=1000),
    )
    return _messages(result)


def run_delete_coded_value_from_domain(
    arcpy: Any,
    workspace_path: str,
    domain_name: str,
    code: Any,
) -> list[str]:
    require_allow_write()
    workspace = validate_input_path_optional(workspace_path, "workspace_path")
    _require_exists(arcpy, workspace, "workspace_path")
    _require_schema_lock(arcpy, workspace)
    result = arcpy.management.DeleteCodedValueFromDomain(
        workspace,
        _clean_name(domain_name, "domain_name"),
        code,
    )
    return _messages(result)


def run_set_range_domain(
    arcpy: Any,
    workspace_path: str,
    domain_name: str,
    minimum_value: Any,
    maximum_value: Any,
) -> list[str]:
    require_allow_write()
    workspace = validate_input_path_optional(workspace_path, "workspace_path")
    _require_exists(arcpy, workspace, "workspace_path")
    _require_schema_lock(arcpy, workspace)
    if minimum_value is None or maximum_value is None:
        raise RuntimeError("minimum_value 和 maximum_value 不能为空")
    result = arcpy.management.SetValueForRangeDomain(
        workspace,
        _clean_name(domain_name, "domain_name"),
        minimum_value,
        maximum_value,
    )
    return _messages(result)


def run_assign_domain_to_field(
    arcpy: Any,
    dataset_path: str,
    field_name: str,
    domain_name: str,
    subtype_code: int | None = None,
) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    subtype = "#" if subtype_code is None else int(subtype_code)
    result = arcpy.management.AssignDomainToField(
        path,
        _clean_name(field_name, "field_name"),
        _clean_name(domain_name, "domain_name"),
        subtype,
    )
    return _messages(result)


def run_remove_domain_from_field(
    arcpy: Any,
    dataset_path: str,
    field_name: str,
    subtype_code: int | None = None,
) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    subtype = "#" if subtype_code is None else int(subtype_code)
    result = arcpy.management.RemoveDomainFromField(
        path,
        _clean_name(field_name, "field_name"),
        subtype,
    )
    return _messages(result)


def run_set_subtype_field(arcpy: Any, dataset_path: str, field_name: str) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    result = arcpy.management.SetSubtypeField(path, _clean_name(field_name, "field_name"))
    return _messages(result)


def run_clear_subtype_field(arcpy: Any, dataset_path: str) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    return _messages(arcpy.management.SetSubtypeField(path, "#", "CLEAR_SUBTYPE_FIELD"))


def run_add_subtype(
    arcpy: Any,
    dataset_path: str,
    subtype_code: int,
    subtype_name: str,
) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    result = arcpy.management.AddSubtype(
        path,
        int(subtype_code),
        _clean_name(subtype_name, "subtype_name"),
    )
    return _messages(result)


def run_remove_subtype(arcpy: Any, dataset_path: str, subtype_code: int) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    result = arcpy.management.RemoveSubtype(path, int(subtype_code))
    return _messages(result)


def run_set_default_subtype(arcpy: Any, dataset_path: str, subtype_code: int) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    result = arcpy.management.SetDefaultSubtype(path, int(subtype_code))
    return _messages(result)


def attachments_info(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    desc = arcpy.Describe(path)
    return {
        "dataset_path": path,
        "has_attachments": bool(getattr(desc, "hasAttachments", False)),
        "relationship_class_names": list(getattr(desc, "relationshipClassNames", None) or []),
    }


def run_enable_attachments(arcpy: Any, dataset_path: str) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    return _messages(arcpy.management.EnableAttachments(path))


def run_disable_attachments(arcpy: Any, dataset_path: str) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    return _messages(arcpy.management.DisableAttachments(path))


def run_add_attachments(
    arcpy: Any,
    dataset_path: str,
    in_join_field: str,
    match_table: str,
    match_join_field: str,
    match_path_field: str,
    working_folder: str = "",
) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    matches = validate_input_path_optional(match_table, "match_table")
    folder = validate_input_path_optional(working_folder, "working_folder") if working_folder else None
    _require_exists(arcpy, path, "dataset_path")
    _require_exists(arcpy, matches, "match_table")
    result = arcpy.management.AddAttachments(
        path,
        _clean_name(in_join_field, "in_join_field"),
        matches,
        _clean_name(match_join_field, "match_join_field"),
        _clean_name(match_path_field, "match_path_field"),
        folder,
    )
    return _messages(result)


def run_remove_attachments(
    arcpy: Any,
    dataset_path: str,
    in_join_field: str,
    match_table: str,
    match_join_field: str,
    match_name_field: str = "",
) -> list[str]:
    require_allow_write()
    path = validate_input_path_optional(dataset_path, "dataset_path")
    matches = validate_input_path_optional(match_table, "match_table")
    _require_exists(arcpy, path, "dataset_path")
    _require_exists(arcpy, matches, "match_table")
    args: list[Any] = [
        path,
        _clean_name(in_join_field, "in_join_field"),
        matches,
        _clean_name(match_join_field, "match_join_field"),
    ]
    if match_name_field:
        args.append(_clean_name(match_name_field, "match_name_field"))
    result = arcpy.management.RemoveAttachments(*args)
    return _messages(result)


def relationship_classes(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    path = validate_input_path_optional(dataset_path, "dataset_path")
    _require_exists(arcpy, path, "dataset_path")
    desc = arcpy.Describe(path)
    return {
        "dataset_path": path,
        "relationship_class_names": list(getattr(desc, "relationshipClassNames", None) or []),
    }


def run_create_relationship_class(
    arcpy: Any,
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
) -> list[str]:
    require_allow_write()
    require_gp_output_root_mandatory()
    origin = validate_input_path_optional(origin_table, "origin_table")
    destination = validate_input_path_optional(destination_table, "destination_table")
    output = validate_gp_output_path(out_relationship_class, "out_relationship_class")
    _require_exists(arcpy, origin, "origin_table")
    _require_exists(arcpy, destination, "destination_table")
    _require_schema_lock(arcpy, origin)
    _require_schema_lock(arcpy, destination)
    relationship_cardinality = _enum(cardinality, _CARDINALITIES, "cardinality")
    needs_destination_keys = relationship_cardinality == "MANY_TO_MANY" or bool(attributed)
    destination_primary = destination_primary_key.strip()
    destination_foreign = destination_foreign_key.strip()
    if needs_destination_keys and (not destination_primary or not destination_foreign):
        raise RuntimeError(
            "MANY_TO_MANY 或 attributed 关系需要 destination_primary_key "
            "和 destination_foreign_key"
        )
    result = arcpy.management.CreateRelationshipClass(
        origin,
        destination,
        output,
        _enum(relationship_type, _RELATIONSHIP_TYPES, "relationship_type"),
        _clean_name(forward_label, "forward_label"),
        _clean_name(backward_label, "backward_label"),
        _enum(message_direction, _MESSAGE_DIRECTIONS, "message_direction"),
        relationship_cardinality,
        "ATTRIBUTED" if attributed else "NONE",
        _clean_name(origin_primary_key, "origin_primary_key"),
        _clean_name(origin_foreign_key, "origin_foreign_key"),
        destination_primary or None,
        destination_foreign or None,
    )
    _require_exists(arcpy, output, "out_relationship_class")
    return _messages(result)


def run_delete_relationship_class(arcpy: Any, relationship_class_path: str) -> list[str]:
    require_allow_write()
    require_gp_output_root_mandatory()
    path = validate_gp_output_path(relationship_class_path, "relationship_class_path")
    _require_exists(arcpy, path, "relationship_class_path")
    _require_schema_lock(arcpy, path)
    return _messages(arcpy.management.Delete(path))


def topology_info(arcpy: Any, topology_path: str) -> dict[str, Any]:
    path = validate_input_path_optional(topology_path, "topology_path")
    _require_exists(arcpy, path, "topology_path")
    desc = arcpy.Describe(path)
    return {
        "topology_path": path,
        "name": getattr(desc, "name", None),
        "cluster_tolerance": getattr(desc, "clusterTolerance", None),
        "feature_class_names": list(getattr(desc, "featureClassNames", None) or []),
    }


def run_create_topology(
    arcpy: Any,
    feature_dataset: str,
    topology_name: str,
    cluster_tolerance: float | None = None,
) -> dict[str, Any]:
    require_allow_write()
    require_gp_output_root_mandatory()
    feature_dataset_path = validate_gp_output_path(feature_dataset, "feature_dataset")
    _require_exists(arcpy, feature_dataset_path, "feature_dataset")
    _require_schema_lock(arcpy, feature_dataset_path)
    name = _clean_dataset_name(topology_name, "topology_name")
    args: list[Any] = [feature_dataset_path, name]
    if cluster_tolerance is not None:
        tolerance = float(cluster_tolerance)
        if tolerance <= 0:
            raise RuntimeError("cluster_tolerance 必须大于 0")
        args.append(tolerance)
    result = arcpy.management.CreateTopology(*args)
    output = (
        str(result.getOutput(0))
        if hasattr(result, "getOutput")
        else os.path.join(feature_dataset_path, name)
    )
    output = validate_gp_output_path(output, "topology_path")
    _require_exists(arcpy, output, "topology_path")
    return {"topology_path": output, "messages": _messages(result)}


def run_add_feature_class_to_topology(
    arcpy: Any,
    topology_path: str,
    feature_class: str,
    xy_rank: int = 1,
    z_rank: int = 1,
) -> list[str]:
    require_allow_write()
    require_gp_output_root_mandatory()
    topology = validate_gp_output_path(topology_path, "topology_path")
    features = validate_input_path_optional(feature_class, "feature_class")
    _require_exists(arcpy, topology, "topology_path")
    _require_exists(arcpy, features, "feature_class")
    _require_schema_lock(arcpy, topology)
    _require_schema_lock(arcpy, features)
    if int(xy_rank) < 1 or int(z_rank) < 1:
        raise RuntimeError("xy_rank 和 z_rank 必须 >= 1")
    return _messages(
        arcpy.management.AddFeatureClassToTopology(
            topology,
            features,
            int(xy_rank),
            int(z_rank),
        )
    )


def run_add_rule_to_topology(
    arcpy: Any,
    topology_path: str,
    rule_type: str,
    origin_feature_class: str,
    destination_feature_class: str = "",
) -> list[str]:
    require_allow_write()
    require_gp_output_root_mandatory()
    topology = validate_gp_output_path(topology_path, "topology_path")
    origin = validate_input_path_optional(origin_feature_class, "origin_feature_class")
    destination = (
        validate_input_path_optional(destination_feature_class, "destination_feature_class")
        if destination_feature_class
        else None
    )
    _require_exists(arcpy, topology, "topology_path")
    _require_exists(arcpy, origin, "origin_feature_class")
    if destination:
        _require_exists(arcpy, destination, "destination_feature_class")
    _require_schema_lock(arcpy, topology)
    result = arcpy.management.AddRuleToTopology(
        topology,
        _clean_name(rule_type, "rule_type", max_length=240),
        origin,
        "#",
        destination or "#",
        "#",
    )
    return _messages(result)


def run_remove_rule_from_topology(
    arcpy: Any,
    topology_path: str,
    rule_name: str,
) -> list[str]:
    """Remove the exact rule string returned by a topology's rule inventory."""
    require_allow_write()
    require_gp_output_root_mandatory()
    topology = validate_gp_output_path(topology_path, "topology_path")
    _require_exists(arcpy, topology, "topology_path")
    _require_schema_lock(arcpy, topology)
    result = arcpy.management.RemoveRuleFromTopology(
        topology,
        _clean_name(rule_name, "rule_name", max_length=500),
    )
    return _messages(result)


def run_remove_feature_class_from_topology(
    arcpy: Any,
    topology_path: str,
    feature_class: str,
) -> list[str]:
    require_allow_write()
    require_gp_output_root_mandatory()
    topology = validate_gp_output_path(topology_path, "topology_path")
    features = validate_input_path_optional(feature_class, "feature_class")
    _require_exists(arcpy, topology, "topology_path")
    _require_exists(arcpy, features, "feature_class")
    _require_schema_lock(arcpy, topology)
    return _messages(arcpy.management.RemoveFeatureClassFromTopology(topology, features))


def run_validate_topology(arcpy: Any, topology_path: str, extent: str = "") -> list[str]:
    require_allow_write()
    require_gp_output_root_mandatory()
    topology = validate_gp_output_path(topology_path, "topology_path")
    _require_exists(arcpy, topology, "topology_path")
    _require_schema_lock(arcpy, topology)
    result = arcpy.management.ValidateTopology(topology, (extent or "").strip() or "Full_Extent")
    return _messages(result)


def run_export_topology_errors(
    arcpy: Any,
    topology_path: str,
    output_path: str,
    output_name: str,
) -> dict[str, Any]:
    require_allow_write()
    require_gp_output_root_mandatory()
    topology = validate_gp_output_path(topology_path, "topology_path")
    target = validate_gp_output_path(output_path, "output_path")
    name = _clean_dataset_name(output_name, "output_name")
    _require_exists(arcpy, topology, "topology_path")
    result = arcpy.management.ExportTopologyErrors(topology, target, name)
    outputs = {
        "points": os.path.join(target, f"{name}_pointErrors"),
        "lines": os.path.join(target, f"{name}_lineErrors"),
        "polygons": os.path.join(target, f"{name}_polyErrors"),
    }
    # ArcPy exposes the authoritative derived outputs. Prefer those over
    # constructed legacy names when available.
    if hasattr(result, "getOutput"):
        for index, key in enumerate(("points", "lines", "polygons")):
            try:
                value = str(result.getOutput(index))
            except Exception:  # noqa: BLE001
                continue
            if value:
                outputs[key] = validate_gp_output_path(value, f"{key}_output")
    verified = {
        key: verify_output_dataset(arcpy, output)
        for key, output in outputs.items()
    }
    return {
        "output_path": target,
        "output_name": name,
        "outputs": verified,
        "messages": _messages(result),
        "verified": True,
    }
