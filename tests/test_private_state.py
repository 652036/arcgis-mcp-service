from __future__ import annotations

import ctypes
import os
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import private_state
from arcgis_pro_mcp.private_state import (
    MAX_PRIVATE_STATE_BYTES,
    private_file_is_trusted,
    read_private_json,
    remove_private_json_if,
    write_private_json,
)


def _apply_windows_sddl(path: Path, sddl: str) -> None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
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
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise OSError(ctypes.get_last_error(), "ConvertStringSecurityDescriptor failed")
    try:
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ) or not present.value:
            raise OSError(ctypes.get_last_error(), "GetSecurityDescriptorDacl failed")
        status = advapi32.SetNamedSecurityInfoW(
            str(path),
            1,
            0x00000004 | 0x80000000,
            None,
            None,
            dacl,
            None,
        )
        if status:
            raise OSError(int(status), "SetNamedSecurityInfoW failed")
    finally:
        kernel32.LocalFree(descriptor)


class PrivateStateTests(unittest.TestCase):
    def test_private_state_round_trip_and_identity_bound_removal(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace) as root:
            path = Path(root) / "private" / "state.json"
            payload = {"session_id": "session-a", "token": "secret", "port": 17865}
            write_private_json(path, payload, temp_tag="session-a")
            self.assertTrue(private_file_is_trusted(path))
            self.assertEqual(read_private_json(path), payload)

            remove_private_json_if(path, "session_id", "different")
            self.assertTrue(path.exists())
            remove_private_json_if(path, "session_id", "session-a")
            self.assertFalse(path.exists())

    def test_untrusted_and_oversized_files_fail_closed(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace) as root:
            path = Path(root) / "state.json"
            path.write_bytes(b"x" * (MAX_PRIVATE_STATE_BYTES + 1))
            self.assertEqual(read_private_json(path), {})

    @unittest.skipUnless(os.name == "nt", "Windows DACL ACE verification is Windows-only")
    def test_windows_dacl_ace_validation_rejects_broad_trustees(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace) as root:
            path = Path(root) / "private" / "state.json"
            payload = {"session_id": "session-acl", "token": "secret"}
            write_private_json(path, payload, temp_tag="session-acl")
            sid = private_state._current_windows_sid()

            # Current-user-only protected DACL (matching SDK Add-In DiscoveryFile.cs) passes.
            _apply_windows_sddl(path, f"D:P(A;;FA;;;{sid})")
            self.assertTrue(private_file_is_trusted(path))
            self.assertEqual(read_private_json(path), payload)

            # Protected DACL granting Everyone (WD) read access must fail closed.
            _apply_windows_sddl(path, f"D:P(A;;FA;;;{sid})(A;;FR;;;WD)")
            self.assertFalse(private_file_is_trusted(path))
            self.assertEqual(read_private_json(path), {})

            # Protected DACL granting BUILTIN\Users (BU) must fail closed.
            _apply_windows_sddl(path, f"D:P(A;;FA;;;{sid})(A;;FR;;;BU)")
            self.assertFalse(private_file_is_trusted(path))
            self.assertEqual(read_private_json(path), {})

            # Protected DACL missing current-user allow ACE must fail closed.
            _apply_windows_sddl(path, "D:P(A;;FA;;;SY)")
            self.assertFalse(private_file_is_trusted(path))
            self.assertEqual(read_private_json(path), {})

            # Restore private ACL so TemporaryDirectory cleanup always succeeds.
            _apply_windows_sddl(path, f"D:P(A;;FA;;;{sid})(A;;FA;;;SY)(A;;FA;;;BA)")

    def test_read_private_json_rejects_toctou_inode_swap(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace) as root:
            path = Path(root) / "private" / "state.json"
            write_private_json(path, {"session_id": "s1"}, temp_tag="s1")
            real_fstat = os.fstat

            def swapped_fstat(fd: int) -> SimpleNamespace:
                actual = real_fstat(fd)
                return SimpleNamespace(
                    st_mode=actual.st_mode,
                    st_dev=actual.st_dev,
                    st_ino=actual.st_ino + 1,
                )

            with patch.object(private_state.os, "fstat", side_effect=swapped_fstat):
                self.assertEqual(read_private_json(path), {})

    def test_write_private_json_tolerates_stale_tmp_and_retries_windows_replace(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=workspace) as root:
            path = Path(root) / "private" / "state.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            stale = path.with_name(f".{path.name}.sessiona.tmp")
            stale.write_text("stale", encoding="utf-8")

            real_replace = os.replace
            attempts = {"count": 0}

            def flaky_replace(src: Path, dst: Path) -> None:
                attempts["count"] += 1
                if attempts["count"] <= 2:
                    raise PermissionError("sharing violation")
                real_replace(src, dst)

            with (
                patch.object(private_state.os, "replace", side_effect=flaky_replace),
                patch.object(private_state.os, "name", "nt"),
                patch.object(private_state.time, "sleep") as sleep_mock,
            ):
                write_private_json(path, {"session_id": "ok"}, temp_tag="session-a")

            self.assertEqual(attempts["count"], 3)
            self.assertEqual(sleep_mock.call_count, 2)
            self.assertEqual(read_private_json(path), {"session_id": "ok"})


if __name__ == "__main__":
    unittest.main()
