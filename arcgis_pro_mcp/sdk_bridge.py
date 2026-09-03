"""Client for the optional ArcGIS Pro SDK Add-In loopback bridge.

Bearer and lease secrets never cross the public MCP boundary.  A caller receives
an opaque process-local session reference after acquiring a project lease and
uses that reference for event, job, and native edit commands.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from arcgis_pro_mcp.paths import validate_project_path

_DISCOVERY_NAME = re.compile(r"^bridge-(\d+)\.json$")
_SESSION_PREFIX = "arcgis-sdk-session:"
_MAX_DISCOVERY_BYTES = 16_384
_MAX_RESPONSE_BYTES = 2_097_152
_DEFAULT_TIMEOUT_SECONDS = 10.0
_SESSIONS_LOCK = threading.RLock()


@dataclass
class _Discovery:
    path: str
    protocol_version: str
    process_id: int
    port: int
    token: str
    server_session_id: str
    created_at_utc: str


@dataclass
class _LeaseSession:
    discovery: _Discovery
    lease_id: str
    generation: int
    project_uri: str
    expires_at_utc: str


_SESSIONS: dict[str, _LeaseSession] = {}


def _discovery_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA 未配置，无法发现 ArcGIS Pro SDK bridge")
    return Path(local_app_data) / "ArcGISProMcp" / "sdk-bridge"


def _text(value: Any, label: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"SDK bridge discovery 的 {label} 无效")
    result = value.strip()
    if not result or len(result) > max_length or "\r" in result or "\n" in result:
        raise RuntimeError(f"SDK bridge discovery 的 {label} 无效")
    return result


def _load_discovery(path: Path) -> _Discovery:
    try:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("discovery 不是普通文件")
        size = path.stat().st_size
        if size < 2 or size > _MAX_DISCOVERY_BYTES:
            raise RuntimeError("discovery 文件大小异常")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法读取 SDK bridge discovery：{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("SDK bridge discovery 必须为 JSON object")
    try:
        process_id = int(payload["processId"])
        port = int(payload["port"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("SDK bridge discovery 缺少有效 processId/port") from exc
    match = _DISCOVERY_NAME.fullmatch(path.name)
    if process_id <= 0 or match is None or int(match.group(1)) != process_id:
        raise RuntimeError("SDK bridge discovery 的进程标识不一致")
    if port < 1 or port > 65_535:
        raise RuntimeError("SDK bridge discovery 的端口无效")
    return _Discovery(
        path=str(path),
        protocol_version=_text(payload.get("protocolVersion"), "protocolVersion", max_length=16),
        process_id=process_id,
        port=port,
        token=_text(payload.get("token"), "token", max_length=512),
        server_session_id=_text(
            payload.get("serverSessionId"), "serverSessionId", max_length=256
        ),
        created_at_utc=_text(payload.get("createdAtUtc"), "createdAtUtc", max_length=80),
    )


def _discoveries() -> list[_Discovery]:
    directory = _discovery_directory()
    if not directory.is_dir():
        return []
    values: list[_Discovery] = []
    for path in sorted(directory.glob("bridge-*.json")):
        try:
            values.append(_load_discovery(path))
        except RuntimeError:
            continue
    return values


def _choose_discovery(process_id: int = 0) -> _Discovery:
    candidates = _discoveries()
    if process_id:
        candidates = [item for item in candidates if item.process_id == int(process_id)]
    if not candidates:
        suffix = f" process_id={process_id}" if process_id else ""
        raise RuntimeError(f"未发现可用的 ArcGIS Pro SDK bridge{suffix}")
    if len(candidates) != 1:
        ids = [item.process_id for item in candidates]
        raise RuntimeError(f"发现多个 SDK bridge，请显式指定 process_id：{ids}")
    return candidates[0]


def _decode_response(response: Any) -> dict[str, Any]:
    body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("SDK bridge 响应超过大小限制")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("SDK bridge 返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("SDK bridge 响应必须为 JSON object")
    return payload


def _request(
    discovery: _Discovery,
    method: str,
    target: str,
    *,
    body: dict[str, Any] | None = None,
    lease: _LeaseSession | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not target.startswith("/v1/") or "\r" in target or "\n" in target:
        raise RuntimeError("SDK bridge 请求路径无效")
    data = None
    headers = {
        "Authorization": f"Bearer {discovery.token}",
        "Accept": "application/json",
        "Connection": "close",
    }
    if lease is not None:
        if lease.discovery.server_session_id != discovery.server_session_id:
            raise RuntimeError("SDK session 与 bridge session 不一致")
        headers["X-ArcGIS-Pro-Session"] = discovery.server_session_id
        headers["X-ArcGIS-Pro-Lease"] = lease.lease_id
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > 1_048_576:
            raise RuntimeError("SDK bridge 请求体超过 1 MiB")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{discovery.port}{target}",
        data=data,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=max(0.2, min(float(timeout_seconds), 35.0))) as response:
            payload = _decode_response(response)
    except urllib.error.HTTPError as exc:
        try:
            payload = _decode_response(exc)
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code") or "http_error")[:80]
                message = str(error.get("message") or "SDK bridge request failed")[:500]
                raise RuntimeError(f"SDK bridge {code}: {message}") from exc
        except RuntimeError:
            raise
        raise RuntimeError(f"SDK bridge HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"SDK bridge 不可达：{type(exc).__name__}") from exc
    if payload.get("ok") is not True:
        raise RuntimeError("SDK bridge 返回未成功结果")
    return payload


def _public_status(discovery: _Discovery) -> dict[str, Any]:
    payload = _request(discovery, "GET", "/v1/status")
    context = payload.get("context")
    typed_contracts = payload.get("typedGpContracts")
    if not isinstance(typed_contracts, list):
        typed_contracts = []
    return {
        "process_id": discovery.process_id,
        "protocol_version": payload.get("protocolVersion", discovery.protocol_version),
        "created_at_utc": discovery.created_at_utc,
        "reachable": True,
        "write_enabled": bool(payload.get("writeEnabled", False)),
        "edit_commands_enabled": bool(payload.get("editCommandsEnabled", False)),
        "discard_edits_enabled": bool(payload.get("discardEditsEnabled", False)),
        "feature_edits_enabled": bool(payload.get("featureEditsEnabled", False)),
        "destructive_edits_enabled": bool(payload.get("destructiveEditsEnabled", False)),
        "gp_allowlist_count": payload.get("gpAllowlistCount"),
        "gp_output_root_configured": bool(payload.get("gpOutputRootConfigured", False)),
        "input_root_count": payload.get("inputRootCount"),
        "project_root_count": payload.get("projectRootCount"),
        "typed_gp_contracts": [str(value) for value in typed_contracts[:128]],
        "capabilities": (
            dict(payload.get("capabilities"))
            if isinstance(payload.get("capabilities"), dict)
            else {}
        ),
        "context": context if isinstance(context, dict) else {},
    }


def bridge_status(process_id: int = 0) -> dict[str, Any]:
    """Return sanitized discovery/status records without bearer or lease tokens."""
    discoveries = _discoveries()
    if process_id:
        discoveries = [item for item in discoveries if item.process_id == int(process_id)]
    bridges: list[dict[str, Any]] = []
    for discovery in discoveries:
        try:
            bridges.append(_public_status(discovery))
        except RuntimeError as exc:
            bridges.append(
                {
                    "process_id": discovery.process_id,
                    "protocol_version": discovery.protocol_version,
                    "created_at_utc": discovery.created_at_utc,
                    "reachable": False,
                    "error": str(exc)[:500],
                }
            )
    return {
        "ok": True,
        "bridge_count": len(bridges),
        "bridges": bridges,
        "active_sdk_session_count": len(_SESSIONS),
    }


def acquire_lease(
    expected_project_uri: str,
    *,
    process_id: int = 0,
    ttl_seconds: int = 45,
) -> dict[str, Any]:
    project_uri = validate_project_path(expected_project_uri, "expected_project_uri")
    if project_uri.upper() == "CURRENT":
        raise RuntimeError("SDK lease 需要已保存工程的绝对 .aprx 路径，不能使用 CURRENT")
    ttl = int(ttl_seconds)
    if ttl < 10 or ttl > 120:
        raise RuntimeError("ttl_seconds 必须在 10–120 之间")
    discovery = _choose_discovery(process_id)
    payload = _request(
        discovery,
        "POST",
        "/v1/lease/acquire",
        body={
            "serverSessionId": discovery.server_session_id,
            "expectedProjectUri": project_uri,
            "ttlSeconds": ttl,
        },
    )
    lease = payload.get("lease")
    if not isinstance(lease, dict):
        raise RuntimeError("SDK bridge 未返回 lease")
    lease_id = _text(lease.get("leaseId"), "leaseId", max_length=256)
    server_session_id = _text(
        lease.get("serverSessionId"), "serverSessionId", max_length=256
    )
    if server_session_id != discovery.server_session_id:
        raise RuntimeError("SDK bridge lease session 校验失败")
    returned_project = _text(lease.get("projectUri"), "projectUri", max_length=4096)
    if os.path.normcase(os.path.normpath(returned_project)) != os.path.normcase(
        os.path.normpath(project_uri)
    ):
        raise RuntimeError("SDK bridge lease 绑定了不同工程")
    expires_at = _text(lease.get("expiresAtUtc"), "expiresAtUtc", max_length=80)
    try:
        generation = int(lease["generation"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("SDK bridge lease 缺少有效 generation") from exc
    if generation < 1:
        raise RuntimeError("SDK bridge lease generation 无效")
    reference = _SESSION_PREFIX + secrets.token_urlsafe(24)
    with _SESSIONS_LOCK:
        _SESSIONS[reference] = _LeaseSession(
            discovery=discovery,
            lease_id=lease_id,
            generation=generation,
            project_uri=returned_project,
            expires_at_utc=expires_at,
        )
    return {
        "ok": True,
        "sdk_session_ref": reference,
        "process_id": discovery.process_id,
        "project_uri": returned_project,
        "lease_generation": generation,
        "expires_at_utc": expires_at,
    }


def _session(reference: str) -> _LeaseSession:
    if not isinstance(reference, str) or not reference.startswith(_SESSION_PREFIX):
        raise RuntimeError("sdk_session_ref 无效")
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(reference)
    if session is None:
        raise RuntimeError("sdk_session_ref 不存在、已释放或属于另一 MCP 进程")
    return session


def renew_lease(reference: str) -> dict[str, Any]:
    session = _session(reference)
    payload = _request(
        session.discovery,
        "POST",
        "/v1/lease/renew",
        body={},
        lease=session,
    )
    lease = payload.get("lease")
    if not isinstance(lease, dict):
        raise RuntimeError("SDK bridge 未返回续期 lease")
    session.expires_at_utc = _text(
        lease.get("expiresAtUtc"), "expiresAtUtc", max_length=80
    )
    try:
        renewed_generation = int(lease["generation"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("SDK bridge 续期响应缺少 generation") from exc
    if renewed_generation != session.generation:
        raise RuntimeError("SDK bridge lease generation 在续期时意外改变")
    return {
        "ok": True,
        "sdk_session_ref": reference,
        "process_id": session.discovery.process_id,
        "project_uri": session.project_uri,
        "lease_generation": session.generation,
        "expires_at_utc": session.expires_at_utc,
    }


def release_lease(reference: str) -> dict[str, Any]:
    session = _session(reference)
    _request(
        session.discovery,
        "POST",
        "/v1/lease/release",
        body={},
        lease=session,
    )
    with _SESSIONS_LOCK:
        _SESSIONS.pop(reference, None)
    return {"ok": True, "sdk_session_ref": reference, "released": True}


def wait_events(
    reference: str,
    *,
    after: int = 0,
    limit: int = 128,
    wait_ms: int = 30_000,
) -> dict[str, Any]:
    session = _session(reference)
    if int(after) < 0:
        raise RuntimeError("after 必须 >= 0")
    count = int(limit)
    if count < 1 or count > 256:
        raise RuntimeError("limit 必须在 1–256 之间")
    wait = int(wait_ms)
    if wait < 0 or wait > 30_000:
        raise RuntimeError("wait_ms 必须在 0–30000 之间")
    query = urllib.parse.urlencode({"after": int(after), "limit": count, "waitMs": wait})
    return _request(
        session.discovery,
        "GET",
        f"/v1/events?{query}",
        lease=session,
        timeout_seconds=max(_DEFAULT_TIMEOUT_SECONDS, wait / 1000.0 + 3.0),
    )


def start_gp_job(
    reference: str,
    tool_name: str,
    parameters: list[str],
    environments: dict[str, str] | None = None,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    name = (tool_name or "").strip()
    if not name or len(name) > 256 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*", name):
        raise RuntimeError("tool_name 无效")
    if not confirm:
        raise RuntimeError("启动 SDK GP job 必须设置 confirm=true")
    if not isinstance(parameters, list) or len(parameters) > 128 or not all(
        isinstance(value, str) and len(value) <= 32_768 for value in parameters
    ):
        raise RuntimeError("parameters 必须为最多 128 项的字符串列表")
    env = environments or {}
    if not isinstance(env, dict) or len(env) > 64 or not all(
        isinstance(key, str)
        and isinstance(value, str)
        and len(key) <= 128
        and len(value) <= 32_768
        for key, value in env.items()
    ):
        raise RuntimeError("environments 必须为受限字符串映射")
    return _request(
        session.discovery,
        "POST",
        "/v1/jobs",
        body={
            "toolName": name,
            "parameters": parameters,
            "environments": env,
            "confirm": True,
        },
        lease=session,
    )


def gp_job_status(reference: str, job_id: str) -> dict[str, Any]:
    session = _session(reference)
    job = _job_id(job_id)
    return _request(session.discovery, "GET", f"/v1/jobs/{job}", lease=session)


def cancel_gp_job(
    reference: str,
    job_id: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    job = _job_id(job_id)
    if not confirm:
        raise RuntimeError("取消 SDK GP job 必须设置 confirm=true")
    return _request(
        session.discovery,
        "POST",
        f"/v1/jobs/{job}/cancel",
        body={"confirm": True},
        lease=session,
    )


def _job_id(value: str) -> str:
    job = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", job):
        raise RuntimeError("job_id 无效")
    return job


def edit_status(reference: str) -> dict[str, Any]:
    session = _session(reference)
    return _request(session.discovery, "GET", "/v1/edit/status", lease=session)


def edit_command(
    reference: str,
    command: str,
    *,
    expected_edit_generation: int,
    expected_map_uri: str = "",
    confirm: bool = False,
    confirm_discard_all: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    action = (command or "").strip().lower()
    if action not in {"undo", "redo", "save", "discard"}:
        raise RuntimeError("command 须为 undo、redo、save 或 discard")
    if not confirm:
        raise RuntimeError(f"SDK edit {action} 必须设置 confirm=true")
    generation = int(expected_edit_generation)
    if generation < 0:
        raise RuntimeError("expected_edit_generation 必须 >= 0")
    map_uri = (expected_map_uri or "").strip()
    if action in {"undo", "redo"} and not map_uri:
        raise RuntimeError("undo/redo 必须提供 edit/status 返回的 expected_map_uri")
    if len(map_uri) > 4096 or "\r" in map_uri or "\n" in map_uri:
        raise RuntimeError("expected_map_uri 无效")
    if action == "discard" and not confirm_discard_all:
        raise RuntimeError("discard 必须设置 confirm_discard_all=true")
    body: dict[str, Any] = {
        "confirm": True,
        "expectedEditGeneration": generation,
    }
    if action in {"undo", "redo"}:
        body["expectedMapUri"] = map_uri
    if action == "discard":
        body["confirmDiscardAll"] = True
    return _request(
        session.discovery,
        "POST",
        f"/v1/edit/{action}",
        body=body,
        lease=session,
    )


def _generation(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{label} 必须为非负整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 必须为非负整数") from exc
    if result < 0:
        raise RuntimeError(f"{label} 必须为非负整数")
    return result


def _command_text(value: Any, label: str, *, maximum: int = 4096) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or "\r" in text or "\n" in text or "\x00" in text:
        raise RuntimeError(f"{label} 无效")
    return text


def _duration(value: Any, label: str, *, minimum: int = 0) -> int:
    result = _generation(value, label)
    if result < minimum or result > 30_000:
        raise RuntimeError(f"{label} 必须在 {minimum}–30000 之间")
    return result


def _finite_optional(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise RuntimeError(f"{label} 必须为有限数值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 必须为有限数值") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{label} 必须为有限数值")
    return result


def _view_body(
    expected_map_uri: str,
    expected_context_generation: int,
    *,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise RuntimeError("SDK view 命令必须设置 confirm=true")
    return {
        "confirm": True,
        "expectedMapUri": _command_text(expected_map_uri, "expected_map_uri"),
        "expectedContextGeneration": _generation(
            expected_context_generation, "expected_context_generation"
        ),
    }


def context_snapshot(reference: str) -> dict[str, Any]:
    """Read a full native view/selection/edit snapshot under an active lease."""
    session = _session(reference)
    return _request(session.discovery, "GET", "/v1/context", lease=session)


def set_camera(
    reference: str,
    expected_map_uri: str,
    expected_context_generation: int,
    expected_spatial_reference_wkid: int,
    *,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    scale: float | None = None,
    heading: float | None = None,
    pitch: float | None = None,
    roll: float | None = None,
    duration_milliseconds: int = 0,
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    body = _view_body(
        expected_map_uri,
        expected_context_generation,
        confirm=confirm,
    )
    wkid = _generation(expected_spatial_reference_wkid, "expected_spatial_reference_wkid")
    if wkid < 1:
        raise RuntimeError("expected_spatial_reference_wkid 必须大于 0")
    values = {
        "x": _finite_optional(x, "x"),
        "y": _finite_optional(y, "y"),
        "z": _finite_optional(z, "z"),
        "scale": _finite_optional(scale, "scale"),
        "heading": _finite_optional(heading, "heading"),
        "pitch": _finite_optional(pitch, "pitch"),
        "roll": _finite_optional(roll, "roll"),
    }
    if not any(value is not None for value in values.values()):
        raise RuntimeError("相机命令至少提供 x/y/z/scale/heading/pitch/roll 之一")
    if values["scale"] is not None and values["scale"] <= 0:
        raise RuntimeError("scale 必须大于 0")
    if values["pitch"] is not None and not -90 <= values["pitch"] <= 90:
        raise RuntimeError("pitch 必须在 -90–90 之间")
    body.update(
        {
            "expectedSpatialReferenceWkid": wkid,
            "durationMilliseconds": _duration(
                duration_milliseconds, "duration_milliseconds"
            ),
        }
    )
    body.update({key: value for key, value in values.items() if value is not None})
    return _request(
        session.discovery,
        "POST",
        "/v1/view/camera",
        body=body,
        lease=session,
    )


def zoom_layer(
    reference: str,
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    *,
    selected_only: bool = False,
    duration_milliseconds: int = 0,
    maintain_view_direction: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    body = _view_body(
        expected_map_uri,
        expected_context_generation,
        confirm=confirm,
    )
    body.update(
        {
            "layerUri": _command_text(layer_uri, "layer_uri"),
            "selectedOnly": bool(selected_only),
            "durationMilliseconds": _duration(
                duration_milliseconds, "duration_milliseconds"
            ),
            "maintainViewDirection": bool(maintain_view_direction),
        }
    )
    return _request(
        session.discovery,
        "POST",
        "/v1/view/zoom-layer",
        body=body,
        lease=session,
    )


def refresh_view(
    reference: str,
    expected_map_uri: str,
    expected_context_generation: int,
    *,
    clear_cache: bool = False,
    wait_milliseconds: int = 10_000,
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    body = _view_body(
        expected_map_uri,
        expected_context_generation,
        confirm=confirm,
    )
    wait = _duration(wait_milliseconds, "wait_milliseconds", minimum=1)
    body.update({"clearCache": bool(clear_cache), "waitMilliseconds": wait})
    return _request(
        session.discovery,
        "POST",
        "/v1/view/refresh",
        body=body,
        lease=session,
        timeout_seconds=max(_DEFAULT_TIMEOUT_SECONDS, wait / 1000.0 + 3.0),
    )


def _offset_datetime(value: str, label: str) -> str:
    text = _command_text(value, label, maximum=80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"{label} 必须为 ISO-8601 日期时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"{label} 必须包含 Z 或显式 UTC offset")
    return text


def set_active_time(
    reference: str,
    expected_map_uri: str,
    expected_context_generation: int,
    *,
    start_time: str = "",
    end_time: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    body = _view_body(
        expected_map_uri,
        expected_context_generation,
        confirm=confirm,
    )
    if bool(start_time) != bool(end_time):
        raise RuntimeError("start_time/end_time 必须同时提供，或同时留空以禁用时间范围")
    start = _offset_datetime(start_time, "start_time") if start_time else None
    end = _offset_datetime(end_time, "end_time") if end_time else None
    if start is not None and end is not None:
        start_value = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_value = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if start_value > end_value:
            raise RuntimeError("start_time 不能晚于 end_time")
    body.update({"start": start, "end": end})
    return _request(
        session.discovery,
        "POST",
        "/v1/view/time",
        body=body,
        lease=session,
    )


def open_table(
    reference: str,
    expected_map_uri: str,
    expected_context_generation: int,
    table_uri: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    body = _view_body(
        expected_map_uri,
        expected_context_generation,
        confirm=confirm,
    )
    body["tableUri"] = _command_text(table_uri, "table_uri")
    return _request(
        session.discovery,
        "POST",
        "/v1/view/open-table",
        body=body,
        lease=session,
    )


def _attributes(values: dict[str, Any] | None, *, required: bool) -> dict[str, Any] | None:
    if values is None:
        if required:
            raise RuntimeError("attributes 不能为空")
        return None
    if not isinstance(values, dict) or len(values) > 64:
        raise RuntimeError("attributes 必须为最多 64 个字段的对象")
    result: dict[str, Any] = {}
    for raw_key, value in values.items():
        key = _command_text(raw_key, "attribute field", maximum=256)
        if isinstance(value, bool) or isinstance(value, (dict, list, tuple, bytes, bytearray)):
            raise RuntimeError(f"attributes[{key!r}] 只允许 null、字符串、整数或有限浮点数")
        if isinstance(value, str) and len(value) > 8192:
            raise RuntimeError(f"attributes[{key!r}] 字符串超过 8192 字符")
        if isinstance(value, float) and not math.isfinite(value):
            raise RuntimeError(f"attributes[{key!r}] 必须为有限数值")
        if value is not None and not isinstance(value, (str, int, float)):
            raise RuntimeError(f"attributes[{key!r}] 类型不受支持")
        result[key] = value
    if required and not result:
        raise RuntimeError("attributes 不能为空")
    return result


def _geometry(value: dict[str, Any] | None, *, required: bool) -> dict[str, Any] | None:
    if value is None:
        if required:
            raise RuntimeError("geometry 不能为空")
        return None
    if not isinstance(value, dict) or set(value) != {"type", "spatial_reference_wkid", "coordinates"}:
        raise RuntimeError(
            "geometry 必须且只可包含 type/spatial_reference_wkid/coordinates"
        )
    geometry_type = _command_text(value["type"], "geometry.type", maximum=32).lower()
    if geometry_type not in {"point", "polyline", "polygon"}:
        raise RuntimeError("geometry.type 须为 point/polyline/polygon")
    wkid = _generation(value["spatial_reference_wkid"], "geometry.spatial_reference_wkid")
    if wkid < 1:
        raise RuntimeError("geometry.spatial_reference_wkid 必须大于 0")
    raw_coordinates = value["coordinates"]
    if not isinstance(raw_coordinates, list) or len(raw_coordinates) > 10_000:
        raise RuntimeError("geometry.coordinates 必须为最多 10000 个二维坐标")
    coordinates: list[list[float]] = []
    for index, coordinate in enumerate(raw_coordinates):
        if not isinstance(coordinate, list) or len(coordinate) != 2:
            raise RuntimeError(f"geometry.coordinates[{index}] 必须为 [x, y]")
        x = _finite_optional(coordinate[0], f"geometry.coordinates[{index}][0]")
        y = _finite_optional(coordinate[1], f"geometry.coordinates[{index}][1]")
        if x is None or y is None:
            raise RuntimeError(f"geometry.coordinates[{index}] 不允许 null")
        coordinates.append([x, y])
    minimum = {"point": 1, "polyline": 2, "polygon": 4}[geometry_type]
    if len(coordinates) < minimum or (geometry_type == "point" and len(coordinates) != 1):
        raise RuntimeError(f"{geometry_type} 的坐标数量无效")
    if geometry_type == "polygon" and coordinates[0] != coordinates[-1]:
        raise RuntimeError("polygon ring 必须显式闭合")
    return {
        "type": geometry_type,
        "spatialReferenceWkid": wkid,
        "coordinates": coordinates,
    }


def _feature_body(
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    expected_edit_generation: int,
    *,
    confirm: bool,
) -> dict[str, Any]:
    body = _view_body(
        expected_map_uri,
        expected_context_generation,
        confirm=confirm,
    )
    body.update(
        {
            "layerUri": _command_text(layer_uri, "layer_uri"),
            "expectedEditGeneration": _generation(
                expected_edit_generation, "expected_edit_generation"
            ),
        }
    )
    return body


def create_feature(
    reference: str,
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    expected_edit_generation: int,
    geometry: dict[str, Any],
    *,
    attributes: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    body = _feature_body(
        expected_map_uri,
        expected_context_generation,
        layer_uri,
        expected_edit_generation,
        confirm=confirm,
    )
    body["geometry"] = _geometry(geometry, required=True)
    checked_attributes = _attributes(attributes, required=False)
    if checked_attributes is not None:
        body["attributes"] = checked_attributes
    return _request(
        session.discovery,
        "POST",
        "/v1/features/create",
        body=body,
        lease=session,
    )


def _selection_cas(
    body: dict[str, Any],
    expected_selection_generation: int,
    expected_count: int,
    expected_oid_digest: str,
    *,
    maximum_count: int,
) -> None:
    count = _generation(expected_count, "expected_count")
    if count < 1 or count > maximum_count:
        raise RuntimeError(f"expected_count 必须在 1–{maximum_count} 之间")
    digest = _command_text(expected_oid_digest, "expected_oid_digest", maximum=64)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise RuntimeError("expected_oid_digest 必须为 context 返回的 64 位 SHA-256")
    body.update(
        {
            "expectedSelectionGeneration": _generation(
                expected_selection_generation, "expected_selection_generation"
            ),
            "expectedCount": count,
            "expectedOidDigest": digest.lower(),
        }
    )


def modify_selected_features(
    reference: str,
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    expected_edit_generation: int,
    expected_selection_generation: int,
    expected_count: int,
    expected_oid_digest: str,
    *,
    attributes: dict[str, Any] | None = None,
    geometry: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    body = _feature_body(
        expected_map_uri,
        expected_context_generation,
        layer_uri,
        expected_edit_generation,
        confirm=confirm,
    )
    _selection_cas(
        body,
        expected_selection_generation,
        expected_count,
        expected_oid_digest,
        maximum_count=100,
    )
    checked_attributes = _attributes(attributes, required=False)
    checked_geometry = _geometry(geometry, required=False)
    if not checked_attributes and checked_geometry is None:
        raise RuntimeError("modify 至少提供一个非空 attributes 或 geometry")
    if checked_attributes:
        body["attributes"] = checked_attributes
    if checked_geometry is not None:
        if int(expected_count) != 1:
            raise RuntimeError("geometry 修改只允许 expected_count=1")
        body["geometry"] = checked_geometry
    return _request(
        session.discovery,
        "POST",
        "/v1/features/modify",
        body=body,
        lease=session,
    )


def delete_selected_features(
    reference: str,
    expected_map_uri: str,
    expected_context_generation: int,
    layer_uri: str,
    expected_edit_generation: int,
    expected_selection_generation: int,
    expected_count: int,
    expected_oid_digest: str,
    *,
    confirm: bool = False,
    confirm_delete_selection: bool = False,
) -> dict[str, Any]:
    session = _session(reference)
    body = _feature_body(
        expected_map_uri,
        expected_context_generation,
        layer_uri,
        expected_edit_generation,
        confirm=confirm,
    )
    _selection_cas(
        body,
        expected_selection_generation,
        expected_count,
        expected_oid_digest,
        maximum_count=1000,
    )
    if not confirm_delete_selection:
        raise RuntimeError("delete 必须设置 confirm_delete_selection=true")
    body["confirmDeleteSelection"] = True
    return _request(
        session.discovery,
        "POST",
        "/v1/features/delete",
        body=body,
        lease=session,
    )


def clear_local_sessions() -> int:
    """Test/support hook: forget local handles without contacting ArcGIS Pro."""
    with _SESSIONS_LOCK:
        count = len(_SESSIONS)
        _SESSIONS.clear()
    return count
