# ArcGIS Pro MCP SDK bridge

This directory is a minimal ArcGIS Pro 3.6 add-in skeleton for the capabilities
that cannot be implemented reliably through an in-process Python script tool:
live active-view state, Pro events, native undoable edits, and cancellable
asynchronous geoprocessing.

It is intentionally independent from the current Python host. Nothing in this
directory changes the Python MCP protocol or automatically enables write access.

## Build

Requirements:

- ArcGIS Pro 3.6
- Visual Studio 2022 17.13 or newer, or the .NET 8 SDK
- ArcGIS Pro SDK for .NET 3.6 when using the Visual Studio templates

The project targets `net8.0-windows`/`win-x64` and pins the official
`Esri.ArcGISPro.Extensions30` package to `3.6.0.59527`.

```powershell
dotnet restore .\ArcGISProMcp.AddIn.csproj
dotnet build .\ArcGISProMcp.AddIn.csproj -c Release
```

The Esri build targets package and register an `.esriAddInX` when the required
ArcGIS Pro tooling is available. Open a saved project after installing the
add-in; the auto-loaded module starts the bridge without adding Ribbon UI.

## Discovery and authentication

At every add-in load, the bridge creates independent 256-bit random bearer and
server-session tokens and listens on an operating-system-assigned port on
`127.0.0.1` only. It does not listen on all interfaces and does not accept a
configured remote address.

The discovery document is written to:

```text
%LOCALAPPDATA%\ArcGISProMcp\sdk-bridge\bridge-<ArcGISPro PID>.json
```

The directory and file receive a Windows ACL containing only the current user.
The document contains `port`, `token`, `serverSessionId`, process ID, protocol
version, and creation time. It is deleted on normal unload. Clients must still
treat a stale file as untrusted and verify process/session status.

Every HTTP request, including status, requires:

```text
Authorization: Bearer <token from discovery document>
```

The bridge rejects non-loopback peers, a `Host` value other than the discovered
`127.0.0.1:<port>`, browser `Origin` requests, chunked bodies, duplicate
headers, request pipelining, bodies over 1 MiB, and headers over 16 KiB. Request
headers and bodies must arrive within 10 seconds, and at most 24 clients are
served concurrently. It never logs request headers, bodies, tokens,
credentials, or GP parameters. All responses use `Cache-Control: no-store`.

## Project lease

Authentication identifies the local bridge instance; a short project lease
binds commands to one exact saved `.aprx` URI. Acquire it with:

```http
POST /v1/lease/acquire
Authorization: Bearer ...
Content-Type: application/json

{
  "serverSessionId": "...",
  "expectedProjectUri": "C:\\GIS_Projects\\Example\\Example.aprx",
  "ttlSeconds": 45
}
```

Subsequent requests require both returned values:

```text
X-ArcGIS-Pro-Session: <serverSessionId>
X-ArcGIS-Pro-Lease: <leaseId>
```

The lease response contains `leaseId`, `serverSessionId`, `projectUri`,
`generation`, and `expiresAtUtc`. Clients send the ID headers shown above; the
generation is returned for diagnostics and result correlation, not as another
header.

The lease lasts 10–120 seconds, defaults to 45 seconds, and is exclusive.
`POST /v1/lease/renew` renews it and `POST /v1/lease/release` releases it. Every
leased request re-reads the current project URI. A project switch, bridge
restart, expired lease, or URI mismatch fails closed instead of silently
retargeting another project. Acquire, renew, release, and mutation submission
are serialized; release is an exact lease-ID compare-and-swap. Project events
advance an internal generation before canceling registered work, so a stale
request cannot release or submit work under a replacement lease.
Releasing a lease, replacing an expired lease, or detecting lease/project
invalidation also requests cancellation of registered GP work.

## Endpoints

All routes are HTTP/1.1 JSON and close the connection after one request.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/v1/status` | Process, session, write gate, project, and active map status |
| `POST` | `/v1/lease/acquire` | Acquire an exclusive project lease |
| `POST` | `/v1/lease/renew` | Renew the current lease |
| `POST` | `/v1/lease/release` | Release the current lease |
| `GET` | `/v1/context` | Active-view/camera/layer/selection/edit snapshot |
| `GET` | `/v1/events?after=0&limit=128&waitMs=30000` | Bounded long-poll event stream |
| `POST` | `/v1/jobs` | Start one allowlisted asynchronous GP job |
| `GET` | `/v1/jobs/{jobId}` | Read job status/progress/result |
| `POST` | `/v1/jobs/{jobId}/cancel` | Request SDK cancellation |
| `GET` | `/v1/edit/status` | Read pending-edit and native undo/redo state |
| `POST` | `/v1/edit/undo` | Undo the active map's latest operation |
| `POST` | `/v1/edit/redo` | Redo the active map's latest operation |
| `POST` | `/v1/edit/save` | Save all pending project data edits |
| `POST` | `/v1/edit/discard` | Discard all pending project data edits |
| `POST` | `/v1/view/camera` | Set a typed camera with map/context CAS |
| `POST` | `/v1/view/zoom-layer` | Zoom to one exact layer URI |
| `POST` | `/v1/view/refresh` | Redraw and wait for a later DrawComplete event |
| `POST` | `/v1/view/time` | Set or disable the active map-view time range |
| `POST` | `/v1/view/open-table` | Open an existing standalone table by exact URI |
| `POST` | `/v1/features/create` | Native undoable single-feature create |
| `POST` | `/v1/features/modify` | Native undoable edit of the exact selected OID set |
| `POST` | `/v1/features/delete` | Native undoable delete of the exact selected OID set |

`GET /v1/status` is the only route above that needs only the bearer token.
Every other route except lease acquisition additionally needs the session and
lease headers. Status advertises a machine-readable `capabilities` object and
`typedGpContracts`; clients must inspect those values rather than infer support.

## Live context and view CAS

`GET /v1/context` returns saved-project identity; `activeViewType` (`map`,
`layout`, `table`, or `none`) and `activeViewUri`; active map identity; typed
camera values; the first TOC-selected layer; current time; and monotonic
`contextGeneration`, `selectionGeneration`, `editGeneration`, and
`drawGeneration` values.

Selection contains total/counts and SHA-256 OID digests globally and per map
member. It intentionally omits OIDs and all feature values. A layer digest is
SHA-256 over its exact URI and ascending OIDs. It is an optimistic concurrency
token, not an authorization token.

The stable context shape is:

```json
{
  "projectUri": "C:\\GIS_Projects\\Example\\Example.aprx",
  "projectName": "Example",
  "projectReadOnly": false,
  "activeViewType": "map",
  "activeViewUri": "map-uri",
  "activeMapName": "Map",
  "activeMapUri": "map-uri",
  "camera": {
    "x": 0.0, "y": 0.0, "z": 0.0, "scale": 10000.0,
    "heading": 0.0, "pitch": -90.0, "roll": 0.0,
    "spatialReferenceWkid": 3857, "spatialReferenceName": "WGS 1984 Web Mercator Auxiliary Sphere"
  },
  "activeLayer": {"layerUri": "layer-uri", "name": "Roads", "layerType": "FeatureLayer"},
  "selectedLayerCount": 1,
  "selection": {
    "totalCount": 2,
    "oidDigest": "sha256-hex",
    "layers": [{"layerUri": "layer-uri", "name": "Roads", "count": 2, "oidDigest": "sha256-hex"}]
  },
  "activeTime": {"start": "2026-01-01T00:00:00.0000000Z", "end": "2026-01-02T00:00:00.0000000Z"},
  "contextGeneration": 10,
  "selectionGeneration": 3,
  "editGeneration": 2,
  "drawGeneration": 18
}
```

Unavailable nullable members are omitted. When a table pane is active,
`activeViewUri` is the table/layer URI and its owning map is still reported as
`activeMapUri`; `activeLayer` specifically means the first TOC-selected layer
of an active map view. `activeTime` is omitted when the map has no time-aware
members; an empty `activeTime` object means time-aware data exists but the view
is currently showing all time (time filtering disabled).

Every `/v1/view/*` body contains `confirm`, `expectedMapUri`, and
`expectedContextGeneration` from the latest context. A changed map or
generation returns `409`; do not blindly retry. Camera values must be finite,
scale positive, pitch -90..90, and `durationMilliseconds` 0..30000. Camera
requests also echo the latest `camera.spatialReferenceWkid` as
`expectedSpatialReferenceWkid`; the bridge never guesses or silently projects
coordinate values. Zoom also requires exact `layerUri`. Refresh accepts
`clearCache` and `waitMilliseconds` (1..30000) and reports whether a later SDK
`DrawComplete` event for that same map arrived.

Time values must include `Z` or an explicit UTC offset. Both time bounds null
disable time; a non-null range is rejected when the map has no time-aware
members. Open-table only accepts an already loaded standalone-table URI in the
expected map; it never opens an arbitrary path or connection file.

All JSON member names are exact camelCase and unknown members are rejected.
The view request bodies extend the three common CAS fields as follows:

| Route | Additional request members |
| --- | --- |
| `/v1/view/camera` | required `expectedSpatialReferenceWkid`; optional `x`, `y`, `z`, `scale`, `heading`, `pitch`, `roll`; `durationMilliseconds` |
| `/v1/view/zoom-layer` | `layerUri`; `selectedOnly`; `durationMilliseconds`; `maintainViewDirection` |
| `/v1/view/refresh` | `clearCache`; `waitMilliseconds` |
| `/v1/view/time` | nullable ISO-8601 `start` and `end` |
| `/v1/view/open-table` | exact loaded `tableUri` |

Each successful view response is
`{"ok":true,"view":{"completed":true,"drawCompleted":true,"context":{...}}}`;
`drawCompleted` is present only for refresh. A refresh timeout returns a normal
response with `drawCompleted:false`, not a fabricated completion event.

GP start, GP cancel, and editing command bodies must contain
`{"confirm":true}`. Starting a GP job uses:

```json
{
  "toolName": "management.CopyFeatures",
  "parameters": ["C:\\GIS_Data\\input.gdb\\roads", "C:\\GIS_Outputs\\GP\\output.gdb\\roads_copy"],
  "environments": {"workspace": "C:\\GIS_Outputs\\GP\\output.gdb"},
  "confirm": true
}
```

GP execution is disabled unless all of these are true:

- `ARCGIS_PRO_MCP_ALLOW_WRITE=1`
- the exact tool is present in `ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST`
- the tool has one of the built-in typed contracts listed below
- `ARCGIS_PRO_MCP_INPUT_ROOTS` is non-empty and every typed input path is below it
- `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` is configured and every typed output path and
  path-valued environment is below it
- every supplied environment name is present in
  `ARCGIS_PRO_MCP_SDK_GP_ENV_ALLOWLIST`
- a valid project lease and explicit confirmation are present

Both allowlists are comma- or semicolon-separated. They default to empty. The
built-in GP contracts are:

| Tool | Parameters | Typed paths |
| --- | --- | --- |
| `management.CopyFeatures` | 2–6 | input index 0, output index 1 |
| `analysis.Buffer` | 3–11 | input index 0, output index 1 |
| `analysis.PairwiseBuffer` | 3–11 | input index 0, output index 1 |
| `sa.Fill` | 2–3 | input 0, output 1; positive optional z-limit |
| `sa.FlowDirection` | 2–5 | input 0, output 1, optional output 3; fixed enums |
| `sa.FlowAccumulation` | 2–5 | input 0, optional input 2, output 1; fixed enums |
| `sa.SnapPourPoint` | 4–5 | inputs 0/1, output 2; positive distance |
| `sa.Watershed` | 3–4 | inputs 0/1, output 2 |
| `stats.HotSpots` | 3–10 | input 0, optional weights input 8, output 2 |
| `stats.OptimizedHotSpotAnalysis` | 2–9 | input 0, optional polygon inputs 4/5, output 1 |
| `stats.ClustersOutliers` | 3–10 | input 0, optional weights input 7, output 2 |
| `stats.SpatialAutocorrelation` | 2–7 | input 0; reports forced off |
| `stats.AverageNearestNeighbor` | 1–4 | input 0; reports forced off |
| `stats.MultiDistanceSpatialClustering` | 3–8 | input 0, output 1; display forced off |
| `na.MakeRouteAnalysisLayer` | 2–3 | network input 0; `MCP_` name; explicit `.gdb` workspace |
| `na.MakeServiceAreaAnalysisLayer` | 2–5 | network input 0; typed direction/cutoffs; `.gdb` workspace |
| `na.MakeODCostMatrixAnalysisLayer` | 2–5 | network input 0; typed cutoff/count; `.gdb` workspace |
| `na.MakeClosestFacilityAnalysisLayer` | 2–5 | network input 0; typed direction/cutoff; `.gdb` workspace |
| `na.AddLocations` | exactly 3 | input table 2; fixed sublayer enum |
| `na.Solve` | 1–2 | `MCP_` analysis layer; `SKIP`/`HALT` only |

Spatial Analyst and Network Analyst contracts still depend on the matching Pro
license. No license token or Portal credential is accepted. Network jobs use an
explicit stateful sequence: make a unique `MCP_...` layer, add typed inputs,
then solve. `management.CopyFeatures` accepts a temporary analysis reference
only with that prefix and a known route/service-area/OD/closest-facility
sublayer; its durable destination remains under the mandatory GP output root.
The bridge exposes neither arbitrary `arcpy.nax` objects nor Portal ready-to-use
services.

Each `na.Make*AnalysisLayer` call must provide a `workspace` environment that
is an existing file geodatabase under `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT`; the
resulting temporary analysis layer is added to the active map so the subsequent
typed `AddLocations`, `Solve`, and `CopyFeatures` calls can address its exact
`MCP_` name. A prefix alone is not sufficient: the bridge registers the name
only after a successful make job and binds it to the current lease ID, project,
and lease generation. The registry is cleared on lease/project invalidation.
The bridge does not fall back to the project's default geodatabase.

Adding a name to the environment allowlist does not make an arbitrary
environment usable. The skeleton only contracts `workspace` and
`scratchWorkspace`, and validates both as output paths. It injects
`overwriteoutput=false`; the caller cannot override it. Existing filesystem
links are resolved before root comparison, and output paths containing `.sde`
or `.ags` connection files are rejected even when the connection file is under
the output root.

The bridge retains at most 64 completed jobs, permits one running job, uses the
SDK GP thread, and keeps a cancellation token for `POST .../cancel`.
Cancellation is cooperative: `cancelRequested` means a request was sent, not
that side effects were prevented. A tool that completes after cancellation is
reported as `succeededAfterCancellationRequest`; one that completes after its
lease/project context is invalidated is
`succeededAfterLeaseInvalidation`. Callers must inspect `status`,
`cancellationRequested`, and `leaseInvalidated` before treating a job as a
normal success.

Example fail-closed configuration:

```text
ARCGIS_PRO_MCP_ALLOW_WRITE=1
ARCGIS_PRO_MCP_INPUT_ROOTS=C:\GIS_Data
ARCGIS_PRO_MCP_PROJECT_ROOTS=C:\GIS_Projects
ARCGIS_PRO_MCP_GP_OUTPUT_ROOT=C:\GIS_Outputs\GP
ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST=management.CopyFeatures
ARCGIS_PRO_MCP_SDK_GP_ENV_ALLOWLIST=workspace;scratchWorkspace
ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS=0
ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS=0
ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS=0
ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=0
```

Every configured root must be an existing absolute directory when the add-in
loads. `ARCGIS_PRO_MCP_PROJECT_ROOTS` falls back to the input roots when it is
unset, matching the Python host's project policy.

## Editing command preconditions

Editing commands are disabled unless both
`ARCGIS_PRO_MCP_ALLOW_WRITE=1` and
`ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS=1` are set. First call
`GET /v1/edit/status`; its response includes `activeMapUri` and a monotonic
`editGeneration`. Undo and redo require both values:

```json
{
  "confirm": true,
  "expectedMapUri": "map-uri-from-status",
  "expectedEditGeneration": 12
}
```

Save requires `confirm` and `expectedEditGeneration`. Discard affects all
pending data edits in the project, so it additionally requires
`ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS=1` and
`ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=1`, plus
`"confirmDiscardAll": true`. These checks detect active-map and edit-event
changes, but user interaction can still race an SDK command; clients must stop
on a `409` and refresh status rather than blindly retrying.

## Native feature edits

Create/modify/delete need both `ARCGIS_PRO_MCP_ALLOW_WRITE=1` and
`ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS=1`. Delete additionally needs
`ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=1` and
`"confirmDeleteSelection":true`.

Every feature request supplies exact `expectedMapUri`, `layerUri`,
`expectedContextGeneration`, and `expectedEditGeneration`. Modify/delete also
supply `expectedSelectionGeneration`, `expectedCount`, and the target layer's
`expectedOidDigest` from `/v1/context`. The bridge re-reads the current layer
selection immediately before execution and rejects any mismatch. Modify is
capped at 100 selected features and delete at 1000; one geometry modification
can target only one selected feature.

| Route | Operation-specific request members |
| --- | --- |
| `/v1/features/create` | required `geometry`; optional `attributes` object |
| `/v1/features/modify` | selection CAS fields above; at least one of `attributes` or `geometry` |
| `/v1/features/delete` | selection CAS fields above; required `confirmDeleteSelection:true` |

A successful response is
`{"ok":true,"edit":{"operation":"modify","layerUri":"...","affectedCount":2,"oidDigest":"...","context":{...}}}`.
The create response omits `oidDigest` because the bridge never exposes or
accepts a caller-selected new OID.

Attributes are capped at 64 fields. Every key must exactly match an editable,
non-system, non-geometry SDK field. Complex JSON, binary/raster/XML fields,
booleans, non-finite numbers, and strings over 8192 characters are rejected.
JSON strings, integers, and floating-point values must match the SDK field
type; date/timestamp values additionally require `Z` or an explicit UTC offset.
Date-only and time-only fields are not accepted by this narrow contract.

Geometry is deliberately narrow:

```json
{
  "type": "polygon",
  "spatialReferenceWkid": 3857,
  "coordinates": [[0, 0], [10, 0], [10, 10], [0, 0]]
}
```

Only 2D point, single-part straight polyline, and single-ring straight polygon
are accepted, with at most 10000 vertices. Polygon rings must be explicitly
closed. Z, M, curves, multipart geometries, reprojection, multipoints, and
multipatches are rejected. WKID and geometry type must exactly match the
feature class. The operation is forced to `EditOperationType.Long`, so a data
source that cannot participate in Pro undo/save/discard fails instead of being
directly committed. Each successful request is one named native undo entry.

## Events

The controller subscribes to these ArcGIS Pro SDK events:

- active map view changed
- map camera changed
- TOC layer selection changed
- map selection changed
- map draw completed
- map-view time changed
- layout map frame activated, deactivated, or navigated
- active pane changed (including map/layout/table focus)
- edit operation completed
- project opened or closed (also invalidates the lease and requests cancellation
  of a running GP job)

The ring buffer retains 512 metadata-only notifications. Events contain a
sequence number, type, and UTC timestamp—never feature values, paths, request
arguments, tokens, or credentials. `truncated: true` tells a client that its
cursor predates the oldest retained event.

## Deliberate limits

- This is a bridge skeleton, not a generic remote-code or arbitrary CIM API.
- It does not expose portal sign-in, cookies, OAuth tokens, or credentials.
- It does not retain cross-request `EditOperation` objects. Each typed feature
  request creates and atomically executes one native operation.
- GP is deliberately not generic. New tools require a code-reviewed typed
  contract that identifies every input/output path; adding an allowlist entry
  alone is insufficient. Destructive tools are not included.
- The bridge does not provide TLS because it is loopback-only and authenticates
  each request with a random token protected by a per-user ACL. Remote transport
  must be a separate mutually authenticated component, not a broader bind.
- Interactive prompt geometry is advertised as
  `capabilities.promptGeometry=false`. It needs a DAML-registered `MapTool`,
  lifecycle cancellation, and in-Pro mouse/keyboard acceptance tests. This
  checkout has no .NET SDK/Visual Studio packaging toolchain, so metadata-only
  compilation is not enough to claim that UI capability safely.
