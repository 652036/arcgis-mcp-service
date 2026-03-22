import { getArcgisToken, getGeocodeServerUrl } from "./config.js";

function withToken(params: URLSearchParams): URLSearchParams {
  const token = getArcgisToken();
  if (token && !params.has("token")) params.set("token", token);
  return params;
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  const text = await res.text();
  let data: unknown;
  try {
    data = JSON.parse(text) as unknown;
  } catch {
    throw new Error(`Invalid JSON (${res.status}): ${text.slice(0, 500)}`);
  }
  if (!res.ok) {
    const err = data as { error?: { message?: string } };
    const msg = err?.error?.message ?? `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data as T;
}

export type GeocodeCandidate = {
  address?: string;
  location?: { x: number; y: number };
  score?: number;
  attributes?: Record<string, unknown>;
};

export async function findAddressCandidates(singleLine: string, maxLocations = 5) {
  const base = getGeocodeServerUrl();
  const params = withToken(
    new URLSearchParams({
      f: "json",
      SingleLine: singleLine,
      maxLocations: String(Math.min(Math.max(maxLocations, 1), 20)),
    }),
  );
  const url = `${base}/findAddressCandidates?${params}`;
  return fetchJson<{
    candidates?: GeocodeCandidate[];
    error?: { message?: string };
  }>(url);
}

export async function reverseGeocode(longitude: number, latitude: number) {
  const base = getGeocodeServerUrl();
  const params = withToken(
    new URLSearchParams({
      f: "json",
      location: `${longitude},${latitude}`,
    }),
  );
  const url = `${base}/reverseGeocode?${params}`;
  return fetchJson<{
    address?: { Match_addr?: string; LongLabel?: string };
    location?: { x: number; y: number };
    error?: { message?: string };
  }>(url);
}

export async function suggestAddress(text: string, maxSuggestions = 5) {
  const base = getGeocodeServerUrl();
  const params = withToken(
    new URLSearchParams({
      f: "json",
      text,
      maxSuggestions: String(Math.min(Math.max(maxSuggestions, 1), 15)),
    }),
  );
  const url = `${base}/suggest?${params}`;
  return fetchJson<{
    suggestions?: { text?: string; magicKey?: string }[];
    error?: { message?: string };
  }>(url);
}

function normalizeLayerUrl(layerUrl: string): string {
  const u = layerUrl.trim().replace(/\/$/, "");
  if (!u.startsWith("http://") && !u.startsWith("https://")) {
    throw new Error("layer_url must be an http(s) URL");
  }
  return u;
}

export async function getLayerMetadata(layerUrl: string) {
  const u = normalizeLayerUrl(layerUrl);
  const params = withToken(new URLSearchParams({ f: "json" }));
  const url = `${u}?${params}`;
  return fetchJson<Record<string, unknown>>(url);
}

export async function queryFeatureLayer(
  layerUrl: string,
  options: {
    where?: string;
    outFields?: string;
    returnGeometry?: boolean;
    resultRecordCount?: number;
    orderByFields?: string;
  },
) {
  const u = normalizeLayerUrl(layerUrl);
  const where = options.where?.trim() || "1=1";
  const outFields = options.outFields?.trim() || "*";
  const returnGeometry = options.returnGeometry !== false;
  const resultRecordCount = Math.min(
    Math.max(options.resultRecordCount ?? 50, 1),
    2000,
  );
  const params = withToken(
    new URLSearchParams({
      f: "json",
      where,
      outFields,
      returnGeometry: String(returnGeometry),
      resultRecordCount: String(resultRecordCount),
    }),
  );
  if (options.orderByFields?.trim()) {
    params.set("orderByFields", options.orderByFields.trim());
  }
  const url = `${u}/query?${params}`;
  return fetchJson<Record<string, unknown>>(url);
}
