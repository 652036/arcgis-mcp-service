"""Private, bounded state-file helpers for loopback bridge discovery.

Bearer discovery files are capabilities.  They therefore live in a dedicated
per-user directory, use atomic writes, reject reparse points, and are accepted
only when owned by the current user with a protected/private ACL (Windows) or
0600/0700-style permissions (POSIX).
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

MAX_PRIVATE_STATE_BYTES = 16_384


def default_state_directory() -> Path:
    """Return the per-user window-host discovery directory."""

    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA", "").strip()
        if not root or not os.path.isabs(root):
            raise RuntimeError("LOCALAPPDATA 未配置为绝对路径，无法安全发布窗口宿主状态")
        return Path(root) / "ArcGISProMcp" / "window-host"
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    base = Path(runtime) if runtime and os.path.isabs(runtime) else Path(tempfile.gettempdir())
    uid = getattr(os, "getuid", lambda: 0)()
    return base / f"arcgis-pro-mcp-{uid}" / "window-host"


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(attributes & reparse_flag)


def _current_windows_sid() -> str:
    import ctypes
    from ctypes import wintypes

    token_query = 0x0008
    token_user = 1
    token = wintypes.HANDLE()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(size))
        if not size.value:
            raise OSError(ctypes.get_last_error(), "GetTokenInformation size failed")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token,
            token_user,
            buffer,
            size,
            ctypes.byref(size),
        ):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")

        class _SidAndAttributes(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        class _TokenUser(ctypes.Structure):
            _fields_ = [("User", _SidAndAttributes)]

        sid = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents.User.Sid
        rendered = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(rendered)):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSid failed")
        try:
            return str(rendered.value)
        finally:
            kernel32.LocalFree(rendered)
    finally:
        kernel32.CloseHandle(token)


def _set_windows_private_acl(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    sid = _current_windows_sid()
    # Protected DACL: current user plus LocalSystem and local Administrators.
    sddl = f"D:P(A;;FA;;;{sid})(A;;FA;;;SY)(A;;FA;;;BA)"
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(descriptor),
        None,
    ):
        raise OSError(ctypes.get_last_error(), "无法创建私有 Windows DACL")
    try:
        if not advapi32.ConvertStringSidToSidW(sid, ctypes.byref(owner)):
            raise OSError(ctypes.get_last_error(), "无法创建当前 Windows 用户 SID")
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(present),
            ctypes.byref(dacl),
            ctypes.byref(defaulted),
        ) or not present.value:
            raise OSError(ctypes.get_last_error(), "无法读取私有 Windows DACL")
        owner_information = 0x00000001
        dacl_information = 0x00000004
        protected_dacl_information = 0x80000000
        status = advapi32.SetNamedSecurityInfoW(
            str(path),
            1,
            owner_information | dacl_information | protected_dacl_information,
            owner,
            None,
            dacl,
            None,
        )
        if status:
            raise OSError(int(status), "无法设置私有 Windows DACL")
    finally:
        if owner:
            kernel32.LocalFree(owner)
        kernel32.LocalFree(descriptor)


def _windows_owner_and_dacl_are_private(path: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ushort),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    owner_information = 0x00000001
    dacl_information = 0x00000004
    status = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,
        owner_information | dacl_information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status:
        return False
    try:
        rendered = wintypes.LPWSTR()
        if not owner or not advapi32.ConvertSidToStringSidW(owner, ctypes.byref(rendered)):
            return False
        try:
            owner_sid = str(rendered.value)
        finally:
            kernel32.LocalFree(rendered)
        control = ctypes.c_ushort()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            descriptor,
            ctypes.byref(control),
            ctypes.byref(revision),
        ):
            return False
        return bool(dacl) and owner_sid == _current_windows_sid() and bool(control.value & 0x1000)
    finally:
        kernel32.LocalFree(descriptor)


def _set_private_permissions(path: Path, *, directory: bool) -> None:
    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except OSError:
        if os.name != "nt":
            raise
    if os.name == "nt":
        _set_windows_private_acl(path)


def _permissions_are_private(path: Path, *, directory: bool) -> bool:
    if os.name == "nt":
        try:
            return _windows_owner_and_dacl_are_private(path)
        except OSError:
            return False
    try:
        info = path.stat()
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            return False
        required_type = stat.S_ISDIR if directory else stat.S_ISREG
        return required_type(info.st_mode) and info.st_mode & 0o077 == 0
    except OSError:
        return False


def _prepare_private_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if _is_reparse_or_link(directory) or not directory.is_dir():
        raise RuntimeError("窗口宿主状态目录不是可信普通目录")
    _set_private_permissions(directory, directory=True)
    if not _permissions_are_private(directory, directory=True):
        raise RuntimeError("无法验证窗口宿主状态目录的当前用户私有权限")


def private_file_is_trusted(path: Path) -> bool:
    """Return whether an existing state file satisfies the local trust contract."""

    try:
        if not path.parent.is_dir() or _is_reparse_or_link(path.parent):
            return False
        if not _permissions_are_private(path.parent, directory=True):
            return False
        info = path.lstat()
        if _is_reparse_or_link(path) or not stat.S_ISREG(info.st_mode):
            return False
        if info.st_size < 2 or info.st_size > MAX_PRIVATE_STATE_BYTES:
            return False
        return _permissions_are_private(path, directory=False)
    except OSError:
        return False


def read_private_json(path: Path) -> dict[str, Any]:
    """Read a trusted, bounded JSON object or return an empty object."""

    if not private_file_is_trusted(path):
        return {}
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            raw = os.read(descriptor, MAX_PRIVATE_STATE_BYTES + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            return {}
        if len(raw) > MAX_PRIVATE_STATE_BYTES:
            return {}
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_private_json(path: Path, payload: dict[str, Any], *, temp_tag: str) -> None:
    """Atomically publish a bounded JSON state object with private permissions."""

    _prepare_private_directory(path.parent)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_PRIVATE_STATE_BYTES:
        raise RuntimeError("窗口宿主状态超过大小限制")
    safe_tag = "".join(ch for ch in str(temp_tag) if ch.isalnum())[:64]
    if not safe_tag:
        raise RuntimeError("窗口宿主临时状态标识无效")
    temporary = path.with_name(f".{path.name}.{safe_tag}.tmp")
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _set_private_permissions(temporary, directory=False)
        if not _permissions_are_private(temporary, directory=False):
            raise RuntimeError("无法验证窗口宿主临时状态文件的私有权限")
        os.replace(temporary, path)
        _set_private_permissions(path, directory=False)
        if not private_file_is_trusted(path):
            raise RuntimeError("无法验证窗口宿主状态文件的当前用户私有权限")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def remove_private_json_if(path: Path, key: str, expected: str) -> None:
    """Remove only a trusted state file whose identity field still matches."""

    payload = read_private_json(path)
    if payload.get(key) != expected:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
