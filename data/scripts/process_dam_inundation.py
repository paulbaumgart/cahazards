#!/usr/bin/env python3
"""
Process Cal OES dam breach inundation polygons.

Loads raw dam inundation shapefiles, extracts key attributes,
simplifies geometries, and outputs a single GeoJSON.

NOTE: Coverage is incomplete for smaller dams. Cal OES only requires
inundation mapping for dams under DSOD jurisdiction above certain
size thresholds, so many smaller private dams are not represented.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
from shapely.validation import make_valid


# Possible field name mappings (raw data may use varying conventions)
FIELD_ALIASES = {
    "dam_name": ["dam_name", "DAM_NAME", "DAMNAME", "DamName", "Dam_Name", "NAME", "name"],
    "dam_height": ["dam_height", "DAM_HEIGHT", "DamHeight", "Dam_Height", "HEIGHT", "HGT_FT"],
    "reservoir_capacity": [
        "reservoir_capacity", "RES_CAP", "ReservoirCapacity",
        "Reservoir_Capacity", "CAPACITY", "CAP_AF", "STORAGE",
    ],
    "downstream_community": [
        "downstream_community", "DOWNSTREAM", "DownstreamCommunity",
        "Downstream_Community", "COMMUNITY", "DS_COMMUNITY",
    ],
}


def find_field(gdf, aliases):
    """Return the first matching column name from a list of aliases."""
    for alias in aliases:
        if alias in gdf.columns:
            return alias
    return None


def load_dam_inundation(raw_dir):
    """Load all shapefiles/gdb layers from the raw dam inundation directory."""
    raw_path = Path(raw_dir)
    frames = []

    # Try shapefiles first
    shp_files = list(raw_path.glob("**/*.shp"))
    for shp in shp_files:
        print(f"  Loading shapefile: {shp.name}")
        gdf = gpd.read_file(shp)
        frames.append(gdf)

    # Try geodatabases
    gdb_files = list(raw_path.glob("**/*.gdb"))
    for gdb in gdb_files:
        import fiona
        layers = fiona.listlayers(str(gdb))
        for layer in layers:
            print(f"  Loading GDB layer: {gdb.name}/{layer}")
            gdf = gpd.read_file(gdb, layer=layer)
            frames.append(gdf)

    # Try GeoJSON
    geojson_files = list(raw_path.glob("**/*.geojson")) + list(raw_path.glob("**/*.json"))
    for gj in geojson_files:
        print(f"  Loading GeoJSON: {gj.name}")
        gdf = gpd.read_file(gj)
        frames.append(gdf)

    if not frames:
        print(f"ERROR: No spatial data files found in {raw_dir}", file=sys.stderr)
        sys.exit(1)

    combined = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))
    return combined


def extract_fields(gdf):
    """Extract and rename target fields from the raw data."""
    result = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)

    for target_name, aliases in FIELD_ALIASES.items():
        src = find_field(gdf, aliases)
        if src:
            result[target_name] = gdf[src]
        else:
            print(f"  WARNING: Could not find field for '{target_name}'. "
                  f"Tried: {aliases}")
            result[target_name] = None

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Process Cal OES dam breach inundation polygons."
    )
    parser.add_argument(
        "--input-dir",
        default="data/raw/dam_inundation",
        help="Directory containing raw dam inundation data (default: data/raw/dam_inundation)",
    )
    parser.add_argument(
        "--output",
        default="data/processed/dam_inundation.geojson",
        help="Output GeoJSON path (default: data/processed/dam_inundation.geojson)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0001,
        help="Geometry simplification tolerance in degrees (default: 0.0001)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Dam Inundation Processing Pipeline")
    print("=" * 60)

    # Load raw data
    print(f"\nLoading data from {args.input_dir}...")
    import pandas as pd
    gdf = load_dam_inundation(args.input_dir)
    print(f"  Loaded {len(gdf)} features")
    print(f"  CRS: {gdf.crs}")
    print(f"  Columns: {list(gdf.columns)}")

    # Reproject to WGS84 if needed
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        print(f"\n  Reprojecting from {gdf.crs} to EPSG:4326...")
        gdf = gdf.to_crs(epsg=4326)

    # Extract fields
    print("\nExtracting fields...")
    gdf = extract_fields(gdf)

    # Fix invalid geometries
    print("Validating geometries...")
    invalid_count = (~gdf.geometry.is_valid).sum()
    if invalid_count > 0:
        print(f"  Fixing {invalid_count} invalid geometries...")
        gdf["geometry"] = gdf.geometry.apply(
            lambda g: make_valid(g) if g and not g.is_valid else g
        )

    # Drop null geometries
    null_geom = gdf.geometry.isna().sum()
    if null_geom > 0:
        print(f"  Dropping {null_geom} features with null geometry")
        gdf = gdf.dropna(subset=["geometry"])

    # Simplify geometries
    print(f"\nSimplifying geometries (tolerance={args.tolerance} degrees)...")
    gdf["geometry"] = gdf.geometry.simplify(args.tolerance, preserve_topology=True)

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics")
    print("=" * 60)
    print(f"  Total features:     {len(gdf)}")
    if gdf["dam_name"].notna().any():
        print(f"  Unique dams:        {gdf['dam_name'].nunique()}")
    if gdf["dam_height"].notna().any():
        heights = gdf["dam_height"].dropna().astype(float)
        print(f"  Dam height range:   {heights.min():.0f} - {heights.max():.0f} ft")
    if gdf["downstream_community"].notna().any():
        print(f"  Communities at risk: {gdf['downstream_community'].nunique()}")
    bounds = gdf.total_bounds
    print(f"  Bounding box:       [{bounds[0]:.4f}, {bounds[1]:.4f}] to "
          f"[{bounds[2]:.4f}, {bounds[3]:.4f}]")
    print(f"\n  NOTE: Coverage is incomplete for smaller dams not under")
    print(f"  DSOD jurisdiction or below mapping thresholds.")

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting to {args.output}...")
    gdf.to_file(args.output, driver="GeoJSON")
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Output size: {size_mb:.1f} MB")

    print("\nDone.")


if __name__ == "__main__":
    import pandas as pd
    main()
