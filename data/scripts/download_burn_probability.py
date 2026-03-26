#!/usr/bin/env python3
"""
Download USFS FSim burn probability data from ImageServer and tile at 0.1 degrees.

Source: USFS Wildfire Risk to Communities (WRC) Burn Probability 2024
  https://imagery.geoplatform.gov/iipp/rest/services/Fire_Aviation/USFS_EDW_RMRS_WRC_BurnProbability/ImageServer

The raster values are uint16 representing annual burn probability * 100000
(Scott et al. 2020, "Wildfire Risk to Communities"). So value 100 = 0.1% annual BP.

We store the raw uint16 values. The Worker divides by 100000 at query time.
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
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds

BASE_URL = "https://imagery.geoplatform.gov/iipp/rest/services/Fire_Aviation/USFS_EDW_RMRS_WRC_BurnProbability/ImageServer/exportImage"

CA_LAT_MIN, CA_LAT_MAX = 32.5, 42.0
CA_LON_MIN, CA_LON_MAX = -124.5, -114.0
TILE_DEG = 0.1
# 30m native resolution — request at that resolution
# 0.1 degree ≈ 11.1km lat, ~8.9km lon at 37N
# At 30m: ~370 rows, ~297 cols per tile. Use 400x400 to be safe.
TILE_PX = 400


def fetch_tile(lat_south, lon_west):
    bbox = f"{lon_west},{lat_south},{lon_west + TILE_DEG},{lat_south + TILE_DEG}"
    url = (f"{BASE_URL}?bbox={bbox}&bboxSR=4326"
           f"&size={TILE_PX},{TILE_PX}&format=tiff&pixelType=U16&f=image")
    req = urllib.request.Request(url, headers={"User-Agent": "cahazards/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        return data if len(data) > 1000 else None
    except Exception:
        return None


def write_tile(data, path, lat_south, lon_west, rows, cols):
    """Write uint16 tile with JSON sidecar (same format as elevation)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data.tofile(str(path))
    sidecar = {
        "rows": rows, "cols": cols,
        "bounds": {
            "north": round(lat_south + TILE_DEG, 4),
            "south": round(lat_south, 4),
            "west": round(lon_west, 4),
            "east": round(lon_west + TILE_DEG, 4),
        },
        "scale": 100000,
        "units": "annual_burn_probability",
        "dtype": "uint16",
    }
    with open(path.with_suffix(".json"), "w") as f:
        json.dump(sidecar, f)


def process_tile(lat_s, lon_w, out_dir):
    tile_name = f"{lat_s}_{lon_w}"
    out_path = out_dir / f"{tile_name}.bin"
    if out_path.exists() and out_path.stat().st_size > 100:
        return 0

    tiff_data = fetch_tile(lat_s, lon_w)
    if tiff_data is None:
        return 0

    with rasterio.open(BytesIO(tiff_data)) as src:
        arr = src.read(1)
        if src.crs and src.crs.to_epsg() != 4326:
            # Reproject from Web Mercator
            dst = np.zeros_like(arr)
            dst_transform = from_bounds(lon_w, lat_s, lon_w + TILE_DEG, lat_s + TILE_DEG,
                                        arr.shape[1], arr.shape[0])
            reproject(arr, dst,
                      src_transform=src.transform, src_crs=src.crs,
                      dst_transform=dst_transform, dst_crs="EPSG:4326",
                      resampling=Resampling.nearest)
            arr = dst

    # Flip: rasterio row 0 = north, we want row 0 = south
    arr = np.flipud(arr).astype(np.uint16)

    if arr.max() == 0:
        return 0

    write_tile(arr, out_path, lat_s, lon_w, arr.shape[0], arr.shape[1])
    return 1


def main():
    parser = argparse.ArgumentParser(description="Download FSim burn probability tiles")
    parser.add_argument("--output-dir", default="data/tiles/burn_probability")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for lat in np.arange(CA_LAT_MIN, CA_LAT_MAX, TILE_DEG):
        for lon in np.arange(CA_LON_MIN, CA_LON_MAX, TILE_DEG):
            jobs.append((round(lat, 1), round(lon, 1)))

    print(f"Total tiles: {len(jobs)}, workers: {args.workers}")
    t0 = time.time()
    written = 0
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_tile, lat, lon, out_dir): (lat, lon) for lat, lon in jobs}
        for future in as_completed(futures):
            n = future.result()
            written += n
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)}: {written} tiles, {time.time()-t0:.0f}s", flush=True)

    print(f"\nDone: {written} tiles in {time.time()-t0:.0f}s")
    total_mb = sum(f.stat().st_size for f in out_dir.glob("*.bin")) / (1024*1024)
    print(f"Total: {total_mb:.0f} MB")


if __name__ == "__main__":
    main()
