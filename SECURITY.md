# Security Policy

`arcgis-pro-mcp` exposes a curated subset of ArcPy to MCP clients. Because the
same process has full access to ArcGIS Pro, the local filesystem, and any
configured enterprise geodatabases, the project treats security reports with
high priority.

## Supported Versions

Only the latest released version on the `main` branch receives security fixes.
Older tags are not backported.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for a suspected security problem.
Instead, use one of the following private channels:

- GitHub's private vulnerability reporting ("Report a vulnerability" button
  on the repository's *Security* tab), or
- Open a minimal public issue that only requests a private contact, without
  disclosing the vulnerability details.

When reporting, please include:

- affected version / commit,
- operating system and ArcGIS Pro version,
- a minimal reproduction or a description of the attack pre-conditions,
- the impact you observed (write bypass, path escape, credential leak, etc.).

You should receive an acknowledgement within a reasonable time frame. Once a
fix is ready we will coordinate disclosure and credit in `CHANGELOG.md`.

## Scope and Threat Model

The server is designed around the assumption that:

- the MCP client may be partially untrusted (e.g. driven by an LLM),
- the server operator controls the environment variables that gate ordinary,
  destructive, CIM, publishing, public-sharing, enterprise, and SDK-native
  writes, along with every input/output root and GP allowlist,
- the Python window host and optional SDK Add-In listen only on loopback and
  are never proxied or exposed to another machine,
- any Portal, Server, enterprise-geodatabase, or local network-dataset access
  has already been authorized by the operator outside this project.

Issues we consider in-scope include:

- path-validation bypasses that allow reads or writes outside the configured
  roots,
- ways to execute GP tools that are not in the allowlist when the generic GP
  runner is disabled, or to use an allowlisted generic/current-map GP call for
  in-place, destructive, code-executing, ambiguous-output, or overwrite work,
- leaks or unintended use of the fixed database credential environment variables,
- disclosure of window-host bearer tokens, SDK discovery tokens, lease IDs,
  connection passwords, Portal tokens, or signed preflight tokens,
- any operation that modifies data despite `ARCGIS_PRO_MCP_ALLOW_WRITE` being
  unset, or bypasses a more specific destructive/publish/enterprise/SDK gate,
- silent fallback from an authenticated `CURRENT` request to file mode, or a
  write applied after the attached project/session has changed.

Out of scope:

- denial-of-service triggered by operator-supplied parameters that are not
  themselves a path-escape,
- issues that require ArcGIS Pro to already be compromised,
- hardening suggestions that do not correspond to a concrete exploit.

## Deployment Rules

- Keep `ARCGIS_PRO_MCP_ALLOW_WRITE=0` until a task actually needs writes.
- Scope `ARCGIS_PRO_MCP_INPUT_ROOTS`, `ARCGIS_PRO_MCP_PROJECT_ROOTS`,
  `ARCGIS_PRO_MCP_EXPORT_ROOT`, and `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` to the
  smallest practical directories. Do not use a drive root as a convenience.
- Generic GP requires the base write gate, explicit enablement, an exact
  allowlist entry, a configured GP output root, and at least one complete output
  path. Container/name output pairs, in-place or no-output operations,
  destructive/code-executing tools, and existing targets are rejected;
  execution forces `overwriteOutput=False`.
- Local exports and publishing artifacts must use new paths under the export
  root. Service overwrite is a separate external operation guarded by the
  publishing-overwrite gate; it does not authorize replacing local files.
- Creating a database connection requires an exact `PLATFORM|instance` entry in
  `ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST`. The tool can read only the dedicated
  `ARCGIS_PRO_MCP_DB_USERNAME` and `ARCGIS_PRO_MCP_DB_PASSWORD` variables,
  never a caller-chosen environment variable; credentials are not saved unless
  the call explicitly confirms that choice.
- Leave generic GP, publishing, enterprise writes, raw CIM writes, destructive
  operations, and SDK native edits disabled unless the corresponding workflow
  is explicitly required.
- `ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE` protects enterprise version
  management, maintenance, and Utility Network administration. Ordinary
  feature/row edits use the base write gate, plus the destructive gate for
  deletion and the SDK-feature gate for native SDK feature edits; an enterprise
  data source does not implicitly add or remove those requirements.
- Calculate Field accepts only the constrained pure-Arcade expression subset,
  and label expressions are Arcade-only. Python/VB/code blocks and remote
  dynamic evaluation are not accepted. Repair Geometry always uses `KEEP_NULL`
  so that null-geometry rows are not deleted as a repair side effect.
- Do not put credentials in MCP arguments, repository configuration, issue
  bodies, logs, examples, or screenshots. Prefer ArcGIS-managed connection
  files or the fixed server-side database credential variables; response
  payloads must remain redacted.
- Treat both bridge discovery documents as secrets. The Python `CURRENT` host
  stores state under the current user's
  `%LOCALAPPDATA%\ArcGISProMcp\window-host` directory using atomic writes and a
  protected private ACL; readers reject links/reparse points, unexpected size,
  ownership, or DACL. Do not copy either Python or SDK discovery state, and do
  not expose either loopback listener through a proxy or port forward.
- Project toolbox registration accepts compiled `.atbx`/`.tbx` toolboxes, not
  executable Python `.pyt` files. The repository's own `接入当前窗口.pyt` is a
  local ArcGIS Pro launcher and is not a general remote toolbox-loading path.
- Treat an execution timeout after a job entered `RUNNING` as an unknown result.
  Re-read state before retrying any non-idempotent request.

The complete gate and path matrix is maintained in
[`skills/arcgis-pro-mcp/references/security-and-paths.md`](./skills/arcgis-pro-mcp/references/security-and-paths.md).
