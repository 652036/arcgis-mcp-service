from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import gp_stats


class GenerateTessellationTests(unittest.TestCase):
    def test_uses_positional_spatial_reference_for_arcpy_36(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root, "grid.gdb", "Cells")
            generate = MagicMock()
            spatial_reference = object()
            arcpy = SimpleNamespace(
                management=SimpleNamespace(GenerateTessellation=generate),
                SpatialReference=MagicMock(return_value=spatial_reference),
            )
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
                },
                clear=True,
            ):
                result = gp_stats.run_generate_tessellation(
                    arcpy,
                    str(output),
                    "0 0 10 10",
                    spatial_reference_wkid=3857,
                )

        self.assertEqual(result, os.path.normpath(str(output)))
        generate.assert_called_once_with(
            os.path.normpath(str(output)),
            "0 0 10 10",
            "HEXAGON",
            "",
            spatial_reference,
        )


if __name__ == "__main__":
    unittest.main()
