#!/usr/bin/env python3
"""Process CGS Seismic Hazard Zone data (liquefaction and landslide zones).

Loads raw CGS zone polygons, simplifies geometries for efficient point-in-polygon
queries, and outputs processed GeoJSON files.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd


SIMPLIFY_TOLERANCE = 0.0001  # ~10m in degrees

ZONE_CONFIGS = {
    "liquefaction": {
        "glob_patterns": ["*[Ll]iquefaction*", "*[Ll][Ii][Qq]*"],
        "output_filename": "liquefaction_zones.geojson",
    },
    "landslide": {
        "glob_patterns": ["*[Ll]andslide*", "*[Ll][Ss]*"],
        "output_filename": "landslide_zones.geojson",
    },
}

SUPPORTED_EXTENSIONS = {".shp", ".geojson", ".json"}


def find_input_files(input_dir: Path, glob_patterns: list[str]) -> list[Path]:
    """Find input files matching glob patterns with supported extensions (recursive)."""
    matches = []
    for pattern in glob_patterns:
        for ext in SUPPORTED_EXTENSIONS:
            # Search recursively
            matches.extend(input_dir.rglob(f"{pattern}{ext}"))
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in matches:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def load_geodataframes(paths: list[Path]) -> gpd.GeoDataFrame | None:
    """Load and concatenate geodataframes from multiple files."""
    if not paths:
        return None

    gdfs = []
    for path in paths:
        print(f"  Loading {path.name}...")
        try:
            gdf = gpd.read_file(path)
            gdfs.append(gdf)
        except Exception as e:
            print(f"  WARNING: Failed to load {path}: {e}", file=sys.stderr)

    if not gdfs:
        return None

    return gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True)) if len(gdfs) > 1 else gdfs[0]


def process_zone(gdf: gpd.GeoDataFrame, zone_type: str, tolerance: float) -> gpd.GeoDataFrame:
    """Simplify geometries and add zone_type field."""
    # Ensure CRS is WGS84 for degree-based tolerance
    if gdf.crs and not gdf.crs.is_geographic:
        print(f"  Reprojecting from {gdf.crs} to EPSG:4326...")
        gdf = gdf.to_crs(epsg=4326)
    elif gdf.crs is None:
        print("  WARNING: No CRS found, assuming EPSG:4326", file=sys.stderr)
        gdf = gdf.set_crs(epsg=4326)

    # Simplify geometries
    print(f"  Simplifying geometries (tolerance={tolerance} degrees)...")
    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].simplify(tolerance, preserve_topology=True)

    # Remove empty geometries that may result from simplification
    gdf = gdf[~gdf["geometry"].is_empty].reset_index(drop=True)

    # Add zone type
    gdf["zone_type"] = zone_type

    return gdf


def print_summary(gdf: gpd.GeoDataFrame, zone_type: str) -> None:
    """Print summary statistics for a processed zone dataset."""
    polygon_count = len(gdf)

    # Compute area in a projected CRS for meaningful km^2 values
    try:
        gdf_proj = gdf.to_crs(epsg=3310)  # California Albers
        total_area_km2 = gdf_proj["geometry"].area.sum() / 1e6
        area_str = f"{total_area_km2:,.2f} km^2"
    except Exception:
        area_str = "N/A (projection failed)"

    bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]

    print(f"\n  {zone_type.upper()} ZONES:")
    print(f"    Polygon count : {polygon_count}")
    print(f"    Total area    : {area_str}")
    print(f"    Bounding box  : [{bounds[0]:.4f}, {bounds[1]:.4f}, {bounds[2]:.4f}, {bounds[3]:.4f}]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process CGS Seismic Hazard Zone data for liquefaction and landslide zones."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/raw/cgs_zones"),
        help="Directory containing raw CGS zone shapefiles or GeoJSON files (default: data/raw/cgs_zones)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed"),
        help="Directory to write processed GeoJSON files (default: data/processed)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=SIMPLIFY_TOLERANCE,
        help=f"Geometry simplification tolerance in degrees (default: {SIMPLIFY_TOLERANCE})",
    )
    args = parser.parse_args()

    if not args.input_dir.is_dir():
        print(f"ERROR: Input directory does not exist: {args.input_dir}", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Need pandas for concat in load_geodataframes
    global pd
    import pandas as pd

    processed_any = False

    for zone_type, config in ZONE_CONFIGS.items():
        print(f"\nProcessing {zone_type} zones...")

        input_files = find_input_files(args.input_dir, config["glob_patterns"])
        if not input_files:
            print(f"  No input files found for {zone_type} zones, skipping.")
            continue

        print(f"  Found {len(input_files)} file(s): {[f.name for f in input_files]}")

        gdf = load_geodataframes(input_files)
        if gdf is None or gdf.empty:
            print(f"  No valid data loaded for {zone_type} zones, skipping.")
            continue

        gdf = process_zone(gdf, zone_type, args.tolerance)

        output_path = args.output_dir / config["output_filename"]
        print(f"  Writing {output_path}...")
        gdf.to_file(output_path, driver="GeoJSON")

        print_summary(gdf, zone_type)
        processed_any = True

    if not processed_any:
        print("\nWARNING: No zone data was processed.", file=sys.stderr)
        sys.exit(1)

    print("\nDone.")


if __name__ == "__main__":
    main()
