"""Generic GP execution engine and toolbox listing."""

from __future__ import annotations

import re
from typing import Any

from arcgis_pro_mcp.paths import require_allow_write

_TOOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]+$")


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
    params = parameters or {}
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
