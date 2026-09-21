"""Generic GP execution engine and toolbox listing."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any

from arcgis_pro_mcp import session_refs
from arcgis_pro_mcp.paths import (
    inline_db_password_allowed,
    is_probably_path,
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)
from arcgis_pro_mcp.redaction import redact_text

_TOOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]+$")
_GENERIC_GP_ENABLE_ENV = "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP"

# The generic endpoint must not become a destructive or Python/script execution
# surface. Match the operation rather than an exact qualified spelling so modern names
# (``management.Delete``), legacy aliases (``Delete_management``), casing, and
# toolbox aliases cannot bypass this boundary.
_HARD_DENIED_OPERATION_PREFIXES = (
    "calculatefield",
    "calculatevalue",
    "delete",
    "execute",
    "rastercalculator",
    "run",
    "truncate",
)
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
_OUTPUT_CONTAINER_KEYS = frozenset(
    {
        "out_folder",
        "out_folder_path",
        "out_path",
        "out_workspace",
        "target_workspace",
        "workspace_out",
    }
)
_OUTPUT_NAME_KEYS = frozenset(
    {
        "feature_class_name",
        "out_name",
        "output_name",
        "raster_dataset_name",
        "table_name",
    }
)


def generic_gp_enabled() -> bool:
    value = os.environ.get(_GENERIC_GP_ENABLE_ENV, "1").strip().lower()
    return value in ("1", "true", "yes", "on")


def generic_gp_allowlist() -> list[str]:
    """Compatibility field: deployment allowlists are no longer used."""
    return []


def _operation_name(tool_name: str) -> str:
    """Return a toolbox-independent operation name for safety classification."""
    leaf = tool_name.rsplit(".", 1)[-1]
    if "_" in leaf:
        # ArcPy's legacy aliases are shaped like ``Delete_management``.
        leaf = leaf.rsplit("_", 1)[0]
    return re.sub(r"[^A-Za-z0-9]", "", leaf).lower()


def _ensure_tool_is_not_hard_denied(tool_name: str) -> None:
    operation = _operation_name(tool_name)
    if operation.startswith(_HARD_DENIED_OPERATION_PREFIXES):
        raise RuntimeError(
            f"工具 {tool_name!r} 属于通用 GP 永久拒绝的破坏性或代码执行操作；"
            "请使用带精确目标、确认值和专用门禁的语义工具。"
        )


def _ensure_generic_tool_allowed(tool_name: str) -> None:
    if not generic_gp_enabled():
        raise RuntimeError(f"通用 GP 已禁用。设置 {_GENERIC_GP_ENABLE_ENV}=1 或移除此配置即可启用。")
    _ensure_tool_is_not_hard_denied(tool_name)


def _resolve_registered_tool(arcpy: Any, tool_name: str) -> tuple[Any, list[Any]]:
    """Resolve a GP catalog entry, never an arbitrary attribute of arcpy."""
    parts = tool_name.split(".")
    if len(parts) == 2:
        candidate = f"{parts[1]}_{parts[0]}"
    elif len(parts) == 1:
        candidate = parts[0]
    else:
        raise RuntimeError("tool_name 格式须为 'module.Tool' 或 'Tool_toolbox'")
    list_tools = getattr(arcpy, "ListTools", None)
    if not callable(list_tools):
        raise RuntimeError("当前 ArcPy 缺少 ListTools，无法确认原生 GP 工具")
    registered = next((name for name in list_tools() or [] if name.casefold() == candidate.casefold()), None)
    if registered is None:
        raise RuntimeError(f"未找到已注册的原生 GP 工具: {tool_name}")
    describe_parameters = getattr(arcpy, "GetParameterInfo", None)
    if not callable(describe_parameters):
        raise RuntimeError("当前 ArcPy 缺少 GetParameterInfo，无法确认原生 GP 参数")
    parameters = [p for p in describe_parameters(registered) if p.parameterType != "Derived"]
    func = getattr(arcpy, registered, None)
    if callable(func):
        return func, parameters
    # Spatial/Image Analyst GP entries often have no top-level Python alias.
    # arcpy.gp exposes their native positional contract, including output paths;
    # arcpy.sa/ia functions instead return Raster objects and have other signatures.
    native = getattr(getattr(arcpy, "gp", None), registered, None)
    if not callable(native):
        raise RuntimeError(f"已注册的 GP 工具不可调用: {registered}")

    def invoke_native(**values: Any) -> Any:
        last = max((i for i, p in enumerate(parameters) if p.name in values), default=-1)
        return native(*(values.get(p.name, "#") for p in parameters[:last + 1]))

    return invoke_native, parameters


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


def _sanitize_parameter_value(key: str | None, value: Any, native_mode: str | None = None) -> Any:
    inferred = _path_mode_for_key(key)
    mode = "secret" if inferred == "secret" else native_mode or inferred
    if isinstance(value, dict):
        return {k: _sanitize_parameter_value(str(k), v, native_mode) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_parameter_value(key, item, native_mode) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_parameter_value(key, item, native_mode) for item in value)
    if isinstance(value, str):
        s = value.strip()
        if session_refs.is_reference(s):
            if mode == "output":
                raise RuntimeError(f"输出参数 {key!r} 不允许使用临时对象引用")
            return session_refs.resolve(s)
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


def _controlled_output_targets(parameters: dict[str, Any], output_names: set[str]) -> list[str]:
    """Return exact durable outputs and reject ambiguous container/name outputs."""

    container_keys = {
        str(key).strip().lower()
        for key, value in parameters.items()
        if str(key).strip().lower() in _OUTPUT_CONTAINER_KEYS and value not in (None, "")
    }
    name_keys = {
        str(key).strip().lower()
        for key, value in parameters.items()
        if str(key).strip().lower() in _OUTPUT_NAME_KEYS and value not in (None, "")
    }
    if container_keys or name_keys:
        raise RuntimeError(
            "通用 GP 不接受输出容器与名称分离的参数，因无法可靠证明最终目标；"
            "请改用具有完整 out_* 路径的工具或专用语义工具。"
        )
    outputs: list[str] = []
    for key, value in parameters.items():
        if key not in output_names:
            continue
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for item in candidates:
            if not isinstance(item, str) or not item.strip():
                continue
            outputs.append(validate_gp_output_path(item, str(key)))
    return list(dict.fromkeys(outputs))


def _output_exists(arcpy: Any, path: str) -> bool:
    try:
        if bool(arcpy.Exists(path)):
            return True
    except Exception:  # noqa: BLE001
        pass
    return os.path.exists(path)


@contextmanager
def _overwrite_disabled(arcpy: Any):
    manager = getattr(arcpy, "EnvManager", None)
    if not callable(manager):
        raise RuntimeError("当前 ArcPy 缺少 EnvManager，无法强制 overwriteOutput=False")
    with manager(overwriteOutput=False):
        yield


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
    func, native_parameters = _resolve_registered_tool(arcpy, tn)
    require_gp_output_root_mandatory()
    supplied_parameters = parameters or {}
    if not isinstance(supplied_parameters, dict):
        raise RuntimeError("parameters 必须为对象")
    by_name = {p.name: p for p in native_parameters}
    unknown = set(supplied_parameters) - set(by_name)
    if unknown:
        raise RuntimeError(f"原生 GP 参数无效 ({tn}): {', '.join(sorted(unknown))}")
    params = {
        key: _sanitize_parameter_value(key, value, by_name[key].direction.lower())
        for key, value in supplied_parameters.items()
    }
    targets = _controlled_output_targets(params, {p.name for p in native_parameters if p.direction == "Output"})
    if not targets:
        raise RuntimeError(
            "通用 GP 只允许创建受 ARCGIS_PRO_MCP_GP_OUTPUT_ROOT 约束的持久输出；"
            "就地修改或无输出操作必须使用专用语义工具。"
        )
    existing = [target for target in targets if _output_exists(arcpy, target)]
    if existing:
        raise RuntimeError(f"通用 GP 拒绝覆盖已有输出：{existing[:20]}")
    with _overwrite_disabled(arcpy):
        result = func(**params)
    msgs: list[str] = []
    try:
        for i in range(result.messageCount):
            msgs.append(redact_text(result.getMessage(i)))
    except Exception:
        try:
            msgs.append(redact_text(result))
        except Exception:
            pass
    return "\n".join(msgs) if msgs else "OK"


def get_messages(arcpy: Any) -> list[str]:
    msgs: list[str] = []
    try:
        count = arcpy.GetMessageCount()
        for i in range(count):
            msgs.append(redact_text(arcpy.GetMessage(i)))
    except Exception as ex:
        msgs.append(f"获取消息失败: {redact_text(ex)}")
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
