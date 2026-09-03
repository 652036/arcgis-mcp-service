from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import server


class _Result:
    def __init__(self, value: str = "2") -> None:
        self.value = value

    def getOutput(self, _index: int) -> str:
        return self.value

    @staticmethod
    def getMessages() -> str:
        return "completed; token=must-not-leak"


class _Management:
    def __init__(self) -> None:
        self.calculate_field_calls: list[tuple[object, ...]] = []
        self.calculate_geometry_calls: list[tuple[object, ...]] = []

    @staticmethod
    def GetCount(_path: str) -> _Result:
        return _Result()

    def CalculateField(self, *args: object) -> _Result:
        self.calculate_field_calls.append(args)
        return _Result()

    def CalculateGeometryAttributes(self, *args: object, **kwargs: object) -> _Result:
        self.calculate_geometry_calls.append((*args, kwargs))
        return _Result()


class _Arcpy:
    def __init__(self) -> None:
        self.management = _Management()

    @staticmethod
    def ListFields(_path: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(name="OBJECTID"),
            SimpleNamespace(name="VALUE"),
            SimpleNamespace(name="AREA_M2"),
        ]


class FieldCalculationSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.table = str(Path(self.temp.name) / "data.gdb" / "features")
        self.arcpy = _Arcpy()
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp.name,
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_calculate_field_is_arcade_only_count_bound_and_redacted(self) -> None:
        with patch.object(server, "_arcpy", return_value=self.arcpy):
            payload = json.loads(
                server.arcgis_pro_gp_calculate_field(
                    self.table,
                    "value",
                    "$feature.VALUE + 1",
                    2,
                    self.table,
                )
            )
        self.assertEqual(payload["rows_targeted"], 2)
        self.assertNotIn("must-not-leak", repr(payload))
        self.assertEqual(
            self.arcpy.management.calculate_field_calls,
            [(os.path.normpath(self.table), "VALUE", "$feature.VALUE + 1", "ARCADE")],
        )

    def test_calculate_field_rejects_code_and_remote_arcade(self) -> None:
        with patch.object(server, "_arcpy", return_value=self.arcpy):
            with self.assertRaisesRegex(RuntimeError, "仅允许 ARCADE"):
                server.arcgis_pro_gp_calculate_field(
                    self.table,
                    "VALUE",
                    "1",
                    2,
                    self.table,
                    expression_type="PYTHON3",
                )
            with self.assertRaises(RuntimeError):
                server.arcgis_pro_gp_calculate_field(
                    self.table,
                    "VALUE",
                    "FeatureSetByPortalItem/*x*/('id')",
                    2,
                    self.table,
                )
        self.assertEqual(self.arcpy.management.calculate_field_calls, [])

    def test_calculate_geometry_requires_exact_target_mapping_and_count(self) -> None:
        mapping = [["AREA_M2", "AREA"]]
        with patch.object(server, "_arcpy", return_value=self.arcpy):
            payload = json.loads(
                server.arcgis_pro_gp_calculate_geometry(
                    self.table,
                    mapping,
                    2,
                    self.table,
                    mapping,
                    area_unit="SQUARE_METERS",
                )
            )
            with self.assertRaisesRegex(RuntimeError, "confirm_geometry_property"):
                server.arcgis_pro_gp_calculate_geometry(
                    self.table,
                    mapping,
                    2,
                    self.table,
                    [["AREA_M2", "LENGTH"]],
                )
        self.assertEqual(payload["geometry_property"], [["AREA_M2", "AREA_GEODESIC"]])
        self.assertEqual(payload["rows_targeted"], 2)
        self.assertEqual(len(self.arcpy.management.calculate_geometry_calls), 1)


if __name__ == "__main__":
    unittest.main()
