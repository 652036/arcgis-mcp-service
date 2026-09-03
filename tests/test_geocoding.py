from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import geocoding


class Result:
    def getMessages(self):
        return "geocoded"

    def getOutput(self, _index):
        return "2"


class GeocodingTools:
    def __init__(self, arcpy):
        self.arcpy = arcpy
        self.calls = []

    def Locator(self, path):
        self.calls.append(("locator", path))
        return SimpleNamespace(
            capabilities="Geocode,ReverseGeocode",
            compatibilityVersion="3.4",
            countryCode="CHN",
            languageCode="zh",
            locatorType="PointAddress",
            precisionType="GLOBAL_HIGH",
            role="PointAddress",
            multilineAddressFields=[SimpleNamespace(name="Address")],
            outputFields=[SimpleNamespace(name="Score")],
            spatialReference=SimpleNamespace(name="WGS 1984", factoryCode=4326),
        )

    def GeocodeAddresses(self, *args):
        self.calls.append(("geocode", args))
        self.arcpy.created.add(args[3])
        return Result()

    def ReverseGeocode(self, *args):
        self.calls.append(("reverse", args))
        self.arcpy.created.add(args[2])
        return Result()


class FakeArcpy:
    def __init__(self):
        self.created = set()
        self.geocoding = GeocodingTools(self)
        self.management = SimpleNamespace(GetCount=lambda _path: Result())

    def Exists(self, path):
        return path in self.created

    @staticmethod
    def ListFields(_table):
        return [SimpleNamespace(name="Address"), SimpleNamespace(name="Country")]


class GeocodingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.table = os.path.join(self.temp.name, "input.gdb", "addresses")
        self.points = os.path.join(self.temp.name, "input.gdb", "points")
        self.locator = os.path.join(self.temp.name, "locator.loc")
        self.arcpy = FakeArcpy()
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp.name,
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": self.temp.name,
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_locator_info_is_typed(self):
        result = geocoding.locator_info(self.arcpy, self.locator)
        self.assertEqual(result["country_code"], "CHN")
        self.assertEqual(result["multiline_address_fields"], ["Address"])
        self.assertEqual(result["spatial_reference"]["wkid"], 4326)

    def test_geocode_uses_typed_field_mapping_and_verifies_output(self):
        output = os.path.join(self.temp.name, "out.gdb", "matches")
        result = geocoding.geocode_addresses(
            self.arcpy,
            self.table,
            self.locator,
            [{"locator_field": "Single Line Input", "table_field": "address"}],
            output,
            country_codes=["CHN"],
        )
        self.assertTrue(result["output"]["verified"])
        args = self.arcpy.geocoding.calls[-1][1]
        self.assertEqual(args[2], [["Single Line Input", "Address"]])
        self.assertEqual(args[4], "STATIC")

    def test_unknown_input_field_is_rejected_before_gp(self):
        output = os.path.join(self.temp.name, "out.gdb", "matches")
        with self.assertRaisesRegex(RuntimeError, "未找到表字段"):
            geocoding.geocode_addresses(
                self.arcpy,
                self.table,
                self.locator,
                [{"locator_field": "SingleLine", "table_field": "Missing"}],
                output,
            )

    def test_reverse_geocode_restricts_feature_types(self):
        output = os.path.join(self.temp.name, "out.gdb", "reverse")
        with self.assertRaisesRegex(RuntimeError, "不受支持"):
            geocoding.reverse_geocode(
                self.arcpy,
                self.points,
                self.locator,
                output,
                feature_types=["ANYTHING"],
            )
        result = geocoding.reverse_geocode(
            self.arcpy,
            self.points,
            self.locator,
            output,
            feature_types=["POINT_ADDRESS"],
        )
        self.assertTrue(result["output"]["verified"])

    def test_remote_locator_is_rejected_before_credit_consumption(self):
        with self.assertRaises(RuntimeError):
            geocoding.locator_info(
                self.arcpy,
                "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer",
            )


if __name__ == "__main__":
    unittest.main()
