# ArcGIS Pro MCP Development Notes

## Registration And Modules

- Main MCP registration lives in `arcgis_pro_mcp/server.py`.
- Path policy and environment gates live in `arcgis_pro_mcp/paths.py`.
- GP wrappers are split across `gp_analysis.py`, `gp_convert.py`, `gp_create.py`, `gp_network.py`, `gp_raster.py`, `gp_schema.py`, `gp_write.py`, and `gp_generic.py`.
- Data-access helpers live in `da_read.py` and `da_write.py`.
- Symbology and metadata helpers live in `symbology.py` and `metadata.py`.

## Runtime Boundary

- Real execution requires ArcGIS Pro Python with `arcpy`.
- Do not claim a Linux validation proves ArcPy behavior. Linux checks cover syntax and no-ArcPy unit tests only.
- Before claiming a live MCP host uses this checkout, inspect the active MCP client config and verify `command`, `args`, `cwd`, and environment variables.

## Tests

Use these checks for repository validation:

```bash
pip install -e ".[dev]"
ruff check arcgis_pro_mcp arcgis_pro_mcp_bootstrap.py tests "接入当前窗口.py"
python -m compileall -q arcgis_pro_mcp
python -m py_compile arcgis_pro_mcp_bootstrap.py "接入当前窗口.py"
python -m unittest discover -s tests -p "test_*.py"
```

On a Windows workstation with ArcGIS Pro installed, run the no-ArcPy tests through Pro's launcher too:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -m unittest discover -s tests -p "test_*.py"
```

Use this smoke test only when you need to prove real ArcPy is usable on the machine:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -c "import arcpy; print(arcpy.GetInstallInfo())"
```

Existing no-ArcPy tests cover path policy, generic GP gating, server registration shape, project-open caching, and GP helper behavior. Real ArcPy behavior still needs Windows/ArcGIS Pro validation.

## Project Opening

- `_open_project` validates `aprx_path` with `validate_project_path` before opening the project.
- `CURRENT` bypasses absolute-path and project-root checks. Standalone calls do not cache it; the Pro host binds exactly one CURRENT `ArcGISProject` reference for each request so target validation and tool execution use the same object.
- Project file paths are cached in-process by normalized absolute lowercase path to avoid repeatedly constructing `arcpy.mp.ArcGISProject` for the same `.aprx`.
- Window host lives in `arcgis_pro_mcp/pro_host.py`; stdio auto-forward lives in `arcgis_pro_mcp/pro_attach.py`.
- The source-checkout launchers share the package-external `arcgis_pro_mcp_bootstrap.py`. After the old foreground host stops, it purges every cached `arcgis_pro_mcp` module and fresh-imports `pro_attach`, `pro_host`, `server`, and helpers. Keep it outside the package so a stale package cannot prevent bootstrapping. Never mutate a running module generation; the persistent process lock and `ARCGIS_PRO_MCP_IN_PRO_HOST` marker must fail closed.
- Window protocol changes must update `PROTOCOL_VERSION`, authenticated state discovery, `tests/test_pro_attach.py`, and `tests/test_pro_host.py`. Never execute ArcPy tool functions in the HTTP handler threads.
- Route only explicit `aprx_path=CURRENT` calls to the host. CURRENT must fail closed; absolute project paths must retain file-mode semantics.
- The host must not force write permission or hardcode policy roots. It receives only the allowlisted MCP policy environment keys from the authenticated stdio client for each call.
- Tests that call `_open_project` should clear `server._PROJECT_CACHE` in teardown or otherwise isolate cache state.

## Documentation Discipline

- Keep README tool summaries aligned with `arcgis_pro_server_capabilities`.
- Keep this skill updated when changing environment variables, write gates, path validation, generic GP behavior, or major tool groups.
- Keep `references/runtime-notes.md` updated when live ArcPy/Pro 3.x wrapper behavior changes.
- Canonical skill files live in `skills/arcgis-pro-mcp/`. `.cursor/skills/arcgis-pro-mcp`, `.claude/skills/arcgis-pro-mcp`, and `.grok/skills/arcgis-pro-mcp` are NTFS junctions to that directory so Cursor, Claude, and Grok discover the same copy. Do not duplicate SKILL.md into those trees.
- If adding a tool, decide whether it is read-only, write-gated, export-gated, or generic-GP related and expose that correctly in `arcgis_pro_server_capabilities`.
