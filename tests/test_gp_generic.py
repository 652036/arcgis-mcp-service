from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import gp_generic


class _FakeResult:
    messageCount = 1

    def getMessage(self, index: int) -> str:
        return f"message-{index}"


class _FakeManagement:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def CopyFeatures(self, **kwargs: object) -> _FakeResult:
        self.calls.append(kwargs)
        return _FakeResult()

    def Buffer(self, **kwargs: object) -> _FakeResult:
        self.calls.append(kwargs)
        return _FakeResult()


class _FakeArcpy:
    def __init__(self) -> None:
        self.management = _FakeManagement()


class GenericGPTests(unittest.TestCase):
    def test_generic_gp_is_disabled_by_default(self) -> None:
        arcpy = _FakeArcpy()
        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "通用 GP 已禁用"):
                gp_generic.run_tool(arcpy, "management.CopyFeatures", {})

    def test_generic_gp_requires_allowlist(self) -> None:
        arcpy = _FakeArcpy()
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "未配置 ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST"):
                gp_generic.run_tool(arcpy, "management.CopyFeatures", {})

    def test_generic_gp_validates_paths_for_allowlisted_tool(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            in_features = str(Path(input_root) / "roads.shp")
            out_features = str(Path(output_root) / "buffered.gdb" / "roads")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
                    "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST": "management.CopyFeatures",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                message = gp_generic.run_tool(
                    arcpy,
                    "management.CopyFeatures",
                    {
                        "in_features": in_features,
                        "out_feature_class": out_features,
                    },
                )
        self.assertEqual(message, "message-0")
        self.assertEqual(arcpy.management.calls[0]["in_features"], os.path.normpath(in_features))
        self.assertEqual(arcpy.management.calls[0]["out_feature_class"], os.path.normpath(out_features))

    def test_generic_gp_rejects_inline_secret_parameters(self) -> None:
        arcpy = _FakeArcpy()
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
                "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST": "management.Buffer",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "不允许内联敏感字符串参数"):
                gp_generic.run_tool(
                    arcpy,
                    "management.Buffer",
                    {"password": "secret"},
                )

