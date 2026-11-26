"""Shared geoprocessing validation, existence, schema-lock, and message helpers.

This module intentionally avoids importing ArcPy at module load time so callers
can inject ArcPy or fakes for unit testing outside ArcGIS Pro.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from typing import Any

from arcgis_pro_mcp.redaction import redact_text


def gp_messages(result_or_arcpy: Any) -> list[str]:
    """Extract and redact geoprocessing messages from a GP Result or arcpy module."""
    if result_or_arcpy is None:
        return []
    messages: list[str] = []
    try:
        if hasattr(result_or_arcpy, "messageCount") and callable(
            getattr(result_or_arcpy, "getMessage", None)
        ):
            for index in range(int(result_or_arcpy.messageCount)):
                messages.append(redact_text(result_or_arcpy.getMessage(index)))
            return messages
    except Exception:  # noqa: BLE001
        pass
    try:
        get_count = getattr(result_or_arcpy, "GetMessageCount", None)
        get_message = getattr(result_or_arcpy, "GetMessage", None)
        if callable(get_count) and callable(get_message):
            for index in range(int(get_count())):
                messages.append(redact_text(get_message(index)))
            return messages
    except Exception:  # noqa: BLE001
        pass
    for attr in ("getMessages", "GetMessages"):
        getter = getattr(result_or_arcpy, attr, None)
        if callable(getter):
            try:
                combined = str(getter())
            except Exception:  # noqa: BLE001
                combined = ""
            if combined:
                messages.append(redact_text(combined))
            break
    return messages


def required_text(
    value: Any,
    field_name: str,
    *,
    max_length: int | None = None,
    strict_str: bool = False,
) -> str:
    """Validate that a text parameter is non-empty after stripping whitespace."""
    if strict_str and not isinstance(value, str):
        raise RuntimeError(f"{field_name} 必须为字符串")
    cleaned = (value if isinstance(value, str) else str(value or "")).strip()
    if not cleaned:
        raise RuntimeError(f"{field_name} 不能为空")
    if "\x00" in cleaned or (max_length is not None and len(cleaned) > max_length):
        raise RuntimeError(f"{field_name} 无效")
    return cleaned


def validated_enum(
    value: Any,
    allowed: Collection[str] | Mapping[str, str],
    field_name: str,
) -> str:
    """Normalize and validate an enum parameter against an allowlist or canonical map."""
    normalized = str(value or "").strip().upper()
    if isinstance(allowed, Mapping):
        if normalized not in allowed:
            raise RuntimeError(f"{field_name} 须为 {sorted(allowed)}")
        return allowed[normalized]
    if normalized not in allowed:
        raise RuntimeError(f"{field_name} 须为 {sorted(allowed)}")
    return normalized


def arcpy_exists(
    arcpy_mod: Any,
    path: Any,
    *,
    require_api: bool = False,
    missing_api_message: str = "当前 ArcPy 不支持 Exists，无法核验数据集",
    fallback_os_exists: bool = False,
) -> bool:
    """Check whether a dataset or file path exists via ``arcpy.Exists``."""
    checker = getattr(arcpy_mod, "Exists", None)
    if callable(checker):
        try:
            if bool(checker(path)):
                return True
        except Exception:  # noqa: BLE001
            if not fallback_os_exists:
                raise
    elif require_api:
        raise RuntimeError(missing_api_message)
    return fallback_os_exists and isinstance(path, str) and os.path.exists(path)


def require_exists(
    arcpy_mod: Any,
    path: Any,
    label: str = "dataset_path",
    *,
    require_api: bool = False,
    missing_api_message: str = "当前 ArcPy 不支持 Exists，无法核验数据集",
    fallback_os_exists: bool = False,
) -> None:
    """Raise ``RuntimeError`` when the target dataset/path does not exist."""
    checker = getattr(arcpy_mod, "Exists", None)
    if not callable(checker):
        if require_api:
            raise RuntimeError(missing_api_message)
        if fallback_os_exists:
            if isinstance(path, str) and os.path.exists(path):
                return
            raise RuntimeError(f"{label} 不存在：{path}")
        return
    if not arcpy_exists(
        arcpy_mod,
        path,
        require_api=require_api,
        missing_api_message=missing_api_message,
        fallback_os_exists=fallback_os_exists,
    ):
        raise RuntimeError(f"{label} 不存在：{path}")


def require_schema_lock(
    arcpy_mod: Any,
    path: str,
    *,
    require_api: bool = False,
) -> None:
    """Raise ``RuntimeError`` when a schema lock cannot be acquired."""
    tester = getattr(arcpy_mod, "TestSchemaLock", None)
    if not callable(tester):
        if require_api:
            raise RuntimeError("当前 ArcPy 不支持 TestSchemaLock，拒绝方案变更")
        return
    if not bool(tester(path)):
        raise RuntimeError(f"无法获取方案锁：{path}")


def management_tool(
    arcpy_mod: Any,
    name: str,
    *,
    prefix: str = "当前 ArcPy 不支持",
) -> Any:
    """Return ``arcpy.management.<name>`` or fail closed with a clear message."""
    tool = getattr(getattr(arcpy_mod, "management", None), name, None)
    if not callable(tool):
        raise RuntimeError(f"{prefix} arcpy.management.{name}")
    return tool
