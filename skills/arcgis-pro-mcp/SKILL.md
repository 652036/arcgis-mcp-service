---
name: arcgis-pro-mcp
description: Use the ArcGIS Pro MCP server for ArcGIS Pro and ArcPy automation in either file mode or an attached live Pro window. Covers .aprx projects, active views, maps, layers, layouts, selections, rendering, exports, tables, feature classes, geodatabases, rasters, metadata, symbology, constrained arcpy.da writes, named or allowlisted geoprocessing, and network analysis. Use when a task mentions ArcGIS Pro, ArcPy, an open/current GIS window, .aprx, map documents, layouts, feature classes, geodatabases, shapefiles, rasters, GP tools, spatial analysis, layer rendering, labels, bookmarks, map frames, or ArcGIS exports.
---

# ArcGIS Pro MCP

## Overview

Use this skill to operate this repository's ArcGIS Pro MCP server safely. The server exposes a constrained subset of `arcpy.mp`, `arcpy.da`, and ArcPy geoprocessing through stdio MCP tools. Keep its two execution modes explicit:

- **File mode:** use an absolute `.aprx` path in the standalone MCP process.
- **Window mode:** use the exact token `aprx_path=CURRENT` through an attached ArcGIS Pro host when the open UI must change.

Repository-local facts:

- Source root: the repository root containing `pyproject.toml`
- Python package: `arcgis-pro-mcp`
- MCP server name: `arcgis-pro`
- Runtime module: `arcgis_pro_mcp`
- Transport: stdio
- Tool source: `arcgis_pro_mcp/server.py` and helper modules under `arcgis_pro_mcp/`

Canonical skill path: `skills/arcgis-pro-mcp/`. Cursor, Grok, Codex, and Claude load that same directory (see Clients). MCP server name is `arcgis-pro`.

## Hard Runtime Requirement

Real MCP execution requires Windows, ArcGIS Pro, and ArcGIS Pro's bundled Python. Live-window execution additionally requires the in-Pro host from `接入当前窗口.pyt` or `接入当前窗口.py`. A common Python path is:

```text
C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
```

Linux or non-ArcGIS Python can inspect the repository and run some tests, but cannot import `arcpy` or execute real ArcGIS Pro operations.

## Required First Calls

1. Confirm the current agent session actually has `arcgis_pro_*` MCP tools exposed. If not, inspect source/config and say real MCP execution is unavailable.
2. Call `arcgis_pro_environment_info`.
3. Call `arcgis_pro_server_capabilities`.
4. Choose the target mode from the user's intent and keep it stable for the workflow:
   - For the open/current Pro UI, call `arcgis_pro_window_status`, require `window_attached=true` and `host_ready=true`, verify `current_project`, then call `arcgis_pro_active_view_info(aprx_path="CURRENT")`.
   - For a project file, call `arcgis_pro_list_projects`. If `project_count` is 0, read its `note` and either configure project/input roots or use an allowed absolute `.aprx` path.
5. Treat `arcgis_pro_server_capabilities` as authoritative for:
   - available read/write/export tools
   - `allow_write`
   - export root configuration
   - GP output root configuration
   - input/project root restrictions
   - generic GP enablement and allowlist
6. Keep exact `.aprx` paths, map names, layer names, layout names, table names, dataset paths, and output paths returned by discovery tools.

## Runtime Commands

Install and run from the repository root in ArcGIS Pro Python:

```bash
pip install -e .
python -m arcgis_pro_mcp
```

Validate source without requiring ArcGIS Pro:

```bash
pip install -e ".[dev]"
ruff check arcgis_pro_mcp arcgis_pro_mcp_bootstrap.py tests "接入当前窗口.py"
python -m compileall -q arcgis_pro_mcp
python -m py_compile arcgis_pro_mcp_bootstrap.py "接入当前窗口.py"
python -m unittest discover -s tests -p "test_*.py"
```

On Windows with ArcGIS Pro installed, also run the no-ArcPy tests through Pro's launcher when available:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -m unittest discover -s tests -p "test_*.py"
```

For real ArcPy availability, run a separate smoke test:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -c "import arcpy; print(arcpy.GetInstallInfo())"
```

## Safety Rules

- Start read-only unless the user explicitly asks to save, edit, select, export, write tables/features, run write GP, or modify layouts/maps.
- Never automatically translate an absolute `.aprx` path to `CURRENT` or vice versa. If a window workflow loses its host/session or changes projects, stop and re-read status. Treat a user-approved switch to file mode as a new workflow: require an exact absolute `.aprx` path and explain that active-view focus, zoom, and refresh have no file-mode equivalent.
- Verify `ARCGIS_PRO_MCP_ALLOW_WRITE=1` through environment/capabilities before write-gated tools.
- Verify `ARCGIS_PRO_MCP_EXPORT_ROOT` before layout/map/report exports or `saveACopy` outputs when export roots are configured.
- Verify `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` before write GP outputs.
- Respect `ARCGIS_PRO_MCP_INPUT_ROOTS` and `ARCGIS_PRO_MCP_PROJECT_ROOTS`; do not bypass path validation.
- Prefer named GP wrappers over `arcgis_pro_gp_run_tool`.
- Use generic GP only when `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP=1` and the exact tool is allowlisted in `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST`.
- Prefer `save_project_copy` or controlled output datasets over overwriting original `.aprx` or geodatabases.
- `_open_project` validates `aprx_path` through `validate_project_path`. Absolute project paths are cached by normalized path; standalone `CURRENT` calls are not cached, while the Pro host binds exactly one `CURRENT` project reference per request.

## Clients

Keep MCP `command` pointed at ArcGIS Pro Python and `cwd` at this repository. Config locations:

| Client | Skill discovery | MCP config |
| --- | --- | --- |
| Cursor | `.cursor/skills/arcgis-pro-mcp/` and `~/.cursor/skills/arcgis-pro-mcp/` | `.cursor/mcp.json`, `~/.cursor/mcp.json` |
| Grok | `.grok/skills/arcgis-pro-mcp/` and `~/.grok/skills/arcgis-pro-mcp/` | `~/.grok/config.toml`, `.grok/config.toml` |
| Codex | `skills/arcgis-pro-mcp/` (this directory) | `~/.codex/config.toml` |
| Claude Code / Desktop | `.claude/skills/arcgis-pro-mcp/` and `~/.claude/skills/arcgis-pro-mcp/` | `~/.claude.json`, `%APPDATA%\Claude\claude_desktop_config.json` |

Confirm live tools with `arcgis_pro_environment_info` after connecting. Restart the MCP client after changing skill or MCP config.

## Attach To The Open Pro Window

Default MCP is a separate ArcPy process. It does not drive the map you already have open. To make the Pro window follow MCP edits:

1. Open a saved project in ArcGIS Pro. An Untitled/unsaved project has no stable identity and must be saved before the host can attach.
2. Prefer adding `接入当前窗口.pyt` in Catalog and running **接入当前窗口**. The Python Window alternative is `import runpy; runpy.run_path(r"<absolute repo path>\接入当前窗口.py")`.
3. Leave that tool/Python command running.
4. Keep using the existing stdio MCP client config. The host uses an authenticated loopback session discovered from its private temporary state file.
5. Call `arcgis_pro_window_status`; require `window_attached=true`, `host_ready=true`, and `target_confirmed=true`, then verify `current_project`. This explicitly latches the session/project that later CURRENT calls may control.
6. Pass `aprx_path=CURRENT` only when the operation must affect the open window. Absolute `.aprx` paths always stay in file mode.

`CURRENT` is fail-closed: if the host is unavailable, not ready, has restarted, or the project changed while the request was queued, the call fails instead of falling back to a file target. The host applies the write and path policy forwarded from the stdio MCP configuration; it does not force-enable writes or invent output roots.

After a host restart, same-port replacement, or project switch, call `arcgis_pro_window_status` again and verify the new target before any CURRENT call. Ordinary health probes do not silently retarget the latch.

Only tools with an explicit `aprx_path` parameter are eligible for window routing. Do not send path-only DA/GP tools directly to the host RPC. The default port permits one discoverable host; for multiple Pro instances, configure a distinct `ARCGIS_PRO_MCP_HOST_PORT` in each Pro process and its matching MCP client before starting the host.

Stop by cancelling the `.pyt`, pressing Ctrl+C in the Python Window, or calling `arcgis_pro_detach_window`. Before loading changed repository code, wait until Pro prints `窗口宿主已停止`, then run the launcher again. The launchers use the package-external `arcgis_pro_mcp_bootstrap.py` to discard the complete cached `arcgis_pro_mcp.*` generation and rebuild the host, helpers, and unproxied FastMCP manager from this checkout. They deliberately reject reload/purge while a host is active. When first upgrading an already loaded Python toolbox to the bootstrap-based launcher, refresh it in Catalog (or remove and re-add it), or use the Python Window `runpy.run_path(...)` entry once; Pro may still hold the old `AttachWindow` class itself. Restart ArcGIS Pro only if toolbox refresh does not replace that old entry code or an interrupted run leaves the in-host marker stuck. Restart the MCP client as well after stdio, tool-registration, or protocol changes because refreshing the Pro host does not refresh that separate process.

The Python host is an exclusive foreground bridge, not general GUI automation. It can manipulate `ArcGISProject("CURRENT")`, active map views, layers, selections, layouts, and refresh requests, but it cannot click arbitrary Ribbon/dialog UI or provide fully concurrent user interaction. A production-quality concurrent window controller requires an ArcGIS Pro SDK add-in using `QueuedTask`, the UI dispatcher, and Pro events.

For a live-window action:

1. Re-read `arcgis_pro_window_status` immediately before a consequential write and confirm the expected project and session. If the host is busy, do not blindly queue another write; wait only within the user's requested time budget, then re-read status.
2. Use `arcgis_pro_active_view_info` to confirm whether a map or layout is active; open the intended map/layout explicitly when needed. If “current map” is not uniquely determined, stop and ask for the target.
3. Discover layers before targeting one. If short names are duplicated in groups, use the returned exact `long_name` or `uri`; never assume the first match. Maps and layouts can likewise be selected by returned URI when names collide.
4. Apply the smallest semantic tool. Use active-view extent/zoom tools for the screen camera and `arcgis_pro_refresh_layer` after an in-place update that has not redrawn or whenever the user explicitly requests a refresh.
5. For attribute or location selections, require `selection_verified=true` and use `selected_count` as the exact selection size. A mismatch between ArcPy's derived count and `getSelectionSet()` is an ambiguous write outcome: stop, re-read selection state, and do not automatically retry. Selection writes against CURRENT request a visible-layer refresh.
6. Re-read the changed layer/project/view. When the user explicitly asks to save the current writable project, use `arcgis_pro_save_project`; if the reference is read-only, do not silently substitute a save-copy operation.
7. Never automatically retry a write after a running-call timeout; its result may be unknown. Re-read state and outputs first.

## Workflow Patterns

Read-only project review:

1. `arcgis_pro_environment_info`
2. `arcgis_pro_server_capabilities`
3. `arcgis_pro_list_projects`
4. `arcgis_pro_project_summary`
5. `arcgis_pro_list_maps`, `arcgis_pro_list_layouts`, `arcgis_pro_list_layers`
6. `arcgis_pro_describe`, `arcgis_pro_list_fields`
7. `arcgis_pro_da_table_sample`, `arcgis_pro_da_query_rows`, or `arcgis_pro_da_distinct_values`

Safe layer/layout/export update:

1. Verify write/export gates in capabilities
2. Confirm target project/map/layer/layout exists
3. Apply the smallest specific tool
4. Re-read the changed object
5. Save only when requested, preferably as a copy

GP workflow:

1. Confirm input dataset with `arcgis_pro_describe`, `arcgis_pro_list_fields`, or `arcgis_pro_gp_get_count`
2. Confirm output root and output path
3. Use a named GP wrapper when possible
4. Read GP messages or verify output existence/counts

Temporary view workflow:

1. `arcgis_pro_make_feature_layer` or `arcgis_pro_make_table_view`
2. Apply selection/query/symbology/GP operations to the temporary layer or view name
3. Persist only when the user asks for a durable output

## References

- Read `references/security-and-paths.md` for environment variables and write gates.
- Read `references/runtime-notes.md` before live `CURRENT` work, DA writes, layout/map-frame edits, symbology, or GP that measures distance.
- Read `references/tools.md` for grouped ArcGIS Pro MCP tool names.
- Read `references/development.md` before editing server registration, GP helpers, path validation, or tests.
