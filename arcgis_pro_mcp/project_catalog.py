"""Constrained ArcGISProject catalog connection and style management."""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.paths import require_allow_write, validate_input_path_optional

_SAFE_TOOLBOX_SUFFIXES = frozenset({".atbx", ".tbx"})


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def _messages(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, (list, tuple)) else ([] if value is None else [value])


def _verified(items: Any, key: str, path: str) -> bool:
    return any(
        isinstance(item, dict)
        and isinstance(item.get(key), str)
        and _same_path(item[key], path)
        for item in (items or [])
    )


def add_folder_connection(
    project: Any,
    folder_path: str,
    alias: str = "",
    *,
    make_home: bool = False,
    validate: bool = True,
) -> dict[str, Any]:
    require_allow_write()
    path = validate_input_path_optional(folder_path, "folder_path")
    values = [dict(item) for item in (getattr(project, "folderConnections", None) or [])]
    if _verified(values, "connectionString", path):
        raise RuntimeError("folder connection 已存在")
    label = (alias or "").strip()
    if len(label) > 256 or "\r" in label or "\n" in label:
        raise RuntimeError("alias 无效")
    if make_home:
        for item in values:
            item["isHomeFolder"] = False
    values.append(
        {"connectionString": path, "alias": label, "isHomeFolder": bool(make_home)}
    )
    invalid = _messages(project.updateFolderConnections(values, bool(validate)))
    current = list(getattr(project, "folderConnections", None) or [])
    return {
        "folder_path": path,
        "verified": _verified(current, "connectionString", path),
        "invalid_connections": invalid,
        "folder_connections": current,
    }


def remove_folder_connection(project: Any, folder_path: str) -> dict[str, Any]:
    require_allow_write()
    path = validate_input_path_optional(folder_path, "folder_path")
    values = [dict(item) for item in (getattr(project, "folderConnections", None) or [])]
    matches = [item for item in values if _same_path(str(item.get("connectionString", "")), path)]
    if len(matches) != 1:
        raise RuntimeError(f"预期精确匹配一个 folder connection，实际为 {len(matches)}")
    if bool(matches[0].get("isHomeFolder")):
        raise RuntimeError("不能移除当前 home folder；请先把另一连接设为 home")
    retained = [item for item in values if item is not matches[0]]
    invalid = _messages(project.updateFolderConnections(retained, True))
    current = list(getattr(project, "folderConnections", None) or [])
    if _verified(current, "connectionString", path):
        raise RuntimeError("folder connection 移除后仍存在")
    return {"folder_path": path, "removed": True, "invalid_connections": invalid}


def add_database(
    project: Any,
    database_path: str,
    *,
    make_default: bool = False,
    validate: bool = True,
) -> dict[str, Any]:
    require_allow_write()
    path = validate_input_path_optional(database_path, "database_path")
    values = [dict(item) for item in (getattr(project, "databases", None) or [])]
    if _verified(values, "databasePath", path):
        raise RuntimeError("database connection 已存在")
    if make_default:
        for item in values:
            item["isDefaultDatabase"] = False
    values.append({"databasePath": path, "isDefaultDatabase": bool(make_default)})
    invalid = _messages(project.updateDatabases(values, bool(validate)))
    current = list(getattr(project, "databases", None) or [])
    return {
        "database_path": path,
        "verified": _verified(current, "databasePath", path),
        "invalid_connections": invalid,
        "databases": current,
    }


def remove_database(project: Any, database_path: str) -> dict[str, Any]:
    require_allow_write()
    path = validate_input_path_optional(database_path, "database_path")
    values = [dict(item) for item in (getattr(project, "databases", None) or [])]
    matches = [item for item in values if _same_path(str(item.get("databasePath", "")), path)]
    if len(matches) != 1:
        raise RuntimeError(f"预期精确匹配一个 database connection，实际为 {len(matches)}")
    if bool(matches[0].get("isDefaultDatabase")):
        raise RuntimeError("不能移除当前 default geodatabase；请先设置另一默认数据库")
    retained = [item for item in values if item is not matches[0]]
    invalid = _messages(project.updateDatabases(retained, True))
    current = list(getattr(project, "databases", None) or [])
    if _verified(current, "databasePath", path):
        raise RuntimeError("database connection 移除后仍存在")
    return {"database_path": path, "removed": True, "invalid_connections": invalid}


def add_toolbox(
    project: Any,
    toolbox_path: str,
    *,
    make_default: bool = False,
    validate: bool = True,
) -> dict[str, Any]:
    require_allow_write()
    path = validate_input_path_optional(toolbox_path, "toolbox_path")
    suffix = os.path.splitext(path)[1].casefold()
    if suffix not in _SAFE_TOOLBOX_SUFFIXES:
        raise RuntimeError(
            "toolbox_path 仅允许 .atbx 或 .tbx；MCP 不加载可执行 Python .pyt 工具箱"
        )
    values = [dict(item) for item in (getattr(project, "toolboxes", None) or [])]
    if _verified(values, "toolboxPath", path):
        raise RuntimeError("toolbox 已存在")
    if make_default:
        for item in values:
            item["isDefaultToolbox"] = False
    values.append({"toolboxPath": path, "isDefaultToolbox": bool(make_default)})
    invalid = _messages(project.updateToolboxes(values, bool(validate)))
    current = list(getattr(project, "toolboxes", None) or [])
    return {
        "toolbox_path": path,
        "verified": _verified(current, "toolboxPath", path),
        "invalid_toolboxes": invalid,
        "toolboxes": current,
    }


def validate_safe_toolbox_path(toolbox_path: str, label: str = "toolbox_path") -> str:
    """Validate a non-Python toolbox path before assigning it to a project."""

    path = validate_input_path_optional(toolbox_path, label)
    if os.path.splitext(path)[1].casefold() not in _SAFE_TOOLBOX_SUFFIXES:
        raise RuntimeError(f"{label} 仅允许 .atbx 或 .tbx；MCP 不加载可执行 Python .pyt 工具箱")
    return path


def remove_toolbox(project: Any, toolbox_path: str) -> dict[str, Any]:
    require_allow_write()
    path = validate_input_path_optional(toolbox_path, "toolbox_path")
    values = [dict(item) for item in (getattr(project, "toolboxes", None) or [])]
    matches = [item for item in values if _same_path(str(item.get("toolboxPath", "")), path)]
    if len(matches) != 1:
        raise RuntimeError(f"预期精确匹配一个 toolbox，实际为 {len(matches)}")
    if bool(matches[0].get("isDefaultToolbox")):
        raise RuntimeError("不能移除当前 default toolbox；请先设置另一默认工具箱")
    retained = [item for item in values if item is not matches[0]]
    invalid = _messages(project.updateToolboxes(retained, True))
    current = list(getattr(project, "toolboxes", None) or [])
    if _verified(current, "toolboxPath", path):
        raise RuntimeError("toolbox 移除后仍存在")
    return {"toolbox_path": path, "removed": True, "invalid_toolboxes": invalid}


def _style_value(style: str) -> str:
    value = (style or "").strip()
    if not value or len(value) > 4096 or "\r" in value or "\n" in value:
        raise RuntimeError("style 无效")
    if value.lower().endswith(".stylx") or any(separator in value for separator in ("/", "\\")):
        return validate_input_path_optional(value, "style")
    return value


def add_style(project: Any, style: str) -> dict[str, Any]:
    require_allow_write()
    value = _style_value(style)
    styles = [str(item) for item in (getattr(project, "styles", None) or [])]
    if any(item.casefold() == value.casefold() for item in styles):
        raise RuntimeError("style 已存在")
    invalid = _messages(project.updateStyles([*styles, value]))
    current = [str(item) for item in (getattr(project, "styles", None) or [])]
    verified = any(item.casefold() == value.casefold() for item in current)
    return {"style": value, "verified": verified, "invalid_styles": invalid, "styles": current}


def remove_style(project: Any, style: str) -> dict[str, Any]:
    require_allow_write()
    value = _style_value(style)
    styles = [str(item) for item in (getattr(project, "styles", None) or [])]
    matches = [item for item in styles if item.casefold() == value.casefold()]
    if len(matches) != 1:
        raise RuntimeError(f"预期精确匹配一个 style，实际为 {len(matches)}")
    invalid = _messages(project.updateStyles([item for item in styles if item != matches[0]]))
    current = [str(item) for item in (getattr(project, "styles", None) or [])]
    if any(item.casefold() == value.casefold() for item in current):
        raise RuntimeError("style 移除后仍存在")
    return {"style": value, "removed": True, "invalid_styles": invalid}


def list_style_items(
    project: Any,
    style: str,
    style_class: str,
    wildcard: str = "*",
    max_items: int = 200,
) -> dict[str, Any]:
    value = _style_value(style)
    kind = (style_class or "").strip()
    if not kind or len(kind) > 80 or "\r" in kind or "\n" in kind:
        raise RuntimeError("style_class 无效")
    pattern = (wildcard or "*").strip()
    if len(pattern) > 160 or "\r" in pattern or "\n" in pattern:
        raise RuntimeError("wildcard 无效")
    cap = max(1, min(int(max_items), 1000))
    rows = []
    for item in list(project.listStyleItems(value, kind, pattern))[:cap]:
        rows.append(
            {
                "name": getattr(item, "name", None),
                "category": getattr(item, "category", None),
                "tags": getattr(item, "tags", None),
                "style_class": getattr(item, "styleClass", kind),
            }
        )
    return {"style": value, "style_class": kind, "count": len(rows), "items": rows}
