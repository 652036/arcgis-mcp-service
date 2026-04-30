# ArcGIS Pro MCP Agent Guide

## Runtime Boundary

- Real MCP execution requires Windows, ArcGIS Pro, and ArcGIS Pro's bundled Python with `arcpy`.
- Treat this repository as a source checkout until the active MCP client config proves the live `command`, `args`, `cwd`, and environment variables.
- Linux validation is limited to syntax and no-ArcPy unit tests; it does not prove ArcPy runtime behavior.

## Safety

- Start with `arcgis_pro_environment_info` and `arcgis_pro_server_capabilities`.
- Do not run write-gated tools unless `ARCGIS_PRO_MCP_ALLOW_WRITE` is enabled.
- Respect `ARCGIS_PRO_MCP_EXPORT_ROOT`, `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT`, `ARCGIS_PRO_MCP_INPUT_ROOTS`, and `ARCGIS_PRO_MCP_PROJECT_ROOTS`.
- Prefer named GP wrappers. Use `arcgis_pro_gp_run_tool` only when generic GP is enabled and the exact tool is allowlisted.
- Prefer saving project copies or controlled outputs over overwriting source `.aprx` files or geodatabases.

## Development

- Main MCP tool registration lives in `arcgis_pro_mcp/server.py`.
- Path and write policy lives in `arcgis_pro_mcp/paths.py`; keep policies centralized.
- Run `python -m compileall arcgis_pro_mcp` and `python -m unittest discover -s tests -p "test_*.py"` before pushing.
- Run `ruff check .` when the dev dependency is available.

## Skill

- Repo-local Codex skill: `skills/arcgis-pro-mcp/SKILL.md`.
- Keep the skill and `skills/arcgis-pro-mcp/references/` updated when changing MCP tool names, write gates, path policies, generic GP behavior, or recommended workflows.
