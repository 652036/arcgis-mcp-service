"""Stdio MCP 自动转发到 ArcGIS Pro 窗口宿主。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from arcgis_pro_mcp import __version__ as PACKAGE_VERSION

ENV_IN_HOST = "ARCGIS_PRO_MCP_IN_PRO_HOST"
ENV_PORT = "ARCGIS_PRO_MCP_HOST_PORT"
DEFAULT_PORT = 17865
STATE_NAME = "arcgis-pro-mcp-host.json"
SERVICE_NAME = "arcgis-pro-mcp-window-host"
PROTOCOL_VERSION = 3
TOKEN_HEADER = "X-ArcGIS-Pro-MCP-Token"
HEALTH_TIMEOUT = 0.6
HOST_JOB_TIMEOUT = 300
CALL_TIMEOUT = HOST_JOB_TIMEOUT + 10
FORWARDED_ENV_KEYS = (
    "ARCGIS_PRO_MCP_ALLOW_WRITE",
    "ARCGIS_PRO_MCP_EXPORT_ROOT",
    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT",
    "ARCGIS_PRO_MCP_INPUT_ROOTS",
    "ARCGIS_PRO_MCP_PROJECT_ROOTS",
    "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP",
    "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST",
    "ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD",
)


def in_pro_host() -> bool:
    return os.environ.get(ENV_IN_HOST, "").strip() in ("1", "true", "yes", "on")


def configured_host_port() -> int | None:
    raw = os.environ.get(ENV_PORT, "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise RuntimeError(f"{ENV_PORT} 必须是 1-65535 的端口号")
    return int(raw)


def state_path() -> str:
    configured_port = configured_host_port()
    if configured_port is not None and configured_port != DEFAULT_PORT:
        stem, suffix = os.path.splitext(STATE_NAME)
        name = f"{stem}-{configured_port}{suffix}"
    else:
        name = STATE_NAME
    return os.path.join(tempfile.gettempdir(), name)


def read_state() -> dict[str, Any]:
    path = state_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.loads(f.read())
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class _HostEndpoint:
    base: str
    token: str
    state: dict[str, Any]


@dataclass(frozen=True)
class _ConfirmedTarget:
    session_id: str
    project_id: str
    project: str


_TARGET_LOCK = threading.Lock()
_CONFIRMED_TARGET: _ConfirmedTarget | None = None


def _project_identity(path: object) -> str:
    value = str(path or "").strip().strip('"')
    if not value or value.upper() == "CURRENT":
        return value.upper()
    return os.path.normcase(os.path.realpath(os.path.abspath(value))).lower()


def clear_confirmed_target() -> None:
    global _CONFIRMED_TARGET
    with _TARGET_LOCK:
        _CONFIRMED_TARGET = None


def confirm_host_target(snapshot: dict[str, Any]) -> None:
    global _CONFIRMED_TARGET
    session_id = str(snapshot.get("session_id") or "")
    project = str(snapshot.get("project") or "")
    project_id = _project_identity(project)
    if not session_id or not project_id:
        clear_confirmed_target()
        raise RuntimeError("窗口宿主没有可确认的 session/project 目标")
    with _TARGET_LOCK:
        _CONFIRMED_TARGET = _ConfirmedTarget(session_id, project_id, project)


def _target_is_confirmed(snapshot: dict[str, Any]) -> bool:
    session_id = str(snapshot.get("session_id") or "")
    project_id = _project_identity(snapshot.get("project"))
    with _TARGET_LOCK:
        target = _CONFIRMED_TARGET
    return bool(
        target
        and session_id == target.session_id
        and project_id
        and project_id == target.project_id
    )


def _require_confirmed_target(snapshot: dict[str, Any]) -> None:
    if _target_is_confirmed(snapshot):
        return
    with _TARGET_LOCK:
        target = _CONFIRMED_TARGET
    if target is None:
        raise RuntimeError(
            "尚未确认 ArcGIS Pro 窗口目标；请先调用 arcgis_pro_window_status，"
            "核对 current_project 后再执行 aprx_path=CURRENT"
        )
    raise RuntimeError(
        "ArcGIS Pro 窗口会话或工程已变化；"
        f"上次确认的是 {target.project!r}。请重新调用 arcgis_pro_window_status 并核对目标"
    )


def _endpoint_snapshot() -> _HostEndpoint:
    configured_port = configured_host_port()
    expected_port = configured_port or DEFAULT_PORT
    state = read_state()
    state_port = state.get("port")
    trusted_state = (
        state.get("service") == SERVICE_NAME
        and state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("package_version") == PACKAGE_VERSION
        and isinstance(state.get("token"), str)
        and bool(state.get("token"))
        and isinstance(state_port, int)
        and 1 <= state_port <= 65535
        and state_port == expected_port
    )
    if trusted_state:
        port = state_port
    else:
        port = expected_port
    state_token = state.get("token")
    token = state_token if trusted_state and isinstance(state_token, str) else ""
    return _HostEndpoint(base=f"http://127.0.0.1:{port}", token=token, state=state)


def host_port() -> int:
    return int(_endpoint_snapshot().base.rsplit(":", 1)[1])


def host_base() -> str:
    return _endpoint_snapshot().base


def _state_token() -> str:
    return _endpoint_snapshot().token


def _request_headers(*, json_body: bool = False, token: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if json_body:
        headers["Content-Type"] = "application/json; charset=utf-8"
    selected_token = _state_token() if token is None else token
    if selected_token:
        headers[TOKEN_HEADER] = selected_token
    return headers


def forwarded_environment() -> dict[str, str]:
    """Return the MCP policy settings that the in-Pro host must enforce."""
    return {key: os.environ[key] for key in FORWARDED_ENV_KEYS if key in os.environ}


def _read_json_response(resp: Any) -> dict[str, Any]:
    data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("窗口宿主返回了无效响应")
    return data


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(exc.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except Exception:  # noqa: BLE001
        pass
    return str(exc.reason or exc)


def host_health() -> dict[str, Any] | None:
    if in_pro_host():
        return {"ok": True, "in_host": True}
    endpoint = _endpoint_snapshot()
    req = urllib.request.Request(
        endpoint.base + "/health",
        method="GET",
        headers=_request_headers(token=endpoint.token),
    )
    try:
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
            data = _read_json_response(resp)
    except Exception:  # noqa: BLE001
        return None
    if (
        data.get("service") != SERVICE_NAME
        or data.get("protocol_version") != PROTOCOL_VERSION
        or data.get("package_version") != PACKAGE_VERSION
    ):
        return None
    data["_endpoint_base"] = endpoint.base
    data["_endpoint_token"] = endpoint.token
    return data


def host_available() -> bool:
    if in_pro_host():
        return False
    health = host_health()
    return bool(health and health.get("ok") and health.get("ready"))


def host_status(*, confirm_target: bool = False) -> dict[str, Any]:
    if in_pro_host():
        state = read_state()
        return {
            "window_attached": True,
            "execution_mode": "window",
            "window_host": "in-process",
            "current_project": state.get("project"),
            "host_pid": state.get("pid") or os.getpid(),
            "host_session_id": state.get("session_id"),
            "host_protocol_version": state.get("protocol_version") or PROTOCOL_VERSION,
            "host_package_version": state.get("package_version") or PACKAGE_VERSION,
        }
    health = host_health()
    if not health or not health.get("ok"):
        if confirm_target:
            clear_confirmed_target()
        return {"window_attached": False, "execution_mode": "file", "window_host": None}
    if confirm_target:
        if health.get("ready"):
            confirm_host_target(health)
        else:
            clear_confirmed_target()
    return {
        "window_attached": True,
        "execution_mode": "window",
        "window_host": health.get("_endpoint_base") or host_base(),
        "current_project": health.get("project"),
        "host_pid": health.get("pid"),
        "host_session_id": health.get("session_id"),
        "host_protocol_version": health.get("protocol_version"),
        "host_package_version": health.get("package_version"),
        "host_started_at": health.get("started_at"),
        "host_ready": health.get("ready", False),
        "host_state": health.get("state"),
        "host_context_error": health.get("context_error"),
        "host_busy": health.get("busy", False),
        "host_active_tool": health.get("active_tool"),
        "host_queue_depth": health.get("queue_depth", 0),
        "host_completed_calls": health.get("completed_calls", 0),
        "host_failed_calls": health.get("failed_calls", 0),
        "active_view": health.get("active_view"),
        "target_confirmed": _target_is_confirmed(health),
    }


def host_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    host_snapshot: dict[str, Any] | None = None,
) -> str:
    request_id = uuid.uuid4().hex
    snapshot = host_snapshot or host_health()
    if not snapshot or not snapshot.get("ok"):
        raise RuntimeError("ArcGIS Pro 窗口宿主未连接或协议不匹配")
    _require_confirmed_target(snapshot)
    payload = json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "session_id": snapshot.get("session_id"),
            "expected_project": snapshot.get("project"),
            "tool": tool_name,
            "arguments": arguments or {},
            "environment": forwarded_environment(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    endpoint = _endpoint_snapshot()
    base = str(snapshot.get("_endpoint_base") or endpoint.base)
    token = str(snapshot.get("_endpoint_token") or endpoint.token)
    req = urllib.request.Request(
        base + "/call",
        data=payload,
        method="POST",
        headers=_request_headers(json_body=True, token=token),
    )
    try:
        with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as resp:
            data = _read_json_response(resp)
    except urllib.error.HTTPError as exc:
        detail = _http_error_message(exc)
        raise RuntimeError(
            f"ArcGIS Pro 窗口宿主拒绝调用（HTTP {exc.code}，request_id={request_id}）：{detail}"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"ArcGIS Pro 窗口宿主返回无效 JSON（request_id={request_id}）"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(
            f"等待 ArcGIS Pro 窗口宿主超时（request_id={request_id}）；运行中任务的结果可能未知"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"无法连接 ArcGIS Pro 窗口宿主 {base}（request_id={request_id}）：{exc}"
        ) from exc
    if data.get("request_id") != request_id:
        raise RuntimeError(f"窗口宿主响应 request_id 不匹配（期望 {request_id}）")
    if data.get("session_id") != snapshot.get("session_id"):
        raise RuntimeError("窗口宿主会话在调用期间发生变化；结果已拒绝")
    if data.get("protocol_version") != PROTOCOL_VERSION:
        raise RuntimeError("窗口宿主响应协议版本不匹配；请重启 MCP 客户端和窗口宿主")
    if not data.get("ok"):
        remote_id = data.get("request_id") or request_id
        raise RuntimeError(f"窗口宿主调用失败（request_id={remote_id}）：{data.get('error') or '未知错误'}")
    result = data.get("result")
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def stop_host(host_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the attached in-Pro host to stop after its current call."""
    endpoint = _endpoint_snapshot()
    snapshot = host_snapshot or {}
    base = str(snapshot.get("_endpoint_base") or endpoint.base)
    token = str(snapshot.get("_endpoint_token") or endpoint.token)
    req = urllib.request.Request(
        base + "/stop",
        data=b"{}",
        method="POST",
        headers=_request_headers(json_body=True, token=token),
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return _read_json_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"ArcGIS Pro 窗口宿主拒绝断开（HTTP {exc.code}）：{_http_error_message(exc)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 ArcGIS Pro 窗口宿主 {base}：{exc}") from exc


def should_forward_to_host(arguments: dict[str, Any]) -> bool:
    """Only CURRENT project calls belong to the live window session."""
    value = arguments.get("aprx_path")
    return isinstance(value, str) and value.strip().strip('"').upper() == "CURRENT"


def install_stdio_proxy(mcp: Any) -> int:
    """把 FastMCP 工具转发到窗口宿主；宿主未启动时仍走本地文件模式。"""
    if in_pro_host():
        return 0
    mgr = getattr(mcp, "_tool_manager", None)
    if mgr is None or not hasattr(mgr, "list_tools") or not hasattr(mgr, "get_tool"):
        return 0
    wrapped = 0
    for spec in list(mgr.list_tools()):
        name = getattr(spec, "name", None)
        if not name:
            continue
        tool = mgr.get_tool(name)
        orig = getattr(tool, "fn", None)
        if orig is None:
            continue

        def make_proxy(tool_name: str, original: Any):
            def proxy(**kwargs: Any) -> Any:
                if should_forward_to_host(kwargs):
                    health = host_health()
                    if not health or not health.get("ok") or not health.get("ready"):
                        detail = ""
                        if health and health.get("context_error"):
                            detail = f"（{health['context_error']}）"
                        raise RuntimeError(
                            "aprx_path=CURRENT 需要已接入的 ArcGIS Pro 窗口；"
                            "请在 Pro 中启动 接入当前窗口.py，或改用绝对 .aprx 路径进入文件模式"
                            f"{detail}"
                        )
                    return host_call(tool_name, kwargs, health)
                return original(**kwargs)

            proxy.__name__ = getattr(original, "__name__", tool_name)
            proxy.__doc__ = getattr(original, "__doc__", None)
            return proxy

        proxy_fn = make_proxy(name, orig)
        try:
            tool.fn = proxy_fn
        except Exception:  # noqa: BLE001
            object.__setattr__(tool, "fn", proxy_fn)
        wrapped += 1
    return wrapped
