#!/usr/bin/env python3
"""Process CAL FIRE Fire Hazard Severity Zone (FHSZ) shapefiles.

Loads SRA and LRA FHSZ shapefiles, merges them into a single GeoDataFrame,
standardizes hazard classifications, simplifies geometries, and outputs
a consolidated GeoJSON file.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd


# Canonical hazard class names
HAZARD_CLASSES = {"Moderate", "High", "Very High"}

# Map common raw values to standardized names
HAZARD_CLASS_MAP = {
    "moderate": "Moderate",
    "mod": "Moderate",
    "medium": "Moderate",
    "high": "High",
    "very high": "Very High",
    "very_high": "Very High",
    "veryhigh": "Very High",
    "vhigh": "Very High",
    # Numeric codes used in some FHSZ datasets
    "1": "Moderate",
    "2": "High",
    "3": "Very High",
}


def find_input_files(input_dir: Path) -> dict[str, list[Path]]:
    """Locate SRA/LRA shapefiles or combined GeoJSON/shapefiles in the input directory."""
    results: dict[str, list[Path]] = {"SRA": [], "LRA": [], "combined": []}
    for ext in ("*.shp", "*.geojson", "*.json"):
        for f in input_dir.rglob(ext):
            name_lower = f.stem.lower()
            if "sra" in name_lower and "lra" not in name_lower:
                results["SRA"].append(f)
            elif "lra" in name_lower and "sra" not in name_lower:
                results["LRA"].append(f)
            elif "fhsz" in name_lower or "fire" in name_lower or "hazard" in name_lower:
                results["combined"].append(f)
    return results


def standardize_hazard_class(value) -> str | None:
    """Normalize a raw hazard classification value."""
    if value is None:
        return None
    key = str(value).strip().lower()
    return HAZARD_CLASS_MAP.get(key)


def detect_hazard_column(gdf: gpd.GeoDataFrame) -> str | None:
    """Heuristically find the column containing hazard class values."""
    candidates = ["fhsz_description", "haz_class", "haz_code", "hazard", "sra_class",
                   "lra_class", "fhsz", "fhsz_class", "severity", "hazardclass"]
    col_lower_map = {c.lower(): c for c in gdf.columns}
    for candidate in candidates:
        if candidate in col_lower_map:
            return col_lower_map[candidate]
    # Fallback: look for any column whose values partially match known classes
    for col in gdf.columns:
        if col == "geometry":
            continue
        sample = gdf[col].dropna().astype(str).str.lower().unique()
        matches = sum(1 for v in sample if v.strip() in HAZARD_CLASS_MAP)
        if matches > 0:
            return col
    return None


def process(input_dir: Path, output_path: Path, tolerance: float = 0.0001) -> None:
    """Main processing pipeline."""
    # --- Discover and load input files ---
    file_map = find_input_files(input_dir)
    sra_files = file_map["SRA"]
    lra_files = file_map["LRA"]
    combined_files = file_map["combined"]

    if not sra_files and not lra_files and not combined_files:
        print(f"ERROR: No FHSZ data files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    frames = []

    # Load combined files (may already have SRA/LRA column)
    for f in combined_files:
        print(f"  Loading combined: {f}")
        gdf = gpd.read_file(f)
        # Check if there's an SRA/LRA column already
        sra_col = None
        for col in gdf.columns:
            if col.lower() in ("sra", "responsibility_area", "ra"):
                sra_col = col
                break
        if sra_col and "responsibility_area" not in gdf.columns:
            gdf["responsibility_area"] = gdf[sra_col]
        elif "responsibility_area" not in gdf.columns:
            gdf["responsibility_area"] = "Unknown"
        frames.append(gdf)

    # Load separate SRA/LRA files
    for ra, files in [("SRA", sra_files), ("LRA", lra_files)]:
        for f in files:
            print(f"  Loading {ra}: {f}")
            gdf = gpd.read_file(f)
            gdf["responsibility_area"] = ra
            frames.append(gdf)

    print(f"Found {len(sra_files)} SRA, {len(lra_files)} LRA, {len(combined_files)} combined file(s)")

    merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))
    print(f"Merged dataset: {len(merged)} features")

    # --- Standardize hazard classification ---
    haz_col = detect_hazard_column(merged)
    if haz_col is None:
        print("ERROR: Could not identify hazard classification column.", file=sys.stderr)
        print(f"  Available columns: {list(merged.columns)}", file=sys.stderr)
        sys.exit(1)

    print(f"Using hazard column: '{haz_col}'")
    merged["hazard_class"] = merged[haz_col].apply(standardize_hazard_class)

    unknown = merged["hazard_class"].isna()
    if unknown.any():
        raw_unknowns = merged.loc[unknown, haz_col].unique()
        print(f"  WARNING: {unknown.sum()} features with unrecognized hazard values: {raw_unknowns}")
        merged = merged[~unknown].copy()
        print(f"  Dropped unrecognized features; {len(merged)} remain")

    # --- Simplify geometries ---
    print(f"Simplifying geometries (tolerance={tolerance} degrees)...")
    merged["geometry"] = merged["geometry"].simplify(tolerance, preserve_topology=True)

    # --- Keep only required columns ---
    merged = merged[["geometry", "hazard_class", "responsibility_area"]].copy()

    # --- Reproject to EPSG:4326 if needed (GeoJSON standard) ---
    if merged.crs and merged.crs.to_epsg() != 4326:
        print(f"  Reprojecting from {merged.crs} to EPSG:4326")
        merged = merged.to_crs(epsg=4326)

    # --- Summary statistics ---
    print("\n--- Summary (area in sq degrees, approximate) ---")
    merged["_area"] = merged.geometry.area
    summary = merged.groupby("hazard_class")["_area"].agg(["sum", "count"])
    summary.columns = ["total_area", "num_features"]
    for cls in ["Moderate", "High", "Very High"]:
        if cls in summary.index:
            row = summary.loc[cls]
            print(f"  {cls:>10s}: {row['num_features']:6.0f} features, area ~ {row['total_area']:.6f}")
        else:
            print(f"  {cls:>10s}: (none)")

    by_ra = merged.groupby("responsibility_area")["_area"].agg(["sum", "count"])
    by_ra.columns = ["total_area", "num_features"]
    print("\nBy responsibility area:")
    for ra in ["SRA", "LRA"]:
        if ra in by_ra.index:
            row = by_ra.loc[ra]
            print(f"  {ra}: {row['num_features']:6.0f} features, area ~ {row['total_area']:.6f}")

    merged = merged.drop(columns=["_area"])

    # --- Write output ---
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_file(output_path, driver="GeoJSON")
    print(f"\nWrote {len(merged)} features to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Process CAL FIRE FHSZ shapefiles into a single GeoJSON."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/raw/calfire"),
        help="Directory containing SRA/LRA FHSZ shapefiles (default: data/raw/calfire)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/fire_zones.geojson"),
        help="Output GeoJSON path (default: data/processed/fire_zones.geojson)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0001,
        help="Geometry simplification tolerance in degrees (default: 0.0001)",
    )
    args = parser.parse_args()
    process(args.input_dir, args.output, tolerance=args.tolerance)


if __name__ == "__main__":
    main()
