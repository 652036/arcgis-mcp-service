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
python -m compileall arcgis_pro_mcp
python -m unittest discover -s tests -p "test_*.py"
```

Run `ruff` if the dev dependency is installed:

```bash
ruff check .
```

Existing no-ArcPy tests cover path policy, generic GP gating, server registration shape, and GP helper behavior. Real ArcPy behavior still needs Windows/ArcGIS Pro validation.

## Documentation Discipline

- Keep README tool summaries aligned with `arcgis_pro_server_capabilities`.
- Keep this skill updated when changing environment variables, write gates, path validation, generic GP behavior, or major tool groups.
- If adding a tool, decide whether it is read-only, write-gated, export-gated, or generic-GP related and expose that correctly in `arcgis_pro_server_capabilities`.
