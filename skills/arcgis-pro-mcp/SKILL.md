---
name: arcgis-pro-mcp
description: Use the ArcGIS Pro MCP server for ArcGIS Pro and ArcPy automation, including .aprx project inspection, maps, layers, layouts, reports, exports, table and feature-class queries, geodatabases, rasters, metadata, symbology, labels, selections, constrained arcpy.da writes, named geoprocessing tools, generic GP allowlist workflows, and network analysis. Use when a task mentions ArcGIS Pro, ArcPy, .aprx, map documents, layouts, feature classes, geodatabases, shapefiles, rasters, GP tools, spatial analysis, layer rendering, labels, bookmarks, map frames, or ArcGIS exports.
---

# ArcGIS Pro MCP

## Overview

Use this skill to operate this repository's ArcGIS Pro MCP server safely. The server exposes a constrained subset of `arcpy.mp`, `arcpy.da`, and ArcPy geoprocessing through stdio MCP tools.

Repository-local facts:

- Source root: `/root/arcgis-mcp-service`
- Python package: `arcgis-pro-mcp`
- MCP server name: `arcgis-pro`
- Runtime module: `arcgis_pro_mcp`
- Transport: stdio
- Tool source: `arcgis_pro_mcp/server.py` and helper modules under `arcgis_pro_mcp/`

## Hard Runtime Requirement

Real MCP execution requires Windows, ArcGIS Pro, and ArcGIS Pro's bundled Python. A common Python path is:

```text
C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
```

Linux or non-ArcGIS Python can inspect the repository and run some tests, but cannot import `arcpy` or execute real ArcGIS Pro operations.

## Required First Calls

1. Confirm the current agent session actually has `arcgis_pro_*` MCP tools exposed. If not, inspect source/config and say live MCP execution is unavailable.
2. Call `arcgis_pro_environment_info`.
3. Call `arcgis_pro_server_capabilities`.
4. Treat `arcgis_pro_server_capabilities` as authoritative for:
   - available read/write/export tools
   - `allow_write`
   - export root configuration
   - GP output root configuration
   - input/project root restrictions
   - generic GP enablement and allowlist
5. Keep exact `.aprx` paths, map names, layer names, layout names, table names, dataset paths, and output paths returned by discovery tools.

## Runtime Commands

Install and run from the repository root in ArcGIS Pro Python:

```bash
pip install -e .
python -m arcgis_pro_mcp
```

Validate source without requiring ArcGIS Pro:

```bash
python -m compileall arcgis_pro_mcp
python -m unittest discover -s tests -p "test_*.py"
```

## Safety Rules

- Start read-only unless the user explicitly asks to save, edit, select, export, write tables/features, run write GP, or modify layouts/maps.
- Verify `ARCGIS_PRO_MCP_ALLOW_WRITE=1` through environment/capabilities before write-gated tools.
- Verify `ARCGIS_PRO_MCP_EXPORT_ROOT` before layout/map/report exports or `saveACopy` outputs when export roots are configured.
- Verify `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` before write GP outputs.
- Respect `ARCGIS_PRO_MCP_INPUT_ROOTS` and `ARCGIS_PRO_MCP_PROJECT_ROOTS`; do not bypass path validation.
- Prefer named GP wrappers over `arcgis_pro_gp_run_tool`.
- Use generic GP only when `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP=1` and the exact tool is allowlisted in `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST`.
- Prefer `save_project_copy` or controlled output datasets over overwriting original `.aprx` or geodatabases.

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
- Read `references/tools.md` for grouped ArcGIS Pro MCP tool names.
- Read `references/development.md` before editing server registration, GP helpers, path validation, or tests.
