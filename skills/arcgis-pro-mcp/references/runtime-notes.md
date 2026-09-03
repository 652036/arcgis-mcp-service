# ArcGIS Pro MCP Runtime Notes

Live constraints verified on ArcGIS Pro 3.6. Prefer `arcgis_pro_server_capabilities` over this file when they disagree.

## Discovery

- If `arcgis_pro_list_projects` returns `project_count: 0`, read `note`. Set `ARCGIS_PRO_MCP_PROJECT_ROOTS` (or `ARCGIS_PRO_MCP_INPUT_ROOTS`) or pass an absolute `.aprx` path.
- Unset project/input roots still allow opening an absolute `.aprx`; they only disable project scanning.

## Data Access

- Feature inserts accept geometry only as `SHAPE@WKT` or `SHAPE@JSON` strings. Do not send `SHAPE@` objects.
- Do not update OID fields through DA write tools.
- `arcgis_pro_set_definition_query` uses `definition_query`, not `where_clause`.
- `arcgis_pro_clear_map_selection` defaults to `all_layers`. `map` is accepted as an alias.
- Selection count/FID readers use `Layer.getSelectionSet()` and never substitute the
  layer's total row count. Attribute/location selection writes compare the ArcPy
  derived Count output with that actual set and return `selection_verified=true`
  only on agreement. Treat a mismatch as an unknown write result: re-read state and
  do not automatically retry.

## Maps, Layouts, Layers

- `create_layout` adds a map frame when the project already has a map. Legends, reports, and bookmarks are not created; they must already exist.
- Map-frame extent uses the frame camera (`camera.setExtent`).
- Layer source repair uses `updateConnectionProperties` (Pro 3.x). Pass `FILEGDB_WORKSPACE` for file geodatabases.
- Heatmap rendering is point or multipoint only.
- Unique-value renderers need 1–3 fields that exist on the layer data source.
- `Symbology` itself has no `type` member. Inspect `Symbology.renderer.type` for
  feature layers or `Symbology.colorizer.type` for raster layers.
- Basemap names are locale-specific. Confirm the name from the project or Pro UI instead of assuming English names such as `Topographic`.
- `_open_project` caches `ArcGISProject` for the MCP process. Datasets used by an open project may stay schema-locked until the layer is removed or the MCP process restarts.
- `aprx_path=CURRENT` binds to the project open in the ArcGIS Pro UI only when `接入当前窗口.py`/`.pyt` is running. CURRENT is not cached across requests, but one request reuses one bound project reference for validation and execution.
- The open project must have a stable absolute `filePath`. Untitled/unsaved projects are rejected until saved because two pathless projects cannot be distinguished safely.
- Only calls that explicitly pass `aprx_path=CURRENT` are forwarded to the window host. Absolute `.aprx` paths remain file-mode calls even while a host is online.
- CURRENT is fail-closed. A missing/not-ready host, a changed session, or a project switch between probe and execution returns an error rather than silently changing targets.
- Never perform an automatic window-to-file fallback. A user-approved mode switch is a separate workflow and requires an exact absolute `.aprx` path; active-view focus, zoom, and refresh do not have file-mode equivalents.
- Window health reports a protocol/session identity, live project, active view, readiness, busy tool, and queue depth. The bridge uses a bounded, serialized queue because ArcPy calls must not run in HTTP worker threads.
- `arcgis_pro_window_status` explicitly latches the confirmed session/project. A restarted host, same-port replacement, or project switch is rejected until that status tool is called again and the new target is verified.
- The authenticated client forwards only ArcGIS MCP write/path-policy variables. The host never force-enables writes or hardcodes export/GP output roots.
- The host RPC accepts only tools that explicitly carry `aprx_path=CURRENT`; no-project DA/GP tools remain on the stdio/file side until a dedicated live-window command exists.
- The default host port is single-instance. Distinct Pro windows require distinct explicit `ARCGIS_PRO_MCP_HOST_PORT` values in both the Pro process and the matching MCP client; non-default ports use isolated state files.
- `arcgis_pro_active_view_info` and the active-view camera/focus tools require CURRENT. Use `arcgis_pro_refresh_layer` after cursor/in-place updates that Pro has not redrawn automatically.
- ArcGIS Pro retains imported Python modules across toolbox runs. Both window launchers reload the package-external bootstrap, remove the complete cached `arcgis_pro_mcp.*` tree, and fresh-import the host, `pro_attach`, all helpers, and a clean (non-stdio-proxied) FastMCP manager. Never reload or purge modules while a host is active: cancel it, wait for `窗口宿主已停止`, and then rerun the launcher. On the first upgrade from the old `.pyt`, refresh or re-add the Python toolbox in Catalog (or invoke the `.py` file through `runpy`) because Pro may retain the old `AttachWindow` class before the bootstrap can run. Restart Pro only when that refresh fails or an interrupted run leaves the in-host marker stuck; restart the separate MCP client after stdio/tool/protocol changes.
- The Python Window and Python toolbox host execute in ArcGIS Pro's foreground run mode. Treat this as an exclusive Agent-controlled bridge. Use an ArcGIS Pro SDK add-in for concurrent human interaction, UI events, reliable running-job cancellation, or arbitrary application UI control.

## Geoprocessing

- Write GP outputs must fall under `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT`.
- Named wrappers first. `arcgis_pro_gp_run_tool` stays off unless generic GP is enabled and the exact tool is allowlisted.
- Network analysis tools need a network dataset, not a file geodatabase workspace.
- `arcgis_pro_gp_validate_topology` needs an existing topology.
- Distance tools that report linear units (Ripley's K, some hot-spot distance bands) need a projected coordinate system. Geographic WGS84 often fails with "projected data required".
- `CalculateGeometryAttributes` property names are Esri tokens (`AREA_GEODESIC`, `PERIMETER_LENGTH_GEODESIC`). The wrapper maps `AREA` / `LENGTH` / `PERIMETER` to those tokens.
- Kriging needs a numeric z-field with enough non-null points and supplies an ordinary kriging model.
- Dissolve, ExportFeatures, ExportTable, and TestSchemaLock follow Pro 3.6 toolbox locations (`management` / `conversion` / `arcpy.TestSchemaLock`).
