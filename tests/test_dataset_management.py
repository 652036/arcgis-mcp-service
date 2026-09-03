from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import dataset_management


class _Result:
    messageCount = 1

    def __init__(self, output: str = "") -> None:
        self.output = output

    def getMessage(self, index: int) -> str:
        return f"message-{index}"

    def getOutput(self, index: int) -> str:
        return self.output


class _Management:
    def __init__(self, count: int = 2) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.count = count

    def __getattr__(self, name: str):
        def call(*args: object, **kwargs: object) -> _Result:
            self.calls.append((name, args, kwargs))
            if name == "GetCount":
                return _Result(str(self.count))
            if name == "CreateTopology":
                return _Result(os.path.join(str(args[0]), str(args[1])))
            return _Result()

        return call


class _Arcpy:
    def __init__(self, *, exists: bool = True, lock: bool = True, count: int = 2) -> None:
        self._exists = exists
        self._lock = lock
        self.management = _Management(count)
        self.da = SimpleNamespace(
            ListSubtypes=lambda _path: {
                1: {
                    "Name": "Primary",
                    "Default": True,
                    "SubtypeField": "TYPE",
                    "FieldValues": {"STATUS": ("open", SimpleNamespace(name="StatusDomain"))},
                }
            },
            ListDomains=lambda _path: [
                SimpleNamespace(
                    name="StatusDomain",
                    description="Allowed status values",
                    type="String",
                    domainType="CodedValue",
                    owner="",
                    splitPolicy="DefaultValue",
                    mergePolicy="DefaultValue",
                    codedValues={"open": "Open", "closed": "Closed"},
                    range=None,
                ),
                SimpleNamespace(
                    name="HeightRange",
                    description="Valid heights",
                    type="Double",
                    domainType="Range",
                    owner="",
                    splitPolicy="Duplicate",
                    mergePolicy="DefaultValue",
                    codedValues=None,
                    range=(0.0, 100.0),
                ),
            ],
        )

    def Exists(self, _path: str) -> bool:
        return self._exists

    def TestSchemaLock(self, _path: str) -> bool:
        return self._lock

    def Describe(self, path: str) -> SimpleNamespace:
        return SimpleNamespace(
            dataType="FeatureClass",
            catalogPath=path,
            shapeType="Polygon",
            OIDFieldName="OBJECTID",
            isVersioned=True,
            editorTrackingEnabled=True,
            hasAttachments=True,
            relationshipClassNames=["Parcels__ATTACHREL"],
            spatialReference=SimpleNamespace(name="WGS 84", factoryCode=4326),
            name=Path(path).name,
            clusterTolerance=0.001,
            featureClassNames=["Parcels"],
        )

    def ListFields(self, _path: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                name="OBJECTID",
                aliasName="OBJECTID",
                type="OID",
                length=4,
                isNullable=False,
                required=True,
                editable=False,
                domain="",
                defaultValue=None,
            ),
            SimpleNamespace(
                name="STATUS",
                aliasName="Status",
                type="String",
                length=32,
                isNullable=True,
                required=False,
                editable=True,
                domain="StatusDomain",
                defaultValue="open",
            ),
        ]

    def ListIndexes(self, _path: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                name="PK_OBJECTID",
                isUnique=True,
                isAscending=True,
                fields=[SimpleNamespace(name="OBJECTID")],
            )
        ]


class DatasetManagementTests(unittest.TestCase):
    def test_dataset_schema_collects_fields_indexes_subtypes_and_relationships(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": root},
            clear=True,
        ):
            dataset = str(Path(root) / "data.gdb" / "Parcels")
            result = dataset_management.dataset_schema(arcpy, dataset)
        self.assertEqual(result["oid_field_name"], "OBJECTID")
        self.assertEqual(result["fields"][1]["domain"], "StatusDomain")
        self.assertEqual(result["indexes"][0]["fields"], ["OBJECTID"])
        self.assertEqual(result["subtypes"][0]["field_values"]["STATUS"]["domain"], "StatusDomain")
        self.assertEqual(result["relationship_class_names"], ["Parcels__ATTACHREL"])

    def test_verify_output_requires_existing_output_and_can_require_rows(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root},
            clear=True,
        ):
            output = str(Path(root) / "result.gdb" / "features")
            result = dataset_management.verify_output_dataset(
                _Arcpy(count=3),
                output,
                require_nonempty=True,
            )
            self.assertEqual(result["count"], 3)
            with self.assertRaisesRegex(RuntimeError, "输出不存在"):
                dataset_management.verify_output_dataset(_Arcpy(exists=False), output)
            with self.assertRaisesRegex(RuntimeError, "输出为空"):
                dataset_management.verify_output_dataset(
                    _Arcpy(count=0),
                    output,
                    require_nonempty=True,
                )

    def test_domain_write_requires_gate_and_schema_lock(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"ARCGIS_PRO_MCP_INPUT_ROOTS": root}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "写入类操作已禁用"):
                    dataset_management.run_create_domain(
                        _Arcpy(), root, "Status", "Status values", "TEXT"
                    )
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "方案锁"):
                    dataset_management.run_create_domain(
                        _Arcpy(lock=False), root, "Status", "Status values", "TEXT"
                    )

    def test_domain_and_subtype_helpers_call_named_management_tools(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
            },
            clear=True,
        ):
            dataset_management.run_create_domain(
                arcpy, root, "Status", "Status values", "TEXT"
            )
            dataset_management.run_add_coded_value_to_domain(
                arcpy, root, "Status", 1, "Open"
            )
            dataset = str(Path(root) / "data.gdb" / "Parcels")
            dataset_management.run_assign_domain_to_field(
                arcpy, dataset, "STATUS", "Status", 2
            )
            dataset_management.run_add_subtype(arcpy, dataset, 2, "Secondary")
        names = [name for name, _args, _kwargs in arcpy.management.calls]
        self.assertEqual(
            names,
            ["CreateDomain", "AddCodedValueToDomain", "AssignDomainToField", "AddSubtype"],
        )

    def test_domain_read_and_update_helpers_cover_coded_and_range_domains(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
            },
            clear=True,
        ):
            domains = dataset_management.list_domains(arcpy, root)
            dataset_management.run_alter_domain(
                arcpy,
                root,
                "StatusDomain",
                new_domain_description="Updated status values",
            )
            dataset_management.run_set_range_domain(
                arcpy,
                root,
                "HeightRange",
                0,
                250,
            )
            dataset_management.run_clear_subtype_field(
                arcpy,
                str(Path(root) / "data.gdb" / "Parcels"),
            )
        self.assertEqual(domains["domains"][0]["coded_values"][0]["code"], "open")
        self.assertEqual(domains["domains"][1]["range"], [0.0, 100.0])
        self.assertEqual(
            [name for name, _args, _kwargs in arcpy.management.calls],
            ["AlterDomain", "SetValueForRangeDomain", "SetSubtypeField"],
        )
        self.assertEqual(
            arcpy.management.calls[-1][1],
            (os.path.normpath(str(Path(root) / "data.gdb" / "Parcels")), "#", "CLEAR_SUBTYPE_FIELD"),
        )

    def test_attachment_helpers_validate_both_dataset_paths(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
            },
            clear=True,
        ):
            dataset = str(Path(root) / "data.gdb" / "Parcels")
            matches = str(Path(root) / "attachment_matches.csv")
            dataset_management.run_add_attachments(
                arcpy,
                dataset,
                "GLOBALID",
                matches,
                "REL_GLOBALID",
                "FILE_PATH",
                root,
            )
        name, args, _kwargs = arcpy.management.calls[-1]
        self.assertEqual(name, "AddAttachments")
        self.assertEqual(args[0], os.path.normpath(dataset))
        self.assertEqual(args[2], os.path.normpath(matches))

    def test_relationship_and_topology_outputs_stay_under_gp_root(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            origin = str(Path(input_root) / "data.gdb" / "Origin")
            destination = str(Path(input_root) / "data.gdb" / "Destination")
            relationship = str(Path(output_root) / "data.gdb" / "OriginDestination")
            feature_dataset = str(Path(output_root) / "data.gdb" / "Network")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                dataset_management.run_create_relationship_class(
                    arcpy,
                    origin,
                    destination,
                    relationship,
                    "SIMPLE",
                    "to destination",
                    "to origin",
                    "NONE",
                    "ONE_TO_MANY",
                    "GLOBALID",
                    "ORIGIN_GUID",
                )
                topology = dataset_management.run_create_topology(
                    arcpy,
                    feature_dataset,
                    "ParcelTopology",
                    0.001,
                )
            self.assertTrue(topology["topology_path"].endswith("ParcelTopology"))
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "GP_OUTPUT_ROOT"):
                    dataset_management.run_create_relationship_class(
                        arcpy,
                        origin,
                        destination,
                        str(Path(input_root) / "outside.gdb" / "Relationship"),
                        "SIMPLE",
                        "forward",
                        "backward",
                        "NONE",
                        "ONE_TO_ONE",
                        "OBJECTID",
                        "ORIGINID",
                    )

    def test_remove_topology_rule_uses_arcpy_two_argument_contract(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as output_root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
            },
            clear=True,
        ):
            topology = str(Path(output_root) / "data.gdb" / "ParcelTopology")
            dataset_management.run_remove_rule_from_topology(
                arcpy,
                topology,
                "Must Not Have Gaps (Area)",
            )
        name, args, kwargs = arcpy.management.calls[-1]
        self.assertEqual(name, "RemoveRuleFromTopology")
        self.assertEqual(
            args,
            (os.path.normpath(topology), "Must Not Have Gaps (Area)"),
        )
        self.assertEqual(kwargs, {})


if __name__ == "__main__":
    unittest.main()
