from __future__ import annotations

import unittest

from arcgis_pro_mcp.arcade import validate_safe_arcade_expression


class SafeArcadeTests(unittest.TestCase):
    def test_accepts_field_literals_operators_and_pure_functions(self) -> None:
        expression = "IIf(IsEmpty($feature.NAME), 'Unknown', Upper($feature.NAME))"
        self.assertEqual(validate_safe_arcade_expression(expression), expression)
        self.assertEqual(validate_safe_arcade_expression('$feature["道路名称"]'), '$feature["道路名称"]')

    def test_rejects_remote_dynamic_and_statement_forms(self) -> None:
        blocked = (
            "FeatureSetByPortalItem(Portal('https://example.test'), 'id')",
            "FeatureSetByPortalItem/* bypass */('id')",
            "Evaluate('FeatureSetByPortalItem(...)')",
            "var x = 1; return x",
            "$map.VALUE",
        )
        for expression in blocked:
            with self.subTest(expression=expression):
                with self.assertRaises(RuntimeError):
                    validate_safe_arcade_expression(expression)


if __name__ == "__main__":
    unittest.main()
