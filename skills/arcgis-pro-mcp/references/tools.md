# ArcGIS Pro MCP Tool And Workflow Reference

This is a routing guide, not a frozen catalog. The server exposes hundreds of typed tools and the exact set can change with the running checkout.

## Machine-Readable Discovery

- `arcgis_pro_environment_info`: ArcGIS product/runtime and top-level policy state.
- `arcgis_pro_server_capabilities`: current modes, configured gates/roots, and grouped capability summary.
- `arcgis_pro_tool_info(name="...")`: exact description, input/output JSON schema, and policy for one tool. With an empty name it returns the full catalog without invoking ArcPy.

Inspect these `policy` fields before acting:

- `read_only`, `destructive`, `idempotent`, `open_world`;
- `requires_current`, `requires_sdk_bridge`;
- `gates` and `conditional_gates`.

The tool result is structured JSON. Check explicit outcome, verification, counts, messages, artifact identity, and job state rather than parsing prose.

## Live Window Controls

### Python CURRENT Host

- Attach/status: `arcgis_pro_window_status`, `arcgis_pro_detach_window`
- Active state: `arcgis_pro_active_view_info`
- View focus: `arcgis_pro_open_map_view`, `arcgis_pro_open_layout_view`, `arcgis_pro_open_report_view`, `arcgis_pro_close_views`
- Camera/redraw: `arcgis_pro_set_active_view_extent`, `arcgis_pro_zoom_active_view_to_layer`, `arcgis_pro_zoom_active_view_to_all_layers`, `arcgis_pro_refresh_layer`
- Bounded jobs/change wait: `arcgis_pro_window_job_submit`, `arcgis_pro_window_job_status`, `arcgis_pro_window_job_cancel`, `arcgis_pro_window_wait_for_change`

Pass the exact token `aprx_path="CURRENT"`. Only tools with an `aprx_path` parameter can be forwarded.

### Native SDK Bridge

- Discovery/lease: `arcgis_pro_sdk_bridge_status`, `arcgis_pro_sdk_acquire_project_lease`, `arcgis_pro_sdk_renew_project_lease`, `arcgis_pro_sdk_release_project_lease`
- Context/events: `arcgis_pro_sdk_context`, `arcgis_pro_sdk_wait_events`
- View: `arcgis_pro_sdk_set_camera`, `arcgis_pro_sdk_zoom_layer`, `arcgis_pro_sdk_refresh_view`, `arcgis_pro_sdk_set_active_time`, `arcgis_pro_sdk_open_table`
- Native features: `arcgis_pro_sdk_create_feature`, `arcgis_pro_sdk_modify_selected_features`, `arcgis_pro_sdk_delete_selected_features`
- Async typed GP: `arcgis_pro_sdk_gp_job_submit`, `arcgis_pro_sdk_gp_job_status`, `arcgis_pro_sdk_gp_job_cancel`
- Edit stack: `arcgis_pro_sdk_edit_status`, `arcgis_pro_sdk_edit_undo`, `arcgis_pro_sdk_edit_redo`, `arcgis_pro_sdk_edit_save`, `arcgis_pro_sdk_edit_discard`

Every SDK command after discovery is bound to an opaque session reference and exact project lease. Use fresh context/status generations and exact URIs/digests; do not mix Python CURRENT session fields with SDK fields.

## Project, Map, Layout, And Cartography

Representative discovery tools:

- projects/connections/styles: `arcgis_pro_list_projects`, `arcgis_pro_project_summary`, `arcgis_pro_project_connections`, `arcgis_pro_list_style_items`, `arcgis_pro_list_basemaps`, `arcgis_pro_list_color_ramps`
- maps/layouts/reports: `arcgis_pro_list_maps`, `arcgis_pro_list_layouts`, `arcgis_pro_list_reports`, `arcgis_pro_list_bookmarks`
- layers/tables: `arcgis_pro_list_layers`, `arcgis_pro_list_tables`, `arcgis_pro_layer_properties`, `arcgis_pro_list_broken_sources`
- cameras/frames/elements: `arcgis_pro_map_camera`, `arcgis_pro_mapframe_extent`, `arcgis_pro_list_layout_map_frames`, `arcgis_pro_list_layout_elements`

Mutation groups include project copies/saves, connections and imports, create/duplicate/remove/rename maps and layouts, add/copy/insert/group/move layers, data-source repair, definition queries, visibility/scale/joins/relates, clipping and spatial reference, layout/report/map-series elements, and map/layer document exports. Discover the exact operation by name and inspect its gate before calling.

Cartography covers semantic simple/unique/graduated/heatmap renderers, labels, charts, metadata, layer aliases, map series, legends, bookmarks, and a separately gated raw CIM update. Prefer semantic tools because they validate fields, geometry support, and target identity. Label-expression create/update accepts Arcade only; Python, VBScript, and JScript are not exposed. Label expression/font and other CIM-backed semantic writes still require the reported CIM-write gate.

Map, layout, report, chart, bookmark, layer-file, project-copy, and similar artifact exports require a new path under the export root. They do not silently replace an existing file; choose a new name instead of deleting a prior artifact unless a separate reviewed overwrite operation exists.

## Data, Editing, And Integrity

Read/preflight:

- `arcgis_pro_describe`, `arcgis_pro_list_fields`, `arcgis_pro_dataset_exists`, `arcgis_pro_dataset_schema`, `arcgis_pro_verify_output_dataset`
- workspace/feature-class/raster/table/feature-dataset/domain discovery through `arcgis_pro_workspace_list_*`
- table reads through `arcgis_pro_da_table_sample`, `arcgis_pro_da_query_rows`, and `arcgis_pro_da_distinct_values`
- selection truth through `arcgis_pro_layer_selection_count` and `arcgis_pro_layer_selection_fids`
- edit preflights and schema-lock checks before an applicable edit/schema operation

Writes include constrained DA insert/update/delete, exact-selection deletion, geometry/workspace edit apply operations, field/table/feature-class management, domains/subtypes, attachments, relationship classes, topology, indexes, editor tracking, and Global IDs. Deletion/schema removal is not implied by permission to update attributes.

`arcgis_pro_gp_calculate_field` is deliberately narrower than ArcPy Calculate Field: it operates on an exact existing field/count confirmation and accepts only the validated pure-Arcade expression subset (field references plus allowlisted pure functions). Python/VB, code blocks, comments/statements, and remote/dynamic evaluation are rejected. `arcgis_pro_gp_calculate_geometry` has its own exact dataset/mapping/count and destructive confirmations. `arcgis_pro_gp_repair_geometry` always calls ArcPy with `KEEP_NULL`; it will not delete null-geometry rows as a repair side effect.

Data-integrity workflows expose read/list and controlled import/export/mutation for:

- attribute rules;
- field groups;
- contingent values.

Use `arcgis_pro_tool_info` to discover the installed exact `arcgis_pro_*attribute_rule*`, `*field_group*`, and `*contingent_value*` tools. Mutations require a schema lock, exact dataset echo, destructive gate, and the fixed confirmation phrase in that tool's schema. Attribute-rule import also requires the exact expected rule-name set; contingent-value import parses the official CSV manifest and post-verifies the resulting inventory. Never treat either import as an ordinary append.

Suggested pattern:

1. Read dataset schema and current rules/groups/values.
2. Confirm a schema lock and obtain the exact mutation contract with `arcgis_pro_tool_info`.
3. Export a backup under the controlled export root when supported.
4. Perform one narrow schema mutation with exact confirmations.
5. Re-list and compare the resulting schema. Do not auto-retry a partial or ambiguous import.

## Raster, Mosaic, And LAS

Core raster wrappers include properties/cell values, slope/aspect/hillshade, reclassification, extract/mask, density/interpolation, raster calculator, raster/vector conversion, clip/resample/project, mosaic-to-new-raster, and hydrology.

Advanced typed tools include:

- in-place/new raster: `arcgis_pro_raster_calculate_statistics`, `arcgis_pro_raster_build_pyramids`, `arcgis_pro_raster_set_nodata`, `arcgis_pro_raster_copy`
- local/conditional/distance: `arcgis_pro_raster_focal_statistics`, `arcgis_pro_raster_cell_statistics`, `arcgis_pro_raster_con`, `arcgis_pro_raster_set_null`, `arcgis_pro_raster_euclidean_distance`, `arcgis_pro_raster_distance_accumulation`, `arcgis_pro_raster_optimal_path_as_line`
- hydrology: `arcgis_pro_raster_fill`, `arcgis_pro_raster_flow_direction`, `arcgis_pro_raster_flow_accumulation`, `arcgis_pro_raster_snap_pour_point`, `arcgis_pro_raster_watershed`, `arcgis_pro_raster_stream_order`, `arcgis_pro_raster_stream_to_feature`, `arcgis_pro_raster_basin`
- mosaic datasets: `arcgis_pro_create_mosaic_dataset`, `arcgis_pro_add_rasters_to_mosaic_dataset`, `arcgis_pro_build_mosaic_footprints`, `arcgis_pro_build_mosaic_overviews`, `arcgis_pro_remove_rasters_from_mosaic_dataset`
- LAS: `arcgis_pro_las_dataset_info`, `arcgis_pro_create_las_dataset`, `arcgis_pro_calculate_las_statistics`, `arcgis_pro_build_las_pyramid`

Verify the required extension, every input, the GP output root, output nonexistence, NoData semantics, cell size/snap raster, coordinate system, and environment object. In-place statistic/pyramid/NoData changes and mosaic removals can be destructive even without creating a new output.

## Spatial Analysis And Modeling

Named vector/table GP covers overlay, proximity, dissolve/merge, spatial join, statistics/frequency, conversion, field calculation, geometry repair/checking, sampling and tessellation. Prefer these wrappers over generic GP.

The standalone generic runner is creation-only: it requires WRITE, explicit generic-GP enablement, an exact allowlist entry, a configured GP output root, and at least one complete `out_*` path. It rejects workspace/name output pairs, existing targets, no-output/in-place operations, destructive tools, and code-execution tools, and enforces `overwriteOutput=False`. The `CURRENT` map-analysis entry has the same complete-new-output/root rule plus a closed tool list; it cannot be used to run Calculate Field, Calculate Geometry Attributes, or Repair Geometry.

Spatial statistics and models include hot spots, cluster/outlier, Global Moran, nearest neighbor, Ripley's K, center/directional distribution, OLS/GWR/forest models, plus:

- `arcgis_pro_calculate_distance_band`
- `arcgis_pro_generate_spatial_weights_matrix`
- `arcgis_pro_find_point_clusters`
- `arcgis_pro_multivariate_clustering`
- `arcgis_pro_spatially_constrained_multivariate_clustering`
- `arcgis_pro_generalized_linear_regression`

Space-time workflows use:

- `arcgis_pro_create_space_time_cube`
- `arcgis_pro_emerging_hot_spot_analysis`
- `arcgis_pro_time_series_clustering`
- `arcgis_pro_curve_fit_forecast`
- `arcgis_pro_exponential_smoothing_forecast`
- `arcgis_pro_forest_based_forecast`
- `arcgis_pro_evaluate_forecasts_by_location`

Treat returned GP diagnostics/messages as part of the result. Validate numeric fields, time fields/intervals, projected units, neighborhood/weights choices, extension availability, and every output path before execution.

## Local Network Analysis And Geocoding

Prefer the typed one-shot local Network Dataset tools:

- `arcgis_pro_network_travel_modes`
- `arcgis_pro_network_solve_route`
- `arcgis_pro_network_solve_service_area`
- `arcgis_pro_network_solve_closest_facility`
- `arcgis_pro_network_solve_od_cost_matrix`

Legacy stateful NA layer tools remain available for compatible workflows; inspect them rather than mixing state from a one-shot solve. Inputs must be local allowed datasets and outputs new paths under the GP output root. Confirm travel mode, time/direction, barriers, unlocated input counts, solve status, and exported result counts. These wrappers reject URL/Portal solvers.

Local locator tools include `arcgis_pro_locator_info`, `arcgis_pro_geocode_addresses`, and `arcgis_pro_reverse_geocode`. Confirm locator/input/output roots and rematch/overwrite behavior. Do not assume hosted geocoding, credits, or credentials.

## Enterprise Geodatabase And Utility Network

Enterprise/version tools cover:

- version discovery/create/change/reconcile/post/delete;
- register/unregister as versioned;
- indexes, rebuild/analyze, editor tracking, and Global IDs.

Start with `arcgis_pro_list_versions` or dataset maintenance info. Use exact `.sde` or allowlisted HTTPS FeatureServer identities. Reconcile/post/delete/unregister are high-impact; follow the exact target/edit-version confirmations and never target the default version by inference.

Prefer an existing controlled `.sde`. `arcgis_pro_create_db_connection` is an open-world operation and requires a configured exact `PLATFORM|instance` database target plus the fixed DB credential variables for database authentication. It cannot read caller-selected environment variables, rejects an existing output, and does not save credentials unless explicitly confirmed.

The enterprise-write gate applies to version management, maintenance, and Utility Network administration. Ordinary feature/row edits use WRITE, plus destructive permission for deletion and the SDK-feature gate for native SDK feature edits; the storage location does not turn the enterprise gate into a blanket data-edit permission.

Utility Network tools include:

- `arcgis_pro_utility_network_info`
- `arcgis_pro_validate_utility_network_topology`
- `arcgis_pro_utility_network_trace`
- `arcgis_pro_update_subnetwork`
- `arcgis_pro_export_subnetwork`

Confirm the exact network identity and named trace/subnetwork configuration. Whole-tier update has an additional destructive confirmation. Validate privileges, topology state, and output root on a controlled network/data version before production use.

## Controlled Publishing

- inspect: `arcgis_pro_portal_status`, `arcgis_pro_get_artifact_digest`
- draft: `arcgis_pro_create_sharing_draft`
- stage: `arcgis_pro_stage_service_definition`
- upload: `arcgis_pro_publish_service_definition`

Keep draft and service-definition artifacts under the export root and use new, non-existing `.sddraft`/`.sd` paths. Local artifacts are never silently overwritten. Bind each stage to the exact SHA-256 of its input, require exact Portal/server allowlists, and use separate gates for public sharing and external service overwrite. Publication changes an external system; verify the resulting service before any retry.

## Deliberately Unsupported

No tool authorizes arbitrary Python/.NET execution, arbitrary Ribbon/dialog clicking, remote bridge binding, credentials/tokens, unreviewed generic SDK GP, arbitrary SDK CIM, implicit SDK reprojection, Portal network services, or interactive SDK prompt geometry. Unsupported extensions/data models fail explicitly rather than being emulated.
