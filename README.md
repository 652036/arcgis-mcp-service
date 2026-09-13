# ArcGIS Pro MCP

**English** | [简体中文](README.zh-CN.md)

Use ArcPy from an MCP client within explicit permission and path boundaries, and attach to a running ArcGIS Pro session when needed. The project supports project files on disk, a Python `CURRENT` host inside Pro, and an optional native ArcGIS Pro SDK control interface.

Version 2.0 provides 400+ registered tools for projects, maps, layers, layouts, data, cartography, rasters, LAS, spatial analysis, networks, enterprise geodatabases, publishing, and live-window control. The count changes with releases; use `arcgis_pro_server_capabilities()` and `arcgis_pro_tool_info()` as the runtime source of truth.

> Status: Beta. Real GIS execution requires Windows, ArcGIS Pro, and an ArcGIS Pro Python environment that can `import arcpy`. Licensed under the MIT License.

[Quick start](#quick-start) · [Execution modes](#execution-modes) · [Python CURRENT](#attach-to-the-python-current-host) · [SDK control](#native-control-with-the-sdk-add-in) · [Security](#security-model) · [Capabilities](#capabilities) · [Troubleshooting](#troubleshooting)

Recent work includes explicit clipping modes, full-extent coverage checks, and result qualification gates. See [GIS reliability scope and limitations](docs/GIS_RELIABILITY.md) (Chinese).

## Execution modes

Each mode serves a distinct purpose. Requests never silently fall back to another mode.

| Mode | Request identifier | Execution environment | Typical use | Controls the open Pro window |
| --- | --- | --- | --- | --- |
| Project file | Allowed absolute `.aprx` path | Separate ArcGIS Pro Python process | Batch processing, project inspection, data production, export | No |
| Python `CURRENT` host | Exact value `aprx_path="CURRENT"` | Python host running inside ArcGIS Pro | Current project, active view, selections, layouts, refresh, and most existing tools | Yes |
| SDK Add-In | Opaque `sdk_session_ref` | ArcGIS Pro SDK Add-In | Native events, DrawComplete, camera/time, cancellable GP, Undo/Redo, `EditOperation` | Yes |

```text
MCP client
    | stdio
    v
ArcGIS Pro MCP server
    +-- absolute .aprx ---------> Separate ArcPy process: project-file mode
    +-- aprx_path=CURRENT ------> Python host v4: current Pro window
    +-- arcgis_pro_sdk_* -------> SDK Add-In: leases / events / native edits
```

Absolute `.aprx` paths always use project-file mode. Only an explicit `CURRENT` request reaches the Python host. A disconnected or restarted host, or a changed project, causes the request to fail closed instead of modifying a project file on disk.

For live control, the Python toolbox/script reuses existing `arcpy.mp` tools; the SDK Add-In provides native events, responsive UI operations, and `EditOperation`. They have separate discovery, authentication, and control protocols. Neither automatically takes control of the current project.

See [live-window control architecture (Chinese)](docs/WINDOW_CONTROL.md) for protocol details.

## Capabilities

The runtime tool catalog identifies read/write behavior, required path roots, live-window requirements, and additional permission gates. Main areas include:

- Projects and catalogs: discovery, summaries, document import, project copies, cache release, connection repair, MAPX/LYRX.
- Maps and cartography: maps, layers, standalone tables, layouts, map frames, bookmarks, reports, charts, labels, symbology, controlled CIM writes, and export.
- Tables and features: fields, domains, indexes, constrained `arcpy.da` queries and writes, selections, relationships, editor tracking, GlobalIDs, attribute rules, field groups, and contingent values.
- Vector and spatial analysis: clip, overlay, buffer, join, conversion, spatial statistics, regression, clustering, space-time cubes, and forecasting.
- Rasters and terrain: properties, statistics, pyramids, NoData, map algebra, hydrology/distance analysis, mosaic datasets, LAS datasets, and pyramids.
- Networks and geocoding: routes, service areas, closest facilities, and OD cost matrices using local network datasets; batch and reverse geocoding with local locators.
- Enterprise data: geodatabase connections, versioning, reconcile/post, maintenance, Utility Network queries, validation, tracing, subnetworks, and export.
- Publishing: sharing drafts, service-definition staging, and publishing with separate gates for Portal/Server targets, public sharing, and service overwrites.
- Live control: Python `CURRENT` views and selections; SDK active context, events, camera, time, native edits, and cancellable allowlisted GP jobs.

This project does not provide arbitrary Python, CIM, or geoprocessing execution, or general desktop mouse automation. Generic GP is disabled by default. The SDK accepts only the typed contracts implemented in the code.

## Requirements

- Windows, meeting the system requirements of the target ArcGIS Pro version.
- ArcGIS Pro and its bundled or cloned Python environment.
- Python 3.10+.
- `mcp>=1.20,<2`.
- For the optional SDK Add-In: ArcGIS Pro 3.6, .NET 8 / Visual Studio 2022, and ArcGIS Pro SDK for .NET 3.6.

A typical Python location is shown below; installation paths and environment names may differ:

```text
C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
```

## Quick start

### 1. Install

Use a writable cloned ArcGIS Pro Python environment:

```powershell
git clone https://github.com/652036/arcgis-mcp-service.git
Set-Location arcgis-mcp-service
python -m pip install -e .
python -c "import arcpy; print(arcpy.GetInstallInfo()['Version'])"
```

The server communicates over stdio. Running `python -m arcgis_pro_mcp` manually is useful for troubleshooting; normally the MCP client launches it.

### 2. Configure your MCP client

The following is a generic `mcpServers` example. Replace the interpreter, repository, and data paths with your own values and grant access only to the directories you need. Do not commit local credentials or real internal paths to a public repository.

```json
{
  "mcpServers": {
    "arcgis-pro": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": ["-m", "arcgis_pro_mcp"],
      "cwd": "C:\\path\\to\\arcgis-mcp-service",
      "env": {
        "ARCGIS_PRO_MCP_ALLOW_WRITE": "0",
        "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "0",
        "ARCGIS_PRO_MCP_INPUT_ROOTS": "C:\\GIS_Data",
        "ARCGIS_PRO_MCP_PROJECT_ROOTS": "C:\\GIS_Projects",
        "ARCGIS_PRO_MCP_EXPORT_ROOT": "C:\\GIS_Outputs",
        "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": "C:\\GIS_Outputs\\GP",
        "ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST": "SQL_SERVER|db.example.internal",
        "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "0"
      }
    }
  }
}
```

Separate multiple input or project roots with `;` on Windows. Restart the MCP client after changing configuration. Reattach the Python host so that it receives the updated policy snapshot.

### 3. Discover runtime capabilities

Start each session with:

```text
arcgis_pro_environment_info()
arcgis_pro_server_capabilities()
```

Inspect a tool's full schema, risks, and prerequisites:

```text
arcgis_pro_tool_info(name="arcgis_pro_network_solve_route")
```

Use the runtime catalog instead of a static README tool list. `arcgis_pro_server_capabilities()` returns registered tools, read/write classifications, path requirements, extra gates, and window status.

### 4. Work with a project file

```text
arcgis_pro_project_summary(aprx_path="C:\\GIS_Projects\\demo.aprx")
arcgis_pro_list_maps(aprx_path="C:\\GIS_Projects\\demo.aprx")
arcgis_pro_list_layers(aprx_path="C:\\GIS_Projects\\demo.aprx", map_name="Map")
```

Project-file mode supports automation and reproducible processing. It does not represent the active pane or immediately update the map visible in Pro.

## Attach to the Python CURRENT host

The Python host reuses many tools with an `aprx_path` parameter and is the simplest way to attach to the current project.

1. Open and save the target project in ArcGIS Pro.
2. Add `接入当前窗口.pyt` from the repository root to Catalog. Run the tool named “接入当前窗口” (attach to the current window) and leave it running.
3. Call `arcgis_pro_window_status()`.
4. Require `window_attached=true`, `host_ready=true`, and `target_confirmed=true`. Check that `current_project` is the intended project.
5. Run a read-only smoke test with `arcgis_pro_active_view_info(aprx_path="CURRENT")`.
6. Pass `aprx_path="CURRENT"` only when you intend to control the open Pro window.

The launcher prefers a compatible FastMCP installation in the repository's `.arcgis-pro-mcp-deps` directory to avoid changing Pro's system environment. If startup reports `No module named 'mcp'`, run the following in PowerShell with your actual paths:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" -m pip install --target "C:\path\to\arcgis-mcp-service\.arcgis-pro-mcp-deps" "mcp>=1.20,<2"
```

Run the attachment tool again after installation. Do not use `ArcGISPro.exe -m pip`.

You can also launch the host from the ArcGIS Pro Python window:

```python
import runpy
runpy.run_path(r"C:\path\to\arcgis-mcp-service\接入当前窗口.py")
```

Protocol v4 uses random session tokens, loopback-only networking, atomic private discovery files, a locked target project, a bounded serial queue, queued cancellation, and job/event status. On Windows, discovery state is stored under the current user's `%LOCALAPPDATA%\ArcGISProMcp\window-host` with protected user ACLs. Readers reject links/reparse points, unexpected sizes, incorrect owners, and non-private DACLs. These files contain local capability credentials: do not copy, share, or commit them. Call `arcgis_pro_window_status()` again after a project change, host restart, or session change.

The Python tool/window runs in Pro's foreground and suits periods of exclusive agent control. Long jobs can limit Pro interaction. Use the SDK Add-In for continuous events, native Undo/Redo, DrawComplete, or cancellable background jobs.

## Native control with the SDK Add-In

[`sdk/ArcGISProMcp.AddIn`](sdk/ArcGISProMcp.AddIn) contains the ArcGIS Pro 3.6 Add-In source. Build and install it separately; installing the Python package does not load the Add-In:

```powershell
Set-Location sdk\ArcGISProMcp.AddIn
dotnet restore .\ArcGISProMcp.AddIn.csproj
dotnet build .\ArcGISProMcp.AddIn.csproj -c Release
```

The Add-In listens only on `127.0.0.1` at a system-assigned port, generates random bearer/session tokens each time it loads, and restricts discovery files to the current Windows user. MCP responses do not expose tokens or lease secrets.

A typical workflow is:

```text
arcgis_pro_sdk_bridge_status()
arcgis_pro_sdk_acquire_project_lease(expected_project_uri="C:\\GIS_Projects\\demo.aprx")
arcgis_pro_sdk_context(sdk_session_ref="...")
arcgis_pro_sdk_set_camera(..., expected_context_generation=..., confirm=true)
arcgis_pro_sdk_wait_events(...)
arcgis_pro_sdk_release_project_lease(...)
```

A lease binds to one exact, saved `.aprx`, lasts 45 seconds by default, and can be renewed. Only one controller can hold the Add-In lease at a time. A changed project, expired lease, Add-In restart, or URI mismatch fails closed.

Native SDK control provides:

- Consistent snapshots of the active view, camera, layers, selection summaries, time, and multiple generation counters.
- Camera updates, zoom by URI, refresh with DrawComplete waiting, time ranges, and opening loaded tables.
- Bounded long polling for pane, camera, selection, edit, draw, time, and project events.
- `EditOperation` create/update/delete operations, native Undo/Redo, and save/discard editing.
- Asynchronous GP jobs, status, and cooperative cancellation within typed contracts, dual allowlists, and input/output path constraints.

SDK write requests use `expectedMapUri`, context/selection/edit generations, selection counts, and OID digests for compare-and-swap checks. Read the context again after a conflict before retrying. See the [SDK bridge README](sdk/ArcGISProMcp.AddIn/README.md) for endpoints and request contracts.

## Security model

Ordinary reads and writes are enabled by default. Set `ARCGIS_PRO_MCP_ALLOW_WRITE=0` to make the server read-only. Other high-risk gates default to disabled. Deletion, publishing, enterprise maintenance, CIM operations, and SDK editing also require their narrower gates, exact target confirmation, and path policies.

| Setting | Purpose |
| --- | --- |
| `ARCGIS_PRO_MCP_ALLOW_WRITE` | General write gate, enabled by default; disable with `0`, `false`, `no`, or `off` |
| `ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=1` | Destructive operations such as delete, overwrite, and discard |
| `ARCGIS_PRO_MCP_ALLOW_CIM_WRITE=1` | Raw CIM writes |
| `ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE=1` | Enterprise versioning, maintenance, and Utility Network administration; does not replace ordinary feature/row write authorization |
| `ARCGIS_PRO_MCP_ALLOW_PUBLISH=1` | Publishing operations |
| `ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE=1` | Sharing with `EVERYONE` |
| `ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE=1` | Overwriting existing published services |
| `ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD=1` | Explicit inline database passwords; prefer environment variables or connection files |
| `ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST` | Exact database targets in `platform\|instance` format; required when creating `.sde` files |
| `ARCGIS_PRO_MCP_DB_USERNAME` / `ARCGIS_PRO_MCP_DB_PASSWORD` | Fixed credential variables for database connection creation; tools cannot choose other variable names |
| `ARCGIS_PRO_MCP_INPUT_ROOTS` | Allowed input data roots |
| `ARCGIS_PRO_MCP_PROJECT_ROOTS` | Allowed `.aprx` roots; falls back to input roots when unset |
| `ARCGIS_PRO_MCP_EXPORT_ROOT` | Root for map, layout, report, chart, and audit exports |
| `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` | Required output root for GP operations that write data |
| `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP=1` + `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST` | Both gates required for generic Python GP |
| `ARCGIS_PRO_MCP_PORTAL_ALLOWLIST` / `ARCGIS_PRO_MCP_SERVER_ALLOWLIST` | Allowed publishing and enterprise targets |
| `ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST` / `ARCGIS_PRO_MCP_SDK_GP_ENV_ALLOWLIST` | SDK GP tool and environment allowlists |
| `ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS=1` | SDK Undo/Redo/save commands |
| `ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS=1` | Native SDK feature create/update/delete operations |
| `ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS=1` | SDK discard of all pending edits |
| `ARCGIS_PRO_MCP_HOST_PORT` | Bind a Python `CURRENT` host to an explicit loopback port |

Additional rules:

- Do not put passwords, Portal tokens, host bearer tokens, lease IDs, or connection strings in the repository, issues, logs, or screenshots.
- Prefer existing ArcGIS-managed database connection files. Creating a new `.sde` requires a match in `ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST` and reads only the fixed credential variables. Inline passwords are rejected and credentials are not saved by default.
- GP writes must stay within configured output roots. Generic GP requires both enablement and an exact allowlist match, plus at least one complete `out_*` path per call. Split output container/name forms, in-place/no-output operations, destructive tools, and code execution are rejected.
- Generic GP and `CURRENT` analysis reject existing outputs and enforce `overwriteOutput=False`. Map, layout, report, chart, project-copy, local publishing-draft, and service-definition exports also require new files. Remote service overwrite uses its separate publishing gate.
- `arcgis_pro_gp_calculate_field` accepts restricted pure Arcade expressions, not Python/VB, code blocks, or remote data fetching. Label expressions also require Arcade; related CIM writes require the CIM gate. `arcgis_pro_gp_repair_geometry` always uses `KEEP_NULL` to preserve null-geometry records.
- Ordinary feature/row edits require the general write gate. Deletion also requires the destructive gate; native SDK feature editing also requires the SDK feature gate. The enterprise gate adds protection only for enterprise version management, maintenance, and Utility Network administration.
- Delete/overwrite tools usually require an `expected_count`, target path, or dedicated `confirm_*` parameter.
- Project-file cache operations `arcgis_pro_release_project` and `arcgis_pro_reload_project` do not save pending changes. Both require WRITE + DESTRUCTIVE and an exact match between `confirm_aprx_path` and `aprx_path`.
- A timeout does not establish that an operation failed. Re-read the project, selection, or outputs before retrying a non-idempotent action.
- Do not proxy, forward, or expose the Python host or SDK loopback ports to other machines.

See [SECURITY.md](SECURITY.md) and the [skill security matrix](skills/arcgis-pro-mcp/references/security-and-paths.md) for the full policy.

## Troubleshooting

### `cannot import name 'FORWARDED_ENV_KEYS'`

This usually means Pro still has an older `arcgis_pro_mcp.pro_attach` module cached while loading a newer `pro_host`. The version 2.0 external bootstrap clears and reloads the complete `arcgis_pro_mcp.*` package generation. Stop the old host before reloading an upgraded toolbox:

1. Cancel the running `.pyt` or press Ctrl+C in the Python window, then wait for the host-stopped message (“窗口宿主已停止”).
2. Make sure the checkout contains one complete version, rather than a mixture of individual `.py` files.
3. Refresh the toolbox in Catalog. If the old entry remains, remove and re-add `接入当前窗口.pyt`.
4. Alternatively, run the `.py` entry once with `runpy.run_path(...)` in the Python window.
5. Start the host again and restart the MCP client. Restart Pro only if it still retains stale classes or interruption state.

Do not force `importlib.reload()` while the host is running; this can mix module generations and tool registrations.

### `No module named 'mcp'`

Use the ArcGIS Pro environment's `python.exe`, not `ArcGISPro.exe -m pip`. Install `mcp>=1.20,<2` into the checkout-local `.arcgis-pro-mcp-deps` directory using the command in the attachment section, then run the attachment tool again. The launcher discovers that directory automatically.

### `window_attached=false` or `target_confirmed=false`

- Confirm that the project is saved, the host is running, and the stdio server and Pro use the same host port.
- Call `arcgis_pro_window_status()` after every host restart or project switch.
- Give each Pro instance a different `ARCGIS_PRO_MCP_HOST_PORT`; do not rely on the first window found.

### SDK bridge missing or multiple bridges found

- Verify that the Add-In is installed and loaded in the running Pro instance.
- With multiple instances, inspect redacted status and pass a PID to `arcgis_pro_sdk_bridge_status(process_id=...)`.
- Rediscover the bridge and acquire a new lease after discovery/lease expiry. Do not reuse an old `sdk_session_ref`.

### A tool is listed but its call is rejected

Call `arcgis_pro_tool_info(name="...")` to inspect required gates, roots, `CURRENT` attachment, or SDK context. Restart the MCP client after environment changes and reattach the host.

## Development and validation

Standard Python can validate syntax, lint, and tests without ArcPy. It does not establish actual ArcPy behavior:

```powershell
python -m pip install -e ".[dev]"
ruff check .
python -m compileall -q arcgis_pro_mcp
python -m py_compile arcgis_pro_mcp_bootstrap.py "接入当前窗口.py" "接入当前窗口.pyt"
python -m unittest discover -s tests -p "test_*.py"
```

Validate runtime changes with the target ArcGIS Pro Python environment. SDK changes also need a build using the ArcGIS Pro SDK 3.6 toolchain and validation inside Pro. Read [CONTRIBUTING.md](CONTRIBUTING.md) before contributing.

## Documentation

- [English guide](README.md) / [中文指南](README.zh-CN.md)
- [Live-window control architecture (Chinese)](docs/WINDOW_CONTROL.md)
- [SDK Add-In protocol and build instructions](sdk/ArcGISProMcp.AddIn/README.md)
- [Security policy](SECURITY.md)
- [English changelog](CHANGELOG.md) / [中文更新日志](CHANGELOG.zh-CN.md)
- [Contribution guide](CONTRIBUTING.md)
- [GIS reliability scope and limitations (Chinese)](docs/GIS_RELIABILITY.md)

## License

[MIT](LICENSE)
