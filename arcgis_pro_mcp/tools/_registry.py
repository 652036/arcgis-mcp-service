"""Tool registration, domain mapping, and profile resolution for ArcGIS Pro MCP."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any


class _ServerProxy:
    """Resolve attributes dynamically against the active ``arcgis_pro_mcp.server`` module."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(sys.modules["arcgis_pro_mcp.server"], name)


SERVER_PROXY: Any = _ServerProxy()

TOOL_PROFILE_ENV = "ARCGIS_PRO_MCP_TOOL_PROFILE"
DEFAULT_TOOL_PROFILE = "full"

DOMAIN_MODULES: tuple[tuple[str, str], ...] = (
    ("window", "core_window"),
    ("project", "project_map"),
    ("data", "data_access"),
    ("gp", "gp_core"),
    ("analysis", "gp_analysis"),
    ("raster", "gp_raster"),
    ("stats_network", "gp_stats_network"),
    ("editing", "editing_schema"),
    ("cartography", "cartography_publishing"),
    ("enterprise", "enterprise_utility_sdk"),
)

DOMAIN_KEYS: frozenset[str] = frozenset(domain for domain, _ in DOMAIN_MODULES)

DOMAIN_ALIASES: dict[str, str] = {
    **{domain: domain for domain, _ in DOMAIN_MODULES},
    **{module_name: domain for domain, module_name in DOMAIN_MODULES},
    "stats": "stats_network",
    "network": "stats_network",
    "schema": "editing",
    "publishing": "cartography",
    "utility": "enterprise",
    "sdk": "enterprise",
}

AVAILABLE_TOOL_PROFILES: tuple[str, ...] = (
    "full",
    "core",
    *(domain for domain, _ in DOMAIN_MODULES),
)

# Lean, high-leverage tool set (~60 tools) keeping tools/list well under 90 KB
# while retaining discovery, live window control, project/layer inspection,
# data/schema reads, generic GP execution, checked raster QA, and core export.
CORE_PROFILE_TOOLS: frozenset[str] = frozenset(
    {
        # Discovery & capabilities (3)
        "arcgis_pro_environment_info",
        "arcgis_pro_server_capabilities",
        "arcgis_pro_tool_info",
        # Window management & live view (12)
        "arcgis_pro_list_windows",
        "arcgis_pro_select_window",
        "arcgis_pro_window_status",
        "arcgis_pro_detach_window",
        "arcgis_pro_window_job_submit",
        "arcgis_pro_window_job_status",
        "arcgis_pro_window_job_cancel",
        "arcgis_pro_window_wait_for_change",
        "arcgis_pro_active_view_info",
        "arcgis_pro_open_map_view",
        "arcgis_pro_zoom_active_view_to_layer",
        "arcgis_pro_export_active_view_image",
        # Project / map / layer inspect & core manipulation (17)
        "arcgis_pro_list_projects",
        "arcgis_pro_project_summary",
        "arcgis_pro_save_project",
        "arcgis_pro_save_project_copy",
        "arcgis_pro_list_maps",
        "arcgis_pro_list_layouts",
        "arcgis_pro_list_layers",
        "arcgis_pro_list_tables",
        "arcgis_pro_layer_properties",
        "arcgis_pro_get_layer_extent",
        "arcgis_pro_map_spatial_reference",
        "arcgis_pro_list_broken_sources",
        "arcgis_pro_add_layer_from_path",
        "arcgis_pro_remove_layer",
        "arcgis_pro_set_layer_visible",
        "arcgis_pro_set_definition_query",
        "arcgis_pro_select_layer_by_attribute",
        # Data access & schema inspection (11)
        "arcgis_pro_describe",
        "arcgis_pro_list_fields",
        "arcgis_pro_dataset_exists",
        "arcgis_pro_dataset_schema",
        "arcgis_pro_verify_output_dataset",
        "arcgis_pro_workspace_list_feature_classes",
        "arcgis_pro_workspace_list_rasters",
        "arcgis_pro_workspace_list_tables",
        "arcgis_pro_da_table_sample",
        "arcgis_pro_da_query_rows",
        "arcgis_pro_da_distinct_values",
        # Generic GP & GP discovery (7)
        "arcgis_pro_gp_run_tool",
        "arcgis_pro_gp_list_registered",
        "arcgis_pro_gp_list_toolboxes",
        "arcgis_pro_gp_list_tools_in_toolbox",
        "arcgis_pro_gp_get_messages",
        "arcgis_pro_gp_get_count",
        "arcgis_pro_extension_status",
        # Checked raster quality & raster inspection (4)
        "arcgis_pro_analysis_asset_info",
        "arcgis_pro_analysis_result_status",
        "arcgis_pro_gp_clip_raster_checked",
        "arcgis_pro_raster_info",
        # Basic symbology & layout/map export (6)
        "arcgis_pro_export_layout_pdf",
        "arcgis_pro_export_layout_image",
        "arcgis_pro_export_map_to_image",
        "arcgis_pro_set_simple_renderer",
        "arcgis_pro_set_unique_value_renderer",
        "arcgis_pro_toggle_layer_labels",
    }
)


@dataclass(frozen=True)
class ToolSpec:
    """Declarative metadata for a tool defined in a domain module."""

    name: str
    description: str
    fn: Any
    domain: str


@dataclass(frozen=True)
class ResolvedToolProfile:
    """Validated profile configuration controlling which tools register on MCP."""

    profile: str
    include_all: bool
    domains: frozenset[str]
    explicit_tools: frozenset[str]

    def allows(self, tool_name: str, domain: str) -> bool:
        if self.include_all:
            return True
        return domain in self.domains or tool_name in self.explicit_tools


def tool(*, name: str, description: str = "") -> Any:
    """Mark a domain function as an MCP tool without building schemas at import time."""

    def decorator(fn: Any) -> Any:
        fn.__arcgis_tool_spec__ = (name, description)
        return fn

    return decorator


def collect_module_tools(module: Any, domain: str) -> list[ToolSpec]:
    """Return every @tool-decorated function defined in ``module`` in definition order."""
    specs: list[ToolSpec] = []
    module_name = getattr(module, "__name__", "")
    for value in vars(module).values():
        spec = getattr(value, "__arcgis_tool_spec__", None)
        if spec is None:
            continue
        if getattr(value, "__module__", "") != module_name:
            continue
        tool_name, description = spec
        specs.append(ToolSpec(name=tool_name, description=description, fn=value, domain=domain))
    return specs


def parse_tool_profile(raw_profile: str | None = None) -> ResolvedToolProfile:
    """Parse and validate ``ARCGIS_PRO_MCP_TOOL_PROFILE`` (failing closed on invalid names)."""
    raw = os.environ.get(TOOL_PROFILE_ENV, "") if raw_profile is None else raw_profile
    cleaned = raw.strip()
    if not cleaned:
        return ResolvedToolProfile(
            profile=DEFAULT_TOOL_PROFILE,
            include_all=True,
            domains=DOMAIN_KEYS,
            explicit_tools=frozenset(),
        )

    parts = [part.strip().lower() for part in cleaned.split(",")]
    if any(not part for part in parts):
        raise ValueError(
            f"Invalid {TOOL_PROFILE_ENV} value {raw!r}: empty profile token. "
            f"Valid profiles: {', '.join(AVAILABLE_TOOL_PROFILES)}"
        )

    include_all = False
    include_core = False
    selected_domains: set[str] = set()
    normalized_tokens: list[str] = []

    for token in parts:
        if token == "full":
            include_all = True
            if "full" not in normalized_tokens:
                normalized_tokens.append("full")
        elif token == "core":
            include_core = True
            if "core" not in normalized_tokens:
                normalized_tokens.append("core")
        elif token in DOMAIN_ALIASES:
            canonical = DOMAIN_ALIASES[token]
            selected_domains.add(canonical)
            if canonical not in normalized_tokens:
                normalized_tokens.append(canonical)
        else:
            raise ValueError(
                f"Invalid {TOOL_PROFILE_ENV} token {token!r} in {raw!r}. "
                f"Valid profiles: {', '.join(AVAILABLE_TOOL_PROFILES)}"
            )

    if include_all:
        return ResolvedToolProfile(
            profile="full",
            include_all=True,
            domains=DOMAIN_KEYS,
            explicit_tools=frozenset(),
        )

    return ResolvedToolProfile(
        profile=",".join(normalized_tokens),
        include_all=False,
        domains=frozenset(selected_domains),
        explicit_tools=CORE_PROFILE_TOOLS if include_core else frozenset(),
    )
