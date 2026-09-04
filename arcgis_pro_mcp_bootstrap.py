"""Load one clean ArcGIS Pro MCP module generation inside a long-lived Pro process."""
from __future__ import annotations

import importlib
import importlib.metadata
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

_PACKAGE_NAME = "arcgis_pro_mcp"
_IN_HOST_ENV = "ARCGIS_PRO_MCP_IN_PRO_HOST"
_PROCESS_STATE_NAME = "_arcgis_pro_mcp_bootstrap_state_v1"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_LOCAL_DEPENDENCY_DIRS = (".arcgis-pro-mcp-deps", ".test-mcp1")


def _process_lock() -> threading.Lock:
    """Keep the launch lock stable even when this bootstrap module is reloaded."""
    state = getattr(sys, _PROCESS_STATE_NAME, None)
    if not isinstance(state, dict) or not hasattr(state.get("lock"), "acquire"):
        state = {"lock": threading.Lock()}
        setattr(sys, _PROCESS_STATE_NAME, state)
    return state["lock"]


@contextmanager
def _exclusive_launch() -> Iterator[None]:
    lock = _process_lock()
    if not lock.acquire(blocking=False):
        raise RuntimeError(
            "当前 ArcGIS Pro 进程已经在启动或运行窗口宿主。"
            "请先取消旧的“接入当前窗口”，等待“窗口宿主已停止”后再运行。"
        )
    try:
        yield
    finally:
        lock.release()


def _host_marker_is_set() -> bool:
    return os.environ.get(_IN_HOST_ENV, "").strip().lower() in _TRUE_VALUES


def _require_stopped_host() -> None:
    if _host_marker_is_set():
        raise RuntimeError(
            "检测到当前 ArcGIS Pro 进程仍处于窗口宿主模式，不能安全刷新模块。"
            "请先取消旧的“接入当前窗口”，等待“窗口宿主已停止”后重试；"
            "若标记未恢复，请重启 ArcGIS Pro。"
        )


def _path_key(value: object) -> str | None:
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(value))))
    except (OSError, TypeError, ValueError):
        return None


def _promote_repo_root(repo_root: Path) -> None:
    root_text = str(repo_root)
    root_key = _path_key(root_text)
    # Keep the empty-string entry because it follows the process's future cwd;
    # an explicit checkout path is still placed ahead of it for this launch.
    sys.path[:] = [
        entry for entry in sys.path if entry == "" or _path_key(entry) != root_key
    ]
    sys.path.insert(0, root_text)


def _activate_repo_dependencies(repo_root: Path) -> Path | None:
    """Expose a checkout-local FastMCP install to the embedded Pro interpreter."""
    for directory_name in _LOCAL_DEPENDENCY_DIRS:
        dependency_root = (repo_root / directory_name).resolve()
        fastmcp_package = dependency_root / "mcp" / "server" / "fastmcp"
        try:
            available = fastmcp_package.is_dir()
        except OSError:
            available = False
        if not available:
            continue
        dependency_text = str(dependency_root)
        dependency_key = _path_key(dependency_text)
        sys.path[:] = [
            entry
            for entry in sys.path
            if entry == "" or _path_key(entry) != dependency_key
        ]
        # The repository itself remains first so a dependency target cannot
        # shadow this checkout's arcgis_pro_mcp package.
        sys.path.insert(1 if sys.path else 0, dependency_text)
        return dependency_root
    return None


def _package_module_names() -> list[str]:
    prefix = f"{_PACKAGE_NAME}."
    names = [
        name
        for name in tuple(sys.modules)
        if name == _PACKAGE_NAME or name.startswith(prefix)
    ]
    return sorted(names, key=lambda name: (name.count("."), len(name)), reverse=True)


def _snapshot_package_modules() -> dict[str, ModuleType | None]:
    previous: dict[str, ModuleType | None] = {}
    for name in _package_module_names():
        try:
            previous[name] = sys.modules[name]
        except KeyError:
            continue
    return previous


def _remove_package_modules() -> None:
    for name in _package_module_names():
        sys.modules.pop(name, None)


def _restore_package_modules(previous: dict[str, ModuleType | None]) -> None:
    for name in _package_module_names():
        sys.modules.pop(name, None)
    sys.modules.update(previous)


def _assert_repo_generation(repo_root: Path) -> None:
    package_root = (repo_root / _PACKAGE_NAME).resolve()
    package_key = _path_key(package_root)
    for name in _package_module_names():
        module = sys.modules.get(name)
        source = getattr(module, "__file__", None)
        if not source:
            raise RuntimeError(f"模块 {name} 没有可验证的 __file__ 来源")
        source_path = Path(source).resolve()
        source_key = _path_key(source_path)
        try:
            common = os.path.commonpath([str(package_root), str(source_path)])
        except ValueError as exc:
            raise RuntimeError(f"模块 {name} 来自其他驱动器：{source_path}") from exc
        if _path_key(common) != package_key or source_key == package_key:
            raise RuntimeError(
                f"模块 {name} 未从当前仓库加载：{source_path}；期望位于 {package_root}"
            )


def _require_compatible_fastmcp(repo_root: Path | None = None) -> str:
    """Fail with an actionable error before importing the large tool registry."""
    try:
        module = importlib.import_module("mcp.server.fastmcp")
        if not hasattr(module, "FastMCP"):
            raise ImportError("FastMCP symbol is missing")
    except Exception as exc:  # noqa: BLE001
        try:
            installed = importlib.metadata.version("mcp")
        except importlib.metadata.PackageNotFoundError:
            installed = "not installed"
        dependency_target = (
            (repo_root / _LOCAL_DEPENDENCY_DIRS[0]).resolve()
            if repo_root is not None
            else Path(".arcgis-pro-mcp-deps")
        )
        environment_python = Path(sys.prefix) / "python.exe"
        python_executable = environment_python if environment_python.is_file() else Path(sys.executable)
        raise RuntimeError(
            "ArcGIS Pro Python 中缺少兼容的 FastMCP。当前 mcp 版本："
            f"{installed}。请在 PowerShell 中执行 "
            f"& \"{python_executable}\" -m pip install --target "
            f"\"{dependency_target}\" \"mcp>=1.20,<2\"，然后重新运行接入工具。"
        ) from exc
    try:
        return importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _load_fresh_host_unlocked(repo_root: str | os.PathLike[str]) -> ModuleType:
    """Replace all cached package modules and return a clean ``pro_host`` module."""
    _require_stopped_host()
    root = Path(repo_root).resolve()
    host_source = root / _PACKAGE_NAME / "pro_host.py"
    if not host_source.is_file():
        raise RuntimeError(f"仓库不完整，找不到：{host_source}")

    previous_sys_path = list(sys.path)
    previous_modules = _snapshot_package_modules()
    try:
        _promote_repo_root(root)
        _activate_repo_dependencies(root)
        importlib.invalidate_caches()
        _remove_package_modules()
        importlib.import_module(_PACKAGE_NAME)
        importlib.import_module(f"{_PACKAGE_NAME}.pro_attach")
        pro_host = importlib.import_module(f"{_PACKAGE_NAME}.pro_host")
        _require_compatible_fastmcp(root)
        # Build a fresh FastMCP manager and all registered helper imports now,
        # before accepting a live-window request.  Do not import __main__ here:
        # that is the stdio entry point which installs the forwarding proxy.
        importlib.import_module(f"{_PACKAGE_NAME}.server")
        # pro_host adds the user site so the MCP dependency is visible. Put the
        # checkout back at highest precedence after that dependency setup.
        _promote_repo_root(root)
        _assert_repo_generation(root)
    except BaseException:
        try:
            _restore_package_modules(previous_modules)
        finally:
            sys.path[:] = previous_sys_path
        raise
    return pro_host



def _default_emit(message: str) -> None:
    """print() that survives cp1252 / legacy Windows consoles."""
    try:
        print(message)
        return
    except UnicodeEncodeError:
        pass
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    payload = (message + "\n").encode(encoding, errors="replace")
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        try:
            buffer.write(payload)
            buffer.flush()
            return
        except Exception:  # noqa: BLE001
            pass


def run_host(
    repo_root: str | os.PathLike[str],
    emit: Callable[[str], object] | None = None,
) -> None:
    """Fresh-load the package and run one exclusive foreground window host."""
    if emit is None:
        emit = _default_emit
    with _exclusive_launch():
        _require_stopped_host()
        root = Path(repo_root).resolve()
        pro_host = _load_fresh_host_unlocked(root)
        emit(f"已从当前仓库刷新 ArcGIS Pro MCP 模块：{root}")
        pro_host.main()
