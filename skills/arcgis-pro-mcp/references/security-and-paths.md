# ArcGIS Pro MCP Security And Paths

## Environment Variables

- `ARCGIS_PRO_MCP_ALLOW_WRITE`: must be `1`, `true`, `yes`, or `on` before save, layer edits, selections, table/feature writes, layout changes, and write GP tools.
- `ARCGIS_PRO_MCP_EXPORT_ROOT`: if set, layout/map/report exports and `saveACopy` outputs must be under this root.
- `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT`: required for many write GP output datasets.
- `ARCGIS_PRO_MCP_INPUT_ROOTS`: optional semicolon-separated input roots; many input paths must be under one of them when set.
- `ARCGIS_PRO_MCP_PROJECT_ROOTS`: optional semicolon-separated project roots; `.aprx` paths must be under these roots when set. Falls back to input roots when unset.
- `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP`: must be `1` before `arcgis_pro_gp_run_tool`.
- `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST`: exact allowlist for generic GP, such as `management.CopyFeatures,analysis.Buffer`.
- `ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD`: default blocks inline database passwords; enable only in a controlled environment.

## Required Probes

Always call these before real work:

1. `arcgis_pro_environment_info`
2. `arcgis_pro_server_capabilities`

The capabilities response reports `tools_read_only`, `tools_require_allow_write`, `tools_export`, root configuration, and generic GP state. Use it rather than README assumptions.

## Safe Write Sequence

1. Verify `allow_write` is true.
2. Verify target `.aprx`, data, and output paths are within configured roots.
3. Run a read-only check on the target object.
4. Execute the specific write tool.
5. Re-read the changed state.
6. Save only when requested.

## Generic GP Rules

- Prefer named wrappers such as `arcgis_pro_gp_buffer`, `arcgis_pro_gp_clip`, or `arcgis_pro_gp_project`.
- Use `arcgis_pro_gp_run_tool` only when generic GP is enabled and the exact tool appears in the allowlist.
- Path-like parameters are still subject to input-root and GP-output-root validation.
- Read GP messages with `arcgis_pro_gp_get_messages` when a GP tool fails or returns warnings.
