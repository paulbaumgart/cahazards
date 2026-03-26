#!/usr/bin/env python3
"""
Spatial tiling script for California hazards data pipeline.

Cuts all processed GeoJSON files into 0.1-degree spatial tiles for R2 storage.
The Cloudflare Worker fetches only the tiles it needs per request.

Key design decision: when a polygon spans multiple tiles, each tile gets the
FULL polygon (not clipped). This ensures point-in-polygon tests work correctly
at tile boundaries. Tiles near boundaries will be larger, but correctness > size.

Point datasets (contamination, airports) use a coarser 0.5-degree grid and
include features from adjacent cells within a search radius.
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box

# California bounding box (0.1-degree grid)
CA_LAT_MIN = 32.5
CA_LAT_MAX = 42.0
CA_LON_MIN = -124.5
CA_LON_MAX = -114.0

# All polygon/zone datasets are tiled as vector GeoJSON for exact boundaries.
# Only continuous raster data (elevation, burn probability, Vs30) is handled separately.
VECTOR_DATASETS = [
    "faults",
    "dam_inundation",
    "erosion",
    "airports",
    "traffic",
    "calenviroscreen",
    "contamination",
    "flood_zones",
    "tsunami",
    "landslide",
    "liquefaction",
    "fire_zones",
    "soils",
    "slr",
    "landslide_inventory",
    "landslide_supplemental",
    "census_tracts",
]

# Raster data handled by separate scripts (not tiled here)
SKIP_DATASETS = {"elevation", "vs30", "slope", "burn_probability"}

# Datasets that use coarser 0.5-degree tiles.
# - contamination/airports: point data with search radius
# - faults: the earthquake model searches up to 50km (~0.5 degrees),
#   so a single 0.5-degree tile captures the full search radius.
COARSE_DATASETS = {"contamination", "airports", "faults"}
POINT_TILE_SIZE = 0.5  # degrees
POINT_SEARCH_RADIUS = 0.5  # degrees -- include features from adjacent cells


def generate_grid_cells(tile_size, lat_min=CA_LAT_MIN, lat_max=CA_LAT_MAX,
                        lon_min=CA_LON_MIN, lon_max=CA_LON_MAX):
    """Generate grid cell origins (lat, lon) for the given tile size."""
    lats = np.arange(lat_min, lat_max, tile_size)
    lons = np.arange(lon_min, lon_max, tile_size)
    cells = []
    for lat in lats:
        for lon in lons:
            cells.append((round(lat, 4), round(lon, 4)))
    return cells


def tile_dataset(args):
    """
    Tile a single dataset. Designed to run in a subprocess.

    args: (dataset_name, input_path, output_dir, tile_size, is_point_data)
    Returns: list of (relative_path, file_size) for manifest
    """
    dataset_name, input_path, output_dir, tile_size, is_point_data = args

    try:
        gdf = gpd.read_file(input_path)
    except Exception as e:
        print(f"  ERROR loading {dataset_name}: {e}", flush=True)
        return []

    if gdf.empty:
        print(f"  SKIP {dataset_name}: empty dataset", flush=True)
        return []

    # Ensure CRS is WGS84
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    # Build spatial index
    sindex = gdf.sindex

    # Choose grid parameters
    if is_point_data:
        effective_tile_size = POINT_TILE_SIZE
        search_buffer = POINT_SEARCH_RADIUS
    else:
        effective_tile_size = tile_size
        search_buffer = 0.0

    cells = generate_grid_cells(effective_tile_size)
    dataset_dir = os.path.join(output_dir, dataset_name)
    os.makedirs(dataset_dir, exist_ok=True)

    manifest_entries = []
    tiles_written = 0

    for lat, lon in cells:
        # Query box: the cell itself, optionally expanded by search buffer
        query_box = box(
            lon - search_buffer,
            lat - search_buffer,
            lon + effective_tile_size + search_buffer,
            lat + effective_tile_size + search_buffer,
        )

        # Use spatial index to find candidate features
        candidate_idx = list(sindex.intersection(query_box.bounds))
        if not candidate_idx:
            continue

        candidates = gdf.iloc[candidate_idx]

        # Filter to features that actually intersect the query box
        mask = candidates.intersects(query_box)
        tile_features = candidates[mask]

        if tile_features.empty:
            continue

        # Write tile -- keep FULL geometries (no clipping)
        tile_path = os.path.join(dataset_dir, f"{lat}_{lon}.json")
        tile_features.to_file(tile_path, driver="GeoJSON")

        file_size = os.path.getsize(tile_path)
        rel_path = f"{dataset_name}/{lat}_{lon}.json"
        manifest_entries.append((rel_path, file_size))
        tiles_written += 1

    print(f"  {dataset_name}: {tiles_written} tiles, "
          f"{len(gdf)} features, tile_size={effective_tile_size}deg", flush=True)

    return manifest_entries


def find_datasets(input_dir):
    """Find all processable GeoJSON files in the input directory."""
    datasets = []
    for name in VECTOR_DATASETS:
        # Try common naming patterns (exact match, then prefix match)
        candidates = [
            os.path.join(input_dir, f"{name}.geojson"),
            os.path.join(input_dir, f"{name}.json"),
            os.path.join(input_dir, name, f"{name}.geojson"),
        ]
        found = None
        for c in candidates:
            if os.path.exists(c):
                found = c
                break
        # Try prefix match (e.g., "contamination" matches "contamination_sites.geojson")
        if not found:
            import glob as globmod
            prefix_matches = sorted(globmod.glob(os.path.join(input_dir, f"{name}*.geojson")))
            if prefix_matches:
                found = prefix_matches[0]
        if found:
            datasets.append((name, found))
        else:
            print(f"  WARN: {name} not found in {input_dir}", flush=True)
    return datasets


def main():
    parser = argparse.ArgumentParser(
        description="Tile processed GeoJSON into spatial grid cells for R2 storage."
    )
    parser.add_argument(
        "--input-dir", default="data/processed",
        help="Directory containing processed GeoJSON files (default: data/processed)",
    )
    parser.add_argument(
        "--output-dir", default="data/tiles",
        help="Output directory for tiles (default: data/tiles)",
    )
    parser.add_argument(
        "--tile-size", type=float, default=0.1,
        help="Tile size in degrees (default: 0.1). Point datasets always use 0.5.",
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Number of parallel workers (default: CPU count)",
    )
    parser.add_argument(
        "--dataset", type=str, nargs="+", default=None,
        help="Process only these datasets (for debugging). Can specify multiple.",
    )
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Input:  {input_dir}")
    print(f"Output: {output_dir}")
    print(f"Tile size: {args.tile_size} degrees (point data: {POINT_TILE_SIZE} degrees)")
    print()

    # Discover datasets
    datasets = find_datasets(input_dir)
    if args.dataset:
        selected = set(args.dataset)
        datasets = [(n, p) for n, p in datasets if n in selected]

    if not datasets:
        print("No datasets found. Check --input-dir path.")
        sys.exit(1)

    print(f"Found {len(datasets)} datasets to tile:")
    for name, path in datasets:
        print(f"  {name}: {path}")
    print()

    # Build work items
    work_items = []
    for name, path in datasets:
        is_point = name in COARSE_DATASETS
        work_items.append((name, path, output_dir, args.tile_size, is_point))

    # Process datasets in parallel
    t0 = time.time()
    all_manifest_entries = []

    print("Tiling datasets...")
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(tile_dataset, item): item[0]
            for item in work_items
        }
        for future in as_completed(futures):
            dataset_name = futures[future]
            try:
                entries = future.result()
                all_manifest_entries.extend(entries)
            except Exception as e:
                print(f"  FAILED {dataset_name}: {e}", flush=True)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")

    # Write manifest
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tile_size_default": args.tile_size,
        "tile_size_point": POINT_TILE_SIZE,
        "total_tiles": len(all_manifest_entries),
        "total_size_bytes": sum(s for _, s in all_manifest_entries),
        "tiles": {
            path: size for path, size in sorted(all_manifest_entries)
        },
    }

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    total_mb = manifest["total_size_bytes"] / (1024 * 1024)
    print(f"\nManifest: {manifest_path}")
    print(f"Total: {manifest['total_tiles']} tiles, {total_mb:.1f} MB")


if __name__ == "__main__":
    main()
