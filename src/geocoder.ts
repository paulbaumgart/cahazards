// geocoder.ts — Geocoding via Census Bureau + Nominatim fallback, with D1 caching

export interface GeocodingResult {
  lat: number;
  lon: number;
  address: string;
  censusTract?: string; // GEOID from Census geocoder
}

/**
 * Normalize an address for use as a cache key.
 * Lowercases and collapses whitespace.
 */
function normalizeAddress(address: string): string {
  return address.toLowerCase().replace(/\s+/g, ' ').trim();
}

/**
 * Try the Census Bureau geocoder.
 * Returns a GeocodingResult or null if no match.
 */
async function tryCensus(address: string): Promise<GeocodingResult | null> {
  const url =
    'https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress' +
    `?address=${encodeURIComponent(address)}` +
    '&benchmark=Public_AR_Current&vintage=Current_Current&format=json';

  const resp = await fetch(url);
  if (!resp.ok) return null;

  const data: any = await resp.json();
  const matches = data?.result?.addressMatches;
  if (!matches || matches.length === 0) return null;

  const match = matches[0];
  const lat = match.coordinates?.y;
  const lon = match.coordinates?.x;
  if (lat == null || lon == null) return null;

  let censusTract: string | undefined;
  try {
    censusTract = match.geographies?.['Census Tracts']?.[0]?.GEOID;
  } catch {
    // Census tract extraction is best-effort
  }

  return {
    lat: Number(lat),
    lon: Number(lon),
    address: match.matchedAddress || address,
    censusTract,
  };
}

/**
 * Try the Nominatim geocoder (OpenStreetMap).
 * Returns a GeocodingResult or null if no match.
 */
async function tryNominatim(address: string): Promise<GeocodingResult | null> {
  const url =
    'https://nominatim.openstreetmap.org/search' +
    `?q=${encodeURIComponent(address)}` +
    '&format=json&countrycodes=us&limit=1';

  const resp = await fetch(url, {
    headers: { 'User-Agent': 'cahazards/1.0' },
  });
  if (!resp.ok) return null;

  const data: any = await resp.json();
  if (!Array.isArray(data) || data.length === 0) return null;

  const hit = data[0];
  const lat = parseFloat(hit.lat);
  const lon = parseFloat(hit.lon);
  if (isNaN(lat) || isNaN(lon)) return null;

  return {
    lat,
    lon,
    address: hit.display_name || address,
  };
}

/**
 * Geocode an address. Tries Census Bureau first, then Nominatim.
 * If lat/lon are already provided, returns them immediately.
 */
export async function geocode(
  address: string,
  lat?: number,
  lon?: number,
): Promise<GeocodingResult | null> {
  // If coordinates already supplied, skip geocoding
  if (lat != null && lon != null) {
    return { lat, lon, address };
  }

  // Try Census Bureau first
  const censusResult = await tryCensus(address);
  if (censusResult) return censusResult;

  // Fall back to Nominatim
  const nominatimResult = await tryNominatim(address);
  if (nominatimResult) return nominatimResult;

  return null;
}

// ---------------------------------------------------------------------------
// D1 caching layer
// ---------------------------------------------------------------------------

const ENSURE_TABLE_SQL = `
CREATE TABLE IF NOT EXISTS geocode_cache (
  address_normalized TEXT PRIMARY KEY,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  census_tract TEXT,
  created_at INTEGER NOT NULL
)`;

/**
 * Ensure the geocode_cache table exists. Call once at startup or lazily.
 */
export async function ensureCacheTable(db: D1Database): Promise<void> {
  await db.prepare(ENSURE_TABLE_SQL).run();
}

/**
 * Check D1 cache for a previously geocoded address.
 */
export async function getCachedGeocode(
  db: D1Database,
  address: string,
): Promise<GeocodingResult | null> {
  const key = normalizeAddress(address);
  const row = await db
    .prepare('SELECT lat, lon, address_normalized, census_tract FROM geocode_cache WHERE address_normalized = ?')
    .bind(key)
    .first<{ lat: number; lon: number; address_normalized: string; census_tract: string | null }>();

  if (!row) return null;

  return {
    lat: row.lat,
    lon: row.lon,
    address: address,
    censusTract: row.census_tract ?? undefined,
  };
}

/**
 * Store a geocoding result in D1 cache.
 */
export async function cacheGeocode(
  db: D1Database,
  address: string,
  result: GeocodingResult,
): Promise<void> {
  const key = normalizeAddress(address);
  await db
    .prepare(
      'INSERT OR REPLACE INTO geocode_cache (address_normalized, lat, lon, census_tract, created_at) VALUES (?, ?, ?, ?, ?)',
    )
    .bind(key, result.lat, result.lon, result.censusTract ?? null, Math.floor(Date.now() / 1000))
    .run();
}
