from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import gp_analysis


class RepairGeometrySecurityTests(unittest.TestCase):
    def test_repair_geometry_always_keeps_null_geometry_rows(self) -> None:
        repair = MagicMock()
        arcpy = SimpleNamespace(management=SimpleNamespace(RepairGeometry=repair))
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
            },
            clear=True,
        ):
            dataset = str(Path(root) / "data.gdb" / "roads")
            gp_analysis.run_repair_geometry(arcpy, dataset)
        repair.assert_called_once_with(os.path.normpath(dataset), "KEEP_NULL")


if __name__ == "__main__":
    unittest.main()
