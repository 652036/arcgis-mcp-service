"""Generic GP execution engine and toolbox listing."""

from __future__ import annotations

import os
import re
from typing import Any

from arcgis_pro_mcp.paths import (
    inline_db_password_allowed,
    is_probably_path,
    require_allow_write,
    validate_gp_output_path,
    validate_input_path_optional,
)

_TOOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]+$")
_GENERIC_GP_ENABLE_ENV = "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP"
_GENERIC_GP_ALLOWLIST_ENV = "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST"
_GENERIC_GP_SPLIT_RE = re.compile(r"[\n,;]+")
_OUTPUT_KEY_WORDS = (
    "out",
    "output",
    "target_workspace",
    "workspace_out",
)
_INPUT_KEY_WORDS = (
    "in",
    "input",
    "source",
    "workspace",
    "dataset",
    "table",
    "features",
    "feature_class",
    "raster",
    "mask",
    "template",
    "connection",
)


def generic_gp_enabled() -> bool:
    value = os.environ.get(_GENERIC_GP_ENABLE_ENV, "").strip().lower()
    return value in ("1", "true", "yes", "on")


def generic_gp_allowlist() -> list[str]:
    raw = os.environ.get(_GENERIC_GP_ALLOWLIST_ENV, "").strip()
    if not raw:
        return []
    names = [x.strip() for x in _GENERIC_GP_SPLIT_RE.split(raw) if x.strip()]
    return sorted(dict.fromkeys(names))


def _ensure_generic_tool_allowed(tool_name: str) -> None:
    if not generic_gp_enabled():
        raise RuntimeError(
            f"通用 GP 已禁用。设置 {_GENERIC_GP_ENABLE_ENV}=1 并通过"
            f" {_GENERIC_GP_ALLOWLIST_ENV} 显式列出允许的工具名后才能使用。"
        )
    allowlist = {name.lower() for name in generic_gp_allowlist()}
    if not allowlist:
        raise RuntimeError(f"未配置 {_GENERIC_GP_ALLOWLIST_ENV}，拒绝执行通用 GP")
    if tool_name.lower() not in allowlist:
        raise RuntimeError(
            f"工具 {tool_name!r} 不在 {_GENERIC_GP_ALLOWLIST_ENV} 允许列表中"
        )


def _path_mode_for_key(key: str | None) -> str | None:
    if key is None:
        return None
    name = key.strip().lower()
    if not name:
        return None
    if any(token in name for token in ("password", "secret", "token")):
        return "secret"
    if name == "out_name":
        return None
    if name.startswith(_OUTPUT_KEY_WORDS) or "output" in name:
        return "output"
    if (
        name.startswith(_INPUT_KEY_WORDS)
        or name.endswith("_path")
        or name.endswith("_paths")
        or name.endswith("_workspace")
    ):
        return "input"
    return None


def _sanitize_parameter_value(key: str | None, value: Any) -> Any:
    mode = _path_mode_for_key(key)
    if isinstance(value, dict):
        return {k: _sanitize_parameter_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_parameter_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_parameter_value(key, item) for item in value)
    if isinstance(value, str):
        s = value.strip()
        if mode == "secret" and s:
            if inline_db_password_allowed():
                return value
            raise RuntimeError(
                "通用 GP 不允许内联敏感字符串参数。请改用工具专用入口，"
                "或由服务端通过环境变量/受控配置提供。"
            )
        if not is_probably_path(s):
            return value
        if mode == "output":
            return validate_gp_output_path(value, key or "output_path")
        if mode == "input":
            return validate_input_path_optional(value, key or "input_path")
        raise RuntimeError(
            f"参数 {key!r} 看起来像路径，但名称不足以判断其是输入还是输出路径；"
            "请使用更明确的参数名或专用 MCP 工具。"
        )
    return value


def run_tool(
    arcpy: Any,
    tool_name: str,
    parameters: dict[str, Any] | None = None,
) -> str:
    require_allow_write()
    tn = tool_name.strip()
    if not tn:
        raise RuntimeError("tool_name 不能为空")
    if not _TOOL_RE.match(tn):
        raise RuntimeError("tool_name 格式不合法（如 analysis.Buffer 或 management.Clip）")
    _ensure_generic_tool_allowed(tn)
    parts = tn.split(".")
    if len(parts) == 2:
        module_name, func_name = parts
        mod = getattr(arcpy, module_name, None)
        if mod is None:
            raise RuntimeError(f"未找到 arcpy 模块: {module_name}")
        func = getattr(mod, func_name, None)
        if func is None:
            raise RuntimeError(f"未找到工具: {tn}")
    elif len(parts) == 1:
        func = getattr(arcpy, parts[0], None)
        if func is None:
            raise RuntimeError(f"未找到工具: {tn}")
    else:
        raise RuntimeError("tool_name 格式须为 'module.Tool' 或 'Tool'")
    params = _sanitize_parameter_value(None, parameters or {})
    result = func(**params)
    msgs: list[str] = []
    try:
        for i in range(result.messageCount):
            msgs.append(result.getMessage(i))
    except Exception:
        try:
            msgs.append(str(result))
        except Exception:
            pass
    return "\n".join(msgs) if msgs else "OK"


def get_messages(arcpy: Any) -> list[str]:
    msgs: list[str] = []
    try:
        count = arcpy.GetMessageCount()
        for i in range(count):
            msgs.append(arcpy.GetMessage(i))
    except Exception as ex:
        msgs.append(f"获取消息失败: {ex!s}")
    return msgs


def list_toolboxes(arcpy: Any) -> list[str]:
    try:
        return [str(t) for t in arcpy.ListToolboxes() or []]
    except Exception as ex:
        raise RuntimeError(f"列出工具箱失败: {ex!s}") from ex


def list_tools_in_toolbox(arcpy: Any, toolbox: str) -> list[str]:
    tb = toolbox.strip()
    if not tb:
        raise RuntimeError("toolbox 不能为空")
    try:
        return [str(t) for t in arcpy.ListTools(f"*_{tb}") or []]
    except Exception as ex:
        raise RuntimeError(f"列出工具失败: {ex!s}") from ex
