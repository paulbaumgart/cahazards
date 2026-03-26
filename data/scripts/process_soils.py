#!/usr/bin/env python3
"""
Process USDA SSURGO soil data for California.

Extracts shrink-swell potential (Linear Extensibility Percent) from
SSURGO map unit polygons, classifies into risk categories, and
outputs simplified GeoJSON.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.validation import make_valid


# Shrink-swell classification based on LEP (Linear Extensibility Percent)
LEP_CLASSES = [
    (0, 3, "Low"),
    (3, 6, "Moderate"),
    (6, 9, "High"),
    (9, float("inf"), "Very High"),
]

# Possible field names for SSURGO data
MUKEY_ALIASES = ["MUKEY", "mukey", "Mukey", "MUSYM", "MUID"]
LEP_ALIASES = [
    "LEP", "lep", "LEP_R", "lep_r", "LEPAVG", "LEP_AVG",
    "LINEAR_EXTENSIBILITY", "LinearExtensibility",
    "lep_pct", "LEP_PCT",
]
SOIL_NAME_ALIASES = [
    "MUNAME", "muname", "MuName", "MUSYM", "musym",
    "SOIL_NAME", "soil_name", "COMPNAME", "compname",
]


def find_field(df, aliases):
    """Return the first matching column name."""
    for alias in aliases:
        if alias in df.columns:
            return alias
    col_lower = {c.lower(): c for c in df.columns}
    for alias in aliases:
        if alias.lower() in col_lower:
            return col_lower[alias.lower()]
    return None


def classify_lep(lep_value):
    """Classify LEP into shrink-swell potential category."""
    if pd.isna(lep_value):
        return "Unknown"
    lep = float(lep_value)
    for lo, hi, label in LEP_CLASSES:
        if lo <= lep < hi:
            return label
    return "Unknown"


def load_ssurgo(raw_dir):
    """
    Load SSURGO data, potentially joining spatial and tabular components.

    SSURGO is distributed as:
    - Spatial: soilmu_a_*.shp (map unit polygons with MUKEY)
    - Tabular: component.txt, chorizon.txt (with LEP values)

    Or it may be pre-joined in a geodatabase or shapefile.
    """
    raw_path = Path(raw_dir)

    # Try pre-joined spatial file first
    for pattern in ["**/*.shp", "**/*.gpkg", "**/*.geojson"]:
        files = list(raw_path.glob(pattern))
        if files:
            # Prefer files with soil/ssurgo in the name
            soil_files = [f for f in files if any(
                k in f.name.lower() for k in ["soil", "ssurgo", "mupolygon"]
            )]
            target = soil_files[0] if soil_files else files[0]
            print(f"  Loading spatial: {target.name}")
            gdf = gpd.read_file(target)

            # Check if LEP is already present
            lep_col = find_field(gdf, LEP_ALIASES)
            if lep_col:
                print(f"  Found LEP column: {lep_col}")
                return gdf

            # If not, try to join with tabular data
            mukey_col = find_field(gdf, MUKEY_ALIASES)
            if mukey_col:
                gdf = join_ssurgo_tabular(gdf, raw_path, mukey_col)
            return gdf

    # Try geodatabase
    gdb_files = list(raw_path.glob("**/*.gdb"))
    for gdb in gdb_files:
        import fiona
        layers = fiona.listlayers(str(gdb))
        # Find the map unit polygon layer (case-insensitive)
        mu_layer = next(
            (l for l in layers if l.upper() == "MUPOLYGON" or "mupolygon" in l.lower() or "soilmu" in l.lower()),
            layers[0] if layers else None,
        )
        if mu_layer:
            print(f"  Loading GDB: {gdb.name}/{mu_layer}")
            gdf = gpd.read_file(gdb, layer=mu_layer)
            mukey_col = find_field(gdf, MUKEY_ALIASES)
            if mukey_col:
                # Try to get component data from same GDB
                comp_layer = next(
                    (l for l in layers if l.lower() == "component" or "component" in l.lower()), None
                )
                if comp_layer:
                    print(f"  Loading component table: {comp_layer}")
                    comp_df = gpd.read_file(gdb, layer=comp_layer)
                    gdf = join_component_data(gdf, comp_df, mukey_col)

                    # If LEP still not found, try chorizon table
                    if "lep_pct" not in gdf.columns or gdf["lep_pct"].isna().all():
                        hz_layer = next(
                            (l for l in layers if l.lower() == "chorizon"), None
                        )
                        if hz_layer:
                            print(f"  LEP not in component; loading chorizon: {hz_layer}")
                            hz_df = gpd.read_file(gdb, layer=hz_layer)
                            gdf = join_chorizon_lep(gdf, comp_df, hz_df, mukey_col)
                else:
                    gdf = join_ssurgo_tabular(gdf, raw_path, mukey_col)
            return gdf

    print(f"ERROR: No SSURGO data found in {raw_dir}", file=sys.stderr)
    sys.exit(1)


def join_ssurgo_tabular(gdf, raw_path, mukey_col):
    """Join tabular SSURGO data (component/chorizon) to spatial data."""
    # Look for component table
    comp_files = list(raw_path.glob("**/component.*")) + list(raw_path.glob("**/comp.csv"))
    if not comp_files:
        print("  WARNING: No component table found for LEP join")
        return gdf

    comp_file = comp_files[0]
    print(f"  Loading component table: {comp_file.name}")

    if comp_file.suffix == ".txt":
        # SSURGO pipe-delimited text
        comp_df = pd.read_csv(comp_file, sep="|", header=None, low_memory=False)
        # Column positions vary by SSURGO version; try common layout
        print(f"  Component table: {comp_df.shape[1]} columns, {len(comp_df)} rows")
    else:
        comp_df = pd.read_csv(comp_file, low_memory=False)

    return join_component_data(gdf, comp_df, mukey_col)


def join_component_data(gdf, comp_df, mukey_col):
    """Join component LEP data to map unit polygons."""
    comp_mukey = find_field(comp_df, MUKEY_ALIASES)
    comp_lep = find_field(comp_df, LEP_ALIASES)

    if not comp_mukey:
        print("  WARNING: No MUKEY column in component table")
        return gdf

    if comp_lep:
        # Get dominant component LEP per map unit
        comp_df[comp_lep] = pd.to_numeric(comp_df[comp_lep], errors="coerce")

        # Use component with highest comppct (percentage of map unit)
        pct_col = find_field(comp_df, ["COMPPCT_R", "comppct_r", "COMPPCT", "comppct"])
        if pct_col:
            comp_df[pct_col] = pd.to_numeric(comp_df[pct_col], errors="coerce")
            # Get row with highest component percentage per map unit
            idx = comp_df.groupby(comp_mukey)[pct_col].idxmax()
            dominant = comp_df.loc[idx, [comp_mukey, comp_lep]].copy()
        else:
            # Average LEP per map unit
            dominant = comp_df.groupby(comp_mukey)[comp_lep].mean().reset_index()

        dominant = dominant.rename(columns={comp_lep: "lep_pct"})
        dominant[comp_mukey] = dominant[comp_mukey].astype(str)
        gdf[mukey_col] = gdf[mukey_col].astype(str)

        gdf = gdf.merge(dominant, left_on=mukey_col, right_on=comp_mukey, how="left")
        matched = gdf["lep_pct"].notna().sum()
        print(f"  Joined LEP data: {matched}/{len(gdf)} map units matched")
    else:
        print("  WARNING: No LEP column in component table")
        # Try chorizon table for LEP
        print("  (LEP may be in chorizon table; consider pre-processing)")
        gdf["lep_pct"] = None

    # Also grab soil name
    name_col = find_field(comp_df, SOIL_NAME_ALIASES)
    if name_col and comp_mukey:
        name_map = comp_df.drop_duplicates(comp_mukey).set_index(comp_mukey)[name_col]
        gdf[mukey_col] = gdf[mukey_col].astype(str)
        gdf["soil_name"] = gdf[mukey_col].map(name_map)

    return gdf


def join_chorizon_lep(gdf, comp_df, hz_df, mukey_col):
    """Join LEP from chorizon table via component table to map unit polygons.

    Join chain: MUPOLYGON --(mukey)--> component --(cokey)--> chorizon (lep_r)
    Uses the dominant component (highest comppct_r) per map unit, and the
    thickest horizon's LEP as representative.
    """
    comp_mukey = find_field(comp_df, MUKEY_ALIASES)
    comp_cokey = find_field(comp_df, ["cokey", "COKEY", "Cokey"])
    hz_cokey = find_field(hz_df, ["cokey", "COKEY", "Cokey"])
    hz_lep = find_field(hz_df, ["lep_r", "LEP_R", "lep_h", "LEP_H"] + LEP_ALIASES)

    if not all([comp_mukey, comp_cokey, hz_cokey, hz_lep]):
        print("  WARNING: Missing join keys for chorizon LEP join")
        print(f"    comp_mukey={comp_mukey}, comp_cokey={comp_cokey}, "
              f"hz_cokey={hz_cokey}, hz_lep={hz_lep}")
        return gdf

    print(f"  Joining via: MUPOLYGON.{mukey_col} -> component.{comp_mukey} -> "
          f"component.{comp_cokey} -> chorizon.{hz_cokey} -> chorizon.{hz_lep}")

    # Get dominant component per map unit
    pct_col = find_field(comp_df, ["comppct_r", "COMPPCT_R", "comppct", "COMPPCT"])
    if pct_col:
        comp_df[pct_col] = pd.to_numeric(comp_df[pct_col], errors="coerce")
        idx = comp_df.groupby(comp_mukey)[pct_col].idxmax()
        dom_comp = comp_df.loc[idx, [comp_mukey, comp_cokey]].copy()
    else:
        dom_comp = comp_df.drop_duplicates(comp_mukey)[[comp_mukey, comp_cokey]].copy()

    # Get weighted-average LEP per component from horizons (weight by thickness)
    hz_df[hz_lep] = pd.to_numeric(hz_df[hz_lep], errors="coerce")
    hz_thick_col = find_field(hz_df, ["hzdepb_r", "HZDEPB_R", "hzthk_r"])
    hz_top_col = find_field(hz_df, ["hzdept_r", "HZDEPT_R"])

    if hz_thick_col and hz_top_col:
        hz_df["_thickness"] = pd.to_numeric(hz_df[hz_thick_col], errors="coerce") - \
                              pd.to_numeric(hz_df[hz_top_col], errors="coerce")
        hz_df["_weighted_lep"] = hz_df[hz_lep] * hz_df["_thickness"]
        hz_agg = hz_df.groupby(hz_cokey).agg(
            total_thick=("_thickness", "sum"),
            total_wlep=("_weighted_lep", "sum"),
        ).reset_index()
        hz_agg["lep_pct"] = hz_agg["total_wlep"] / hz_agg["total_thick"]
    else:
        # Simple mean LEP per component
        hz_agg = hz_df.groupby(hz_cokey)[hz_lep].mean().reset_index()
        hz_agg = hz_agg.rename(columns={hz_lep: "lep_pct"})

    # Join: dominant component -> horizon LEP
    dom_comp[comp_cokey] = dom_comp[comp_cokey].astype(str)
    hz_agg[hz_cokey] = hz_agg[hz_cokey].astype(str)
    merged = dom_comp.merge(hz_agg[[hz_cokey, "lep_pct"]], left_on=comp_cokey, right_on=hz_cokey, how="left")

    # Join to spatial data
    gdf[mukey_col] = gdf[mukey_col].astype(str)
    merged[comp_mukey] = merged[comp_mukey].astype(str)

    # Drop old lep_pct if exists
    if "lep_pct" in gdf.columns:
        gdf = gdf.drop(columns=["lep_pct"])

    gdf = gdf.merge(merged[[comp_mukey, "lep_pct"]], left_on=mukey_col, right_on=comp_mukey, how="left")
    matched = gdf["lep_pct"].notna().sum()
    print(f"  Joined chorizon LEP: {matched}/{len(gdf)} map units matched")

    return gdf


def main():
    parser = argparse.ArgumentParser(
        description="Process USDA SSURGO soil data for shrink-swell potential."
    )
    parser.add_argument(
        "--input-dir",
        default="data/raw/usda_ssurgo",
        help="Directory containing raw SSURGO data (default: data/raw/usda_ssurgo)",
    )
    parser.add_argument(
        "--output",
        default="data/processed/soils.geojson",
        help="Output GeoJSON path (default: data/processed/soils.geojson)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0001,
        help="Geometry simplification tolerance in degrees (default: 0.0001)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("SSURGO Soil Shrink-Swell Processing Pipeline")
    print("=" * 60)

    # Load data - try primary path, fall back to alternate
    input_dir = args.input_dir
    if not Path(input_dir).exists():
        fallback = "data/raw/soils" if "usda_ssurgo" in input_dir else "data/raw/usda_ssurgo"
        if Path(fallback).exists():
            print(f"  Primary path {input_dir} not found, using fallback: {fallback}")
            input_dir = fallback
    print(f"\nLoading data from {input_dir}...")
    gdf = load_ssurgo(input_dir)
    print(f"  Loaded {len(gdf)} map unit polygons")
    print(f"  CRS: {gdf.crs}")

    # Reproject to WGS84 if needed
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        print(f"\n  Reprojecting from {gdf.crs} to EPSG:4326...")
        gdf = gdf.to_crs(epsg=4326)

    # Ensure we have LEP and classify
    if "lep_pct" not in gdf.columns:
        lep_col = find_field(gdf, LEP_ALIASES)
        if lep_col:
            gdf["lep_pct"] = pd.to_numeric(gdf[lep_col], errors="coerce")
        else:
            gdf["lep_pct"] = None

    gdf["shrink_swell_class"] = gdf["lep_pct"].apply(classify_lep)

    # Build output
    result = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)

    mukey_col = find_field(gdf, MUKEY_ALIASES)
    result["mukey"] = gdf[mukey_col].values if mukey_col else None
    result["lep_pct"] = gdf["lep_pct"].values
    result["shrink_swell_class"] = gdf["shrink_swell_class"].values

    if "soil_name" in gdf.columns:
        result["soil_name"] = gdf["soil_name"].values
    else:
        name_col = find_field(gdf, SOIL_NAME_ALIASES)
        result["soil_name"] = gdf[name_col].values if name_col else None

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

    # Simplify
    print(f"\nSimplifying geometries (tolerance={args.tolerance} degrees)...")
    result["geometry"] = result.geometry.simplify(args.tolerance, preserve_topology=True)

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics")
    print("=" * 60)
    print(f"  Total map units: {len(result):,}")

    print("\n  Shrink-Swell Classification:")
    for cls, count in result["shrink_swell_class"].value_counts().items():
        pct = 100.0 * count / len(result)
        print(f"    {cls:<12s}: {count:>8,} ({pct:5.1f}%)")

    if result["lep_pct"].notna().any():
        lep = result["lep_pct"].dropna()
        print(f"\n  LEP Statistics:")
        print(f"    Mean:   {lep.mean():.2f}%")
        print(f"    Median: {lep.median():.2f}%")
        print(f"    Max:    {lep.max():.2f}%")

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
