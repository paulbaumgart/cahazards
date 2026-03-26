// raster.ts — Binary grid sampling and value decoding

const CELL_SIZE = 0.1; // degrees per tile

// ---- Float32 grid sampling (bilinear interpolation) ----

export function sampleFloat32Grid(
  tile: { data: Float32Array; rows: number; cols: number; south: number; west: number; nodata: number } | null,
  lat: number,
  lon: number,
): number {
  if (!tile) return NaN;

  const { data, rows, cols, south, west, nodata } = tile;
  const north = south + CELL_SIZE;
  const east = west + CELL_SIZE;

  // Out-of-bounds
  if (lat < south || lat > north || lon < west || lon > east) return NaN;

  // Convert to fractional row/col (row 0 = southernmost)
  const rowF = (lat - south) / (north - south) * (rows - 1);
  const colF = (lon - west) / (east - west) * (cols - 1);

  const r0 = Math.floor(rowF);
  const c0 = Math.floor(colF);
  const r1 = Math.min(r0 + 1, rows - 1);
  const c1 = Math.min(c0 + 1, cols - 1);

  const dr = rowF - r0;
  const dc = colF - c0;

  // Four surrounding values
  const v00 = data[r0 * cols + c0];
  const v01 = data[r0 * cols + c1];
  const v10 = data[r1 * cols + c0];
  const v11 = data[r1 * cols + c1];

  // Collect valid (non-nodata) values with their weights
  const pairs: { v: number; w: number }[] = [];
  const w00 = (1 - dr) * (1 - dc);
  const w01 = (1 - dr) * dc;
  const w10 = dr * (1 - dc);
  const w11 = dr * dc;

  if (v00 !== nodata) pairs.push({ v: v00, w: w00 });
  if (v01 !== nodata) pairs.push({ v: v01, w: w01 });
  if (v10 !== nodata) pairs.push({ v: v10, w: w10 });
  if (v11 !== nodata) pairs.push({ v: v11, w: w11 });

  if (pairs.length === 0) return NaN;

  // Weighted average of valid neighbors
  let sumW = 0;
  let sumV = 0;
  for (const p of pairs) {
    sumW += p.w;
    sumV += p.v * p.w;
  }
  return sumV / sumW;
}

// ---- Elevation (uint16) + slope-from-gradient ----

export interface ElevationResult {
  elevation_m: number;  // meters, NaN if unavailable
  slope_deg: number;    // degrees at parcel point, NaN if unavailable
  nearby_max_slope_deg: number;  // max slope within ~100m, for bluff/cliff proximity
}

/**
 * Sample elevation from a uint16 grid and compute slope from neighboring cells.
 *
 * Encoding: stored_value = (elevation_m + 100) * 10
 *   -> elevation_m = stored_value / 10 - 100
 *   -> range: -100m to 6453m at 0.1m precision
 *   -> nodata: 65535
 *
 * Slope is computed on the fly from the 4 cardinal neighbors using the
 * finite-difference gradient, avoiding the need to store a separate slope grid.
 */
export function sampleElevation(
  tile: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null,
  lat: number,
  lon: number,
): ElevationResult {
  const NODATA_U16 = 65535;
  if (!tile) return { elevation_m: NaN, slope_deg: NaN, nearby_max_slope_deg: NaN };

  const { data, rows, cols, south, west } = tile;
  const north = south + CELL_SIZE;
  const east = west + CELL_SIZE;

  if (lat < south || lat > north || lon < west || lon > east) {
    return { elevation_m: NaN, slope_deg: NaN, nearby_max_slope_deg: NaN };
  }

  // Nearest cell
  const rowF = (lat - south) / (north - south) * (rows - 1);
  const colF = (lon - west) / (east - west) * (cols - 1);
  const r = Math.min(Math.max(Math.round(rowF), 0), rows - 1);
  const c = Math.min(Math.max(Math.round(colF), 0), cols - 1);

  const raw = data[r * cols + c];
  if (raw === NODATA_U16) return { elevation_m: NaN, slope_deg: NaN, nearby_max_slope_deg: NaN };
  const elevation_m = raw / 10 - 100;

  // Compute slope from cardinal neighbors
  const cellSizeY = CELL_SIZE / rows * 111000; // meters per row
  const cellSizeX = CELL_SIZE / cols * 111000 * Math.cos(lat * Math.PI / 180); // meters per col

  const getElev = (row: number, col: number): number => {
    if (row < 0 || row >= rows || col < 0 || col >= cols) return elevation_m;
    const v = data[row * cols + col];
    return v === NODATA_U16 ? elevation_m : v / 10 - 100;
  };

  const dzdx = (getElev(r, c + 1) - getElev(r, c - 1)) / (2 * cellSizeX);
  const dzdy = (getElev(r + 1, c) - getElev(r - 1, c)) / (2 * cellSizeY);
  const slope_deg = Math.atan(Math.sqrt(dzdx * dzdx + dzdy * dzdy)) * 180 / Math.PI;

  // Max slope within ~100m radius (~4 pixels at 25m resolution).
  // Captures bluff/cliff edges adjacent to the parcel — a house on a flat lot
  // at the top of a coastal bluff is at real landslide/erosion risk from the
  // bluff retreating. Checking only the parcel point would miss this.
  const searchR = Math.min(4, Math.floor(rows / 50)); // ~100m
  let maxSlope = slope_deg;
  for (let dr = -searchR; dr <= searchR; dr++) {
    for (let dc = -searchR; dc <= searchR; dc++) {
      if (dr === 0 && dc === 0) continue;
      const rr = r + dr;
      const cc = c + dc;
      if (rr < 1 || rr >= rows - 1 || cc < 1 || cc >= cols - 1) continue;
      const dx = (getElev(rr, cc + 1) - getElev(rr, cc - 1)) / (2 * cellSizeX);
      const dy = (getElev(rr + 1, cc) - getElev(rr - 1, cc)) / (2 * cellSizeY);
      const s = Math.atan(Math.sqrt(dx * dx + dy * dy)) * 180 / Math.PI;
      if (s > maxSlope) maxSlope = s;
    }
  }

  return { elevation_m, slope_deg, nearby_max_slope_deg: maxSlope };
}

// ---- Burn probability (uint16, value / 100000 = annual BP) ----

/**
 * Sample FSim annual burn probability from a uint16 grid.
 * Returns the annual probability (0 to ~0.014), or 0 if no data.
 *
 * If the pixel value is zero (developed/non-burnable in LANDFIRE), searches
 * a ~500m neighborhood for the maximum burn probability. This captures the
 * "fire comes from the wildland" exposure for structures in developed areas
 * adjacent to burnable terrain — e.g., Paradise, where the pixel is classified
 * non-burnable but the Camp Fire arrived from wildland 500m east.
 *
 * Source: USFS Wildfire Risk to Communities, FSim (Scott et al. 2020)
 * Grid values are annual burn probability * 100000.
 */
export function sampleBurnProbability(
  tile: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null,
  lat: number,
  lon: number,
): number {
  if (!tile) return 0;
  const { data, rows, cols, south, west } = tile;
  const north = south + CELL_SIZE;
  const east = west + CELL_SIZE;

  if (lat < south || lat > north || lon < west || lon > east) return 0;

  const r = Math.min(Math.max(Math.round((lat - south) / (north - south) * (rows - 1)), 0), rows - 1);
  const c = Math.min(Math.max(Math.round((lon - west) / (east - west) * (cols - 1)), 0), cols - 1);

  const raw = data[r * cols + c];

  // If non-zero, return the at-point value (structure is in burnable terrain)
  if (raw > 0) return raw / 100000;

  // Zero = developed/non-burnable in LANDFIRE. Compute fire exposure from
  // nearby wildland using a lognormal-weighted kernel.
  //
  // WUI structures ignite from embers (firebrands) transported from adjacent
  // wildland. Spotting distance follows a lognormal distribution (Sardoy et al.
  // 2008, ELMFIRE operational model). Using ELMFIRE default parameters for a
  // high-intensity California WUI fire (10,000 kW/m fireline intensity,
  // 30 km/h wind):
  //
  //   Lognormal: μ=6.63, σ=0.51
  //   Median spotting distance: 754m
  //   Mean: 857m
  //   95th percentile: 1,733m
  //
  // We weight each wildland pixel's BP by the lognormal PDF at its distance
  // from the structure, then take the weighted average. This gives highest
  // weight to wildland ~750m away (the mode) with a long tail to ~2km.
  //
  // Citations:
  //   Sardoy et al. 2008, Combust Flame 154:478-488 (lognormal firebrand dist)
  //   ELMFIRE spotting model (elmfire.io), default parameters
  //   Storey et al. 2020, Fire 3(2):10 (observed: mean 900m, 95th 3.9km)
  //   Lareau et al. 2026, JGR Atmospheres (Camp Fire: 5-10km plume spotting)
  const MU = 6.63;          // lognormal μ (log-meters)
  const SIGMA_LN = 0.51;    // lognormal σ
  const PIXEL_SIZE = 30;    // meters per pixel
  const searchRadius = Math.min(60, Math.floor(rows / 3)); // ~1.8km at 30m

  let weightedSum = 0;
  let weightSum = 0;
  for (let dr = -searchRadius; dr <= searchRadius; dr++) {
    for (let dc = -searchRadius; dc <= searchRadius; dc++) {
      if (dr === 0 && dc === 0) continue;
      const rr = r + dr;
      const cc = c + dc;
      if (rr >= 0 && rr < rows && cc >= 0 && cc < cols) {
        const v = data[rr * cols + cc];
        if (v > 0) {
          const distM = Math.sqrt(dr * dr + dc * dc) * PIXEL_SIZE;
          // Lognormal PDF: f(x) = 1/(x·σ·√(2π)) · exp(-½((ln(x)-μ)/σ)²)
          const lnD = Math.log(distM);
          const z = (lnD - MU) / SIGMA_LN;
          const w = Math.exp(-0.5 * z * z) / distM;
          weightedSum += v * w;
          weightSum += w;
        }
      }
    }
  }

  if (weightSum === 0) return 0;
  return (weightedSum / weightSum) / 100000;
}

/**
 * Sample USFS Conditional Flame Length (CFL) in feet.
 *
 * CFL is the mean headfire flame length if a fire occurs, weighted across
 * 216 weather scenarios (WildEST/FlamMap, Scott et al. 2024).
 *
 * CFL = 0 at developed areas (LANDFIRE "non-burnable"). For building
 * locations, the caller should search nearby pixels for the nearest
 * non-zero value representing the adjacent wildland threat.
 *
 * Source: USFS Wildfire Risk to Communities v2.0
 */
export function sampleCFL(
  tile: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null,
  lat: number,
  lon: number,
): number {
  if (!tile) return 0;
  const { data, rows, cols, south, west } = tile;
  const north = south + CELL_SIZE;
  const east = west + CELL_SIZE;

  if (lat < south || lat > north || lon < west || lon > east) return 0;

  const r = Math.min(Math.max(Math.round((lat - south) / (north - south) * (rows - 1)), 0), rows - 1);
  const c = Math.min(Math.max(Math.round((lon - west) / (east - west) * (cols - 1)), 0), cols - 1);

  const raw = data[r * cols + c];

  // If zero (developed/non-burnable), search a small neighborhood for the
  // nearest wildland CFL — this captures the "across the street from forest" case.
  if (raw === 0) {
    let maxNearby = 0;
    const searchRadius = Math.min(7, Math.floor(rows / 10)); // ~200m at 30m resolution
    for (let dr = -searchRadius; dr <= searchRadius; dr++) {
      for (let dc = -searchRadius; dc <= searchRadius; dc++) {
        const rr = r + dr;
        const cc = c + dc;
        if (rr >= 0 && rr < rows && cc >= 0 && cc < cols) {
          const v = data[rr * cols + cc];
          if (v > maxNearby) maxNearby = v;
        }
      }
    }
    return maxNearby; // feet
  }

  return raw; // feet
}

/**
 * Sample Structure Separation Distance (SSD) in meters.
 *
 * Median SSD per ~250m cell, computed from Microsoft Building Footprints.
 * 0 = no buildings in cell (rural/undeveloped).
 *
 * Source: Microsoft USBuildingFootprints v2
 */
export function sampleSSD(
  tile: { data: Uint16Array; rows: number; cols: number; south: number; west: number } | null,
  lat: number,
  lon: number,
): number {
  if (!tile) return 0;
  const { data, rows, cols, south, west } = tile;
  const north = south + CELL_SIZE;
  const east = west + CELL_SIZE;

  if (lat < south || lat > north || lon < west || lon > east) return 0;

  const r = Math.min(Math.max(Math.round((lat - south) / (north - south) * (rows - 1)), 0), rows - 1);
  const c = Math.min(Math.max(Math.round((lon - west) / (east - west) * (cols - 1)), 0), cols - 1);

  return data[r * cols + c]; // meters
}

/**
 * Sample Mirus et al. 2024 n10 landslide susceptibility (0-81).
 *
 * n10 = number of susceptible 10m cells within each 90m pixel.
 * 0 = non-susceptible, 81 = fully susceptible.
 *
 * Source: Mirus et al. 2024, AGU Advances, doi:10.1029/2024AV001214
 */
export function sampleLandslideSusc(
  tile: { data: Uint8Array; rows: number; cols: number; south: number; west: number } | null,
  lat: number,
  lon: number,
): number {
  if (!tile) return 0;
  const { data, rows, cols, south, west } = tile;
  const north = south + CELL_SIZE;
  const east = west + CELL_SIZE;

  if (lat < south || lat > north || lon < west || lon > east) return 0;

  const r = Math.min(Math.max(Math.round((lat - south) / (north - south) * (rows - 1)), 0), rows - 1);
  const c = Math.min(Math.max(Math.round((lon - west) / (east - west) * (cols - 1)), 0), cols - 1);

  return data[r * cols + c];
}

// ---- Decode helpers ----

/**
 * Decode SLR bit-packed uint8.
 * Bits 0-5 correspond to 1ft, 2ft, 3ft, 4ft, 6ft, 10ft increments.
 */
export function decodeSLR(value: number): {
  "1ft": boolean;
  "2ft": boolean;
  "3ft": boolean;
  "4ft": boolean;
  "6ft": boolean;
  "10ft": boolean;
} {
  return {
    "1ft": (value & 1) !== 0,
    "2ft": (value & 2) !== 0,
    "3ft": (value & 4) !== 0,
    "4ft": (value & 8) !== 0,
    "6ft": (value & 16) !== 0,
    "10ft": (value & 32) !== 0,
  };
}

