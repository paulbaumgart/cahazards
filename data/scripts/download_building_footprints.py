#!/usr/bin/env python3
"""
Download Microsoft Building Footprints for California and compute
Structure Separation Distance (SSD) tiles.

SSD = distance from each building centroid to its nearest neighbor.
This is the #1 predictor of wildfire structure loss (Zamanialaei et al. 2025).

We tile SSD as a spatial raster at 0.01-degree resolution (~1km) storing
the median SSD for all buildings in each cell. The Worker samples this
at query time.

Source: Microsoft USBuildingFootprints v2
https://github.com/microsoft/USBuildingFootprints
"""

import json
import math
import os
import struct
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

URL = "https://minedbuildings.z5.web.core.windows.net/legacy/usbuildings-v2/California.geojson.zip"
ZIP_PATH = "data/raw/California.geojson.zip"
GEOJSON_PATH = "data/raw/California.geojson"
OUT_DIR = "data/tiles/ssd"

# Tile at 0.1 degree (matching other tiles), store median SSD per cell
TILE_DEG = 0.1
# Internal grid resolution within each tile: 40x40 cells
# Each cell is 0.0025 degrees ≈ 250m
CELLS_PER_TILE = 40

CA_LAT_MIN, CA_LAT_MAX = 32.5, 42.0
CA_LON_MIN, CA_LON_MAX = -124.5, -114.0


def download():
    """Download the California footprints zip."""
    os.makedirs(os.path.dirname(ZIP_PATH), exist_ok=True)
    if os.path.exists(ZIP_PATH):
        size_gb = os.path.getsize(ZIP_PATH) / (1024**3)
        print(f"Already downloaded: {ZIP_PATH} ({size_gb:.2f} GB)")
        return

    print(f"Downloading California building footprints (~1GB compressed)...")
    print(f"  URL: {URL}")
    req = urllib.request.Request(URL, headers={"User-Agent": "cahazards/1.0"})

    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:
        total = int(resp.headers.get('Content-Length', 0))
        downloaded = 0
        with open(ZIP_PATH, 'wb') as f:
            while True:
                chunk = resp.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0 and downloaded % (50 * 1024 * 1024) == 0:
                    pct = downloaded / total * 100
                    print(f"  {downloaded/(1024**3):.2f} GB / {total/(1024**3):.2f} GB ({pct:.0f}%)")

    elapsed = time.time() - t0
    size_gb = os.path.getsize(ZIP_PATH) / (1024**3)
    print(f"  Done in {elapsed:.0f}s ({size_gb:.2f} GB)")


def extract_centroids():
    """Stream-parse the GeoJSON to extract building centroids.

    The file is too large to load into memory. We parse line-by-line
    looking for coordinate arrays and compute centroids on the fly.
    """
    # First check if we have the extracted geojson
    if not os.path.exists(GEOJSON_PATH):
        print("Extracting zip...")
        with zipfile.ZipFile(ZIP_PATH) as zf:
            # Find the geojson file inside
            names = zf.namelist()
            geojson_name = [n for n in names if n.endswith('.geojson')][0]
            print(f"  Extracting {geojson_name}...")
            zf.extract(geojson_name, 'data/raw/')
            extracted = os.path.join('data/raw', geojson_name)
            if extracted != GEOJSON_PATH:
                os.rename(extracted, GEOJSON_PATH)

    print(f"Extracting centroids from {GEOJSON_PATH}...")
    centroids = []
    count = 0
    t0 = time.time()

    # Use ogr2ogr if available for speed, otherwise fall back to manual parsing
    try:
        import geopandas as gpd
        print("  Using geopandas (this will use ~8GB RAM)...")
        gdf = gpd.read_file(GEOJSON_PATH)
        centroids_geom = gdf.geometry.centroid
        centroids = np.column_stack([centroids_geom.y, centroids_geom.x])
        count = len(centroids)
    except MemoryError:
        print("  Out of memory with geopandas, falling back to streaming parser...")
        centroids = stream_parse_centroids(GEOJSON_PATH)
        count = len(centroids)

    elapsed = time.time() - t0
    print(f"  Extracted {count:,} centroids in {elapsed:.0f}s")
    return centroids


def stream_parse_centroids(path):
    """Memory-efficient centroid extraction using ijson or manual parsing."""
    centroids = []
    import re

    coord_pattern = re.compile(r'\[(-?\d+\.\d+),\s*(-?\d+\.\d+)\]')

    with open(path, 'r') as f:
        in_feature = False
        coords_text = ""
        for line_num, line in enumerate(f):
            if '"type":"Feature"' in line or '"type": "Feature"' in line:
                in_feature = True
                coords_text = ""
            if in_feature:
                coords_text += line
            if in_feature and '}' in line and line.strip().rstrip(',') == '}':
                # Try to extract coordinates
                matches = coord_pattern.findall(coords_text)
                if matches:
                    lons = [float(m[0]) for m in matches]
                    lats = [float(m[1]) for m in matches]
                    centroids.append([np.mean(lats), np.mean(lons)])
                in_feature = False
                coords_text = ""

                if len(centroids) % 1000000 == 0:
                    print(f"    {len(centroids):,} centroids...")

    return np.array(centroids)


def compute_ssd_tiles(centroids):
    """Compute median SSD per grid cell and write tiles."""
    from scipy.spatial import cKDTree

    print(f"Building KD-tree for {len(centroids):,} centroids...")
    t0 = time.time()
    tree = cKDTree(centroids)
    print(f"  KD-tree built in {time.time()-t0:.0f}s")

    # Find nearest neighbor for each centroid (k=2, first is self)
    print("Computing nearest-neighbor distances...")
    t0 = time.time()
    # Process in chunks to manage memory
    chunk_size = 500000
    ssd_m = np.zeros(len(centroids), dtype=np.float32)

    for start in range(0, len(centroids), chunk_size):
        end = min(start + chunk_size, len(centroids))
        dists, _ = tree.query(centroids[start:end], k=2)
        # k=2: first neighbor is self (dist=0), second is nearest other
        # Convert degrees to approximate meters (at CA latitude ~37N)
        deg_to_m = 111000 * np.cos(np.radians(centroids[start:end, 0]))
        ssd_m[start:end] = dists[:, 1] * deg_to_m
        if start % 2000000 == 0:
            print(f"    {start:,}/{len(centroids):,}...")

    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.0f}s")
    print(f"  SSD stats: median={np.median(ssd_m):.1f}m, "
          f"mean={np.mean(ssd_m):.1f}m, "
          f"p10={np.percentile(ssd_m, 10):.1f}m, "
          f"p90={np.percentile(ssd_m, 90):.1f}m")

    # Tile: for each 0.1-degree tile, compute median SSD in a 40x40 grid
    os.makedirs(OUT_DIR, exist_ok=True)
    cell_size = TILE_DEG / CELLS_PER_TILE  # 0.0025 degrees

    tiles_written = 0
    total_bytes = 0

    lats = np.arange(CA_LAT_MIN, CA_LAT_MAX, TILE_DEG)
    lons = np.arange(CA_LON_MIN, CA_LON_MAX, TILE_DEG)

    for tile_lat in lats:
        for tile_lon in lons:
            # Find centroids in this tile
            mask = (
                (centroids[:, 0] >= tile_lat) & (centroids[:, 0] < tile_lat + TILE_DEG) &
                (centroids[:, 1] >= tile_lon) & (centroids[:, 1] < tile_lon + TILE_DEG)
            )
            if not mask.any():
                continue

            tile_centroids = centroids[mask]
            tile_ssd = ssd_m[mask]

            # Bin into grid cells and compute median SSD per cell
            grid = np.full((CELLS_PER_TILE, CELLS_PER_TILE), 0, dtype=np.uint16)

            rows = ((tile_centroids[:, 0] - tile_lat) / cell_size).astype(int)
            cols = ((tile_centroids[:, 1] - tile_lon) / cell_size).astype(int)
            rows = np.clip(rows, 0, CELLS_PER_TILE - 1)
            cols = np.clip(cols, 0, CELLS_PER_TILE - 1)

            for r in range(CELLS_PER_TILE):
                for c in range(CELLS_PER_TILE):
                    cell_mask = (rows == r) & (cols == c)
                    if cell_mask.any():
                        # Store median SSD in meters, uint16 (max 65535m = 65km)
                        grid[r, c] = min(int(np.median(tile_ssd[cell_mask])), 65535)

            if grid.max() == 0:
                continue

            # Write tile (same format as other uint16 tiles)
            tile_name = f"{round(tile_lat, 1)}_{round(tile_lon, 1)}"
            bin_path = os.path.join(OUT_DIR, f"{tile_name}.bin")
            json_path = os.path.join(OUT_DIR, f"{tile_name}.json")

            grid.tofile(bin_path)
            sidecar = {
                "rows": CELLS_PER_TILE,
                "cols": CELLS_PER_TILE,
                "bounds": {
                    "north": round(tile_lat + TILE_DEG, 4),
                    "south": round(tile_lat, 4),
                    "west": round(tile_lon, 4),
                    "east": round(tile_lon + TILE_DEG, 4),
                },
                "units": "ssd_meters",
                "dtype": "uint16",
                "description": "Median Structure Separation Distance (meters) per 250m cell",
            }
            with open(json_path, 'w') as f:
                json.dump(sidecar, f)

            total_bytes += os.path.getsize(bin_path)
            tiles_written += 1

    print(f"\nWrote {tiles_written} SSD tiles to {OUT_DIR}")
    print(f"Total size: {total_bytes / (1024*1024):.1f} MB")


def main():
    download()
    centroids = extract_centroids()
    if isinstance(centroids, list):
        centroids = np.array(centroids)
    compute_ssd_tiles(centroids)


if __name__ == '__main__':
    main()
