"""Typed project import/export, connection repair, and report helpers.

The helpers in this module deliberately operate on already-resolved ``arcpy.mp``
objects.  Server-facing path and identity selection stays explicit, while every
filesystem input/output is still checked against the central path policy.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from arcgis_pro_mcp.paths import (
    normalize_path,
    require_allow_destructive,
    require_allow_write,
    validate_input_path_optional,
    validate_output_in_export_root,
)
from arcgis_pro_mcp.redaction import redact_sensitive

_IMPORT_EXTENSIONS = frozenset({".mxd", ".3dd", ".sxd", ".mapx", ".pagx", ".rptx"})
_PASTE_PROPERTIES = frozenset(
    {
        "CHARTS",
        "DEFINITION_QUERIES",
        "DISPLAY_EXPRESSION",
        "DISPLAY_FILTERS",
        "FIELD_PROPERTIES",
        "LABELING",
        "POPUPS",
        "SYMBOLOGY",
        "VISIBILITY_RANGE",
    }
)
_REPORT_TEMPLATES = frozenset(
    {"ATTR_LIST", "ATTR_LIST_GROUP", "BASIC_SUM", "BASIC_SUM_GROUP", "PAGE_PER_FEATURE"}
)
_REPORT_STYLES = frozenset({"BLACK_AND_WHITE", "COOL_TONES", "WARM_TONES", "NO_STYLING"})
_PAGE_UNITS = frozenset({"CENTIMETER", "INCH", "MILLIMETER", "POINT"})
_PAGE_MARGINS = frozenset({"NORMAL", "NARROW", "MODERATE", "WIDE"})
_SORT_ORDERS = frozenset({"ASC", "DESC", "NONE"})
_STATISTICS = frozenset({"COUNT", "MEAN", "MEDIAN", "SUM", "STD_DEV", "MAX", "MIN"})
_REPAIR_SECRET = secrets.token_bytes(32)
_REPAIR_TOKEN_TTL_SECONDS = 300
_MAX_REPAIRS = 100


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign_repair(payload: dict[str, Any]) -> str:
    body = _json_bytes(payload)
    signature = hmac.new(_REPAIR_SECRET, body, hashlib.sha256).digest()
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def _verify_repair(token: str) -> dict[str, Any]:
    try:
        body_text, signature_text = token.split(".", 1)
        body = _b64decode(body_text)
        signature = _b64decode(signature_text)
        expected = hmac.new(_REPAIR_SECRET, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise RuntimeError("repair_token 签名无效")
        payload = json.loads(body.decode("utf-8"))
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("repair_token 格式无效") from exc
    if float(payload.get("expires_at", 0)) < time.time():
        raise RuntimeError("repair_token 已过期，请重新预检")
    return payload


def redact_connection(value: Any) -> Any:
    """Return JSON-safe connection information without credentials."""
    return redact_sensitive(value)


def _required_text(value: Any, label: str, maximum: int = 512) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"{label} 不能为空")
    if len(text) > maximum or "\x00" in text:
        raise RuntimeError(f"{label} 无效或过长")
    return text


def _require_method(target: Any, name: str, label: str) -> Any:
    method = getattr(target, name, None)
    if not callable(method):
        raise RuntimeError(f"当前 ArcGIS Pro/对象不支持 {label}（缺少 {name}）")
    return method


def _identity(item: Any) -> dict[str, str]:
    return {
        "name": str(getattr(item, "name", "") or ""),
        "uri": str(getattr(item, "URI", "") or ""),
        "long_name": str(getattr(item, "longName", "") or ""),
        "type": type(item).__name__,
    }


def _prepare_output(
    output_path: str,
    extension: str,
    *,
    overwrite: bool,
    confirm_overwrite_path: str,
) -> str:
    output = validate_output_in_export_root(output_path, "output_path")
    if Path(output).suffix.lower() != extension:
        raise RuntimeError(f"output_path 必须以 {extension} 结尾")
    if os.path.exists(output):
        require_allow_destructive()
        if not overwrite:
            raise RuntimeError("输出已存在；overwrite=true 并精确回显 confirm_overwrite_path 才能覆盖")
        if normalize_path(confirm_overwrite_path) != normalize_path(output):
            raise RuntimeError("confirm_overwrite_path 必须精确回显 output_path")
    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return output


def import_document(
    project: Any,
    document_path: str,
    *,
    include_layout: bool = True,
    reuse_existing_maps: bool = False,
    log_files: bool = False,
) -> dict[str, Any]:
    require_allow_write()
    source = validate_input_path_optional(document_path, "document_path")
    extension = Path(source).suffix.lower()
    if extension not in _IMPORT_EXTENSIONS:
        raise RuntimeError(
            "document_path 须为 .mxd/.3dd/.sxd/.mapx/.pagx/.rptx"
        )
    if log_files:
        home = str(getattr(project, "homeFolder", "") or "")
        if not home:
            raise RuntimeError("启用 log_files 时无法确定工程 homeFolder")
        validate_output_in_export_root(
            os.path.join(home, "ImportLog"),
            "project ImportLog directory",
        )
    before = project_inventory(project)
    method = _require_method(project, "importDocument", "importDocument")
    imported = method(
        source,
        bool(include_layout),
        bool(reuse_existing_maps),
        bool(log_files),
    )
    after = project_inventory(project)
    return {
        "document_path": normalize_path(source),
        "document_type": extension.removeprefix(".").upper(),
        "imported": (
            [_identity(item) for item in imported]
            if isinstance(imported, (list, tuple))
            else (_identity(imported) if imported is not None else None)
        ),
        "added": {
            key: [item for item in after[key] if item not in before[key]]
            for key in ("maps", "layouts", "reports")
        },
        "log_files": bool(log_files),
    }


def project_inventory(project: Any) -> dict[str, list[dict[str, str]]]:
    return {
        "maps": [_identity(item) for item in list(project.listMaps() or [])],
        "layouts": [_identity(item) for item in list(project.listLayouts() or [])],
        "reports": [
            _identity(item)
            for item in list(getattr(project, "listReports", lambda: [])() or [])
        ],
    }


def export_mapx(
    map_object: Any,
    output_path: str,
    *,
    overwrite: bool = False,
    confirm_overwrite_path: str = "",
) -> dict[str, Any]:
    output = _prepare_output(
        output_path,
        ".mapx",
        overwrite=overwrite,
        confirm_overwrite_path=confirm_overwrite_path,
    )
    _require_method(map_object, "exportToMAPX", "exportToMAPX")(output)
    if not os.path.isfile(output):
        raise RuntimeError("exportToMAPX 返回后未找到输出文件")
    return {"map": _identity(map_object), "output_path": output, "bytes": os.path.getsize(output)}


def save_layer_file(
    item: Any,
    output_path: str,
    *,
    overwrite: bool = False,
    confirm_overwrite_path: str = "",
) -> dict[str, Any]:
    output = _prepare_output(
        output_path,
        ".lyrx",
        overwrite=overwrite,
        confirm_overwrite_path=confirm_overwrite_path,
    )
    _require_method(item, "saveACopy", "保存 Layer File")(output)
    if not os.path.isfile(output):
        raise RuntimeError("saveACopy 返回后未找到 .lyrx 输出")
    return {"item": _identity(item), "output_path": output, "bytes": os.path.getsize(output)}


def paste_layer_properties(
    target_layer: Any,
    source_layer: Any,
    properties: list[str],
) -> dict[str, Any]:
    require_allow_write()
    if not properties:
        raise RuntimeError("properties 不能为空；必须显式列出要复制的语义属性")
    normalized = []
    for raw in properties:
        value = _required_text(raw, "property", 64).upper()
        if value == "ALL" or value not in _PASTE_PROPERTIES:
            raise RuntimeError(
                "properties 仅允许显式选择：" + ", ".join(sorted(_PASTE_PROPERTIES))
            )
        if value not in normalized:
            normalized.append(value)
    _require_method(target_layer, "pasteProperties", "pasteProperties")(
        source_layer, normalized
    )
    return {
        "target": _identity(target_layer),
        "source": _identity(source_layer),
        "properties": normalized,
    }


def _connection_info(item: Any) -> dict[str, Any]:
    properties = copy.deepcopy(getattr(item, "connectionProperties", None))
    return {
        "identity": _identity(item),
        "is_broken": bool(getattr(item, "isBroken", False)),
        "connection_properties": redact_connection(properties),
        # Bind the preflight to the complete value while never returning secrets.
        "connection_digest": _digest(properties),
    }


def replace_item_connection(
    item: Any,
    current_workspace: str,
    new_workspace: str,
    *,
    new_dataset_name: str = "",
    auto_update_joins_and_relates: bool = True,
    validate: bool = True,
    ignore_case: bool = False,
) -> dict[str, Any]:
    require_allow_write()
    current = validate_input_path_optional(current_workspace, "current_workspace")
    replacement = validate_input_path_optional(new_workspace, "new_workspace")
    before = _connection_info(item)
    old_connection: Any = current
    new_connection: Any = replacement
    dataset = new_dataset_name.strip()
    raw = copy.deepcopy(getattr(item, "connectionProperties", None))
    if dataset:
        if not isinstance(raw, dict):
            raise RuntimeError("指定 new_dataset_name 时对象必须公开 connectionProperties 字典")
        old_connection = raw
        new_connection = copy.deepcopy(raw)
        connection_info = new_connection.get("connection_info")
        if isinstance(connection_info, dict):
            for key in ("database", "server", "instance"):
                if key in connection_info and key == "database":
                    connection_info[key] = replacement
        else:
            new_connection["connection_info"] = {"database": replacement}
        new_connection["dataset"] = _required_text(dataset, "new_dataset_name", 1024)
    _require_method(item, "updateConnectionProperties", "更新数据源")(
        old_connection,
        new_connection,
        bool(auto_update_joins_and_relates),
        bool(validate),
        bool(ignore_case),
    )
    after = _connection_info(item)
    if validate and after["is_broken"]:
        rollback_error = ""
        try:
            _require_method(item, "updateConnectionProperties", "回滚数据源")(
                new_connection,
                old_connection,
                bool(auto_update_joins_and_relates),
                False,
                bool(ignore_case),
            )
        except Exception as exc:  # noqa: BLE001
            rollback_error = f"；自动回滚也失败：{str(exc)[:300]}"
        raise RuntimeError(
            "更新数据源后对象仍为 broken，已尝试恢复原连接" + rollback_error
        )
    return {"before": before, "after": after, "validated": bool(validate)}


def _object_lookup(project: Any) -> dict[str, Any | None]:
    lookup: dict[str, Any | None] = {}
    for map_object in list(project.listMaps() or []):
        map_aliases = {
            value
            for value in (
                str(getattr(map_object, "URI", "") or ""),
                str(getattr(map_object, "name", "") or ""),
            )
            if value
        }
        for item in list(map_object.listLayers() or []) + list(map_object.listTables() or []):
            identity = _identity(item)
            item_aliases = {
                value
                for value in (identity["uri"], identity["long_name"], identity["name"])
                if value
            }
            for map_alias in map_aliases:
                for item_alias in item_aliases:
                    key = f"{map_alias}\n{item_alias}"
                    if key in lookup and lookup[key] is not item:
                        lookup[key] = None
                    else:
                        lookup[key] = item
    return lookup


def _normalize_repairs(project: Any, repairs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not repairs or len(repairs) > _MAX_REPAIRS:
        raise RuntimeError(f"repairs 必须为 1–{_MAX_REPAIRS} 项数组")
    lookup = _object_lookup(project)
    normalized: list[dict[str, Any]] = []
    plan: list[dict[str, Any]] = []
    for index, raw in enumerate(repairs):
        if not isinstance(raw, dict):
            raise RuntimeError(f"repairs[{index}] 必须为对象")
        map_identifier = _required_text(raw.get("map_identifier"), f"repairs[{index}].map_identifier")
        item_identifier = _required_text(raw.get("item_identifier"), f"repairs[{index}].item_identifier")
        current = validate_input_path_optional(
            _required_text(raw.get("current_workspace"), f"repairs[{index}].current_workspace", 32767),
            f"repairs[{index}].current_workspace",
        )
        replacement = validate_input_path_optional(
            _required_text(raw.get("new_workspace"), f"repairs[{index}].new_workspace", 32767),
            f"repairs[{index}].new_workspace",
        )
        key = f"{map_identifier}\n{item_identifier}"
        item = lookup.get(key)
        if item is None:
            raise RuntimeError(
                f"repairs[{index}] 未唯一定位对象；map_identifier/item_identifier 应使用 URI，"
                "或在名称唯一时使用精确名称"
            )
        operation = {
            "map_identifier": map_identifier,
            "item_identifier": item_identifier,
            "current_workspace": normalize_path(current),
            "new_workspace": normalize_path(replacement),
            "new_dataset_name": str(raw.get("new_dataset_name") or "").strip(),
            "auto_update_joins_and_relates": bool(raw.get("auto_update_joins_and_relates", True)),
            "validate": bool(raw.get("validate", True)),
            "ignore_case": bool(raw.get("ignore_case", False)),
        }
        before = _connection_info(item)
        normalized.append(operation)
        plan.append({"index": index, "target": before, "replacement": redact_connection(operation)})
    return normalized, plan


def connection_repair_preflight(project: Any, repairs: list[dict[str, Any]]) -> dict[str, Any]:
    normalized, plan = _normalize_repairs(project, repairs)
    issued_at = time.time()
    payload = {
        "operations_digest": _digest(normalized),
        "target_digest": _digest([item["target"] for item in plan]),
        "issued_at": issued_at,
        "expires_at": issued_at + _REPAIR_TOKEN_TTL_SECONDS,
    }
    return {
        "plan": plan,
        "repair_token": _sign_repair(payload),
        "expires_at": payload["expires_at"],
    }


def connection_repair_apply(
    project: Any,
    repairs: list[dict[str, Any]],
    repair_token: str,
) -> dict[str, Any]:
    require_allow_write()
    payload = _verify_repair(repair_token)
    normalized, plan = _normalize_repairs(project, repairs)
    if payload.get("operations_digest") != _digest(normalized):
        raise RuntimeError("repairs 已在预检后改变")
    if payload.get("target_digest") != _digest([item["target"] for item in plan]):
        raise RuntimeError("目标连接在预检后改变；本次未写入")
    lookup = _object_lookup(project)
    results: list[dict[str, Any]] = []
    for operation in normalized:
        item = lookup[f"{operation['map_identifier']}\n{operation['item_identifier']}"]
        if item is None:  # guarded by the repeated normalization above
            raise RuntimeError("目标连接对象不再唯一；本次未继续写入")
        result = replace_item_connection(
            item,
            operation["current_workspace"],
            operation["new_workspace"],
            new_dataset_name=operation["new_dataset_name"],
            auto_update_joins_and_relates=operation["auto_update_joins_and_relates"],
            validate=operation["validate"],
            ignore_case=operation["ignore_case"],
        )
        results.append(result)
    return {
        "repaired_count": len(results),
        "results": results,
        "atomic": False,
        "note": "ArcPy 不提供跨多个图层/表连接的原子事务；失败时请重新读取目标状态",
    }


def add_relate(
    arcpy: Any,
    input_layer_or_view: Any,
    input_field: str,
    relate_table: Any,
    relate_field: str,
    relate_name: str,
    cardinality: str = "ONE_TO_MANY",
) -> dict[str, Any]:
    require_allow_write()
    name = _required_text(relate_name, "relate_name", 128)
    card = _required_text(cardinality, "cardinality", 32).upper()
    if card not in {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_MANY"}:
        raise RuntimeError("cardinality 须为 ONE_TO_ONE/ONE_TO_MANY/MANY_TO_MANY")
    result = arcpy.management.AddRelate(
        input_layer_or_view,
        _required_text(input_field, "input_field", 128),
        relate_table,
        _required_text(relate_field, "relate_field", 128),
        name,
        card,
    )
    return {
        "relate_name": name,
        "cardinality": card,
        "messages": str(getattr(result, "getMessages", lambda: "")() or "")[:4000],
        "persistent": False,
        "note": "AddRelate 创建图层/表视图属性；永久关系请使用 CreateRelationshipClass",
    }


def remove_relate(
    arcpy: Any,
    input_layer_or_view: Any,
    relate_name: str,
    *,
    confirm_relate_name: str,
) -> dict[str, Any]:
    require_allow_destructive()
    name = _required_text(relate_name, "relate_name", 128)
    if confirm_relate_name != name:
        raise RuntimeError("confirm_relate_name 必须精确回显 relate_name")
    result = arcpy.management.RemoveRelate(input_layer_or_view, name)
    return {
        "relate_name": name,
        "removed": True,
        "messages": str(getattr(result, "getMessages", lambda: "")() or "")[:4000],
    }


def list_transformations(
    arcpy: Any,
    from_wkid: int,
    to_wkid: int,
    *,
    extent: list[float] | None = None,
    vertical: bool = False,
    first_only: bool = False,
) -> dict[str, Any]:
    source = arcpy.SpatialReference(int(from_wkid))
    target = arcpy.SpatialReference(int(to_wkid))
    area = None
    if extent is not None:
        if len(extent) != 4:
            raise RuntimeError("extent 必须为 [xmin, ymin, xmax, ymax]")
        values = [float(value) for value in extent]
        if values[0] >= values[2] or values[1] >= values[3]:
            raise RuntimeError("extent 必须满足 xmin < xmax 且 ymin < ymax")
        area = arcpy.Extent(*values)
        area.spatialReference = source
    values = list(arcpy.ListTransformations(source, target, area, bool(vertical), bool(first_only)) or [])
    return {
        "from_wkid": int(getattr(source, "factoryCode", from_wkid)),
        "to_wkid": int(getattr(target, "factoryCode", to_wkid)),
        "vertical": bool(vertical),
        "transformations": [str(value) for value in values],
        "recommended": str(values[0]) if values else None,
    }


def _validate_report_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not fields:
        raise RuntimeError("fields 不能为空")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(fields):
        if not isinstance(raw, dict) or set(raw) - {"fieldName", "sortInfo", "groupField"}:
            raise RuntimeError(f"fields[{index}] 仅允许 fieldName/sortInfo/groupField")
        name = _required_text(raw.get("fieldName"), f"fields[{index}].fieldName", 256)
        order = str(raw.get("sortInfo") or "NONE").strip().upper()
        if order not in _SORT_ORDERS:
            raise RuntimeError(f"fields[{index}].sortInfo 须为 ASC/DESC/NONE")
        result.append(
            {"fieldName": name, "sortInfo": order, "groupField": bool(raw.get("groupField", False))}
        )
    return result


def _validate_report_statistics(statistics: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, raw in enumerate(statistics):
        if not isinstance(raw, dict) or set(raw) != {"fieldName", "statistic"}:
            raise RuntimeError(f"statistics[{index}] 必须且只可包含 fieldName/statistic")
        name = _required_text(raw["fieldName"], f"statistics[{index}].fieldName", 256)
        statistic = _required_text(raw["statistic"], f"statistics[{index}].statistic", 32).upper()
        if statistic not in _STATISTICS:
            raise RuntimeError(f"statistics[{index}].statistic 不受支持")
        result.append({"fieldName": name, "statistic": statistic})
    return result


def create_report(
    project: Any,
    data_source: Any,
    *,
    name: str,
    fields: list[dict[str, Any]],
    statistics: list[dict[str, Any]] | None = None,
    width: float = 8.5,
    height: float = 11.0,
    units: str = "INCH",
    margins: str = "NORMAL",
    template: str = "ATTR_LIST",
    styling: str = "BLACK_AND_WHITE",
) -> dict[str, Any]:
    require_allow_write()
    report_name = _required_text(name, "name", 256)
    unit = _required_text(units, "units", 32).upper()
    margin = _required_text(margins, "margins", 32).upper()
    report_template = _required_text(template, "template", 64).upper()
    style = _required_text(styling, "styling", 64).upper()
    if unit not in _PAGE_UNITS or margin not in _PAGE_MARGINS:
        raise RuntimeError("units/margins 不受支持")
    if report_template not in _REPORT_TEMPLATES or style not in _REPORT_STYLES:
        raise RuntimeError("template/styling 不受支持")
    page_info = {
        "width": float(width),
        "height": float(height),
        "units": unit,
        "margins": margin,
    }
    if page_info["width"] <= 0 or page_info["height"] <= 0:
        raise RuntimeError("width/height 必须大于 0")
    report = _require_method(project, "createReport", "createReport")(
        page_info,
        data_source,
        _validate_report_fields(fields),
        _validate_report_statistics(statistics or []),
        report_name,
        report_template,
        style,
    )
    return {"report": _identity(report), "page_info": page_info, "template": report_template, "styling": style}


def report_sections(report: Any) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    for section in list(_require_method(report, "listSections", "listSections")() or []):
        section_type = str(getattr(section, "type", "") or "")
        item: dict[str, Any] = {
            "name": str(getattr(section, "name", "") or ""),
            "type": section_type,
            "visible": bool(getattr(section, "visible", True)),
        }
        if section_type == "REPORT_SECTION":
            item.update(
                {
                    "definition_query": str(getattr(section, "definitionQuery", "") or ""),
                    "fields": redact_connection(getattr(section, "fields", [])),
                    "statistics": redact_connection(getattr(section, "statistics", [])),
                    "reference_data_source": redact_connection(
                        getattr(section, "referenceDataSource", None)
                    ),
                }
            )
        sections.append(item)
    return {"report": _identity(report), "sections": sections}


def update_report_section(
    report: Any,
    section_name: str,
    *,
    data_source: Any | None = None,
    definition_query: str | None = None,
    visible: bool | None = None,
) -> dict[str, Any]:
    require_allow_write()
    name = _required_text(section_name, "section_name", 256)
    matches = [item for item in list(report.listSections() or []) if str(getattr(item, "name", "")) == name]
    if len(matches) != 1:
        raise RuntimeError(f"section_name 必须唯一命中，实际 {len(matches)} 项")
    section = matches[0]
    if str(getattr(section, "type", "")) != "REPORT_SECTION":
        raise RuntimeError("只能更新 REPORT_SECTION，不能把布局节当作数据节")
    if data_source is not None:
        _require_method(section, "setReferenceDataSource", "setReferenceDataSource")(data_source)
    if definition_query is not None:
        if len(definition_query) > 8000:
            raise RuntimeError("definition_query 过长")
        section.definitionQuery = definition_query
    if visible is not None:
        section.visible = bool(visible)
    return report_sections(report)
