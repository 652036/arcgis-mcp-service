"""Constrained Utility Network inspection, tracing, and maintenance helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from arcgis_pro_mcp.paths import (
    normalize_path,
    require_allow_destructive,
    require_allow_enterprise_write,
    require_allow_write,
    validate_input_path_optional,
    validate_output_in_export_root,
)

_TRACE_TYPES = frozenset(
    {
        "CONNECTED",
        "SUBNETWORK",
        "SUBNETWORK_CONTROLLERS",
        "UPSTREAM",
        "DOWNSTREAM",
        "LOOPS",
        "SHORTEST_PATH",
        "ISOLATION",
    }
)
_SELECTION_TYPES = frozenset(
    {"NEW_SELECTION", "ADD_TO_SELECTION", "REMOVE_FROM_SELECTION", "SUBSET_SELECTION"}
)


def _required_text(value: Any, label: str, maximum: int = 512) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"{label} 不能为空")
    if len(text) > maximum or "\x00" in text:
        raise RuntimeError(f"{label} 无效或过长")
    return text


def _network_input(value: Any, label: str = "in_utility_network") -> Any:
    if isinstance(value, str):
        return validate_input_path_optional(value, label)
    return value


def _network_identity(value: Any) -> str:
    if isinstance(value, str):
        return normalize_path(value)
    for name in ("URI", "longName", "name", "dataSource"):
        raw = str(getattr(value, name, "") or "").strip()
        if raw:
            return raw
    raise RuntimeError("无法确定 Utility Network 对象身份")


def _confirm_network(value: Any, expected_network: str) -> str:
    identity = _network_identity(value)
    if expected_network != identity:
        raise RuntimeError("expected_network 必须精确回显 Utility Network 路径/URI/名称")
    return identity


def _messages(result: Any) -> str:
    method = getattr(result, "getMessages", None)
    if not callable(method):
        return ""
    return str(method() or "")[:8000]


def _result_output(result: Any, index: int) -> Any:
    method = getattr(result, "getOutput", None)
    if not callable(method):
        return None
    try:
        return method(index)
    except Exception:  # noqa: BLE001
        return None


def _plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def describe_utility_network(arcpy: Any, in_utility_network: Any) -> dict[str, Any]:
    network = _network_input(in_utility_network)
    desc = arcpy.Describe(network)
    domains: list[dict[str, Any]] = []
    for domain in list(getattr(desc, "domainNetworks", None) or []):
        tiers: list[dict[str, Any]] = []
        for tier in list(getattr(domain, "tiers", None) or []):
            tiers.append(
                {
                    "name": str(getattr(tier, "name", "") or ""),
                    "rank": _plain(getattr(tier, "rank", None)),
                    "topology_type": _plain(getattr(tier, "topologyType", None)),
                    "is_dirty_managed": _plain(
                        getattr(getattr(tier, "manageSubnetwork", None), "isDirty", None)
                    ),
                }
            )
        domains.append(
            {
                "name": str(getattr(domain, "domainNetworkName", "") or getattr(domain, "name", "") or ""),
                "tier_definition": _plain(getattr(domain, "tierDefinition", None)),
                "tiers": tiers,
            }
        )
    return {
        "identity": _network_identity(network),
        "data_type": str(getattr(desc, "dataType", "") or ""),
        "utility_network_version": _plain(getattr(desc, "utilityNetworkVersion", None)),
        "schema_generation": _plain(getattr(desc, "schemaGeneration", None)),
        "network_topology_enabled": _plain(getattr(desc, "networkTopologyEnabled", None)),
        "domain_networks": domains,
    }


def _extent(arcpy: Any, values: list[float] | None, keyword: str) -> Any:
    if values is not None:
        if len(values) != 4:
            raise RuntimeError("extent 必须为 [xmin, ymin, xmax, ymax]")
        numbers = [float(value) for value in values]
        if numbers[0] >= numbers[2] or numbers[1] >= numbers[3]:
            raise RuntimeError("extent 必须满足 xmin < xmax 且 ymin < ymax")
        return arcpy.Extent(*numbers)
    key = str(keyword or "MAXOF").strip().upper()
    if key not in {"MAXOF", "MINOF", "DISPLAY"}:
        raise RuntimeError("extent_keyword 须为 MAXOF/MINOF/DISPLAY")
    return key


def validate_network_topology(
    arcpy: Any,
    in_utility_network: Any,
    *,
    expected_network: str,
    extent: list[float] | None = None,
    extent_keyword: str = "MAXOF",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    network = _network_input(in_utility_network)
    identity = _confirm_network(network, expected_network)
    area = _extent(arcpy, extent, extent_keyword)
    result = arcpy.un.ValidateNetworkTopology(network, area)
    output_json = _result_output(result, 1)
    parsed: Any = output_json
    if isinstance(output_json, str) and output_json.strip().startswith(("{", "[")):
        try:
            parsed = json.loads(output_json)
        except json.JSONDecodeError:
            parsed = output_json
    return {
        "network": identity,
        "extent": [float(value) for value in extent] if extent is not None else str(area),
        "discovered_subnetworks": _plain(parsed),
        "messages": _messages(result),
    }


def trace_named_configuration(
    arcpy: Any,
    in_utility_network: Any,
    trace_type: str,
    trace_config_name: str,
    *,
    starting_points: str = "",
    barriers: str = "",
    selection_type: str = "NEW_SELECTION",
    clear_previous_results: bool = True,
    trace_name: str = "",
) -> dict[str, Any]:
    require_allow_write()
    network = _network_input(in_utility_network)
    trace = _required_text(trace_type, "trace_type", 64).upper()
    if trace not in _TRACE_TYPES:
        raise RuntimeError("trace_type 不受支持")
    config = _required_text(trace_config_name, "trace_config_name", 256)
    selection = _required_text(selection_type, "selection_type", 64).upper()
    if selection not in _SELECTION_TYPES:
        raise RuntimeError("selection_type 不受支持")
    starts: Any = ""
    if starting_points.strip():
        starts = validate_input_path_optional(starting_points, "starting_points")
    stops: Any = ""
    if barriers.strip():
        stops = validate_input_path_optional(barriers, "barriers")
    result = arcpy.un.Trace(
        network,
        trace,
        starts,
        stops,
        selection_type=selection,
        clear_all_previous_trace_results=(
            "CLEAR_ALL_PREVIOUS_TRACE_RESULTS"
            if clear_previous_results
            else "DO_NOT_CLEAR_ALL_PREVIOUS_TRACE_RESULTS"
        ),
        trace_name=str(trace_name or "").strip(),
        use_trace_config="USE_TRACE_CONFIGURATION",
        trace_config_name=config,
    )
    return {
        "network": _network_identity(network),
        "trace_type": trace,
        "trace_config_name": config,
        "selection_type": selection,
        "messages": _messages(result),
    }


def update_subnetwork(
    arcpy: Any,
    in_utility_network: Any,
    domain_network: str,
    tier: str,
    *,
    subnetwork_name: str = "",
    all_subnetworks: bool = False,
    continue_on_failure: bool = False,
    expected_network: str,
    confirm_all: str = "",
) -> dict[str, Any]:
    require_allow_enterprise_write()
    network = _network_input(in_utility_network)
    identity = _confirm_network(network, expected_network)
    domain = _required_text(domain_network, "domain_network", 256)
    tier_name = _required_text(tier, "tier", 256)
    name = str(subnetwork_name or "").strip()
    if all_subnetworks:
        require_allow_destructive()
        if confirm_all != "UPDATE_ALL_SUBNETWORKS_IN_TIER":
            raise RuntimeError(
                "更新整个 tier 必须精确传入 confirm_all=UPDATE_ALL_SUBNETWORKS_IN_TIER"
            )
        mode = "ALL_SUBNETWORKS_IN_TIER"
        name = ""
    else:
        mode = "SPECIFIC_SUBNETWORK"
        name = _required_text(name, "subnetwork_name", 256)
    result = arcpy.un.UpdateSubnetwork(
        network,
        domain,
        tier_name,
        mode,
        name,
        "CONTINUE_ON_FAILURE" if continue_on_failure else "STOP_ON_FAILURE",
    )
    return {
        "network": identity,
        "domain_network": domain,
        "tier": tier_name,
        "mode": mode,
        "subnetwork_name": name or None,
        "messages": _messages(result),
    }


def export_subnetwork(
    arcpy: Any,
    in_utility_network: Any,
    domain_network: str,
    tier: str,
    subnetwork_name: str,
    output_json: str,
    *,
    include_geometry: bool = False,
    include_domain_descriptions: bool = False,
) -> dict[str, Any]:
    network = _network_input(in_utility_network)
    output = validate_output_in_export_root(output_json, "output_json")
    if Path(output).suffix.lower() != ".json":
        raise RuntimeError("output_json 必须以 .json 结尾")
    if os.path.exists(output):
        raise RuntimeError("output_json 已存在；本工具拒绝覆盖")
    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    result = arcpy.un.ExportSubnetwork(
        network,
        _required_text(domain_network, "domain_network", 256),
        _required_text(tier, "tier", 256),
        _required_text(subnetwork_name, "subnetwork_name", 256),
        "NO_ACKNOWLEDGE",
        output,
        include_geometry="INCLUDE_GEOMETRY" if include_geometry else "EXCLUDE_GEOMETRY",
        include_domain_descriptions=(
            "INCLUDE_DOMAIN_DESCRIPTIONS"
            if include_domain_descriptions
            else "EXCLUDE_DOMAIN_DESCRIPTIONS"
        ),
    )
    if not os.path.isfile(output):
        raise RuntimeError("ExportSubnetwork 返回后未找到 JSON 输出")
    return {
        "network": _network_identity(network),
        "output_json": output,
        "bytes": os.path.getsize(output),
        "acknowledged": False,
        "messages": _messages(result),
    }
