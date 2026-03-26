// worker.ts — Cloudflare Worker entry point for the California Hazards API

import { fetchAllTiles } from './tiles';
import { computeHazardReport } from './model/hazards';
import { findContainingFeature } from './spatial';
import {
  geocode,
  getCachedGeocode,
  cacheGeocode,
  ensureCacheTable,
  type GeocodingResult,
} from './geocoder';

export interface Env {
  R2_BUCKET: R2Bucket;
  DB: D1Database;
}

// California bounding box
const CA_LAT_MIN = 32.5;
const CA_LAT_MAX = 42.0;
const CA_LON_MIN = -124.5;
const CA_LON_MAX = -114.0;

const CORS_HEADERS: Record<string, string> = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

const CACHE_CONTROL = 'public, max-age=2592000'; // 30 days

let dbInitialized = false;

// Cached lookups (loaded once from R2)
let fairShareCache: Record<string, number> | null = null;
let nriLandslideCache: Record<string, number> | null = null;

/**
 * Load FAIR Plan share by zip code from R2.
 * FAIR Plan share = policies in force / housing units per zip.
 * Cached in memory after first load (~28KB).
 *
 * Source: California FAIR Plan Association + US Census ACS 2022
 */
async function fetchFairShareLookup(bucket: R2Bucket): Promise<Record<string, number> | null> {
  if (fairShareCache) return fairShareCache;
  try {
    const obj = await bucket.get('data/fair_share_by_zip.json');
    if (!obj) return null;
    fairShareCache = await obj.json() as Record<string, number>;
    return fairShareCache;
  } catch {
    return null;
  }
}

/**
 * Load NRI landslide annual loss rates by census tract from R2.
 * AFREQ × HLRB = annual probability of building loss from landslides.
 * Source: FEMA National Risk Index v1.20 (December 2025)
 */
async function fetchNriLandslideLookup(bucket: R2Bucket): Promise<Record<string, number> | null> {
  if (nriLandslideCache) return nriLandslideCache;
  try {
    const obj = await bucket.get('data/nri_landslide_by_tract.json');
    if (!obj) return null;
    nriLandslideCache = await obj.json() as Record<string, number>;
    return nriLandslideCache;
  } catch {
    return null;
  }
}

function jsonResponse(body: unknown, status = 200, cache = false): Response {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...CORS_HEADERS,
  };
  if (cache) {
    headers['Cache-Control'] = CACHE_CONTROL;
  }
  return new Response(JSON.stringify(body), { status, headers });
}

function errorResponse(message: string, status: number): Response {
  return jsonResponse({ error: message }, status);
}

function isInCalifornia(lat: number, lon: number): boolean {
  return lat >= CA_LAT_MIN && lat <= CA_LAT_MAX && lon >= CA_LON_MIN && lon <= CA_LON_MAX;
}

async function ensureDb(db: D1Database): Promise<void> {
  if (!dbInitialized) {
    await ensureCacheTable(db);
    dbInitialized = true;
  }
}

/**
 * Parse address, lat, and lon from either a JSON body (POST) or query params (GET).
 */
async function parseInput(
  request: Request,
  url: URL,
): Promise<{ address?: string; lat?: number; lon?: number; retrofitted?: boolean }> {
  if (request.method === 'POST') {
    const body = (await request.json()) as Record<string, unknown>;
    return {
      address: typeof body.address === 'string' ? body.address : undefined,
      lat: typeof body.lat === 'number' ? body.lat : undefined,
      lon: typeof body.lon === 'number' ? body.lon : undefined,
      retrofitted: body.retrofitted === true,
    };
  }

  // GET — read from query params
  const address = url.searchParams.get('address') ?? undefined;
  const latStr = url.searchParams.get('lat');
  const lonStr = url.searchParams.get('lon');
  return {
    address,
    lat: latStr != null ? parseFloat(latStr) : undefined,
    lon: lonStr != null ? parseFloat(lonStr) : undefined,
    retrofitted: url.searchParams.get('retrofitted') === 'true',
  };
}

/**
 * Main report handler — shared between POST and GET /api/report.
 */
async function handleReport(request: Request, url: URL, env: Env): Promise<Response> {
  // 1. Parse input
  let input: { address?: string; lat?: number; lon?: number; retrofitted?: boolean };
  try {
    input = await parseInput(request, url);
  } catch {
    return errorResponse('Invalid request body', 400);
  }

  // 2. Validate
  if (!input.address && (input.lat == null || input.lon == null)) {
    return errorResponse('Missing required field: address', 400);
  }

  const address = input.address ?? `${input.lat},${input.lon}`;

  // 3. Geocode (with D1 cache)
  await ensureDb(env.DB);

  let geo: GeocodingResult | null = null;

  if (input.lat != null && input.lon != null) {
    // Coordinates already provided
    geo = { lat: input.lat, lon: input.lon, address };
  } else {
    // Check D1 cache first
    geo = await getCachedGeocode(env.DB, address);

    if (!geo) {
      // Call external geocoder
      geo = await geocode(address);

      if (geo) {
        // Cache for future use (fire-and-forget is fine here, but we await to be safe)
        await cacheGeocode(env.DB, address, geo);
      }
    }
  }

  if (!geo) {
    return errorResponse('Could not geocode address', 404);
  }

  // 4. Validate coordinates are in California
  if (!isInCalifornia(geo.lat, geo.lon)) {
    return errorResponse('Address is outside California', 400);
  }

  // 5. Fetch tiles and lookup data in parallel
  const [tiles, fairShareLookup, nriLandslideLookup] = await Promise.all([
    fetchAllTiles(geo.lat, geo.lon, env.R2_BUCKET),
    fetchFairShareLookup(env.R2_BUCKET),
    fetchNriLandslideLookup(env.R2_BUCKET),
  ]);

  // 6. Look up FAIR Plan share and NRI landslide rate
  const zipMatch = geo.address.match(/\b(\d{5})\b/);
  const zipCode = zipMatch ? zipMatch[1] : null;
  const fairShare = zipCode && fairShareLookup ? (fairShareLookup[zipCode] ?? 0) : 0;

  // Census tract: look up from tiled tract polygons instead of Census geocoder.
  // This eliminates the dependency on Census API returning the tract FIPS.
  const tractFeature = findContainingFeature(geo.lat, geo.lon, tiles.census_tracts);
  const tractFips = tractFeature
    ? (String(tractFeature.properties?.['GEOID'] ?? '') || null)
    : (geo.censusTract ?? null);  // fallback to Census geocoder if tile misses
  const nriLandslideRate = tractFips && nriLandslideLookup ? (nriLandslideLookup[tractFips] ?? 0) : 0;

  // 7. Run hazard model — fetches NSHMP hazard curve internally (uses site Vs30)
  //    NSHMP curve is fetched once and shared between retrofit states
  const report = computeHazardReport(geo.lat, geo.lon, tiles, { retrofitted: false, fairShare, nriLandslideRate });
  const reportRetrofitted = computeHazardReport(geo.lat, geo.lon, tiles, { retrofitted: true, fairShare, nriLandslideRate });

  // 7. Return both models so the frontend can toggle instantly
  const response = {
    address: geo.address,
    census_tract: tractFips,
    ...report,
    retrofitted_structural: reportRetrofitted.structural,
  };

  return jsonResponse(response, 200, true);
}

// ── Markdown → HTML for doc pages ──

function inlineFormat(text: string): string {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function renderMarkdown(md: string): string {
  const lines = md.split('\n');
  let html = '';
  let inList = false;
  let inTable = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Table separator rows
    if (/^\s*\|[\s\-:|]+\|\s*$/.test(line)) continue;

    // Table rows
    if (/^\s*\|/.test(line)) {
      const cells = line.split('|').filter(c => c.trim() !== '');
      if (!inTable) { html += '<table>'; inTable = true; }
      const nextLine = lines[i + 1] || '';
      const tag = /^\s*\|[\s\-:|]+\|\s*$/.test(nextLine) ? 'th' : 'td';
      html += '<tr>' + cells.map(c => `<${tag}>${inlineFormat(c.trim())}</${tag}>`).join('') + '</tr>';
      continue;
    }
    if (inTable) { html += '</table>'; inTable = false; }

    // Headings
    const hMatch = line.match(/^(#{1,4})\s+(.+)/);
    if (hMatch) {
      if (inList) { html += '</ul>'; inList = false; }
      const level = hMatch[1].length;
      html += `<h${level}>${inlineFormat(hMatch[2])}</h${level}>`;
      continue;
    }

    // List items
    if (/^\s*[-*]\s/.test(line)) {
      if (!inList) { html += '<ul>'; inList = true; }
      html += `<li>${inlineFormat(line.replace(/^\s*[-*]\s+/, ''))}</li>`;
      continue;
    }
    if (inList) { html += '</ul>'; inList = false; }

    if (line.trim() === '') continue;
    html += `<p>${inlineFormat(line)}</p>`;
  }

  if (inList) html += '</ul>';
  if (inTable) html += '</table>';
  return html;
}

function renderDocPage(slug: string, md: string): string {
  // Extract title from first heading
  const titleMatch = md.match(/^#\s+(.+)/m);
  const title = titleMatch ? titleMatch[1] : slug.replace(/-/g, ' ');

  const body = renderMarkdown(md);

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${title} — California Hazards</title>
<meta name="description" content="${title}. Methodology documentation for the California Hazards property risk report.">
<link rel="canonical" href="https://cahazards.com/docs/${slug}">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cpolygon points='50,8 95,88 5,88' fill='%23e67e22' stroke='%23000' stroke-width='4'/%3E%3Ctext x='50' y='78' text-anchor='middle' font-size='50' font-weight='bold' font-family='Arial' fill='%23000'%3E!%3C/text%3E%3C/svg%3E">
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root { --bg: #0a0c10; --surface: #12151c; --border: #252a36; --text: #c8cdd8; --text-dim: #6b7280; --text-bright: #e8ecf4; --accent-cyan: #00d2d3; }
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'Source Sans 3', sans-serif; line-height: 1.7; }
.doc-page { max-width: 720px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
.doc-back { display: inline-block; font-family: 'IBM Plex Mono', monospace; font-size: 0.78rem; color: var(--accent-cyan); text-decoration: none; margin-bottom: 2rem; }
.doc-back:hover { text-decoration: underline; }
h1 { font-family: 'DM Serif Display', serif; font-size: 2rem; color: var(--text-bright); margin-bottom: 1.5rem; }
h2 { font-family: 'DM Serif Display', serif; font-size: 1.3rem; color: var(--text-bright); margin-top: 2rem; margin-bottom: 0.75rem; }
h3 { font-size: 1.05rem; font-weight: 600; color: var(--text-bright); margin-top: 1.5rem; margin-bottom: 0.5rem; }
p { margin-bottom: 0.8rem; }
a { color: var(--accent-cyan); text-decoration: none; }
a:hover { text-decoration: underline; }
strong { color: var(--text-bright); }
code { font-family: 'IBM Plex Mono', monospace; font-size: 0.88em; background: var(--surface); padding: 2px 5px; border-radius: 3px; }
ul { margin: 0.5rem 0 1rem 1.5rem; }
li { margin-bottom: 0.3rem; }
table { width: 100%; border-collapse: collapse; margin: 0.8rem 0; font-size: 0.9rem; }
th, td { border: 1px solid var(--border); padding: 0.5rem 0.7rem; text-align: left; }
th { background: rgba(255,255,255,0.04); font-weight: 600; color: var(--text-bright); }
</style>
</head>
<body>
<div class="doc-page">
<a href="/" class="doc-back">&larr; California Hazards</a>
${body}
</div>
</body>
</html>`;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    try {
      // Route: health check
      if (url.pathname === '/health') {
        return jsonResponse({ ok: true });
      }

      // Route: hazard report
      if (url.pathname === '/api/report') {
        if (request.method === 'POST' || request.method === 'GET') {
          return await handleReport(request, url, env);
        }
        return errorResponse('Method not allowed', 405);
      }

      // Route: address autocomplete
      if (url.pathname === '/api/autocomplete') {
        const q = url.searchParams.get('q')?.trim();
        if (!q || q.length < 2) {
          return jsonResponse([]);
        }
        // Simple prefix search using B-tree index (sub-ms on 16M rows).
        // LIKE 'query%' with COLLATE NOCASE uses the index efficiently.
        try {
          const results = await env.DB.prepare(
            `SELECT display, lat, lon FROM addresses
             WHERE address LIKE ? COLLATE NOCASE`
          ).bind(q + '%').all();
          return jsonResponse(results.results ?? []);
        } catch {
          return jsonResponse([]);
        }
      }

      // Route: Census geocoder proxy (avoids CORS issues)
      if (url.pathname === '/api/geocode') {
        const address = url.searchParams.get('address')?.trim();
        if (!address) return errorResponse('Missing address', 400);
        try {
          const censusUrl = `https://geocoding.geo.census.gov/geocoder/addresses/onelineaddress?address=${encodeURIComponent(address)}&benchmark=Public_AR_Current&format=json`;
          const resp = await fetch(censusUrl);
          const data = await resp.json() as Record<string, unknown>;
          return jsonResponse(data, 200, true);
        } catch {
          return errorResponse('Census geocoder unavailable', 502);
        }
      }

      // Serve model documentation as rendered HTML page
      if (url.pathname.startsWith('/docs/')) {
        // Support both /docs/earthquake-model and /docs/earthquake-model.md
        const slug = url.pathname.slice(6).replace(/\.md$/, '').replace(/\/$/, '');
        const doc = await env.R2_BUCKET.get(`docs/${slug}.md`);
        if (doc) {
          const md = await doc.text();
          const html = renderDocPage(slug, md);
          return new Response(html, {
            headers: {
              'Content-Type': 'text/html; charset=utf-8',
              'Cache-Control': 'public, max-age=3600',
              ...CORS_HEADERS,
            },
          });
        }
      }

      // Serve static frontend assets from R2 (uploaded with frontend/ prefix)
      const assetPath = url.pathname === '/' ? 'frontend/index.html' : `frontend${url.pathname}`;
      const asset = await env.R2_BUCKET.get(assetPath);
      if (asset) {
        const contentTypes: Record<string, string> = {
          '.html': 'text/html; charset=utf-8',
          '.js': 'application/javascript; charset=utf-8',
          '.css': 'text/css; charset=utf-8',
          '.json': 'application/json',
          '.png': 'image/png',
          '.svg': 'image/svg+xml',
        };
        const ext = assetPath.substring(assetPath.lastIndexOf('.'));
        return new Response(asset.body, {
          headers: {
            'Content-Type': contentTypes[ext] || 'application/octet-stream',
            'Cache-Control': 'public, max-age=3600',
            ...CORS_HEADERS,
          },
        });
      }

      return errorResponse('Not found', 404);
    } catch (err) {
      console.error('Internal error:', err);
      return errorResponse('Internal error', 500);
    }
  },
};
