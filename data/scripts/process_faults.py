#!/usr/bin/env python3
"""
Process USGS Quaternary Fault and Fold Database for California.

Loads the USGS fault shapefile, filters to California, extracts key attributes,
supplements with UCERF3 30-year probabilities, and outputs a clean GeoJSON.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box


# California approximate bounding box (WGS84)
CA_BBOX = box(-124.5, 32.5, -114.0, 42.0)

# UCERF3 30-year M6.7+ probabilities for key faults.
# Keys are regex patterns matched against the fault_name field in the USGS database.
# The USGS database uses names like "Hayward fault zone", "San Andreas fault zone", etc.
# Where a single database name covers multiple UCERF3 sections (e.g., San Andreas),
# we use the highest probability section as a conservative estimate.
UCERF3_PROBABILITIES = {
    r"Hayward\b": 33,          # Hayward fault zone (includes Rodgers Creek in UCERF3)
    r"Calaveras\b": 18,        # Calaveras fault zone
    r"San Gregorio\b": 10,     # San Gregorio fault zone
    r"Concord\b": 6,           # Concord fault (Concord-Green Valley in UCERF3)
    r"Green Valley\b": 6,      # Green Valley fault
    r"Greenville\b": 6,        # Greenville fault zone
    r"San Andreas\b": 22,      # San Andreas fault zone — use Peninsula section (22%) as representative
    r"San Jacinto\b": 19,      # San Jacinto fault
    r"Elsinore\b": 5,          # Elsinore fault zone
    r"Newport.Inglewood\b": 10, # Newport-Inglewood-Rose Canyon fault zone
    r"Puente Hills\b": 3,      # Puente Hills blind thrust system
    r"^Hollywood fault\b": 5,  # Hollywood fault (anchored to avoid partial matches)
}


def find_shapefile(raw_dir: Path) -> Path:
    """Find the fault shapefile. Searches the given directory and common subdirectories."""
    search_dirs = [
        raw_dir,
        raw_dir / "faults",
        raw_dir / "qfaults",
        raw_dir / "SHP",
        raw_dir / "qfaults" / "SHP",
    ]
    for d in search_dirs:
        if not d.exists():
            continue
        # Prefer files with "fault" or "Qfault" in the name
        shapefiles = list(d.glob("*[Ff]ault*.shp"))
        if shapefiles:
            return shapefiles[0]
        # Fall back to any .shp
        shapefiles = list(d.glob("*.shp"))
        if shapefiles:
            return shapefiles[0]

    # Recursive search as last resort
    shapefiles = list(raw_dir.rglob("*[Ff]ault*.shp"))
    if shapefiles:
        return shapefiles[0]

    sys.exit(f"Error: no fault shapefiles found in {raw_dir} or subdirectories")


def load_and_filter(shapefile_path: Path) -> gpd.GeoDataFrame:
    """Load the shapefile and filter to California faults."""
    print(f"Loading shapefile: {shapefile_path}")
    gdf = gpd.read_file(shapefile_path)
    print(f"  Total features loaded: {len(gdf)}")

    # Ensure WGS84 for bounding box filtering
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    # Try state/location field first if available
    state_col = None
    for col in gdf.columns:
        if col.lower() in ("state", "st", "state_name", "state_abbr", "location"):
            state_col = col
            break

    if state_col is not None:
        ca_mask = gdf[state_col].astype(str).str.contains("CA|California", case=False, na=False)
        if ca_mask.any():
            gdf = gdf[ca_mask].copy()
            print(f"  Filtered by state field '{state_col}': {len(gdf)} faults")
            return gdf

    # Fall back to bounding box intersection
    ca_mask = gdf.geometry.intersects(CA_BBOX)
    gdf = gdf[ca_mask].copy()
    print(f"  Filtered by CA bounding box: {len(gdf)} faults")
    return gdf


def _parse_slip_rate(text: str) -> float:
    """Parse USGS slip rate text like 'Less than 0.2 mm/yr' or 'Between 1 and 5 mm/yr' to a numeric value."""
    import re
    if not isinstance(text, str) or not text.strip():
        return np.nan
    text = text.strip().lower()
    # "less than X mm/yr" -> X / 2
    m = re.search(r'less\s+than\s+([\d.]+)', text)
    if m:
        return float(m.group(1)) / 2.0
    # "greater than X mm/yr" -> X * 1.5
    m = re.search(r'greater\s+than\s+([\d.]+)', text)
    if m:
        return float(m.group(1)) * 1.5
    # "between X and Y mm/yr" -> midpoint
    m = re.search(r'between\s+([\d.]+)\s+and\s+([\d.]+)', text)
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2.0
    # Try to extract any number
    m = re.search(r'([\d.]+)', text)
    if m:
        return float(m.group(1))
    return np.nan


def extract_attributes(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Extract and normalize key attributes from the fault database."""

    # Common column name mappings (USGS QFF field names vary)
    name_candidates = ["fault_name", "name", "NAME", "FAULT_NAME", "flt_name", "FLT_NAME"]
    slip_rate_candidates = ["slip_rate", "sliprate", "SLIP_RATE", "SLIPRATE", "sr", "slip_rt"]
    fault_type_candidates = ["fault_type", "flt_type", "FAULT_TYPE", "FLT_TYPE", "sense", "SENSE",
                             "slip_sense", "SLIP_SENSE", "disp_type", "DISP_TYPE"]
    recurrence_candidates = ["recur_int", "recurrence", "RECUR_INT", "RECURRENCE", "ri",
                             "rec_inter", "REC_INTER"]
    last_event_candidates = ["last_event", "LAST_EVENT", "age", "AGE", "mra", "MRA",
                             "most_recent", "MOST_RECENT"]

    cols = set(gdf.columns)

    def find_col(candidates):
        for c in candidates:
            if c in cols:
                return c
        return None

    name_col = find_col(name_candidates)
    slip_col = find_col(slip_rate_candidates)
    type_col = find_col(fault_type_candidates)
    recur_col = find_col(recurrence_candidates)
    event_col = find_col(last_event_candidates)

    result = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)

    result["fault_name"] = gdf[name_col].fillna("Unknown") if name_col else "Unknown"
    result["slip_rate_text"] = gdf[slip_col].fillna("") if slip_col else ""
    result["slip_rate_mm_yr"] = result["slip_rate_text"].apply(_parse_slip_rate) if slip_col else np.nan
    result["fault_type"] = gdf[type_col].fillna("Unknown") if type_col else "Unknown"
    result["recurrence_interval_yr"] = gdf[recur_col] if recur_col else np.nan
    result["last_event"] = gdf[event_col].fillna("Unknown") if event_col else "Unknown"

    # Normalize fault type to standard categories
    type_map = {
        "strike-slip": "strike-slip",
        "strike slip": "strike-slip",
        "ss": "strike-slip",
        "right lateral": "strike-slip",
        "left lateral": "strike-slip",
        "right-lateral": "strike-slip",
        "left-lateral": "strike-slip",
        "thrust": "thrust",
        "reverse": "thrust",
        "normal": "normal",
        "detachment": "normal",
        "oblique": "oblique",
    }
    if type_col:
        result["fault_type"] = (
            result["fault_type"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map(type_map)
            .fillna("other")
        )

    return result


def assign_ucerf3_probabilities(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Assign UCERF3 30-year M6.7+ probabilities to matching faults."""
    gdf["ucerf3_30yr_m67_prob_pct"] = np.nan

    for pattern, prob in UCERF3_PROBABILITIES.items():
        mask = gdf["fault_name"].str.contains(pattern, case=False, na=False, regex=True)
        if mask.any():
            gdf.loc[mask, "ucerf3_30yr_m67_prob_pct"] = prob
            matched_names = gdf.loc[mask, "fault_name"].unique()
            print(f"    {pattern} -> {prob}% ({len(matched_names)} unique names, {mask.sum()} segments)")

    total_matched = gdf["ucerf3_30yr_m67_prob_pct"].notna().sum()
    print(f"  UCERF3 probabilities assigned to {total_matched} fault segments")
    return gdf


def compute_lengths(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Compute fault segment lengths in kilometers."""
    # Project to California Albers (EPSG:3310) for accurate length measurement
    gdf_proj = gdf.to_crs(epsg=3310)
    gdf["length_km"] = gdf_proj.geometry.length / 1000.0
    return gdf


def print_summary(gdf: gpd.GeoDataFrame) -> None:
    """Print summary statistics."""
    print("\n=== Fault Database Summary ===")
    print(f"  Total fault segments: {len(gdf)}")
    print(f"  Total fault length:   {gdf['length_km'].sum():.1f} km")
    print(f"  Mean segment length:  {gdf['length_km'].mean():.1f} km")
    print(f"  Unique fault names:   {gdf['fault_name'].nunique()}")

    if "fault_type" in gdf.columns:
        print("\n  Fault types:")
        for ftype, count in gdf["fault_type"].value_counts().items():
            print(f"    {ftype}: {count}")

    ucerf_faults = gdf[gdf["ucerf3_30yr_m67_prob_pct"].notna()]
    if len(ucerf_faults) > 0:
        print(f"\n  Faults with UCERF3 probabilities: {len(ucerf_faults)} segments")
        for name in ucerf_faults["fault_name"].unique():
            prob = ucerf_faults.loc[
                ucerf_faults["fault_name"] == name, "ucerf3_30yr_m67_prob_pct"
            ].iloc[0]
            print(f"    {name}: {prob:.0f}%")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Process USGS Quaternary Fault and Fold Database for California"
    )
    parser.add_argument(
        "--input",
        type=str,
        default="data/raw",
        help="Path to raw data directory containing faults/ subdirectory (default: data/raw)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/processed/faults.geojson",
        help="Output GeoJSON file path (default: data/processed/faults.geojson)",
    )
    args = parser.parse_args()

    raw_dir = Path(args.input)
    output_path = Path(args.output)

    # Find and load shapefile
    shapefile_path = find_shapefile(raw_dir)

    # Load and filter to California
    gdf = load_and_filter(shapefile_path)
    if len(gdf) == 0:
        sys.exit("Error: no California faults found after filtering")

    # Extract key attributes
    print("Extracting fault attributes...")
    gdf = extract_attributes(gdf)

    # Compute segment lengths
    print("Computing fault segment lengths...")
    gdf = compute_lengths(gdf)

    # Assign UCERF3 probabilities
    print("Assigning UCERF3 probabilities...")
    gdf = assign_ucerf3_probabilities(gdf)

    # Print summary
    print_summary(gdf)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write GeoJSON
    gdf.to_file(output_path, driver="GeoJSON")
    print(f"Output written to: {output_path}")


if __name__ == "__main__":
    main()
