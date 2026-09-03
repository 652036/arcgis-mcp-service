"""在 ArcGIS Pro 的 Python 窗口运行本文件，把 MCP 接到当前工程。"""
from __future__ import annotations

import sys
from pathlib import Path


def _prefer_replace_errors_on_stdio() -> None:
    """Keep Chinese status lines from crashing on legacy Windows consoles."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            continue


def _safe_print(message: str) -> None:
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
        buffer.write(payload)
        buffer.flush()


def _repo_root() -> Path:
    candidates: list[Path] = []
    if "__file__" in globals():
        candidates.append(Path(__file__).resolve().parent)
    candidates.append(Path.cwd())
    candidates.extend(Path(p) for p in sys.path if p)
    for root in candidates:
        try:
            if (root / "arcgis_pro_mcp" / "pro_host.py").is_file():
                return root.resolve()
        except OSError:
            continue
    raise RuntimeError(
        "找不到 arcgis_pro_mcp/pro_host.py。请在 Pro 的 Python 窗口粘贴下面一行，"
        "并把占位符替换为仓库绝对路径：\n"
        r'import runpy; runpy.run_path(r"<仓库绝对路径>\接入当前窗口.py")'
    )


ROOT = _repo_root()
ROOT_TEXT = str(ROOT)
if ROOT_TEXT in sys.path:
    sys.path.remove(ROOT_TEXT)
sys.path.insert(0, ROOT_TEXT)

import importlib

import arcgis_pro_mcp_bootstrap as bootstrap

bootstrap = importlib.reload(bootstrap)
EXPECTED_BOOTSTRAP = (ROOT / "arcgis_pro_mcp_bootstrap.py").resolve()
ACTUAL_BOOTSTRAP = Path(getattr(bootstrap, "__file__", "")).resolve()
if ACTUAL_BOOTSTRAP != EXPECTED_BOOTSTRAP:
    raise RuntimeError(
        f"启动器未从当前仓库加载：{ACTUAL_BOOTSTRAP}；期望：{EXPECTED_BOOTSTRAP}"
    )

_prefer_replace_errors_on_stdio()
_safe_print(f"ArcGIS Pro MCP 仓库：{ROOT}")
_safe_print(f"Python：{sys.executable}")
bootstrap.run_host(ROOT, emit=_safe_print)
