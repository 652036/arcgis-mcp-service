<!-- Thanks for contributing to arcgis-pro-mcp! -->

## Summary

<!-- What does this PR change and why? Keep it short. -->

## Type of change

- [ ] Bug fix
- [ ] New MCP tool / new capability
- [ ] Refactor / internal cleanup
- [ ] Documentation only
- [ ] CI / build / packaging

## Safety checklist

- [ ] New file-reading tools go through `arcgis_pro_mcp/paths.py` input-root / project-root validation.
- [ ] New file-writing tools respect `ARCGIS_PRO_MCP_ALLOW_WRITE` and the export / GP output roots.
- [ ] No new way to run arbitrary GP tools outside the allowlist.
- [ ] No database credentials are accepted inline by default.

## Test plan

- [ ] `python -m compileall arcgis_pro_mcp`
- [ ] `python -m unittest discover -s tests -p "test_*.py"`
- [ ] `ruff check arcgis_pro_mcp tests`
- [ ] Manual verification inside ArcGIS Pro Python (describe below, if applicable)

## Changelog

- [ ] Updated `CHANGELOG.md` and `CHANGELOG.zh-CN.md`, or this change does not need an entry.
