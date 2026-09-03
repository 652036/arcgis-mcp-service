"""在 ArcGIS Pro Python 窗口中运行，把 MCP 接到当前工程。"""
from __future__ import annotations

import inspect
import json
import os
import queue
import secrets
import site
import socket
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _ensure_import_path() -> None:
    here = Path(__file__).resolve().parent.parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        user_site = site.getusersitepackages()
        if user_site and user_site not in sys.path:
            sys.path.insert(0, user_site)
            site.addsitedir(user_site)
    except Exception:  # noqa: BLE001
        pass


_ensure_import_path()

from arcgis_pro_mcp import __version__ as PACKAGE_VERSION  # noqa: E402
from arcgis_pro_mcp.pro_attach import (  # noqa: E402
    DEFAULT_PORT,
    ENV_IN_HOST,
    FORWARDED_ENV_KEYS,
    HOST_JOB_TIMEOUT,
    PROTOCOL_VERSION,
    SERVICE_NAME,
    TOKEN_HEADER,
    configured_host_port,
    state_path,
)
from arcgis_pro_mcp.pro_attach import ENV_PORT as ENV_PORT  # noqa: E402

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_QUEUE_SIZE = 32
_MISSING = object()
_LIVE_LOCK = threading.Lock()
_LIVE: dict[str, Any] = {
    "project": None,
    "pid": os.getpid(),
    "active_view": None,
    "ready": False,
    "state": "STARTING",
    "busy": False,
    "active_tool": None,
    "active_request_id": None,
    "completed_calls": 0,
    "failed_calls": 0,
    "last_error": None,
    "last_started_at": None,
    "last_finished_at": None,
}


def _prefer_replace_errors_on_stdio() -> None:
    """Avoid UnicodeEncodeError crashes on narrow Windows console encodings."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            continue


def _write_console(message: str) -> None:
    """Write to stdout without aborting the host on cp1252/legacy consoles."""
    stream = sys.stdout
    try:
        stream.write(message)
        stream.flush()
        return
    except UnicodeEncodeError:
        pass
    encoding = getattr(stream, "encoding", None) or "utf-8"
    payload = message.encode(encoding, errors="replace")
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        try:
            buffer.write(payload)
            buffer.flush()
            return
        except Exception:  # noqa: BLE001
            pass
    fallback = getattr(sys, "__stdout__", None)
    if fallback is not None and fallback is not stream:
        try:
            fallback.write(message.encode("ascii", errors="replace").decode("ascii"))
            fallback.flush()
        except Exception:  # noqa: BLE001
            pass

class _ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """Prevent two Pro processes from owning the same window-host endpoint."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _candidate_ports() -> list[int]:
    configured_port = configured_host_port()
    return [configured_port or DEFAULT_PORT]


def _restore_env_value(name: str, previous: object) -> None:
    if previous is _MISSING:
        os.environ.pop(name, None)
    else:
        os.environ[name] = str(previous)


def _current_project() -> tuple[Any, str]:
    import arcpy  # type: ignore[import-untyped]

    try:
        project = arcpy.mp.ArcGISProject("CURRENT")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "没有当前工程。请先在 ArcGIS Pro 里打开一个 .aprx，再运行本脚本。"
        ) from exc
    path = str(getattr(project, "filePath", None) or "").strip().strip('"')
    if not path or not os.path.isabs(path):
        raise RuntimeError(
            "当前 ArcGIS Pro 工程尚未保存，没有可锁存的稳定 .aprx 身份；"
            "请先在 Pro 中保存工程，再启动或继续窗口宿主"
        )
    return project, path


def _project_identity(path: str) -> str:
    value = (path or "").strip().strip('"')
    if not value or value.upper() == "CURRENT":
        return value.upper()
    return os.path.normcase(os.path.realpath(os.path.abspath(value))).lower()


def _active_view_snapshot(project: Any) -> dict[str, Any] | None:
    view = getattr(project, "activeView", None)
    if view is None:
        return None
    out: dict[str, Any] = {"python_type": type(view).__name__}
    name = getattr(view, "name", None)
    if name:
        out["name"] = str(name)
    view_map = getattr(view, "map", None)
    if view_map is not None and hasattr(view, "camera"):
        out["type"] = "MAP_VIEW"
        out["map_name"] = str(getattr(view_map, "name", ""))
    elif hasattr(view, "listElements"):
        out["type"] = "LAYOUT_VIEW"
    else:
        out["type"] = type(view).__name__.upper()
    active_map = getattr(project, "activeMap", None)
    if active_map is not None:
        out["active_map_name"] = str(getattr(active_map, "name", ""))
    return out


def _refresh_live_context() -> None:
    try:
        project, project_path = _current_project()
        active_view = _active_view_snapshot(project)
        with _LIVE_LOCK:
            _LIVE["project"] = project_path
            _LIVE["project_is_read_only"] = bool(getattr(project, "isReadOnly", False))
            _LIVE["active_view"] = active_view
            if _LIVE.get("state") != "STOPPING":
                _LIVE["ready"] = True
                if _LIVE.get("state") != "BUSY":
                    _LIVE["state"] = "READY"
            _LIVE["context_error"] = None
    except Exception as exc:  # noqa: BLE001
        with _LIVE_LOCK:
            _LIVE["project"] = None
            _LIVE["project_is_read_only"] = None
            _LIVE["active_view"] = None
            _LIVE["ready"] = False
            _LIVE["context_error"] = str(exc)[:500]


@contextmanager
def _client_environment(values: dict[str, Any] | None):
    """Apply only the security/path policy carried by the authenticated MCP client."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise RuntimeError("environment 必须是对象")
    unknown = sorted(set(values) - set(FORWARDED_ENV_KEYS))
    if unknown:
        raise RuntimeError(f"environment 包含不允许的键：{unknown}")
    clean: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(value, str):
            raise RuntimeError(f"environment.{key} 必须是字符串")
        if len(value) > 32768 or "\x00" in value:
            raise RuntimeError(f"environment.{key} 无效或过长")
        clean[key] = value
    previous = {key: os.environ.get(key, _MISSING) for key in FORWARDED_ENV_KEYS}
    previous_in_host = os.environ.get(ENV_IN_HOST, _MISSING)
    try:
        for key in FORWARDED_ENV_KEYS:
            if key in clean:
                os.environ[key] = clean[key]
            else:
                os.environ.pop(key, None)
        os.environ[ENV_IN_HOST] = "1"
        yield
    finally:
        for key, value in previous.items():
            if value is _MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        if previous_in_host is _MISSING:
            os.environ.pop(ENV_IN_HOST, None)
        else:
            os.environ[ENV_IN_HOST] = str(previous_in_host)


def _invoke_tool(
    name: str,
    arguments: dict[str, Any],
    environment: dict[str, Any] | None = None,
    current_project: Any | None = None,
    current_project_path: str = "",
) -> str:
    with _client_environment(environment):
        from arcgis_pro_mcp.server import _bind_current_project, mcp

        mgr = mcp._tool_manager
        tool = mgr.get_tool(name)
        if tool is None:
            raise RuntimeError(f"未知工具：{name}")
        args = dict(arguments or {})
        requested = str(args.get("aprx_path") or "").strip().strip('"')
        if requested.upper() != "CURRENT":
            raise RuntimeError(
                "窗口宿主 RPC 只接受 aprx_path=CURRENT；"
                "无工程目标或绝对 .aprx 路径的工具必须由 stdio 文件模式执行"
            )
        try:
            params = inspect.signature(tool.fn).parameters
        except (TypeError, ValueError):
            params = {}
        if "aprx_path" not in params:
            raise RuntimeError(
                f"工具 {name!r} 没有 aprx_path 参数，不能通过窗口宿主 RPC 执行"
            )
        args["aprx_path"] = "CURRENT"
        if current_project is None:
            result = tool.fn(**args)
        else:
            with _bind_current_project(current_project, current_project_path):
                result = tool.fn(**args)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


class _Job:
    def __init__(self, payload: dict[str, Any], request_id: str) -> None:
        self.payload = payload
        self.request_id = request_id
        self.deadline = time.monotonic() + HOST_JOB_TIMEOUT
        self.state = "QUEUED"
        self._state_lock = threading.Lock()
        self.reply: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def claim(self) -> bool:
        """Atomically move a queued, non-expired job to RUNNING."""
        with self._state_lock:
            if self.state != "QUEUED":
                return False
            if time.monotonic() >= self.deadline:
                self.state = "CANCELLED"
                return False
            self.state = "RUNNING"
            return True

    def cancel_if_queued(self) -> bool:
        with self._state_lock:
            if self.state != "QUEUED":
                return False
            self.state = "CANCELLED"
            return True

    def finish(self, state: str) -> None:
        with self._state_lock:
            self.state = state


def _write_state(port: int, project: str, session_id: str, token: str, started_at: float) -> None:
    data = {
        "ok": True,
        "service": SERVICE_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "package_version": PACKAGE_VERSION,
        "port": port,
        "pid": os.getpid(),
        "project": project,
        "session_id": session_id,
        "token": token,
        "started_at": started_at,
    }
    path = Path(state_path())
    tmp = path.with_name(f"{path.name}.{session_id}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _clear_state(session_id: str) -> None:
    path = Path(state_path())
    tmp = path.with_name(f"{path.name}.{session_id}.tmp")
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("session_id") != session_id:
            return
        path.unlink(missing_ok=True)
    except (OSError, ValueError, json.JSONDecodeError):
        pass


def _public_status(jobs: queue.Queue[_Job]) -> dict[str, Any]:
    with _LIVE_LOCK:
        status = dict(_LIVE)
    status.update(
        {
            "ok": True,
            "service": SERVICE_NAME,
            "protocol_version": PROTOCOL_VERSION,
            "package_version": PACKAGE_VERSION,
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "queue_depth": jobs.qsize(),
        }
    )
    return status


def _run_host_main() -> None:
    _prefer_replace_errors_on_stdio()
    previous_in_host = os.environ.get(ENV_IN_HOST, _MISSING)
    os.environ[ENV_IN_HOST] = "1"
    try:
        initial_project, project_path = _current_project()
        del initial_project
    except BaseException:
        _restore_env_value(ENV_IN_HOST, previous_in_host)
        raise
    session_id = secrets.token_hex(16)
    token = secrets.token_urlsafe(32)
    started_at = time.time()
    with _LIVE_LOCK:
        _LIVE.update(
            {
                "project": project_path,
                "pid": os.getpid(),
                "session_id": session_id,
                "started_at": started_at,
                "active_view": None,
                "ready": False,
                "state": "STARTING",
                "busy": False,
                "active_tool": None,
                "active_request_id": None,
                "completed_calls": 0,
                "failed_calls": 0,
                "last_error": None,
                "last_started_at": None,
                "last_finished_at": None,
            }
        )
    try:
        _refresh_live_context()
    except BaseException:
        _restore_env_value(ENV_IN_HOST, previous_in_host)
        raise
    jobs: queue.Queue[_Job] = queue.Queue(maxsize=MAX_QUEUE_SIZE)
    stop = threading.Event()
    admission_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _send(self, code: int, data: dict[str, Any]) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                # Windows often resets the client socket once shutdown begins.
                return

        def _authorized(self) -> bool:
            supplied = self.headers.get(TOKEN_HEADER, "")
            if secrets.compare_digest(supplied, token):
                return True
            self._send(401, {"ok": False, "error": "unauthorized"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            if self.path.split("?", 1)[0] != "/health":
                self._send(404, {"ok": False, "error": "not found"})
                return
            self._send(200, _public_status(jobs))

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            route = self.path.split("?", 1)[0]
            if route == "/stop":
                with admission_lock:
                    stop.set()
                    with _LIVE_LOCK:
                        _LIVE["state"] = "STOPPING"
                        _LIVE["ready"] = False
                        busy = bool(_LIVE.get("busy"))
                        active_request_id = _LIVE.get("active_request_id")
                self._send(
                    200,
                    {
                        "ok": True,
                        "stopping": True,
                        "detached": False,
                        "busy": busy,
                        "active_request_id": active_request_id,
                        "session_id": session_id,
                    },
                )
                return
            if route != "/call":
                self._send(404, {"ok": False, "error": "not found"})
                return
            if stop.is_set():
                self._send(503, {"ok": False, "error": "window host is stopping"})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._send(415, {"ok": False, "error": "content type must be application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send(400, {"ok": False, "error": "invalid content length"})
                return
            if length < 0 or length > MAX_REQUEST_BYTES:
                self._send(413, {"ok": False, "error": "request body too large"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                self._send(400, {"ok": False, "error": "invalid json"})
                return
            if not isinstance(payload, dict):
                self._send(400, {"ok": False, "error": "invalid payload"})
                return
            if payload.get("protocol_version") != PROTOCOL_VERSION:
                self._send(
                    409,
                    {
                        "ok": False,
                        "error": (
                            "protocol version mismatch; restart the MCP client and the ArcGIS Pro host"
                        ),
                        "protocol_version": PROTOCOL_VERSION,
                    },
                )
                return
            if payload.get("session_id") != session_id:
                self._send(
                    409,
                    {
                        "ok": False,
                        "error": "window session changed; refresh status before retrying",
                        "session_id": session_id,
                    },
                )
                return
            request_id = str(payload.get("request_id") or "")[:128]
            if not request_id:
                self._send(400, {"ok": False, "error": "missing request_id"})
                return
            job = _Job(payload, request_id)
            queue_full = False
            with admission_lock:
                if stop.is_set():
                    self._send(503, {"ok": False, "error": "window host is stopping"})
                    return
                try:
                    jobs.put_nowait(job)
                except queue.Full:
                    queue_full = True
            if queue_full:
                self._send(429, {"ok": False, "error": "窗口宿主队列已满"})
                return
            try:
                result = job.reply.get(timeout=HOST_JOB_TIMEOUT)
            except queue.Empty:
                cancelled = job.cancel_if_queued()
                self._send(
                    504,
                    {
                        "ok": False,
                        "protocol_version": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "request_id": request_id,
                        "error": (
                            "窗口宿主排队超时，任务已取消"
                            if cancelled
                            else "窗口宿主执行超时；任务已开始，后台执行可能仍在继续"
                        ),
                    },
                )
                return
            self._send(200, result)

    httpd: _ExclusiveThreadingHTTPServer | None = None
    bind_errors: list[str] = []
    try:
        candidates = _candidate_ports()
    except BaseException:
        _restore_env_value(ENV_IN_HOST, previous_in_host)
        raise
    for candidate in candidates:
        try:
            httpd = _ExclusiveThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            break
        except OSError as exc:
            bind_errors.append(f"{candidate}: {exc}")
        except BaseException:
            _restore_env_value(ENV_IN_HOST, previous_in_host)
            raise
    if httpd is None:
        _restore_env_value(ENV_IN_HOST, previous_in_host)
        raise RuntimeError(f"无法监听 ArcGIS Pro MCP 窗口宿主端口：{'; '.join(bind_errors)}")
    thread: threading.Thread | None = None
    try:
        port = int(httpd.server_address[1])
        httpd.daemon_threads = True
        thread = threading.Thread(
            target=httpd.serve_forever,
            name="arcgis-pro-mcp-host",
            daemon=True,
        )
        thread.start()
        _write_state(port, project_path, session_id, token, started_at)
        _write_console(
            "\n已接入 ArcGIS Pro 当前窗口\n"
            f"工程：{project_path}\n"
            f"地址：http://127.0.0.1:{port}\n"
            f"会话：{session_id}\n"
            "安全策略：每次调用沿用 MCP 客户端配置；宿主不再强制开启写入或硬编码输出目录。\n"
            "请保持本 Python 窗口不要关闭。只有 aprx_path=CURRENT 的工程调用会进入此窗口。\n"
            "结束：在此窗口按 Ctrl+C，或关闭本脚本。\n\n"
        )
    except BaseException:
        with admission_lock:
            stop.set()
        if thread is not None and thread.is_alive():
            httpd.shutdown()
            thread.join(timeout=2)
        while True:
            try:
                pending = jobs.get_nowait()
            except queue.Empty:
                break
            try:
                pending.reply.put_nowait(
                    {
                        "ok": False,
                        "protocol_version": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "request_id": pending.request_id,
                        "error": "窗口宿主启动被中断，任务未执行",
                    }
                )
            except queue.Full:
                pass
            jobs.task_done()
        _clear_state(session_id)
        httpd.server_close()
        _restore_env_value(ENV_IN_HOST, previous_in_host)
        raise
    def _gp_cancelled() -> bool:
        try:
            import arcpy  # type: ignore[import-untyped]

            return bool(getattr(arcpy.env, "isCancelled", False))
        except Exception:  # noqa: BLE001
            return False

    last_context_refresh = 0.0
    active_job: _Job | None = None
    active_job_accounted = True
    try:
        while not stop.is_set() and not _gp_cancelled():
            now = time.monotonic()
            if now - last_context_refresh >= 1.0:
                _refresh_live_context()
                last_context_refresh = now
            try:
                job = jobs.get(timeout=0.25)
            except queue.Empty:
                continue
            active_job = job
            active_job_accounted = False
            tool = str(job.payload.get("tool") or "")
            with admission_lock:
                stopping = stop.is_set()
                claimed = False if stopping else job.claim()
                if claimed:
                    with _LIVE_LOCK:
                        _LIVE["busy"] = True
                        _LIVE["state"] = "BUSY"
                        _LIVE["active_tool"] = tool or None
                        _LIVE["active_request_id"] = job.request_id
                        _LIVE["last_started_at"] = time.time()
            if stopping:
                job.cancel_if_queued()
                try:
                    job.reply.put_nowait(
                        {
                            "ok": False,
                            "protocol_version": PROTOCOL_VERSION,
                            "session_id": session_id,
                            "request_id": job.request_id,
                            "error": "窗口宿主正在停止，任务未执行",
                        }
                    )
                except queue.Full:
                    pass
                active_job_accounted = True
                active_job = None
                jobs.task_done()
                continue
            if not claimed:
                try:
                    job.reply.put_nowait(
                        {
                            "ok": False,
                            "protocol_version": PROTOCOL_VERSION,
                            "session_id": session_id,
                            "request_id": job.request_id,
                            "error": "窗口宿主任务在执行前已取消或过期",
                        }
                    )
                except queue.Full:
                    pass
                active_job_accounted = True
                active_job = None
                jobs.task_done()
                continue
            try:
                arguments = job.payload.get("arguments") or {}
                if not tool:
                    raise RuntimeError("缺少 tool")
                if not isinstance(arguments, dict):
                    raise RuntimeError("arguments 必须是对象")
                expected_project = str(job.payload.get("expected_project") or "")
                current_project, current_path = _current_project()
                try:
                    if not expected_project:
                        raise RuntimeError("缺少 expected_project，无法安全确认窗口目标")
                    if _project_identity(expected_project) != _project_identity(current_path):
                        raise RuntimeError(
                            "ArcGIS Pro 当前工程在排队期间已切换；"
                            f"期望 {expected_project}，实际 {current_path}。请重新读取窗口状态后再调用"
                        )
                    result = _invoke_tool(
                        tool,
                        arguments,
                        job.payload.get("environment"),
                        current_project,
                        current_path,
                    )
                finally:
                    del current_project
                _refresh_live_context()
                with _LIVE_LOCK:
                    _LIVE["completed_calls"] = int(_LIVE["completed_calls"]) + 1
                    _LIVE["last_error"] = None
                job.finish("SUCCEEDED")
                job.reply.put(
                    {
                        "ok": True,
                        "protocol_version": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "request_id": job.request_id,
                        "result": result,
                    }
                )
            except BaseException as exc:  # noqa: BLE001
                fatal = not isinstance(exc, Exception)
                detail = str(exc).strip()
                error_message = detail or type(exc).__name__
                if fatal:
                    error_message = "窗口宿主执行被中断；任务结果可能未知，不要自动重试"
                    if detail:
                        error_message += f"：{detail}"
                with _LIVE_LOCK:
                    _LIVE["failed_calls"] = int(_LIVE["failed_calls"]) + 1
                    _LIVE["last_error"] = error_message[:1000]
                job.finish("FAILED")
                job.reply.put_nowait(
                    {
                        "ok": False,
                        "protocol_version": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "request_id": job.request_id,
                        "error": error_message,
                    }
                )
                if fatal:
                    raise
            finally:
                with _LIVE_LOCK:
                    _LIVE["busy"] = False
                    if _LIVE.get("state") != "STOPPING":
                        _LIVE["state"] = "READY" if _LIVE.get("ready") else "NOT_READY"
                    _LIVE["active_tool"] = None
                    _LIVE["active_request_id"] = None
                    _LIVE["last_finished_at"] = time.time()
                active_job_accounted = True
                active_job = None
                jobs.task_done()
    except KeyboardInterrupt:
        pass
    finally:
        with admission_lock:
            stop.set()
        if active_job is not None and not active_job_accounted:
            active_job.finish("FAILED")
            try:
                active_job.reply.put_nowait(
                    {
                        "ok": False,
                        "protocol_version": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "request_id": active_job.request_id,
                        "error": "窗口宿主执行被中断；任务结果可能未知",
                    }
                )
            except queue.Full:
                pass
            active_job_accounted = True
            active_job = None
            jobs.task_done()
        with _LIVE_LOCK:
            _LIVE["state"] = "STOPPING"
            _LIVE["ready"] = False
        httpd.shutdown()
        thread.join(timeout=2)
        while True:
            try:
                pending = jobs.get_nowait()
            except queue.Empty:
                break
            try:
                pending.reply.put_nowait(
                    {
                        "ok": False,
                        "protocol_version": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "request_id": pending.request_id,
                        "error": "窗口宿主正在停止",
                    }
                )
            except queue.Full:
                pass
            jobs.task_done()
        _clear_state(session_id)
        httpd.server_close()
        _restore_env_value(ENV_IN_HOST, previous_in_host)
        with _LIVE_LOCK:
            _LIVE["state"] = "STOPPED"
        _write_console("窗口宿主已停止。\n")


def main() -> None:
    """Run the host while always restoring the caller's process marker."""
    previous_in_host = os.environ.get(ENV_IN_HOST, _MISSING)
    try:
        _run_host_main()
    finally:
        _restore_env_value(ENV_IN_HOST, previous_in_host)


if __name__ == "__main__":
    main()
