#!/usr/bin/env python3
"""
Process US Census Bureau TIGER/Line 2023 census tract boundaries for California.

Source: US Census Bureau, TIGER/Line Shapefiles, 2023
        https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.2023.html
        File: tl_2023_06_tract.shp (FIPS 06 = California)

Loads the TIGER/Line tract shapefile, keeps only the 11-digit GEOID (state + county
+ tract FIPS code), simplifies geometries to reduce file size, and outputs GeoJSON.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
from shapely.validation import make_valid


SIMPLIFY_TOLERANCE = 0.0001  # ~10m in degrees


def main():
    parser = argparse.ArgumentParser(
        description="Process TIGER/Line 2023 census tract boundaries for California."
    )
    parser.add_argument(
        "--input",
        type=str,
        default="data/raw/census_tracts/tl_2023_06_tract.shp",
        help="Path to TIGER/Line tract shapefile (default: data/raw/census_tracts/tl_2023_06_tract.shp)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/processed/census_tracts.geojson",
        help="Output GeoJSON path (default: data/processed/census_tracts.geojson)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=SIMPLIFY_TOLERANCE,
        help=f"Geometry simplification tolerance in degrees (default: {SIMPLIFY_TOLERANCE})",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("Census Tract Processing Pipeline")
    print("  Source: US Census Bureau TIGER/Line 2023")
    print("=" * 60)

    # Load shapefile
    print(f"\nLoading shapefile: {input_path}")
    gdf = gpd.read_file(input_path)
    print(f"  Loaded {len(gdf):,} census tracts")
    print(f"  CRS: {gdf.crs}")
    print(f"  Columns: {list(gdf.columns)}")

    # Verify GEOID column exists
    geoid_col = None
    for candidate in ["GEOID", "GEOID20", "GEOID10", "TRACTCE", "FIPS"]:
        if candidate in gdf.columns:
            geoid_col = candidate
            break

    if geoid_col is None:
        print(f"ERROR: No GEOID column found. Available columns: {list(gdf.columns)}", file=sys.stderr)
        sys.exit(1)

    if geoid_col != "GEOID":
        print(f"  Using '{geoid_col}' as GEOID field")

    # Reproject to WGS84 if needed
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        print(f"\n  Reprojecting from {gdf.crs} to EPSG:4326...")
        gdf = gdf.to_crs(epsg=4326)
    elif gdf.crs is None:
        print("  WARNING: No CRS found, assuming EPSG:4326", file=sys.stderr)
        gdf = gdf.set_crs(epsg=4326)

    # Keep only GEOID and geometry
    print("\nExtracting GEOID field...")
    result = gpd.GeoDataFrame(
        {"GEOID": gdf[geoid_col].values},
        geometry=gdf.geometry.values,
        crs=gdf.crs,
    )

    # Validate GEOID format (should be 11-digit FIPS: 2-digit state + 3-digit county + 6-digit tract)
    geoid_lengths = result["GEOID"].astype(str).str.len()
    if not (geoid_lengths == 11).all():
        non_standard = geoid_lengths.value_counts()
        print(f"  WARNING: Not all GEOIDs are 11 digits: {dict(non_standard)}", file=sys.stderr)

    # Fix invalid geometries
    invalid_mask = ~result.geometry.is_valid
    if invalid_mask.any():
        print(f"  Fixing {invalid_mask.sum()} invalid geometries...")
        result.loc[invalid_mask, "geometry"] = result.loc[invalid_mask, "geometry"].apply(
            make_valid
        )

    # Drop null/empty geometries
    null_geom = result.geometry.isna() | result.geometry.is_empty
    if null_geom.any():
        print(f"  Dropping {null_geom.sum()} null/empty geometries")
        result = result[~null_geom].reset_index(drop=True)

    # Simplify geometries
    print(f"\nSimplifying geometries (tolerance={args.tolerance} degrees)...")
    result["geometry"] = result.geometry.simplify(args.tolerance, preserve_topology=True)

    # Remove any geometries that became empty after simplification
    empty_mask = result.geometry.is_empty
    if empty_mask.any():
        print(f"  Dropping {empty_mask.sum()} geometries emptied by simplification")
        result = result[~empty_mask].reset_index(drop=True)

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Census tracts: {len(result):,}")
    bounds = result.total_bounds
    print(f"  Bounding box:  [{bounds[0]:.4f}, {bounds[1]:.4f}] to [{bounds[2]:.4f}, {bounds[3]:.4f}]")

    # Spot-check: California should have ~9000 tracts
    if len(result) < 8000 or len(result) > 10000:
        print(f"  WARNING: Expected ~9000 CA tracts, got {len(result):,}", file=sys.stderr)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting to {output_path}...")
    result.to_file(output_path, driver="GeoJSON")
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Output size: {size_mb:.1f} MB")

    print("\nDone.")


if __name__ == "__main__":
    main()
