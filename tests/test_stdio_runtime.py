from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_SERVER = r'''
import os, sys
from mcp.server.fastmcp import FastMCP
from arcgis_pro_mcp.stdio_runtime import run_stdio

mcp = FastMCP("native-noise-test")

@mcp.tool()
def noisy(close_input: bool = False) -> dict:
    os.write(1, b"\xff\xfe native diagnostic\n")
    print("Python diagnostic", flush=True)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        written = wintypes.DWORD()
        data = ctypes.create_string_buffer(b"Win32 diagnostic\n")
        assert kernel32.WriteFile(kernel32.GetStdHandle(-11 & 0xFFFFFFFF), data, len(data.value), ctypes.byref(written), None)
    if close_input:
        sys.stdin.close()
    return {"ok": True, "text": "Unicode response: \u6d4b\u8bd5"}

run_stdio(mcp)
'''


class StdioIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_stdout_and_closed_stdin_do_not_break_rpc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            server_path = folder / "server.py"
            server_path.write_text(_SERVER, encoding="utf-8")
            env = dict(os.environ)
            root = str(Path(__file__).resolve().parents[1])
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (root, env.get("PYTHONPATH"))))
            params = StdioServerParameters(command=sys.executable, args=[str(server_path)], env=env)
            log_path = folder / "stderr.log"
            with log_path.open("wb") as log:
                async with stdio_client(params, errlog=log) as streams:
                    async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=20)) as session:
                        await session.initialize()
                        for close_input in (True, False):
                            result = await session.call_tool("noisy", {"close_input": close_input})
                            self.assertFalse(result.isError, result)
                            payload = result.structuredContent or json.loads(result.content[0].text)
                            self.assertEqual(payload, {"ok": True, "text": "Unicode response: 测试"})
                        names = [tool.name for tool in (await session.list_tools()).tools]
                        self.assertEqual(names, ["noisy"])
            diagnostics = log_path.read_bytes()
            self.assertIn(b"\xff\xfe native diagnostic", diagnostics)
            self.assertIn(b"Python diagnostic", diagnostics)
            if os.name == "nt":
                self.assertIn(b"Win32 diagnostic", diagnostics)


if __name__ == "__main__":
    unittest.main()
