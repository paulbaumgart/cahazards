// spatial.ts — Dependency-free spatial query functions for GeoJSON features

// Minimal GeoJSON types (avoids external dependency)
declare namespace GeoJSON {
  interface Feature { type: 'Feature'; geometry: any; properties: Record<string, any> | null; }
  interface FeatureCollection { type: 'FeatureCollection'; features: Feature[]; }
}

type Position = [number, number] | [number, number, number];
type Ring = Position[];

/**
 * Point-in-polygon test using ray casting algorithm.
 * Returns true if [lon, lat] is inside the polygon.
 * Handles Polygon and MultiPolygon geometries.
 */
export function pointInPolygon(lat: number, lon: number, feature: GeoJSON.Feature): boolean {
  const geom = feature.geometry;
  if (geom.type === 'Polygon') {
    return pointInRings(lon, lat, geom.coordinates as Ring[]);
  }
  if (geom.type === 'MultiPolygon') {
    for (const polygon of geom.coordinates as Ring[][]) {
      if (pointInRings(lon, lat, polygon)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Ray casting for a single polygon (outer ring + optional holes).
 * First ring is the outer boundary; subsequent rings are holes.
 */
function pointInRings(x: number, y: number, rings: Ring[]): boolean {
  // Must be inside outer ring
  if (!pointInRing(x, y, rings[0])) {
    return false;
  }
  // Must not be inside any hole
  for (let i = 1; i < rings.length; i++) {
    if (pointInRing(x, y, rings[i])) {
      return false;
    }
  }
  return true;
}

/**
 * Ray casting algorithm for a single ring.
 * Coordinates are [lon, lat] (x, y).
 */
function pointInRing(x: number, y: number, ring: Ring): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

/**
 * Minimum distance in km from a point to the nearest polygon boundary
 * in a FeatureCollection. Used for proximity-based risk at zone edges
 * (e.g., landslide zone boundary 31m from a parcel).
 *
 * Returns Infinity if no polygon features exist.
 */
export function distanceToNearestPolygonBoundary(
  lat: number, lon: number,
  fc: GeoJSON.FeatureCollection | null,
): number {
  if (!fc) return Infinity;
  let minDist = Infinity;

  for (const feature of fc.features) {
    const geom = feature.geometry;
    let rings: Ring[][];

    if (geom.type === 'Polygon') {
      rings = [geom.coordinates as Ring[]];
    } else if (geom.type === 'MultiPolygon') {
      rings = geom.coordinates as Ring[][];
    } else {
      continue;
    }

    // Quick bounding-box filter: skip features that are clearly far away
    // (> ~0.01 degrees ≈ 1km from any coordinate)
    for (const polygon of rings) {
      for (const ring of polygon) {
        for (let i = 0; i < ring.length - 1; i++) {
          const d = distanceToSegment(lat, lon, ring[i], ring[i + 1]);
          if (d < minDist) minDist = d;
        }
      }
    }
  }

  return minDist;
}

/**
 * Find the feature whose polygon contains the given point.
 * Returns the feature or null.
 */
export function findContainingFeature(
  lat: number,
  lon: number,
  fc: GeoJSON.FeatureCollection | null,
): GeoJSON.Feature | null {
  if (!fc) return null;
  for (const feature of fc.features) {
    const t = feature.geometry.type;
    if ((t === 'Polygon' || t === 'MultiPolygon') && pointInPolygon(lat, lon, feature)) {
      return feature;
    }
  }
  return null;
}

/**
 * Haversine distance in km between two points.
 */
export function distanceKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371; // Earth radius in km
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/**
 * Minimum distance in km from a point to a LineString or MultiLineString feature.
 */
export function distanceToLine(lat: number, lon: number, feature: GeoJSON.Feature): number {
  const geom = feature.geometry;
  if (geom.type === 'Point') {
    const coords = geom.coordinates as Position;
    return distanceKm(lat, lon, coords[1], coords[0]);
  }

  let lines: Position[][];
  if (geom.type === 'LineString') {
    lines = [geom.coordinates as Position[]];
  } else if (geom.type === 'MultiLineString') {
    lines = geom.coordinates as Position[][];
  } else {
    return Infinity;
  }

  let minDist = Infinity;
  for (const line of lines) {
    for (let i = 0; i < line.length - 1; i++) {
      const d = distanceToSegment(lat, lon, line[i], line[i + 1]);
      if (d < minDist) {
        minDist = d;
      }
    }
  }
  return minDist;
}

/**
 * Distance from point (lat, lon) to the nearest point on a line segment [A, B].
 * Coordinates are [lon, lat]. Works by projecting onto the segment in a local
 * equirectangular approximation, then computing Haversine to the closest point.
 */
function distanceToSegment(lat: number, lon: number, a: Position, b: Position): number {
  const aLat = a[1], aLon = a[0];
  const bLat = b[1], bLon = b[0];

  // Use equirectangular projection for the parameter t
  const cosLat = Math.cos(toRad(lat));
  const dx = (bLon - aLon) * cosLat;
  const dy = bLat - aLat;
  const px = (lon - aLon) * cosLat;
  const py = lat - aLat;

  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) {
    // Segment is a single point
    return distanceKm(lat, lon, aLat, aLon);
  }

  // Parameter of projection onto the segment, clamped to [0, 1]
  let t = (px * dx + py * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));

  const closestLon = aLon + t * (bLon - aLon);
  const closestLat = aLat + t * (bLat - aLat);

  return distanceKm(lat, lon, closestLat, closestLon);
}

/**
 * Distance from a point to a feature (Point, LineString, or MultiLineString).
 */
function distanceToFeature(lat: number, lon: number, feature: GeoJSON.Feature): number {
  const geom = feature.geometry;
  if (geom.type === 'Point') {
    const coords = geom.coordinates as Position;
    return distanceKm(lat, lon, coords[1], coords[0]);
  }
  if (geom.type === 'LineString' || geom.type === 'MultiLineString') {
    return distanceToLine(lat, lon, feature);
  }
  return Infinity;
}

/**
 * Find the N nearest features by distance (Point, LineString, or MultiLineString).
 * Returns array sorted by distance ascending.
 */
export function findNearest(
  lat: number,
  lon: number,
  fc: GeoJSON.FeatureCollection | null,
  n: number,
): Array<{ feature: GeoJSON.Feature; distance_km: number }> {
  if (!fc) return [];

  const results: Array<{ feature: GeoJSON.Feature; distance_km: number }> = [];
  for (const feature of fc.features) {
    const d = distanceToFeature(lat, lon, feature);
    if (d !== Infinity) {
      results.push({ feature, distance_km: d });
    }
  }

  results.sort((a, b) => a.distance_km - b.distance_km);
  return results.slice(0, n);
}

/**
 * Find all features within a radius (km).
 * Returns array sorted by distance ascending.
 */
export function findWithinRadius(
  lat: number,
  lon: number,
  fc: GeoJSON.FeatureCollection | null,
  radiusKm: number,
): Array<{ feature: GeoJSON.Feature; distance_km: number }> {
  if (!fc) return [];

  const results: Array<{ feature: GeoJSON.Feature; distance_km: number }> = [];
  for (const feature of fc.features) {
    const d = distanceToFeature(lat, lon, feature);
    if (d <= radiusKm) {
      results.push({ feature, distance_km: d });
    }
  }

  results.sort((a, b) => a.distance_km - b.distance_km);
  return results;
}
