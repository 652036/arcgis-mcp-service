"""Keep native diagnostics and standard-stream changes away from MCP traffic."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Any

import anyio
from mcp.server.stdio import stdio_server


def _update_windows_standard_handles() -> None:
    if os.name != "nt":
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetStdHandle.restype = wintypes.BOOL
    for identifier, descriptor in ((-10, 0), (-11, 1)):
        if not kernel32.SetStdHandle(identifier & 0xFFFFFFFF, msvcrt.get_osfhandle(descriptor)):
            raise OSError(ctypes.get_last_error(), "Could not isolate native standard handles")


@contextmanager
def isolated_stdio():
    """Duplicate protocol pipes before redirecting process-level standard I/O.

    ArcPy tools can write non-UTF-8 diagnostics via C/.NET stdout or close the
    Python stdin object. Neither operation may affect the JSON-RPC transport.
    This changes the process only while the standalone stdio server is running.
    """
    sys.stdout.flush()
    protocol_input = os.fdopen(os.dup(sys.stdin.fileno()), "r", encoding="utf-8", errors="replace")
    protocol_output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    previous_input, previous_output = sys.stdin, sys.stdout
    try:
        with open(os.devnull, "rb") as null_input:
            os.dup2(null_input.fileno(), 0)
        os.dup2(sys.stderr.fileno(), 1)
        _update_windows_standard_handles()
        yield anyio.wrap_file(protocol_input), anyio.wrap_file(protocol_output)
    finally:
        try:
            if not sys.stdout.closed:
                sys.stdout.flush()
        finally:
            os.dup2(protocol_input.fileno(), 0)
            os.dup2(protocol_output.fileno(), 1)
            _update_windows_standard_handles()
            sys.stdin = previous_input if not previous_input.closed else os.fdopen(os.dup(0), "r", encoding="utf-8")
            sys.stdout = previous_output if not previous_output.closed else os.fdopen(os.dup(1), "w", encoding="utf-8")
            protocol_input.close()
            protocol_output.close()


async def _serve(mcp: Any, stdin: Any, stdout: Any) -> None:
    async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
        await mcp._mcp_server.run(
            read_stream,
            write_stream,
            mcp._mcp_server.create_initialization_options(),
        )


def run_stdio(mcp: Any) -> None:
    with isolated_stdio() as (stdin, stdout):
        anyio.run(_serve, mcp, stdin, stdout)
