# Contributing to arcgis-pro-mcp

Thanks for your interest in improving `arcgis-pro-mcp`. This project is an MCP
server that wraps a safe subset of ArcPy, so contributions that either expand
the curated tool surface or tighten its safety guarantees are both welcome.

## Development Setup

`arcpy` itself is only available inside ArcGIS Pro's bundled Python
environment, but the majority of this project can be developed, linted, and
unit-tested on any Python 3.10+ interpreter.

```bash
pip install -e ".[dev]"
python -m compileall arcgis_pro_mcp
python -m unittest discover -s tests -p "test_*.py"
ruff check arcgis_pro_mcp tests
```

When you need to exercise real ArcPy behaviour, run inside the ArcGIS Pro
Python:

```text
C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe -m arcgis_pro_mcp
```

## Branching

- Default branch: `main`.
- Use topic branches for work, e.g. `feature/my-change` or `fix/issue-123`.
- Keep pull requests focused; unrelated cleanups should go in separate PRs.

## Coding Guidelines

- The package targets Python 3.10+; prefer modern typing syntax guarded by
  `from __future__ import annotations`.
- Keep the public MCP tool surface stable. New tools are welcome, but renaming
  or removing an existing tool is a breaking change and needs a changelog
  entry.
- Any new tool that reads or writes files **must** use the helpers in
  `arcgis_pro_mcp/paths.py` so that input roots, project roots, export roots
  and GP output roots are honoured.
- Writing tools **must** respect `ARCGIS_PRO_MCP_ALLOW_WRITE`.
- Avoid adding runtime dependencies beyond `mcp` unless there is a strong
  reason; `arcpy` itself is assumed to be available from the host interpreter.

## Tests

- Unit tests should not require a real `arcpy` install. The existing tests in
  `tests/test_server.py` show how to stub `mcp.server.fastmcp` and patch
  `arcpy` surfaces with `unittest.mock`.
- When you add a tool, add at least one test that exercises the validation
  branches (missing roots, disallowed paths, write gate, etc.).

## Changelog

- Notable changes should be recorded in both `CHANGELOG.md` (English) and
  `CHANGELOG.zh-CN.md` (Chinese), under the *Unreleased* section if present,
  otherwise under the next release heading.

## Security

Please see [`SECURITY.md`](./SECURITY.md) for how to report vulnerabilities.
Do not open public issues for security problems.
