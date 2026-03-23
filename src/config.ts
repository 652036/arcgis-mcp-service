/**
 * Optional ArcGIS Online API key or OAuth token, appended as `token` on REST requests when set.
 */
export function getArcgisToken(): string | undefined {
  const t = process.env.ARCGIS_TOKEN?.trim();
  return t || undefined;
}

export function getGeocodeServerUrl(): string {
  const u = process.env.ARCGIS_GEOCODE_URL?.trim();
  if (u) return u.replace(/\/$/, "");
  return "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer";
}
