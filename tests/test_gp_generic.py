from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        self.CopyFeatures_management = self.management.CopyFeatures
        self.Buffer_analysis = self.management.Buffer
        self.BuildPyramids_management = self.management.BuildPyramids
        self.env_calls: list[dict[str, object]] = []
        self.existing: set[str] = set()

    def ListTools(self) -> list[str]:
        return ["CopyFeatures_management", "Buffer_analysis", "BuildPyramids_management"]

    def Exists(self, path: str) -> bool:
        return path in self.existing

    @contextmanager
    def EnvManager(self, **kwargs: object):
        self.env_calls.append(kwargs)
        yield


class GenericGPTests(unittest.TestCase):
    def test_generic_gp_can_be_explicitly_disabled(self) -> None:
        arcpy = _FakeArcpy()
        for value in ("0", "false", "NO", "off", "", "invalid"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": value}, clear=True
            ):
                with self.assertRaisesRegex(RuntimeError, "通用 GP 已禁用"):
                    gp_generic.run_tool(arcpy, "management.CopyFeatures", {})
        self.assertEqual(arcpy.management.calls, [])

    def test_generic_gp_is_enabled_by_default_without_allowlist(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root}, clear=True
        ):
            self.assertTrue(gp_generic.generic_gp_enabled())
            for name in ("management.CopyFeatures", "CopyFeatures_management", "analysis.buffer"):
                gp_generic.run_tool(arcpy, name, {"out_feature_class": str(Path(root) / "result.shp")})
        self.assertEqual(len(arcpy.management.calls), 3)

    def test_legacy_allowlist_does_not_restrict_native_tool_names(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
            "ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST": "unrelated.Tool",
        }, clear=True):
            gp_generic.run_tool(arcpy, "management.CopyFeatures", {
                "out_feature_class": str(Path(root) / "result.shp"),
            })
            self.assertEqual(gp_generic.generic_gp_allowlist(), [])
        self.assertEqual(len(arcpy.management.calls), 1)

    def test_only_registered_gp_tools_can_be_called(self) -> None:
        arcpy = _FakeArcpy()
        arcpy.management.Unregistered = MagicMock()
        arcpy.GetInstallInfo = MagicMock()
        for name in ("management.Unregistered", "GetInstallInfo", "os.system", "missing.Tool"):
            with self.subTest(name=name), patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "未找到已注册的原生 GP 工具"):
                    gp_generic.run_tool(arcpy, name, {})
        arcpy.management.Unregistered.assert_not_called()
        arcpy.GetInstallInfo.assert_not_called()

    def test_missing_catalog_and_noncallable_tool_fail_before_execution(self) -> None:
        arcpy = _FakeArcpy()
        with patch.dict(os.environ, {}, clear=True), patch.object(arcpy, "ListTools", None):
            with self.assertRaisesRegex(RuntimeError, "缺少 ListTools"):
                gp_generic.run_tool(arcpy, "management.CopyFeatures", {})
        with patch.dict(os.environ, {}, clear=True), patch.object(arcpy, "CopyFeatures_management", None):
            with self.assertRaisesRegex(RuntimeError, "不可调用"):
                gp_generic.run_tool(arcpy, "management.CopyFeatures", {})
        self.assertEqual(arcpy.management.calls, [])

    def test_generic_gp_still_respects_read_only_deployment(self) -> None:
        arcpy = _FakeArcpy()
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "0"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ARCGIS_PRO_MCP_ALLOW_WRITE"):
                gp_generic.run_tool(arcpy, "management.CopyFeatures", {})
        self.assertEqual(arcpy.management.calls, [])

    def test_generic_gp_validates_paths_for_native_tool(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            in_features = str(Path(input_root) / "roads.shp")
            out_features = str(Path(output_root) / "buffered.gdb" / "roads")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
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
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "不允许内联敏感字符串参数"):
                    gp_generic.run_tool(
                        arcpy,
                        "analysis.Buffer",
                        {"password": "secret", "out_feature_class": output},
                    )

    def test_generic_gp_rejects_destructive_or_code_execution_tools(self) -> None:
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
            },
            clear=True,
        ):
            for tool_name in blocked:
                with self.subTest(tool_name=tool_name):
                    with self.assertRaisesRegex(RuntimeError, "永久拒绝"):
                        gp_generic.run_tool(arcpy, tool_name, {})

    def test_generic_gp_rejects_in_place_tool(self) -> None:
        arcpy = _FakeArcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            raster = str(Path(input_root) / "surface.tif")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "1",
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
