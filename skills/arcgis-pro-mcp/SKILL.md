---
name: arcgis-pro-mcp
description: Operate ArcGIS Pro and ArcPy through this repository's constrained MCP server, using project-file mode, an attached CURRENT Python host, or the optional native SDK bridge. Use for ArcGIS Pro projects, live maps/views, GIS datasets, geoprocessing, cartography, editing, raster/LAS, spatial or network analysis, enterprise geodatabases, Utility Network, and controlled publishing.
---

# ArcGIS Pro MCP

Use the smallest supported semantic tool and preserve the user's chosen target. The server exposes structured tool schemas and policy metadata; do not infer a capability or safety gate from a remembered tool list.

## Choose One Execution Mode

- **File mode:** pass an allowed absolute `.aprx` path. Use for deterministic project inspection, batch processing, durable outputs, and edits that do not need to affect the visible Pro window.
- **Python CURRENT host:** run the repository's `接入当前窗口.pyt` toolbox or `接入当前窗口.py` script inside Pro, then pass the exact token `aprx_path="CURRENT"`. Use for broad `arcpy.mp` operations on the open project and active view. The foreground host serializes work and is not a concurrent UI controller.
- **Native SDK bridge:** separately build/install the optional Add-In and use `arcgis_pro_sdk_*` tools only after `arcgis_pro_sdk_bridge_status` reports the capability. Use its exclusive project lease, live context/events, camera/time/table commands, asynchronous typed GP, and native undoable feature/edit commands when true SDK behavior is needed.

Never silently switch among these modes. In particular, a failed `CURRENT` or SDK request must not fall back to a disk path.

Read [references/runtime-notes.md](references/runtime-notes.md) before controlling a live window or choosing between the two live modes.

## Discover Before Acting

1. Confirm the client actually exposes `arcgis_pro_*` tools. Source inspection alone does not prove that the running MCP client uses this checkout.
2. Call `arcgis_pro_environment_info` and `arcgis_pro_server_capabilities`.
3. Call `arcgis_pro_tool_info(name="...")` before an unfamiliar or consequential tool. Its input/output schema, mode flags, policy hints, gates, and conditional gates are authoritative for the running process. Use `name=""` only when a full catalog is genuinely needed.
4. Discover the exact project, map, layout, layer/table, dataset, network, locator, or service target. Prefer returned URI, `long_name`, absolute path, or other stable identity over a possibly duplicated display name.
5. For `CURRENT`, call `arcgis_pro_window_status` and require the expected saved project, ready host, and confirmed target. For SDK work, acquire a lease for the exact saved project and read `arcgis_pro_sdk_context`.

## Safety Invariants

- Default to read-only. A user's request to inspect or diagnose does not authorize edits, exports, publication, or saves.
- Respect every gate reported by `arcgis_pro_tool_info`, including conditional gates. Ordinary writes, destructive/schema operations, raw CIM, enterprise writes, publication, public sharing, overwrite publication, SDK edits, and SDK feature edits have distinct controls.
- Keep project/input paths inside configured roots. Keep exports and GP outputs inside their respective roots. Never broaden a root to a drive or user profile merely to make a call pass.
- Prefer named/typed wrappers. Generic GP requires base write permission, explicit enablement, an exact allowlist, a configured GP output root, and a complete new output path. It rejects container/name pairs, existing targets, in-place/no-output work, destructive tools, and code execution, and forces overwrite off. The SDK GP bridge additionally requires a built-in typed contract and its own allowlists.
- Treat deletions, truncation, schema removal, overwrite, discard-all, broad selection edits, and similarly irreversible work as destructive. Require the tool's exact target/count/digest confirmation in addition to its environment gate.
- Treat enterprise permission narrowly: `ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE` covers version management, maintenance, and Utility Network administration. Ordinary feature/row edits use WRITE, plus destructive permission for deletion and the SDK-feature gate for native SDK feature edits.
- Use only the constrained Arcade subset for Calculate Field and only Arcade for label expressions; never supply Python/VB/code blocks or remote dynamic evaluation. Repair Geometry is fixed to `KEEP_NULL`. Export and local publishing-artifact paths must be new rather than silently replacing existing files.
- Never pass, log, or commit credentials, bearer tokens, lease IDs, connection secrets, discovery documents, or local private paths. Prefer existing connection files or signed-in Pro state. New database connections require an exact configured instance allowlist and use only the fixed DB credential variables; inline passwords are disabled by default.
- Do not blindly retry a timed-out, cancelled, or conflict (`409`) write. Re-read the target, output, selection, edit generation, and job status first because the outcome may be unknown.
- Saving a project is a separate mutation. Save only when requested; prefer a copy or new controlled output when that satisfies the task.
- Releasing or reloading a cached file-mode project can discard unsaved in-memory state. Use it only with WRITE + DESTRUCTIVE and an exact `confirm_aprx_path` echo after checking that no intended changes remain.

Read [references/security-and-paths.md](references/security-and-paths.md) before any write, publish, enterprise, SDK, or destructive workflow.

## Workflow Routing

- Project/map/layer/layout/cartography, selections, reports, exports, DA access, datasets and schema: read the relevant groups in [references/tools.md](references/tools.md).
- Raster, mosaic, LAS, hydrology/distance, spatial statistics/modeling, space-time cubes/forecasting, local network analysis, and local geocoding: read the analysis section in [references/tools.md](references/tools.md). Verify product extensions and projected-coordinate requirements before assuming execution will succeed.
- Enterprise versions and maintenance, Utility Network, publishing, attribute rules, field groups, contingent values, domains, subtypes, topology, and attachments: read the high-impact workflows in both [references/tools.md](references/tools.md) and [references/security-and-paths.md](references/security-and-paths.md).
- Editing this repository, adding tools, changing protocol/gates, or updating tests: read [references/development.md](references/development.md).

For a mutating workflow: read/preflight, execute the narrow operation once, inspect the structured result and GP messages, then independently re-read the affected state or verify the output artifact.

## Runtime Boundary And Deliberate Limits

Real ArcPy execution requires Windows, ArcGIS Pro, and an ArcGIS Pro Python environment with `arcpy`. Ordinary Python validation can prove syntax and mocked policy behavior, not real Pro, extension, enterprise, Portal, or data-lock behavior.

This project does not provide arbitrary Python/.NET execution, arbitrary Ribbon/menu/dialog automation, remote network control of Pro, credential extraction, silent reprojection of SDK camera/geometry input, Portal ready-to-use network services, or a generic unreviewed SDK GP/CIM surface. SDK prompt geometry is deliberately unavailable. Python CURRENT is an exclusive foreground bridge; the SDK bridge is loopback-only and exposes only its advertised typed capabilities.
