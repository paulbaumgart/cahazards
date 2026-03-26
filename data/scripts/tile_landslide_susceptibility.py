#!/usr/bin/env python3
"""
Tile the USGS n10 landslide susceptibility raster into 0.1-degree tiles.

Source: Mirus et al. 2024, "Parsimonious high-resolution landslide
susceptibility modeling at continental scales", AGU Advances.
doi:10.1029/2024AV001214

Input: n10_conus.tif (90m, values 0-81, NAD 1983)
Output: data/tiles/landslide_susc/{lat}_{lon}.bin + .json (uint8, 0.1-degree tiles)

Pixel values: 0 = non-susceptible, 1-81 = number of susceptible 10m cells
within each 90m cell. Higher = more susceptible terrain.
"""

import json
import math
import os
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds
from rasterio.windows import from_bounds as window_from_bounds

INPUT_PATH = 'data/raw/n10_conus.tif'
OUTPUT_DIR = 'data/tiles/landslide_susc'

CA_LAT_MIN, CA_LAT_MAX = 32.5, 42.0
CA_LON_MIN, CA_LON_MAX = -124.5, -114.0
TILE_DEG = 0.1
TILE_PX = 120  # ~90m native resolution: 0.1deg ≈ 11km, 11km/90m ≈ 122


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Opening {INPUT_PATH}...")
    src = rasterio.open(INPUT_PATH)
    print(f"  CRS: {src.crs}")
    print(f"  Shape: {src.shape}")
    print(f"  Bounds: {src.bounds}")
    print(f"  Resolution: {src.res}")

    t0 = time.time()
    tiles_written = 0
    total_bytes = 0

    lats = np.arange(CA_LAT_MIN, CA_LAT_MAX, TILE_DEG)
    lons = np.arange(CA_LON_MIN, CA_LON_MAX, TILE_DEG)
    total = len(lats) * len(lons)

    for lat_s in lats:
        for lon_w in lons:
            lat_n = round(lat_s + TILE_DEG, 4)
            lon_e = round(lon_w + TILE_DEG, 4)

            try:
                # Read window from source raster
                window = window_from_bounds(
                    lon_w, lat_s, lon_e, lat_n,
                    transform=src.transform
                )
                # Clamp window to raster bounds
                window = window.intersection(rasterio.windows.Window(
                    0, 0, src.width, src.height
                ))
                if window.width <= 0 or window.height <= 0:
                    continue

                data = src.read(1, window=window)

                # Reproject to our tile grid if needed
                if data.shape != (TILE_PX, TILE_PX):
                    dst = np.zeros((TILE_PX, TILE_PX), dtype=np.uint8)
                    dst_transform = from_bounds(lon_w, lat_s, lon_e, lat_n,
                                                TILE_PX, TILE_PX)
                    src_transform = src.window_transform(window)
                    reproject(
                        data.astype(np.uint8), dst,
                        src_transform=src_transform, src_crs=src.crs,
                        dst_transform=dst_transform, dst_crs='EPSG:4326',
                        resampling=Resampling.nearest,
                    )
                    data = dst

                # Flip: rasterio row 0 = north, we want row 0 = south
                data = np.flipud(data).astype(np.uint8)

                if data.max() == 0:
                    continue

                # Write tile
                tile_name = f"{round(lat_s, 1)}_{round(lon_w, 1)}"
                bin_path = os.path.join(OUTPUT_DIR, f"{tile_name}.bin")
                data.tofile(bin_path)

                sidecar = {
                    "rows": TILE_PX, "cols": TILE_PX,
                    "bounds": {
                        "north": lat_n, "south": round(lat_s, 4),
                        "west": round(lon_w, 4), "east": lon_e,
                    },
                    "units": "n10_susceptibility_0_81",
                    "dtype": "uint8",
                    "description": "USGS landslide susceptibility (Mirus et al. 2024). "
                                   "0=non-susceptible, 1-81=susceptible 10m cells per 90m cell.",
                }
                with open(os.path.join(OUTPUT_DIR, f"{tile_name}.json"), 'w') as f:
                    json.dump(sidecar, f)

                total_bytes += os.path.getsize(bin_path)
                tiles_written += 1

            except Exception as e:
                continue

        # Progress
        done = int((lat_s - CA_LAT_MIN) / (CA_LAT_MAX - CA_LAT_MIN) * 100)
        if done % 10 == 0:
            print(f"  {done}% ({tiles_written} tiles)...", flush=True)

    src.close()

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s")
    print(f"Wrote {tiles_written} tiles ({total_bytes / 1024 / 1024:.1f} MB)")


if __name__ == '__main__':
    main()
