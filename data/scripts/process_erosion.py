#!/usr/bin/env python3
"""Process USGS National Assessment of Shoreline Change transect data for California.

Loads USGS shoreline change transects (shapefile format), filters to the
California coast, and writes processed GeoJSON outputs for downstream hazard
modelling.  Also loads the NOAA Medium Resolution Shoreline for distance-to-
coast reference.

Outputs
-------
erosion_transects.geojson
    Transect points with erosion/accretion rates and metadata.
coastline.geojson
    NOAA shoreline clipped to California.
"""

import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

# Approximate bounding box for the California coast (EPSG 4326).
CA_BBOX = box(-124.5, 32.5, -117.0, 42.0)

# Common USGS transect attribute names (may vary across releases).
RATE_FIELD_CANDIDATES = [
    "LRR", "EPR", "WLR",  # regression / end-point rates (m/yr)
    "SCE", "NSM",          # shoreline change envelope / net movement (m)
    "LR", "rate", "Rate",
]
UNCERTAINTY_FIELD_CANDIDATES = ["LCI", "ECI", "LR_UNC", "EPR_UNC", "unc", "Unc"]
DATE_FIELD_CANDIDATES = [
    "DATE_", "DATE1", "DATE2", "earliest_date", "latest_date",
    "StartDate", "EndDate", "start_date", "end_date",
]


def _pick_field(columns, candidates, label="field"):
    """Return the first column name that matches a candidate list."""
    for candidate in candidates:
        for col in columns:
            if col.lower() == candidate.lower():
                return col
    return None


def _find_shapefiles(root: Path, keywords: list[str]) -> list[Path]:
    """Search *root* flexibly for shapefiles related to *keywords*.

    Strategy (first match wins):
    1. Look for subdirectories whose name contains any keyword (case-insensitive)
       and collect .shp files inside them (recursively).
    2. Search the whole tree for .shp files whose path contains a keyword.
    3. Fall back to every .shp file under *root*.
    """
    if not root.is_dir():
        return []

    pattern = re.compile("|".join(keywords), re.IGNORECASE)

    # Strategy 1 – subdirectories matching a keyword
    shapefiles: list[Path] = []
    for subdir in root.rglob("*"):
        if subdir.is_dir() and pattern.search(subdir.name):
            shapefiles.extend(subdir.rglob("*.shp"))
    if shapefiles:
        return sorted(set(shapefiles))

    # Strategy 2 – any .shp whose full path matches a keyword
    shapefiles = [
        p for p in root.rglob("*.shp") if pattern.search(str(p))
    ]
    if shapefiles:
        return sorted(set(shapefiles))

    # Strategy 3 – fall back to all .shp files
    return sorted(root.rglob("*.shp"))


def load_transects(input_dir: Path) -> gpd.GeoDataFrame:
    """Load USGS shoreline-change transect shapefiles from *input_dir*.

    All shapefiles found under the directory are concatenated into a single
    GeoDataFrame.
    """
    shapefiles = _find_shapefiles(input_dir, ["erosion", "transect", "shoreline"])
    if not shapefiles:
        sys.exit(f"ERROR: No transect shapefiles found under {input_dir}")

    frames = []
    for shp in shapefiles:
        print(f"  Loading {shp.name} ...")
        gdf = gpd.read_file(shp)
        frames.append(gdf)

    combined = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))
    if combined.crs is None:
        print("  WARNING: No CRS detected; assuming EPSG:4326")
        combined = combined.set_crs(epsg=4326)
    elif combined.crs.to_epsg() != 4326:
        combined = combined.to_crs(epsg=4326)

    print(f"  Loaded {len(combined)} transects from {len(shapefiles)} file(s)")
    return combined


def filter_california(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Retain only transects whose geometry intersects the California bbox."""
    mask = gdf.geometry.intersects(CA_BBOX)
    filtered = gdf.loc[mask].copy()
    n_dropped = len(gdf) - len(filtered)
    if n_dropped:
        print(f"  Filtered out {n_dropped} non-California transects")
    print(f"  {len(filtered)} California transects retained")
    return filtered


def normalise_fields(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Detect and rename the erosion-rate, uncertainty, and date fields."""
    cols = list(gdf.columns)

    rate_col = _pick_field(cols, RATE_FIELD_CANDIDATES, "erosion rate")
    unc_col = _pick_field(cols, UNCERTAINTY_FIELD_CANDIDATES, "uncertainty")

    # Detect date range columns.
    date_cols_found = [c for c in cols if _pick_field([c], DATE_FIELD_CANDIDATES)]

    if rate_col:
        gdf = gdf.rename(columns={rate_col: "erosion_rate_m_yr"})
        print(f"  Erosion rate field: {rate_col} -> erosion_rate_m_yr")
    else:
        print("  WARNING: Could not identify an erosion-rate field; "
              "creating a placeholder NaN column")
        gdf["erosion_rate_m_yr"] = np.nan

    if unc_col:
        gdf = gdf.rename(columns={unc_col: "uncertainty_m_yr"})
        print(f"  Uncertainty field: {unc_col} -> uncertainty_m_yr")
    else:
        gdf["uncertainty_m_yr"] = np.nan

    # Combine date fields into a single period string when possible.
    if len(date_cols_found) >= 2:
        d1, d2 = date_cols_found[0], date_cols_found[1]
        gdf["measurement_period"] = (
            gdf[d1].astype(str) + " to " + gdf[d2].astype(str)
        )
        print(f"  Measurement period derived from {d1}, {d2}")
    elif len(date_cols_found) == 1:
        gdf["measurement_period"] = gdf[date_cols_found[0]].astype(str)
    else:
        gdf["measurement_period"] = "unknown"

    return gdf


def convert_to_points(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """If geometries are lines, convert to their centroids (points)."""
    geom_types = gdf.geometry.geom_type.unique()
    if "LineString" in geom_types or "MultiLineString" in geom_types:
        print("  Converting line geometries to centroid points")
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.centroid
    return gdf


def load_coastline(input_dir: Path) -> gpd.GeoDataFrame:
    """Load NOAA Medium Resolution Shoreline and clip to California."""
    shapefiles = _find_shapefiles(input_dir, ["coast", "shore", "noaa"])
    if not shapefiles:
        sys.exit(f"ERROR: No coastline shapefiles found under {input_dir}")

    frames = []
    for shp in shapefiles:
        print(f"  Loading coastline {shp.name} ...")
        gdf = gpd.read_file(shp)
        frames.append(gdf)

    coastline = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))
    if coastline.crs is None:
        coastline = coastline.set_crs(epsg=4326)
    elif coastline.crs.to_epsg() != 4326:
        coastline = coastline.to_crs(epsg=4326)

    # Clip to California bounding box.
    coastline = gpd.clip(coastline, CA_BBOX)
    print(f"  {len(coastline)} coastline features after clipping to CA bbox")
    return coastline


def flag_coverage_gaps(
    transects: gpd.GeoDataFrame,
    coastline: gpd.GeoDataFrame,
    segment_length_km: float = 10.0,
) -> None:
    """Identify stretches of coastline with no nearby transect data.

    Walks along the coastline in roughly *segment_length_km* increments and
    reports segments that have no transect within 1 km.
    """
    if coastline.empty or transects.empty:
        print("  Cannot assess coverage gaps (empty inputs)")
        return

    # Work in a projected CRS for distance calculations (CA Albers).
    proj_crs = "EPSG:3310"
    coast_proj = coastline.to_crs(proj_crs)
    trans_proj = transects.to_crs(proj_crs)

    from shapely.ops import unary_union

    merged = unary_union(coast_proj.geometry)

    # Sample points along the merged coastline.
    total_length = merged.length  # metres
    step = segment_length_km * 1000
    sample_distances = np.arange(0, total_length, step)

    gap_count = 0
    gap_locations = []
    for d in sample_distances:
        pt = merged.interpolate(d)
        nearest_dist = trans_proj.geometry.distance(pt).min()
        if nearest_dist > 1000:  # more than 1 km from any transect
            gap_count += 1
            gap_locations.append((pt.x, pt.y, nearest_dist))

    print(f"\n  Coverage gaps (>{segment_length_km} km segments with no "
          f"transect within 1 km): {gap_count} of "
          f"{len(sample_distances)} segments")
    if gap_locations:
        print("  First 5 gap locations (projected coords, distance in m):")
        for x, y, dist in gap_locations[:5]:
            print(f"    ({x:.0f}, {y:.0f})  nearest transect: {dist:.0f} m")


def print_summary(gdf: gpd.GeoDataFrame) -> None:
    """Print summary statistics for erosion-rate data."""
    rates = gdf["erosion_rate_m_yr"].dropna()
    if rates.empty:
        print("\n  No valid erosion rate values to summarise.")
        return

    eroding = (rates < 0).sum()
    accreting = (rates > 0).sum()
    stable = (rates == 0).sum()

    print("\n--- Erosion Transect Summary ---")
    print(f"  Total transects : {len(gdf)}")
    print(f"  With valid rate : {len(rates)}")
    print(f"  Eroding (< 0)   : {eroding}")
    print(f"  Accreting (> 0) : {accreting}")
    print(f"  Stable (== 0)   : {stable}")
    print(f"  Mean rate       : {rates.mean():.3f} m/yr")
    print(f"  Median rate     : {rates.median():.3f} m/yr")
    print(f"  Std dev         : {rates.std():.3f} m/yr")
    print(f"  Range           : [{rates.min():.3f}, {rates.max():.3f}] m/yr")


def main():
    parser = argparse.ArgumentParser(
        description="Process USGS shoreline-change transects for California."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/raw"),
        help="Root directory containing erosion/ and coastline/ subdirs "
             "(default: data/raw)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed"),
        help="Directory for processed GeoJSON outputs (default: data/processed)",
    )
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Transects -----------------------------------------------------------
    print("Loading USGS transects ...")
    transects = load_transects(input_dir)
    transects = filter_california(transects)
    transects = normalise_fields(transects)
    transects = convert_to_points(transects)

    print_summary(transects)

    out_transects = output_dir / "erosion_transects.geojson"
    transects.to_file(out_transects, driver="GeoJSON")
    print(f"\n  Wrote {out_transects}")

    # --- Coastline -----------------------------------------------------------
    print("\nLoading NOAA coastline ...")
    coastline = load_coastline(input_dir)

    out_coastline = output_dir / "coastline.geojson"
    coastline.to_file(out_coastline, driver="GeoJSON")
    print(f"  Wrote {out_coastline}")

    # --- Coverage gaps -------------------------------------------------------
    print("\nChecking transect coverage ...")
    flag_coverage_gaps(transects, coastline)

    print("\nDone.")


if __name__ == "__main__":
    main()
