from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import gp_network


class NetworkGeoprocessingTests(unittest.TestCase):
    def test_add_locations_resolves_localized_na_sublayer(self) -> None:
        add_locations = MagicMock()
        arcpy = SimpleNamespace(
            na=SimpleNamespace(
                GetNAClassNames=MagicMock(return_value={"Stops": "停靠点"}),
                AddLocations=add_locations,
            )
        )
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
            },
            clear=True,
        ):
            input_table = Path(root, "stops.shp")
            input_table.touch()
            gp_network.run_add_locations(
                arcpy, "RouteLayer", "Stops", str(input_table)
            )

        add_locations.assert_called_once_with(
            "RouteLayer", "停靠点", os.path.normpath(str(input_table))
        )


if __name__ == "__main__":
    unittest.main()
