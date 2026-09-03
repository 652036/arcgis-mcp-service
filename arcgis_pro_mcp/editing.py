"""Bounded, single-call edit preflight and atomic dataset writes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from arcgis_pro_mcp import da_write
from arcgis_pro_mcp.paths import (
    normalize_path,
    require_allow_destructive,
    require_allow_write,
    validate_input_path_optional,
)

_TOKEN_SECRET = secrets.token_bytes(32)
_TOKEN_TTL_SECONDS = 300
_MAX_OPERATIONS = 50
_MAX_PREFLIGHT_ROWS = 5000
_MAX_GEOMETRY_UPDATES = 1000
_MAX_GEOMETRY_TEXT_BYTES = 5_000_000


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: dict[str, Any]) -> str:
    body = _canonical(payload)
    signature = hmac.new(_TOKEN_SECRET, body, hashlib.sha256).digest()
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def _verify_token(token: str) -> dict[str, Any]:
    try:
        body_text, signature_text = token.split(".", 1)
        body = _b64decode(body_text)
        signature = _b64decode(signature_text)
        expected = hmac.new(_TOKEN_SECRET, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise RuntimeError("edit_token 签名无效")
        payload = json.loads(body.decode("utf-8"))
    except RuntimeError:
        raise
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError("edit_token 格式无效") from ex
    if float(payload.get("expires_at", 0)) < time.time():
        raise RuntimeError("edit_token 已过期，请重新执行 edit_preflight")
    return payload


def _validated_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not operations:
        raise RuntimeError("operations 不能为空")
    if len(operations) > _MAX_OPERATIONS:
        raise RuntimeError(f"单次编辑最多 {_MAX_OPERATIONS} 个 operation")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(operations):
        if not isinstance(raw, dict):
            raise RuntimeError(f"operations[{index}] 必须为对象")
        op = dict(raw)
        kind = str(op.get("kind") or "").strip().lower()
        if kind not in {"update", "update_geometry", "insert", "delete"}:
            raise RuntimeError(
                f"operations[{index}].kind 须为 update/update_geometry/insert/delete"
            )
        op["kind"] = kind
        if kind in {"update", "delete"}:
            where = str(op.get("where_clause") or "").strip()
            if not where:
                raise RuntimeError(f"operations[{index}].where_clause 不能为空")
            op["where_clause"] = where
        if kind == "update" and not isinstance(op.get("updates"), dict):
            raise RuntimeError(f"operations[{index}].updates 必须为对象")
        if kind == "insert":
            if not isinstance(op.get("fields"), list) or not isinstance(op.get("rows"), list):
                raise RuntimeError(f"operations[{index}] 插入操作必须提供 fields 与 rows 数组")
        if kind == "update_geometry":
            token = str(op.get("geometry_token") or "").strip().upper()
            if token not in {"SHAPE@WKT", "SHAPE@JSON"}:
                raise RuntimeError(
                    f"operations[{index}].geometry_token 须为 SHAPE@WKT 或 SHAPE@JSON"
                )
            rows = op.get("rows")
            if not isinstance(rows, list) or not rows or len(rows) > _MAX_GEOMETRY_UPDATES:
                raise RuntimeError(
                    f"operations[{index}].rows 必须为 1–{_MAX_GEOMETRY_UPDATES} 项数组"
                )
            seen: set[int] = set()
            text_bytes = 0
            normalized_rows: list[dict[str, Any]] = []
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict) or set(row) != {"oid", "geometry"}:
                    raise RuntimeError(
                        f"operations[{index}].rows[{row_index}] 只允许 oid 和 geometry"
                    )
                try:
                    oid = int(row["oid"])
                except (TypeError, ValueError) as ex:
                    raise RuntimeError(
                        f"operations[{index}].rows[{row_index}].oid 必须为整数"
                    ) from ex
                geometry = row["geometry"]
                if not isinstance(geometry, str) or not geometry.strip():
                    raise RuntimeError(
                        f"operations[{index}].rows[{row_index}].geometry 必须为非空字符串"
                    )
                if oid in seen:
                    raise RuntimeError(f"operations[{index}] 包含重复 oid={oid}")
                seen.add(oid)
                text_bytes += len(geometry.encode("utf-8"))
                normalized_rows.append({"oid": oid, "geometry": geometry})
            if text_bytes > _MAX_GEOMETRY_TEXT_BYTES:
                raise RuntimeError(
                    f"operations[{index}] geometry 总大小超过 {_MAX_GEOMETRY_TEXT_BYTES} bytes"
                )
            op["geometry_token"] = token
            op["rows"] = normalized_rows
        normalized.append(op)
    return normalized


def _oid_field(arcpy: Any, dataset: str) -> str:
    desc = arcpy.Describe(dataset)
    oid = str(getattr(desc, "OIDFieldName", "") or "")
    if not oid:
        raise RuntimeError("数据集没有 OID 字段，无法建立稳定编辑预检")
    return oid


def _selected_oids(arcpy: Any, dataset: str, where_clause: str) -> list[int]:
    oid = _oid_field(arcpy, dataset)
    values: list[int] = []
    with arcpy.da.SearchCursor(dataset, [oid], where_clause) as cursor:
        for row in cursor:
            if len(values) >= _MAX_PREFLIGHT_ROWS:
                raise RuntimeError(f"预检命中超过 {_MAX_PREFLIGHT_ROWS} 行，请缩小 where_clause")
            values.append(int(row[0]))
    values.sort()
    return values


def _oid_where_clause(arcpy: Any, dataset: str, oid_field: str, oids: list[int]) -> str:
    delimiter = getattr(arcpy, "AddFieldDelimiters", None)
    field = delimiter(dataset, oid_field) if callable(delimiter) else oid_field
    return f"{field} IN ({','.join(str(value) for value in oids)})"


def _existing_target_oids(arcpy: Any, dataset: str, oids: list[int]) -> list[int]:
    if not oids:
        return []
    oid_field = _oid_field(arcpy, dataset)
    where = _oid_where_clause(arcpy, dataset, oid_field, oids)
    values: list[int] = []
    with arcpy.da.SearchCursor(dataset, [oid_field], where) as cursor:
        values.extend(int(row[0]) for row in cursor)
    values.sort()
    return values


def _operation_plan(arcpy: Any, dataset: str, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = {field.name: field for field in arcpy.ListFields(dataset)}
    plan: list[dict[str, Any]] = []
    for index, op in enumerate(operations):
        kind = op["kind"]
        item: dict[str, Any] = {"index": index, "kind": kind}
        if kind in {"update", "delete"}:
            oids = _selected_oids(arcpy, dataset, op["where_clause"])
            expected_count = op.get("expected_count")
            if expected_count is not None and int(expected_count) != len(oids):
                raise RuntimeError(
                    f"operations[{index}] expected_count={expected_count}，实际命中 {len(oids)} 行"
                )
            limit = int(op.get("max_rows", _MAX_PREFLIGHT_ROWS))
            if limit < len(oids):
                raise RuntimeError(
                    f"operations[{index}] 命中 {len(oids)} 行，超过 max_rows={limit}；不会截断执行"
                )
            item.update(
                {
                    "where_clause": op["where_clause"],
                    "affected_count": len(oids),
                    "oid_sample": oids[:50],
                    "oid_digest": hashlib.sha256(_canonical(oids)).hexdigest(),
                }
            )
            if kind == "update":
                updates = op["updates"]
                for field_name in updates:
                    field = fields.get(field_name)
                    if field is None:
                        raise RuntimeError(f"operations[{index}] 未知字段：{field_name}")
                    if getattr(field, "type", "") in {"OID", "Geometry", "Raster"}:
                        raise RuntimeError(f"operations[{index}] 不允许更新字段：{field_name}")
                item["fields"] = sorted(updates)
        elif kind == "insert":
            field_names = [str(value).strip() for value in op["fields"]]
            rows = op["rows"]
            if len(rows) > _MAX_PREFLIGHT_ROWS:
                raise RuntimeError(f"operations[{index}] 插入超过 {_MAX_PREFLIGHT_ROWS} 行")
            for field_name in field_names:
                if field_name.upper() not in {"SHAPE@WKT", "SHAPE@JSON"} and field_name not in fields:
                    raise RuntimeError(f"operations[{index}] 未知字段：{field_name}")
            item.update({"affected_count": len(rows), "fields": field_names})
        else:
            target_oids = sorted(row["oid"] for row in op["rows"])
            existing_oids = _existing_target_oids(arcpy, dataset, target_oids)
            if existing_oids != target_oids:
                missing = sorted(set(target_oids) - set(existing_oids))
                raise RuntimeError(
                    f"operations[{index}] geometry 目标 OID 不存在：{missing[:50]}"
                )
            expected_count = op.get("expected_count")
            if expected_count is not None and int(expected_count) != len(target_oids):
                raise RuntimeError(
                    f"operations[{index}] expected_count={expected_count}，"
                    f"实际 geometry 目标 {len(target_oids)} 行"
                )
            item.update(
                {
                    "affected_count": len(target_oids),
                    "fields": [op["geometry_token"]],
                    "oid_sample": target_oids[:50],
                    "oid_digest": hashlib.sha256(_canonical(target_oids)).hexdigest(),
                }
            )
        plan.append(item)
    return plan


def _plan_state(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "index": item["index"],
            "kind": item["kind"],
            "affected_count": item["affected_count"],
            "oid_digest": item.get("oid_digest", ""),
        }
        for item in plan
    ]


def edit_preflight(
    arcpy: Any,
    dataset_path: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    dataset = validate_input_path_optional(dataset_path, "dataset_path")
    normalized = _validated_operations(operations)
    plan = _operation_plan(arcpy, dataset, normalized)
    issued_at = time.time()
    payload = {
        "scope": "dataset",
        "dataset": normalize_path(dataset),
        "operations_digest": hashlib.sha256(_canonical(normalized)).hexdigest(),
        "plan": _plan_state(plan),
        "issued_at": issued_at,
        "expires_at": issued_at + _TOKEN_TTL_SECONDS,
    }
    return {
        "ok": True,
        "dataset_path": normalize_path(dataset),
        "plan": plan,
        "total_affected": sum(item["affected_count"] for item in plan),
        "edit_token": _sign(payload),
        "expires_at": payload["expires_at"],
        "native_pro_undo": False,
        "note": "token 仅适用于同一 MCP/窗口宿主进程；CURRENT 原生 Undo/Redo 需要 SDK Add-in",
    }


def _workspace_for_dataset(arcpy: Any, dataset: str) -> tuple[str, bool]:
    desc = arcpy.Describe(dataset)
    workspace = str(getattr(desc, "path", "") or "")
    if not workspace:
        raise RuntimeError("无法确定数据集工作空间，不能启动原子编辑会话")
    for _ in range(4):
        workspace_desc = arcpy.Describe(workspace)
        if getattr(workspace_desc, "workspaceType", None) is not None or str(
            getattr(workspace_desc, "dataType", "")
        ).lower() == "workspace":
            break
        parent = str(getattr(workspace_desc, "path", "") or "")
        if not parent or normalize_path(parent) == normalize_path(workspace):
            break
        workspace = parent
    return workspace, bool(getattr(desc, "isVersioned", False))


def _update_geometry(
    arcpy: Any,
    dataset: str,
    operation: dict[str, Any],
) -> int:
    oid_field = _oid_field(arcpy, dataset)
    values = {int(row["oid"]): row["geometry"] for row in operation["rows"]}
    where = _oid_where_clause(arcpy, dataset, oid_field, sorted(values))
    changed = 0
    with arcpy.da.UpdateCursor(
        dataset,
        [oid_field, operation["geometry_token"]],
        where,
    ) as cursor:
        for raw_row in cursor:
            row = list(raw_row)
            oid = int(row[0])
            if oid not in values:
                continue
            row[1] = values[oid]
            cursor.updateRow(row)
            changed += 1
    return changed


def _execute_operation(
    arcpy: Any,
    dataset: str,
    operation: dict[str, Any],
    expected: int,
) -> tuple[int, bool]:
    kind = operation["kind"]
    if kind == "update":
        return da_write.update_features(
            arcpy,
            dataset,
            operation["updates"],
            operation["where_clause"],
            max_rows_updated=max(expected, 1),
        )
    if kind == "insert":
        return (
            da_write.insert_features(
                arcpy, dataset, operation["fields"], operation["rows"]
            ),
            False,
        )
    if kind == "delete":
        return da_write.delete_selected(
            arcpy,
            dataset,
            operation["where_clause"],
            max_rows_deleted=max(expected, 1),
        )
    return _update_geometry(arcpy, dataset, operation), False


def edit_apply(
    arcpy: Any,
    dataset_path: str,
    operations: list[dict[str, Any]],
    edit_token: str,
) -> dict[str, Any]:
    dataset = validate_input_path_optional(dataset_path, "dataset_path")
    normalized = _validated_operations(operations)
    if any(op["kind"] == "delete" for op in normalized):
        require_allow_destructive()
    else:
        require_allow_write()
    token_payload = _verify_token(edit_token)
    if token_payload.get("scope") != "dataset":
        raise RuntimeError("edit_token 类型与单数据集请求不匹配")
    if normalize_path(dataset) != token_payload.get("dataset"):
        raise RuntimeError("edit_token 的数据集与当前请求不一致")
    digest = hashlib.sha256(_canonical(normalized)).hexdigest()
    if digest != token_payload.get("operations_digest"):
        raise RuntimeError("operations 已在预检后改变，请重新执行 edit_preflight")
    plan = _operation_plan(arcpy, dataset, normalized)
    if _plan_state(plan) != token_payload.get("plan"):
        raise RuntimeError("目标行在预检后已改变，请重新预检；本次未写入")

    workspace, multiuser_mode = _workspace_for_dataset(arcpy, dataset)
    editor = arcpy.da.Editor(workspace)
    results: list[dict[str, Any]] = []
    committed = False
    editor.startEditing(False, multiuser_mode)
    editor.startOperation()
    try:
        for index, op in enumerate(normalized):
            kind = op["kind"]
            expected = int(plan[index]["affected_count"])
            changed, truncated = _execute_operation(arcpy, dataset, op, expected)
            if truncated or changed != expected:
                raise RuntimeError(
                    f"operations[{index}] 预期变更 {expected} 行，实际 {changed} 行；事务将回滚"
                )
            results.append({"index": index, "kind": kind, "changed_count": changed})
        editor.stopOperation()
        editor.stopEditing(True)
        committed = True
    except BaseException:
        try:
            editor.abortOperation()
        finally:
            editor.stopEditing(False)
        raise

    return {
        "ok": True,
        "dataset_path": normalize_path(dataset),
        "committed": committed,
        "operations": results,
        "changed_count": sum(item["changed_count"] for item in results),
        "native_pro_undo": False,
    }


def _validated_workspace_operations(
    arcpy: Any,
    operations: list[dict[str, Any]],
) -> tuple[str, bool, list[dict[str, Any]]]:
    if not operations or len(operations) > _MAX_OPERATIONS:
        raise RuntimeError(f"operations 必须为 1–{_MAX_OPERATIONS} 项数组")
    normalized: list[dict[str, Any]] = []
    workspace: str | None = None
    multiuser_mode = False
    for index, raw in enumerate(operations):
        if not isinstance(raw, dict):
            raise RuntimeError(f"operations[{index}] 必须为对象")
        dataset = validate_input_path_optional(
            str(raw.get("dataset_path") or ""),
            f"operations[{index}].dataset_path",
        )
        operation = dict(raw)
        operation.pop("dataset_path", None)
        operation = _validated_operations([operation])[0]
        operation["dataset_path"] = normalize_path(dataset)
        current_workspace, versioned = _workspace_for_dataset(arcpy, dataset)
        current_workspace = normalize_path(current_workspace)
        if workspace is None:
            workspace = current_workspace
        elif workspace != current_workspace:
            raise RuntimeError(
                "workspace edit 的所有数据集必须属于同一工作空间："
                f"{workspace!r} != {current_workspace!r}"
            )
        multiuser_mode = multiuser_mode or versioned
        normalized.append(operation)
    assert workspace is not None
    return workspace, multiuser_mode, normalized


def _workspace_plans(
    arcpy: Any,
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for operation in operations:
        dataset = operation["dataset_path"]
        payload = dict(operation)
        payload.pop("dataset_path", None)
        plan = _operation_plan(arcpy, dataset, [payload])[0]
        plans.append(
            {
                "dataset_path": dataset,
                "kind": plan["kind"],
                "affected_count": plan["affected_count"],
                "oid_digest": plan.get("oid_digest", ""),
                "details": plan,
            }
        )
    return plans


def _workspace_plan_state(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "dataset_path": item["dataset_path"],
            "kind": item["kind"],
            "affected_count": item["affected_count"],
            "oid_digest": item.get("oid_digest", ""),
        }
        for item in plans
    ]


def workspace_edit_preflight(
    arcpy: Any,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    workspace, _multiuser_mode, normalized = _validated_workspace_operations(
        arcpy, operations
    )
    plans = _workspace_plans(arcpy, normalized)
    issued_at = time.time()
    payload = {
        "scope": "workspace",
        "workspace": workspace,
        "operations_digest": hashlib.sha256(_canonical(normalized)).hexdigest(),
        "plan": _workspace_plan_state(plans),
        "issued_at": issued_at,
        "expires_at": issued_at + _TOKEN_TTL_SECONDS,
    }
    return {
        "ok": True,
        "workspace": workspace,
        "plan": plans,
        "total_affected": sum(item["affected_count"] for item in plans),
        "edit_token": _sign(payload),
        "expires_at": payload["expires_at"],
        "native_pro_undo": False,
    }


def workspace_edit_apply(
    arcpy: Any,
    operations: list[dict[str, Any]],
    edit_token: str,
) -> dict[str, Any]:
    workspace, multiuser_mode, normalized = _validated_workspace_operations(
        arcpy, operations
    )
    if any(operation["kind"] == "delete" for operation in normalized):
        require_allow_destructive()
    else:
        require_allow_write()
    token_payload = _verify_token(edit_token)
    if token_payload.get("scope") != "workspace":
        raise RuntimeError("edit_token 类型与 workspace 请求不匹配")
    if token_payload.get("workspace") != workspace:
        raise RuntimeError("edit_token 的 workspace 与当前请求不一致")
    digest = hashlib.sha256(_canonical(normalized)).hexdigest()
    if digest != token_payload.get("operations_digest"):
        raise RuntimeError("workspace operations 已在预检后改变")
    plans = _workspace_plans(arcpy, normalized)
    if _workspace_plan_state(plans) != token_payload.get("plan"):
        raise RuntimeError("workspace 目标行在预检后已改变；本次未写入")

    editor = arcpy.da.Editor(workspace)
    results: list[dict[str, Any]] = []
    editor.startEditing(False, multiuser_mode)
    editor.startOperation()
    try:
        for index, operation in enumerate(normalized):
            dataset = operation["dataset_path"]
            payload = dict(operation)
            payload.pop("dataset_path", None)
            expected = int(plans[index]["affected_count"])
            changed, truncated = _execute_operation(
                arcpy, dataset, payload, expected
            )
            if truncated or changed != expected:
                raise RuntimeError(
                    f"operations[{index}] 预期变更 {expected} 行，实际 {changed} 行；事务将回滚"
                )
            results.append(
                {
                    "index": index,
                    "dataset_path": dataset,
                    "kind": payload["kind"],
                    "changed_count": changed,
                }
            )
        editor.stopOperation()
        editor.stopEditing(True)
    except BaseException:
        try:
            editor.abortOperation()
        finally:
            editor.stopEditing(False)
        raise
    return {
        "ok": True,
        "workspace": workspace,
        "committed": True,
        "operations": results,
        "changed_count": sum(item["changed_count"] for item in results),
        "native_pro_undo": False,
    }
