#!/usr/bin/env python3
"""
Process NOAA Sea Level Rise inundation layers for California.

The NOAA SLR data for California comes as regional GeoPackages and GDBs
(CA_Central, CA_South, CA_MTR, CA_EKA, CA_Catalina), each containing
layers per SLR increment (e.g., CA_Central_slr_1_0ft, CA_Central_slr_2_0ft).

This script loads all regions, extracts the "slr_" (hydrologically connected)
layers for each target increment, merges across regions, simplifies, and
outputs per-increment and combined GeoJSON files.
"""

import argparse
import re
import sys
from pathlib import Path

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.validation import make_valid

# Target increments in feet (whole numbers for our hazard model)
INCREMENTS_FT = [1, 2, 3, 4, 6, 10]


def discover_sources(raw_dir):
    """Find all GeoPackages, GDBs, and zip files containing SLR data."""
    raw_path = Path(raw_dir)
    sources = []

    # GeoPackages
    for gpkg in raw_path.rglob("*.gpkg"):
        sources.append(("gpkg", gpkg))

    # GDBs (may be inside zips)
    for gdb in raw_path.rglob("*.gdb"):
        sources.append(("gdb", gdb))

    # Zips that haven't been extracted yet
    for zf in raw_path.rglob("*.zip"):
        # Check if already extracted
        extracted = zf.with_suffix("")
        gdb_inside = list(zf.parent.glob(f"{zf.stem}*/*.gdb"))
        if not gdb_inside and not extracted.exists():
            import zipfile
            print(f"  Extracting {zf.name}...")
            with zipfile.ZipFile(zf, "r") as z:
                z.extractall(zf.parent)
            # Find newly extracted GDBs
            for gdb in zf.parent.rglob("*.gdb"):
                if gdb not in [s[1] for s in sources]:
                    sources.append(("gdb", gdb))

    # Re-scan for GDBs after extraction
    for gdb in raw_path.rglob("*.gdb"):
        if ("gdb", gdb) not in sources:
            sources.append(("gdb", gdb))

    return sources


def parse_increment_from_layer(layer_name):
    """Extract SLR increment in feet from a layer name.

    Examples:
        CA_Central_slr_1_0ft -> 1.0
        CA_Central_slr_10_0ft -> 10.0
        CA_Central_slr_0_5ft -> 0.5
    """
    m = re.search(r'slr_(\d+)_(\d+)ft', layer_name, re.I)
    if m:
        whole = int(m.group(1))
        frac = int(m.group(2))
        return whole + frac / 10.0
    return None


def load_increment_from_sources(sources, target_ft, tolerance):
    """Load a specific SLR increment from all regional sources and merge."""
    frames = []

    for src_type, src_path in sources:
        try:
            layers = fiona.listlayers(str(src_path))
        except Exception as e:
            print(f"  WARNING: Cannot read {src_path.name}: {e}")
            continue

        # Find the slr layer matching our target increment
        # Newer files use "slr_X_0ft", older use "slr_Xft"
        int_ft = int(target_ft)
        frac_ft = int((target_ft % 1) * 10)
        patterns = [
            f"slr_{int_ft}_{frac_ft}ft",   # CA_Central_slr_1_0ft
            f"slr_{int_ft}ft",              # CA_MTR23_slr_1ft / CA_EKA_slr_1ft
        ]
        matching = []
        for pat in patterns:
            matching = [l for l in layers if pat in l.lower().replace(" ", "")
                        and "low" not in l.lower()]
            if matching:
                break

        if not matching:
            continue

        layer = matching[0]
        region = src_path.stem.replace("_slr_data_dist", "").replace("_slr_final_dist", "")
        print(f"    {region}: layer={layer}", end="")

        try:
            gdf = gpd.read_file(src_path, layer=layer)
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)

            gdf["increment_ft"] = target_ft
            gdf = gpd.GeoDataFrame(
                {"increment_ft": gdf["increment_ft"], "geometry": gdf.geometry},
                crs=gdf.crs,
            )

            # Fix invalid geometries
            invalid = ~gdf.geometry.is_valid
            if invalid.any():
                gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].apply(make_valid)

            # Simplify
            gdf["geometry"] = gdf.geometry.simplify(tolerance, preserve_topology=True)

            # Drop empty
            gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]

            print(f" -> {len(gdf)} features")
            frames.append(gdf)
        except Exception as e:
            print(f" -> ERROR: {e}")

    if not frames:
        return None

    merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    return merged


def main():
    parser = argparse.ArgumentParser(
        description="Process NOAA SLR inundation layers for California."
    )
    parser.add_argument(
        "--input-dir",
        default="data/raw/noaa_slr",
        help="Directory containing raw SLR data (default: data/raw/noaa_slr)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/processed",
        help="Output directory (default: data/processed)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0001,
        help="Geometry simplification tolerance in degrees (default: 0.0001)",
    )
    parser.add_argument(
        "--increments",
        nargs="+",
        type=int,
        default=INCREMENTS_FT,
        help=f"SLR increments in feet (default: {INCREMENTS_FT})",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Sea Level Rise Inundation Processing Pipeline")
    print("=" * 60)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Discover all data sources
    print(f"\nScanning {args.input_dir} for SLR data...")
    sources = discover_sources(args.input_dir)
    print(f"  Found {len(sources)} data source(s):")
    for stype, spath in sources:
        print(f"    [{stype}] {spath.name}")

    if not sources:
        print("\nERROR: No SLR data found.", file=sys.stderr)
        sys.exit(1)

    # Process each increment
    all_frames = []

    for inc in args.increments:
        print(f"\n  Processing {inc}ft SLR increment...")
        gdf = load_increment_from_sources(sources, float(inc), args.tolerance)
        if gdf is not None and len(gdf) > 0:
            out_path = output_dir / f"slr_{inc}ft.geojson"
            gdf.to_file(out_path, driver="GeoJSON")
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"    -> {out_path.name}: {len(gdf)} features, {size_mb:.1f} MB")
            all_frames.append(gdf)
        else:
            print(f"    -> No data found for {inc}ft")

    if not all_frames:
        print("\nERROR: No SLR data processed.", file=sys.stderr)
        sys.exit(1)

    # Combined output
    print("\nCombining all increments...")
    combined = gpd.GeoDataFrame(pd.concat(all_frames, ignore_index=True), crs="EPSG:4326")
    combined_path = output_dir / "slr_combined.geojson"
    combined.to_file(combined_path, driver="GeoJSON")
    combined_size_mb = combined_path.stat().st_size / (1024 * 1024)

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics")
    print("=" * 60)
    for inc in args.increments:
        subset = combined[combined["increment_ft"] == inc]
        if len(subset) > 0:
            print(f"  {inc:2d}ft: {len(subset):>8,} polygons")
    print(f"\n  Total features:  {len(combined):,}")
    print(f"  Combined output: {combined_size_mb:.1f} MB")
    bounds = combined.total_bounds
    print(f"  Bounding box:    [{bounds[0]:.4f}, {bounds[1]:.4f}] to "
          f"[{bounds[2]:.4f}, {bounds[3]:.4f}]")

    print("\nDone.")


if __name__ == "__main__":
    main()
