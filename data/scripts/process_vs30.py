#!/usr/bin/env python3
"""
Process CGS Vs30 shear-wave velocity map into tiled binary grids.

Reads a GeoTIFF raster of Vs30 values and tiles it into 0.1-degree
cells as Float32Array binary grids (matching the elevation tile format).
Each tile has ~110 x 80 points at ~100m resolution.

Tile format:
  - Header (24 bytes):
    - rows (uint32, 4 bytes)
    - cols (uint32, 4 bytes)
    - south bound (float64, 8 bytes) [min latitude]
    - west bound (float64, 8 bytes) [min longitude]
  - Data: rows * cols float32 values in row-major order (south to north, west to east)
"""

import argparse
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import rasterio
    from rasterio.windows import Window
except ImportError:
    rasterio = None

try:
    import geopandas as gpd
    from rasterio.features import rasterize as rio_rasterize
    from rasterio.transform import from_bounds
except ImportError:
    gpd = None


# California approximate bounds
CA_SOUTH = 32.5
CA_NORTH = 42.0
CA_WEST = -124.5
CA_EAST = -114.0

# Tile size in degrees
TILE_SIZE_DEG = 0.1

# Target resolution (~100m at CA latitudes)
# At ~37N: 1 degree lat ~ 111km, 1 degree lon ~ 88km
# 100m resolution -> ~1110 points per degree lat, ~880 per degree lon
# For a 0.1-degree tile: ~111 rows, ~88 cols
TARGET_RES_DEG = 0.0001  # ~11m, yields ~1000 pts per 0.1-deg (adjust below)
TILE_RES_DEG = 0.001     # ~100m, yields ~100 pts per 0.1-deg tile

# Header format: rows(u32) + cols(u32) + south(f64) + west(f64)
HEADER_FORMAT = "<II dd"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 24 bytes


def get_tile_bounds(lat_idx, lon_idx, tile_size=TILE_SIZE_DEG):
    """Get (south, north, west, east) bounds for a tile index."""
    south = lat_idx * tile_size
    north = south + tile_size
    west = lon_idx * tile_size
    east = west + tile_size
    return south, north, west, east


def extract_tile(src, south, north, west, east, res_deg=TILE_RES_DEG):
    """
    Extract a tile from the raster source and resample to target resolution.

    Returns (rows, cols, data_array) where data_array is float32.
    """
    # Compute output grid dimensions
    rows = int(round((north - south) / res_deg))
    cols = int(round((east - west) / res_deg))

    if rows <= 0 or cols <= 0:
        return 0, 0, None

    # Create target coordinate arrays
    # Row 0 = southernmost, row N = northernmost
    lats = np.linspace(south + res_deg / 2, north - res_deg / 2, rows)
    lons = np.linspace(west + res_deg / 2, east - res_deg / 2, cols)

    # Convert geographic coordinates to pixel coordinates in the source raster
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    # Use rasterio's transform to get pixel positions
    inv_transform = ~src.transform
    col_px, row_px = inv_transform * (lon_grid.ravel(), lat_grid.ravel())
    col_px = np.round(col_px).astype(int)
    row_px = np.round(row_px).astype(int)

    # Bounds check
    valid = (
        (row_px >= 0) & (row_px < src.height) &
        (col_px >= 0) & (col_px < src.width)
    )

    # Read the relevant window from the raster for efficiency
    min_row = max(0, row_px[valid].min()) if valid.any() else 0
    max_row = min(src.height, row_px[valid].max() + 1) if valid.any() else 0
    min_col = max(0, col_px[valid].min()) if valid.any() else 0
    max_col = min(src.width, col_px[valid].max() + 1) if valid.any() else 0

    if max_row <= min_row or max_col <= min_col:
        return rows, cols, np.full(rows * cols, np.nan, dtype=np.float32)

    window = Window(min_col, min_row, max_col - min_col, max_row - min_row)
    chunk = src.read(1, window=window).astype(np.float32)

    # Handle nodata
    if src.nodata is not None:
        chunk[chunk == src.nodata] = np.nan

    # Sample from chunk
    output = np.full(rows * cols, np.nan, dtype=np.float32)
    adjusted_row = row_px - min_row
    adjusted_col = col_px - min_col

    chunk_valid = (
        valid &
        (adjusted_row >= 0) & (adjusted_row < chunk.shape[0]) &
        (adjusted_col >= 0) & (adjusted_col < chunk.shape[1])
    )

    output[chunk_valid] = chunk[adjusted_row[chunk_valid], adjusted_col[chunk_valid]]

    return rows, cols, output


def write_tile(filepath, rows, cols, south, west, data):
    """Write a binary tile with header."""
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "wb") as f:
        # Write header
        header = struct.pack(HEADER_FORMAT, rows, cols, south, west)
        f.write(header)
        # Write data as float32
        f.write(data.astype(np.float32).tobytes())


VS30_ALIASES = ["VS30", "Vs30", "vs30", "SHEAR_VEL", "ShearVelocity", "VEL_30"]


def find_vs30_column(df):
    """Find the Vs30 column using known aliases (case-insensitive fallback)."""
    for alias in VS30_ALIASES:
        if alias in df.columns:
            return alias
    col_lower = {c.lower(): c for c in df.columns}
    for alias in VS30_ALIASES:
        if alias.lower() in col_lower:
            return col_lower[alias.lower()]
    return None


def process_vector_input(vector_path, output_dir, args):
    """
    Process a vector file (shapefile or GeoJSON) containing Vs30 values.

    Rasterizes the polygons into the same tiled binary grid format used
    for GeoTIFF input.
    """
    import geopandas as gpd
    from rasterio.features import rasterize as rio_rasterize
    from rasterio.transform import from_bounds

    print(f"  Loading vector data...")
    gdf = gpd.read_file(vector_path)
    print(f"  Features: {len(gdf)}")
    print(f"  CRS: {gdf.crs}")
    print(f"  Columns: {list(gdf.columns)}")

    # Reproject to WGS84 if needed
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        print(f"  Reprojecting from {gdf.crs} to EPSG:4326...")
        gdf = gdf.to_crs(epsg=4326)

    vs30_col = find_vs30_column(gdf)
    if vs30_col is None:
        print(f"ERROR: No Vs30 column found. Available columns: {list(gdf.columns)}", file=sys.stderr)
        sys.exit(1)

    print(f"  Vs30 column: {vs30_col}")
    gdf[vs30_col] = pd.to_numeric(gdf[vs30_col], errors="coerce") if hasattr(gdf[vs30_col], "astype") else gdf[vs30_col].astype(float)

    # Drop features without Vs30 values or geometry
    gdf = gdf.dropna(subset=[vs30_col])
    gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty]
    print(f"  Valid features with Vs30: {len(gdf)}")

    # Determine tile grid from data bounds
    bounds = gdf.total_bounds  # (minx, miny, maxx, maxy)
    lat_min = max(math.floor(bounds[1] / args.tile_size) * args.tile_size, CA_SOUTH)
    lat_max = min(math.ceil(bounds[3] / args.tile_size) * args.tile_size, CA_NORTH)
    lon_min = max(math.floor(bounds[0] / args.tile_size) * args.tile_size, CA_WEST)
    lon_max = min(math.ceil(bounds[2] / args.tile_size) * args.tile_size, CA_EAST)

    n_lat = int(round((lat_max - lat_min) / args.tile_size))
    n_lon = int(round((lon_max - lon_min) / args.tile_size))
    total_tiles = n_lat * n_lon

    print(f"\n  Tile grid: {n_lat} lat x {n_lon} lon = {total_tiles} potential tiles")
    print(f"  Tile size: {args.tile_size} degrees")
    print(f"  Grid resolution: {args.resolution} degrees (~{args.resolution * 111000:.0f}m)")

    # Build spatial index
    sindex = gdf.sindex

    tiles_written = 0
    tiles_skipped = 0
    total_points = 0
    vs30_min = float("inf")
    vs30_max = float("-inf")
    vs30_values = []

    for i_lat in range(n_lat):
        south = lat_min + i_lat * args.tile_size
        north = south + args.tile_size

        for i_lon in range(n_lon):
            west = lon_min + i_lon * args.tile_size
            east = west + args.tile_size

            rows = int(round((north - south) / args.resolution))
            cols = int(round((east - west) / args.resolution))

            if rows <= 0 or cols <= 0:
                tiles_skipped += 1
                continue

            # Find features that intersect this tile
            tile_candidates = list(sindex.intersection((west, south, east, north)))
            if not tile_candidates:
                tiles_skipped += 1
                continue

            tile_gdf = gdf.iloc[tile_candidates]

            # Build (geometry, value) pairs for rasterization
            shapes = [
                (geom, val)
                for geom, val in zip(tile_gdf.geometry, tile_gdf[vs30_col])
                if geom is not None and not geom.is_empty and np.isfinite(val)
            ]
            if not shapes:
                tiles_skipped += 1
                continue

            # Rasterize: transform maps pixel coords to geographic coords
            # Note: rasterio rasterize expects (west, north) as the origin
            transform = from_bounds(west, south, east, north, cols, rows)
            raster = rio_rasterize(
                shapes,
                out_shape=(rows, cols),
                transform=transform,
                fill=np.nan,
                dtype=np.float32,
            )

            # rasterize produces top-to-bottom rows; flip to south-to-north
            data = np.flipud(raster).ravel().astype(np.float32)

            valid_frac = np.isfinite(data).sum() / len(data)
            if valid_frac < args.min_valid_fraction:
                tiles_skipped += 1
                continue

            tile_name = f"{south:.1f}_{west:.1f}.bin"
            tile_path = output_dir / tile_name
            write_tile(tile_path, rows, cols, south, west, data)

            tiles_written += 1
            total_points += rows * cols

            valid_data = data[np.isfinite(data)]
            if len(valid_data) > 0:
                vs30_min = min(vs30_min, valid_data.min())
                vs30_max = max(vs30_max, valid_data.max())
                if len(vs30_values) < 100000:
                    vs30_values.extend(
                        valid_data[::max(1, len(valid_data) // 1000)].tolist()
                    )

        # Progress
        pct = 100 * (i_lat + 1) / n_lat
        print(f"\r  Progress: {pct:.0f}% ({tiles_written} tiles written)", end="", flush=True)

    print()  # newline after progress

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics (vector input)")
    print("=" * 60)
    print(f"  Tiles written:  {tiles_written:,}")
    print(f"  Tiles skipped:  {tiles_skipped:,}")
    print(f"  Total points:   {total_points:,}")

    if vs30_values:
        vs30_arr = np.array(vs30_values)
        print(f"\n  Vs30 range:     {vs30_min:.0f} - {vs30_max:.0f} m/s")
        print(f"  Vs30 mean:      {vs30_arr.mean():.0f} m/s")
        print(f"  Vs30 median:    {np.median(vs30_arr):.0f} m/s")

    print(f"\n  Output directory: {output_dir}")
    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(
        description="Process CGS Vs30 map into tiled binary grids."
    )
    parser.add_argument(
        "--input-dir",
        default="data/raw/vs30",
        help="Directory containing Vs30 GeoTIFF (default: data/raw/vs30)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/tiles/vs30",
        help="Output tile directory (default: data/tiles/vs30)",
    )
    parser.add_argument(
        "--tile-size",
        type=float,
        default=TILE_SIZE_DEG,
        help=f"Tile size in degrees (default: {TILE_SIZE_DEG})",
    )
    parser.add_argument(
        "--resolution",
        type=float,
        default=TILE_RES_DEG,
        help=f"Grid resolution in degrees (default: {TILE_RES_DEG})",
    )
    parser.add_argument(
        "--min-valid-fraction",
        type=float,
        default=0.01,
        help="Minimum fraction of valid (non-NaN) pixels to write a tile (default: 0.01)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Vs30 Tiling Pipeline")
    print("=" * 60)

    # Find input raster (case-insensitive extension matching)
    input_dir = Path(args.input_dir)
    raster_files = [
        f for f in input_dir.rglob("*")
        if f.suffix.lower() in (".tif", ".tiff")
    ]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not raster_files:
        # Fallback: look for vector input (shapefile or GeoJSON)
        vector_files = [
            f for f in input_dir.rglob("*")
            if f.suffix.lower() in (".shp", ".geojson")
        ]
        if not vector_files:
            print(f"ERROR: No GeoTIFF, shapefile, or GeoJSON found in {args.input_dir}", file=sys.stderr)
            sys.exit(1)
        if gpd is None:
            print("ERROR: geopandas is required for vector input. Install with: pip install geopandas", file=sys.stderr)
            sys.exit(1)
        if rasterio is None:
            print("ERROR: rasterio is required. Install with: pip install rasterio", file=sys.stderr)
            sys.exit(1)

        vector_path = vector_files[0]
        print(f"\nInput vector: {vector_path.name}")
        process_vector_input(vector_path, output_dir, args)
        return

    if rasterio is None:
        print("ERROR: rasterio is required. Install with: pip install rasterio", file=sys.stderr)
        sys.exit(1)

    raster_path = raster_files[0]
    print(f"\nInput raster: {raster_path.name}")

    with rasterio.open(raster_path) as src:
        print(f"  Size: {src.width} x {src.height}")
        print(f"  CRS: {src.crs}")
        print(f"  Bounds: {src.bounds}")
        print(f"  Resolution: {src.res}")
        print(f"  Data type: {src.dtypes[0]}")
        print(f"  NoData: {src.nodata}")

        # Determine tile grid
        bounds = src.bounds
        lat_min = max(math.floor(bounds.bottom / args.tile_size) * args.tile_size, CA_SOUTH)
        lat_max = min(math.ceil(bounds.top / args.tile_size) * args.tile_size, CA_NORTH)
        lon_min = max(math.floor(bounds.left / args.tile_size) * args.tile_size, CA_WEST)
        lon_max = min(math.ceil(bounds.right / args.tile_size) * args.tile_size, CA_EAST)

        n_lat = int(round((lat_max - lat_min) / args.tile_size))
        n_lon = int(round((lon_max - lon_min) / args.tile_size))
        total_tiles = n_lat * n_lon

        print(f"\n  Tile grid: {n_lat} lat x {n_lon} lon = {total_tiles} potential tiles")
        print(f"  Tile size: {args.tile_size} degrees")
        print(f"  Grid resolution: {args.resolution} degrees (~{args.resolution * 111000:.0f}m)")

        tiles_written = 0
        tiles_skipped = 0
        total_points = 0
        vs30_min = float("inf")
        vs30_max = float("-inf")
        vs30_values = []

        for i_lat in range(n_lat):
            south = lat_min + i_lat * args.tile_size
            north = south + args.tile_size

            for i_lon in range(n_lon):
                west = lon_min + i_lon * args.tile_size
                east = west + args.tile_size

                rows, cols, data = extract_tile(
                    src, south, north, west, east, args.resolution
                )

                if data is None or rows == 0 or cols == 0:
                    tiles_skipped += 1
                    continue

                valid_frac = np.isfinite(data).sum() / len(data)
                if valid_frac < args.min_valid_fraction:
                    tiles_skipped += 1
                    continue

                # Replace NaN with 0 for storage (or keep NaN as sentinel)
                # Keep NaN to distinguish ocean/missing from actual values
                tile_name = f"{south:.1f}_{west:.1f}.bin"
                tile_path = output_dir / tile_name
                write_tile(tile_path, rows, cols, south, west, data)

                tiles_written += 1
                total_points += rows * cols

                valid_data = data[np.isfinite(data)]
                if len(valid_data) > 0:
                    vs30_min = min(vs30_min, valid_data.min())
                    vs30_max = max(vs30_max, valid_data.max())
                    # Sample for stats
                    if len(vs30_values) < 100000:
                        vs30_values.extend(
                            valid_data[::max(1, len(valid_data) // 1000)].tolist()
                        )

            # Progress
            pct = 100 * (i_lat + 1) / n_lat
            print(f"\r  Progress: {pct:.0f}% ({tiles_written} tiles written)", end="", flush=True)

        print()  # newline after progress

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics")
    print("=" * 60)
    print(f"  Tiles written:  {tiles_written:,}")
    print(f"  Tiles skipped:  {tiles_skipped:,} (below valid fraction threshold)")
    print(f"  Total points:   {total_points:,}")

    if vs30_values:
        vs30_arr = np.array(vs30_values)
        print(f"\n  Vs30 range:     {vs30_min:.0f} - {vs30_max:.0f} m/s")
        print(f"  Vs30 mean:      {vs30_arr.mean():.0f} m/s")
        print(f"  Vs30 median:    {np.median(vs30_arr):.0f} m/s")

        # NEHRP site class distribution (approximate)
        print("\n  NEHRP Site Class distribution (sampled):")
        classes = [
            ("A (>1500 m/s, Hard Rock)", 1500, float("inf")),
            ("B (760-1500 m/s, Rock)", 760, 1500),
            ("C (360-760 m/s, Dense Soil)", 360, 760),
            ("D (180-360 m/s, Stiff Soil)", 180, 360),
            ("E (<180 m/s, Soft Soil)", 0, 180),
        ]
        for label, lo, hi in classes:
            count = ((vs30_arr >= lo) & (vs30_arr < hi)).sum()
            pct = 100 * count / len(vs30_arr)
            print(f"    {label}: {pct:.1f}%")

    # Tile header info
    rows_per_tile = int(round(args.tile_size / args.resolution))
    cols_per_tile = int(round(args.tile_size / args.resolution))
    tile_data_bytes = rows_per_tile * cols_per_tile * 4  # float32
    tile_total_bytes = HEADER_SIZE + tile_data_bytes
    print(f"\n  Tile dimensions: ~{rows_per_tile} x {cols_per_tile} points")
    print(f"  Tile file size:  ~{tile_total_bytes / 1024:.1f} KB")
    print(f"  Header size:     {HEADER_SIZE} bytes")
    print(f"  Total disk:      ~{tiles_written * tile_total_bytes / (1024*1024):.1f} MB")

    print(f"\n  Output directory: {output_dir}")
    print("\nDone.")


if __name__ == "__main__":
    main()
