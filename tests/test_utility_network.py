from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import utility_network


class Result:
    def __init__(self, outputs=None):
        self.outputs = outputs or []

    def getOutput(self, index):
        return self.outputs[index]

    def getMessages(self):
        return "done"


class UnTools:
    def __init__(self):
        self.calls = []

    def ValidateNetworkTopology(self, *args):
        self.calls.append(("validate", args))
        return Result([args[0], '{"dirty": []}'])

    def Trace(self, *args, **kwargs):
        self.calls.append(("trace", args, kwargs))
        return Result()

    def UpdateSubnetwork(self, *args):
        self.calls.append(("update", args))
        return Result()

    def ExportSubnetwork(self, *args, **kwargs):
        self.calls.append(("export", args, kwargs))
        with open(args[5], "wb") as stream:
            stream.write(b"{}")
        return Result()


class FakeArcpy:
    def __init__(self):
        self.un = UnTools()
        self.Extent = lambda *values: tuple(values)

    @staticmethod
    def Describe(_network):
        tier = SimpleNamespace(
            name="Medium Voltage",
            rank=1,
            topologyType="RADIAL",
            manageSubnetwork=SimpleNamespace(isDirty=True),
        )
        domain = SimpleNamespace(
            domainNetworkName="ElectricDistribution",
            tierDefinition="HIERARCHICAL",
            tiers=[tier],
        )
        return SimpleNamespace(
            dataType="UtilityNetwork",
            utilityNetworkVersion=7,
            schemaGeneration=7,
            networkTopologyEnabled=True,
            domainNetworks=[domain],
        )


class UtilityNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.network = os.path.join(self.temp.name, "network.gdb", "UtilityNetwork")
        self.arcpy = FakeArcpy()
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
                "ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp.name,
                "ARCGIS_PRO_MCP_EXPORT_ROOT": self.temp.name,
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_describe_lists_domain_networks_and_tiers(self):
        result = utility_network.describe_utility_network(self.arcpy, self.network)
        self.assertEqual(result["utility_network_version"], 7)
        self.assertEqual(result["domain_networks"][0]["tiers"][0]["name"], "Medium Voltage")

    def test_validate_requires_exact_network_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, "精确回显"):
            utility_network.validate_network_topology(
                self.arcpy, self.network, expected_network="wrong"
            )
        result = utility_network.validate_network_topology(
            self.arcpy,
            self.network,
            expected_network=os.path.normpath(self.network),
            extent=[0, 0, 10, 10],
        )
        self.assertEqual(result["discovered_subnetworks"], {"dirty": []})

    def test_named_trace_uses_only_named_configuration_contract(self):
        starts = os.path.join(self.temp.name, "starts.gdb", "points")
        result = utility_network.trace_named_configuration(
            self.arcpy,
            self.network,
            "DOWNSTREAM",
            "Approved downstream",
            starting_points=starts,
        )
        self.assertEqual(result["trace_config_name"], "Approved downstream")
        kwargs = self.arcpy.un.calls[-1][2]
        self.assertEqual(kwargs["use_trace_config"], "USE_TRACE_CONFIGURATION")
        self.assertEqual(kwargs["trace_config_name"], "Approved downstream")

    def test_all_subnetworks_needs_fixed_confirmation_phrase(self):
        with self.assertRaisesRegex(RuntimeError, "UPDATE_ALL"):
            utility_network.update_subnetwork(
                self.arcpy,
                self.network,
                "ElectricDistribution",
                "Medium Voltage",
                all_subnetworks=True,
                expected_network=os.path.normpath(self.network),
            )
        result = utility_network.update_subnetwork(
            self.arcpy,
            self.network,
            "ElectricDistribution",
            "Medium Voltage",
            all_subnetworks=True,
            expected_network=os.path.normpath(self.network),
            confirm_all="UPDATE_ALL_SUBNETWORKS_IN_TIER",
        )
        self.assertEqual(result["mode"], "ALL_SUBNETWORKS_IN_TIER")

    def test_export_never_acknowledges_and_verifies_json(self):
        output = os.path.join(self.temp.name, "exports", "subnetwork.json")
        result = utility_network.export_subnetwork(
            self.arcpy,
            self.network,
            "ElectricDistribution",
            "Medium Voltage",
            "RMT001",
            output,
        )
        self.assertFalse(result["acknowledged"])
        args = self.arcpy.un.calls[-1][1]
        self.assertEqual(args[4], "NO_ACKNOWLEDGE")


if __name__ == "__main__":
    unittest.main()
