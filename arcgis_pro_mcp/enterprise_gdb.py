"""Constrained enterprise geodatabase versioning helpers.

The module intentionally has no top-level ArcPy import.  Callers inject ArcPy,
and every mutation passes the enterprise-write gate.  Operations that can
discard or make edits irreversible also require the destructive gate and an
exact target confirmation.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from arcgis_pro_mcp.paths import (
    require_allow_destructive,
    require_allow_enterprise_write,
    validate_input_path_optional,
    validate_new_output_in_export_root,
)

_SERVER_ALLOWLIST_ENV = "ARCGIS_PRO_MCP_SERVER_ALLOWLIST"
_ACCESS_LEVELS = frozenset({"PRIVATE", "PUBLIC", "PROTECTED"})
_VERSION_TYPES = frozenset({"TRANSACTIONAL", "HISTORICAL", "BRANCH"})
_RECONCILE_MODES = frozenset({"ALL_VERSIONS", "BLOCKING_VERSIONS"})
_CONFLICT_DEFINITIONS = frozenset({"BY_OBJECT", "BY_ATTRIBUTE"})
_CONFLICT_RESOLUTIONS = frozenset(
    {"FAVOR_TARGET_VERSION", "FAVOR_EDIT_VERSION"}
)
_EDIT_TO_BASE = frozenset({"NO_EDITS_TO_BASE", "EDITS_TO_BASE"})
_KEEP_EDIT = frozenset({"KEEP_EDIT", "NO_KEEP_EDIT"})
_COMPRESS_DEFAULT = frozenset({"NO_COMPRESS_DEFAULT", "COMPRESS_DEFAULT"})


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


def _identifier(value: str, label: str, *, max_length: int = 160) -> str:
    raw = str(value or "")
    cleaned = raw.strip()
    if not cleaned:
        raise RuntimeError(f"{label} 不能为空")
    if raw != cleaned:
        raise RuntimeError(f"{label} 首尾不能包含空白")
    if len(cleaned) > max_length:
        raise RuntimeError(f"{label} 不能超过 {max_length} 个字符")
    if any(ch in cleaned for ch in ("\r", "\n", ";", "\\", "/", '"', "'")):
        raise RuntimeError(f"{label} 包含不允许的字符")
    return cleaned


def _version_name(value: str, label: str = "version_name") -> str:
    return _identifier(value, label)


def _reject_default_version(version_name: str, operation: str) -> None:
    if version_name.casefold() == "default" or version_name.casefold().endswith(
        ".default"
    ):
        raise RuntimeError(f"{operation} 不允许以 Default 版本为目标")


def _split_allowlist() -> list[str]:
    raw = os.environ.get(_SERVER_ALLOWLIST_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            f"分支版本服务必须在 {_SERVER_ALLOWLIST_ENV} 中显式列出完整 URL"
        )
    return [
        item.strip().strip('"')
        for item in re.split(r"[;,\r\n]+", raw)
        if item.strip()
    ]


def _canonical_feature_service_url(value: str, label: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError(f"{label} 必须是完整的 HTTPS FeatureServer URL")
    if parsed.username or parsed.password:
        raise RuntimeError(f"{label} 禁止包含内联用户名或密码")
    if parsed.query or parsed.fragment:
        raise RuntimeError(f"{label} 禁止包含查询参数或片段")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{label} 端口无效") from exc
    host = parsed.hostname.lower()
    netloc = host if port in (None, 443) else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    if not path.casefold().endswith("/featureserver"):
        raise RuntimeError(f"{label} 必须以 /FeatureServer 结尾")
    return urlunsplit(("https", netloc, path, "", ""))


def _enterprise_workspace(workspace: str, label: str = "workspace_path") -> str:
    raw = str(workspace or "").strip()
    if raw.lower().startswith("https://"):
        target = _canonical_feature_service_url(raw, label)
        allowed = {
            _canonical_feature_service_url(item, f"{_SERVER_ALLOWLIST_ENV} 条目")
            for item in _split_allowlist()
            if item.lower().startswith("https://")
            and urlsplit(item).path.rstrip("/").casefold().endswith("/featureserver")
        }
        if target not in allowed:
            raise RuntimeError(
                f"FeatureServer URL 不在 {_SERVER_ALLOWLIST_ENV} 精确白名单中：{target}"
            )
        return target
    if "://" in raw:
        raise RuntimeError(f"{label} 只接受本地 .sde 路径或白名单 HTTPS URL")
    path = validate_input_path_optional(workspace, label)
    if not isinstance(path, str) or not path.casefold().endswith(".sde"):
        raise RuntimeError(f"{label} 必须是受控的 .sde 连接文件")
    return path


def _dataset_path(dataset_path: str) -> str:
    path = validate_input_path_optional(dataset_path, "dataset_path")
    if not isinstance(path, str):
        raise RuntimeError("dataset_path 必须是企业地理数据库中的受控绝对路径")
    if not re.search(r"\.sde(?:[\\/]|$)", path, re.IGNORECASE):
        raise RuntimeError("dataset_path 必须位于 .sde 企业地理数据库连接内")
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


def _version_ref_name(value: Any) -> str:
    return str(getattr(value, "name", value))


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def _version_payload(version: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": _version_ref_name(version)}
    for source, target in (
        ("access", "access"),
        ("description", "description"),
        ("isOwner", "is_owner"),
        ("created", "created"),
        ("lastModified", "last_modified"),
        ("parentVersionName", "parent_version_name"),
    ):
        try:
            value = getattr(version, source)
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            payload[target] = _json_value(value)
    for source, target in (("ancestors", "ancestors"), ("children", "children")):
        try:
            values = getattr(version, source)
        except Exception:  # noqa: BLE001
            continue
        if values is not None:
            payload[target] = [_version_ref_name(item) for item in values]
    return payload


def _versions(arcpy: Any, workspace: str) -> tuple[list[dict[str, Any]], bool]:
    da_list = getattr(getattr(arcpy, "da", None), "ListVersions", None)
    if callable(da_list):
        return [_version_payload(item) for item in da_list(workspace) or []], True
    list_names = getattr(arcpy, "ListVersions", None)
    if not callable(list_names):
        raise RuntimeError("当前 ArcPy 不支持 ListVersions")
    return [{"name": str(item)} for item in list_names(workspace) or []], False


def list_versions(arcpy: Any, workspace_path: str) -> dict[str, Any]:
    """List visible enterprise versions with rich properties when supported."""
    workspace = _enterprise_workspace(workspace_path)
    if not workspace.lower().startswith("https://"):
        _require_exists(arcpy, workspace, "workspace_path")
    versions, detailed = _versions(arcpy, workspace)
    return {
        "workspace_path": workspace,
        "version_count": len(versions),
        "detailed": detailed,
        "versions": versions,
    }


def create_version(
    arcpy: Any,
    workspace_path: str,
    parent_version: str,
    version_name: str,
    *,
    access_permission: str = "PRIVATE",
    description: str = "",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    workspace = _enterprise_workspace(workspace_path)
    if not workspace.lower().startswith("https://"):
        _require_exists(arcpy, workspace, "workspace_path")
    parent = _version_name(parent_version, "parent_version")
    name = _version_name(version_name)
    access = _enum(access_permission, _ACCESS_LEVELS, "access_permission")
    details = str(description or "").strip()
    if len(details) > 64 or any(ch in details for ch in ("\r", "\n")):
        raise RuntimeError("description 不能超过 64 个字符且不能包含换行")
    before, _ = _versions(arcpy, workspace)
    before_names = {str(item["name"]) for item in before}
    if name in before_names or any(value.endswith(f".{name}") for value in before_names):
        raise RuntimeError(f"版本已存在：{name}")
    result = _management_tool(arcpy, "CreateVersion")(
        workspace,
        parent,
        name,
        access,
        details,
    )
    after, _ = _versions(arcpy, workspace)
    matches = [
        str(item["name"])
        for item in after
        if str(item["name"]) == name or str(item["name"]).endswith(f".{name}")
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "CreateVersion 已返回，但无法在版本列表中唯一验证新版本；不要自动重试"
        )
    return {
        "workspace_path": workspace,
        "created": True,
        "version_name": matches[0],
        "parent_version": parent,
        "access_permission": access,
        "messages": _messages(result),
    }


def _parse_history_date(value: str) -> datetime | str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("history_date 必须为 ISO-8601 日期时间") from exc


def _member_version(member: Any) -> str | None:
    try:
        properties = member.connectionProperties
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(properties, dict):
        return None
    candidates = [properties]
    info = properties.get("connection_info")
    if isinstance(info, dict):
        candidates.append(info)
    for mapping in candidates:
        for key in ("version", "version_name"):
            value = mapping.get(key)
            if value:
                return str(value)
    return None


def change_version(
    arcpy: Any,
    member: Any,
    version_type: str,
    *,
    version_name: str = "",
    history_date: str = "",
    include_participating: bool = True,
) -> dict[str, Any]:
    """Change one already-resolved feature layer or table view workspace."""
    require_allow_enterprise_write()
    kind = _enum(version_type, _VERSION_TYPES, "version_type")
    name = _version_name(version_name) if str(version_name or "").strip() else ""
    date_value = _parse_history_date(history_date)
    if kind == "HISTORICAL":
        if not name and not date_value:
            raise RuntimeError("HISTORICAL 至少需要 version_name 或 history_date")
    else:
        if not name:
            raise RuntimeError(f"{kind} 必须提供 version_name")
        if date_value:
            raise RuntimeError("history_date 仅适用于 HISTORICAL")
    participating = "INCLUDE" if include_participating else "EXCLUDE"
    result = _management_tool(arcpy, "ChangeVersion")(
        member,
        kind,
        name,
        date_value,
        participating,
    )
    observed = _member_version(member)
    if observed is not None and name and observed.casefold() != name.casefold():
        raise RuntimeError(
            "ChangeVersion 已返回，但图层连接的版本与请求不一致；不要自动重试"
        )
    return {
        "changed": True,
        "version_type": kind,
        "version_name": name or None,
        "history_date": date_value.isoformat() if isinstance(date_value, datetime) else None,
        "include_participating": include_participating,
        "observed_version": observed,
        "verified": observed is not None if name else False,
        "messages": _messages(result),
    }


def _version_list(values: list[str], label: str) -> list[str]:
    if not isinstance(values, list):
        raise RuntimeError(f"{label} 必须为字符串数组")
    if len(values) > 100:
        raise RuntimeError(f"{label} 最多包含 100 个版本")
    result = [_version_name(value, f"{label}[{index}]") for index, value in enumerate(values)]
    if len(set(result)) != len(result):
        raise RuntimeError(f"{label} 不能包含重复版本")
    return result


def reconcile_versions(
    arcpy: Any,
    workspace_path: str,
    target_version: str,
    edit_versions: list[str],
    *,
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
) -> dict[str, Any]:
    """Reconcile a precisely confirmed version scope.

    Reconcile itself can resolve conflicts and therefore always uses the
    destructive gate, even when posting and deleting are disabled.
    """
    require_allow_enterprise_write()
    require_allow_destructive()
    workspace = _enterprise_workspace(workspace_path)
    if not workspace.lower().startswith("https://"):
        _require_exists(arcpy, workspace, "workspace_path")
    target = _version_name(target_version, "target_version")
    edits = _version_list(edit_versions, "edit_versions")
    mode = _enum(reconcile_mode, _RECONCILE_MODES, "reconcile_mode")
    if mode == "ALL_VERSIONS" and not edits:
        raise RuntimeError("ALL_VERSIONS 必须显式提供非空 edit_versions")
    if with_delete and not with_post:
        raise RuntimeError("with_delete=true 只允许与 with_post=true 同时使用")
    expected_action = (
        "RECONCILE_POST_AND_DELETE"
        if with_delete
        else "RECONCILE_AND_POST"
        if with_post
        else "RECONCILE"
    )
    if confirm_action != expected_action:
        raise RuntimeError(f"confirm_action 必须精确等于 {expected_action!r}")
    if confirm_target_version != target:
        raise RuntimeError("confirm_target_version 必须精确回显 target_version")
    confirmed_edits = _version_list(
        confirm_edit_versions if confirm_edit_versions is not None else [],
        "confirm_edit_versions",
    )
    if confirmed_edits != edits:
        raise RuntimeError("confirm_edit_versions 必须按相同顺序精确回显 edit_versions")
    conflict_kind = _enum(
        conflict_definition,
        _CONFLICT_DEFINITIONS,
        "conflict_definition",
    )
    resolution = _enum(
        conflict_resolution,
        _CONFLICT_RESOLUTIONS,
        "conflict_resolution",
    )
    log_path = ""
    if str(out_log_path or "").strip():
        log_path = validate_new_output_in_export_root(out_log_path, "out_log_path")
        if not log_path.casefold().endswith(".txt"):
            raise RuntimeError("out_log_path 必须以 .txt 结尾")
        parent = os.path.dirname(log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    result = _management_tool(arcpy, "ReconcileVersions")(
        workspace,
        mode,
        target,
        edits,
        "LOCK_ACQUIRED" if acquire_locks else "NO_LOCK_ACQUIRED",
        "ABORT_CONFLICTS" if abort_on_conflicts else "NO_ABORT",
        conflict_kind,
        resolution,
        "POST" if with_post else "NO_POST",
        "DELETE_VERSION" if with_delete else "KEEP_VERSION",
        log_path,
    )
    return {
        "workspace_path": workspace,
        "reconciled": True,
        "reconcile_mode": mode,
        "target_version": target,
        "edit_versions": edits,
        "posted": with_post,
        "deleted_after_post": with_delete,
        "out_log_path": log_path or None,
        "messages": _messages(result),
    }


def post_version(
    arcpy: Any,
    workspace_path: str,
    version_name: str,
    target_version: str,
    *,
    abort_on_conflicts: bool = True,
    conflict_definition: str = "BY_OBJECT",
    conflict_resolution: str = "FAVOR_TARGET_VERSION",
    out_log_path: str = "",
    confirm_version_name: str = "",
    confirm_target_version: str = "",
    confirm_action: str = "",
) -> dict[str, Any]:
    """Reconcile one exact edit version and post it with the supported GP tool."""
    name = _version_name(version_name)
    target = _version_name(target_version, "target_version")
    _reject_default_version(name, "ReconcileVersions POST")
    if name == target:
        raise RuntimeError("version_name 与 target_version 不能相同")
    if confirm_version_name != name:
        raise RuntimeError("confirm_version_name 必须精确回显 version_name")
    if confirm_target_version != target:
        raise RuntimeError("confirm_target_version 必须精确回显 target_version")
    if confirm_action != "RECONCILE_AND_POST":
        raise RuntimeError("confirm_action 必须精确等于 'RECONCILE_AND_POST'")
    result = reconcile_versions(
        arcpy,
        workspace_path,
        target,
        [name],
        reconcile_mode="ALL_VERSIONS",
        acquire_locks=True,
        abort_on_conflicts=abort_on_conflicts,
        conflict_definition=conflict_definition,
        conflict_resolution=conflict_resolution,
        with_post=True,
        with_delete=False,
        out_log_path=out_log_path,
        confirm_action="RECONCILE_AND_POST",
        confirm_target_version=target,
        confirm_edit_versions=[name],
    )
    result.update(
        {
            "operation": "RECONCILE_AND_POST",
            "version_name": name,
            "irreversible": True,
        }
    )
    return result


def delete_version(
    arcpy: Any,
    workspace_path: str,
    version_name: str,
    *,
    confirm_version_name: str = "",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    require_allow_destructive()
    workspace = _enterprise_workspace(workspace_path)
    if not workspace.lower().startswith("https://"):
        _require_exists(arcpy, workspace, "workspace_path")
    name = _version_name(version_name)
    _reject_default_version(name, "DeleteVersion")
    if confirm_version_name != name:
        raise RuntimeError("confirm_version_name 必须精确回显 version_name")
    before, _ = _versions(arcpy, workspace)
    if name not in {str(item["name"]) for item in before}:
        raise RuntimeError(f"版本不存在或名称未精确匹配：{name}")
    result = _management_tool(arcpy, "DeleteVersion")(workspace, name)
    after, _ = _versions(arcpy, workspace)
    if name in {str(item["name"]) for item in after}:
        raise RuntimeError("DeleteVersion 已返回但版本仍存在；不要自动重试")
    return {
        "workspace_path": workspace,
        "deleted": True,
        "version_name": name,
        "messages": _messages(result),
    }


def register_as_versioned(
    arcpy: Any,
    dataset_path: str,
    *,
    edit_to_base: str = "NO_EDITS_TO_BASE",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    path = _dataset_path(dataset_path)
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    option = _enum(edit_to_base, _EDIT_TO_BASE, "edit_to_base")
    before = arcpy.Describe(path)
    if bool(getattr(before, "isVersioned", False)):
        return {
            "dataset_path": path,
            "registered": True,
            "changed": False,
            "already_versioned": True,
        }
    result = _management_tool(arcpy, "RegisterAsVersioned")(path, option)
    after = arcpy.Describe(path)
    if not bool(getattr(after, "isVersioned", False)):
        raise RuntimeError(
            "RegisterAsVersioned 已返回但 Describe.isVersioned 仍为 false；不要自动重试"
        )
    return {
        "dataset_path": path,
        "registered": True,
        "changed": True,
        "edit_to_base": option,
        "messages": _messages(result),
    }


def unregister_as_versioned(
    arcpy: Any,
    dataset_path: str,
    *,
    keep_edit: str = "KEEP_EDIT",
    compress_default: str = "NO_COMPRESS_DEFAULT",
    confirm_dataset_path: str = "",
    confirm_discard_edits: str = "",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    require_allow_destructive()
    if confirm_dataset_path != dataset_path:
        raise RuntimeError("confirm_dataset_path 必须精确回显 dataset_path")
    path = _dataset_path(dataset_path)
    _require_exists(arcpy, path, "dataset_path")
    _require_schema_lock(arcpy, path)
    keep = _enum(keep_edit, _KEEP_EDIT, "keep_edit")
    compress = _enum(compress_default, _COMPRESS_DEFAULT, "compress_default")
    if keep == "KEEP_EDIT" and compress == "COMPRESS_DEFAULT":
        raise RuntimeError("KEEP_EDIT 与 COMPRESS_DEFAULT 不能组合")
    if keep == "NO_KEEP_EDIT" and confirm_discard_edits != "DISCARD_OUTSTANDING_EDITS":
        raise RuntimeError(
            "NO_KEEP_EDIT 必须令 confirm_discard_edits 精确等于 "
            "'DISCARD_OUTSTANDING_EDITS'"
        )
    before = arcpy.Describe(path)
    if not bool(getattr(before, "isVersioned", False)):
        raise RuntimeError("dataset_path 当前未注册为版本化")
    result = _management_tool(arcpy, "UnregisterAsVersioned")(
        path,
        keep,
        compress,
    )
    after = arcpy.Describe(path)
    if bool(getattr(after, "isVersioned", False)):
        raise RuntimeError(
            "UnregisterAsVersioned 已返回但 Describe.isVersioned 仍为 true；不要自动重试"
        )
    return {
        "dataset_path": path,
        "unregistered": True,
        "keep_edit": keep,
        "compress_default": compress,
        "discarded_outstanding_edits": keep == "NO_KEEP_EDIT",
        "messages": _messages(result),
    }
