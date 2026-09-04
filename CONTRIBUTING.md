# Contributing to arcgis-pro-mcp

Thank you for improving `arcgis-pro-mcp`. The project exposes a large but deliberately constrained ArcGIS Pro surface to MCP clients. Contributions are welcome when they add a typed, reviewable capability or make an existing boundary safer and more predictable.

## Runtime boundaries

Real execution requires Windows, ArcGIS Pro, and an ArcGIS Pro Python environment with `arcpy`. A normal Python 3.10+ interpreter can run protocol, validation, and no-ArcPy tests, but that does not prove ArcPy runtime behaviour.

The repository has three execution surfaces:

- file mode in the standalone MCP process;
- Python live-window protocol v4 for explicit `aprx_path="CURRENT"` calls;
- the optional ArcGIS Pro SDK Add-In for events, native edits, view CAS, and asynchronous GP.

Do not silently route one surface to another. A disconnected `CURRENT` request and an expired SDK lease must fail closed.

## Development setup

Install the package and development dependencies in a disposable environment:

```powershell
python -m pip install -e ".[dev]"
ruff check .
python -m compileall -q arcgis_pro_mcp
python -m py_compile arcgis_pro_mcp_bootstrap.py "接入当前窗口.py" "接入当前窗口.pyt"
python -m unittest discover -s tests -p "test_*.py"
```

The supported MCP dependency line is `mcp>=1.20,<2`. Test against that line rather than relying on an incompatible globally installed major version.

For ArcPy smoke tests, use the target ArcGIS Pro installation or its cloned Python environment. A typical launcher is:

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -c "import arcpy; print(arcpy.GetInstallInfo())"
```

When changing the SDK project, also restore/build `sdk/ArcGISProMcp.AddIn/ArcGISProMcp.AddIn.csproj` with the ArcGIS Pro 3.6 SDK toolchain and exercise the Add-In inside Pro. A metadata-only compile is not enough to claim UI, event, MCT, packaging, or EditOperation correctness.

## Change workflow

- Branch from `main` and keep pull requests focused.
- Preserve unrelated working-tree changes.
- Describe which execution surfaces were tested: normal Python, ArcGIS Pro Python, Python `CURRENT`, and/or SDK Add-In.
- Report skipped runtime validation explicitly, including the missing ArcGIS version, extension licence, dataset, Portal, enterprise geodatabase, or SDK tooling.

## Adding or changing a tool

Tool work is not complete when a helper function exists. Keep these surfaces synchronized:

1. Put implementation logic in the appropriate focused module under `arcgis_pro_mcp/`.
2. Register a stable, explicitly typed MCP wrapper in `arcgis_pro_mcp/server.py`.
3. Add or update its policy metadata in `arcgis_pro_mcp/tool_protocol.py` so `arcgis_pro_tool_info` reports the correct read/write class, roots, window requirements, confirmation fields, and gates.
4. Route all project, input, export, and GP output paths through `arcgis_pro_mcp/paths.py`; do not duplicate or bypass root checks.
5. Add no-ArcPy unit tests for success shape and failure branches. Write tests should cover disabled gates, path escape, existing-output behaviour, exact confirmation, and ambiguous/partial outcomes where relevant.
6. Add an ArcPy runtime smoke test for the exact ArcGIS Pro version and data type when the API signature or result shape cannot be proven with mocks.
7. Update `README.md`, `docs/WINDOW_CONTROL.md`, or the SDK README when user-visible setup or protocol changes.
8. Update `skills/arcgis-pro-mcp/SKILL.md` and the relevant files under `skills/arcgis-pro-mcp/references/` when tool names, workflows, gates, roots, or runtime constraints change.
9. Record notable changes in both `CHANGELOG.md` and `CHANGELOG.zh-CN.md`.

Avoid hard-coded tool totals in documentation. The authoritative inventory is produced at runtime by `arcgis_pro_server_capabilities()` and `arcgis_pro_tool_info()`.

## Safety requirements

- Ordinary writes require the base write gate to remain enabled. It is enabled
  when `ARCGIS_PRO_MCP_ALLOW_WRITE` is unset; set the variable to `0`, `false`,
  `no`, or `off` for a read-only deployment.
- Destructive, raw CIM, publishing, public-sharing, overwrite-publish, enterprise, and SDK-native operations must enforce their narrower gates as well.
- Output-producing GP must require and remain below `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT`.
- Prefer named wrappers. Generic Python GP and SDK GP require exact allowlists; SDK GP also requires a reviewed typed contract.
- Existing output must not be overwritten implicitly. Destructive operations need an exact target and the tool-specific confirmation/count contract.
- Use environment-variable names or ArcGIS-managed connection files for credentials. Do not accept or log secrets unless an existing, documented security gate explicitly permits that channel.
- Treat a timeout after execution began as an unknown outcome. Read state before any retry of a non-idempotent operation.
- Never expose the Python host or SDK Add-In loopback listener through a proxy, remote bind, or port forward.

See [SECURITY.md](SECURITY.md) and the [gate/path matrix](skills/arcgis-pro-mcp/references/security-and-paths.md).

## Python live-window protocol

Changes to `pro_attach.py`, `pro_host.py`, `session_refs.py`, the bootstrap, or either launcher must preserve protocol version and module-generation consistency. Update both sides together and test:

- authentication and protocol/package mismatch;
- saved-project target checks and project-switch rejection;
- session latch reset after restart or same-port replacement;
- queue admission, queued cancellation, running timeout, and stop races;
- forwarded policy filtering without secret disclosure;
- complete package reload after the old host stops.

Do not reintroduce partial `importlib.reload(pro_host)` startup. It can combine incompatible cached generations inside ArcGIS Pro.

## SDK bridge protocol

The Add-In is a narrow local capability bridge, not a generic HTTP API. Changes to its wire contract must update the C# implementation, `arcgis_pro_mcp/sdk_bridge.py`, MCP wrappers, tests, `sdk/ArcGISProMcp.AddIn/README.md`, and `docs/WINDOW_CONTROL.md` together.

Preserve loopback-only binding, per-load random credentials, per-user discovery ACLs, exclusive short project leases, request size/deadline limits, metadata-only events, generation/CAS checks, and typed GP path contracts. Never return bearer tokens or lease secrets through MCP.

New native feature geometry or GP contracts need explicit field/path typing, bounded inputs, licence/error handling, and in-Pro tests. Adding a name to an allowlist is not a substitute for a contract.

## Code style and compatibility

- Target Python 3.10+ and keep `from __future__ import annotations` where appropriate.
- Prefer small modules and plain JSON-serializable return values.
- Keep public MCP names and parameters stable. Renames or removals are breaking changes and require migration notes.
- Do not add a runtime dependency unless the capability cannot reasonably use the standard library, MCP, ArcPy, or the ArcGIS Pro SDK already in scope.
- Use official ArcPy/ArcGIS Pro SDK APIs. Do not invent fallback methods that merely resemble an API name.

## Tests and review evidence

A pull request should normally include:

- focused unit tests for each new helper/tool;
- the full `ruff`, compile, and unittest results;
- ArcGIS Pro Python results for ArcPy-dependent changes;
- SDK build and in-Pro results for SDK changes;
- a short capability/gate matrix for security-sensitive tools;
- screenshots only when visual state is essential, with paths, user names, server names, project names, and tokens redacted.

Tests should not require `arcpy` unless explicitly marked as runtime integration tests. Existing unit tests use mocks/stubs so CI can run without an ArcGIS installation.

## Open-source privacy checklist

Before committing, review all tracked and untracked files for:

- local user/profile/Desktop paths;
- customer, organisation, Portal, database, server, share, and project names;
- bearer tokens, passwords, connection strings, cookies, lease IDs, signed preflight tokens, and discovery documents;
- `.sde`, `.ags`, `.aprx`, generated output, build artefacts, logs, crash dumps, and screenshots;
- proprietary sample data or copied Esri binaries.

Use neutral placeholders such as `C:\GIS_Data` and `example.invalid`. Do not commit ArcGIS SDK packages, locally built `.esriAddInX` files, or licensed Esri assemblies.

## Changelog and security reports

Add user-visible changes to both changelogs under `[Unreleased]` until a release is cut. Security issues must not be disclosed in a public Issue; follow the private process in [SECURITY.md](SECURITY.md).
