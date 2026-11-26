"""Domain tool modules and profile-aware single-pass registration for ArcGIS Pro MCP."""

from __future__ import annotations

import importlib
from typing import Any

from arcgis_pro_mcp import tool_protocol
from arcgis_pro_mcp.tools._registry import (
    AVAILABLE_TOOL_PROFILES,
    CORE_PROFILE_TOOLS,
    DEFAULT_TOOL_PROFILE,
    DOMAIN_ALIASES,
    DOMAIN_KEYS,
    DOMAIN_MODULES,
    TOOL_PROFILE_ENV,
    ResolvedToolProfile,
    ToolSpec,
    collect_module_tools,
    parse_tool_profile,
    tool,
)


def register_tools(
    mcp: Any,
    srv: Any,
    *,
    profile: str | None = None,
) -> dict[str, Any]:
    """Load all domain modules, export functions onto ``srv``, and register active profile tools.

    All 460 ``arcgis_pro_*`` Python callables and domain helpers are always attached to
    ``srv`` for backwards compatibility with direct Python callers and unit tests.
    Only tools permitted by ``ARCGIS_PRO_MCP_TOOL_PROFILE`` are registered on ``mcp``
    (in a single pass with structured output and discovery metadata).
    """
    resolved = parse_tool_profile(profile)
    registered_names: list[str] = []

    for domain_key, module_name in DOMAIN_MODULES:
        module = importlib.import_module(f"arcgis_pro_mcp.tools.{module_name}")
        for export_name in getattr(module, "SERVER_EXPORTS", ()):
            setattr(srv, export_name, getattr(module, export_name))
        for spec in collect_module_tools(module, domain_key):
            setattr(srv, spec.fn.__name__, spec.fn)
            if resolved.allows(spec.name, domain_key):
                tool_protocol.register_structured_tool(
                    mcp,
                    spec.fn,
                    name=spec.name,
                    description=spec.description,
                )
                registered_names.append(spec.name)

    srv._ACTIVE_TOOL_PROFILE = resolved.profile
    srv._AVAILABLE_TOOL_PROFILES = list(AVAILABLE_TOOL_PROFILES)
    return {
        "tool_profile": resolved.profile,
        "available_tool_profiles": list(AVAILABLE_TOOL_PROFILES),
        "registered_tool_count": len(registered_names),
        "registered_tools": registered_names,
    }


__all__ = [
    "AVAILABLE_TOOL_PROFILES",
    "CORE_PROFILE_TOOLS",
    "DEFAULT_TOOL_PROFILE",
    "DOMAIN_ALIASES",
    "DOMAIN_KEYS",
    "DOMAIN_MODULES",
    "ResolvedToolProfile",
    "TOOL_PROFILE_ENV",
    "ToolSpec",
    "collect_module_tools",
    "parse_tool_profile",
    "register_tools",
    "tool",
]
