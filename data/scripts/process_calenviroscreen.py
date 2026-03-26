#!/usr/bin/env python3
"""
Process CalEnviroScreen 4.0 census tract data.

Loads the CES 4.0 shapefile, extracts key indicator percentile fields,
renames to clean short names, and outputs GeoJSON.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.validation import make_valid


# Mapping from target field names to possible source column names.
# CES 4.0 uses various naming conventions across releases.
FIELD_MAP = {
    "geoid": [
        "Tract", "GEOID", "Census.Tract", "FIPS", "GEOID10", "GEOID20",
        "CensusTract", "Census_Tract",
    ],
    "overall_percentile": [
        "CIscoreP", "CIscore_P", "CES4.0Percentile", "CES_4_0_Percentile",
        "CES.4.0.Percentile", "Overall_Percentile", "PERCENTILE",
        "CIscorePctl", "CI_Score_Pctl",
    ],
    "pm25_pctl": [
        "PM2_5_P", "PM2.5_P", "PM25_Pctl", "PM2.5.Pctl", "PM2_5_Pctl",
        "PM2.5Pctl",
    ],
    "ozone_pctl": [
        "OzoneP", "Ozone_P", "OzonePctl", "Ozone.Pctl", "Ozone_Pctl",
    ],
    "diesel_pm_pctl": [
        "DieselPM_P", "DieselPMPctl", "Diesel.PM.Pctl", "DieselPM_Pctl",
        "Diesel_PM_Pctl",
    ],
    "pesticides_pctl": [
        "PesticideP", "Pesticide_P", "Pesticides_P", "Pesticides.Pctl",
        "Pesticides_Pctl", "PesticidePctl",
    ],
    "tox_release_pctl": [
        "Tox_Rel_P", "Tox.Rel_P", "Tox_Release_P", "ToxRelease_Pctl",
        "Tox.Release.Pctl", "Tox_Release_Pctl",
    ],
    "traffic_pctl": [
        "TrafficP", "Traffic_P", "TrafficPctl", "Traffic.Pctl", "Traffic_Pctl",
    ],
    "cleanup_sites_pctl": [
        "CleanupP", "CleanUp_P", "Cleanup_P", "CleanupSites_Pctl",
        "Cleanup.Sites.Pctl", "Cleanup_Sites_Pctl",
    ],
    "groundwater_threats_pctl": [
        "GWThreatP", "GW_Threat_P", "Groundwater_P", "GW_Threats_Pctl",
        "Groundwater.Threats.Pctl", "Groundwater_Threats_Pctl",
    ],
    "haz_waste_pctl": [
        "HazWasteP", "HazWaste_P", "Haz.Waste_P", "HazWaste_Pctl",
        "Haz.Waste.Pctl", "Haz_Waste_Pctl",
    ],
    "solid_waste_pctl": [
        "SolWasteP", "SolidWaste_P", "Solid_Waste_P", "SolidWaste_Pctl",
        "Solid.Waste.Pctl", "Solid_Waste_Pctl",
    ],
    "impaired_water_pctl": [
        "ImpWatBodP", "ImpWater_P", "Imp_Water_P", "ImpairedWater_Pctl",
        "Impaired.Water.Pctl", "Impaired_Water_Pctl",
    ],
    "poverty_pctl": [
        "PovertyP", "Poverty_P", "PovertyPctl", "Poverty.Pctl", "Poverty_Pctl",
    ],
    "unemployment_pctl": [
        "UnemplP", "Unemp_P", "Unemployment_P", "Unemployment_Pctl",
        "Unemployment.Pctl",
    ],
    "housing_burden_pctl": [
        "HousBurdP", "HousBurd_P", "HousingBurden_P", "HousingBurden_Pctl",
        "Housing.Burden.Pctl", "Housing_Burden_Pctl",
    ],
    "linguistic_isolation_pctl": [
        "Ling_IsolP", "LingIsol_P", "LinguisticIsolation_P",
        "LinguisticIsolation_Pctl", "Ling.Isol.Pctl", "Linguistic_Isolation_Pctl",
    ],
    "education_pctl": [
        "EducatP", "Educ_P", "Education_P", "Education_Pctl", "Education.Pctl",
    ],
    "asthma_pctl": [
        "AsthmaP", "Asthma_P", "AsthmaPctl", "Asthma.Pctl", "Asthma_Pctl",
    ],
    "cardiovascular_pctl": [
        "CardiovasP", "CardVas_P", "Cardiovascular_P", "Cardiovascular_Pctl",
        "Cardiovascular.Pctl",
    ],
    "low_birth_weight_pctl": [
        "LowBirWP", "LowBirWt_P", "LowBirthWeight_P", "LowBirthWeight_Pctl",
        "Low.Birth.Weight.Pctl", "Low_Birth_Weight_Pctl",
    ],
}


def find_field(gdf, aliases):
    """Return the first matching column name from a list of aliases."""
    for alias in aliases:
        if alias in gdf.columns:
            return alias
    # Case-insensitive fallback
    col_lower = {c.lower().replace(" ", "_").replace(".", "_"): c for c in gdf.columns}
    for alias in aliases:
        key = alias.lower().replace(" ", "_").replace(".", "_")
        if key in col_lower:
            return col_lower[key]
    return None


def load_ces_data(raw_dir):
    """Load CalEnviroScreen data from the raw directory."""
    raw_path = Path(raw_dir)

    for pattern in ["**/*.shp", "**/*.geojson", "**/*.json", "**/*.gdb", "**/*.gpkg"]:
        files = list(raw_path.glob(pattern))
        if files:
            f = files[0]
            if f.suffix == ".gdb":
                import fiona
                layers = fiona.listlayers(str(f))
                print(f"  Loading GDB: {f.name}, layer: {layers[0]}")
                return gpd.read_file(f, layer=layers[0])
            else:
                print(f"  Loading: {f.name}")
                return gpd.read_file(f)

    print(f"ERROR: No spatial data found in {raw_dir}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Process CalEnviroScreen 4.0 census tract data."
    )
    parser.add_argument(
        "--input-dir",
        default="data/raw/calenviroscreen",
        help="Directory containing raw CES data (default: data/raw/calenviroscreen)",
    )
    parser.add_argument(
        "--output",
        default="data/processed/calenviroscreen.geojson",
        help="Output GeoJSON path (default: data/processed/calenviroscreen.geojson)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0001,
        help="Geometry simplification tolerance in degrees (default: 0.0001)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CalEnviroScreen 4.0 Processing Pipeline")
    print("=" * 60)

    # Load raw data
    print(f"\nLoading data from {args.input_dir}...")
    gdf = load_ces_data(args.input_dir)
    print(f"  Loaded {len(gdf)} census tracts")
    print(f"  CRS: {gdf.crs}")
    print(f"  Columns ({len(gdf.columns)}): {list(gdf.columns)}")

    # Reproject to WGS84 if needed
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        print(f"\n  Reprojecting from {gdf.crs} to EPSG:4326...")
        gdf = gdf.to_crs(epsg=4326)

    # Extract and rename fields
    print("\nExtacting fields...")
    result = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)

    matched = 0
    missing = []
    for target_name, aliases in FIELD_MAP.items():
        src = find_field(gdf, aliases)
        if src:
            result[target_name] = gdf[src].values
            matched += 1
        else:
            missing.append(target_name)
            result[target_name] = None

    print(f"  Matched {matched}/{len(FIELD_MAP)} fields")
    if missing:
        print(f"  Missing fields: {missing}")

    # Replace sentinel values (-999, -999.0) with NaN
    numeric_cols = result.select_dtypes(include="number").columns
    for col in numeric_cols:
        sentinel_mask = result[col] <= -999
        if sentinel_mask.any():
            count = sentinel_mask.sum()
            print(f"  Replacing {count} sentinel values (-999) with NaN in {col}")
            result.loc[sentinel_mask, col] = None

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

    # Simplify geometries
    print(f"\nSimplifying geometries (tolerance={args.tolerance} degrees)...")
    result["geometry"] = result.geometry.simplify(args.tolerance, preserve_topology=True)

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics")
    print("=" * 60)
    print(f"  Total census tracts: {len(result):,}")

    if result["overall_percentile"].notna().any():
        pctl = result["overall_percentile"].dropna().astype(float)
        print(f"\n  Overall CES Percentile:")
        print(f"    Mean:   {pctl.mean():.1f}")
        print(f"    Median: {pctl.median():.1f}")
        print(f"    Min:    {pctl.min():.1f}")
        print(f"    Max:    {pctl.max():.1f}")

        # Distribution by quartile
        for label, lo, hi in [
            ("Bottom 25%", 0, 25), ("25-50%", 25, 50),
            ("50-75%", 50, 75), ("Top 25%", 75, 100.1),
        ]:
            count = ((pctl >= lo) & (pctl < hi)).sum()
            print(f"    {label}: {count:,} tracts")

    # Key indicator summary
    indicators = [
        "pm25_pctl", "ozone_pctl", "diesel_pm_pctl", "pesticides_pctl",
        "traffic_pctl", "poverty_pctl", "asthma_pctl",
    ]
    print("\n  Key Indicator Means:")
    for ind in indicators:
        if result[ind].notna().any():
            mean_val = result[ind].dropna().astype(float).mean()
            print(f"    {ind:<30s} {mean_val:.1f}")

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
