"""Path validation for MCP exports, GP outputs, and optional input roots."""

from __future__ import annotations

import os
import re
from typing import Any

_PROJECT_ROOT_ENV = "ARCGIS_PRO_MCP_PROJECT_ROOTS"
_INPUT_ROOT_ENV = "ARCGIS_PRO_MCP_INPUT_ROOTS"
_INLINE_DB_PASSWORD_ENV = "ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD"
_DESTRUCTIVE_ENV = "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE"
_CIM_WRITE_ENV = "ARCGIS_PRO_MCP_ALLOW_CIM_WRITE"
_PUBLISH_ENV = "ARCGIS_PRO_MCP_ALLOW_PUBLISH"
_PUBLIC_SHARE_ENV = "ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE"
_PUBLISH_OVERWRITE_ENV = "ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE"
_ENTERPRISE_WRITE_ENV = "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE"
_ABS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_RESERVED_NAME_RE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def normalize_path(p: str) -> str:
    return os.path.normpath(p.strip().strip('"'))


def require_absolute(path: str, label: str) -> None:
    if not os.path.isabs(path):
        raise RuntimeError(f"{label} 必须为绝对路径")


def validate_output_name(value: str, label: str, *, maximum: int = 255) -> str:
    """Validate one output basename; never let a GP name smuggle a path."""
    if not isinstance(value, str):
        raise RuntimeError(f"{label} 必须为字符串")
    name = value.strip()
    if not name or len(name) > maximum or name in {".", ".."}:
        raise RuntimeError(f"{label} 必须为有效的单一名称")
    if name.endswith((" ", ".")) or any(ord(char) < 32 for char in name):
        raise RuntimeError(f"{label} 含 Windows 不支持的字符")
    if any(char in name for char in ('/', '\\', ':', '*', '?', '"', '<', '>', '|')):
        raise RuntimeError(f"{label} 只能是 basename，不能包含路径或通配符")
    if _WINDOWS_RESERVED_NAME_RE.fullmatch(name):
        raise RuntimeError(f"{label} 不能使用 Windows 保留设备名")
    return name


def path_under_root(path: str, root: str) -> bool:
    root_real = os.path.realpath(os.path.expanduser(root))
    path_real = os.path.realpath(os.path.expanduser(path))
    if not root_real:
        return True
    root_cmp = os.path.normcase(root_real).lower()
    path_cmp = os.path.normcase(path_real).lower()
    try:
        return os.path.commonpath([path_cmp, root_cmp]) == root_cmp
    except ValueError:
        return False


def is_probably_path(value: str) -> bool:
    s = (value or "").strip().strip('"')
    if not s or s in {"#", "CURRENT", "in_memory", "memory"}:
        return False
    if os.path.isabs(s) or _ABS_DRIVE_RE.match(s):
        return True
    if any(sep in s for sep in (os.sep, "/", "\\")):
        return True
    lower = s.lower()
    return lower.endswith(
        (
            ".aprx",
            ".gdb",
            ".sde",
            ".shp",
            ".dbf",
            ".tif",
            ".tiff",
            ".img",
            ".lyrx",
            ".csv",
            ".xlsx",
            ".xls",
            ".json",
            ".geojson",
            ".kml",
            ".kmz",
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
        ),
    )


def _roots_from_env(var_name: str) -> list[str]:
    raw = os.environ.get(var_name, "").strip()
    if not raw:
        return []
    return [x.strip().strip('"') for x in raw.split(os.pathsep) if x.strip()]


def project_roots() -> list[str]:
    roots = _roots_from_env(_PROJECT_ROOT_ENV)
    if roots:
        return roots
    return _roots_from_env(_INPUT_ROOT_ENV)


def validate_output_in_export_root(output_path: str, label: str) -> str:
    """Require exports to stay below an explicitly configured output root."""
    p = normalize_path(output_path)
    require_absolute(p, label)
    root = os.environ.get("ARCGIS_PRO_MCP_EXPORT_ROOT", "").strip().strip('"')
    if not root:
        raise RuntimeError(f"{label} 需要配置绝对路径 ARCGIS_PRO_MCP_EXPORT_ROOT")
    require_absolute(root, "ARCGIS_PRO_MCP_EXPORT_ROOT")
    if not path_under_root(p, root):
        rr = os.path.realpath(os.path.expanduser(root))
        raise RuntimeError(f"{label} 必须位于 ARCGIS_PRO_MCP_EXPORT_ROOT 内：{rr}")
    return p


def validate_new_output_in_export_root(output_path: str, label: str) -> str:
    """Validate an export target and fail closed rather than overwriting it."""

    path = validate_output_in_export_root(output_path, label)
    if os.path.lexists(path):
        raise RuntimeError(f"{label} 已存在；此工具拒绝隐式覆盖：{path}")
    return path


def validate_gp_output_path(output_path: str, label: str) -> str:
    """When ARCGIS_PRO_MCP_GP_OUTPUT_ROOT is set, outputs must stay under it."""
    p = normalize_path(output_path)
    require_absolute(p, label)
    root = os.environ.get("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT", "").strip().strip('"')
    if root and not path_under_root(p, root):
        rr = os.path.realpath(os.path.expanduser(root))
        raise RuntimeError(f"{label} 必须位于 ARCGIS_PRO_MCP_GP_OUTPUT_ROOT 内：{rr}")
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return p


def require_gp_output_root_mandatory() -> str:
    """写入型 GP 必须配置输出根目录且为绝对路径。"""
    root = os.environ.get("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT", "").strip().strip('"')
    if not root:
        raise RuntimeError(
            "写入型地理处理必须设置环境变量 ARCGIS_PRO_MCP_GP_OUTPUT_ROOT（绝对路径），"
            "且输出要素类路径须位于该目录下。"
        )
    require_absolute(root, "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT")
    return os.path.realpath(os.path.expanduser(root))


def validate_input_path_optional(input_path: Any, label: str) -> Any:
    """Restrict inputs when roots are configured; otherwise require an absolute path."""
    from arcgis_pro_mcp import session_refs

    if session_refs.is_reference(input_path):
        return session_refs.resolve(input_path)
    if not isinstance(input_path, str):
        raise RuntimeError(f"{label} 必须为绝对路径或有效的临时对象引用")
    p = normalize_path(input_path)
    require_absolute(p, label)
    roots = _roots_from_env(_INPUT_ROOT_ENV)
    if not roots:
        return p
    if not any(path_under_root(p, r) for r in roots):
        raise RuntimeError(
            f"{label} 必须位于 {_INPUT_ROOT_ENV} 中的某一目录下（使用 {os.pathsep!r} 分隔多个根路径）"
        )
    return p


def is_current_project_token(project_path: str) -> bool:
    return (project_path or "").strip().strip('"').upper() == "CURRENT"


def validate_project_path(project_path: str, label: str = "aprx_path") -> str:
    """Validate ArcGIS Pro project paths under PROJECT_ROOTS or INPUT_ROOTS."""
    if is_current_project_token(project_path):
        return "CURRENT"
    p = normalize_path(project_path)
    require_absolute(p, label)
    roots = project_roots()
    if roots and not any(path_under_root(p, r) for r in roots):
        source_env = _PROJECT_ROOT_ENV if _roots_from_env(_PROJECT_ROOT_ENV) else _INPUT_ROOT_ENV
        raise RuntimeError(
            f"{label} 必须位于 {source_env} 中的某一目录下（使用 {os.pathsep!r} 分隔多个根路径）"
        )
    return p


def writes_allowed() -> bool:
    return _env_enabled("ARCGIS_PRO_MCP_ALLOW_WRITE")


def require_allow_write() -> None:
    if not writes_allowed():
        raise RuntimeError(
            "写入类操作已禁用。设置 ARCGIS_PRO_MCP_ALLOW_WRITE=1 以启用：保存工程、修改图层、"
            "按属性/位置选择、地图框缩放到书签、添加/移除图层、写入型 GP、Join 与布局文本等。"
        )


def inline_db_password_allowed() -> bool:
    return _env_enabled(_INLINE_DB_PASSWORD_ENV)


def destructive_allowed() -> bool:
    return _env_enabled(_DESTRUCTIVE_ENV)


def require_allow_destructive() -> None:
    """Require both the ordinary write gate and the explicit destructive gate."""
    require_allow_write()
    if not destructive_allowed():
        raise RuntimeError(
            "破坏性操作已禁用。除 ARCGIS_PRO_MCP_ALLOW_WRITE=1 外，还必须设置 "
            "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=1，并提供工具要求的 expected_count/confirm_all。"
        )


def cim_write_allowed() -> bool:
    return _env_enabled(_CIM_WRITE_ENV)


def require_allow_cim_write() -> None:
    """Gate raw CIM mutation separately from ordinary semantic map writes."""
    require_allow_write()
    if not cim_write_allowed():
        raise RuntimeError(
            "原始 CIM 写入已禁用。设置 ARCGIS_PRO_MCP_ALLOW_CIM_WRITE=1 后才能使用；"
            "优先使用受约束的语义化样式或布局工具。"
        )


def publish_allowed() -> bool:
    return _env_enabled(_PUBLISH_ENV)


def require_allow_publish() -> None:
    """Gate external Portal/server publication independently from local writes."""
    require_allow_write()
    if not publish_allowed():
        raise RuntimeError(
            "发布操作已禁用。设置 ARCGIS_PRO_MCP_ALLOW_PUBLISH=1，并配置 Portal/Server "
            "目标白名单后才能发布。"
        )


def public_share_allowed() -> bool:
    return _env_enabled(_PUBLIC_SHARE_ENV)


def require_allow_public_share() -> None:
    require_allow_publish()
    if not public_share_allowed():
        raise RuntimeError(
            "公开共享已禁用。设置 ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE=1 后才能向 EVERYONE 共享。"
        )


def publish_overwrite_allowed() -> bool:
    return _env_enabled(_PUBLISH_OVERWRITE_ENV)


def require_allow_publish_overwrite() -> None:
    require_allow_publish()
    if not publish_overwrite_allowed():
        raise RuntimeError(
            "覆盖发布已禁用。设置 ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE=1，"
            "并提供精确服务标识后才能覆盖。"
        )


def enterprise_write_allowed() -> bool:
    return _env_enabled(_ENTERPRISE_WRITE_ENV)


def require_allow_enterprise_write() -> None:
    require_allow_write()
    if not enterprise_write_allowed():
        raise RuntimeError(
            "企业级版本/协调/提交写入已禁用。设置 "
            "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE=1 后才能执行。"
        )
