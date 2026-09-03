# ArcGIS Pro MCP Security And Paths

Call `arcgis_pro_server_capabilities` and `arcgis_pro_tool_info(name="...")` in the running server. This reference explains the gates; it does not replace per-tool policy metadata.

## Policy Environment

All feature flags default to disabled. Enabled boolean values are `1`, `true`, `yes`, or `on` unless a tool says otherwise.

| Variable | Scope |
| --- | --- |
| `ARCGIS_PRO_MCP_ALLOW_WRITE` | Base gate for project/map/data mutations and write GP. It does not by itself authorize destructive, enterprise, publishing, CIM, or SDK editing operations. |
| `ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE` | Additional gate for deletion, truncation, overwrite/removal, discard-all, high-risk schema changes, and other tools marked destructive. Exact confirmations still apply. |
| `ARCGIS_PRO_MCP_ALLOW_CIM_WRITE` | Additional gate for raw CIM mutation. Prefer semantic cartography/layout tools. |
| `ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE` | Additional gate for enterprise version management, reconciliation/post, maintenance, and Utility Network administration. It is not a blanket gate for ordinary feature/row edits. Some enterprise operations also require the destructive gate. |
| `ARCGIS_PRO_MCP_ALLOW_PUBLISH` | Additional gate for creating/staging/uploading sharing artifacts. Publishing is an external mutation and also requires ordinary write permission. |
| `ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE` | Additional gate for sharing to `EVERYONE`; organization/owner scope does not imply it. |
| `ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE` | Additional gate for overwriting an existing service, together with an exact service identity/option. |
| `ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD` | Permits an inline database password only when explicitly enabled. Prefer existing controlled `.sde`/`.ags` connection files. |
| `ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST` | Required exact `PLATFORM|instance` entries for creating a new `.sde` connection. Entries are semicolon-separated. |
| `ARCGIS_PRO_MCP_DB_USERNAME` / `ARCGIS_PRO_MCP_DB_PASSWORD` | Dedicated server-side credentials for new database connections. A caller cannot select another environment-variable name. Credentials are not stored in the connection file by default. |
| `ARCGIS_PRO_MCP_INPUT_ROOTS` | Optional path-list restriction for input projects, datasets, locators, network datasets, connection files, imports, and other path inputs. |
| `ARCGIS_PRO_MCP_PROJECT_ROOTS` | Optional project-path restriction and project discovery roots. Falls back to input roots when unset. |
| `ARCGIS_PRO_MCP_EXPORT_ROOT` | Required absolute root for exports, project copies, layer/map documents, charts, reports, sharing artifacts, and other artifact outputs. |
| `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` | Restricts durable GP/data outputs. Many write wrappers require a configured absolute root rather than treating it as optional. |
| `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP` | Enables the standalone generic GP entry point; disabled by default. |
| `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST` | Exact standalone generic GP names. An allowlist entry does not bypass typed path validation. |
| `ARCGIS_PRO_MCP_PORTAL_ALLOWLIST` | Exact canonical HTTPS Portal targets for publishing. |
| `ARCGIS_PRO_MCP_SERVER_ALLOWLIST` | Exact canonical HTTPS server/FeatureServer targets or exact approved `.ags` connection paths, depending on the tool. |
| `ARCGIS_PRO_MCP_HOST_PORT` | Matches one Python CURRENT host to one MCP client. Use a distinct explicit port for each concurrent Pro instance. |
| `ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST` | Exact SDK GP tools; a tool must also have a built-in reviewed typed contract. |
| `ARCGIS_PRO_MCP_SDK_GP_ENV_ALLOWLIST` | Allowed SDK GP environment names. Only environments implemented by the bridge contract are usable. |
| `ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS` | Additional gate for native undo/redo/save/discard commands. |
| `ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS` | Additional gate for discarding every pending project data edit. Destructive confirmation is still required. |
| `ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS` | Additional gate for native SDK feature create/modify/delete. Delete also requires the destructive gate. |

The Python CURRENT host receives only an allowlisted subset of the stdio process policy. The SDK add-in reads its policy when it loads. Restart the relevant process after changing environment configuration and re-run discovery.

`tool_info.gates` lists configuration that is mandatory for that tool. Optional `ARCGIS_PRO_MCP_INPUT_ROOTS` and `ARCGIS_PRO_MCP_PROJECT_ROOTS` restrictions are enforced whenever configured even when they are not shown as mandatory gates; export and durable GP output roots are mandatory for their respective write paths.

## Path Rules

- Supply absolute paths. Never use a relative path, unresolved user-home shortcut, broad drive root, or workspace root as a convenience boundary.
- Path lists use the operating-system path separator; publishing/server allowlists accept comma, semicolon, or newline-separated exact entries where documented.
- Root checks resolve filesystem links before comparison. Do not rely on a junction/symlink to escape an allowed root.
- `CURRENT`, `memory`, `in_memory`, and opaque temporary-view references are special typed targets, not arbitrary filesystem paths.
- Output tools generally reject an existing target. Do not delete or overwrite it unless the same tool exposes a reviewed overwrite path and `arcgis_pro_tool_info` reports the corresponding gate.
- Input permission is not output permission. A source under `ARCGIS_PRO_MCP_INPUT_ROOTS` does not authorize writing beside it.
- Do not set roots to a whole drive, installation directory, user profile, or shared organization tree when the task needs only a narrow project/data/output folder.

## High-Impact Confirmation Pattern

Environment gates express deployment policy, not user intent for one target. Consequential tools therefore use one or more compare-and-swap values:

- exact dataset, project, network, map/layer URI, index, version, service, or artifact digest;
- exact selection count plus SHA-256 OID digest;
- context, selection, edit, or lease generation;
- a fixed confirmation phrase or `confirm=true`/operation-specific boolean;
- exact expected item sets for bulk imports/deletes.

Obtain these values from a fresh read/preflight and echo them unchanged. Never invent, normalize, reorder, truncate, or reuse stale confirmation values. If a call conflicts or times out after starting, inspect state before deciding whether any further write is safe.

## Generic And SDK GP

Prefer named Python wrappers. `arcgis_pro_gp_run_tool` requires the base write gate, generic-GP enablement, an exact allowlist match, input validation, a configured GP output root, and at least one complete durable output path. It rejects output workspace/name pairs because they do not prove the final target, as well as no-output/in-place operations, existing targets, destructive tools, and code-execution tools. It runs under `overwriteOutput=False`; an allowlist entry cannot relax any of these rules.

`arcgis_pro_current_map_run_analysis` follows the same new-output/root boundary while additionally accepting typed references to layers or tables in the attached map. It has a closed code allowlist and explicitly denies Calculate Field, Calculate Geometry Attributes, and Repair Geometry. Use their reviewed semantic wrappers where available.

SDK GP is intentionally narrower. Submission requires:

- a valid exact-project lease and `confirm=true`;
- the base write gate;
- an exact `ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST` entry;
- a built-in typed contract for that tool;
- configured non-empty input roots and GP output root;
- only allowlisted, implemented environment names.

An allowlist entry cannot convert an arbitrary GP tool into a safe contract. The SDK bridge does not accept destructive GP, arbitrary `arcpy.nax` objects, `.sde`/`.ags` output connections, or Portal ready-to-use network services. Cancellation is cooperative; inspect final status fields before accepting a success after cancellation or lease invalidation.

Calculate Field is available only through its dedicated wrapper and accepts the constrained pure-Arcade subset; Python/VB/code blocks and remote/dynamic evaluation are rejected. Label expression create/update is likewise Arcade-only and its CIM-backed writes require the CIM gate reported by tool metadata. Repair Geometry always uses `KEEP_NULL`, so it does not delete null-geometry records.

## Enterprise, Utility Network, And Schema Integrity

- Enterprise version management, maintenance, and mutating Utility Network administration require ordinary write plus enterprise-write permission. Reconcile/post/delete/unregister and similar irreversible operations also require the destructive gate and exact version/dataset confirmations.
- Ordinary feature/row edits, including edits whose dataset happens to be in an enterprise geodatabase, use the base write gate; delete-like operations additionally use the destructive gate, and native SDK feature edits additionally use `ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS`. The enterprise gate neither replaces nor broadens those edit gates.
- Exact approved `.sde` or HTTPS FeatureServer targets are required. Do not expose connection credentials or broaden the server allowlist.
- Creating a new `.sde` additionally requires an exact `PLATFORM|instance` database allowlist entry. Prefer a pre-created controlled connection file. The creation tool reads only its two fixed credential variables, defaults to `DO_NOT_SAVE_USERNAME`, and requires explicit confirmation before saving credentials.
- Utility Network validation/update operations use an exact network identity. Updating all subnetworks in a tier has an additional destructive confirmation; trace/read/export behavior must be taken from that tool's policy.
- Attribute-rule, field-group, and contingent-value mutations are schema changes. They require a schema lock, destructive permission, exact dataset echo, and the fixed confirmation string required by the schema tool. Attribute-rule import additionally confirms the expected rule-name set; contingent-value import parses the official CSV manifest and verifies the resulting inventory.
- Domain, subtype, attachment, topology, relationship-class, index, editor-tracking, and GlobalID operations vary in destructiveness. Inspect each exact tool instead of treating the category as one blanket permission.

Use disposable or backed-up data for first-run validation. Production privileges, ownership, schema locks, version type, server version, and active edit sessions can change behavior even when local checks pass.

## Publishing Workflow

1. Read Portal status and confirm the active target is on the exact Portal/server allowlist.
2. Create and analyze a new, non-existing `.sddraft` under the export root. Public and service-overwrite options require their separate gates.
3. Read the artifact SHA-256 and pass that exact digest when staging to a new, non-existing `.sd`.
4. Read the staged `.sd` digest and pass it unchanged when uploading to the same intended target.
5. Verify the external result. Never automatically retry an upload with an unknown outcome.

Do not put passwords, tokens, cookies, connection strings, or secret-like content in summaries, tags, group names, URLs, logs, issues, test fixtures derived from real systems, or repository configuration.

## Live Bridge Threat Boundary

- Both live bridges bind only to loopback and authenticate each local request with random per-session material stored in private discovery state. Python `CURRENT` state lives under `%LOCALAPPDATA%\ArcGISProMcp\window-host` on Windows; atomic writes, a protected current-user ACL, owner/DACL/type/size checks, and symlink/reparse-point rejection make the state fail closed. Treat the state document itself as a local capability secret.
- Python `CURRENT` additionally binds calls to the last explicitly confirmed host session and exact saved project.
- SDK commands additionally require an exclusive short project lease; context-changing commands use map/layer identities and generation checks.
- Authentication is not authorization. All normal gates, roots, typed schemas, and confirmations still apply.
- Never return or persist discovery bearer tokens or raw lease IDs. Public MCP SDK tools use an opaque process-local `sdk_session_ref`.
- Neither bridge is a supported remote-control endpoint. Remote access requires a separate, explicitly designed mutually authenticated transport, not a broader listen address or copied discovery token.
