from __future__ import annotations

import unittest

from arcgis_pro_mcp import server
from arcgis_pro_mcp.gp_allowlist import list_registered_gp_tools


class GpAllowlistContractTests(unittest.TestCase):
    def test_list_registered_gp_tools_matches_server_registration_exactly(self) -> None:
        catalog = list_registered_gp_tools()
        self.assertTrue(catalog)

        listed_names: list[str] = []
        for entry in catalog:
            name = entry.get("name")
            role = entry.get("role")
            self.assertIsInstance(name, str)
            self.assertTrue(name.startswith("arcgis_pro_gp_"), msg=f"unexpected name: {name!r}")
            self.assertIsInstance(role, str)
            self.assertTrue(role.strip(), msg=f"empty role for {name!r}")
            listed_names.append(name)

        duplicates = sorted(name for name in set(listed_names) if listed_names.count(name) > 1)
        self.assertEqual(duplicates, [], msg=f"duplicate entries in gp_allowlist: {duplicates}")

        registered_gp_names = {
            name
            for name in server.mcp._tool_manager._tools
            if name.startswith("arcgis_pro_gp_")
        }
        listed_set = set(listed_names)

        missing = sorted(registered_gp_names - listed_set)
        extra = sorted(listed_set - registered_gp_names)
        self.assertEqual(
            missing,
            [],
            msg=f"gp_allowlist.list_registered_gp_tools() is missing registered GP tools: {missing}",
        )
        self.assertEqual(
            extra,
            [],
            msg=f"gp_allowlist.list_registered_gp_tools() has unregistered GP tools: {extra}",
        )
        self.assertEqual(listed_set, registered_gp_names)


if __name__ == "__main__":
    unittest.main()
