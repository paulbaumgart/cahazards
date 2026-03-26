#!/usr/bin/env python3
"""
Compute FSim burn probability calibration factors from CalFire FRAP perimeters.

Compares observed fire frequency from CalFire FRAP historical fire perimeters
(1996-2025) against USFS FSim modeled burn probability (Scott et al. 2020,
"Wildfire Risk to Communities") to derive regional calibration factors on a
0.5-degree grid covering California.

Sources:
  - CalFire FRAP Fire Perimeters (1996-2025):
    https://frap.fire.ca.gov/frap-projects/fire-perimeters/
    GIS dataset of wildfire and prescribed fire perimeters in California.
  - USFS FSim Burn Probability (WRC 2024):
    https://wildfirerisk.org/
    Scott, J.H., Thompson, M.P., Calkin, D.E. (2013). A wildfire risk
    assessment framework for land and resource management. USDA Forest
    Service RMRS-GTR-315.

Algorithm:
  1. Load CalFire perimeters for the observation window (default 1996-2025).
  2. Create a 0.5-degree grid covering California (19 lat x 21 lon cells).
  3. For each grid cell, rasterize fire perimeters at ~100m resolution to
     compute observed annual burn frequency, and average FSim tile pixels
     to get modeled burn probability.
  4. Calibration factor = observed / modeled, clamped to [0.5, 20].
  5. Smooth with 3x3 uniform filter to reduce noise from small-sample cells.
  6. Fill cells with no data using the statewide median factor.

Output: data/processed/fsim_calibration_factors.json
"""

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box
from scipy.ndimage import uniform_filter

# Grid parameters
GRID_RES = 0.5
LAT_MIN, LAT_MAX = 32.5, 42.0
LON_MIN, LON_MAX = -124.5, -114.0
N_LAT = int(round((LAT_MAX - LAT_MIN) / GRID_RES))  # 19
N_LON = int(round((LON_MAX - LON_MIN) / GRID_RES))  # 21

# FSim tile parameters
TILE_DEG = 0.1
TILE_PX = 400
TILE_SCALE = 100000  # uint16 value / TILE_SCALE = annual BP

# Rasterization resolution for observed frequency (~100m in degrees at ~37N)
OBS_RES_DEG = 0.001  # ~111m lat, ~88m lon at 37N

# Calibration factor clamp range. FSim is a national model that can
# systematically under- or over-predict in specific regions, but factors
# outside this range likely reflect data artifacts (e.g., tiny cells with
# one large fire, or cells with near-zero FSim values).
FACTOR_MIN = 0.5
FACTOR_MAX = 20.0

# Default paths (relative to repo root)
DEFAULT_PERIMETERS = "data/raw/calfire_perimeters/ca_fire_perimeters_1996_2025.geojson"
DEFAULT_TILES_DIR = "data/tiles/burn_probability"
DEFAULT_OUTPUT = "data/processed/fsim_calibration_factors.json"


def load_perimeters(path: Path, year_min: int, year_max: int) -> gpd.GeoDataFrame:
    """Load CalFire perimeters and filter to the observation window."""
    print(f"Loading perimeters from {path}...")
    gdf = gpd.read_file(path)
    print(f"  Loaded {len(gdf)} total perimeters")

    # Filter by year
    gdf["YEAR_"] = gdf["YEAR_"].astype(int)
    gdf = gdf[(gdf["YEAR_"] >= year_min) & (gdf["YEAR_"] <= year_max)]
    print(f"  {len(gdf)} perimeters in {year_min}-{year_max}")

    # Ensure WGS84
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    return gdf


def compute_observed_frequency(
    perimeters: gpd.GeoDataFrame,
    lat_south: float,
    lon_west: float,
    n_years: int,
) -> float | None:
    """
    Compute observed annual burn frequency in a grid cell by rasterizing
    fire perimeters at ~100m resolution.

    Returns the fraction of pixels burned per year, averaged over n_years.
    Returns None if the cell has no burnable area (e.g., ocean).
    """
    cell_box = box(lon_west, lat_south, lon_west + GRID_RES, lat_south + GRID_RES)

    # Clip perimeters to this cell
    cell_perimeters = perimeters[perimeters.intersects(cell_box)]
    if len(cell_perimeters) == 0:
        return 0.0

    # Rasterize: for each year, mark which pixels burned
    n_x = int(round(GRID_RES / OBS_RES_DEG))
    n_y = int(round(GRID_RES / OBS_RES_DEG))
    total_burned_pixels = 0

    # Group by year to handle overlapping fires within a year
    # (a pixel can only burn once per year for frequency purposes)
    for year, year_group in cell_perimeters.groupby("YEAR_"):
        burned = np.zeros((n_y, n_x), dtype=bool)
        for geom in year_group.geometry:
            if geom is None or geom.is_empty:
                continue
            clipped = geom.intersection(cell_box)
            if clipped.is_empty:
                continue
            # Rasterize this polygon
            _rasterize_polygon(burned, clipped, lat_south, lon_west, n_x, n_y)
        total_burned_pixels += burned.sum()

    total_pixels = n_x * n_y
    # Annual frequency = total burned pixels / (total pixels * n_years)
    return total_burned_pixels / (total_pixels * n_years)


def _rasterize_polygon(
    grid: np.ndarray,
    geom,
    lat_south: float,
    lon_west: float,
    n_x: int,
    n_y: int,
):
    """
    Simple rasterization: mark grid cells whose centers fall inside the polygon.
    Uses a bounding-box filter for efficiency.
    """
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.prepared import prep

    minx, miny, maxx, maxy = geom.bounds

    # Convert bounds to pixel indices
    col_min = max(0, int((minx - lon_west) / OBS_RES_DEG))
    col_max = min(n_x - 1, int((maxx - lon_west) / OBS_RES_DEG))
    row_min = max(0, int((miny - lat_south) / OBS_RES_DEG))
    row_max = min(n_y - 1, int((maxy - lat_south) / OBS_RES_DEG))

    if col_min > col_max or row_min > row_max:
        return

    prepared = prep(geom)

    # Generate center coordinates for the bounding box region
    cols = np.arange(col_min, col_max + 1)
    rows = np.arange(row_min, row_max + 1)
    cx = lon_west + (cols + 0.5) * OBS_RES_DEG
    cy = lat_south + (rows + 0.5) * OBS_RES_DEG

    # For small regions, use vectorized point-in-polygon
    from shapely.geometry import Point

    for ri, r in enumerate(rows):
        for ci, c in enumerate(cols):
            if not grid[r, c]:
                if prepared.contains(Point(cx[ci], cy[ri])):
                    grid[r, c] = True


def load_fsim_mean_bp(tiles_dir: Path, lat_south: float, lon_west: float) -> float | None:
    """
    Compute mean FSim burn probability across all tiles within a grid cell.

    Returns None if no tiles exist for this cell.
    """
    bp_sum = 0.0
    bp_count = 0

    # Iterate over 0.1-degree tiles within the 0.5-degree cell
    n_tiles = int(round(GRID_RES / TILE_DEG))
    for i in range(n_tiles):
        for j in range(n_tiles):
            tile_lat = round(lat_south + i * TILE_DEG, 1)
            tile_lon = round(lon_west + j * TILE_DEG, 1)
            tile_path = tiles_dir / f"{tile_lat}_{tile_lon}.bin"

            if not tile_path.exists():
                continue

            data = np.fromfile(tile_path, dtype=np.uint16)
            if len(data) != TILE_PX * TILE_PX:
                continue

            # Convert to annual burn probability
            # 0 means no data or zero BP; include zeros in the average
            # since they represent areas with no modeled fire risk
            bp_sum += data.astype(np.float64).sum() / TILE_SCALE
            bp_count += len(data)

    if bp_count == 0:
        return None

    return bp_sum / bp_count


def main():
    parser = argparse.ArgumentParser(
        description="Compute FSim calibration factors from CalFire FRAP perimeters"
    )
    parser.add_argument(
        "--perimeters",
        type=Path,
        default=Path(DEFAULT_PERIMETERS),
        help="Path to CalFire fire perimeters GeoJSON",
    )
    parser.add_argument(
        "--tiles-dir",
        type=Path,
        default=Path(DEFAULT_TILES_DIR),
        help="Directory containing burn probability tiles",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(DEFAULT_OUTPUT),
        help="Output JSON path",
    )
    parser.add_argument(
        "--year-min",
        type=int,
        default=1996,
        help="Start year for observation window (inclusive)",
    )
    parser.add_argument(
        "--year-max",
        type=int,
        default=2025,
        help="End year for observation window (inclusive)",
    )
    args = parser.parse_args()

    n_years = args.year_max - args.year_min + 1
    print(f"Observation window: {args.year_min}-{args.year_max} ({n_years} years)")

    # Load perimeters
    perimeters = load_perimeters(args.perimeters, args.year_min, args.year_max)

    # Build spatial index for fast intersection queries
    perimeters_sindex = perimeters.sindex  # noqa: F841 — triggers index build

    # Compute raw calibration factors
    raw_factors = np.full((N_LAT, N_LON), np.nan)
    observed_grid = np.full((N_LAT, N_LON), np.nan)
    fsim_grid = np.full((N_LAT, N_LON), np.nan)

    for i in range(N_LAT):
        lat = LAT_MIN + i * GRID_RES
        for j in range(N_LON):
            lon = LON_MIN + j * GRID_RES
            label = f"[{lat:.1f}, {lon:.1f}]"

            obs = compute_observed_frequency(perimeters, lat, lon, n_years)
            fsim = load_fsim_mean_bp(args.tiles_dir, lat, lon)

            if obs is not None:
                observed_grid[i, j] = obs
            if fsim is not None:
                fsim_grid[i, j] = fsim

            if fsim is not None and fsim > 0 and obs is not None:
                factor = obs / fsim
                factor = np.clip(factor, FACTOR_MIN, FACTOR_MAX)
                raw_factors[i, j] = factor
                print(f"  {label}  obs={obs:.6f}  fsim={fsim:.6f}  factor={factor:.2f}")
            else:
                print(f"  {label}  obs={obs}  fsim={fsim}  factor=N/A")

        print(f"Row {i+1}/{N_LAT} complete")

    # Statewide median factor for filling gaps
    valid_factors = raw_factors[~np.isnan(raw_factors)]
    if len(valid_factors) == 0:
        print("ERROR: No valid calibration factors computed", file=sys.stderr)
        sys.exit(1)

    median_factor = float(np.median(valid_factors))
    print(f"\nStatewide median factor: {median_factor:.4f}")
    print(f"Valid cells: {len(valid_factors)} / {N_LAT * N_LON}")

    # Fill NaN cells with median before smoothing
    filled = raw_factors.copy()
    filled[np.isnan(filled)] = median_factor

    # Smooth with 3x3 uniform filter to reduce cell-to-cell noise
    smoothed = uniform_filter(filled, size=3, mode="nearest")

    # Report statistics
    print(f"\nSmoothed factor range: {smoothed.min():.4f} - {smoothed.max():.4f}")
    print(f"Smoothed factor mean:  {smoothed.mean():.4f}")

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "description": (
            "FSim burn probability calibration factors on 0.5-degree grid. "
            "Multiply FSim BP by this factor to match observed CalFire frequency "
            f"({args.year_min}-{args.year_max}). "
            "Smoothed with 3x3 uniform filter."
        ),
        "grid_res": GRID_RES,
        "lat_min": LAT_MIN,
        "lon_min": LON_MIN,
        "n_lat": N_LAT,
        "n_lon": N_LON,
        "factors": smoothed.tolist(),
    }

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
