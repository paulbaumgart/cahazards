#!/usr/bin/env python3
"""
Process CalTrans AADT traffic data.

Loads road segment data with Annual Average Daily Traffic counts,
filters to high-traffic segments (>5000 AADT), simplifies geometries,
and outputs GeoJSON.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.validation import make_valid


# Possible field name mappings
FIELD_ALIASES = {
    "aadt": [
        "AADT", "aadt", "Aadt", "AHEAD_AADT", "BACK_AADT", "AVG_ANN_DT",
        "TRAFFIC", "ADT", "AAWDT",
    ],
    "road_name": [
        "ROAD_NAME", "road_name", "RoadName", "ROUTE", "Route",
        "STREET", "ST_NAME", "NAME", "FULLNAME", "ROUTE_NAME",
    ],
    "route_type": [
        "ROUTE_TYPE", "route_type", "RouteType", "FUNC_CLASS", "FUN_CLASS",
        "ROAD_TYPE", "NHS", "SYSTEM", "SYS_CODE", "ROUTE_SUFX",
    ],
}


def find_field(gdf, aliases):
    """Return the first matching column name from a list of aliases."""
    for alias in aliases:
        if alias in gdf.columns:
            return alias
    # Case-insensitive fallback
    col_lower = {c.lower(): c for c in gdf.columns}
    for alias in aliases:
        if alias.lower() in col_lower:
            return col_lower[alias.lower()]
    return None


def load_traffic_data(raw_dir):
    """Load traffic data from the raw directory."""
    raw_path = Path(raw_dir)
    frames = []

    for pattern in ["**/*.shp", "**/*.geojson", "**/*.json", "**/*.gdb"]:
        files = list(raw_path.glob(pattern))
        for f in files:
            if f.suffix == ".gdb":
                import fiona
                layers = fiona.listlayers(str(f))
                for layer in layers:
                    print(f"  Loading GDB layer: {f.name}/{layer}")
                    gdf = gpd.read_file(f, layer=layer)
                    frames.append(gdf)
            else:
                print(f"  Loading: {f.name}")
                gdf = gpd.read_file(f)
                frames.append(gdf)

    if not frames:
        print(f"ERROR: No spatial data found in {raw_dir}", file=sys.stderr)
        sys.exit(1)

    combined = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))
    return combined


def main():
    parser = argparse.ArgumentParser(
        description="Process CalTrans AADT traffic data."
    )
    parser.add_argument(
        "--input-dir",
        default="data/raw/traffic",
        help="Directory containing raw traffic data (default: data/raw/traffic)",
    )
    parser.add_argument(
        "--output",
        default="data/processed/traffic.geojson",
        help="Output GeoJSON path (default: data/processed/traffic.geojson)",
    )
    parser.add_argument(
        "--min-aadt",
        type=int,
        default=5000,
        help="Minimum AADT threshold (default: 5000)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.00005,
        help="Line geometry simplification tolerance in degrees (default: 0.00005)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CalTrans AADT Traffic Processing Pipeline")
    print("=" * 60)

    # Load raw data
    print(f"\nLoading data from {args.input_dir}...")
    gdf = load_traffic_data(args.input_dir)
    print(f"  Loaded {len(gdf)} road segments")
    print(f"  CRS: {gdf.crs}")
    print(f"  Columns: {list(gdf.columns)}")

    # Reproject to WGS84 if needed
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        print(f"\n  Reprojecting from {gdf.crs} to EPSG:4326...")
        gdf = gdf.to_crs(epsg=4326)

    # Find AADT field
    aadt_col = find_field(gdf, FIELD_ALIASES["aadt"])
    if not aadt_col:
        print("ERROR: Cannot find AADT column.", file=sys.stderr)
        print(f"  Available columns: {list(gdf.columns)}", file=sys.stderr)
        sys.exit(1)
    print(f"\n  Using AADT column: '{aadt_col}'")

    # Convert AADT to numeric
    gdf["aadt"] = pd.to_numeric(gdf[aadt_col], errors="coerce")

    # Filter by AADT threshold
    before = len(gdf)
    gdf = gdf[gdf["aadt"] >= args.min_aadt].copy()
    print(f"  Filtered AADT >= {args.min_aadt}: {before} -> {len(gdf)} segments")

    if len(gdf) == 0:
        print("ERROR: No segments above AADT threshold.", file=sys.stderr)
        sys.exit(1)

    # Extract fields
    result = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)
    result["aadt"] = gdf["aadt"].values

    road_col = find_field(gdf, FIELD_ALIASES["road_name"])
    if road_col:
        result["road_name"] = gdf[road_col].values
    else:
        print("  WARNING: Could not find road name column")
        result["road_name"] = None

    route_col = find_field(gdf, FIELD_ALIASES["route_type"])
    if route_col:
        result["route_type"] = gdf[route_col].values
    else:
        print("  WARNING: Could not find route type column")
        result["route_type"] = None

    # Fix invalid geometries
    invalid_mask = ~result.geometry.is_valid
    if invalid_mask.any():
        print(f"\n  Fixing {invalid_mask.sum()} invalid geometries...")
        result.loc[invalid_mask, "geometry"] = result.loc[invalid_mask, "geometry"].apply(
            make_valid
        )

    # Drop null geometries
    null_geom = result.geometry.isna() | result.geometry.is_empty
    if null_geom.any():
        print(f"  Dropping {null_geom.sum()} null/empty geometries")
        result = result[~null_geom]

    # Simplify line geometries
    print(f"\nSimplifying geometries (tolerance={args.tolerance} degrees)...")
    result["geometry"] = result.geometry.simplify(args.tolerance, preserve_topology=True)

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics")
    print("=" * 60)
    print(f"  Total segments:    {len(result):,}")
    print(f"  AADT range:        {result['aadt'].min():,.0f} - {result['aadt'].max():,.0f}")
    print(f"  AADT mean:         {result['aadt'].mean():,.0f}")
    print(f"  AADT median:       {result['aadt'].median():,.0f}")
    if result["road_name"].notna().any():
        print(f"  Unique road names: {result['road_name'].nunique()}")

    # AADT distribution
    bins = [5000, 10000, 25000, 50000, 100000, 500000]
    for i in range(len(bins) - 1):
        count = ((result["aadt"] >= bins[i]) & (result["aadt"] < bins[i + 1])).sum()
        print(f"    {bins[i]:>7,} - {bins[i+1]:>7,}: {count:>6,} segments")
    count = (result["aadt"] >= bins[-1]).sum()
    print(f"    {bins[-1]:>7,}+          : {count:>6,} segments")

    bounds = result.total_bounds
    print(f"\n  Bounding box: [{bounds[0]:.4f}, {bounds[1]:.4f}] to "
          f"[{bounds[2]:.4f}, {bounds[3]:.4f}]")

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting to {args.output}...")
    result.to_file(args.output, driver="GeoJSON")
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Output size: {size_mb:.1f} MB")

    print("\nDone.")


if __name__ == "__main__":
    main()
