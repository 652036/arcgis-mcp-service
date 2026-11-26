"""Catalog 里双击即可把 MCP 接到当前工程。"""
from __future__ import annotations

import sys
from pathlib import Path


class Toolbox:
    def __init__(self) -> None:
        self.label = "ArcGIS Pro MCP"
        self.alias = "arcgispromcp"
        self.tools = [AttachWindow]


class AttachWindow:
    def __init__(self) -> None:
        self.label = "接入当前窗口"
        self.description = (
            "把 MCP 接到本窗口的工程；可在多个 Pro 窗口分别运行，自动分配端口。"
            "运行期间前台工具保持忙碌；取消即断开。"
        )
        self.canRunInBackground = False

    def getParameterInfo(self) -> list:
        return []

    def isLicensed(self) -> bool:
        return True

    def updateParameters(self, parameters) -> None:
        return

    def updateMessages(self, parameters) -> None:
        return

    def execute(self, parameters, messages) -> None:
        root = Path(__file__).resolve().parent
        root_text = str(root)
        if root_text in sys.path:
            sys.path.remove(root_text)
        sys.path.insert(0, root_text)
        import importlib

        import arcgis_pro_mcp_bootstrap as bootstrap

        bootstrap = importlib.reload(bootstrap)
        expected_bootstrap = (root / "arcgis_pro_mcp_bootstrap.py").resolve()
        actual_bootstrap = Path(getattr(bootstrap, "__file__", "")).resolve()
        if actual_bootstrap != expected_bootstrap:
            raise RuntimeError(
                f"启动器未从当前仓库加载：{actual_bootstrap}；"
                f"期望：{expected_bootstrap}"
            )
        messages.addMessage(f"ArcGIS Pro MCP 仓库：{root}")
        messages.addMessage("正在接入本窗口；MCP通过窗口选择或CURRENT:进程号区分多个窗口。")
        messages.addMessage("该桥接占用前台执行线程，请保持工具运行；取消即断开。")
        bootstrap.run_host(root, messages.addMessage)
