from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import gp_generic


class _FakeResult:
    messageCount = 1

    def getMessage(self, index: int) -> str:
        return f"message-{index}; token=must-not-leak"


class _FakeManagement:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def CopyFeatures(self, **kwargs: object) -> _FakeResult:
        self.calls.append(kwargs)
        return _FakeResult()

    def Buffer(self, **kwargs: object) -> _FakeResult:
        self.calls.append(kwargs)
        return _FakeResult()

    def BuildPyramids(self, **kwargs: object) -> _FakeResult:
        self.calls.append(kwargs)
        return _FakeResult()


class _FakeArcpy:
    def __init__(self) -> None:
        self.management = _FakeManagement()
        self.env_calls: list[dict[str, object]] = []
        self.existing: set[str] = set()

    def Exists(self, path: str) -> bool:
        return path in self.existing

    @contextmanager
    def EnvManager(self, **kwargs: object):
        self.env_calls.append(kwargs)
        yield


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
        self.assertEqual(message, "message-0; token=[REDACTED]")
        self.assertEqual(arcpy.management.calls[0]["in_features"], os.path.normpath(in_features))
        self.assertEqual(arcpy.management.calls[0]["out_feature_class"], os.path.normpath(out_features))
        self.assertEqual(arcpy.env_calls, [{"overwriteOutput": False}])

    def test_generic_gp_refuses_existing_or_ambiguous_outputs(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as output_root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
                "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST": "management.CopyFeatures",
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
            },
            clear=True,
        ):
            output = os.path.normpath(str(Path(output_root) / "copy.shp"))
            arcpy.existing.add(output)
            with self.assertRaisesRegex(RuntimeError, "拒绝覆盖已有输出"):
                gp_generic.run_tool(
                    arcpy,
                    "management.CopyFeatures",
                    {"in_features": output, "out_feature_class": output},
                )
            with self.assertRaisesRegex(RuntimeError, "输出容器与名称分离"):
                gp_generic.run_tool(
                    arcpy,
                    "management.CopyFeatures",
                    {"out_path": output_root, "out_name": "copy"},
                )
        self.assertEqual(arcpy.management.calls, [])

    def test_generic_gp_rejects_inline_secret_parameters(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as output_root:
            output = str(Path(output_root) / "buffered.shp")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
                    "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST": "management.Buffer",
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "不允许内联敏感字符串参数"):
                    gp_generic.run_tool(
                        arcpy,
                        "management.Buffer",
                        {"password": "secret", "out_feature_class": output},
                    )

    def test_allowlist_cannot_enable_destructive_or_code_execution_tools(self) -> None:
        arcpy = _FakeArcpy()
        blocked = [
            "management.Delete",
            "DeleteRows_management",
            "management.TruncateTable",
            "management.CalculateField",
            "CalculateValue_management",
            "custom.RunScript",
        ]
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
                "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST": ",".join(blocked),
            },
            clear=True,
        ):
            for tool_name in blocked:
                with self.subTest(tool_name=tool_name):
                    with self.assertRaisesRegex(RuntimeError, "allowlist 不能覆盖"):
                        gp_generic.run_tool(arcpy, tool_name, {})

    def test_generic_gp_rejects_in_place_tool_even_when_allowlisted(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            raster = str(Path(input_root) / "surface.tif")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
                    "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST": "management.BuildPyramids",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "就地修改或无输出"):
                    gp_generic.run_tool(
                        arcpy,
                        "management.BuildPyramids",
                        {"in_raster_dataset": raster},
                    )
        self.assertEqual(arcpy.management.calls, [])

    def test_generic_gp_requires_configured_output_root(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as output_root:
            output = str(Path(output_root) / "copied.shp")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
                    "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST": "management.CopyFeatures",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT"):
                    gp_generic.run_tool(
                        arcpy,
                        "management.CopyFeatures",
                        {"in_features": output, "out_feature_class": output},
                    )
        self.assertEqual(arcpy.management.calls, [])

