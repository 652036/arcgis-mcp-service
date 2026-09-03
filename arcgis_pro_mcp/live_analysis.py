"""Constrained geoprocessing against layers in the attached CURRENT map."""

from __future__ import annotations

import os
from typing import Any

from arcgis_pro_mcp.paths import (
    is_probably_path,
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)
from arcgis_pro_mcp.redaction import redact_text

# Exact ArcPy tools already represented by semantic wrappers in this project.  The
# live command accepts their ArcPy names so current Layer objects and their selection
# sets can be used without weakening the generic-GP gate.
CURRENT_NAMED_GP_TOOLS = frozenset(
    {
        "analysis.Buffer",
        "analysis.Clip",
        "analysis.Dissolve",
        "analysis.Erase",
        "analysis.Identity",
        "analysis.Intersect",
        "analysis.MultipleRingBuffer",
        "analysis.Select",
        "analysis.SpatialJoin",
        "analysis.SymDiff",
        "analysis.Union",
        "conversion.ExportFeatures",
        "conversion.ExportTable",
        "conversion.FeatureToRaster",
        "conversion.JSONToFeatures",
        "conversion.PolygonToRaster",
        "conversion.RasterToPolygon",
        "management.AggregatePolygons",
        "management.CheckGeometry",
        "management.CopyFeatures",
        "management.FeatureToLine",
        "management.FeatureToPoint",
        "management.GenerateNearTable",
        "management.Merge",
        "management.MinimumBoundingGeometry",
        "management.MultipartToSinglepart",
        "management.PointsToLine",
        "management.PolygonToLine",
        "management.Project",
        "management.Resample",
        "management.Statistics",
    }
)

# Field Calculator can execute Python/code blocks inside the ArcGIS Pro process.
# Keep this explicit deny in addition to omitting it above so a future allowlist
# edit cannot silently restore that execution path.
_CURRENT_HARD_DENIED_GP_TOOLS = frozenset(
    {
        "management.CalculateField",
        "management.CalculateGeometryAttributes",
        "management.RepairGeometry",
    }
)

_OUTPUT_KEYS = frozenset(
    {
        "out_dataset",
        "out_feature_class",
        "out_features",
        "out_raster",
        "out_table",
        "output",
        "output_data",
        "output_feature_class",
        "output_raster",
        "target_workspace",
    }
)
_OUTPUT_CONTAINER_KEYS = frozenset(
    {
        "out_folder",
        "out_folder_path",
        "out_path",
        "out_workspace",
        "target_workspace",
        "workspace_out",
    }
)
_INPUT_KEY_MARKERS = (
    "in_",
    "input",
    "clip_features",
    "erase_features",
    "identity_features",
    "join_features",
    "near_features",
    "select_features",
    "update_features",
)
_ENVIRONMENT_KEYS = frozenset(
    {
        "cellSize",
        "extent",
        "geographicTransformations",
        "mask",
        "outputCoordinateSystem",
        "parallelProcessingFactor",
        "scratchWorkspace",
        "snapRaster",
        "workspace",
    }
)


def _find_layer(map_obj: Any, selector: str) -> Any:
    values = list(map_obj.listLayers())
    exact = [
        layer
        for layer in values
        if selector
        in {
            str(getattr(layer, "name", "")),
            str(getattr(layer, "longName", "")),
            str(getattr(layer, "URI", "")),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RuntimeError(f"图层标识不唯一：{selector!r}；请使用 URI 或 longName")
    raise RuntimeError(f"当前地图中未找到图层：{selector!r}")


def _find_table(map_obj: Any, selector: str) -> Any:
    values = list(map_obj.listTables())
    exact = [
        table
        for table in values
        if selector in {str(getattr(table, "name", "")), str(getattr(table, "URI", ""))}
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RuntimeError(f"表标识不唯一：{selector!r}；请使用 URI")
    raise RuntimeError(f"当前地图中未找到表：{selector!r}")


def _resolve_reference(map_obj: Any, value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"layer"}:
            return _find_layer(map_obj, str(value["layer"]))
        if set(value) == {"table"}:
            return _find_table(map_obj, str(value["table"]))
        raise RuntimeError("对象参数只允许 {'layer': selector} 或 {'table': selector} 引用")
    if isinstance(value, list):
        return [_resolve_reference(map_obj, item) for item in value]
    return value


def _sanitize_parameter(key: str, value: Any, map_obj: Any) -> Any:
    resolved = _resolve_reference(map_obj, value)
    if isinstance(resolved, list):
        return [_sanitize_parameter(key, item, map_obj) for item in resolved]
    if not isinstance(resolved, str):
        return resolved
    name = key.strip().lower()
    if any(marker in name for marker in ("password", "secret", "token")) and resolved:
        raise RuntimeError(f"参数 {key!r} 不允许包含凭据")
    if name in _OUTPUT_KEYS or name.startswith("out_") or "output" in name:
        return validate_gp_output_path(resolved, key)
    if name.startswith(_INPUT_KEY_MARKERS) or name.endswith(("_path", "_dataset", "_table", "_raster")):
        return validate_input_path_optional(resolved, key)
    return resolved


def _tool_callable(arcpy: Any, name: str) -> Any:
    if name in _CURRENT_HARD_DENIED_GP_TOOLS:
        raise RuntimeError(
            f"当前窗口分析永久拒绝工具 {name!r}：字段表达式可能执行代码；"
            "请使用不接受 Python/code_block 的专用语义工具。"
        )
    if name not in CURRENT_NAMED_GP_TOOLS:
        raise RuntimeError(
            f"当前窗口分析不允许工具 {name!r}；可选：{sorted(CURRENT_NAMED_GP_TOOLS)}"
        )
    module_name, function_name = name.split(".", 1)
    module = getattr(arcpy, module_name, None)
    function = getattr(module, function_name, None) if module is not None else None
    if function is None:
        raise RuntimeError(f"当前 ArcPy 安装没有工具：{name}")
    return function


def _messages(result: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    count = int(getattr(result, "messageCount", 0) or 0)
    for index in range(count):
        try:
            severity = int(result.getSeverity(index))
        except Exception:  # noqa: BLE001
            severity = None
        try:
            text = redact_text(result.getMessage(index))
        except Exception:  # noqa: BLE001
            continue
        messages.append({"severity": severity, "text": text})
    return messages


def _derived_outputs(arcpy: Any, result: Any) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    count = int(getattr(result, "outputCount", 0) or 0)
    for index in range(count):
        try:
            value = result.getOutput(index)
        except Exception:  # noqa: BLE001
            continue
        text = str(value)
        exists = False
        try:
            exists = bool(arcpy.Exists(value))
        except Exception:  # noqa: BLE001
            pass
        item: dict[str, Any] = {"index": index, "value": text, "exists": exists}
        if exists:
            try:
                item["count"] = int(arcpy.management.GetCount(value).getOutput(0))
            except Exception:  # noqa: BLE001
                pass
        outputs.append(item)
    return outputs


def _validated_environment(environment: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(environment or {})
    unknown = sorted(set(values) - _ENVIRONMENT_KEYS)
    if unknown:
        raise RuntimeError(f"不允许的 GP environment：{unknown}")
    for key, value in list(values.items()):
        if value is None or value == "":
            continue
        if key in {"workspace", "scratchWorkspace"}:
            if not isinstance(value, str):
                raise RuntimeError(f"GP environment {key} 必须为路径字符串")
            values[key] = validate_gp_output_path(value, key)
        elif key in {"snapRaster", "mask"}:
            if not isinstance(value, str):
                raise RuntimeError(f"GP environment {key} 必须为路径字符串")
            values[key] = validate_input_path_optional(value, key)
        elif key in {"extent", "cellSize", "outputCoordinateSystem"}:
            if isinstance(value, str) and is_probably_path(value):
                values[key] = validate_input_path_optional(value, key)
    return values


def _expected_output_paths(parameters: dict[str, Any]) -> list[str]:
    outputs: list[str] = []
    for key, value in parameters.items():
        name = key.strip().lower()
        if name in _OUTPUT_CONTAINER_KEYS:
            raise RuntimeError(
                "当前窗口分析不接受输出容器与名称分离的参数；请提供完整 out_* 目标路径"
            )
        if not (name in _OUTPUT_KEYS or name.startswith("out_") or "output" in name):
            continue
        candidates = value if isinstance(value, list) else [value]
        outputs.extend(item for item in candidates if isinstance(item, str) and item)
    return list(dict.fromkeys(outputs))


def _output_exists(arcpy: Any, path: str) -> bool:
    try:
        if bool(arcpy.Exists(path)):
            return True
    except Exception:  # noqa: BLE001
        pass
    return os.path.exists(path)


def run_current_analysis(
    arcpy: Any,
    map_obj: Any,
    tool_name: str,
    parameters: dict[str, Any],
    *,
    environment: dict[str, Any] | None = None,
    add_outputs_to_map: bool = True,
) -> dict[str, Any]:
    """Run one allowlisted GP tool with current Layer/Table object references."""
    require_allow_write()
    function = _tool_callable(arcpy, tool_name.strip())
    require_gp_output_root_mandatory()
    if not isinstance(parameters, dict) or not parameters:
        raise RuntimeError("parameters 必须为非空对象")
    sanitized = {key: _sanitize_parameter(str(key), value, map_obj) for key, value in parameters.items()}
    environments = _validated_environment(environment)
    expected_outputs = _expected_output_paths(sanitized)
    if not expected_outputs:
        raise RuntimeError("当前窗口分析必须提供至少一个完整、受控的输出路径")
    existing = [output for output in expected_outputs if _output_exists(arcpy, output)]
    if existing:
        raise RuntimeError(f"当前窗口分析拒绝覆盖已有输出：{existing[:20]}")
    manager = getattr(arcpy, "EnvManager", None)
    if not callable(manager):
        raise RuntimeError("当前 ArcPy 不支持 EnvManager，无法强制 overwriteOutput=False")

    with manager(overwriteOutput=False, **environments):
        result = function(**sanitized)

    outputs = _derived_outputs(arcpy, result)
    missing = [output for output in expected_outputs if not bool(arcpy.Exists(output))]
    if missing:
        raise RuntimeError(f"GP 返回后预期输出不存在：{missing[:20]}")
    known_values = {item["value"] for item in outputs}
    for output in expected_outputs:
        if output in known_values:
            continue
        item: dict[str, Any] = {"index": None, "value": output, "exists": True}
        try:
            item["count"] = int(arcpy.management.GetCount(output).getOutput(0))
        except Exception:  # noqa: BLE001
            pass
        outputs.append(item)
    added: list[str] = []
    if add_outputs_to_map:
        for output in outputs:
            value = output["value"]
            if not output["exists"]:
                continue
            try:
                map_obj.addDataFromPath(value)
                added.append(value)
            except Exception:  # noqa: BLE001
                continue
    return {
        "ok": True,
        "tool_name": tool_name,
        "map_name": str(getattr(map_obj, "name", "")),
        "messages": _messages(result),
        "outputs": outputs,
        "added_to_map": added,
        "selection_semantics": "Layer 引用会使用当前选择集；绝对数据路径不会读取 UI 选择集",
    }


def query_current_layer(
    arcpy: Any,
    map_obj: Any,
    layer_selector: str,
    fields: list[str],
    *,
    where_clause: str = "",
    selected_only: bool = True,
    max_rows: int = 200,
) -> dict[str, Any]:
    """Read rows from a live layer while optionally honoring its UI selection."""
    layer = _find_layer(map_obj, layer_selector)
    available = {field.name for field in arcpy.ListFields(layer)}
    requested = [str(field).strip() for field in fields if str(field).strip()]
    if not requested:
        raise RuntimeError("fields 不能为空")
    for field in requested:
        if field.upper().startswith(("SHAPE@", "OID@")):
            continue
        if field not in available:
            raise RuntimeError(f"未知字段：{field!r}")
    where = (where_clause or "").strip()
    if len(where) > 8000:
        raise RuntimeError("where_clause 过长")
    cap = max(1, min(int(max_rows), 2000))
    selection_count = None
    try:
        selection_count = len(layer.getSelectionSet())
    except Exception as ex:  # noqa: BLE001
        if selected_only:
            raise RuntimeError("selected_only=true，但无法读取图层选择集") from ex
    if selected_only and selection_count == 0:
        return {
            "ok": True,
            "map_name": str(getattr(map_obj, "name", "")),
            "layer_name": str(getattr(layer, "name", "")),
            "layer_uri": str(getattr(layer, "URI", "") or ""),
            "selected_only": True,
            "selection_count": 0,
            "fields": requested,
            "rows": [],
            "row_count": 0,
            "truncated": False,
        }
    source = layer if selected_only else getattr(layer, "dataSource", layer)
    rows: list[dict[str, Any]] = []
    truncated = False
    with arcpy.da.SearchCursor(source, requested, where or None) as cursor:
        for row in cursor:
            if len(rows) >= cap:
                truncated = True
                break
            rows.append({requested[index]: value for index, value in enumerate(row)})
    return {
        "ok": True,
        "map_name": str(getattr(map_obj, "name", "")),
        "layer_name": str(getattr(layer, "name", "")),
        "layer_uri": str(getattr(layer, "URI", "") or ""),
        "selected_only": bool(selected_only),
        "selection_count": selection_count,
        "fields": requested,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
