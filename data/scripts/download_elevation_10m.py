#!/usr/bin/env python3
"""
Download 3DEP elevation at ~10m resolution from the USGS ImageServer,
then tile into 0.1-degree binary grids for the Worker.

Strategy:
  - Download 0.2-degree chunks (2160x2160 px) from the ImageServer
  - Reproject from EPSG:3857 to EPSG:4326
  - Split each chunk into 4 tiles of 0.1 degrees
  - Write elevation + slope as float32 binary tiles with JSON sidecars
  - 5 parallel downloads
"""
import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
import urllib.request

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling, calculate_default_transform
from rasterio.transform import from_bounds

BASE_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
CHUNK_DEG = 0.2
CHUNK_PX = 2160  # ~10m at 1/3 arc-second
TILE_DEG = 0.1
NODATA = -9999.0

CA_LAT_MIN, CA_LAT_MAX = 32.4, 42.0
CA_LON_MIN, CA_LON_MAX = -124.6, -114.0


def fetch_chunk(lat_s, lon_w):
    """Download a 0.2-degree chunk from ImageServer. Returns raw TIFF bytes or None."""
    bbox = f"{lon_w},{lat_s},{lon_w + CHUNK_DEG},{lat_s + CHUNK_DEG}"
    url = (f"{BASE_URL}?bbox={bbox}&bboxSR=4326"
           f"&size={CHUNK_PX},{CHUNK_PX}&format=tiff&pixelType=F32"
           f"&interpolation=RSP_BilinearInterpolation&f=image")
    req = urllib.request.Request(url, headers={"User-Agent": "cahazards/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        data = resp.read()
        return data if len(data) > 5000 else None
    except Exception:
        return None


def reproject_to_4326(tiff_bytes, target_south, target_west, target_north, target_east):
    """Reproject a Web Mercator TIFF to EPSG:4326 and return as numpy array."""
    with rasterio.open(BytesIO(tiff_bytes)) as src:
        arr = src.read(1).astype(np.float32)
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan

        # Target grid: 0.2 degrees at ~10m = ~2160 pixels
        dst_height = CHUNK_PX
        dst_width = CHUNK_PX
        dst_transform = from_bounds(
            target_west, target_south, target_east, target_north,
            dst_width, dst_height
        )
        dst = np.full((dst_height, dst_width), np.nan, dtype=np.float32)
        reproject(
            source=arr,
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs="EPSG:4326",
            resampling=Resampling.bilinear,
            src_nodata=np.nan,
            dst_nodata=np.nan,
        )
        return dst


def compute_slope(elev, lat_center):
    """Compute slope in degrees. Cell size varies with latitude."""
    cell_y = TILE_DEG / elev.shape[0] * 111000  # meters
    cell_x = TILE_DEG / elev.shape[1] * 111000 * math.cos(math.radians(lat_center))
    dy, dx = np.gradient(elev, cell_y, cell_x)
    slope = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
    slope[np.isnan(elev)] = np.nan
    return slope


def write_tile(data, path, south, west):
    """Write float32 binary tile with JSON sidecar."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows, cols = data.shape
    clean = np.where(np.isnan(data), NODATA, data).astype(np.float32)
    with open(path, "wb") as f:
        f.write(clean.tobytes(order="C"))
    sidecar = {
        "rows": rows, "cols": cols,
        "bounds": {
            "north": round(south + TILE_DEG, 4),
            "south": round(south, 4),
            "west": round(west, 4),
            "east": round(west + TILE_DEG, 4),
        },
        "nodata": NODATA, "dtype": "float32", "byte_order": "little-endian",
    }
    with open(path.with_suffix(".json"), "w") as f:
        json.dump(sidecar, f)


def process_chunk(lat_s, lon_w, elev_dir, slope_dir):
    """Download one chunk, split into 0.1-degree tiles, write elevation + slope."""
    # Check if all 4 output tiles already exist
    tiles_needed = []
    for dlat in [0.0, 0.1]:
        for dlon in [0.0, 0.1]:
            ts = round(lat_s + dlat, 1)
            tw = round(lon_w + dlon, 1)
            name = f"{ts}_{tw}"
            if not (elev_dir / f"{name}.bin").exists():
                tiles_needed.append((ts, tw, dlat, dlon))
    if not tiles_needed:
        return 0

    tiff_bytes = fetch_chunk(lat_s, lon_w)
    if tiff_bytes is None:
        return 0

    # Reproject to EPSG:4326
    arr = reproject_to_4326(tiff_bytes, lat_s, lon_w, lat_s + CHUNK_DEG, lon_w + CHUNK_DEG)
    if arr is None or np.all(np.isnan(arr)):
        return 0

    rows, cols = arr.shape
    half_r, half_c = rows // 2, cols // 2
    written = 0

    for ts, tw, dlat, dlon in tiles_needed:
        # Extract sub-tile
        r0 = 0 if dlat == 0.1 else half_r  # top half = higher lat = dlat=0.1
        c0 = 0 if dlon == 0.0 else half_c
        sub = arr[r0:r0 + half_r, c0:c0 + half_c].copy()

        # Flip: rasterio gives row 0 = north, we want row 0 = south
        sub = np.flipud(sub)

        if np.all(np.isnan(sub)):
            continue

        name = f"{ts}_{tw}"
        write_tile(sub, elev_dir / f"{name}.bin", ts, tw)

        slope = compute_slope(sub, ts + TILE_DEG / 2)
        write_tile(slope, slope_dir / f"{name}.bin", ts, tw)
        written += 1

    return written


def main():
    parser = argparse.ArgumentParser(description="Download 3DEP at 10m and tile")
    parser.add_argument("--output-dir", default="data/tiles")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    elev_dir = Path(args.output_dir) / "elevation"
    slope_dir = Path(args.output_dir) / "slope"
    elev_dir.mkdir(parents=True, exist_ok=True)
    slope_dir.mkdir(parents=True, exist_ok=True)

    # Build chunk grid
    chunks = []
    for lat_s in np.arange(CA_LAT_MIN, CA_LAT_MAX, CHUNK_DEG):
        for lon_w in np.arange(CA_LON_MIN, CA_LON_MAX, CHUNK_DEG):
            chunks.append((round(lat_s, 1), round(lon_w, 1)))

    print(f"Total chunks: {len(chunks)} ({CHUNK_DEG}° each, {CHUNK_PX}px)")
    print(f"Workers: {args.workers}")
    t0 = time.time()
    written = 0
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_chunk, lat, lon, elev_dir, slope_dir): (lat, lon)
            for lat, lon in chunks
        }
        for future in as_completed(futures):
            n = future.result()
            written += n
            done += 1
            if done % 50 == 0:
                elapsed = time.time() - t0
                print(f"  {done}/{len(chunks)}: {written} tiles, {elapsed:.0f}s", flush=True)

    elapsed = time.time() - t0
    elev_mb = sum(f.stat().st_size for f in elev_dir.glob("*.bin")) / (1024 * 1024)
    slope_mb = sum(f.stat().st_size for f in slope_dir.glob("*.bin")) / (1024 * 1024)
    print(f"\nDone: {written} tiles in {elapsed:.0f}s")
    print(f"Elevation: {elev_mb:.0f} MB")
    print(f"Slope: {slope_mb:.0f} MB")


if __name__ == "__main__":
    main()
