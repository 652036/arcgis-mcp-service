# -*- coding: utf-8 -*-
"""Catalog 里双击即可把 MCP 接到当前工程。"""
from __future__ import annotations

import sys
from pathlib import Path

import arcpy


class Toolbox(object):
    def __init__(self) -> None:
        self.label = "ArcGIS Pro MCP"
        self.alias = "arcgispromcp"
        self.tools = [AttachWindow]


class AttachWindow(object):
    def __init__(self) -> None:
        self.label = "接入当前窗口"
        self.description = (
            "把 MCP 接到当前打开的工程并独占式处理 CURRENT 调用。"
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
        messages.addMessage("正在接入当前窗口；仅 aprx_path=CURRENT 的调用进入本窗口。")
        messages.addMessage("该桥接占用前台执行线程，请保持工具运行；取消即断开。")
        bootstrap.run_host(root, messages.addMessage)
