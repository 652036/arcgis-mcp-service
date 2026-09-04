from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from arcgis_pro_mcp import data_integrity


def _norm(value: str) -> str:
    return os.path.normpath(value)


class _Result:
    messageCount = 1

    def getMessage(self, index: int) -> str:
        return f"message-{index}"


class _Rule:
    def __init__(
        self,
        name: str,
        rule_type: str = "esriARTCalculation",
        *,
        script: str = "return 1",
        editable: bool = True,
        batch: bool = False,
    ) -> None:
        self.id = abs(hash(name)) % 100000
        self.name = name
        self.type = rule_type
        self.scriptExpression = script
        self.userEditable = editable
        self.isEnabled = True
        self.triggeringEvents = ["Update"]
        self.errorNumber = None
        self.errorMessage = None
        self.description = "test rule"
        self.subtypeCode = None
        self.subtypeCodes = [1, 2]
        self.fieldName = "VALUE"
        self.excludeFromClientEvaluation = False
        self.batch = batch
        self.severity = None
        self.tags = ["audit"]
        self.evaluationOrder = 1
        self.creationTime = "2026-09-03T00:00:00"
        self.referencesExternalService = False
        self.requiredGeodatabaseClientVersion = "3.0"
        self.checkParameters = None
        self.triggeringFields = ["SOURCE"]


class _FieldGroup:
    def __init__(self, name: str, fields: list[str], restrictive: bool) -> None:
        self.name = name
        self.fieldNames = list(fields)
        self.isEditingRestrictive = restrictive


class _ContingentField:
    def __init__(
        self,
        name: str,
        value_type: str,
        value: Any,
    ) -> None:
        self.name = name
        self.type = value_type
        self.code = value if value_type == "CODED_VALUE" else None
        if value_type == "RANGE" and isinstance(value, str):
            lower, upper = value.split(";", 1)
            self.range = (float(lower), float(upper))
        else:
            self.range = None


class _ContingentValue:
    def __init__(
        self,
        identifier: int,
        group: str,
        subtype: int | None,
        retired: bool,
        values: list[_ContingentField],
    ) -> None:
        self.id = identifier
        self.fieldGroupName = group
        self.subtype = subtype
        self.isRetired = retired
        self.values = values


class _DA:
    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner

    def ListContingentValues(
        self,
        table: str,
        field_group_name: str = "",
        subtype_code: int | None = None,
    ) -> list[_ContingentValue]:
        self.owner.da_calls.append((table, field_group_name, subtype_code))
        values = list(self.owner.contingent_values)
        if field_group_name:
            values = [value for value in values if value.fieldGroupName == field_group_name]
        if subtype_code is not None:
            values = [value for value in values if value.subtype == subtype_code]
        return values


class _Management:
    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner

    def _record(self, name: str, args: tuple[Any, ...]) -> _Result:
        self.owner.calls.append((name, args))
        return _Result()

    def ExportAttributeRules(self, *args: Any) -> _Result:
        if self.owner.mark_exports:
            Path(args[1]).touch()
        return self._record("ExportAttributeRules", args)

    def ImportAttributeRules(self, *args: Any) -> _Result:
        if self.owner.apply_mutations:
            self.owner.rules.extend(_Rule(name) for name in self.owner.import_rule_names)
        return self._record("ImportAttributeRules", args)

    def AddAttributeRule(self, *args: Any) -> _Result:
        if self.owner.apply_mutations:
            type_name = {
                "CALCULATION": "esriARTCalculation",
                "CONSTRAINT": "esriARTConstraint",
                "VALIDATION": "esriARTValidation",
            }[args[2]]
            rule = _Rule(
                args[1],
                type_name,
                script=args[3],
                editable=args[4] != "NONEDITABLE",
                batch=args[12] == "BATCH",
            )
            rule.triggeringEvents = list(args[5] or [])
            rule.errorNumber = args[6]
            rule.errorMessage = args[7]
            rule.description = args[8]
            rule.subtypeCode = args[9]
            rule.fieldName = args[10]
            rule.excludeFromClientEvaluation = args[11] == "EXCLUDE"
            rule.severity = args[13]
            rule.tags = list(args[14] or [])
            rule.triggeringFields = list(args[15] or [])
            self.owner.rules.append(rule)
        return self._record("AddAttributeRule", args)

    def DeleteAttributeRule(self, *args: Any) -> _Result:
        if self.owner.apply_mutations:
            names = set(args[1])
            self.owner.rules = [rule for rule in self.owner.rules if rule.name not in names]
        return self._record("DeleteAttributeRule", args)

    def CreateFieldGroup(self, *args: Any) -> _Result:
        if self.owner.apply_mutations:
            self.owner.field_groups.append(
                _FieldGroup(args[1], list(args[2]), args[3] == "RESTRICT")
            )
        return self._record("CreateFieldGroup", args)

    def DeleteFieldGroup(self, *args: Any) -> _Result:
        if self.owner.apply_mutations:
            self.owner.field_groups = [
                group for group in self.owner.field_groups if group.name != args[1]
            ]
            self.owner.contingent_values = [
                value for value in self.owner.contingent_values if value.fieldGroupName != args[1]
            ]
        return self._record("DeleteFieldGroup", args)

    def AddContingentValue(self, *args: Any) -> _Result:
        if self.owner.apply_mutations:
            identifier = max((value.id for value in self.owner.contingent_values), default=0) + 1
            values = [
                _ContingentField(row[0], row[1], row[2])
                for row in args[2]
            ]
            self.owner.contingent_values.append(
                _ContingentValue(
                    identifier,
                    args[1],
                    args[3],
                    args[4] == "RETIRE",
                    values,
                )
            )
        return self._record("AddContingentValue", args)

    def RemoveContingentValue(self, *args: Any) -> _Result:
        if self.owner.apply_mutations:
            self.owner.contingent_values = [
                value for value in self.owner.contingent_values if value.id != args[1]
            ]
        return self._record("RemoveContingentValue", args)

    def ExportContingentValues(self, *args: Any) -> _Result:
        if self.owner.mark_exports:
            Path(args[1]).touch()
            Path(args[2]).touch()
        return self._record("ExportContingentValues", args)

    def ImportContingentValues(self, *args: Any) -> _Result:
        if self.owner.apply_mutations:
            if args[3] == "REPLACE":
                self.owner.field_groups = []
                self.owner.contingent_values = []
            self.owner.field_groups.extend(self.owner.import_field_groups)
            self.owner.contingent_values.extend(self.owner.import_contingent_values)
        return self._record("ImportContingentValues", args)


class _Arcpy:
    def __init__(
        self,
        dataset_path: str,
        *,
        rules: list[_Rule] | None = None,
        field_groups: list[_FieldGroup] | None = None,
        contingent_values: list[_ContingentValue] | None = None,
        apply_mutations: bool = True,
        mark_exports: bool = True,
        schema_lock: bool = True,
    ) -> None:
        self.dataset_path = _norm(dataset_path)
        self.rules = list(rules or [])
        self.field_groups = list(field_groups or [])
        self.contingent_values = list(contingent_values or [])
        self.apply_mutations = apply_mutations
        self.mark_exports = mark_exports
        self.schema_lock = schema_lock
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.da_calls: list[tuple[str, str, int | None]] = []
        self.import_rule_names: list[str] = []
        self.import_field_groups: list[_FieldGroup] = []
        self.import_contingent_values: list[_ContingentValue] = []
        self.fields = [
            SimpleNamespace(name="ZONE"),
            SimpleNamespace(name="PRESSURE"),
            SimpleNamespace(name="VALUE"),
            SimpleNamespace(name="SOURCE"),
        ]
        self.management = _Management(self)
        self.da = _DA(self)

    def Exists(self, path: str) -> bool:
        return _norm(path) == self.dataset_path or os.path.exists(path)

    def TestSchemaLock(self, path: str) -> bool:
        return _norm(path) == self.dataset_path and self.schema_lock

    def Describe(self, path: str) -> Any:
        if _norm(path) != self.dataset_path:
            raise RuntimeError("unexpected dataset")
        return SimpleNamespace(
            attributeRules=self.rules,
            fieldGroups=self.field_groups,
        )

    def ListFields(self, path: str) -> list[Any]:
        if _norm(path) != self.dataset_path:
            return []
        return self.fields


@contextmanager
def _roots(
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
    export_root: bool = True,
) -> Iterator[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
        environment = {
            "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1" if allow_write else "0",
        }
        if export_root:
            environment["ARCGIS_PRO_MCP_EXPORT_ROOT"] = output_root
        if allow_destructive:
            environment["ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE"] = "1"
        with patch.dict(os.environ, environment, clear=True):
            yield input_root, output_root


class DataIntegrityTests(unittest.TestCase):
    def test_non_applicable_rule_severity_minus_one_is_normalized(self) -> None:
        rule = _Rule("Constraint", "esriARTConstraint")
        rule.severity = -1
        self.assertIsNone(data_integrity._attribute_rule_payload(rule)["severity"])

    def test_rule_inventory_ignores_arcgis_managed_evaluation_order(self) -> None:
        first = {"name": "A", "type": "constraint", "evaluation_order": 1}
        renumbered = {"name": "A", "type": "constraint", "evaluation_order": 2}
        self.assertTrue(data_integrity._same_rule_inventory([first], [renumbered]))

    def test_read_inventory_uses_describe_and_da_list(self) -> None:
        with _roots() as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            rule = _Rule("PopulateValue", editable=False)
            group = _FieldGroup("AssetGroup", ["ZONE", "PRESSURE"], True)
            value = _ContingentValue(
                4,
                "AssetGroup",
                2,
                False,
                [
                    _ContingentField("ZONE", "CODED_VALUE", 1),
                    _ContingentField("PRESSURE", "RANGE", "10;100"),
                ],
            )
            arcpy = _Arcpy(dataset, rules=[rule], field_groups=[group], contingent_values=[value])

            rules = data_integrity.list_attribute_rules(arcpy, dataset)
            groups = data_integrity.list_field_groups(arcpy, dataset)
            values = data_integrity.list_contingent_values(
                arcpy,
                dataset,
                "AssetGroup",
                2,
            )

        self.assertEqual(rules["source_api"], "arcpy.Describe(...).attributeRules")
        self.assertFalse(rules["rules"][0]["is_editable"])
        self.assertTrue(rules["rules"][0]["is_enabled"])
        self.assertEqual(rules["rules"][0]["subtype_codes"], [1, 2])
        self.assertEqual(groups["field_groups"][0]["fields"], ["ZONE", "PRESSURE"])
        self.assertTrue(groups["field_groups"][0]["is_restrictive"])
        self.assertEqual(values["source_api"], "arcpy.da.ListContingentValues")
        self.assertEqual(values["contingent_values"][0]["values"][1]["range"], [10.0, 100.0])

    def test_exports_stay_in_export_root_reject_overwrite_and_verify_files(self) -> None:
        with _roots() as (input_root, output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            arcpy = _Arcpy(dataset)
            rules_csv = str(Path(output_root) / "rules.csv")
            groups_csv = str(Path(output_root) / "groups.csv")
            values_csv = str(Path(output_root) / "values.csv")

            rules_result = data_integrity.export_attribute_rules(arcpy, dataset, rules_csv)
            contingent_result = data_integrity.export_contingent_values(
                arcpy,
                dataset,
                groups_csv,
                values_csv,
            )
            with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
                data_integrity.export_attribute_rules(arcpy, dataset, rules_csv)

        self.assertTrue(rules_result["output"]["verified"])
        self.assertTrue(contingent_result["outputs"]["field_groups_csv"]["verified"])
        self.assertEqual(
            arcpy.calls,
            [
                ("ExportAttributeRules", (_norm(dataset), _norm(rules_csv))),
                (
                    "ExportContingentValues",
                    (_norm(dataset), _norm(groups_csv), _norm(values_csv)),
                ),
            ],
        )

    def test_export_root_is_mandatory(self) -> None:
        with _roots(export_root=False) as (input_root, output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            with self.assertRaisesRegex(RuntimeError, "EXPORT_ROOT"):
                data_integrity.export_attribute_rules(
                    _Arcpy(dataset),
                    dataset,
                    str(Path(output_root) / "rules.csv"),
                )

    def test_add_and_delete_attribute_rule_require_confirmation_and_verify_state(self) -> None:
        with _roots(allow_write=True, allow_destructive=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            arcpy = _Arcpy(dataset)
            added = data_integrity.add_attribute_rule(
                arcpy,
                dataset,
                "PopulateValue",
                "CALCULATION",
                "return $feature.SOURCE;",
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
                is_editable=False,
                triggering_events=["INSERT", "UPDATE"],
                field="VALUE",
                triggering_fields=["SOURCE"],
                tags=["integrity"],
            )
            deleted = data_integrity.delete_attribute_rules(
                arcpy,
                dataset,
                ["PopulateValue"],
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
                rule_type="CALCULATION",
            )

        self.assertEqual(
            arcpy.calls[0],
            (
                "AddAttributeRule",
                (
                    _norm(dataset),
                    "PopulateValue",
                    "CALCULATION",
                    "return $feature.SOURCE;",
                    "NONEDITABLE",
                    ["INSERT", "UPDATE"],
                    None,
                    None,
                    None,
                    None,
                    "VALUE",
                    "INCLUDE",
                    "NOT_BATCH",
                    None,
                    ["integrity"],
                    ["SOURCE"],
                ),
            ),
        )
        self.assertEqual(
            arcpy.calls[1],
            (
                "DeleteAttributeRule",
                (_norm(dataset), ["PopulateValue"], "CALCULATION"),
            ),
        )
        self.assertTrue(added["verified"])
        self.assertTrue(deleted["verified"])

    def test_constraint_and_validation_attribute_rule_parameters(self) -> None:
        with _roots(allow_write=True, allow_destructive=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            arcpy = _Arcpy(dataset)
            data_integrity.add_attribute_rule(
                arcpy,
                dataset,
                "PressureConstraint",
                "CONSTRAINT",
                "return $feature.PRESSURE > 0;",
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
                triggering_events=["UPDATE"],
                error_number=101,
                error_message="Pressure must be positive",
                exclude_from_client_evaluation=True,
                triggering_fields=["PRESSURE"],
            )
            data_integrity.add_attribute_rule(
                arcpy,
                dataset,
                "PressureValidation",
                "VALIDATION",
                "return $feature.PRESSURE < 1000;",
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
                batch=True,
                error_number=102,
                error_message="Pressure is implausible",
                severity=2,
            )

        constraint = arcpy.calls[0][1]
        validation = arcpy.calls[1][1]
        self.assertEqual(constraint[2:7], (
            "CONSTRAINT",
            "return $feature.PRESSURE > 0;",
            None,
            ["UPDATE"],
            "101",
        ))
        self.assertEqual(constraint[11:16], (
            "EXCLUDE",
            "NOT_BATCH",
            None,
            None,
            ["PRESSURE"],
        ))
        self.assertEqual(validation[2:7], (
            "VALIDATION",
            "return $feature.PRESSURE < 1000;",
            None,
            None,
            "102",
        ))
        self.assertEqual(validation[11:16], (None, "BATCH", 2, None, None))

    def test_import_attribute_rules_confirms_csv_name_set(self) -> None:
        with _roots(allow_write=True, allow_destructive=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            first = Path(input_root) / "rules_a.csv"
            second = Path(input_root) / "rules_b.csv"
            first.write_text("NAME,TYPE\nRuleA,CALCULATION\n", encoding="utf-8")
            second.write_text("NAME,TYPE\nRuleB,CONSTRAINT\n", encoding="utf-8")
            arcpy = _Arcpy(dataset)
            arcpy.import_rule_names = ["RuleA", "RuleB"]
            result = data_integrity.import_attribute_rules(
                arcpy,
                dataset,
                [str(first), str(second)],
                ["RuleA", "RuleB"],
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
            )

        self.assertEqual(result["imported_rule_names"], ["RuleA", "RuleB"])
        self.assertEqual(result["rule_count_after"], 2)
        self.assertEqual(arcpy.calls[0][0], "ImportAttributeRules")
        self.assertEqual(arcpy.calls[0][1][1], [_norm(str(first)), _norm(str(second))])

    def test_field_group_and_contingent_value_lifecycle(self) -> None:
        with _roots(allow_write=True, allow_destructive=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            arcpy = _Arcpy(dataset)
            group = data_integrity.create_field_group(
                arcpy,
                dataset,
                "AssetGroup",
                ["zone", "pressure"],
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
                restrictive=True,
            )
            added = data_integrity.add_contingent_value(
                arcpy,
                dataset,
                "AssetGroup",
                [
                    {"field": "ZONE", "value_type": "CODED_VALUE", "value": 2},
                    {
                        "field": "PRESSURE",
                        "value_type": "RANGE",
                        "minimum": 12345678,
                        "maximum": 12345679,
                    },
                ],
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
                subtype="Water",
                retired=True,
            )
            identifier = added["contingent_value"]["id"]
            removed = data_integrity.remove_contingent_value(
                arcpy,
                dataset,
                identifier,
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
            )
            deleted = data_integrity.delete_field_group(
                arcpy,
                dataset,
                "AssetGroup",
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
            )

        self.assertEqual(group["field_group"]["fields"], ["ZONE", "PRESSURE"])
        self.assertEqual(
            arcpy.calls[0],
            (
                "CreateFieldGroup",
                (_norm(dataset), "AssetGroup", ["ZONE", "PRESSURE"], "RESTRICT"),
            ),
        )
        self.assertEqual(arcpy.calls[1][1][2], [
            ["ZONE", "CODED_VALUE", 2],
            ["PRESSURE", "RANGE", "12345678;12345679"],
        ])
        self.assertEqual(arcpy.calls[1][1][3], "Water")
        self.assertEqual(arcpy.calls[1][1][4], "RETIRE")
        self.assertEqual(arcpy.calls[2], ("RemoveContingentValue", (_norm(dataset), identifier)))
        self.assertEqual(arcpy.calls[3], ("DeleteFieldGroup", (_norm(dataset), "AssetGroup")))
        self.assertTrue(removed["verified"])
        self.assertTrue(deleted["verified"])

    def test_import_contingent_values_is_the_typed_bulk_update(self) -> None:
        with _roots(allow_write=True, allow_destructive=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            groups_csv = Path(input_root) / "groups.csv"
            values_csv = Path(input_root) / "values.csv"
            groups_csv.write_text(
                "NAME,IS_RESTRICTIVE,FIELD1,FIELD2\n"
                "AssetGroup,TRUE,ZONE,PRESSURE\n",
                encoding="utf-8",
            )
            values_csv.write_text(
                "CAV_ID,IS_RETIRED,FIELD_GROUP,SUBTYPE,SUBTYPE_NAME,"
                "CV_TYPE1,CV_VALUE1,DESCRIPTION1,CV_TYPE2,CV_VALUE2,DESCRIPTION2\n"
                "8,FALSE,AssetGroup,,,1,,,2,,\n",
                encoding="utf-8",
            )
            arcpy = _Arcpy(dataset)
            arcpy.import_field_groups = [_FieldGroup("AssetGroup", ["ZONE", "PRESSURE"], True)]
            arcpy.import_contingent_values = [
                _ContingentValue(
                    8,
                    "AssetGroup",
                    None,
                    False,
                    [
                        _ContingentField("ZONE", "ANY", None),
                        _ContingentField("PRESSURE", "NULL", None),
                    ],
                )
            ]
            result = data_integrity.import_contingent_values(
                arcpy,
                dataset,
                str(groups_csv),
                str(values_csv),
                "UNION",
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
            )

        self.assertTrue(result["verified"])
        self.assertEqual(result["field_group_count_after"], 1)
        self.assertEqual(result["contingent_value_count_after"], 1)
        self.assertEqual(result["verification"], "official_csv_manifest_and_post_state")
        self.assertEqual(
            arcpy.calls[0],
            (
                "ImportContingentValues",
                (
                    _norm(dataset),
                    _norm(str(groups_csv)),
                    _norm(str(values_csv)),
                    "UNION",
                ),
            ),
        )

    def test_replace_contingent_values_verifies_exact_manifest_and_rejects_noop(self) -> None:
        with _roots(allow_write=True, allow_destructive=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            groups_csv = Path(input_root) / "replace_groups.csv"
            values_csv = Path(input_root) / "replace_values.csv"
            groups_csv.write_text(
                "NAME,IS_RESTRICTIVE,FIELD1\nReplacement,TRUE,ZONE\n",
                encoding="utf-8",
            )
            values_csv.write_text(
                "CAV_ID,IS_RETIRED,FIELD_GROUP,SUBTYPE,SUBTYPE_NAME,"
                "CV_TYPE1,CV_VALUE1,DESCRIPTION1\n"
                "12,TRUE,Replacement,2,Water,3,1,Primary\n",
                encoding="utf-8",
            )
            replacement_group = _FieldGroup("Replacement", ["ZONE"], True)
            replacement_value = _ContingentValue(
                12,
                "Replacement",
                2,
                True,
                [_ContingentField("ZONE", "CODED_VALUE", 1)],
            )
            arcpy = _Arcpy(
                dataset,
                field_groups=[_FieldGroup("Old", ["ZONE"], False)],
                contingent_values=[
                    _ContingentValue(
                        1,
                        "Old",
                        None,
                        False,
                        [_ContingentField("ZONE", "ANY", None)],
                    )
                ],
            )
            arcpy.import_field_groups = [replacement_group]
            arcpy.import_contingent_values = [replacement_value]
            result = data_integrity.import_contingent_values(
                arcpy,
                dataset,
                str(groups_csv),
                str(values_csv),
                "REPLACE",
                expected_dataset=dataset,
                confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
            )
            noop = _Arcpy(dataset, apply_mutations=False)
            with self.assertRaisesRegex(RuntimeError, "CSV 字段组未全部出现"):
                data_integrity.import_contingent_values(
                    noop,
                    dataset,
                    str(groups_csv),
                    str(values_csv),
                    "REPLACE",
                    expected_dataset=dataset,
                    confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
                )

        self.assertTrue(result["verified"])
        self.assertEqual(result["field_group_count_after"], 1)
        self.assertEqual(result["contingent_value_count_after"], 1)
        self.assertEqual(result["manifest_contingent_value_count"], 1)

    def test_all_schema_mutations_require_both_gates_exact_target_and_phrase(self) -> None:
        def operations(dataset: str) -> dict[str, Any]:
            common = {
                "expected_dataset": dataset,
                "confirmation": data_integrity.SCHEMA_MUTATION_CONFIRMATION,
            }
            return {
                "import_attribute_rules": lambda arcpy, **override: (
                    data_integrity.import_attribute_rules(
                        arcpy,
                        dataset,
                        [str(Path(dataset).parent / "rules.csv")],
                        ["Rule"],
                        **(common | override),
                    )
                ),
                "add_attribute_rule": lambda arcpy, **override: data_integrity.add_attribute_rule(
                    arcpy,
                    dataset,
                    "Rule",
                    "CALCULATION",
                    "return 1;",
                    triggering_events=["INSERT"],
                    **(common | override),
                ),
                "delete_attribute_rules": lambda arcpy, **override: (
                    data_integrity.delete_attribute_rules(
                        arcpy,
                        dataset,
                        ["Rule"],
                        **(common | override),
                    )
                ),
                "create_field_group": lambda arcpy, **override: data_integrity.create_field_group(
                    arcpy,
                    dataset,
                    "Group",
                    ["ZONE", "PRESSURE"],
                    **(common | override),
                ),
                "delete_field_group": lambda arcpy, **override: data_integrity.delete_field_group(
                    arcpy,
                    dataset,
                    "Group",
                    **(common | override),
                ),
                "add_contingent_value": lambda arcpy, **override: (
                    data_integrity.add_contingent_value(
                        arcpy,
                        dataset,
                        "Group",
                        [
                            {"field": "ZONE", "value_type": "ANY"},
                            {"field": "PRESSURE", "value_type": "NULL"},
                        ],
                        **(common | override),
                    )
                ),
                "remove_contingent_value": lambda arcpy, **override: (
                    data_integrity.remove_contingent_value(
                        arcpy,
                        dataset,
                        1,
                        **(common | override),
                    )
                ),
                "import_contingent_values": lambda arcpy, **override: (
                    data_integrity.import_contingent_values(
                        arcpy,
                        dataset,
                        str(Path(dataset).parent / "groups.csv"),
                        str(Path(dataset).parent / "values.csv"),
                        **(common | override),
                    )
                ),
            }

        with _roots() as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            for name, operation in operations(dataset).items():
                with self.subTest(operation=name, gate="write"):
                    with self.assertRaisesRegex(RuntimeError, "ALLOW_WRITE"):
                        operation(_Arcpy(dataset))

        with _roots(allow_write=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            for name, operation in operations(dataset).items():
                with self.subTest(operation=name, gate="destructive"):
                    with self.assertRaisesRegex(RuntimeError, "ALLOW_DESTRUCTIVE"):
                        operation(_Arcpy(dataset))

        with _roots(allow_write=True, allow_destructive=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            for name, operation in operations(dataset).items():
                arcpy = _Arcpy(dataset)
                with self.subTest(operation=name, gate="target"):
                    with self.assertRaisesRegex(RuntimeError, "expected_dataset"):
                        operation(arcpy, expected_dataset=dataset + "_wrong")
                with self.subTest(operation=name, gate="phrase"):
                    with self.assertRaisesRegex(RuntimeError, "confirmation"):
                        operation(arcpy, confirmation="yes")
                self.assertEqual(arcpy.calls, [])

    def test_mutations_fail_when_post_state_cannot_be_verified(self) -> None:
        with _roots(allow_write=True, allow_destructive=True) as (input_root, _output_root):
            dataset = str(Path(input_root) / "data.gdb" / "assets")
            arcpy = _Arcpy(dataset, apply_mutations=False)
            with self.assertRaisesRegex(RuntimeError, "无法验证字段组"):
                data_integrity.create_field_group(
                    arcpy,
                    dataset,
                    "Group",
                    ["ZONE", "PRESSURE"],
                    expected_dataset=dataset,
                    confirmation=data_integrity.SCHEMA_MUTATION_CONFIRMATION,
                )

    def test_no_nonexistent_validate_or_update_contingent_api_is_exposed(self) -> None:
        self.assertFalse(hasattr(data_integrity, "validate_contingent_values"))
        self.assertFalse(hasattr(data_integrity, "update_contingent_values"))


if __name__ == "__main__":
    unittest.main()
