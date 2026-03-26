// hazards.ts — All hazard models for the cahazards worker.
// Each model is a pure function of site parameters + tile data.

import {
  sampleFloat32Grid,
  sampleElevation,
  sampleBurnProbability,
  sampleCFL,
  sampleSSD,
  sampleLandslideSusc,
  decodeSLR,
} from '../raster';
import type { TileData } from '../tiles';
import {
  distanceKm,
  distanceToLine,
  distanceToNearestPolygonBoundary,
  findNearest,
  findWithinRadius,
  findContainingFeature,
  pointInPolygon,
} from '../spatial';

// ---- Return type ----

export interface HazardReport {
  coordinates: { lat: number; lon: number };
  elevation_m: number;
  slope_deg: number;
  vs30: number;

  faults: Array<{
    name: string;
    distance_km: number;
    type: string;
    slip_rate_mm_yr: number | null;
    ucerf3_prob: number | null;
    expected_mmi: number;
  }>;

  zones: {
    liquefaction: boolean;
    landslide: boolean;
    fema_flood: string;
    fire_hazard: string;
    tsunami_inundation: boolean;
    dam_inundation: { in_zone: boolean; dam_names: string[] };
    expansive_soil: string;
  };

  sea_level_rise: {
    lowest_threshold_ft: number | null;
    inundated_at: Record<string, boolean>;
  };

  structural: {
    earthquake: { annual_p: number; p30yr: number };
    wildfire: { annual_p: number; p30yr: number; wui_underestimate: boolean };
    flood: { annual_p: number; p30yr: number };
    tsunami: { annual_p: number; p30yr: number };
    erosion: { annual_p: number; p30yr: number; years_to_threat: number | null };
    landslide: { annual_p: number; p30yr: number; tier: 1 | 2 | 3 | 4 };
    dam_inundation: { annual_p: number; p30yr: number };
    combined_30yr: number;
  };

  calenviroscreen: {
    overall_percentile: number | null;
    tract: string | null;
    pm25_pctl: number | null;
    diesel_pm_pctl: number | null;
    traffic_pctl: number | null;
    poverty_pctl: number | null;
  } | null;

  aviation_lead: {
    nearest_airport: { code: string; name: string; distance_km: number; piston_ops: number } | null;
    risk_level: string;
  };

  traffic_pollution: {
    nearest_major_road: { distance_m: number; aadt: number } | null;
    risk_level: string;
  };

  contamination: {
    sites_within_500m: Array<{ name: string; type: string; status: string; distance_m: number }>;
    sites_within_1km: Array<{ name: string; type: string; status: string; distance_m: number }>;
  };
}

// ---- USGS NSHMP hazard curve integration ----

/**
 * Supported Vs30 values for the USGS NSHMP hazard API.
 * The API only accepts these specific values.
 */
const NSHMP_VS30_VALUES = [180, 259, 360, 537, 760, 1150, 2000];

function nearestSupportedVs30(vs30: number): number {
  let best = NSHMP_VS30_VALUES[0];
  let bestDist = Math.abs(vs30 - best);
  for (const v of NSHMP_VS30_VALUES) {
    const d = Math.abs(vs30 - v);
    if (d < bestDist) { best = v; bestDist = d; }
  }
  return best;
}

interface NSHMPHazardCurve {
  /** PGA values in g */
  xvalues: number[];
  /** Annual probability of exceedance for each PGA level */
  yvalues: number[];
}

/**
 * Fetch the USGS NSHMP hazard curve for a site.
 *
 * Returns the total hazard curve: annual P(PGA > x) for 20 PGA levels.
 * This integrates all UCERF3 fault sources, background seismicity,
 * and site amplification — replacing our fault-by-fault summation.
 *
 * Source: USGS National Seismic Hazard Model, 2014 edition
 * https://earthquake.usgs.gov/nshmp-haz-ws/
 *
 * Adds ~1s latency per request.
 */
export async function fetchNSHMPHazardCurve(lat: number, lon: number, vs30: number): Promise<NSHMPHazardCurve | null> {
  const supportedVs30 = nearestSupportedVs30(vs30);
  const url = `https://earthquake.usgs.gov/nshmp-haz-ws/hazard`
    + `?edition=E2014&region=COUS`
    + `&longitude=${lon.toFixed(4)}&latitude=${lat.toFixed(4)}`
    + `&imt=PGA&vs30=${supportedVs30}`;

  try {
    const resp = await fetch(url, {
      headers: { 'User-Agent': 'cahazards/1.0' },
      signal: AbortSignal.timeout(8000), // 8s timeout
    });
    if (!resp.ok) return null;

    const data: any = await resp.json();
    if (data.status !== 'success') return null;

    const response = data.response?.[0];
    if (!response) return null;

    // Find the "Total" component (sum of all source types)
    const totalData = response.data?.find((d: any) => d.component === 'Total');
    if (!totalData) return null;

    return {
      xvalues: response.metadata.xvalues,
      yvalues: totalData.yvalues,
    };
  } catch {
    return null;
  }
}

/**
 * Integrate NSHMP hazard curve against HAZUS fragility curve to get
 * annual P(structural damage).
 *
 * For each PGA level, we compute:
 *   P(damage) += P(damage | PGA) * P(PGA in this bin)
 *
 * where P(PGA in bin) is the difference in exceedance probabilities
 * between adjacent PGA levels (i.e., the probability mass in each bin).
 *
 * This properly accounts for correlated faults and background seismicity
 * because the NSHMP hazard curve already integrates over all sources.
 */
function integrateNSHMPDamage(curve: NSHMPHazardCurve, retrofitted: boolean): { annual_p: number; p30yr: number } {
  const retrofitMult = retrofitted ? 0.3 : 1.0;
  let annualDamage = 0;

  for (let i = 0; i < curve.xvalues.length - 1; i++) {
    // PGA at midpoint of this bin
    const pgaMid = (curve.xvalues[i] + curve.xvalues[i + 1]) / 2;
    // Probability mass in this bin: P(exceed lower) - P(exceed upper)
    const pBin = curve.yvalues[i] - curve.yvalues[i + 1];
    if (pBin <= 0) continue;

    // Damage probability at this PGA level
    const mmi = pgaToMMI(pgaMid);
    const dmgProb = woodFrameDamageProb(mmi) * retrofitMult;

    annualDamage += pBin * dmgProb;
  }

  // Add contribution from the last (highest) PGA bin
  // This captures the tail: P(PGA > max level) * damage at max level
  const lastPGA = curve.xvalues[curve.xvalues.length - 1];
  const lastExceedance = curve.yvalues[curve.yvalues.length - 1];
  if (lastExceedance > 0) {
    const mmi = pgaToMMI(lastPGA);
    const dmgProb = woodFrameDamageProb(mmi) * retrofitMult;
    annualDamage += lastExceedance * dmgProb;
  }

  return { annual_p: annualDamage, p30yr: p30(annualDamage) };
}

// ---- Helpers ----

/** Convert annual probability to 30-year probability. */
function p30(annual: number): number {
  return 1 - Math.pow(1 - annual, 30);
}

/** Clamp a value between min and max. */
function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

/** Safe property getter — returns number or null. */
function numProp(props: Record<string, unknown> | null, key: string): number | null {
  if (!props) return null;
  const v = props[key];
  if (typeof v === 'number') return isNaN(v) ? null : v;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return isNaN(n) ? null : n;
  }
  return null;
}

function strProp(props: Record<string, unknown> | null, key: string): string {
  if (!props) return '';
  const v = props[key];
  return typeof v === 'string' ? v : String(v ?? '');
}

// ---- Earthquake sub-model ----

interface FaultResult {
  name: string;
  distance_km: number;
  type: string;
  slip_rate_mm_yr: number | null;
  ucerf3_prob: number | null;
  expected_mmi: number;
  annual_rate: number;
  damage_prob: number;
}

/**
 * BSSA14 ground-motion model: median PGA in g (strike-slip, global).
 *
 * Boore, Stewart, Seyhan & Atkinson (2014), "NGA-West2 equations for
 * predicting PGA, PGV, and 5% damped PSA for shallow crustal earthquakes",
 * Earthquake Spectra 30(3), 1057–1085.
 *
 * ln(Y) = F_e(M) + F_p(R,M) + F_s(Vs30)
 *
 * Coefficients below are for PGA, unspecified/strike-slip mechanism.
 */
function bssa14PGA(M: number, Rjb_km: number, vs30: number): number {
  // Source (event) term coefficients
  const e0 = 0.4473;
  const e1 = 0.4856;
  const e5 = -0.1091;
  const Mh = 5.5;

  // Path term coefficients
  const c1 = -1.243;
  const c2 = 0.1489;
  const c3 = -0.00344;
  const Dc3 = 0; // global model (no regional adjustment)
  const h = 5.3; // fictitious depth for PGA (km)
  const Mref = 4.5;
  const Rref = 1.0;

  // Site term coefficients (linear only)
  const clin = -0.6000;
  const Vref = 760; // m/s — NEHRP B/C boundary
  const Vc = 1500;

  // F_e: source term
  const Fe = M <= Mh
    ? e0 + e1 * (M - Mh)
    : e0 + e5 * (M - Mh);

  // F_p: path term
  const R = Math.sqrt(Rjb_km * Rjb_km + h * h);
  const Fp = (c1 + c2 * (M - Mref)) * Math.log(R / Rref)
    + (c3 + Dc3) * (R - Rref);

  // F_s: linear site term
  const Vs30_star = Math.min(vs30, Vc);
  const Fs = clin * Math.log(Vs30_star / Vref);

  return Math.exp(Fe + Fp + Fs);
}

/**
 * PGA (g) -> MMI via Worden et al. (2012).
 *
 * Worden, Gerstenberger, Rhoades & Wald (2012), "Probabilistic
 * relationships between ground-motion parameters and Modified Mercalli
 * Intensity in California", BSSA 102(1), 204–221.
 *
 * Uses PGA in cm/s^2 = PGA_g * 980.665 and log10:
 *   log10(PGA) < 1.57: MMI = 1.78 + 1.55 * log10(PGA)
 *   log10(PGA) >= 1.57: MMI = -1.60 + 3.70 * log10(PGA)
 */
function pgaToMMI(pga: number): number {
  const pgaCms2 = pga * 980.665; // g -> cm/s^2
  if (pgaCms2 <= 0) return 1;
  const logPGA = Math.log10(pgaCms2);
  if (logPGA < 1.57) {
    return 1.78 + 1.55 * logPGA;
  }
  return -1.60 + 3.70 * logPGA;
}

/** Wood-frame fragility: MMI -> probability of structural damage */
/**
 * Wood-frame fragility curve: MMI -> P(extensive or complete damage).
 *
 * Based on HAZUS-MH MR5 damage functions for W1 (light wood-frame),
 * "extensive + complete" damage states. This represents major structural
 * damage (>20% of replacement cost): foundation cracking, partial wall
 * collapse, structural deformation, or total loss.
 *
 * Does NOT include "slight" or "moderate" HAZUS states (cosmetic cracks,
 * broken windows, minor wall cracking).
 */
function woodFrameDamageProb(mmi: number): number {
  if (mmi < 5) return 0;
  const curve: [number, number][] = [[5, 0.01], [6, 0.05], [7, 0.15], [8, 0.35], [9, 0.60], [10, 0.80]];
  if (mmi >= 10) return 0.80;
  for (let i = 0; i < curve.length - 1; i++) {
    const [m0, p0] = curve[i];
    const [m1, p1] = curve[i + 1];
    if (mmi >= m0 && mmi < m1) {
      return p0 + (p1 - p0) * (mmi - m0) / (m1 - m0);
    }
  }
  return 0;
}

function computeFaults(
  lat: number, lon: number, vs30: number, tiles: TileData,
): FaultResult[] {
  if (!tiles.faults) return [];

  const results: FaultResult[] = [];
  for (const f of tiles.faults.features) {
    const dist = distanceToLine(lat, lon, f);
    if (dist > 50) continue; // only faults within 50 km

    const props = f.properties;
    const name = strProp(props, 'fault_name') || strProp(props, 'name') || 'Unknown';
    const type = strProp(props, 'fault_type') || strProp(props, 'type') || 'Unknown';
    const slipRate = numProp(props, 'slip_rate_mm_yr') ?? numProp(props, 'slip_rate');
    // UCERF3 prob is stored as percent (e.g., 22 = 22%), convert to decimal
    const ucerfPct = numProp(props, 'ucerf3_30yr_m67_prob_pct') ?? numProp(props, 'ucerf3_prob');
    const ucerfProb = ucerfPct != null ? ucerfPct / 100 : null;

    // Estimate magnitude:
    // - If UCERF3 probability exists, use M6.7 (the UCERF3 reference magnitude).
    // - Otherwise, use Wells & Coppersmith (1994) for strike-slip:
    //   M = 5.16 + 1.12 * log10(SRL_km)
    //   where SRL is surface rupture length from the fault's length_km property.
    // - For unknown/unnamed faults without length, use a conservative M6.0 default.
    let mag: number;
    const lengthKm = numProp(props, 'length_km');
    if (ucerfProb != null) {
      mag = 6.7;
    } else if (lengthKm != null && lengthKm > 0) {
      mag = clamp(5.16 + 1.12 * Math.log10(lengthKm), 5.0, 8.5);
    } else {
      mag = 6.0;
    }

    const pga = bssa14PGA(mag, dist, vs30);
    const mmi = pgaToMMI(pga);
    const dmgProb = woodFrameDamageProb(mmi);

    // Annual rate of the fault rupturing
    let annualRate: number;
    if (ucerfProb != null && ucerfProb > 0) {
      annualRate = 1 - Math.pow(1 - ucerfProb, 1 / 30);
    } else if (slipRate != null && slipRate > 0) {
      // Rough estimate: higher slip -> more frequent events
      annualRate = slipRate * 0.0001; // ~1e-4 per mm/yr
    } else {
      annualRate = 0.0001; // fallback: ~0.3% in 30 yr
    }

    results.push({
      name,
      distance_km: Math.round(dist * 100) / 100,
      type,
      slip_rate_mm_yr: slipRate,
      ucerf3_prob: ucerfProb,
      expected_mmi: Math.round(mmi * 10) / 10,
      annual_rate: annualRate,
      damage_prob: dmgProb,
    });
  }

  // Sort by distance
  results.sort((a, b) => a.distance_km - b.distance_km);

  // Deduplicate: keep only the nearest segment per fault name
  const seen = new Set<string>();
  const deduped: FaultResult[] = [];
  for (const r of results) {
    if (!seen.has(r.name)) {
      seen.add(r.name);
      deduped.push(r);
    }
  }
  return deduped;
}

// ---- Zone lookups ----

function lookupZones(lat: number, lon: number, tiles: TileData) {
  // All zone lookups are now vector point-in-polygon for exact boundaries.
  // No rasterization artifacts.

  // Liquefaction
  const inLiq = !!findContainingFeature(lat, lon, tiles.liquefaction);

  // Landslide
  const inLs = !!findContainingFeature(lat, lon, tiles.landslide);

  // Flood zone
  const floodFeature = findContainingFeature(lat, lon, tiles.flood_zones);
  const floodZoneRaw = floodFeature ? strProp(floodFeature.properties, 'FLD_ZONE') : '';
  const floodZoneMap: Record<string, string> = {
    'V': 'V/VE', 'VE': 'V/VE', 'AE': 'AE', 'A': 'A', 'AH': 'A', 'AO': 'A',
    'A99': 'A', 'AR': 'A', 'D': 'D', 'X': 'X',
  };

  // Fire zone (FHSZ — regulatory designation, not used for probability)
  const fireFeat = findContainingFeature(lat, lon, tiles.fire_zones);
  const fireClass = fireFeat ? strProp(fireFeat.properties, 'hazard_class') || strProp(fireFeat.properties, 'FHSZ_Description') : 'None';

  // Tsunami
  const inTsunami = !!findContainingFeature(lat, lon, tiles.tsunami);

  // Soils
  const soilFeat = findContainingFeature(lat, lon, tiles.soils);
  const soilClass = soilFeat
    ? strProp(soilFeat.properties, 'shrink_swell_class') || strProp(soilFeat.properties, 'lep_class') || 'Unknown'
    : 'Unknown';

  // Dam inundation
  const damNames: string[] = [];
  let inDamZone = false;
  if (tiles.dam_inundation) {
    for (const f of tiles.dam_inundation.features) {
      if (pointInPolygon(lat, lon, f)) {
        inDamZone = true;
        const name = strProp(f.properties, 'dam_name') || strProp(f.properties, 'DAMNAME') || strProp(f.properties, 'name') || 'Unknown';
        damNames.push(name);
      }
    }
  }

  return {
    liquefaction: inLiq,
    landslide: inLs,
    fema_flood: floodZoneMap[floodZoneRaw] || 'None',
    fire_hazard: fireClass,
    tsunami_inundation: inTsunami,
    dam_inundation: { in_zone: inDamZone, dam_names: damNames },
    expansive_soil: soilClass,
  };
}

// ---- Sea level rise ----

function computeSLR(lat: number, lon: number, tiles: TileData) {
  // SLR is now vector — the combined GeoJSON has an 'increment_ft' property
  const thresholds: [string, number][] = [
    ['1ft', 1], ['2ft', 2], ['3ft', 3], ['4ft', 4], ['6ft', 6], ['10ft', 10],
  ];

  let lowestFt: number | null = null;
  const inundatedAt: Record<string, boolean> = {};

  for (const [key, ft] of thresholds) {
    // Check if point is inside any SLR polygon for this increment
    let inundated = false;
    if (tiles.slr) {
      for (const f of tiles.slr.features) {
        const incFt = numProp(f.properties, 'increment_ft');
        if (incFt === ft && pointInPolygon(lat, lon, f)) {
          inundated = true;
          break;
        }
      }
    }
    inundatedAt[key] = inundated;
    if (inundated && lowestFt === null) {
      lowestFt = ft;
    }
  }

  return { lowest_threshold_ft: lowestFt, inundated_at: inundatedAt };
}

// ---- Structural hazard models ----

function computeEarthquake(
  faults: FaultResult[], retrofitted: boolean,
): { annual_p: number; p30yr: number } {
  // Sum over faults: P(damage) = 1 - prod(1 - rate_i * dmg_i), assuming independence
  // Retrofit multiplier: 0.3x damage probability per FEMA P-807
  const retrofitMult = retrofitted ? 0.3 : 1.0;
  let survivalProduct = 1;
  for (const f of faults) {
    survivalProduct *= (1 - f.annual_rate * f.damage_prob * retrofitMult);
  }
  const annual = 1 - survivalProduct;
  return { annual_p: annual, p30yr: p30(annual) };
}

function computeLiquefaction(
  inZone: boolean, faults: FaultResult[],
): { annual_p: number; p30yr: number } {
  if (!inZone) return { annual_p: 0, p30yr: 0 };

  // P(strong shaking) = P(any fault produces MMI >= VII at site)
  let survivalMMI7 = 1;
  for (const f of faults) {
    if (f.expected_mmi >= 7) {
      survivalMMI7 *= (1 - f.annual_rate);
    }
  }
  const pStrongShaking = 1 - survivalMMI7;

  // P(liquefaction damage) = P(shaking) * P(liq|shaking) * P(damage|liq)
  const annual = pStrongShaking * 0.30 * 0.40;
  return { annual_p: annual, p30yr: p30(annual) };
}

/**
 * Liquefaction using NSHMP hazard curve for shaking probability.
 * P(liquefaction damage) = P(PGA > 0.1g) * P(liq|shaking) * P(damage|liq)
 */
function computeLiquefactionFromNSHMP(
  curve: NSHMPHazardCurve, inZone: boolean,
): { annual_p: number; p30yr: number } {
  if (!inZone) return { annual_p: 0, p30yr: 0 };

  // Interpolate P(PGA > 0.1g) from the hazard curve
  let pExceed01g = 0;
  for (let i = 0; i < curve.xvalues.length - 1; i++) {
    if (curve.xvalues[i] <= 0.1 && curve.xvalues[i + 1] >= 0.1) {
      const frac = (0.1 - curve.xvalues[i]) / (curve.xvalues[i + 1] - curve.xvalues[i]);
      pExceed01g = curve.yvalues[i] + frac * (curve.yvalues[i + 1] - curve.yvalues[i]);
      break;
    }
  }

  // P(liq|shaking) * P(damage|liq) — same as existing model
  const annual = pExceed01g * 0.30 * 0.40;
  return { annual_p: annual, p30yr: p30(annual) };
}

/**
 * Wildfire structural damage model.
 *
 * P(damage) = FSim_BP × P(damage | fire)
 *
 * Where P(damage | fire) is estimated from a model trained on the
 * Zamanialaei et al. 2025 DINS dataset (47K structures, 5 CA fires)
 * with three features:
 *   - SSD: Structure Separation Distance (meters, from MS Building Footprints)
 *   - CFL: Conditional Flame Length (feet, from USFS WRC)
 *   - FAIR Plan share: insurer-of-last-resort market share by zip (0-1)
 *
 * The model achieves AUC=0.852 on 5-fold CV, outperforming Zamanialaei's
 * full 10-feature model (AUC=0.818) because FAIR Plan share captures
 * community-level vulnerability that building inspection data misses.
 *
 * When model inputs are unavailable, falls back to a conservative estimate
 * using the USFS response function (Scott et al. 2024).
 *
 * Citations:
 *   Zamanialaei et al. 2025, Nature Communications 16:8041
 *   Scott et al. 2024, USFS Wildfire Risk to Communities v2.0
 *   California FAIR Plan Association (cfpnet.com)
 */
/**
 * Wildfire structural damage model.
 *
 * Pre-computed P(major structural damage, 30yr) from a raster tile
 * (data/tiles/fire_risk/). The raster was generated by XGBoost trained on:
 *   - Positives: 27,754 DINS structures with major damage (Zamanialaei et al. 2025)
 *   - Negatives: 27,754 random CA buildings outside all CalFire perimeters (1996-2025)
 *   - Features: FSim BP (lognormal ember kernel), CFL, SSD, FAIR Plan share
 *   - 5-fold CV AUC: 0.998
 *
 * The raster encodes P(damage) × 10000 as uint16 at 30m resolution.
 * Feature importance: FAIR share (0.718), SSD (0.148), FSim BP (0.088), CFL (0.046).
 *
 * Citations:
 *   Zamanialaei et al. 2025, Nature Communications 16:8041
 *   CalFire FRAP historical fire perimeters (1996-2025)
 *   Scott et al. 2024, USFS Wildfire Risk to Communities v2.0
 *   Sardoy et al. 2008, Combust Flame 154:478-488 (lognormal ember transport)
 *   California FAIR Plan Association
 */
function computeWildfire(
  fireRiskP30: number,  // pre-computed P(damage, 30yr) from raster tile
  fireHazardZone: string,  // CalFire FHSZ designation
): { annual_p: number; p30yr: number; wui_underestimate: boolean } {
  // Flag WUI underestimate: CalFire says high hazard but our model reads low.
  // This means FSim can't model fire spread into this developed area.
  // Paradise, Malibu, Berkeley Hills are examples.
  const highHazard = fireHazardZone === 'Very High' || fireHazardZone === 'High';
  const wui_underestimate = highHazard && fireRiskP30 < 0.05;

  if (fireRiskP30 <= 0) return { annual_p: 0, p30yr: 0, wui_underestimate };
  const annual = 1 - Math.pow(1 - fireRiskP30, 1 / 30);
  return { annual_p: annual, p30yr: fireRiskP30, wui_underestimate };
}

function computeFlood(floodZone: string): { annual_p: number; p30yr: number } {
  // FEMA flood zone -> annual probability of flood event * damage factor
  let annual: number;
  switch (floodZone) {
    case 'V/VE': annual = 0.01 * 0.60; break;   // coastal high hazard
    case 'AE':   annual = 0.01 * 0.40; break;   // 100-year with BFE
    case 'A':    annual = 0.01 * 0.40; break;    // 100-year
    case 'D':    annual = 0.005 * 0.30; break;   // undetermined
    case 'X':    annual = 0.0002 * 0.10; break;  // 500-year or better, minimal damage
    default:     annual = 0.0001 * 0.05;          // unmapped, negligible
  }
  return { annual_p: annual, p30yr: p30(annual) };
}

function computeTsunami(inZone: boolean, elevation_m: number, distToCoast_km: number): { annual_p: number; p30yr: number } {
  // The CGS tsunami zone represents worst-case (975-year return period) inundation.
  // But actual risk depends heavily on elevation — a site at 50m elevation
  // within the mapped zone is far safer than one at 3m.
  //
  // Maximum credible tsunami runup for California is ~10-15m (Cascadia M9.2,
  // near-source submarine landslide). Sites above ~20m are effectively safe.
  //
  // Scale the damage probability by an elevation-based attenuation:
  //   - Below 5m: full damage probability (0.50)
  //   - 5-15m: linearly decreasing
  //   - Above 15m: negligible (0.01 residual for unmapped local effects)
  if (!inZone) return { annual_p: 0, p30yr: 0 };

  let damageFactor: number;
  if (elevation_m <= 0 || isNaN(elevation_m)) {
    // No elevation data — use distance as proxy
    // Within 200m of coast at unknown elevation: assume low-lying
    // Beyond 1km: likely elevated
    if (distToCoast_km < 0.2) {
      damageFactor = 0.50;
    } else if (distToCoast_km < 1.0) {
      damageFactor = 0.50 * (1 - (distToCoast_km - 0.2) / 0.8);
    } else {
      damageFactor = 0.01;
    }
  } else if (elevation_m < 5) {
    damageFactor = 0.50;
  } else if (elevation_m < 15) {
    damageFactor = 0.50 * (1 - (elevation_m - 5) / 10);
  } else {
    damageFactor = 0.01;
  }

  const annual = 0.001 * damageFactor;
  return { annual_p: annual, p30yr: p30(annual) };
}

/**
 * Landslide risk based on USGS susceptibility (Mirus et al. 2024).
 *
 * Uses the n10 susceptibility score (0-81) calibrated against the USGS
 * national landslide inventory (174K California landslides, Belair et al. 2022)
 * to derive empirical annual landslide rates per susceptibility level.
 *
 * The nearby max susceptibility (within ~200m) captures proximity to
 * susceptible terrain — a house on flat ground at the top of a susceptible
 * bluff is at real risk from the bluff retreating underneath it.
 *
 * Annual rates derived from inventory density / 50-year effective observation
 * period. The 50-year estimate is conservative (older mapping is incomplete),
 * which biases rates downward per CLAUDE.md.
 *
 * P(damage|landslide) is not modeled — we report landslide probability
 * directly. Structural damage given landslide depends heavily on landslide
 * type, velocity, and building construction in ways we can't resolve from
 * remote data.
 *
 * Citations:
 *   Mirus et al. 2024, AGU Advances, doi:10.1029/2024AV001214
 *   Belair et al. 2022, USGS data release, doi:10.5066/P9FZUX6N
 *   Crovelli & Coe 2008, USGS OFR 2008-1116
 */

// Annual landslide probability per pixel at each n10 susceptibility level.
// Calibrated from 174K California landslides / 50yr effective observation.
// Key values: n10=0 → ~0%, n10=40 → 0.08%, n10=60 → 0.22%, n10=81 → 8%.
const LANDSLIDE_RATE_BY_N10: number[] = [
  // n10 = 0-9
  0.00000049, 0.00000100, 0.00000150, 0.00000200, 0.00000300,
  0.00000450, 0.00000600, 0.00000770, 0.00000770, 0.00000770,
  // n10 = 10-19
  0.00000770, 0.00000900, 0.00001050, 0.00001260, 0.00001260,
  0.00001260, 0.00001400, 0.00001576, 0.00001576, 0.00001576,
  // n10 = 20-29
  0.00001260, 0.00001400, 0.00001576, 0.00001576, 0.00001750,
  0.00001950, 0.00002100, 0.00002300, 0.00002450, 0.00002588,
  // n10 = 30-39
  0.00001576, 0.00001750, 0.00001950, 0.00002100, 0.00002300,
  0.00002450, 0.00002588, 0.00002800, 0.00003100, 0.00003500,
  // n10 = 40-49
  0.00002588, 0.00002800, 0.00003100, 0.00003500, 0.00003800,
  0.00003959, 0.00004200, 0.00004700, 0.00005200, 0.00005800,
  // n10 = 50-59
  0.00003959, 0.00004500, 0.00005200, 0.00006000, 0.00006500,
  0.00007448, 0.00007448, 0.00008000, 0.00009000, 0.00010000,
  // n10 = 60-69
  0.00007448, 0.00008500, 0.00010000, 0.00011500, 0.00013000,
  0.00016499, 0.00016499, 0.00018000, 0.00020000, 0.00023000,
  // n10 = 70-79
  0.00016499, 0.00020000, 0.00024000, 0.00028000, 0.00030705,
  0.00030705, 0.00040000, 0.00050000, 0.00060000, 0.00072447,
  // n10 = 80-81
  0.00072447, 0.00276627,
];

/**
 * Four-tier landslide risk model.
 *
 * Tier 1: InSAR velocity > 3.5mm/yr (active ground movement)
 *   → P(major damage, 30yr) ≈ 50%
 *   → Derived from Portuguese Bend: 140/170 homes damaged 1956-58, 245/~400
 *     homes impacted 2023-24. Single rate across all active velocities.
 *   → Cruden & Varnes 1996 Class 1-2: "some permanent structures undamaged"
 *     at very slow (16-1600 mm/yr), implying most ARE damaged.
 *   → Conservative for slow active deposits (2-16 mm/yr), aggressive for
 *     extreme cases (>1600 mm/yr). Velocity-graded rates are a future
 *     refinement when CA-specific fragility data is available.
 *   → Requires OPERA-DISP InSAR velocity data — not yet integrated.
 *     Until InSAR is available, all deposits fall to Tier 2.
 *
 * Tier 2: On USGS mapped deposit (C≥3) + InSAR velocity ≤ 3.5mm/yr (dormant)
 *   → P(major damage, 30yr) ≈ 3%
 *   → Derived from Handwerger et al. 2019 (JGR Earth Surface): 193 deposits
 *     reactivated out of ~6,500 mapped (C≥3) in the Eel River study area
 *     during the extreme 2017 wet year = 3.0% single-year reactivation rate.
 *   → Used as 30-year rate: not all reactivations cause major structural
 *     damage (many are slow earthflows in rural areas), roughly offsetting
 *     the probability of multiple extreme wet years in 30 years.
 *   → This is the default tier for all deposits until InSAR data is integrated.
 *
 * Tier 3: Inside CGS Seismic Hazard Zone (earthquake-induced landslide) but
 *   NOT on a USGS mapped deposit.
 *   → Uses n10 susceptibility at point (Mirus et al. 2024), already calibrated
 *     against the USGS inventory. CGS zones correspond to n10 76-78 (median),
 *     giving ~0.5%/30yr — intermediate between dormant deposit and background.
 *   → More defensible than inventing a CGS-zone-specific rate: the n10 model
 *     was independently validated and captures terrain-level variation.
 *
 * Tier 4: None of the above.
 *   → FEMA NRI tract-level loss rate from disaster claims.
 *   → annual_loss = LNDS_AFREQ × LNDS_HLRB (FEMA NRI v1.20)
 *
 * Citations:
 *   Belair et al. 2025, USGS landslide inventory v3, doi:10.5066/P9FZUX6N
 *   Handwerger et al. 2019, JGR Earth Surface, doi:10.1029/2019JF005035
 *   Cruden & Varnes 1996, TRB Special Report 247, pp.36-75
 *   Mirus et al. 2024, AGU Advances, doi:10.1029/2024AV001214
 *   FEMA National Risk Index v1.20, December 2025
 */

// Tier 1: Active deposit annual damage rate.
// Portuguese Bend 1956-58: 140/170 homes = 82%. 2023-24: 245/~400 = 61%.
// Abalone Cove 1974-78: ~50 homes damaged. La Conchita 2005: 36/~80 = 45%.
// Single rate lumping all active velocities. Conservative for slow active,
// aggressive for extreme. 50%/30yr ≈ 0.0230/yr.
const ACTIVE_DEPOSIT_ANNUAL_RATE = 0.0230;

// Tier 2: Dormant deposit annual damage rate.
// Midpoint of plausible range (2-14%) derived from:
//   - Crovelli & Coe 2008 (USGS OFR 2008-1116): 65 damaging landslides/yr
//     in the 10-county SF Bay Area, ~30,000 mapped deposits (C≥3)
//   - Range depends on structures per event (1-3) and fraction on mapped
//     deposits (30-70%), both unknown
//   - Handwerger et al. 2019: 3% single-year reactivation rate (lower bound)
// Midpoint ~8%/30yr is the least biased estimate given the uncertainty.
// 8%/30yr ≈ 0.00277/yr.
const DORMANT_DEPOSIT_ANNUAL_RATE = 0.00277;

function computeLandslide(
  insideMappedDeposit: boolean,
  depositConfidence: number | null,
  insarVelocityMmYr: number | null,  // null = no InSAR data available
  inCgsLandslideZone: boolean,
  n10Susceptibility: number,
  nriAnnualRate: number,
): { annual_p: number; p30yr: number; tier: 1 | 2 | 3 | 4 } {
  // Tier 1: Active ground movement (InSAR velocity > 3.5mm/yr).
  // InSAR is the primary classifier — it sees what mappers missed.
  // A parcel with measurable displacement is Tier 1 regardless of whether
  // a deposit polygon exists in the USGS inventory. Seal Cove (Moss Beach)
  // and other unmapped active slides would otherwise fall to Tier 3/4.
  // Threshold at 3.5 mm/yr = 2× typical Sentinel-1 velocity standard deviation
  // (~1.75 mm/yr). Below this, signal is indistinguishable from noise.
  // Sadeghi et al. 2021 (Remote Sensing of Environment) measured 1.1 mm/yr
  // standard deviation across Sentinel-1 InSAR processing approaches.
  if (insarVelocityMmYr != null && insarVelocityMmYr > 3.5) {
    return {
      annual_p: ACTIVE_DEPOSIT_ANNUAL_RATE,
      p30yr: p30(ACTIVE_DEPOSIT_ANNUAL_RATE),
      tier: 1,
    };
  }

  // Tier 2: Dormant deposit (on mapped deposit, InSAR shows no movement
  // or InSAR data not yet available). The inventory matters here because
  // dormant deposits are invisible to InSAR — they're not moving.
  if (insideMappedDeposit && (depositConfidence ?? 0) >= 3) {
    return {
      annual_p: DORMANT_DEPOSIT_ANNUAL_RATE,
      p30yr: p30(DORMANT_DEPOSIT_ANNUAL_RATE),
      tier: 2,
    };
  }

  // Tier 3: CGS Seismic Hazard Zone — susceptible terrain, no confirmed deposit,
  // no InSAR signal. Use calibrated n10 rate at point.
  if (inCgsLandslideZone && n10Susceptibility > 0) {
    const n10 = Math.min(n10Susceptibility, 81);
    const annual = LANDSLIDE_RATE_BY_N10[n10];
    return { annual_p: annual, p30yr: p30(annual), tier: 3 };
  }

  // Tier 4: Off-deposit, outside CGS zone — NRI tract-level loss rate
  if (nriAnnualRate > 0) {
    return { annual_p: nriAnnualRate, p30yr: p30(nriAnnualRate), tier: 4 };
  }

  return { annual_p: 0, p30yr: 0, tier: 4 };
}

function computeDamInundation(inZone: boolean): { annual_p: number; p30yr: number } {
  // Dam failure is very rare but high-consequence
  const annual = inZone ? 0.0001 * 0.90 : 0;
  return { annual_p: annual, p30yr: p30(annual) };
}

function computeErosion(
  lat: number, lon: number, tiles: TileData,
): { annual_p: number; p30yr: number; years_to_threat: number | null } {
  if (!tiles.erosion) return { annual_p: 0, p30yr: 0, years_to_threat: null };

  // Find nearest erosion transect
  const nearest = findNearest(lat, lon, tiles.erosion, 1);
  if (nearest.length === 0) return { annual_p: 0, p30yr: 0, years_to_threat: null };

  const { feature, distance_km } = nearest[0];
  const distCoastKm = distance_km; // proxy: distance to transect ~ distance to coast

  // Erosion relevant within ~2km of coast (transect distance is a proxy for
  // distance to the active shoreline, not an exact measurement)
  if (distCoastKm > 2.0) return { annual_p: 0, p30yr: 0, years_to_threat: null };

  const erosionRate = numProp(feature.properties, 'erosion_rate_m_yr')
    ?? numProp(feature.properties, 'erosion_rate')
    ?? numProp(feature.properties, 'EPR')
    ?? numProp(feature.properties, 'rate_m_yr');

  if (erosionRate == null || erosionRate >= 0) {
    // Null, zero, or positive (accreting) — no erosion threat
    return { annual_p: 0, p30yr: 0, years_to_threat: null };
  }

  const distCoastM = distCoastKm * 1000;
  const absRate = Math.abs(erosionRate); // m/yr
  const yearsToThreat = distCoastM / absRate;

  // Erosion damage model:
  //
  // The USGS transect rate is a long-term average, but coastal erosion is
  // episodic — bluffs can lose 5-20m in a single storm event. A property
  // at 200m from the shoreline with a -0.5 m/yr rate has a mean time of
  // 400 years, but could be reached by a large episodic event much sooner.
  //
  // We model two components:
  // 1. Steady-state erosion: probability of the shoreline reaching the property
  //    based on average rate, with variance from rate uncertainty
  // 2. Episodic bluff failure: for properties within ~200m of the coast on
  //    eroding shorelines, there's a base probability of a large single-event
  //    retreat (storm bluff collapse, landslide) that could affect the property
  //
  // This approach follows Hapke & Plant (2010), "Predicting coastal cliff
  // erosion using a Bayesian probabilistic model", Marine Geology 278.
  //
  // The annual probability increases sharply within ~100m of the coast
  // and for faster erosion rates.

  let annual: number;

  if (distCoastM < 50) {
    // Within 50m: imminent threat, high probability
    annual = 0.02 * (absRate / 0.5); // 2%/yr baseline scaled by rate
  } else if (distCoastM < 200) {
    // 50-200m: episodic bluff failure risk
    // At 100m with 0.5m/yr rate: ~0.5%/yr
    annual = 0.005 * (absRate / 0.5) * (200 - distCoastM) / 150;
  } else {
    // Beyond 200m: steady-state model, risk diminishes with distance
    // Use logistic centered on years_to_threat
    annual = 1 / (1 + Math.exp(0.05 * (yearsToThreat - 100)));
  }

  return {
    annual_p: annual,
    p30yr: p30(annual),
    years_to_threat: Math.round(yearsToThreat),
  };
}

// ---- Tier 2: environmental/health ----

function computeCalEnviroScreen(
  lat: number, lon: number, tiles: TileData,
): HazardReport['calenviroscreen'] {
  const feature = findContainingFeature(lat, lon, tiles.calenviroscreen);
  if (!feature) return null;

  const props = feature.properties;
  return {
    overall_percentile: numProp(props, 'overall_percentile') ?? numProp(props, 'CIscoreP'),
    tract: strProp(props, 'tract') || strProp(props, 'Census_Tract') || null,
    pm25_pctl: numProp(props, 'pm25_pctl') ?? numProp(props, 'PM2_5_P'),
    diesel_pm_pctl: numProp(props, 'diesel_pm_pctl') ?? numProp(props, 'Diesel_PM_P'),
    traffic_pctl: numProp(props, 'traffic_pctl') ?? numProp(props, 'Traffic_P'),
    poverty_pctl: numProp(props, 'poverty_pctl') ?? numProp(props, 'Poverty_P'),
  };
}

function computeAviationLead(
  lat: number, lon: number, tiles: TileData,
): HazardReport['aviation_lead'] {
  if (!tiles.airports) return { nearest_airport: null, risk_level: 'Low' };

  // Find nearest airport with piston ops
  const all = findNearest(lat, lon, tiles.airports, 10);
  let nearest: { code: string; name: string; distance_km: number; piston_ops: number } | null = null;

  for (const { feature, distance_km } of all) {
    const ops = numProp(feature.properties, 'annual_piston_ops') ?? 0;
    if (ops > 0) {
      nearest = {
        code: strProp(feature.properties, 'airport_id') || strProp(feature.properties, 'ident') || strProp(feature.properties, 'code') || '',
        name: strProp(feature.properties, 'airport_name') || strProp(feature.properties, 'name') || '',
        distance_km: Math.round(distance_km * 100) / 100,
        piston_ops: ops,
      };
      break; // already sorted by distance
    }
  }

  let risk = 'Low';
  if (nearest) {
    const d = nearest.distance_km;
    if (d < 0.5) risk = 'High';
    else if (d < 1) risk = 'Moderate';
    else if (d < 2) risk = 'Elevated';
  }

  return { nearest_airport: nearest, risk_level: risk };
}

function computeTrafficPollution(
  lat: number, lon: number, tiles: TileData,
): HazardReport['traffic_pollution'] {
  if (!tiles.traffic) return { nearest_major_road: null, risk_level: 'Low' };

  const nearest = findNearest(lat, lon, tiles.traffic, 1);
  if (nearest.length === 0) return { nearest_major_road: null, risk_level: 'Low' };

  const { feature, distance_km } = nearest[0];
  const distM = distance_km * 1000;

  // Parse AADT from properties (may be string)
  const props = feature.properties;
  const aadtRaw = props?.['AHEAD_AADT'] ?? props?.['BACK_AADT'] ?? props?.['aadt'] ?? 0;
  const aadt = typeof aadtRaw === 'string' ? parseInt(aadtRaw, 10) || 0 : Number(aadtRaw) || 0;

  let risk = 'Low';
  if (distM < 100 && aadt > 100_000) risk = 'Severe';
  else if (distM < 200 && aadt > 20_000) risk = 'Elevated';

  return {
    nearest_major_road: { distance_m: Math.round(distM), aadt },
    risk_level: risk,
  };
}

function computeContamination(
  lat: number, lon: number, tiles: TileData,
): HazardReport['contamination'] {
  const within500m = findWithinRadius(lat, lon, tiles.contamination, 0.5);
  const withinHalfMile = findWithinRadius(lat, lon, tiles.contamination, 0.8047);

  const mapSite = (item: { feature: { geometry: { type: string; coordinates?: number[] }; properties: Record<string, unknown> | null }; distance_km: number }) => {
    // Extract coordinates from point geometry
    const geom = item.feature.geometry;
    let siteLat: number | null = null;
    let siteLon: number | null = null;
    if (geom.type === 'Point' && geom.coordinates) {
      siteLon = geom.coordinates[0];
      siteLat = geom.coordinates[1];
    }
    return {
      name: strProp(item.feature.properties, 'site_name') || strProp(item.feature.properties, 'BUSINESS_NAME') || strProp(item.feature.properties, 'name') || 'Unknown',
      type: strProp(item.feature.properties, 'site_type') || strProp(item.feature.properties, 'type') || 'Unknown',
      status: strProp(item.feature.properties, 'status') || strProp(item.feature.properties, 'cleanup_status') || 'Unknown',
      source: strProp(item.feature.properties, 'source') || 'unknown',
      distance_m: Math.round(item.distance_km * 1000),
      lat: siteLat,
      lon: siteLon,
    };
  };

  return {
    sites_within_500m: within500m.map(mapSite),
    sites_within_1km: withinHalfMile.map(mapSite),
  };
}

// ---- Main entry point ----

export function computeHazardReport(
  lat: number,
  lon: number,
  tiles: TileData,
  options: { retrofitted?: boolean; fairShare?: number; nriLandslideRate?: number } = {},
): HazardReport {
  // Sample site parameters
  const { elevation_m: elevation, slope_deg: slope } = sampleElevation(tiles.elevation, lat, lon);
  const vs30Raw = sampleFloat32Grid(tiles.vs30, lat, lon);
  const vs30 = isNaN(vs30Raw) ? 760 : vs30Raw; // default to reference rock if unavailable

  // Fault analysis (still used for display — nearby faults list)
  const faultResults = computeFaults(lat, lon, vs30, tiles);

  // Zone lookups
  const zones = lookupZones(lat, lon, tiles);

  // Sea level rise
  const slr = computeSLR(lat, lon, tiles);

  // Earthquake: computed client-side from USGS NSHMP hazard curve.
  // Worker provides vs30 and liquefaction zone; frontend fetches NSHMP
  // and runs the HAZUS fragility integration. This eliminates the ~1s
  // NSHMP API call from the worker, keeping it under 10ms CPU.
  const earthquake = { annual_p: 0, p30yr: 0 }; // placeholder, filled client-side

  // Wildfire: FSim burn probability × model-based P(damage|fire)
  const fsimBP = sampleBurnProbability(tiles.burn_probability, lat, lon);
  const cflFt = sampleCFL(tiles.cfl, lat, lon);
  const ssdM = sampleSSD(tiles.ssd, lat, lon);
  const fairShare = options.fairShare ?? 0;
  // Fire risk: pre-computed P(major damage, 30yr) from raster tile.
  // Tile encodes P × 10000 as uint16 (same format as burn_probability but different scale).
  const fireRiskTile = tiles.fire_risk;
  let fireRiskP30 = 0;
  if (fireRiskTile) {
    const { data, rows, cols, south, west } = fireRiskTile;
    const north = south + 0.1;
    const east = west + 0.1;
    if (lat >= south && lat <= north && lon >= west && lon <= east) {
      const r = Math.min(Math.max(Math.round((lat - south) / (north - south) * (rows - 1)), 0), rows - 1);
      const c = Math.min(Math.max(Math.round((lon - west) / (east - west) * (cols - 1)), 0), cols - 1);
      fireRiskP30 = data[r * cols + c] / 10000;  // uint16 / 10000 = probability
    }
  }
  const wildfire = computeWildfire(fireRiskP30, zones.fire_hazard);
  const flood = computeFlood(zones.fema_flood);
  // Estimate distance to coast (rough heuristic — will be replaced with proper coastline distance)
  // For the California coast, the tsunami zone boundary gives us a proxy: if you're in the zone,
  // you're within a few km of the coast. Use erosion transect data if available.
  let distToCoastKm = 10; // default: assume far from coast
  if (tiles.erosion) {
    const nearestCoast = findNearest(lat, lon, tiles.erosion, 1);
    if (nearestCoast.length > 0) distToCoastKm = nearestCoast[0].distance_km;
  }
  const tsunami = computeTsunami(zones.tsunami_inundation, elevation, distToCoastKm);
  // Landslide: four-tier model
  // Check supplemental layer first (validated local studies like Seal Cove).
  // These override everything — if a licensed geologist mapped it as active, it's Tier 1.
  const suppFeature = findContainingFeature(lat, lon, tiles.landslide_supplemental);
  // Tier 1/2: USGS inventory deposit check (Belair et al. 2025)
  const lsFeature = suppFeature ?? findContainingFeature(lat, lon, tiles.landslide_inventory);
  const insideMappedDeposit = !!lsFeature;
  const lsConfidence = lsFeature ? numProp(lsFeature.properties, 'Confidence') : null;
  // Tier 3: CGS Seismic Hazard Zone + n10 susceptibility at point
  const n10 = sampleLandslideSusc(tiles.landslide_susc, lat, lon);
  // Tier 4: FEMA NRI tract-level rate
  const nriLandslideRate = options.nriLandslideRate ?? 0;
  // InSAR velocity from OPERA-DISP Sentinel-1.
  // Three sources, checked in order:
  //   1. Supplemental polygon is_active flag — a licensed geologist's determination
  //      overrides satellite data. Reported as velocity above threshold.
  //   2. Inventory polygon property (velocity_max_mm_yr) — max velocity across
  //      all pixels within the deposit boundary.
  //   3. Raster tile (insar_velocity) — point velocity at the parcel location.
  //      Catches active movement outside mapped deposits.
  const suppIsActive = suppFeature
    && (suppFeature.properties as Record<string, unknown>)?.is_active === true;
  const insarFromDeposit = lsFeature
    ? numProp(lsFeature.properties, 'velocity_max_mm_yr') ?? null
    : null;
  const insarFromRaster = tiles.insar_velocity
    ? sampleFloat32Grid(tiles.insar_velocity, lat, lon)
    : NaN;
  // If a supplemental study says active, synthesize a velocity above threshold.
  const insarVelocity: number | null = suppIsActive
    ? (insarFromDeposit ?? 999)  // force Tier 1; use real velocity if available
    : insarFromDeposit ?? (isNaN(insarFromRaster) ? null : Math.abs(insarFromRaster));
  const landslide = computeLandslide(
    insideMappedDeposit, lsConfidence, insarVelocity,
    zones.landslide, n10, nriLandslideRate,
  );
  const damInundation = computeDamInundation(zones.dam_inundation.in_zone);
  const erosion = computeErosion(lat, lon, tiles);

  // Combined 30-year: P = 1 - product of (1 - p_i)
  const allAnnual = [
    earthquake.annual_p, wildfire.annual_p,
    flood.annual_p, tsunami.annual_p, landslide.annual_p,
    damInundation.annual_p, erosion.annual_p,
  ];
  let survivalProduct = 1;
  for (const pa of allAnnual) {
    survivalProduct *= (1 - pa);
  }
  const combined30 = 1 - Math.pow(survivalProduct, 30);

  return {
    coordinates: { lat, lon },
    elevation_m: isNaN(elevation) ? 0 : Math.round(elevation * 10) / 10,
    slope_deg: isNaN(slope) ? 0 : Math.round(slope * 10) / 10,
    vs30: Math.round(vs30),

    faults: faultResults.map(f => ({
      name: f.name,
      distance_km: f.distance_km,
      type: f.type,
      slip_rate_mm_yr: f.slip_rate_mm_yr,
      ucerf3_prob: f.ucerf3_prob,
      expected_mmi: f.expected_mmi,
    })),

    zones,
    sea_level_rise: slr,

    structural: {
      earthquake,
      wildfire,
      flood,
      tsunami,
      erosion,
      landslide,
      dam_inundation: damInundation,
      combined_30yr: combined30,
    },

    calenviroscreen: computeCalEnviroScreen(lat, lon, tiles),
    aviation_lead: computeAviationLead(lat, lon, tiles),
    traffic_pollution: computeTrafficPollution(lat, lon, tiles),
    contamination: computeContamination(lat, lon, tiles),
  };
}
