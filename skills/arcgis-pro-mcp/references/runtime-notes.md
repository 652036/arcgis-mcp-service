# ArcGIS Pro MCP Runtime Notes

Use runtime discovery over this reference when they disagree. Real behavior also depends on the installed ArcGIS Pro version, licenses, data source, active UI state, and schema locks.

## Mode Selection

| Need | File mode | Python CURRENT host | Native SDK bridge |
| --- | --- | --- | --- |
| Batch/project-file work | Preferred | Only if visible state matters | Usually unnecessary |
| Broad `arcpy.mp` support in the open project | No | Preferred | Only advertised SDK commands |
| Active view, focus, camera, visible refresh | No | Supported, serialized | Supported with context CAS and DrawComplete |
| Change/event observation | No | Revision long-poll | Native SDK event long-poll |
| Native Undo/Redo and pending-edit save/discard | No | Not a transaction controller | Supported when separately enabled |
| Native feature EditOperation | No | Use constrained ArcPy/DA tools instead | Supported narrow create/modify/delete contract |
| Asynchronous cancellable typed GP | Standalone call semantics | Host job state; running cancellation is limited | Preferred when an advertised typed contract exists |
| Concurrent human interaction | Not applicable | No; foreground, exclusive bridge | Better fit, still guarded by one project lease |

Do not change modes to work around a rejected gate or path. A deliberate mode change requires a new target check.

## File Mode

- Pass an absolute `.aprx`; `ARCGIS_PRO_MCP_PROJECT_ROOTS` constrains it when configured and otherwise falls back to input roots.
- `arcgis_pro_list_projects` only scans configured roots. An empty result does not mean an absolute allowed project cannot be opened.
- Projects are cached by normalized absolute path. After verifying that no intended unsaved project changes exist, use `arcgis_pro_release_project` before an external schema change or `arcgis_pro_reload_project` when stale file-mode state must be discarded. Neither operation saves pending changes; both require WRITE + DESTRUCTIVE and an exact `confirm_aprx_path` echo.
- File mode cannot read or alter the user's active pane, current camera, UI selection focus, or screen redraw.

## Python CURRENT Host (Protocol v4)

1. Open and save the target project in ArcGIS Pro. An untitled project has no stable identity and is rejected.
2. Run **接入当前窗口** from `接入当前窗口.pyt`, or run `接入当前窗口.py` through `runpy` in the Pro Python window. Leave it running.
3. Call `arcgis_pro_window_status`. Require `window_attached`, `host_ready`, `target_confirmed`, and the expected `current_project`/session.
4. Use `arcgis_pro_active_view_info(aprx_path="CURRENT")` as the first target smoke test.
5. Pass `CURRENT` only to tools whose schema includes `aprx_path`. Absolute project paths remain file-mode calls even while a host is running.

The host is authenticated, loopback-only, bounded, and serialized. It creates one `ArcGISProject("CURRENT")` reference for target verification and execution of each request. On Windows its discovery state is atomically published under `%LOCALAPPDATA%\ArcGISProMcp\window-host` with a protected current-user ACL; the reader rejects symlinks/reparse points and invalid owner/DACL/type/size. Treat the state file as a local capability secret. The stdio client forwards only an allowlisted set of policy variables; the host does not enable permissions itself.

`CURRENT` fails closed on a missing/not-ready host, protocol mismatch, new host session, project switch, or stale target confirmation. Call `arcgis_pro_window_status` again after any host restart or project switch. Never automatically substitute the on-disk project path.

Use `arcgis_pro_window_job_submit` only for a tool that explicitly accepts `aprx_path=CURRENT`; then inspect it with `arcgis_pro_window_job_status`. `arcgis_pro_window_job_cancel` can reliably remove queued work, but an already-running ArcPy/GP call may only report a cancellation request. `arcgis_pro_window_wait_for_change` observes bounded revision changes; it does not claim native DrawComplete semantics.

Stop the old host before updating its code. Wait until Pro reports that the window host stopped, refresh/re-add the toolbox if necessary, restart the separate MCP client after tool/protocol changes, then reconfirm the target. The package-external bootstrap purges the complete cached `arcgis_pro_mcp.*` generation before importing the replacement. This prevents a stale `pro_attach` from causing errors such as a missing `FORWARDED_ENV_KEYS`; do not reload pieces of the package while a host is active.

Multiple Pro instances require a distinct `ARCGIS_PRO_MCP_HOST_PORT` for each matching host/client pair.

## Native SDK Bridge

The optional source add-in lives under `sdk/ArcGISProMcp.AddIn`. Do not assume it is installed merely because its source exists. Start with `arcgis_pro_sdk_bridge_status(process_id=...)`; status is sanitized and never exposes the bearer or lease secret.

1. Select the expected Pro process when more than one SDK bridge is present.
2. Acquire a short exclusive lease using `arcgis_pro_sdk_acquire_project_lease(expected_project_uri=...)`. The project must be saved and must remain exactly the same.
3. Keep the returned opaque `sdk_session_ref` inside the MCP process. Do not expose, persist, or log it.
4. Read `arcgis_pro_sdk_context`. Preserve the exact map/layer/table URI, camera WKID, selection digest/count, and context/selection/edit/draw generations.
5. Send the matching expected values and `confirm=true` to the smallest command. A generation or target conflict is a reason to re-read context, not to retry blindly.
6. Renew the lease only while work is still authorized. Release it when finished.

Native SDK capabilities include, when advertised:

- active context and bounded metadata-only events;
- typed camera, zoom-to-layer, refresh/DrawComplete wait, time range, and loaded standalone-table commands;
- allowlisted asynchronous typed GP jobs with status and cooperative cancellation;
- edit status, undo, redo, save, and destructive discard-all;
- one-operation native feature create/modify/delete with selection/edit/context compare-and-swap.

Feature geometry is deliberately limited to finite 2D points, single-part straight polylines, and single-ring closed straight polygons with an exact matching WKID and geometry type. It does not accept Z/M, curves, multipart geometry, multipoints, multipatches, or implicit reprojection. Modify and delete bind to the current exact selection count and OID digest; never reconstruct or guess these values.

The SDK bridge is loopback-only. It does not provide a remote transport, Portal sign-in/token access, generic code execution, arbitrary CIM, or arbitrary UI control. `promptGeometry` is false.

## Data And Selection Semantics

- Discover layers by URI or exact `long_name`. Grouped layers can share short display names.
- ArcPy selection tools compare GP-derived counts with `Layer.getSelectionSet()` and report `selection_verified`. A mismatch is an unknown write result; re-read rather than replay.
- DA inserts accept geometry through supported JSON/WKT tokens, not live Python geometry objects. Do not update OID/system fields.
- Temporary feature layers/table views use opaque session references. Release them when no longer needed and do not treat an in-process reference as a durable dataset.
- In-place cursor/schema changes can leave Pro holding locks or displaying cached state. Re-read schema/selection and request an appropriate refresh rather than assuming the screen has updated.

## ArcPy And Extension Constraints

- Spatial Analyst, 3D Analyst/LAS, Network Analyst, Space Time Pattern Mining, geostatistical, Utility Network, and other advanced tools depend on the matching installed license and supported data.
- Distance-based statistics and many Euclidean tools need an appropriate projected coordinate system. Do not infer meaningful linear units from an unsuitable geographic coordinate system.
- Local network wrappers require a local Network Dataset. They reject Portal/URL ready-to-use services.
- Local geocoding wrappers require an allowed local locator/`.loc` input. They are not a hosted credit-consuming geocoding client.
- Enterprise versioning and Utility Network behavior depends on the geodatabase/server version, ownership and privileges. Preflight on a non-production copy when possible.
- The enterprise-write gate covers version management, maintenance, and Utility Network administration, not ordinary feature/row edits. Those use WRITE, destructive permission when applicable, and the SDK-feature gate for native SDK feature edits.
- Output creation generally rejects an existing target. Generic GP and CURRENT map analysis require a complete path under the configured GP output root, reject in-place/destructive/code-execution work and existing targets, and force `overwriteOutput=False`. Local exports and publishing artifacts also require new paths; only an explicitly exposed external service-overwrite workflow can use its separate overwrite gate and exact confirmation.
- Calculate Field uses only the constrained pure-Arcade subset; labels are Arcade-only. Repair Geometry always uses `KEEP_NULL` and therefore does not delete null-geometry rows.
