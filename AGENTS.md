# ArcGIS Pro MCP Agent Guide

## Runtime Boundary

- Real MCP execution requires Windows, ArcGIS Pro, and ArcGIS Pro's bundled Python with `arcpy`.
- Treat this repository as a source checkout until the active MCP client config proves the live `command`, `args`, `cwd`, and environment variables.
- Linux validation is limited to syntax and no-ArcPy unit tests; it does not prove ArcPy runtime behavior.

## Safety

- Start with `arcgis_pro_environment_info` and `arcgis_pro_server_capabilities`.
- Do not run write-gated tools unless `ARCGIS_PRO_MCP_ALLOW_WRITE` is enabled.
- Respect `ARCGIS_PRO_MCP_EXPORT_ROOT`, `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT`, `ARCGIS_PRO_MCP_INPUT_ROOTS`, and `ARCGIS_PRO_MCP_PROJECT_ROOTS`.
- Prefer native GP tools through `arcgis_pro_gp_run_tool` for supported file-based operations. Generic GP is enabled by default without a deployment tool allowlist; confirm the native name and parameter contract. Use dedicated wrappers for CURRENT context, checked quality workflows, or operations outside the generic new-output contract.
- Prefer saving project copies or controlled outputs over overwriting source `.aprx` files or geodatabases.
- To drive the open Pro GUI, the user must run `接入当前窗口.pyt` or `接入当前窗口.py` in Pro; then use `aprx_path=CURRENT`. Verify `arcgis_pro_window_status.host_ready` and `current_project` first. CURRENT calls fail closed if the authenticated host/session is unavailable or the project changes; absolute `.aprx` paths remain file mode.

## Development

- Main MCP tool registration lives in `arcgis_pro_mcp/server.py`.
- Path and write policy lives in `arcgis_pro_mcp/paths.py`; keep policies centralized.
- Run `python -m compileall arcgis_pro_mcp` and `python -m unittest discover -s tests -p "test_*.py"` before pushing.
- Run `ruff check .` when the dev dependency is available.
- Tests require `mcp>=1.20,<2`. If system Python has MCP 2.x, use the checkout-local dependencies in a scoped PowerShell session (do not modify the global installation):

```powershell
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Resolve-Path .arcgis-pro-mcp-deps).Path
    python -c "from mcp.server.fastmcp import FastMCP"
    python -m unittest discover -s tests -p "test_*.py"
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
```

If that directory is absent, install the project's declared dependencies in a dedicated virtual environment first. The ordinary suite skips native QA; passing it does not prove ArcPy/GDAL execution.

## Skill

- Canonical skill: `skills/arcgis-pro-mcp/SKILL.md` (Cursor, Grok, Codex, Claude).
- Keep the skill and `skills/arcgis-pro-mcp/references/` updated when changing MCP tool names, write gates, path policies, generic GP behavior, or recommended workflows.
