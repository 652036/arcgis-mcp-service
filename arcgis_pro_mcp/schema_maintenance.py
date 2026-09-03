"""Enterprise dataset index, statistics, tracking, and Global ID helpers.

ArcPy is injected by callers so this module remains importable outside ArcGIS
Pro.  All mutations require the enterprise-write gate; operations that remove
an index or disable audit tracking additionally require the destructive gate
and an exact target confirmation.
"""

from __future__ import annotations

import re
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_destructive,
    require_allow_enterprise_write,
    validate_input_path_optional,
)

_UNIQUE = frozenset({"UNIQUE", "NON_UNIQUE"})
_ASCENDING = frozenset({"ASCENDING", "NON_ASCENDING"})
_DELTA_ONLY = frozenset({"ALL", "ONLY_DELTAS"})
_RECORD_DATES_IN = frozenset({"UTC", "DATABASE_TIME"})


def _messages(result: Any) -> list[str]:
    values: list[str] = []
    try:
        for index in range(int(result.messageCount)):
            values.append(str(result.getMessage(index)))
    except Exception:  # noqa: BLE001
        pass
    return values


def _enum(value: str, allowed: frozenset[str], label: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in allowed:
        raise RuntimeError(f"{label} 须为 {sorted(allowed)}")
    return normalized


def _name(value: str, label: str, *, max_length: int = 160) -> str:
    raw = str(value or "")
    cleaned = raw.strip()
    if not cleaned:
        raise RuntimeError(f"{label} 不能为空")
    if raw != cleaned:
        raise RuntimeError(f"{label} 首尾不能包含空白")
    if len(cleaned) > max_length or any(
        ch in cleaned for ch in ("\r", "\n", ";", "\\", "/", '"', "'")
    ):
        raise RuntimeError(f"{label} 无效")
    return cleaned


def _enterprise_dataset(dataset_path: str, label: str = "dataset_path") -> str:
    path = validate_input_path_optional(dataset_path, label)
    if not isinstance(path, str):
        raise RuntimeError(f"{label} 必须是企业地理数据库中的受控绝对路径")
    if not re.search(r"\.sde(?:[\\/]|$)", path, re.IGNORECASE):
        raise RuntimeError(f"{label} 必须位于 .sde 企业地理数据库连接内")
    return path


def _enterprise_workspace(workspace_path: str) -> str:
    path = validate_input_path_optional(workspace_path, "workspace_path")
    if not isinstance(path, str) or not path.casefold().endswith(".sde"):
        raise RuntimeError("workspace_path 必须是受控的 .sde 连接文件")
    return path


def _require_exists(arcpy: Any, path: str, label: str) -> None:
    exists = getattr(arcpy, "Exists", None)
    if callable(exists) and not bool(exists(path)):
        raise RuntimeError(f"{label} 不存在：{path}")


def _require_schema_lock(arcpy: Any, path: str) -> None:
    tester = getattr(arcpy, "TestSchemaLock", None)
    if callable(tester) and not bool(tester(path)):
        raise RuntimeError(f"无法获取方案锁：{path}")


def _management_tool(arcpy: Any, name: str) -> Any:
    tool = getattr(getattr(arcpy, "management", None), name, None)
    if not callable(tool):
        raise RuntimeError(f"当前 ArcPy 不支持 arcpy.management.{name}")
    return tool


def _field_map(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    list_fields = getattr(arcpy, "ListFields", None)
    if not callable(list_fields):
        raise RuntimeError("当前 ArcPy 不支持 ListFields")
    result: dict[str, Any] = {}
    for field in list_fields(dataset_path) or []:
        field_name = str(getattr(field, "name", ""))
        if field_name:
            result[field_name.casefold()] = field
    return result


def _index_payloads(arcpy: Any, dataset_path: str) -> list[dict[str, Any]]:
    list_indexes = getattr(arcpy, "ListIndexes", None)
    if not callable(list_indexes):
        raise RuntimeError("当前 ArcPy 不支持 ListIndexes")
    values: list[dict[str, Any]] = []
    for index in list_indexes(dataset_path) or []:
        values.append(
            {
                "name": str(getattr(index, "name", "")),
                "unique": bool(getattr(index, "isUnique", False)),
                "ascending": bool(getattr(index, "isAscending", False)),
                "fields": [
                    str(getattr(field, "name", field))
                    for field in getattr(index, "fields", None) or []
                ],
            }
        )
    return values


def _tracking_payload(description: Any) -> dict[str, Any]:
    return {
        "enabled": bool(getattr(description, "editorTrackingEnabled", False)),
        "creator_field": getattr(description, "creatorFieldName", None) or None,
        "creation_date_field": getattr(description, "createdAtFieldName", None) or None,
        "last_editor_field": getattr(description, "editorFieldName", None) or None,
        "last_edit_date_field": getattr(description, "editedAtFieldName", None) or None,
        "dates_in_utc": getattr(description, "isTimeInUTC", None),
    }


def _global_id_field(arcpy: Any, dataset_path: str, description: Any | None = None) -> str | None:
    desc = description if description is not None else arcpy.Describe(dataset_path)
    value = getattr(desc, "globalIDFieldName", None)
    if value:
        return str(value)
    for field in _field_map(arcpy, dataset_path).values():
        if str(getattr(field, "type", "")).casefold() == "globalid":
            return str(getattr(field, "name", "")) or None
    return None


def dataset_maintenance_info(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    """Inspect index, editor-tracking, Global ID, and versioned state."""
    path = _enterprise_dataset(dataset_path)
    _require_exists(arcpy, path, "dataset_path")
    description = arcpy.Describe(path)
    return {
        "dataset_path": path,
        "is_versioned": bool(getattr(description, "isVersioned", False)),
        "indexes": _index_payloads(arcpy, path),
        "editor_tracking": _tracking_payload(description),
        "global_id_field": _global_id_field(arcpy, path, description),
    }


def add_index(
    arcpy: Any,
    dataset_path: str,
    fields: list[str],
    index_name: str,
    *,
    unique: str = "NON_UNIQUE",
    ascending: str = "NON_ASCENDING",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    path = _enterprise_dataset(dataset_path)
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    name = _name(index_name, "index_name")
    if not isinstance(fields, list) or not fields or len(fields) > 32:
        raise RuntimeError("fields 必须为包含 1 到 32 个字段名的数组")
    available = _field_map(arcpy, path)
    canonical: list[str] = []
    for index, value in enumerate(fields):
        requested = _name(value, f"fields[{index}]")
        field = available.get(requested.casefold())
        if field is None:
            raise RuntimeError(f"字段不存在：{requested}")
        canonical.append(str(getattr(field, "name", requested)))
    if len({value.casefold() for value in canonical}) != len(canonical):
        raise RuntimeError("fields 不能包含重复字段")
    if any(item["name"].casefold() == name.casefold() for item in _index_payloads(arcpy, path)):
        raise RuntimeError(f"索引已存在：{name}")
    unique_value = _enum(unique, _UNIQUE, "unique")
    ascending_value = _enum(ascending, _ASCENDING, "ascending")
    result = _management_tool(arcpy, "AddIndex")(
        path,
        canonical,
        name,
        unique_value,
        ascending_value,
    )
    matches = [
        item
        for item in _index_payloads(arcpy, path)
        if item["name"].casefold() == name.casefold()
    ]
    if len(matches) != 1 or [value.casefold() for value in matches[0]["fields"]] != [
        value.casefold() for value in canonical
    ]:
        raise RuntimeError("AddIndex 已返回但无法验证索引及字段；不要自动重试")
    return {
        "dataset_path": path,
        "added": True,
        "index": matches[0],
        "messages": _messages(result),
    }


def remove_index(
    arcpy: Any,
    dataset_path: str,
    index_name: str,
    *,
    confirm_index_name: str = "",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    require_allow_destructive()
    path = _enterprise_dataset(dataset_path)
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    name = _name(index_name, "index_name")
    if confirm_index_name != name:
        raise RuntimeError("confirm_index_name 必须精确回显 index_name")
    matches = [item for item in _index_payloads(arcpy, path) if item["name"] == name]
    if len(matches) != 1:
        raise RuntimeError(f"索引不存在或名称未精确匹配：{name}")
    result = _management_tool(arcpy, "RemoveIndex")(path, name)
    if any(item["name"] == name for item in _index_payloads(arcpy, path)):
        raise RuntimeError("RemoveIndex 已返回但索引仍存在；不要自动重试")
    return {
        "dataset_path": path,
        "removed": True,
        "index": matches[0],
        "messages": _messages(result),
    }


def _relative_datasets(values: list[str], label: str = "datasets") -> list[str]:
    if not isinstance(values, list) or len(values) > 500:
        raise RuntimeError(f"{label} 必须为最多 500 项的字符串数组")
    cleaned: list[str] = []
    for index, value in enumerate(values):
        item = _name(value, f"{label}[{index}]", max_length=256)
        if ":" in item or item in {".", ".."}:
            raise RuntimeError(f"{label}[{index}] 必须是相对 workspace 的数据集名")
        cleaned.append(item)
    if len({item.casefold() for item in cleaned}) != len(cleaned):
        raise RuntimeError(f"{label} 不能包含重复数据集")
    return cleaned


def rebuild_indexes(
    arcpy: Any,
    workspace_path: str,
    datasets: list[str],
    *,
    include_system: bool = False,
    delta_only: str = "ONLY_DELTAS",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    workspace = _enterprise_workspace(workspace_path)
    _require_exists(arcpy, workspace, "workspace_path")
    names = _relative_datasets(datasets)
    if not include_system and not names:
        raise RuntimeError("include_system=false 时必须显式提供 datasets")
    delta = _enum(delta_only, _DELTA_ONLY, "delta_only")
    result = _management_tool(arcpy, "RebuildIndexes")(
        workspace,
        "SYSTEM" if include_system else "NO_SYSTEM",
        names,
        delta,
    )
    return {
        "workspace_path": workspace,
        "rebuilt": True,
        "include_system": include_system,
        "datasets": names,
        "delta_only": delta,
        "messages": _messages(result),
    }


def analyze_datasets(
    arcpy: Any,
    workspace_path: str,
    datasets: list[str],
    *,
    include_system: bool = False,
    analyze_base: bool = True,
    analyze_delta: bool = True,
    analyze_archive: bool = True,
) -> dict[str, Any]:
    require_allow_enterprise_write()
    workspace = _enterprise_workspace(workspace_path)
    _require_exists(arcpy, workspace, "workspace_path")
    names = _relative_datasets(datasets)
    if not include_system and not names:
        raise RuntimeError("include_system=false 时必须显式提供 datasets")
    if names and not any((analyze_base, analyze_delta, analyze_archive)):
        raise RuntimeError("至少启用 analyze_base、analyze_delta 或 analyze_archive 之一")
    result = _management_tool(arcpy, "AnalyzeDatasets")(
        workspace,
        "SYSTEM" if include_system else "NO_SYSTEM",
        names,
        "ANALYZE_BASE" if analyze_base else "NO_ANALYZE_BASE",
        "ANALYZE_DELTA" if analyze_delta else "NO_ANALYZE_DELTA",
        "ANALYZE_ARCHIVE" if analyze_archive else "NO_ANALYZE_ARCHIVE",
    )
    return {
        "workspace_path": workspace,
        "analyzed": True,
        "include_system": include_system,
        "datasets": names,
        "analyze_base": analyze_base,
        "analyze_delta": analyze_delta,
        "analyze_archive": analyze_archive,
        "messages": _messages(result),
    }


def enable_editor_tracking(
    arcpy: Any,
    dataset_path: str,
    creator_field: str,
    creation_date_field: str,
    last_editor_field: str,
    last_edit_date_field: str,
    *,
    add_fields: bool = False,
    record_dates_in: str = "UTC",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    path = _enterprise_dataset(dataset_path)
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    requested = {
        "creator_field": _name(creator_field, "creator_field"),
        "creation_date_field": _name(creation_date_field, "creation_date_field"),
        "last_editor_field": _name(last_editor_field, "last_editor_field"),
        "last_edit_date_field": _name(last_edit_date_field, "last_edit_date_field"),
    }
    if not add_fields:
        fields = _field_map(arcpy, path)
        missing = [value for value in requested.values() if value.casefold() not in fields]
        if missing:
            raise RuntimeError(f"add_fields=false 时字段必须已存在：{missing}")
    else:
        fields = _field_map(arcpy, path)
    expected_types = {
        "creator_field": {"string", "text"},
        "creation_date_field": {"date"},
        "last_editor_field": {"string", "text"},
        "last_edit_date_field": {"date"},
    }
    for key, value in requested.items():
        field = fields.get(value.casefold())
        if field is None:
            continue
        field_type = str(getattr(field, "type", "")).casefold()
        if field_type not in expected_types[key]:
            raise RuntimeError(
                f"{key} 的现有字段类型不兼容：{value} ({field_type or 'unknown'})"
            )
    date_mode = _enum(record_dates_in, _RECORD_DATES_IN, "record_dates_in")
    result = _management_tool(arcpy, "EnableEditorTracking")(
        path,
        requested["creator_field"],
        requested["creation_date_field"],
        requested["last_editor_field"],
        requested["last_edit_date_field"],
        "ADD_FIELDS" if add_fields else "NO_ADD_FIELDS",
        date_mode,
    )
    observed = _tracking_payload(arcpy.Describe(path))
    if not observed["enabled"]:
        raise RuntimeError(
            "EnableEditorTracking 已返回但 Describe.editorTrackingEnabled 仍为 false；"
            "不要自动重试"
        )
    for key, value in requested.items():
        actual = observed.get(key)
        if actual and str(actual).casefold() != value.casefold():
            raise RuntimeError(f"编辑者追踪字段验证失败：{key}")
    return {
        "dataset_path": path,
        "enabled": True,
        "add_fields": add_fields,
        "record_dates_in": date_mode,
        "editor_tracking": observed,
        "messages": _messages(result),
    }


def disable_editor_tracking(
    arcpy: Any,
    dataset_path: str,
    *,
    disable_creator: bool = True,
    disable_creation_date: bool = True,
    disable_last_editor: bool = True,
    disable_last_edit_date: bool = True,
    confirm_dataset_path: str = "",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    require_allow_destructive()
    if confirm_dataset_path != dataset_path:
        raise RuntimeError("confirm_dataset_path 必须精确回显 dataset_path")
    if not any(
        (
            disable_creator,
            disable_creation_date,
            disable_last_editor,
            disable_last_edit_date,
        )
    ):
        raise RuntimeError("至少要禁用一种编辑者追踪字段")
    path = _enterprise_dataset(dataset_path)
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    before = _tracking_payload(arcpy.Describe(path))
    if not before["enabled"]:
        raise RuntimeError("dataset_path 当前未启用编辑者追踪")
    result = _management_tool(arcpy, "DisableEditorTracking")(
        path,
        "DISABLE_CREATOR" if disable_creator else "NO_DISABLE_CREATOR",
        "DISABLE_CREATION_DATE" if disable_creation_date else "NO_DISABLE_CREATION_DATE",
        "DISABLE_LAST_EDITOR" if disable_last_editor else "NO_DISABLE_LAST_EDITOR",
        "DISABLE_LAST_EDIT_DATE" if disable_last_edit_date else "NO_DISABLE_LAST_EDIT_DATE",
    )
    observed = _tracking_payload(arcpy.Describe(path))
    disabled_all = all(
        (
            disable_creator,
            disable_creation_date,
            disable_last_editor,
            disable_last_edit_date,
        )
    )
    if disabled_all and observed["enabled"]:
        raise RuntimeError(
            "DisableEditorTracking 已返回但 Describe.editorTrackingEnabled 仍为 true；"
            "不要自动重试"
        )
    return {
        "dataset_path": path,
        "disabled": True,
        "disabled_all": disabled_all,
        "fields_retained": True,
        "editor_tracking": observed,
        "messages": _messages(result),
    }


def add_global_ids(
    arcpy: Any,
    dataset_paths: list[str],
) -> dict[str, Any]:
    require_allow_enterprise_write()
    if not isinstance(dataset_paths, list) or not dataset_paths or len(dataset_paths) > 50:
        raise RuntimeError("dataset_paths 必须为包含 1 到 50 个路径的数组")
    paths = [_enterprise_dataset(value, f"dataset_paths[{index}]") for index, value in enumerate(dataset_paths)]
    if len({value.casefold() for value in paths}) != len(paths):
        raise RuntimeError("dataset_paths 不能包含重复路径")
    pending: list[str] = []
    existing: dict[str, str] = {}
    for path in paths:
        _require_exists(arcpy, path, "dataset_path")
        _require_schema_lock(arcpy, path)
        field = _global_id_field(arcpy, path)
        if field:
            existing[path] = field
        else:
            pending.append(path)
    result = None
    if pending:
        result = _management_tool(arcpy, "AddGlobalIDs")(
            pending[0] if len(pending) == 1 else pending
        )
    fields: dict[str, str] = dict(existing)
    for path in pending:
        field = _global_id_field(arcpy, path)
        if not field:
            raise RuntimeError(
                "AddGlobalIDs 已返回但无法验证 GlobalID 字段；不要自动重试"
            )
        fields[path] = field
    return {
        "dataset_paths": paths,
        "changed": bool(pending),
        "updated_count": len(pending),
        "already_present_count": len(existing),
        "global_id_fields": [
            {"dataset_path": path, "field_name": fields[path]} for path in paths
        ],
        "messages": _messages(result) if result is not None else [],
    }
