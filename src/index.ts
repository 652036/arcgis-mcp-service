import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import * as z from "zod/v4";
import {
  findAddressCandidates,
  getLayerMetadata,
  queryFeatureLayer,
  reverseGeocode,
  suggestAddress,
} from "./arcgis-client.js";

const mcp = new McpServer({
  name: "arcgis-mcp",
  version: "1.0.0",
});

mcp.registerTool(
  "arcgis_geocode",
  {
    description:
      "Forward geocode a single-line address or place name using the configured ArcGIS Geocoding service (default: Esri World Geocoding). Set ARCGIS_TOKEN for ArcGIS Online API key or OAuth token if required.",
    inputSchema: {
      single_line: z
        .string()
        .min(1)
        .describe("Address or place text to geocode"),
      max_locations: z
        .number()
        .int()
        .min(1)
        .max(20)
        .optional()
        .describe("Maximum candidate locations (default 5)"),
    },
  },
  async ({ single_line, max_locations }) => {
    const data = await findAddressCandidates(single_line, max_locations ?? 5);
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify(data, null, 2),
        },
      ],
    };
  },
);

mcp.registerTool(
  "arcgis_reverse_geocode",
  {
    description:
      "Reverse geocode a WGS84 longitude/latitude pair using the configured ArcGIS Geocoding service.",
    inputSchema: {
      longitude: z.number().describe("Longitude in WGS84 (decimal degrees)"),
      latitude: z.number().describe("Latitude in WGS84 (decimal degrees)"),
    },
  },
  async ({ longitude, latitude }) => {
    const data = await reverseGeocode(longitude, latitude);
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify(data, null, 2),
        },
      ],
    };
  },
);

mcp.registerTool(
  "arcgis_geocode_suggest",
  {
    description:
      "Return address/place autocomplete suggestions for partial user input.",
    inputSchema: {
      text: z.string().min(1).describe("Partial address or place string"),
      max_suggestions: z
        .number()
        .int()
        .min(1)
        .max(15)
        .optional()
        .describe("Max suggestions (default 5)"),
    },
  },
  async ({ text, max_suggestions }) => {
    const data = await suggestAddress(text, max_suggestions ?? 5);
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify(data, null, 2),
        },
      ],
    };
  },
);

mcp.registerTool(
  "arcgis_layer_metadata",
  {
    description:
      "Fetch ArcGIS REST metadata for a map/feature layer (GET layer URL with f=json). Example: .../FeatureServer/0",
    inputSchema: {
      layer_url: z
        .string()
        .url()
        .describe("Full REST URL of the layer, without query string"),
    },
  },
  async ({ layer_url }) => {
    const data = await getLayerMetadata(layer_url);
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify(data, null, 2),
        },
      ],
    };
  },
);

mcp.registerTool(
  "arcgis_query_layer",
  {
    description:
      "Run a query against an ArcGIS feature layer (GET .../query). Uses SQL-style where clause. Respects layer security; use ARCGIS_TOKEN when the service requires authentication.",
    inputSchema: {
      layer_url: z
        .string()
        .url()
        .describe("Full REST URL of the layer (e.g. .../FeatureServer/0)"),
      where: z
        .string()
        .optional()
        .describe('SQL where clause (default "1=1")'),
      out_fields: z
        .string()
        .optional()
        .describe('Comma-separated field names or "*" (default "*")'),
      return_geometry: z
        .boolean()
        .optional()
        .describe("Include geometry in features (default true)"),
      result_record_count: z
        .number()
        .int()
        .min(1)
        .max(2000)
        .optional()
        .describe("Max records to return (default 50, max 2000)"),
      order_by_fields: z
        .string()
        .optional()
        .describe("Optional ORDER BY fields for the query"),
    },
  },
  async ({
    layer_url,
    where,
    out_fields,
    return_geometry,
    result_record_count,
    order_by_fields,
  }) => {
    const data = await queryFeatureLayer(layer_url, {
      where,
      outFields: out_fields,
      returnGeometry: return_geometry,
      resultRecordCount: result_record_count,
      orderByFields: order_by_fields,
    });
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify(data, null, 2),
        },
      ],
    };
  },
);

async function main() {
  const transport = new StdioServerTransport();
  await mcp.connect(transport);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

process.stdin.on("close", () => {
  void mcp.close();
});
