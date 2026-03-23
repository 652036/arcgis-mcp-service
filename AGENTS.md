# AGENTS.md

## Cursor Cloud specific instructions

This is a single-service TypeScript MCP (Model Context Protocol) server for ArcGIS geospatial tools. It communicates over **stdio** (JSON-RPC), not HTTP.

### Running and testing

- Standard commands are in `package.json` scripts: `npm start`, `npm run typecheck`, `npm test` (alias for typecheck).
- The server reads from stdin and writes to stdout. To test interactively, pipe newline-delimited JSON-RPC messages and keep stdin open (e.g. with `sleep`):
  ```
  { echo '<initialize msg>'; echo '<initialized notification>'; echo '<tools/call msg>'; sleep 10; } | npx tsx src/index.ts
  ```
- There is no HTTP endpoint, no web UI, and no database. The only external dependency is the ArcGIS REST API (public, no auth required for basic geocoding).

### Environment variables

- `ARCGIS_TOKEN` (optional): API key / OAuth token for authenticated ArcGIS services.
- `ARCGIS_GEOCODE_URL` (optional): Override the default Esri World Geocoding Service URL.

### Gotchas

- The MCP server uses `StdioServerTransport` — it will hang if you run `npm start` directly in a terminal without piping input, because it waits on stdin.
- The project uses Zod v4 with the `zod/v4` import path (not the default `zod` import), which is specific to zod 4.x.
