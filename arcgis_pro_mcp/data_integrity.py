"""Auditable ArcPy helpers for attribute rules and contingent values.

The public helpers use only APIs present in the ArcGIS Pro 3.6 ArcPy wrapper.
ArcPy is injected by the caller so this module remains importable and testable
outside ArcGIS Pro.  Every schema mutation is deliberately treated as
destructive and requires an exact dataset echo plus a fixed confirmation.
"""

from __future__ import annotations

import csv
import math
import os
from collections import Counter
from typing import Any

from arcgis_pro_mcp.paths import (
    require_allow_destructive,
    validate_input_path_optional,
    validate_output_in_export_root,
)

SCHEMA_MUTATION_CONFIRMATION = "CONFIRM_DATA_INTEGRITY_SCHEMA_MUTATION"

_RULE_TYPES = frozenset({"CALCULATION", "CONSTRAINT", "VALIDATION"})
_TRIGGERING_EVENTS = frozenset({"INSERT", "UPDATE", "DELETE"})
_CONTINGENT_VALUE_TYPES = frozenset({"ANY", "NULL", "CODED_VALUE", "RANGE"})
_CONTINGENT_IMPORT_TYPES = frozenset({"UNION", "REPLACE"})
_MISSING = object()


def _messages(result: Any) -> list[str]:
    messages: list[str] = []
    try:
        for index in range(int(result.messageCount)):
            messages.append(str(result.getMessage(index)))
    except Exception:  # noqa: BLE001
        try:
            combined = str(result.getMessages())
        except Exception:  # noqa: BLE001
            combined = ""
        if combined:
            messages.append(combined)
    return messages


def _enum(value: str, allowed: frozenset[str], label: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized not in allowed:
        raise RuntimeError(f"{label} 须为 {sorted(allowed)}")
    return normalized


def _text(
    value: Any,
    label: str,
    *,
    maximum: int,
    required: bool = True,
    forbid_delimiters: bool = False,
) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} 必须为字符串")
    cleaned = value.strip()
    if required and not cleaned:
        raise RuntimeError(f"{label} 不能为空")
    if len(cleaned) > maximum or "\x00" in cleaned:
        raise RuntimeError(f"{label} 无效")
    if forbid_delimiters and any(char in cleaned for char in (";", "\r", "\n")):
        raise RuntimeError(f"{label} 含非法分隔符")
    return cleaned


def _name(value: Any, label: str, *, maximum: int = 128) -> str:
    return _text(
        value,
        label,
        maximum=maximum,
        required=True,
        forbid_delimiters=True,
    )


def _name_list(
    values: list[str] | None,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 128,
    item_length: int = 128,
) -> list[str]:
    if values is None:
        values = []
    if not isinstance(values, list) or not minimum <= len(values) <= maximum:
        raise RuntimeError(f"{label} 数量必须在 {minimum}–{maximum} 之间")
    cleaned = [_name(value, f"{label}[{index}]", maximum=item_length) for index, value in enumerate(values)]
    if len({value.casefold() for value in cleaned}) != len(cleaned):
        raise RuntimeError(f"{label} 不得包含重复项")
    return cleaned


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{label} 必须为整数")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f"–{maximum}" if maximum is not None else " 以上"
        raise RuntimeError(f"{label} 必须在 {minimum}{upper} 范围内")
    return value


def _finite(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label} 必须为有限数值")
    if isinstance(value, float) and not math.isfinite(value):
        raise RuntimeError(f"{label} 必须为有限数值")
    return value


def _dataset(arcpy: Any, dataset_path: str, label: str = "dataset_path") -> str:
    path = validate_input_path_optional(dataset_path, label)
    if not isinstance(path, str):
        raise RuntimeError(f"{label} 必须为 input roots 内的绝对数据集路径")
    exists = getattr(arcpy, "Exists", None)
    if not callable(exists):
        raise RuntimeError("当前 ArcPy 不支持 Exists，无法核验数据集")
    if not bool(exists(path)):
        raise RuntimeError(f"{label} 不存在：{path}")
    return path


def _management_tool(arcpy: Any, name: str) -> Any:
    tool = getattr(getattr(arcpy, "management", None), name, None)
    if not callable(tool):
        raise RuntimeError(f"当前 ArcGIS Pro/ArcPy 不支持 arcpy.management.{name}")
    return tool


def _require_schema_lock(arcpy: Any, dataset_path: str) -> None:
    tester = getattr(arcpy, "TestSchemaLock", None)
    if not callable(tester):
        raise RuntimeError("当前 ArcPy 不支持 TestSchemaLock，拒绝方案变更")
    if not bool(tester(dataset_path)):
        raise RuntimeError(f"无法获取方案锁：{dataset_path}")


def _mutation_target(
    arcpy: Any,
    dataset_path: str,
    expected_dataset: str,
    confirmation: str,
) -> str:
    require_allow_destructive()
    if expected_dataset != dataset_path:
        raise RuntimeError("expected_dataset 必须逐字符精确回显 dataset_path")
    if confirmation != SCHEMA_MUTATION_CONFIRMATION:
        raise RuntimeError(
            f"confirmation 必须精确等于 {SCHEMA_MUTATION_CONFIRMATION!r}"
        )
    path = _dataset(arcpy, dataset_path)
    _require_schema_lock(arcpy, path)
    return path


def _property(item: Any, *names: str, default: Any = _MISSING) -> Any:
    if isinstance(item, dict):
        folded = {str(key).casefold(): value for key, value in item.items()}
        for name in names:
            if name.casefold() in folded:
                return folded[name.casefold()]
    else:
        for name in names:
            try:
                return getattr(item, name)
            except (AttributeError, RuntimeError):
                continue
    if default is _MISSING:
        raise RuntimeError(f"ArcPy 对象缺少预期属性：{'/'.join(names)}")
    return default


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _attribute_rule_payload(item: Any) -> dict[str, Any]:
    severity = _jsonable(_property(item, "severity", default=None))
    if severity == -1:
        # ArcGIS Pro 3.6 reports -1 when severity is not applicable to an
        # immediate calculation/constraint rule.
        severity = None
    return {
        "id": _jsonable(_property(item, "id", default=None)),
        "name": str(_property(item, "name", default="") or ""),
        "type": _jsonable(_property(item, "type", default=None)),
        "script_expression": _jsonable(
            _property(item, "scriptExpression", "script_expression", default=None)
        ),
        "is_editable": _jsonable(
            _property(item, "userEditable", "isEditable", "is_editable", default=None)
        ),
        "is_enabled": _jsonable(_property(item, "isEnabled", "is_enabled", default=None)),
        "triggering_events": _jsonable(
            _property(item, "triggeringEvents", "triggering_events", default=[])
        ),
        "error_number": _jsonable(_property(item, "errorNumber", "error_number", default=None)),
        "error_message": _jsonable(_property(item, "errorMessage", "error_message", default=None)),
        "description": _jsonable(_property(item, "description", default=None)),
        "subtype_code": _jsonable(_property(item, "subtypeCode", "subtype_code", default=None)),
        "subtype_codes": _jsonable(
            _property(item, "subtypeCodes", "subtype_codes", default=[])
        ),
        "field_name": _jsonable(_property(item, "fieldName", "field_name", default=None)),
        "exclude_from_client_evaluation": _jsonable(
            _property(
                item,
                "excludeFromClientEvaluation",
                "exclude_from_client_evaluation",
                default=None,
            )
        ),
        "batch": _jsonable(_property(item, "batch", "isBatch", default=None)),
        "severity": severity,
        "tags": _jsonable(_property(item, "tags", default=[])),
        "evaluation_order": _jsonable(
            _property(item, "evaluationOrder", "evaluation_order", default=None)
        ),
        "creation_time": _jsonable(
            _property(item, "creationTime", "creation_time", default=None)
        ),
        "references_external_service": _jsonable(
            _property(
                item,
                "referencesExternalService",
                "references_external_service",
                default=None,
            )
        ),
        "required_geodatabase_client_version": _jsonable(
            _property(
                item,
                "requiredGeodatabaseClientVersion",
                "required_geodatabase_client_version",
                default=None,
            )
        ),
        "check_parameters": _jsonable(
            _property(item, "checkParameters", "check_parameters", default=None)
        ),
        "triggering_fields": _jsonable(
            _property(item, "triggeringFields", "triggering_fields", default=[])
        ),
    }


def _describe_collection(arcpy: Any, dataset_path: str, property_name: str) -> list[Any]:
    describe = getattr(arcpy, "Describe", None)
    if not callable(describe):
        raise RuntimeError("当前 ArcPy 不支持 Describe")
    description = describe(dataset_path)
    # Classic Describe omits these dynamic properties when the collection is
    # empty, so absence is the documented empty-state rather than an error.
    values = _property(description, property_name, default=None)
    if values is None:
        return []
    try:
        return list(values)
    except TypeError as exc:
        raise RuntimeError(f"Describe.{property_name} 不是可枚举集合") from exc


def _attribute_rules(arcpy: Any, dataset_path: str) -> list[dict[str, Any]]:
    values = [
        _attribute_rule_payload(item)
        for item in _describe_collection(arcpy, dataset_path, "attributeRules")
    ]
    if any(not item["name"] for item in values):
        raise RuntimeError("Describe.attributeRules 返回了缺少名称的规则")
    return sorted(values, key=lambda item: item["name"].casefold())


def _same_rule_inventory(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    """Compare rules while ignoring ArcGIS-managed evaluation-order renumbering."""

    def stable(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for value in values:
            item = dict(value)
            item.pop("evaluation_order", None)
            result.append(item)
        return result

    return stable(left) == stable(right)


def list_attribute_rules(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    """List rules using the documented ``Describe.attributeRules`` property."""
    path = _dataset(arcpy, dataset_path)
    rules = _attribute_rules(arcpy, path)
    return {
        "dataset_path": path,
        "rules": rules,
        "rule_count": len(rules),
        "source_api": "arcpy.Describe(...).attributeRules",
        "verified": True,
    }


def _export_path(output_path: str, label: str) -> str:
    export_root = os.environ.get("ARCGIS_PRO_MCP_EXPORT_ROOT", "").strip().strip('"')
    if not export_root:
        raise RuntimeError(f"{label} 需要配置绝对路径 ARCGIS_PRO_MCP_EXPORT_ROOT")
    if not os.path.isabs(export_root):
        raise RuntimeError("ARCGIS_PRO_MCP_EXPORT_ROOT 必须为绝对路径")
    path = validate_output_in_export_root(output_path, label)
    if not path.lower().endswith(".csv"):
        raise RuntimeError(f"{label} 必须以 .csv 结尾")
    if os.path.exists(path):
        raise RuntimeError(f"{label} 已存在；数据完整性导出拒绝覆盖：{path}")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def _verify_export(arcpy: Any, output_path: str, label: str) -> dict[str, Any]:
    visible = os.path.isfile(output_path)
    exists = getattr(arcpy, "Exists", None)
    if not visible and callable(exists):
        try:
            visible = bool(exists(output_path))
        except Exception:  # noqa: BLE001
            visible = False
    if not visible:
        raise RuntimeError(f"{label} 未创建或不可见：{output_path}")
    return {"path": output_path, "verified": True}


def export_attribute_rules(
    arcpy: Any,
    dataset_path: str,
    output_csv: str,
) -> dict[str, Any]:
    path = _dataset(arcpy, dataset_path)
    output = _export_path(output_csv, "output_csv")
    result = _management_tool(arcpy, "ExportAttributeRules")(path, output)
    return {
        "dataset_path": path,
        "output": _verify_export(arcpy, output, "output_csv"),
        "messages": _messages(result),
        "verified": True,
    }


def _input_csv(path: str, label: str) -> str:
    resolved = validate_input_path_optional(path, label)
    if not isinstance(resolved, str) or not resolved.lower().endswith(".csv"):
        raise RuntimeError(f"{label} 必须为 input roots 内的 .csv 绝对路径")
    if not os.path.isfile(resolved):
        raise RuntimeError(f"{label} 不存在或不是文件：{resolved}")
    return resolved


def _official_csv_rows(path: str, label: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        handle = open(path, encoding="utf-8-sig", newline="")  # noqa: SIM115
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"{label} 无法按 UTF-8 CSV 读取") from exc
    with handle:
        reader = csv.DictReader(handle)
        raw_headers = reader.fieldnames or []
        headers = [str(header).strip().upper() for header in raw_headers]
        if not headers or any(not header for header in headers):
            raise RuntimeError(f"{label} 缺少有效表头")
        if len(set(headers)) != len(headers):
            raise RuntimeError(f"{label} 表头在忽略大小写后重复")
        rows: list[dict[str, str]] = []
        for row_number, raw in enumerate(reader, start=2):
            normalized = {
                str(key).strip().upper(): str(value or "").strip()
                for key, value in raw.items()
                if key is not None
            }
            if not any(normalized.values()):
                continue
            normalized["__ROW__"] = str(row_number)
            rows.append(normalized)
    return headers, rows


def _csv_boolean(value: str, label: str) -> bool:
    token = value.strip().upper()
    if token in {"TRUE", "1"}:
        return True
    if token in {"FALSE", "0"}:
        return False
    raise RuntimeError(f"{label} 必须为 TRUE/FALSE")


def _csv_subtype(value: str, label: str) -> int | None:
    token = value.strip()
    if not token or token == "-1":
        return None
    try:
        parsed = int(token)
    except ValueError as exc:
        raise RuntimeError(f"{label} 必须为空或整数 subtype code") from exc
    if parsed < 0:
        raise RuntimeError(f"{label} 必须为空或非负 subtype code")
    return parsed


def _contingent_import_manifest(
    field_groups_csv: str,
    contingent_values_csv: str,
) -> dict[str, Any]:
    group_headers, group_rows = _official_csv_rows(field_groups_csv, "field_groups_csv")
    required_group_headers = {"NAME", "IS_RESTRICTIVE"}
    if not required_group_headers.issubset(group_headers):
        raise RuntimeError("field_groups_csv 缺少官方 NAME/IS_RESTRICTIVE 列")
    field_headers = sorted(
        (
            header
            for header in group_headers
            if header.startswith("FIELD") and header[5:].isdigit()
        ),
        key=lambda header: int(header[5:]),
    )
    if not field_headers or field_headers[0] != "FIELD1":
        raise RuntimeError("field_groups_csv 至少需要官方 FIELD1 列")
    groups: list[dict[str, Any]] = []
    for row in group_rows:
        row_label = f"field_groups_csv 第 {row['__ROW__']} 行"
        fields = [row.get(header, "") for header in field_headers]
        while fields and not fields[-1]:
            fields.pop()
        if not fields or any(not value for value in fields):
            raise RuntimeError(f"{row_label} 的 FIELD 列必须连续且非空")
        groups.append(
            {
                "name": _name(row.get("NAME"), f"{row_label} NAME"),
                "fields": [
                    _name(value, f"{row_label} {field_headers[index]}")
                    for index, value in enumerate(fields)
                ],
                "is_restrictive": _csv_boolean(
                    row.get("IS_RESTRICTIVE", ""),
                    f"{row_label} IS_RESTRICTIVE",
                ),
            }
        )
    if not groups:
        raise RuntimeError("field_groups_csv 不含任何字段组")
    if len({item["name"].casefold() for item in groups}) != len(groups):
        raise RuntimeError("field_groups_csv 包含重复字段组名称")

    value_headers, value_rows = _official_csv_rows(
        contingent_values_csv,
        "contingent_values_csv",
    )
    required_value_headers = {"CAV_ID", "IS_RETIRED", "FIELD_GROUP", "SUBTYPE"}
    if not required_value_headers.issubset(value_headers):
        raise RuntimeError(
            "contingent_values_csv 缺少官方 CAV_ID/IS_RETIRED/FIELD_GROUP/SUBTYPE 列"
        )
    group_names = {item["name"].casefold() for item in groups}
    signatures: list[tuple[str, str, int | None, bool]] = []
    for row in value_rows:
        row_label = f"contingent_values_csv 第 {row['__ROW__']} 行"
        try:
            cav_id = int(row.get("CAV_ID", ""))
        except ValueError as exc:
            raise RuntimeError(f"{row_label} CAV_ID 必须为正整数") from exc
        _integer(cav_id, f"{row_label} CAV_ID", minimum=1)
        group_name = _name(row.get("FIELD_GROUP"), f"{row_label} FIELD_GROUP")
        if group_name.casefold() not in group_names:
            raise RuntimeError(f"{row_label} 引用了 field_groups_csv 中不存在的字段组")
        signatures.append(
            (
                str(cav_id),
                group_name.casefold(),
                _csv_subtype(row.get("SUBTYPE", ""), f"{row_label} SUBTYPE"),
                _csv_boolean(row.get("IS_RETIRED", ""), f"{row_label} IS_RETIRED"),
            )
        )
    if not signatures:
        raise RuntimeError("contingent_values_csv 不含任何条件值")
    return {"field_groups": groups, "contingent_signatures": signatures}


def _csv_rule_names(csv_files: list[str]) -> list[str]:
    values: list[str] = []
    for file_index, path in enumerate(csv_files):
        try:
            handle = open(path, encoding="utf-8-sig", newline="")  # noqa: SIM115
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"csv_files[{file_index}] 无法按 UTF-8 CSV 读取") from exc
        with handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            name_header = next(
                (header for header in fieldnames if str(header).strip().casefold() == "name"),
                None,
            )
            if name_header is None:
                raise RuntimeError(f"csv_files[{file_index}] 缺少官方 NAME 列")
            for row_index, row in enumerate(reader, start=2):
                raw_name = row.get(name_header)
                values.append(
                    _name(
                        raw_name,
                        f"csv_files[{file_index}] 第 {row_index} 行 NAME",
                        maximum=64,
                    )
                )
    if not values:
        raise RuntimeError("属性规则 CSV 不含任何规则")
    if len({value.casefold() for value in values}) != len(values):
        raise RuntimeError("属性规则 CSV 包含重复规则名称")
    return values


def import_attribute_rules(
    arcpy: Any,
    dataset_path: str,
    csv_files: list[str],
    expected_rule_names: list[str],
    *,
    expected_dataset: str,
    confirmation: str,
) -> dict[str, Any]:
    """Import official rule CSV files after confirming their exact NAME set."""
    path = _mutation_target(arcpy, dataset_path, expected_dataset, confirmation)
    if not isinstance(csv_files, list) or not 1 <= len(csv_files) <= 32:
        raise RuntimeError("csv_files 必须包含 1–32 个 CSV 路径")
    sources = [_input_csv(value, f"csv_files[{index}]") for index, value in enumerate(csv_files)]
    if len({os.path.normcase(value) for value in sources}) != len(sources):
        raise RuntimeError("csv_files 不得重复")
    expected = _name_list(
        expected_rule_names,
        "expected_rule_names",
        minimum=1,
        maximum=512,
        item_length=64,
    )
    from_csv = _csv_rule_names(sources)
    if {name.casefold() for name in from_csv} != {name.casefold() for name in expected}:
        raise RuntimeError("expected_rule_names 必须精确覆盖 CSV 中的 NAME 集合")
    before = _attribute_rules(arcpy, path)
    before_names = {item["name"].casefold() for item in before}
    collisions = [name for name in expected if name.casefold() in before_names]
    if collisions:
        raise RuntimeError(f"目标数据集中已存在同名规则，拒绝隐式覆盖：{collisions}")
    result = _management_tool(arcpy, "ImportAttributeRules")(path, sources)
    after = _attribute_rules(arcpy, path)
    after_names = {item["name"].casefold() for item in after}
    added = after_names - before_names
    expected_folded = {name.casefold() for name in expected}
    if added != expected_folded:
        raise RuntimeError(
            "ImportAttributeRules 已返回，但新增规则集合与 expected_rule_names 不一致；"
            "不要自动重试"
        )
    if not _same_rule_inventory(
        [item for item in after if item["name"].casefold() not in expected_folded],
        before,
    ):
        raise RuntimeError("ImportAttributeRules 后既有规则发生变化；不要自动重试")
    return {
        "dataset_path": path,
        "imported_rule_names": expected,
        "rule_count_before": len(before),
        "rule_count_after": len(after),
        "messages": _messages(result),
        "verified": True,
    }


def _optional_number(value: int | None, label: str) -> str | None:
    if value is None:
        return None
    return str(_integer(value, label, minimum=1, maximum=999999999))


def add_attribute_rule(
    arcpy: Any,
    dataset_path: str,
    rule_name: str,
    rule_type: str,
    script_expression: str,
    *,
    expected_dataset: str,
    confirmation: str,
    is_editable: bool = True,
    triggering_events: list[str] | None = None,
    error_number: int | None = None,
    error_message: str = "",
    description: str = "",
    subtypes: list[str] | None = None,
    field: str = "",
    exclude_from_client_evaluation: bool = False,
    batch: bool = False,
    severity: int | None = None,
    tags: list[str] | None = None,
    triggering_fields: list[str] | None = None,
) -> dict[str, Any]:
    path = _mutation_target(arcpy, dataset_path, expected_dataset, confirmation)
    name = _name(rule_name, "rule_name", maximum=64)
    kind = _enum(rule_type, _RULE_TYPES, "rule_type")
    expression = _text(script_expression, "script_expression", maximum=65535)
    if not all(
        isinstance(value, bool)
        for value in (is_editable, exclude_from_client_evaluation, batch)
    ):
        raise RuntimeError("is_editable、exclude_from_client_evaluation 和 batch 必须为布尔值")
    events = [
        _enum(value, _TRIGGERING_EVENTS, "triggering_events")
        for value in _name_list(triggering_events, "triggering_events", maximum=3, item_length=16)
    ]
    error = _optional_number(error_number, "error_number")
    error_text = _text(error_message, "error_message", maximum=256, required=False)
    description_text = _text(description, "description", maximum=256, required=False)
    subtype_values = _name_list(subtypes, "subtypes", maximum=256, item_length=128)
    field_name = _name(field, "field") if field else None
    severity_value = (
        _integer(severity, "severity", minimum=1, maximum=5) if severity is not None else None
    )
    tag_values = _name_list(tags, "tags", maximum=64, item_length=128)
    update_fields = _name_list(
        triggering_fields,
        "triggering_fields",
        maximum=128,
        item_length=128,
    )

    if kind in {"CONSTRAINT", "VALIDATION"} and (error is None or not error_text):
        raise RuntimeError("CONSTRAINT/VALIDATION 必须提供 error_number 和 error_message")
    if kind == "VALIDATION":
        if not batch or events:
            raise RuntimeError("VALIDATION 必须 batch=true 且不得设置 triggering_events")
    elif kind == "CONSTRAINT":
        if batch or not events:
            raise RuntimeError("CONSTRAINT 必须 batch=false 且至少提供一个 triggering_event")
    elif batch and events:
        raise RuntimeError("批量 CALCULATION 不得设置 triggering_events")
    elif not batch and not events:
        raise RuntimeError("即时 CALCULATION 至少需要一个 triggering_event")
    if kind != "CALCULATION" and field_name is not None:
        raise RuntimeError("field 仅适用于 CALCULATION")
    if kind != "VALIDATION" and severity_value is not None:
        raise RuntimeError("severity 仅适用于 VALIDATION")
    if update_fields and "UPDATE" not in events:
        raise RuntimeError("triggering_fields 仅可在 UPDATE 触发事件中使用")

    before = _attribute_rules(arcpy, path)
    if any(item["name"].casefold() == name.casefold() for item in before):
        raise RuntimeError(f"属性规则已存在，拒绝隐式覆盖：{name}")
    result = _management_tool(arcpy, "AddAttributeRule")(
        path,
        name,
        kind,
        expression,
        ("EDITABLE" if is_editable else "NONEDITABLE")
        if kind == "CALCULATION"
        else None,
        events or None,
        error,
        error_text or None,
        description_text or None,
        subtype_values or None,
        field_name,
        ("EXCLUDE" if exclude_from_client_evaluation else "INCLUDE")
        if kind != "VALIDATION" and not batch
        else None,
        "BATCH" if batch else "NOT_BATCH",
        severity_value,
        tag_values or None,
        update_fields or None,
    )
    after = _attribute_rules(arcpy, path)
    matches = [item for item in after if item["name"] == name]
    if len(matches) != 1:
        raise RuntimeError("AddAttributeRule 已返回但无法验证新规则；不要自动重试")
    if not _same_rule_inventory(
        [item for item in after if item["name"] != name],
        before,
    ):
        raise RuntimeError("AddAttributeRule 后既有规则发生变化；不要自动重试")
    expected_type = {
        "CALCULATION": "esriARTCalculation",
        "CONSTRAINT": "esriARTConstraint",
        "VALIDATION": "esriARTValidation",
    }[kind]
    observed = matches[0]
    checks = {
        "type": expected_type,
        "batch": batch,
        "field_name": field_name,
        "severity": severity_value,
    }
    if kind == "CALCULATION":
        checks["is_editable"] = is_editable
    for key, expected_value in checks.items():
        actual_value = observed.get(key)
        if expected_value is None:
            matches_expected = actual_value in (None, "", [], ())
        else:
            matches_expected = actual_value is None or actual_value == expected_value
        if not matches_expected:
            raise RuntimeError(f"AddAttributeRule 后 {key} 与请求不一致；不要自动重试")
    return {
        "dataset_path": path,
        "added": True,
        "rule": observed,
        "messages": _messages(result),
        "verified": True,
    }


def delete_attribute_rules(
    arcpy: Any,
    dataset_path: str,
    rule_names: list[str],
    *,
    expected_dataset: str,
    confirmation: str,
    rule_type: str = "",
) -> dict[str, Any]:
    path = _mutation_target(arcpy, dataset_path, expected_dataset, confirmation)
    names = _name_list(rule_names, "rule_names", minimum=1, maximum=128, item_length=64)
    kind = _enum(rule_type, _RULE_TYPES, "rule_type") if rule_type else None
    before = _attribute_rules(arcpy, path)
    available = {item["name"] for item in before}
    missing = [name for name in names if name not in available]
    if missing:
        raise RuntimeError(f"属性规则不存在或大小写未精确匹配：{missing}")
    result = _management_tool(arcpy, "DeleteAttributeRule")(path, names, kind)
    after = _attribute_rules(arcpy, path)
    expected_after = [item for item in before if item["name"] not in set(names)]
    if not _same_rule_inventory(after, expected_after):
        raise RuntimeError("DeleteAttributeRule 后规则清单与预期差分不一致；不要自动重试")
    return {
        "dataset_path": path,
        "deleted_rule_names": names,
        "messages": _messages(result),
        "verified": True,
    }


def _field_names(value: Any) -> list[str]:
    if value is None:
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        else:
            result.append(str(_property(item, "name", default=item)))
    return result


def _field_group_payload(item: Any) -> dict[str, Any]:
    restrictive = _property(
        item,
        "isEditingRestrictive",
        "isRestrictive",
        "is_restrictive",
        default=None,
    )
    return {
        "name": str(_property(item, "name", default="") or ""),
        "fields": _field_names(_property(item, "fieldNames", "fields", default=[])),
        "is_restrictive": bool(restrictive) if restrictive is not None else None,
    }


def _field_groups(arcpy: Any, dataset_path: str) -> list[dict[str, Any]]:
    values = [
        _field_group_payload(item)
        for item in _describe_collection(arcpy, dataset_path, "fieldGroups")
    ]
    if any(not item["name"] for item in values):
        raise RuntimeError("Describe.fieldGroups 返回了缺少名称的字段组")
    return sorted(values, key=lambda item: item["name"].casefold())


def list_field_groups(arcpy: Any, dataset_path: str) -> dict[str, Any]:
    path = _dataset(arcpy, dataset_path)
    groups = _field_groups(arcpy, path)
    return {
        "dataset_path": path,
        "field_groups": groups,
        "field_group_count": len(groups),
        "source_api": "arcpy.Describe(...).fieldGroups",
        "verified": True,
    }


def _canonical_fields(arcpy: Any, dataset_path: str, values: list[str]) -> list[str]:
    requested = _name_list(values, "fields", minimum=2, maximum=32)
    lister = getattr(arcpy, "ListFields", None)
    if not callable(lister):
        raise RuntimeError("当前 ArcPy 不支持 ListFields")
    available: dict[str, str] = {}
    for field in lister(dataset_path) or []:
        field_name = str(getattr(field, "name", "") or "")
        if field_name:
            available[field_name.casefold()] = field_name
    missing = [name for name in requested if name.casefold() not in available]
    if missing:
        raise RuntimeError(f"字段不存在：{missing}")
    return [available[name.casefold()] for name in requested]


def create_field_group(
    arcpy: Any,
    dataset_path: str,
    field_group_name: str,
    fields: list[str],
    *,
    expected_dataset: str,
    confirmation: str,
    restrictive: bool = True,
) -> dict[str, Any]:
    path = _mutation_target(arcpy, dataset_path, expected_dataset, confirmation)
    name = _name(field_group_name, "field_group_name")
    if not isinstance(restrictive, bool):
        raise RuntimeError("restrictive 必须为布尔值")
    canonical = _canonical_fields(arcpy, path, fields)
    before = _field_groups(arcpy, path)
    if any(item["name"].casefold() == name.casefold() for item in before):
        raise RuntimeError(f"字段组已存在，拒绝隐式覆盖：{name}")
    result = _management_tool(arcpy, "CreateFieldGroup")(
        path,
        name,
        canonical,
        "RESTRICT" if restrictive else "DO_NOT_RESTRICT",
    )
    after = _field_groups(arcpy, path)
    matches = [item for item in after if item["name"] == name]
    if len(matches) != 1:
        raise RuntimeError("CreateFieldGroup 已返回但无法验证字段组；不要自动重试")
    if [item for item in after if item["name"] != name] != before:
        raise RuntimeError("CreateFieldGroup 后既有字段组发生变化；不要自动重试")
    observed = matches[0]
    if [value.casefold() for value in observed["fields"]] != [
        value.casefold() for value in canonical
    ] or observed["is_restrictive"] is not restrictive:
        raise RuntimeError("CreateFieldGroup 返回后的字段或 restrictive 状态不一致")
    return {
        "dataset_path": path,
        "created": True,
        "field_group": observed,
        "messages": _messages(result),
        "verified": True,
    }


def delete_field_group(
    arcpy: Any,
    dataset_path: str,
    field_group_name: str,
    *,
    expected_dataset: str,
    confirmation: str,
) -> dict[str, Any]:
    path = _mutation_target(arcpy, dataset_path, expected_dataset, confirmation)
    name = _name(field_group_name, "field_group_name")
    before = _field_groups(arcpy, path)
    if not any(item["name"] == name for item in before):
        raise RuntimeError("字段组不存在或大小写未精确匹配")
    before_values = _contingent_values(arcpy, path)
    result = _management_tool(arcpy, "DeleteFieldGroup")(path, name)
    after_groups = _field_groups(arcpy, path)
    expected_groups = [item for item in before if item["name"] != name]
    after_values = _contingent_values(arcpy, path)
    expected_values = [item for item in before_values if item["field_group_name"] != name]
    if after_groups != expected_groups or after_values != expected_values:
        raise RuntimeError("DeleteFieldGroup 后字段组/条件值差分不一致；不要自动重试")
    return {
        "dataset_path": path,
        "deleted_field_group": name,
        "messages": _messages(result),
        "verified": True,
    }


def _contingent_field_payload(item: Any) -> dict[str, Any]:
    range_value = _property(item, "range", default=None)
    return {
        "field": str(_property(item, "name", default="") or ""),
        "value_type": str(_property(item, "type", default="") or ""),
        "code": _jsonable(_property(item, "code", default=None)),
        "range": _jsonable(range_value),
    }


def _contingent_payload(item: Any) -> dict[str, Any]:
    raw_values = _property(item, "values", default=[])
    if callable(raw_values):
        raw_values = raw_values()
    return {
        "id": _jsonable(_property(item, "id")),
        "field_group_name": str(
            _property(item, "fieldGroupName", "field_group_name", default="") or ""
        ),
        "subtype": _jsonable(_property(item, "subtype", default=None)),
        "is_retired": bool(_property(item, "isRetired", "is_retired", default=False)),
        "values": [_contingent_field_payload(value) for value in (raw_values or [])],
    }


def _contingent_values(
    arcpy: Any,
    dataset_path: str,
    field_group_name: str = "",
    subtype_code: int | None = None,
) -> list[dict[str, Any]]:
    lister = getattr(getattr(arcpy, "da", None), "ListContingentValues", None)
    if not callable(lister):
        raise RuntimeError("当前 ArcPy 不支持 arcpy.da.ListContingentValues")
    if subtype_code is not None and not field_group_name:
        raise RuntimeError("按 subtype_code 筛选时必须同时指定 field_group_name")
    if subtype_code is not None:
        raw = lister(dataset_path, field_group_name, subtype_code)
    elif field_group_name:
        raw = lister(dataset_path, field_group_name)
    else:
        raw = lister(dataset_path)
    values = [_contingent_payload(item) for item in (raw or [])]
    return sorted(values, key=lambda item: str(item["id"]))


def list_contingent_values(
    arcpy: Any,
    dataset_path: str,
    field_group_name: str = "",
    subtype_code: int | None = None,
) -> dict[str, Any]:
    path = _dataset(arcpy, dataset_path)
    group = _name(field_group_name, "field_group_name") if field_group_name else ""
    subtype = (
        _integer(subtype_code, "subtype_code", minimum=0) if subtype_code is not None else None
    )
    values = _contingent_values(arcpy, path, group, subtype)
    return {
        "dataset_path": path,
        "field_group_name": group or None,
        "subtype_code": subtype,
        "contingent_values": values,
        "contingent_value_count": len(values),
        "source_api": "arcpy.da.ListContingentValues",
        "verified": True,
    }


def _coded_value(value: Any, label: str) -> str | int | float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RuntimeError(f"{label} 必须为字符串或有限数值")
    if isinstance(value, float) and not math.isfinite(value):
        raise RuntimeError(f"{label} 必须为有限数值")
    if isinstance(value, str):
        return _text(value, label, maximum=1024)
    return value


def _contingent_rows(values: list[dict[str, Any]]) -> tuple[list[list[Any]], list[str]]:
    if not isinstance(values, list) or not 1 <= len(values) <= 32:
        raise RuntimeError("values 必须包含 1–32 个条件值字段定义")
    rows: list[list[Any]] = []
    fields: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise RuntimeError(f"values[{index}] 必须为对象")
        value_type = _enum(
            str(item.get("value_type", "")),
            _CONTINGENT_VALUE_TYPES,
            f"values[{index}].value_type",
        )
        field = _name(item.get("field"), f"values[{index}].field")
        expected_keys = {"field", "value_type"}
        if value_type == "CODED_VALUE":
            expected_keys.add("value")
            encoded: Any = _coded_value(item.get("value"), f"values[{index}].value")
        elif value_type == "RANGE":
            expected_keys.update({"minimum", "maximum"})
            minimum = _finite(item.get("minimum"), f"values[{index}].minimum")
            maximum = _finite(item.get("maximum"), f"values[{index}].maximum")
            if minimum > maximum:
                raise RuntimeError(f"values[{index}].minimum 不得大于 maximum")
            # str(int) is exact and str(float) is Python's shortest
            # round-trippable representation.  ``:g`` would silently round
            # to six significant digits and can change domain endpoints.
            encoded = f"{minimum};{maximum}"
        else:
            encoded = None
        if set(item) != expected_keys:
            raise RuntimeError(
                f"values[{index}] 的键必须精确为 {sorted(expected_keys)}"
            )
        rows.append([field, value_type, encoded])
        fields.append(field)
    if len({field.casefold() for field in fields}) != len(fields):
        raise RuntimeError("values 不得重复定义字段")
    return rows, fields


def add_contingent_value(
    arcpy: Any,
    dataset_path: str,
    field_group_name: str,
    values: list[dict[str, Any]],
    *,
    expected_dataset: str,
    confirmation: str,
    subtype: str = "",
    retired: bool = False,
) -> dict[str, Any]:
    path = _mutation_target(arcpy, dataset_path, expected_dataset, confirmation)
    group = _name(field_group_name, "field_group_name")
    # AddContingentValue takes the subtype *name* (String).  This intentionally
    # differs from da.ListContingentValues, whose optional filter is a numeric
    # subtype code.
    subtype_name = _name(subtype, "subtype", maximum=128) if subtype else None
    if not isinstance(retired, bool):
        raise RuntimeError("retired 必须为布尔值")
    groups = [item for item in _field_groups(arcpy, path) if item["name"] == group]
    if len(groups) != 1:
        raise RuntimeError("field_group_name 不存在或大小写未精确匹配")
    rows, fields = _contingent_rows(values)
    if [value.casefold() for value in fields] != [
        value.casefold() for value in groups[0]["fields"]
    ]:
        raise RuntimeError("values 必须按字段组顺序精确覆盖全部字段")
    before = _contingent_values(arcpy, path, group)
    before_ids = {str(item["id"]) for item in before}
    result = _management_tool(arcpy, "AddContingentValue")(
        path,
        group,
        rows,
        subtype_name,
        "RETIRE" if retired else "DO_NOT_RETIRE",
    )
    after = _contingent_values(arcpy, path, group)
    added = [item for item in after if str(item["id"]) not in before_ids]
    if len(added) != 1:
        raise RuntimeError("AddContingentValue 已返回但无法唯一识别新增 ID；不要自动重试")
    if [item for item in after if str(item["id"]) in before_ids] != before:
        raise RuntimeError("AddContingentValue 后既有条件值发生变化；不要自动重试")
    return {
        "dataset_path": path,
        "added": True,
        "contingent_value": added[0],
        "messages": _messages(result),
        "verified": True,
    }


def remove_contingent_value(
    arcpy: Any,
    dataset_path: str,
    contingent_value_id: int,
    *,
    expected_dataset: str,
    confirmation: str,
) -> dict[str, Any]:
    path = _mutation_target(arcpy, dataset_path, expected_dataset, confirmation)
    identifier = _integer(contingent_value_id, "contingent_value_id", minimum=1)
    before = _contingent_values(arcpy, path)
    matches = [item for item in before if str(item["id"]) == str(identifier)]
    if len(matches) != 1:
        raise RuntimeError("contingent_value_id 不存在或不唯一")
    result = _management_tool(arcpy, "RemoveContingentValue")(path, identifier)
    after = _contingent_values(arcpy, path)
    expected_after = [item for item in before if str(item["id"]) != str(identifier)]
    if after != expected_after:
        raise RuntimeError("RemoveContingentValue 后条件值清单与预期差分不一致；不要自动重试")
    return {
        "dataset_path": path,
        "removed": True,
        "contingent_value": matches[0],
        "messages": _messages(result),
        "verified": True,
    }


def export_contingent_values(
    arcpy: Any,
    dataset_path: str,
    field_groups_csv: str,
    contingent_values_csv: str,
) -> dict[str, Any]:
    path = _dataset(arcpy, dataset_path)
    groups_output = _export_path(field_groups_csv, "field_groups_csv")
    values_output = _export_path(contingent_values_csv, "contingent_values_csv")
    if os.path.normcase(groups_output) == os.path.normcase(values_output):
        raise RuntimeError("field_groups_csv 与 contingent_values_csv 必须不同")
    result = _management_tool(arcpy, "ExportContingentValues")(
        path,
        groups_output,
        values_output,
    )
    return {
        "dataset_path": path,
        "outputs": {
            "field_groups_csv": _verify_export(arcpy, groups_output, "field_groups_csv"),
            "contingent_values_csv": _verify_export(
                arcpy,
                values_output,
                "contingent_values_csv",
            ),
        },
        "messages": _messages(result),
        "verified": True,
    }


def _observed_contingent_signature(
    item: dict[str, Any],
) -> tuple[str, str, int | None, bool]:
    raw_subtype = item.get("subtype")
    subtype = _csv_subtype(
        "" if raw_subtype is None else str(raw_subtype),
        "ListContingentValues subtype",
    )
    return (
        str(item.get("id")),
        str(item.get("field_group_name", "")).casefold(),
        subtype,
        bool(item.get("is_retired", False)),
    )


def import_contingent_values(
    arcpy: Any,
    dataset_path: str,
    field_groups_csv: str,
    contingent_values_csv: str,
    import_type: str = "UNION",
    *,
    expected_dataset: str,
    confirmation: str,
) -> dict[str, Any]:
    """Bulk-update using the real ``management.ImportContingentValues`` tool."""
    path = _mutation_target(arcpy, dataset_path, expected_dataset, confirmation)
    groups_source = _input_csv(field_groups_csv, "field_groups_csv")
    values_source = _input_csv(contingent_values_csv, "contingent_values_csv")
    if os.path.normcase(groups_source) == os.path.normcase(values_source):
        raise RuntimeError("field_groups_csv 与 contingent_values_csv 必须不同")
    mode = _enum(import_type, _CONTINGENT_IMPORT_TYPES, "import_type")
    manifest = _contingent_import_manifest(groups_source, values_source)
    before_groups = _field_groups(arcpy, path)
    before_values = _contingent_values(arcpy, path)
    result = _management_tool(arcpy, "ImportContingentValues")(
        path,
        groups_source,
        values_source,
        mode,
    )
    after_groups = _field_groups(arcpy, path)
    after_values = _contingent_values(arcpy, path)
    expected_groups = {
        item["name"].casefold(): item for item in manifest["field_groups"]
    }
    after_groups_by_name = {item["name"].casefold(): item for item in after_groups}
    missing_imported_groups = set(expected_groups) - set(after_groups_by_name)
    if missing_imported_groups:
        raise RuntimeError(
            "ImportContingentValues 已返回但 CSV 字段组未全部出现；不要自动重试"
        )
    for key, expected_group in expected_groups.items():
        observed_group = after_groups_by_name[key]
        if [value.casefold() for value in observed_group["fields"]] != [
            value.casefold() for value in expected_group["fields"]
        ] or observed_group["is_restrictive"] is not expected_group["is_restrictive"]:
            raise RuntimeError(
                "ImportContingentValues 后字段组结构与 CSV 清单不一致；不要自动重试"
            )
    for item in after_values:
        group_key = str(item["field_group_name"]).casefold()
        observed_group = after_groups_by_name.get(group_key)
        if observed_group is None:
            raise RuntimeError("ImportContingentValues 后条件值引用了不存在的字段组")
        observed_fields = [str(value["field"]).casefold() for value in item["values"]]
        expected_fields = [str(value).casefold() for value in observed_group["fields"]]
        if observed_fields != expected_fields:
            raise RuntimeError("ImportContingentValues 后条件值字段顺序与字段组不一致")

    expected_signatures = Counter(manifest["contingent_signatures"])
    after_signatures = Counter(_observed_contingent_signature(item) for item in after_values)
    if mode == "UNION":
        before_groups_by_name = {item["name"].casefold(): item for item in before_groups}
        for key, before_group in before_groups_by_name.items():
            if after_groups_by_name.get(key) != before_group:
                raise RuntimeError("UNION 导入后既有字段组发生变化；不要自动重试")
        before_by_id = {str(item["id"]): item for item in before_values}
        after_by_id = {str(item["id"]): item for item in after_values}
        for identifier, before_value in before_by_id.items():
            if after_by_id.get(identifier) != before_value:
                raise RuntimeError("UNION 导入后既有条件值发生变化或消失；不要自动重试")
        new_values = [item for item in after_values if str(item["id"]) not in before_by_id]
        new_signatures = Counter(
            _observed_contingent_signature(item) for item in new_values
        )
        if any(
            count > expected_signatures[signature]
            for signature, count in new_signatures.items()
        ):
            raise RuntimeError("UNION 导入生成了 CSV 清单之外的条件值；不要自动重试")
    else:
        if set(after_groups_by_name) != set(expected_groups):
            raise RuntimeError("REPLACE 后字段组集合与 CSV 清单不一致；不要自动重试")
        if after_signatures != expected_signatures:
            raise RuntimeError("REPLACE 后条件值清单与 CSV 不一致；不要自动重试")
    return {
        "dataset_path": path,
        "import_type": mode,
        "field_group_count_before": len(before_groups),
        "field_group_count_after": len(after_groups),
        "contingent_value_count_before": len(before_values),
        "contingent_value_count_after": len(after_values),
        "manifest_field_group_count": len(expected_groups),
        "manifest_contingent_value_count": sum(expected_signatures.values()),
        "verification": "official_csv_manifest_and_post_state",
        "messages": _messages(result),
        "verified": True,
    }
