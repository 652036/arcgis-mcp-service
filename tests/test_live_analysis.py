from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import live_analysis


class _FakeManagement:
    @staticmethod
    def CalculateField(**kwargs: object) -> object:
        raise AssertionError(f"CalculateField must not run: {kwargs}")


class _FakeArcpy:
    management = _FakeManagement()


class LiveAnalysisSecurityTests(unittest.TestCase):
    def test_calculate_field_is_not_allowlisted_and_is_explicitly_denied(self) -> None:
        self.assertNotIn("management.CalculateField", live_analysis.CURRENT_NAMED_GP_TOOLS)
        with self.assertRaisesRegex(RuntimeError, "永久拒绝"):
            live_analysis._tool_callable(_FakeArcpy(), "management.CalculateField")
        self.assertNotIn("management.RepairGeometry", live_analysis.CURRENT_NAMED_GP_TOOLS)
        with self.assertRaisesRegex(RuntimeError, "永久拒绝"):
            live_analysis._tool_callable(_FakeArcpy(), "management.RepairGeometry")

    def test_current_analysis_cannot_execute_calculate_field(self) -> None:
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "字段表达式可能执行代码"):
                live_analysis.run_current_analysis(
                    _FakeArcpy(),
                    object(),
                    "management.CalculateField",
                    {"in_table": {"layer": "roads"}, "expression": "__import__('os')"},
                )

    def test_current_analysis_disables_overwrite_and_requires_new_exact_output(self) -> None:
        class Arcpy:
            def __init__(self) -> None:
                self.created: set[str] = set()
                self.existing: set[str] = set()
                self.calls: list[dict[str, object]] = []
                self.env_calls: list[dict[str, object]] = []
                self.analysis = SimpleNamespace(Buffer=self.buffer)
                self.management = SimpleNamespace(
                    GetCount=lambda _path: SimpleNamespace(getOutput=lambda _index: "1")
                )

            def buffer(self, **kwargs: object) -> object:
                self.calls.append(kwargs)
                self.created.add(str(kwargs["out_feature_class"]))
                return SimpleNamespace(messageCount=0, outputCount=0)

            def Exists(self, path: object) -> bool:
                return str(path) in self.existing | self.created

            @contextmanager
            def EnvManager(self, **kwargs: object):
                self.env_calls.append(kwargs)
                yield

        class Map:
            name = "Current"

            def __init__(self) -> None:
                self.added: list[str] = []

            @staticmethod
            def listLayers() -> list[SimpleNamespace]:
                return [SimpleNamespace(name="Roads", longName="Roads", URI="layer://roads")]

            @staticmethod
            def listTables() -> list[object]:
                return []

            def addDataFromPath(self, path: str) -> None:
                self.added.append(path)

        arcpy = Arcpy()
        map_obj = Map()
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
            },
            clear=True,
        ):
            output = os.path.normpath(str(Path(root) / "buffer.shp"))
            result = live_analysis.run_current_analysis(
                arcpy,
                map_obj,
                "analysis.Buffer",
                {"in_features": {"layer": "Roads"}, "out_feature_class": output},
            )
            self.assertEqual(result["outputs"][0]["value"], output)
            self.assertEqual(arcpy.env_calls, [{"overwriteOutput": False}])
            arcpy.existing.add(os.path.normpath(str(Path(root) / "existing.shp")))
            with self.assertRaisesRegex(RuntimeError, "拒绝覆盖已有输出"):
                live_analysis.run_current_analysis(
                    arcpy,
                    map_obj,
                    "analysis.Buffer",
                    {
                        "in_features": {"layer": "Roads"},
                        "out_feature_class": str(Path(root) / "existing.shp"),
                    },
                )


if __name__ == "__main__":
    unittest.main()
