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
- `ARCGIS_PRO_MCP_HOST_PORT`: optional live-host port. The default endpoint is single-instance; use a different explicit port in each Pro process and matching MCP client when controlling multiple Pro instances.

## Required Probes

Always call these before real work:

1. `arcgis_pro_environment_info`
2. `arcgis_pro_server_capabilities`

For live-window work, also call `arcgis_pro_window_status` and verify `host_ready`, `current_project`, and `host_session_id` before any write.

The capabilities response reports `tools_read_only`, `tools_require_allow_write`, `tools_export`, root configuration, and generic GP state. Use it rather than README assumptions.

## Safe Write Sequence

1. Verify `allow_write` is true.
2. Verify target `.aprx`, data, and output paths are within configured roots.
3. Run a read-only check on the target object.
4. Execute the specific write tool.
5. Re-read the changed state.
6. Save only when requested.

## Live Window Boundary

- The Pro host listens only on loopback and requires a per-session random token on health, call, and stop endpoints.
- The stdio proxy reads one atomic endpoint snapshot (port, token, session, project) and sends the expected session/project with each CURRENT request.
- The proxy also requires the session/project to match the last explicit `arcgis_pro_window_status` confirmation; a new health response cannot silently retarget an existing workflow.
- Only explicit `aprx_path=CURRENT` calls are routed to the live host. They fail closed when the host is missing, not ready, restarted, or focused on a different project.
- Absolute `.aprx` paths stay in file mode; the host rejects them instead of rewriting them to CURRENT.
- Host RPC dispatch also rejects tools without an explicit `aprx_path`; authentication is not permission to run arbitrary path-only or generic tools inside Pro.
- The host applies only allowlisted policy environment variables supplied by the authenticated stdio process and restores the Pro process environment after each call.
- Queued jobs are bounded and can be cancelled before execution on timeout or stop. A timeout after a job reaches RUNNING is ambiguous and must not be automatically retried as a write.

## Generic GP Rules

- Prefer named wrappers such as `arcgis_pro_gp_buffer`, `arcgis_pro_gp_clip`, or `arcgis_pro_gp_project`.
- Use `arcgis_pro_gp_run_tool` only when generic GP is enabled and the exact tool appears in the allowlist.
- Path-like parameters are still subject to input-root and GP-output-root validation.
- Read GP messages with `arcgis_pro_gp_get_messages` when a GP tool fails or returns warnings.
