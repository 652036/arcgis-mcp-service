# Changelog

All notable changes to this project will be documented in this file.

Chinese version: [`CHANGELOG.zh-CN.md`](./CHANGELOG.zh-CN.md)

## [Unreleased]

### Added
- Added `LICENSE` (MIT) and filled in project metadata in `pyproject.toml`
  (license, authors-friendly URLs, classifiers, keywords, dev extras).
- Added a `py.typed` marker so downstream type checkers can see the package's
  annotations.
- Added `SECURITY.md`, `CONTRIBUTING.md`, a pull-request template, bug report
  and feature request issue templates, and a `dependabot.yml` for weekly pip
  and GitHub Actions updates.
- Added top-level exception handling in `arcgis_pro_mcp.__main__` so startup
  failures print a readable message instead of a bare traceback.

### Changed
- Pinned the `mcp` dependency to `>=1.20,<2` to avoid silent breakage on a
  future major release.
- Expanded CI to a Python 3.10 / 3.11 / 3.12 matrix on Ubuntu and Windows,
  plus a dedicated ruff lint job.
- Completed the "return candidate names instead of `Invalid arguments`"
  improvement at the remaining map frame, layout element, and legend element
  lookup sites that were previously missed.

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

