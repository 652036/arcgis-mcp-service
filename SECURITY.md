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
- the server operator controls the environment variables that gate write
  access, export roots, GP output roots, input roots, project roots, the
  generic GP runner, and inline DB passwords,
- the server is never exposed to the public internet directly.

Issues we consider in-scope include:

- path-validation bypasses that allow reads or writes outside the configured
  roots,
- ways to execute GP tools that are not in the allowlist when the generic GP
  runner is disabled,
- leaks of credentials passed via `*_env_var` options,
- any operation that modifies data despite `ARCGIS_PRO_MCP_ALLOW_WRITE` being
  unset.

Out of scope:

- denial-of-service triggered by operator-supplied parameters that are not
  themselves a path-escape,
- issues that require ArcGIS Pro to already be compromised,
- hardening suggestions that do not correspond to a concrete exploit.
