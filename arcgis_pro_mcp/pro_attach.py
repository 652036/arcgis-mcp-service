"""Stdio MCP 自动转发到 ArcGIS Pro 窗口宿主。"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arcgis_pro_mcp import __version__ as PACKAGE_VERSION
from arcgis_pro_mcp.private_state import (
    default_state_directory,
    read_private_json,
    remove_private_json_if,
)

ENV_IN_HOST = "ARCGIS_PRO_MCP_IN_PRO_HOST"
ENV_PORT = "ARCGIS_PRO_MCP_HOST_PORT"
ENV_BOUND_PORT = "ARCGIS_PRO_MCP_BOUND_HOST_PORT"
DEFAULT_PORT = 17865
STATE_NAME = "arcgis-pro-mcp-host.json"
SERVICE_NAME = "arcgis-pro-mcp-window-host"
PROTOCOL_VERSION = 4
TOKEN_HEADER = "X-ArcGIS-Pro-MCP-Token"
HEALTH_TIMEOUT = 0.6
HOST_JOB_TIMEOUT = 300
CALL_TIMEOUT = HOST_JOB_TIMEOUT + 10
FORWARDED_ENV_KEYS = (
    "ARCGIS_PRO_MCP_ALLOW_WRITE",
    "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE",
    "ARCGIS_PRO_MCP_ALLOW_CIM_WRITE",
    "ARCGIS_PRO_MCP_ALLOW_PUBLISH",
    "ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE",
    "ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE",
    "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE",
    "ARCGIS_PRO_MCP_EXPORT_ROOT",
    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT",
    "ARCGIS_PRO_MCP_INPUT_ROOTS",
    "ARCGIS_PRO_MCP_PROJECT_ROOTS",
    "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP",
    "ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD",
    "ARCGIS_PRO_MCP_PORTAL_ALLOWLIST",
    "ARCGIS_PRO_MCP_SERVER_ALLOWLIST",
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


def state_path(port: int | None = None) -> str:
    configured_port = port if port is not None else configured_host_port()
    if configured_port is not None and configured_port != DEFAULT_PORT:
        stem, suffix = os.path.splitext(STATE_NAME)
        name = f"{stem}-{configured_port}{suffix}"
    else:
        name = STATE_NAME
    return str(default_state_directory() / name)


def read_state() -> dict[str, Any]:
    bound = os.environ.get(ENV_BOUND_PORT, "") if in_pro_host() else ""
    return read_private_json(Path(state_path(int(bound) if bound.isdigit() else None)))


@dataclass(frozen=True)
class _HostEndpoint:
    base: str
    token: str
    state: dict[str, Any]
    paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _ConfirmedTarget:
    session_id: str
    project_id: str
    project: str


class _WindowResolutionError(RuntimeError):
    """Fail-closed window resolution error distinguishing disconnect vs ambiguity."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        live: list[tuple[_HostEndpoint, dict[str, Any]]] | None = None,
        selected: str = "",
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.live = list(live or [])
        self.selected = selected


_DIRECT_HTTP_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_TARGET_LOCK = threading.Lock()
_CONFIRMED_TARGET: _ConfirmedTarget | None = None
_CONFIRMED_TARGETS: dict[tuple[object, ...], _ConfirmedTarget] = {}
_SELECTED_WINDOW: str = ""
_WINDOW_SCOPE: ContextVar[tuple[int, str] | None] = ContextVar("arcgis_pro_mcp_window_scope", default=None)
_MAX_WINDOWS = 128
_MAX_SCAN_STATE_FILES = _MAX_WINDOWS * 16
_PROBE_MAX_WORKERS = 8
_STATE_FILE_RE = re.compile(
    r"arcgis-pro-mcp-(?:host(?:-[0-9]+)?|instance-[1-9][0-9]*-[A-Za-z0-9_-]{1,64})\.json\Z"
)


def _urlopen_loopback(req: urllib.request.Request, *, timeout: float) -> Any:
    """Issue a direct loopback HTTP request without consulting env or Windows proxies."""
    return _DIRECT_HTTP_OPENER.open(req, timeout=timeout)


def _pid_is_alive(pid: int) -> bool:
    """Return False only when the local OS explicitly confirms *pid* is not running."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            process_query_limited_information = 0x1000
            still_active = 259
            error_access_denied = 5
            error_invalid_parameter = 87

            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            kernel32.GetExitCodeProcess.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int

            handle = kernel32.OpenProcess(process_query_limited_information, 0, pid)
            if not handle:
                err = ctypes.get_last_error()
                if err == error_invalid_parameter:
                    return False
                if err == error_access_denied:
                    return True
                return True
            try:
                exit_code = ctypes.c_uint32(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _prune_dead_endpoint_files(endpoint: _HostEndpoint) -> int:
    """Remove stale state files only after probing failed and the recorded PID is dead."""
    if not endpoint.paths:
        return 0
    pid = endpoint.state.get("pid")
    session_id = endpoint.state.get("session_id")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or not isinstance(session_id, str)
        or not session_id
    ):
        return 0
    if _pid_is_alive(pid):
        return 0
    removed = 0
    for path in endpoint.paths:
        try:
            existed = path.exists()
            remove_private_json_if(path, "session_id", session_id)
            if existed and not path.exists():
                removed += 1
        except OSError:
            continue
    return removed


def _confirmation_key(snapshot: dict[str, Any]) -> tuple[object, ...]:
    return (snapshot.get("pid"), snapshot.get("session_id"), snapshot.get("_endpoint_base"))


def _window_id(state: dict[str, Any]) -> str:
    return f"{state.get('pid')}:{state.get('session_id')}"


@contextmanager
def window_scope(process_id: int = 0, window_id: str = ""):
    """Keep explicit routing local to a call, including concurrent tool calls."""
    if isinstance(process_id, bool) or not isinstance(process_id, int) or not 0 <= process_id <= 0xFFFFFFFF:
        raise RuntimeError("process_id 必须为有效的非负进程号")
    if window_id and not re.fullmatch(r"[1-9][0-9]*:[A-Za-z0-9_-]{1,64}", window_id):
        raise RuntimeError("window_id 无效；请使用 arcgis_pro_list_windows 返回的标识")
    if process_id and window_id and int(window_id.split(":", 1)[0]) != process_id:
        raise RuntimeError("process_id 与 window_id 指向不同窗口")
    token = _WINDOW_SCOPE.set((process_id, window_id)) if process_id or window_id else None
    try:
        yield
    finally:
        if token is not None:
            _WINDOW_SCOPE.reset(token)


def current_process_id(value: object) -> int:
    """Parse qualified CURRENT without accepting arbitrary file-mode substitutes."""
    if not isinstance(value, str):
        return 0
    text = value.strip().strip('"').upper()
    if not text.startswith("CURRENT:"):
        return 0
    suffix = text.removeprefix("CURRENT:")
    if not suffix.isascii() or not suffix.isdecimal() or not 0 < int(suffix) <= 0xFFFFFFFF:
        raise RuntimeError("窗口目标须为 CURRENT 或 CURRENT:进程号")
    return int(suffix)


def _trusted_state(state: dict[str, Any], port: int | None = None, *, identity: bool = False) -> bool:
    state_port = state.get("port")
    return bool(
        state.get("service") == SERVICE_NAME
        and state.get("protocol_version") == PROTOCOL_VERSION
        and state.get("package_version") == PACKAGE_VERSION
        and isinstance(state.get("token"), str) and state["token"]
        and type(state_port) is int and 1 <= state_port <= 65535
        and (port is None or state_port == port)
        and (not identity or (
            type(state.get("pid")) is int and state["pid"] > 0
            and isinstance(state.get("session_id"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", state["session_id"])
        ))
    )


def _scan_state_endpoints(
    *,
    directory: Path | None = None,
    port: int | None = None,
) -> tuple[list[_HostEndpoint], int, bool]:
    """Enumerate bounded, private capability files without scanning TCP ports."""
    root = directory if directory is not None else default_state_directory()
    found: dict[tuple[object, ...], _HostEndpoint] = {}
    matched_files = 0
    truncated = False
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not _STATE_FILE_RE.fullmatch(entry.name):
                    continue
                matched_files += 1
                if matched_files > _MAX_SCAN_STATE_FILES:
                    truncated = True
                    break
                path = root / entry.name
                state = read_private_json(path)
                if not _trusted_state(state, port, identity=True):
                    continue
                if entry.name.startswith("arcgis-pro-mcp-instance-"):
                    expected = f"arcgis-pro-mcp-instance-{state['pid']}-{state['session_id']}.json"
                    if entry.name != expected:
                        continue
                else:
                    expected = Path(state_path(state["port"])).name
                    if entry.name != expected:
                        continue
                key = (state["pid"], state["session_id"], state["port"])
                existing = found.get(key)
                if existing is None:
                    found[key] = _HostEndpoint(
                        f"http://127.0.0.1:{state['port']}",
                        state["token"],
                        state,
                        paths=(path,),
                    )
                else:
                    found[key] = _HostEndpoint(
                        existing.base,
                        existing.token,
                        existing.state,
                        paths=(*existing.paths, path),
                    )
    except FileNotFoundError:
        pass
    return list(found.values()), matched_files, truncated


def _probe_endpoints(
    endpoints: list[_HostEndpoint],
) -> tuple[list[tuple[_HostEndpoint, dict[str, Any]]], int]:
    """Probe discovered endpoints in parallel and prune unreachable dead-PID state files."""
    if not endpoints:
        return [], 0
    if len(endpoints) == 1:
        endpoint = endpoints[0]
        health = _probe_endpoint(endpoint)
        if health is not None:
            return [(endpoint, health)], 0
        return [], _prune_dead_endpoint_files(endpoint)
    max_workers = min(_PROBE_MAX_WORKERS, len(endpoints))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        probed = list(pool.map(_probe_endpoint, endpoints))
    live: list[tuple[_HostEndpoint, dict[str, Any]]] = []
    removed_files = 0
    for endpoint, health in zip(endpoints, probed, strict=True):
        if health is not None:
            live.append((endpoint, health))
        else:
            removed_files += _prune_dead_endpoint_files(endpoint)
    return live, removed_files


def prune_stale_state_files(directory: Path | None = None) -> int:
    """Best-effort cleanup of unreachable capability files whose PID is no longer alive."""
    endpoints, _, _ = _scan_state_endpoints(directory=directory, port=None)
    if not endpoints:
        return 0
    _, removed = _probe_endpoints(endpoints)
    return removed


def _discover_live_endpoints(
    *,
    directory: Path | None = None,
    respect_configured_port: bool = True,
) -> list[tuple[_HostEndpoint, dict[str, Any]]]:
    """Discover and probe live endpoints, pruning stale dead-PID records before enforcing caps."""
    configured_port = configured_host_port() if respect_configured_port else None
    endpoints, matched_files, truncated = _scan_state_endpoints(
        directory=directory,
        port=configured_port,
    )
    if matched_files > _MAX_WINDOWS * 2:
        all_endpoints = endpoints
        if configured_port is not None:
            all_endpoints, _, _ = _scan_state_endpoints(directory=directory, port=None)
        live_all, removed = _probe_endpoints(all_endpoints)
        remaining_files = matched_files - removed
        if truncated or remaining_files > _MAX_WINDOWS * 2 or len(live_all) > _MAX_WINDOWS:
            raise RuntimeError("窗口发现记录过多；请清理已停止的旧宿主记录")
        if configured_port is not None:
            return [
                (endpoint, health)
                for endpoint, health in live_all
                if endpoint.state.get("port") == configured_port
            ]
        return live_all
    live, _ = _probe_endpoints(endpoints)
    return live


def _discovered_endpoints(
    *,
    directory: Path | None = None,
    respect_configured_port: bool = True,
) -> list[_HostEndpoint]:
    """Only enumerate bounded, private capability files; never scan TCP ports."""
    configured_port = configured_host_port() if respect_configured_port else None
    endpoints, matched_files, truncated = _scan_state_endpoints(
        directory=directory,
        port=configured_port,
    )
    if matched_files > _MAX_WINDOWS * 2:
        all_endpoints = endpoints
        if configured_port is not None:
            all_endpoints, _, _ = _scan_state_endpoints(directory=directory, port=None)
        live_all, removed = _probe_endpoints(all_endpoints)
        remaining_files = matched_files - removed
        if truncated or remaining_files > _MAX_WINDOWS * 2 or len(live_all) > _MAX_WINDOWS:
            raise RuntimeError("窗口发现记录过多；请清理已停止的旧宿主记录")
        if configured_port is not None:
            return [
                endpoint
                for endpoint, _ in live_all
                if endpoint.state.get("port") == configured_port
            ]
        return [endpoint for endpoint, _ in live_all]
    return endpoints


def _project_identity(path: object) -> str:
    value = str(path or "").strip().strip('"')
    if not value or value.upper() == "CURRENT":
        return value.upper()
    return os.path.normcase(os.path.realpath(os.path.abspath(value))).lower()


def clear_confirmed_target() -> None:
    global _CONFIRMED_TARGET, _SELECTED_WINDOW
    with _TARGET_LOCK:
        _CONFIRMED_TARGET = None
        _CONFIRMED_TARGETS.clear()
        _SELECTED_WINDOW = ""


def clear_window_target(
    *,
    process_id: int = 0,
    window_id: str = "",
    session_id: str = "",
    endpoint_base: str = "",
) -> None:
    """Clear confirmation and default selection for one disconnected or detached window."""
    global _CONFIRMED_TARGET, _SELECTED_WINDOW
    target_pid = process_id if type(process_id) is int and process_id > 0 else 0
    target_session = session_id.strip()
    target_base = endpoint_base.strip()
    if window_id and ":" in window_id:
        wid_pid_str, wid_session = window_id.split(":", 1)
        if wid_pid_str.isdecimal():
            target_pid = target_pid or int(wid_pid_str)
        target_session = target_session or wid_session
    with _TARGET_LOCK:
        if target_pid or target_session or target_base:
            for key in list(_CONFIRMED_TARGETS):
                key_pid, key_session, key_base = key
                if target_pid and key_pid != target_pid:
                    continue
                if target_session and key_session != target_session:
                    continue
                if target_base and key_base != target_base:
                    continue
                _CONFIRMED_TARGETS.pop(key, None)
        if _SELECTED_WINDOW:
            sel_pid_str, _, sel_session = _SELECTED_WINDOW.partition(":")
            sel_pid = int(sel_pid_str) if sel_pid_str.isdecimal() else 0
            matches_selected = (
                (bool(window_id) and _SELECTED_WINDOW == window_id)
                or (
                    bool(target_session)
                    and sel_session == target_session
                    and (not target_pid or sel_pid == target_pid)
                )
                or (bool(target_pid) and not target_session and sel_pid == target_pid)
            )
            if matches_selected:
                _SELECTED_WINDOW = ""
        if not _CONFIRMED_TARGETS:
            _CONFIRMED_TARGET = None
        elif (
            _CONFIRMED_TARGET is not None
            and target_session
            and _CONFIRMED_TARGET.session_id == target_session
        ):
            _CONFIRMED_TARGET = next(reversed(_CONFIRMED_TARGETS.values()), None)


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
        if len(_CONFIRMED_TARGETS) >= _MAX_WINDOWS:
            _CONFIRMED_TARGETS.pop(next(iter(_CONFIRMED_TARGETS)))
        _CONFIRMED_TARGETS[_confirmation_key(snapshot)] = _CONFIRMED_TARGET


def _target_is_confirmed(snapshot: dict[str, Any]) -> bool:
    session_id = str(snapshot.get("session_id") or "")
    project_id = _project_identity(snapshot.get("project"))
    with _TARGET_LOCK:
        target = _CONFIRMED_TARGETS.get(_confirmation_key(snapshot))
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


def _resolve_endpoint_and_health(
    *,
    probe_fallback: bool = True,
) -> tuple[_HostEndpoint, dict[str, Any] | None]:
    configured_port = configured_host_port()
    scope = _WINDOW_SCOPE.get()
    with _TARGET_LOCK:
        selected = _SELECTED_WINDOW
    if scope or selected:
        pid, identifier = scope or (0, selected)
        matches = [
            endpoint
            for endpoint in _discovered_endpoints()
            if (not pid or endpoint.state.get("pid") == pid)
            and (not identifier or _window_id(endpoint.state) == identifier)
        ]
        live, _ = _probe_endpoints(matches)
        if len(live) != 1:
            reason = "ambiguous" if len(live) > 1 else "disconnected"
            raise _WindowResolutionError(
                "指定的 ArcGIS Pro 窗口未连接或不唯一；不会自动改连其他窗口。"
                "请调用 arcgis_pro_list_windows 后重新选择",
                reason=reason,
                live=live,
                selected="" if scope else selected,
            )
        return live[0]
    live = _discover_live_endpoints()
    if len(live) > 1:
        raise _WindowResolutionError(
            "发现多个 ArcGIS Pro 窗口；请先调用 arcgis_pro_list_windows，"
            "再用 arcgis_pro_select_window 或 CURRENT:进程号指定目标",
            reason="ambiguous",
            live=live,
        )
    if len(live) == 1:
        return live[0]
    expected_port = configured_port or DEFAULT_PORT
    state = read_state()
    trusted_state = _trusted_state(state, expected_port)
    port = state["port"] if trusted_state else expected_port
    state_token = state.get("token")
    token = state_token if trusted_state and isinstance(state_token, str) else ""
    endpoint = _HostEndpoint(base=f"http://127.0.0.1:{port}", token=token, state=state)
    health = _probe_endpoint(endpoint) if probe_fallback else None
    return endpoint, health


def _endpoint_snapshot() -> _HostEndpoint:
    return _resolve_endpoint_and_health(probe_fallback=False)[0]


def host_port() -> int:
    return int(_endpoint_snapshot().base.rsplit(":", 1)[1])


def host_base() -> str:
    return _endpoint_snapshot().base


def _state_token() -> str:
    return _endpoint_snapshot().token


def _snapshot_endpoint(snapshot: dict[str, Any] | None) -> _HostEndpoint:
    configured_port = configured_host_port()
    if snapshot and snapshot.get("_endpoint_base") and snapshot.get("_endpoint_token"):
        base = str(snapshot["_endpoint_base"])
        if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", base):
            raise RuntimeError("窗口宿主快照端点无效")
        port = int(base.rsplit(":", 1)[1])
        if not 1 <= port <= 65535:
            raise RuntimeError("窗口宿主快照端口无效")
        if configured_port is not None and port != configured_port:
            raise RuntimeError(f"窗口宿主快照端口与 {ENV_PORT}={configured_port} 不一致")
        return _HostEndpoint(base, str(snapshot["_endpoint_token"]), {})
    return _endpoint_snapshot()


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


def _probe_endpoint(endpoint: _HostEndpoint) -> dict[str, Any] | None:
    req = urllib.request.Request(
        endpoint.base + "/health",
        method="GET",
        headers=_request_headers(token=endpoint.token),
    )
    try:
        with _urlopen_loopback(req, timeout=HEALTH_TIMEOUT) as resp:
            data = _read_json_response(resp)
    except Exception:  # noqa: BLE001
        return None
    if (
        data.get("service") != SERVICE_NAME
        or data.get("protocol_version") != PROTOCOL_VERSION
        or data.get("package_version") != PACKAGE_VERSION
    ):
        return None
    if any(endpoint.state.get(key) is not None and data.get(key) != endpoint.state[key]
           for key in ("pid", "session_id")):
        return None
    data["_endpoint_base"] = endpoint.base
    data["_endpoint_token"] = endpoint.token
    return data


def host_health() -> dict[str, Any] | None:
    if in_pro_host():
        return {"ok": True, "in_host": True}
    return _resolve_endpoint_and_health(probe_fallback=True)[1]


def _public_window(health: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_id": _window_id(health), "process_id": health.get("pid"),
        "project": health.get("project"), "ready": health.get("ready", False),
        "busy": health.get("busy", False), "state": health.get("state"),
        "port": int(str(health["_endpoint_base"]).rsplit(":", 1)[1]),
        "active_view": health.get("active_view"),
        "target_confirmed": _target_is_confirmed(health),
    }


def _ambiguous_window_status(live: list[dict[str, Any]]) -> dict[str, Any]:
    windows = sorted(
        (_public_window(item) for item in live),
        key=lambda item: (item["process_id"], item["window_id"]),
    )
    return {
        "window_attached": False,
        "execution_mode": "file",
        "window_host": None,
        "window_ambiguous": True,
        "window_count": len(windows),
        "windows": windows,
        "note": (
            "发现多个 ArcGIS Pro 窗口；请先调用 arcgis_pro_list_windows，"
            "再用 arcgis_pro_select_window 或 CURRENT:进程号指定目标"
        ),
    }


def list_windows() -> dict[str, Any]:
    windows = [
        _public_window(health)
        for _, health in _discover_live_endpoints()
        if health.get("ok")
    ]
    windows.sort(key=lambda item: (item["process_id"], item["window_id"]))
    with _TARGET_LOCK:
        selected = _SELECTED_WINDOW
    return {"windows": windows, "window_count": len(windows), "selected_window_id": selected or None}


def select_window(process_id: int = 0, window_id: str = "", expected_project: str = "") -> dict[str, Any]:
    global _SELECTED_WINDOW
    if not (process_id or window_id or expected_project.strip()):
        raise RuntimeError("请用 process_id、window_id 或 expected_project 指定窗口")
    # Validate selectors before reading any capability state.
    with window_scope(process_id, window_id):
        pass
    candidates = [
        endpoint
        for endpoint in _discovered_endpoints()
        if (not process_id or endpoint.state.get("pid") == process_id)
        and (not window_id or _window_id(endpoint.state) == window_id)
    ]
    live, _ = _probe_endpoints(candidates)
    matches = [
        health
        for _, health in live
        if health.get("ok")
        and (
            not expected_project.strip()
            or _project_identity(health.get("project")) == _project_identity(expected_project)
        )
    ]
    if len(matches) != 1:
        raise RuntimeError(f"窗口目标未唯一匹配（{len(matches)} 个）；请先调用 arcgis_pro_list_windows")
    health = matches[0]
    if not health.get("ready"):
        raise RuntimeError("指定窗口尚未就绪；请先在该窗口打开并保存工程")
    confirm_host_target(health)
    with _TARGET_LOCK:
        _SELECTED_WINDOW = _window_id(health)
    result = _public_window(health)
    result.update({"selected": True, "current_project": health.get("project")})
    return result


def host_available() -> bool:
    if in_pro_host():
        return False
    try:
        health = host_health()
    except _WindowResolutionError:
        return False
    return bool(health and health.get("ok") and health.get("ready"))


def host_status(*, confirm_target: bool = False, process_id: int = 0, window_id: str = "") -> dict[str, Any]:
    global _SELECTED_WINDOW
    if process_id or window_id:
        with window_scope(process_id, window_id):
            return host_status(confirm_target=confirm_target)
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
    scope = _WINDOW_SCOPE.get()
    try:
        health = host_health()
    except _WindowResolutionError as exc:
        if scope:
            if confirm_target:
                clear_window_target(process_id=scope[0], window_id=scope[1])
            if exc.reason == "ambiguous" and exc.live:
                return _ambiguous_window_status([item_health for _, item_health in exc.live])
            return {"window_attached": False, "execution_mode": "file", "window_host": None}
        if exc.selected:
            live = [
                item_health
                for _, item_health in _discover_live_endpoints()
                if item_health.get("ok")
            ]
            if not live:
                clear_window_target(window_id=exc.selected)
                return {"window_attached": False, "execution_mode": "file", "window_host": None}
            if len(live) > 1:
                clear_window_target(window_id=exc.selected)
                return _ambiguous_window_status(live)
            candidate = live[0]
            if confirm_target or not _target_is_confirmed(candidate):
                clear_window_target(window_id=exc.selected)
                health = candidate
            else:
                return {
                    "window_attached": False,
                    "execution_mode": "file",
                    "window_host": None,
                    "selected_window_id": exc.selected,
                    "selected_window_disconnected": True,
                    "window_count": 1,
                    "windows": [_public_window(candidate)],
                }
        elif exc.reason == "ambiguous":
            return _ambiguous_window_status([item_health for _, item_health in exc.live])
        else:
            return {"window_attached": False, "execution_mode": "file", "window_host": None}
    if not health or not health.get("ok"):
        if confirm_target:
            if scope:
                clear_window_target(process_id=scope[0], window_id=scope[1])
            else:
                clear_confirmed_target()
        return {"window_attached": False, "execution_mode": "file", "window_host": None}
    if confirm_target:
        if health.get("ready"):
            confirm_host_target(health)
            if scope is None and type(health.get("pid")) is int:
                with _TARGET_LOCK:
                    _SELECTED_WINDOW = _window_id(health)
        else:
            with _TARGET_LOCK:
                _CONFIRMED_TARGETS.pop(_confirmation_key(health), None)
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
        "window_id": _window_id(health),
    }


def host_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    host_snapshot: dict[str, Any] | None = None,
) -> str:
    pid = current_process_id((arguments or {}).get("aprx_path"))
    if pid:
        if host_snapshot is not None and host_snapshot.get("pid") != pid:
            raise RuntimeError("CURRENT 进程号与请求快照不一致")
        with window_scope(process_id=pid):
            return host_call(tool_name, {**(arguments or {}), "aprx_path": "CURRENT"}, host_snapshot)
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
    endpoint = _snapshot_endpoint(snapshot)
    base = str(snapshot.get("_endpoint_base") or endpoint.base)
    token = str(snapshot.get("_endpoint_token") or endpoint.token)
    req = urllib.request.Request(
        base + "/call",
        data=payload,
        method="POST",
        headers=_request_headers(json_body=True, token=token),
    )
    try:
        with _urlopen_loopback(req, timeout=CALL_TIMEOUT) as resp:
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


def _validated_job_response(
    data: dict[str, Any],
    snapshot: dict[str, Any],
    request_id: str = "",
) -> dict[str, Any]:
    if data.get("protocol_version") != PROTOCOL_VERSION:
        raise RuntimeError("窗口宿主任务协议版本不匹配；请重启 MCP 客户端和窗口宿主")
    if data.get("session_id") != snapshot.get("session_id"):
        raise RuntimeError("窗口宿主会话在任务请求期间发生变化")
    if request_id and data.get("request_id") != request_id:
        raise RuntimeError("窗口宿主任务 request_id 不匹配")
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or "窗口宿主任务请求失败"))
    result = data.get("result")
    if isinstance(result, str):
        try:
            data["result"] = json.loads(result)
        except json.JSONDecodeError:
            pass
    return data


def submit_host_job(
    tool_name: str,
    arguments: dict[str, Any],
    idempotency_key: str = "",
    host_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pid = current_process_id(arguments.get("aprx_path"))
    if pid:
        if host_snapshot is not None and host_snapshot.get("pid") != pid:
            raise RuntimeError("CURRENT 进程号与任务快照不一致")
        with window_scope(process_id=pid):
            return submit_host_job(tool_name, {**arguments, "aprx_path": "CURRENT"}, idempotency_key, host_snapshot)
    snapshot = host_snapshot or host_health()
    if not snapshot or not snapshot.get("ok"):
        raise RuntimeError("ArcGIS Pro 窗口宿主未连接或协议不匹配")
    _require_confirmed_target(snapshot)
    if not should_forward_to_host(arguments):
        raise RuntimeError("异步窗口任务只接受 arguments.aprx_path=CURRENT")
    request_id = uuid.uuid4().hex
    payload = json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "session_id": snapshot.get("session_id"),
            "expected_project": snapshot.get("project"),
            "tool": tool_name,
            "arguments": arguments,
            "environment": forwarded_environment(),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    endpoint = _snapshot_endpoint(snapshot)
    base = str(snapshot.get("_endpoint_base") or endpoint.base)
    token = str(snapshot.get("_endpoint_token") or endpoint.token)
    req = urllib.request.Request(
        base + "/jobs/submit",
        data=payload,
        method="POST",
        headers=_request_headers(json_body=True, token=token),
    )
    try:
        with _urlopen_loopback(req, timeout=5) as response:
            data = _read_json_response(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"窗口任务提交失败：{_http_error_message(exc)}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError(f"无法提交窗口任务：{exc}") from exc
    returned_request_id = str(data.get("request_id") or "")
    if returned_request_id != request_id and data.get("idempotent_replay") is not True:
        raise RuntimeError("窗口宿主任务 request_id 不匹配")
    if returned_request_id != request_id and not idempotency_key:
        raise RuntimeError("窗口宿主返回了未经请求的幂等重放")
    return _validated_job_response(data, snapshot)


def host_job_status(
    request_id: str,
    host_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rid = request_id.strip()
    if not rid or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in rid):
        raise RuntimeError("request_id 格式无效")
    snapshot = host_snapshot or host_health()
    if not snapshot or not snapshot.get("ok"):
        raise RuntimeError("ArcGIS Pro 窗口宿主未连接")
    _require_confirmed_target(snapshot)
    endpoint = _snapshot_endpoint(snapshot)
    base = str(snapshot.get("_endpoint_base") or endpoint.base)
    token = str(snapshot.get("_endpoint_token") or endpoint.token)
    req = urllib.request.Request(
        base + "/jobs/" + urllib.parse.quote(rid, safe=""),
        method="GET",
        headers=_request_headers(json_body=False, token=token),
    )
    try:
        with _urlopen_loopback(req, timeout=5) as response:
            data = _read_json_response(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"读取窗口任务失败：{_http_error_message(exc)}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError(f"无法读取窗口任务：{exc}") from exc
    return _validated_job_response(data, snapshot, rid)


def cancel_host_job(
    request_id: str,
    host_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rid = request_id.strip()
    snapshot = host_snapshot or host_health()
    if not snapshot or not snapshot.get("ok"):
        raise RuntimeError("ArcGIS Pro 窗口宿主未连接")
    _require_confirmed_target(snapshot)
    payload = json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": snapshot.get("session_id"),
            "request_id": rid,
        }
    ).encode("utf-8")
    endpoint = _snapshot_endpoint(snapshot)
    base = str(snapshot.get("_endpoint_base") or endpoint.base)
    token = str(snapshot.get("_endpoint_token") or endpoint.token)
    req = urllib.request.Request(
        base + "/jobs/cancel",
        data=payload,
        method="POST",
        headers=_request_headers(json_body=True, token=token),
    )
    try:
        with _urlopen_loopback(req, timeout=5) as response:
            data = _read_json_response(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"取消窗口任务失败：{_http_error_message(exc)}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError(f"无法取消窗口任务：{exc}") from exc
    return _validated_job_response(data, snapshot, rid)


def wait_for_window_change(
    after_revision: int,
    timeout_ms: int = 30_000,
    host_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = host_snapshot or host_health()
    if not snapshot or not snapshot.get("ok"):
        raise RuntimeError("ArcGIS Pro 窗口宿主未连接")
    _require_confirmed_target(snapshot)
    wait_ms = max(0, min(int(timeout_ms), 30_000))
    query = urllib.parse.urlencode(
        {"after_revision": max(0, int(after_revision)), "timeout_ms": wait_ms}
    )
    endpoint = _snapshot_endpoint(snapshot)
    base = str(snapshot.get("_endpoint_base") or endpoint.base)
    token = str(snapshot.get("_endpoint_token") or endpoint.token)
    req = urllib.request.Request(
        base + "/events?" + query,
        method="GET",
        headers=_request_headers(json_body=False, token=token),
    )
    try:
        with _urlopen_loopback(req, timeout=wait_ms / 1000 + 5) as response:
            data = _read_json_response(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"等待窗口变化失败：{_http_error_message(exc)}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError(f"无法等待窗口变化：{exc}") from exc
    return _validated_job_response(data, snapshot)


def stop_host(host_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the attached in-Pro host to stop after its current call."""
    snapshot = host_snapshot or {}
    endpoint = _snapshot_endpoint(snapshot)
    base = str(snapshot.get("_endpoint_base") or endpoint.base)
    token = str(snapshot.get("_endpoint_token") or endpoint.token)
    req = urllib.request.Request(
        base + "/stop",
        data=b"{}",
        method="POST",
        headers=_request_headers(json_body=True, token=token),
    )
    try:
        with _urlopen_loopback(req, timeout=5) as resp:
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
    return isinstance(value, str) and (
        value.strip().strip('"').upper() == "CURRENT" or bool(current_process_id(value))
    )


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
                    pid = current_process_id(kwargs.get("aprx_path"))
                    with window_scope(process_id=pid):
                        health = host_health()
                        if not health or not health.get("ok") or not health.get("ready"):
                            detail = ""
                            if health and health.get("context_error"):
                                detail = f"（{health['context_error']}）"
                            raise RuntimeError(
                                "aprx_path=CURRENT 需要已接入的 ArcGIS Pro 窗口；"
                                "请在 Pro 中启动 接入当前窗口.py，并通过 arcgis_pro_list_windows 选择目标"
                                f"{detail}"
                            )
                        forwarded = {**kwargs, "aprx_path": "CURRENT"}
                        remote_result = host_call(tool_name, forwarded, health)
                    if getattr(original, "__arcgis_structured_output__", False):
                        if isinstance(remote_result, str):
                            try:
                                decoded = json.loads(remote_result)
                            except json.JSONDecodeError:
                                return {"ok": True, "result": remote_result}
                            return decoded if isinstance(decoded, dict) else {"ok": True, "result": decoded}
                        return remote_result
                    return remote_result
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
