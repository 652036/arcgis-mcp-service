# ArcGIS Pro MCP Development Notes

## Source Layout

- `arcgis_pro_mcp/server.py`: FastMCP registration and thin JSON adapters.
- `arcgis_pro_mcp/tool_protocol.py`: structured-result wrapping, generated descriptions, annotations, execution-mode flags, gates, and `arcgis_pro_tool_info` catalog.
- `arcgis_pro_mcp/paths.py`: shared roots and write/destructive/CIM/publish/enterprise policy gates.
- `arcgis_pro_mcp/private_state.py`, `redaction.py`: private bridge-state files and centralized secret/error redaction.
- `arcgis_pro_mcp/pro_attach.py`, `pro_host.py`: Python CURRENT discovery, authenticated proxy, protocol-v4 queue/jobs/change wait.
- `arcgis_pro_mcp_bootstrap.py`, `接入当前窗口.py`, `接入当前窗口.pyt`: source-checkout bootstrap and Pro launchers.
- `arcgis_pro_mcp/sdk_bridge.py`: sanitized Python client for the optional native SDK bridge.
- `sdk/ArcGISProMcp.AddIn/`: ArcGIS Pro SDK for .NET add-in source, native context/events/view/edit/GP implementation, and wire-protocol reference.

Capability helpers are split by domain:

- project/map/data: `project_catalog.py`, `project_io.py`, `dataset_management.py`, `workspace_listing.py`, `da_read.py`, `da_write.py`, `session_refs.py`;
- mapping/presentation: `cartography.py`, `symbology.py`, `charts.py`, `metadata.py`;
- edits/schema/integrity: `editing.py`, `schema_maintenance.py`, `data_integrity.py`;
- analysis: `gp_*.py`, `live_analysis.py`, `raster_runtime.py`, `raster_advanced.py`, `lidar.py`, `spatial_modeling.py`, `network_analysis.py`, `geocoding.py`;
- enterprise/external: `enterprise_gdb.py`, `utility_network.py`, `publishing.py`.

## Adding Or Changing A Tool

1. Put ArcPy logic in the relevant helper module and inject the `arcpy` dependency so policy behavior can be unit-tested without ArcGIS Pro.
2. Validate typed enums/numbers, exact target identity, input roots, output roots, existence/nonexistence, extension/data preconditions, schema lock, and confirmation before invoking ArcPy.
3. Register a thin explicit `arcgis_pro_*` wrapper in `server.py`. Keep the direct Python function's historical JSON-string behavior.
4. Classify the tool conservatively in `tool_protocol.py`: read-only, destructive, idempotent, open-world, `CURRENT`/SDK requirement, base and conditional gates, export or GP output roots, and enterprise/SDK special gates.
5. Update `arcgis_pro_server_capabilities` if the capability summary or reported deployment state changes. Do not rely on name heuristics when a tool is an exception.
6. Add module tests for validation and exact ArcPy call shape, plus registry/protocol tests for structured output and gate metadata.
7. Update README/changelogs and this canonical skill when public behavior, environment variables, modes, protocol, or a major tool group changes.

Every registered tool should have an object input schema, object structured output, a useful description, and conservative MCP annotations after `tool_protocol.finalize_tool_registry(mcp)`. `arcgis_pro_tool_info` must accurately describe the running wrapper.

## Runtime And Protocol Invariants

- Real execution requires ArcGIS Pro Python with `arcpy`. Cross-platform tests prove syntax and mocked contracts only.
- Before claiming an MCP client runs this checkout, verify its `command`, `args`, `cwd`, environment, and exposed tool catalog.
- File-mode project cache keys are normalized absolute paths. `CURRENT` is never cached across requests.
- Route only explicit `aprx_path=CURRENT` calls to the Python host. Never fall back to a disk path.
- ArcPy work must not run in HTTP worker threads. The Python host uses its bounded serialized queue and one bound CURRENT project per request.
- Keep the Python host's `PROTOCOL_VERSION`, state/auth fields, forwarded policy allowlist, client validation, and host validation synchronized. Cover changes in attach, HTTP, host, job, and toolbox tests.
- Keep Python CURRENT discovery in the current-user private state directory. Do not bypass `private_state.py` atomic writes, owner/protected-DACL checks, bounded reads, or link/reparse-point rejection.
- The package-external bootstrap must purge the whole `arcgis_pro_mcp.*` module generation only after the old host stops. Partial reloads can combine incompatible `pro_host`/`pro_attach` definitions.
- Keep Python `sdk_bridge.py` request/response members synchronized with the C# add-in. Unknown JSON members fail closed; public Python status must redact bearer and lease secrets.
- SDK mutations must preserve exact project leases and context/selection/edit generation checks. GP tools require reviewed typed contracts; adding a name to an allowlist is insufficient.

## Validation

From the repository root in an ordinary development environment:

```powershell
python -m pip install -e ".[dev]"
ruff check .
python -m compileall -q arcgis_pro_mcp
python -m py_compile arcgis_pro_mcp_bootstrap.py "接入当前窗口.py" "接入当前窗口.pyt"
python -m unittest discover -s tests -p "test_*.py"
```

The project intentionally requires `mcp>=1.20,<2`; registration tests must use a compatible version.

On a Windows workstation with ArcGIS Pro, run the relevant unit modules through Pro Python and verify real ArcPy availability:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -c "import arcpy; print(arcpy.GetInstallInfo())"
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -m unittest discover -s tests -p "test_*.py"
```

When the .NET/ArcGIS Pro SDK toolchain is available, restore and build the add-in from its project directory:

```powershell
dotnet restore .\ArcGISProMcp.AddIn.csproj
dotnet build .\ArcGISProMcp.AddIn.csproj -c Release
```

A green mocked suite or metadata-only C# check does not prove real `.aprx`, UI-threading, extension, network dataset, enterprise geodatabase, Utility Network, locator, Portal, or publication behavior. Run a controlled live smoke test with disposable data for the changed domain and report exactly what was and was not exercised.

## Documentation And Open-Source Hygiene

- Canonical skill files live only in `skills/arcgis-pro-mcp/`. Client-specific skill directories may be junctions; do not create divergent copies.
- Do not commit MCP client configs, SDK/Python discovery state, bearer or lease material, `.sde`/`.ags` files, credentials, real customer paths, user-profile paths, private Portal/server URLs, or production data samples.
- Use synthetic fixtures and neutral path placeholders. Before publishing, inspect tracked/untracked files, `git diff --check`, and the complete diff for secrets and machine-specific paths.
- Keep `README.md`, `docs/WINDOW_CONTROL.md`, `SECURITY.md`, changelogs, SDK protocol documentation, capabilities, tool metadata, and skill references consistent with the implementation.
