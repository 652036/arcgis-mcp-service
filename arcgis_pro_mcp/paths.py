"""Path validation for MCP exports, GP outputs, and optional input roots."""

from __future__ import annotations

import os


def normalize_path(p: str) -> str:
    return os.path.normpath(p.strip().strip('"'))


def require_absolute(path: str, label: str) -> None:
    if not os.path.isabs(path):
        raise RuntimeError(f"{label} 必须为绝对路径")


def path_under_root(path: str, root: str) -> bool:
    root_real = os.path.realpath(os.path.expanduser(root))
    path_real = os.path.realpath(os.path.expanduser(path))
    if not root_real:
        return True
    sep = os.sep
    prefix = root_real.rstrip(sep) + sep
    return path_real == root_real or path_real.lower().startswith(prefix.lower())


def validate_output_in_export_root(output_path: str, label: str) -> str:
    """Honor ARCGIS_PRO_MCP_EXPORT_ROOT when set (same policy as PDF)."""
    p = normalize_path(output_path)
    require_absolute(p, label)
    root = os.environ.get("ARCGIS_PRO_MCP_EXPORT_ROOT", "").strip().strip('"')
    if root and not path_under_root(p, root):
        rr = os.path.realpath(os.path.expanduser(root))
        raise RuntimeError(f"{label} 必须位于 ARCGIS_PRO_MCP_EXPORT_ROOT 内：{rr}")
    return p


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


def validate_input_path_optional(input_path: str, label: str) -> str:
    """If ARCGIS_PRO_MCP_INPUT_ROOTS is set (os.pathsep-separated), restrict inputs."""
    p = normalize_path(input_path)
    require_absolute(p, label)
    raw = os.environ.get("ARCGIS_PRO_MCP_INPUT_ROOTS", "").strip()
    if not raw:
        return p
    roots = [x.strip().strip('"') for x in raw.split(os.pathsep) if x.strip()]
    if not any(path_under_root(p, r) for r in roots):
        raise RuntimeError(
            f"{label} 必须位于 ARCGIS_PRO_MCP_INPUT_ROOTS 中的某一目录下（使用 {os.pathsep!r} 分隔多个根路径）"
        )
    return p


def writes_allowed() -> bool:
    v = os.environ.get("ARCGIS_PRO_MCP_ALLOW_WRITE", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def require_allow_write() -> None:
    if not writes_allowed():
        raise RuntimeError(
            "写入类操作已禁用。设置 ARCGIS_PRO_MCP_ALLOW_WRITE=1 以启用：保存工程、修改图层、"
            "按属性/位置选择、地图框缩放到书签、添加/移除图层、写入型 GP、Join 与布局文本等。"
        )
