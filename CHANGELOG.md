# Changelog

All notable changes to this project will be documented in this file.

Chinese version: [`CHANGELOG.zh-CN.md`](./CHANGELOG.zh-CN.md)

## [Unreleased]

## [2.0.0] - 2026-09-03

### Added
- Added a third, optional native control surface under
  `sdk/ArcGISProMcp.AddIn`: loopback-only discovery, per-load credentials,
  exclusive short project leases, active context generations, metadata-only
  events, camera/time/table commands, DrawComplete-aware refresh, native
  `EditOperation` feature writes, Undo/Redo/save/discard, and cancellable typed
  GP jobs. Python MCP wrappers keep bearer and lease secrets opaque.
- Expanded the curated MCP surface to more than 400 dynamically catalogued
  tools. New focused modules cover project import/export and connection repair,
  cartography and charts, dataset/schema maintenance, attribute integrity,
  symbology, raster/map algebra/hydrology/mosaics, LAS, advanced spatial and
  space-time modelling, local geocoding, local network analysis, enterprise
  versioning, Utility Network workflows, and guarded publishing.
- Added `arcgis_pro_tool_info` and a machine-readable per-tool policy catalogue
  to report read/write classification, path roots, window requirements,
  confirmations, and additional gates without relying on a static tool count.
- Added Python live-window job submission/status/cancellation and bounded change
  waiting, while preserving exact `CURRENT` target binding.
- Window attach host: run `接入当前窗口.pyt` or `接入当前窗口.py` inside ArcGIS Pro.
  Calls that explicitly use `aprx_path=CURRENT` execute against the open project.
  New tools inspect/focus active views, control the live map camera, and request
  layer redraws.
- The window protocol now has a per-session token, protocol/package/target checks,
  atomic state discovery, readiness/busy/queue diagnostics, a bounded job queue,
  cancellation for requests that expire before execution, stop/admission race
  protection, isolated state files for explicitly configured host ports, and an
  explicit status-confirmed session/project latch that prevents silent retargeting.
- Added a research-focused spatial statistics module (`arcgis_pro_mcp/gp_stats.py`)
  registering 14 new MCP tools: Getis-Ord Gi* hot spots, optimized hot spots,
  Anselin Local Moran's I cluster/outlier, Ripley's K multi-distance clustering,
  Global Moran's I spatial autocorrelation, average nearest neighbor, OLS, GWR,
  forest-based classification/regression, central feature, mean center,
  directional distribution (standard deviational ellipse), create random points,
  and generate tessellation. Diagnostic tools return geoprocessing messages so
  statistical indices can be captured for reporting; all output-producing tools
  honor the existing write gate and GP output-root policy.
- Added a repo-local Codex skill at `skills/arcgis-pro-mcp/SKILL.md`, with
  references for ArcGIS Pro runtime requirements, write/path safety gates,
  grouped MCP tools, and development notes.
- Added `AGENTS.md` and `CLAUDE.md` for repository-specific agent guidance.
- Added `LICENSE` (MIT) and filled in project metadata in `pyproject.toml`
  (license, authors-friendly URLs, classifiers, keywords, dev extras).
- Added a `py.typed` marker so downstream type checkers can see the package's
  annotations.
- Added `SECURITY.md`, `CONTRIBUTING.md`, a pull-request template, bug report
  and feature request issue templates, and a `dependabot.yml` for weekly pip
  and GitHub Actions updates.
- Added top-level exception handling in `arcgis_pro_mcp.__main__` so startup
  failures print a readable message instead of a bare traceback.

### Fixed
- Hardened standalone generic GP and CURRENT map analysis so every call must
  create at least one complete path under the configured GP output root. Both
  reject existing targets and force `overwriteOutput=False`; ambiguous
  container/name outputs, in-place/no-output work, destructive operations, and
  code-execution tools are denied even when a name is allowlisted.
- Restricted Calculate Field to a validated pure-Arcade expression subset and
  label expressions to Arcade, removing Python/VB/code-block execution paths.
  Repair Geometry now always uses `KEEP_NULL` so null-geometry rows are not
  deleted as a side effect.
- Changed map/layout/report/chart/project-copy and local publishing-artifact
  exports to reject existing paths instead of inheriting ArcPy overwrite state.
- Moved Python CURRENT discovery state into the current user's private
  `%LOCALAPPDATA%\ArcGISProMcp\window-host` directory with atomic publication,
  protected ACL/owner checks, bounded reads, and link/reparse-point rejection.
- Restricted database-connection creation to exact allowlisted instances and
  fixed dedicated credential variables, preventing caller-selected environment
  reads or credential forwarding to arbitrary targets. New connection files
  reject existing outputs and do not save credentials unless explicitly confirmed.
- Added a destructive gate for mosaic `OVERWRITE_DUPLICATES`, and for releasing
  or reloading cached file-mode projects that may contain unsaved state.
- Selection writes now verify ArcPy's derived count against the layer's actual
  `getSelectionSet()` before reporting success, request a live-window redraw,
  and expose exact selected counts/FIDs. Layer properties now inspect
  `Symbology.renderer` or `Symbology.colorizer` instead of the nonexistent
  `Symbology.type` attribute. This resolves issue #6's false-success, stale-view,
  incorrect-count, and symbology diagnostics.
- Window launchers no longer reload only `pro_host` while retaining stale ArcGIS Pro
  process caches. A package-external bootstrap now replaces the complete
  `arcgis_pro_mcp.*` module generation after the old host stops, preventing new
  host code from importing old `pro_attach`, helper, or FastMCP registrations,
  including the stale-generation `FORWARDED_ENV_KEYS` import failure.
- The Pro host no longer force-enables writes or hardcodes a local drive path; it applies
  the stdio MCP client's allowlisted policy per call. Absolute `.aprx` paths are no
  longer rewritten to CURRENT, and CURRENT fails closed on disconnect or project
  switch. Each host request reuses one bound CURRENT project reference.
- `arcgis_pro_list_projects` now returns an empty inventory plus a setup note
  when no project/input roots are configured, instead of raising.
- `remove_map` / `remove_layout` now call `ArcGISProject.deleteItem`.
- Map image export uses `Map.defaultView`; heatmap rendering uses CIM instead
  of the invalid `HeatMapRenderer` name.
- Label expression engine and font updates go through CIM; zoom-to-selection
  uses the selected-feature envelope.
- DA inserts accept only `SHAPE@WKT` / `SHAPE@JSON` strings; feature updates
  reject OID fields.
- Creating a database connection now ensures the output folder exists; SDE
  listing includes feature classes inside feature datasets.
- Dissolve uses `management.Dissolve`; schema-lock uses `arcpy.TestSchemaLock`;
  ExportFeatures/ExportTable fall back to the Conversion toolbox on Pro 3.6.
- Kriging now supplies `KrigingModelOrdinary`; Ripley's K default envelope token
  matches the tool domain; MapFrame extent uses `camera.setExtent`; layer
  sources use `updateConnectionProperties`.
- `arcgis_pro_gp_import_csv_to_table` no longer silently duplicates
  `arcgis_pro_gp_table_to_table`. It now routes through a dedicated
  `run_import_csv_to_table` that validates the input is a delimited text file
  (`.csv`/`.txt`/`.tab`) and uses the clearer `in_csv` parameter name.

### Changed
- Clarified and aligned gate metadata so enterprise-write permission protects
  version management, maintenance, and Utility Network administration, while
  ordinary feature/row edits use WRITE plus destructive and SDK-feature gates
  where applicable.
- Reworked the README, live-window architecture guide, contributor guidance,
  security documentation, and shared agent skill around the three explicit
  execution surfaces: file mode, Python `CURRENT` protocol v4, and the SDK
  Add-In.
- Expanded the repo skill for Cursor, Grok, Codex, and Claude, and added
  Pro 3.6 live-runtime notes under `skills/arcgis-pro-mcp/references/runtime-notes.md`.
- Pinned the `mcp` dependency to `>=1.20,<2` to avoid silent breakage on a
  future major release.
- Expanded CI to a Python 3.10 / 3.11 / 3.12 matrix on Ubuntu and Windows,
  plus a dedicated ruff lint job.

### Changed (breaking)
- `arcgis_pro_gp_eliminate` parameters renamed: `selection_type` removed; new parameters are `condition` (`AREA`/`PERCENT`/`AREA_OR_PERCENT`), `part_area`, `part_area_percent`, `part_option`.
- `arcgis_pro_da_update_features` no longer accepts the unused `field_name` argument; clients should drop it from existing calls.
- `da_write.insert_features` no longer accepts the unused `include_geometry_wkt` argument; geometry insertion has always been driven by including `SHAPE@WKT` in `fields`.

### Fixed
- Fixed `arcgis_pro_gp_eliminate` so its parameters now correctly reflect the underlying `EliminatePolygonPart` GP. The previous `selection_type=LENGTH/AREA` was incompatible with the tool and would fail at runtime.
- Fixed `arcgis_pro_zoom_to_selection` to actually honor `layer_name`. It now sets the map frame extent to the specified layer's (selection) extent instead of zooming to all layers.
- Replaced every remaining `Invalid arguments` placeholder in server-side validation sites (selection / placement / overlap / join enums and map frame / layout element / legend / text element lookups) with messages that include the offending value and a list of valid choices or available candidates.

### Removed
- Removed the dead server-side `_query_rows`, `_sanitize_order_by`, `_MAX_QUERY_WHERE`, and `_MAX_QUERY_CELL` helpers superseded in 1.0.1 by the shared `da_read.query_rows` implementation.

## [1.0.1] - 2026-03-25

### Added
- Added `arcgis_pro_list_projects` to discover `.aprx` projects under configured project roots.
- Added `arcgis_pro_remove_layout` to complete the basic layout lifecycle operations.
- Added `ARCGIS_PRO_MCP_PROJECT_ROOTS` to constrain ArcGIS Pro project paths separately from general data inputs.
- Added `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP` and `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST` to explicitly gate the generic GP runner.
- Added `username_env_var` and `password_env_var` options to `arcgis_pro_create_db_connection`.
- Added unit tests for project path validation, generic GP gating, shared query delegation, DB connection credentials, and new project discovery behavior.

### Changed
- Changed generic GP execution to be disabled by default and require an explicit allowlist before any tool can run.
- Changed generic GP parameter handling to validate likely input and output paths against the existing MCP root policies.
- Changed `.aprx` loading to require absolute paths and validate them against configured project or input roots.
- Changed `arcgis_pro_da_query_rows` to reuse the shared `da_read.query_rows` implementation instead of a duplicated server-side variant.
- Changed `arcgis_pro_environment_info` and `arcgis_pro_server_capabilities` to report project-root and generic-GP configuration state.
- Changed CI to run `compileall` for the package and execute the new unit test suite.
- Updated README to document the new project-root policy, generic GP gating, DB credential guidance, and new tools.

### Fixed
- Fixed `arcgis_pro_remove_join` so it uses the `arcpy` object returned from `_open_project`.
- Fixed `arcgis_pro_mapframe_zoom_to_bookmark` so bookmark lookup errors no longer mask the original exception with a `NameError`.
- Fixed multiple malformed Chinese validation messages in query and server-side helpers.
- Fixed `da_read.query_rows` ordering so `order_by` generates a proper `ORDER BY` SQL clause and rejects newline or semicolon injection.
- Fixed `arcgis_pro_describe` and `arcgis_pro_list_fields` to honor input-root validation like other dataset readers.
- Improved not-found errors for maps, layouts, layers, map frames, bookmarks, tables, and fields by returning available candidates instead of a generic `Invalid arguments`.

### Security
- Restricted the generic GP runner so it no longer acts as an unrestricted bypass around the curated MCP tool surface.
- Prevented inline database passwords by default; direct password parameters now require `ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD=1`.
