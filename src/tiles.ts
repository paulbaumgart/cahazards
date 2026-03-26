// tiles.ts — Parallel R2 tile fetching for spatial hazard data

// ---- GeoJSON types (minimal, no external deps) ----
namespace GeoJSON {
  export interface Geometry {
    type: string;
    coordinates: unknown;
  }
  export interface Feature {
    type: "Feature";
    geometry: Geometry;
    properties: Record<string, unknown> | null;
  }
  export interface FeatureCollection {
    type: "FeatureCollection";
    features: Feature[];
  }
}

// ---- Tile data shape ----

export interface RasterContinuousTile {
  data: Float32Array;
  rows: number;
  cols: number;
  south: number;
  west: number;
  nodata: number;
}

export interface TileData {
  // Raster tiles (continuous data only — no polygon zone lookups)
  elevation: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null;
  burn_probability: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null;
  cfl: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null;
  ssd: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null;
  landslide_susc: { data: Uint8Array; rows: number; cols: number; south: number; west: number } | null;
  insar_velocity: RasterContinuousTile | null;  // OPERA-DISP mean velocity (mm/yr), float32
  fire_risk: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null;
  vs30: RasterContinuousTile | null;

  // Vector tiles (GeoJSON FeatureCollections — all polygon/point data)
  faults: GeoJSON.FeatureCollection | null;
  flood_zones: GeoJSON.FeatureCollection | null;
  tsunami: GeoJSON.FeatureCollection | null;
  landslide: GeoJSON.FeatureCollection | null;
  liquefaction: GeoJSON.FeatureCollection | null;
  fire_zones: GeoJSON.FeatureCollection | null;
  soils: GeoJSON.FeatureCollection | null;
  slr: GeoJSON.FeatureCollection | null;
  calenviroscreen: GeoJSON.FeatureCollection | null;
  dam_inundation: GeoJSON.FeatureCollection | null;
  airports: GeoJSON.FeatureCollection | null;
  traffic: GeoJSON.FeatureCollection | null;
  erosion: GeoJSON.FeatureCollection | null;
  contamination: GeoJSON.FeatureCollection | null;
  landslide_inventory: GeoJSON.FeatureCollection | null;
  landslide_supplemental: GeoJSON.FeatureCollection | null;
  census_tracts: GeoJSON.FeatureCollection | null;

}

// ---- Tile key helpers ----

function tileKey01(lat: number, lon: number): string {
  const tLat = Math.floor(lat * 10) / 10;
  const tLon = Math.floor(lon * 10) / 10;
  return `${tLat.toFixed(1)}_${tLon.toFixed(1)}`;
}

/** Return 3x3 grid of 0.5-degree tile keys to guarantee 50km fault coverage */
function faultTileKeys(lat: number, lon: number): string[] {
  const baseLat = Math.floor(lat * 2) / 2;
  const baseLon = Math.floor(lon * 2) / 2;
  const keys: string[] = [];
  for (let dLat = -0.5; dLat <= 0.5; dLat += 0.5) {
    for (let dLon = -0.5; dLon <= 0.5; dLon += 0.5) {
      keys.push(`${(baseLat + dLat).toFixed(1)}_${(baseLon + dLon).toFixed(1)}`);
    }
  }
  return keys;
}

function tileKey05(lat: number, lon: number): string {
  const tLat = Math.floor(lat * 2) / 2;
  const tLon = Math.floor(lon * 2) / 2;
  return `${tLat.toFixed(1)}_${tLon.toFixed(1)}`;
}

// ---- Parsers ----

interface Float32Sidecar {
  rows: number;
  cols: number;
  bounds: { north: number; south: number; east: number; west: number };
  nodata: number;
  dtype: string;
}

/** Parse a float32 continuous tile given the sidecar metadata and binary buffer. */
function parseFloat32Tile(sidecar: Float32Sidecar, bin: ArrayBuffer): RasterContinuousTile {
  const data = new Float32Array(bin);
  return {
    data,
    rows: sidecar.rows,
    cols: sidecar.cols,
    south: sidecar.bounds.south,
    west: sidecar.bounds.west,
    nodata: sidecar.nodata,
  };
}

/** Parse vs30 tile with its unique 24-byte header (uint32 rows, uint32 cols, float64 south, float64 west). */
function parseVs30Tile(buf: ArrayBuffer): RasterContinuousTile {
  const view = new DataView(buf);
  const rows = view.getUint32(0, true);
  const cols = view.getUint32(4, true);
  const south = view.getFloat64(8, true);
  const west = view.getFloat64(16, true);
  const data = new Float32Array(buf, 24);
  return { data, rows, cols, south, west, nodata: -9999 };
}

// ---- Fetch helpers ----

async function fetchBinary(bucket: R2Bucket, key: string): Promise<ArrayBuffer | null> {
  const obj = await bucket.get(key);
  if (!obj) return null;
  return obj.arrayBuffer();
}

async function fetchJson<T>(bucket: R2Bucket, key: string): Promise<T | null> {
  const obj = await bucket.get(key);
  if (!obj) return null;
  return obj.json() as Promise<T>;
}

// ---- Main entry point ----

// All polygon/zone data is vector (GeoJSON) for exact boundary precision.
// Only continuous data (elevation, burn probability, Vs30) is raster.
const VECTOR_01 = ["calenviroscreen", "dam_inundation", "traffic", "erosion",
  "flood_zones", "tsunami", "landslide", "liquefaction", "fire_zones",
  "soils", "slr", "landslide_inventory", "landslide_supplemental", "census_tracts"] as const;
const VECTOR_05 = ["airports", "contamination"] as const;

export async function fetchAllTiles(lat: number, lon: number, bucket: R2Bucket): Promise<TileData> {
  const key01 = tileKey01(lat, lon);
  const key05 = tileKey05(lat, lon);

  type FetchJob =
    | { kind: "raster_pair"; layer: string }  // uint16 bin + json sidecar
    | { kind: "raster_u8"; layer: string }    // uint8 bin + json sidecar
    | { kind: "vs30"; layer?: string }          // float32 with 24-byte header
    | { kind: "vector"; layer: string };       // GeoJSON

  const jobs: FetchJob[] = [];
  const promises: Promise<ArrayBuffer | null | unknown>[] = [];

  // ── Raster tiles (continuous data only) ──

  // Elevation: uint16 bin + JSON sidecar
  jobs.push({ kind: "raster_pair", layer: "elevation" });
  promises.push(fetchBinary(bucket, `tiles/elevation/${key01}.bin`));
  promises.push(fetchJson<Float32Sidecar>(bucket, `tiles/elevation/${key01}.json`));

  // FSim burn probability: uint16 bin + JSON sidecar
  jobs.push({ kind: "raster_pair", layer: "burn_probability" });
  promises.push(fetchBinary(bucket, `tiles/burn_probability/${key01}.bin`));
  promises.push(fetchJson<Float32Sidecar>(bucket, `tiles/burn_probability/${key01}.json`));

  // USFS Conditional Flame Length: uint16 bin + JSON sidecar (feet)
  jobs.push({ kind: "raster_pair", layer: "cfl" });
  promises.push(fetchBinary(bucket, `tiles/cfl/${key01}.bin`));
  promises.push(fetchJson<Float32Sidecar>(bucket, `tiles/cfl/${key01}.json`));

  // Structure Separation Distance: uint16 bin + JSON sidecar (meters)
  jobs.push({ kind: "raster_pair", layer: "ssd" });
  promises.push(fetchBinary(bucket, `tiles/ssd/${key01}.bin`));
  promises.push(fetchJson<Float32Sidecar>(bucket, `tiles/ssd/${key01}.json`));

  // Landslide susceptibility: uint8 bin + JSON sidecar (n10, 0-81)
  jobs.push({ kind: "raster_u8", layer: "landslide_susc" });
  promises.push(fetchBinary(bucket, `tiles/landslide_susc/${key01}.bin`));
  promises.push(fetchJson<Float32Sidecar>(bucket, `tiles/landslide_susc/${key01}.json`));

  // Fire risk: uint16 bin + JSON sidecar (P(damage,30yr) × 10000)
  jobs.push({ kind: "raster_pair", layer: "fire_risk" });
  promises.push(fetchBinary(bucket, `tiles/fire_risk/${key01}.bin`));
  promises.push(fetchJson<Float32Sidecar>(bucket, `tiles/fire_risk/${key01}.json`));

  // InSAR velocity: float32 with 24-byte header (mm/yr, OPERA-DISP Sentinel-1)
  jobs.push({ kind: "vs30", layer: "insar_velocity" });  // same format as vs30
  promises.push(fetchBinary(bucket, `tiles/insar_velocity/${key01}.bin`));

  // Vs30: unique 24-byte header format
  jobs.push({ kind: "vs30" });
  promises.push(fetchBinary(bucket, `tiles/vs30/${key01}.bin`));

  // ── Vector tiles (all polygon/point data) ──

  // 0.1-degree tiles
  for (const layer of VECTOR_01) {
    jobs.push({ kind: "vector", layer });
    promises.push(fetchJson<GeoJSON.FeatureCollection>(bucket, `tiles/${layer}/${key01}.json`));
  }

  // 0.5-degree tiles
  for (const layer of VECTOR_05) {
    jobs.push({ kind: "vector", layer });
    promises.push(fetchJson<GeoJSON.FeatureCollection>(bucket, `tiles/${layer}/${key05}.json`));
  }

  // Faults: 3x3 grid of 0.5-degree tiles for 50km coverage
  for (const fk of faultTileKeys(lat, lon)) {
    jobs.push({ kind: "vector", layer: "_fault_" + fk });
    promises.push(fetchJson<GeoJSON.FeatureCollection>(bucket, `tiles/faults/${fk}.json`));
  }

  const results = await Promise.all(promises);

  // ── Assemble TileData ──

  const tileData: TileData = {
    elevation: null, burn_probability: null, cfl: null, ssd: null, landslide_susc: null, insar_velocity: null, fire_risk: null, vs30: null,
    faults: null, flood_zones: null, tsunami: null,
    landslide: null, liquefaction: null, fire_zones: null,
    soils: null, slr: null, calenviroscreen: null,
    dam_inundation: null, airports: null, traffic: null,
    erosion: null, contamination: null, landslide_inventory: null, landslide_supplemental: null, census_tracts: null,
  };

  let ri = 0; // result index
  for (const job of jobs) {
    switch (job.kind) {
      case "raster_pair": {
        const bin = results[ri++] as ArrayBuffer | null;
        const sidecar = results[ri++] as Float32Sidecar | null;
        if (bin && sidecar) {
          (tileData as any)[job.layer] = {
            data: new Uint16Array(bin),
            rows: sidecar.rows,
            cols: sidecar.cols,
            south: sidecar.bounds.south,
            west: sidecar.bounds.west,
          };
        }
        break;
      }
      case "raster_u8": {
        const bin8 = results[ri++] as ArrayBuffer | null;
        const sidecar8 = results[ri++] as Float32Sidecar | null;
        if (bin8 && sidecar8) {
          (tileData as any)[job.layer] = {
            data: new Uint8Array(bin8),
            rows: sidecar8.rows,
            cols: sidecar8.cols,
            south: sidecar8.bounds.south,
            west: sidecar8.bounds.west,
          };
        }
        break;
      }
      case "vs30": {
        const buf = results[ri++] as ArrayBuffer | null;
        if (buf) {
          const parsed = parseVs30Tile(buf);
          if (parsed) (tileData as any)[job.layer ?? "vs30"] = parsed;
        }
        break;
      }
      case "vector": {
        const fc = results[ri++] as GeoJSON.FeatureCollection | null;
        if (fc && job.layer.startsWith("_fault_")) {
          if (!tileData.faults) tileData.faults = { type: "FeatureCollection", features: [] };
          tileData.faults.features.push(...fc.features);
        } else if (fc) {
          (tileData as any)[job.layer] = fc;
        }
        break;
      }
    }
  }

  return tileData;
}
