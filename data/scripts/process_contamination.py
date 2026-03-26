#!/usr/bin/env python3
"""
Process DTSC EnviroStor and SWRCB GeoTracker contamination site data.

Combines hazardous waste sites and leaking underground storage tanks
into a single GeoJSON with standardized fields.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from shapely.validation import make_valid


# EnviroStor site type mappings
ENVIROSTOR_SITE_TYPES = {
    "Federal Superfund": "superfund",
    "State Response": "state_response",
    "Voluntary Cleanup": "voluntary",
    "School Investigation": "school",
    "Corrective Action": "corrective_action",
    "Tiered Permit": "tiered_permit",
    "Military": "military",
    "Evaluation": "evaluation",
}

# GeoTracker site type mappings
GEOTRACKER_SITE_TYPES = {
    "LUST Cleanup Site": "lust",
    "Cleanup Program Site": "cleanup",
    "Military UST": "military_ust",
    "Military Cleanup Site": "military",
    "Land Disposal Site": "land_disposal",
    "WDR Site": "wdr",
}

# Substring patterns for classifying statuses.  Checked in order;
# first match wins.  This is intentionally broad so that minor
# variations in the raw status text ("Active", "Active - action req.",
# "OPEN - Remediation", etc.) are still captured.
_STATUS_PATTERNS = [
    # Land-use restrictions first (most specific)
    ("land use restrict", "closed_restricted"),
    ("deed restrict", "closed_restricted"),
    ("use restriction", "closed_restricted"),
    # Open / active
    ("open", "active"),
    ("active", "active"),
    ("action required", "active"),
    ("remediat", "active"),            # remediation / remedial
    ("assessment", "active"),
    ("site assess", "active"),
    ("interim", "active"),
    ("eligible for closure", "active"),
    # Monitoring / inactive
    ("inactive", "monitoring"),
    ("needs evaluation", "monitoring"),
    ("certified", "monitoring"),
    ("o&m", "monitoring"),
    ("verification monitoring", "monitoring"),
    ("refer", "monitoring"),
    # Closed / completed (without land-use restrictions — those
    # are already caught above)
    ("completed", "closed_restricted"),
    ("closed", "closed_restricted"),
]


def normalize_status(raw_status):
    """Normalize a status string to active/monitoring/closed_restricted."""
    if pd.isna(raw_status):
        return "unknown"
    s = str(raw_status).lower().strip()
    for substring, category in _STATUS_PATTERNS:
        if substring in s:
            return category
    return "unknown"


def find_field(df, candidates):
    """Find first matching column from candidates (case-insensitive)."""
    for c in candidates:
        if c in df.columns:
            return c
    col_lower = {col.lower(): col for col in df.columns}
    for c in candidates:
        if c.lower() in col_lower:
            return col_lower[c.lower()]
    return None


def load_spatial_or_csv(filepath):
    """Load a spatial file or CSV with lat/lon columns."""
    fp = Path(filepath)
    if fp.suffix == ".csv":
        df = pd.read_csv(fp)
        lat_col = find_field(df, [
            "LATITUDE", "Latitude", "lat", "LAT", "Y",
            "APPROXIMATE_LATITUDE", "Approximate_Latitude",
        ])
        lon_col = find_field(df, [
            "LONGITUDE", "Longitude", "lon", "LON", "LONG", "X",
            "APPROXIMATE_LONGITUDE", "Approximate_Longitude",
        ])
        if lat_col and lon_col:
            df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
            df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
            valid = df[lat_col].notna() & df[lon_col].notna()
            df = df[valid]
            geometry = [Point(xy) for xy in zip(df[lon_col], df[lat_col])]
            return gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
        else:
            print(f"  WARNING: No lat/lon columns in {fp.name}")
            return gpd.GeoDataFrame(df)
    else:
        return gpd.read_file(fp)


def load_envirostor(raw_dir):
    """Load EnviroStor data."""
    raw_path = Path(raw_dir)
    patterns = [
        "envirostor*", "EnviroStor*", "ENVIROSTOR*", "dtsc*", "DTSC*",
    ]
    for pat in patterns:
        for ext in ["shp", "csv", "geojson", "json"]:
            files = list(raw_path.glob(f"**/{pat}.{ext}"))
            if files:
                print(f"  Loading EnviroStor: {files[0].name}")
                return load_spatial_or_csv(files[0])

    # Try well-known subdirectories for EnviroStor data
    subdirs = [
        raw_path / "dtsc_envirostor",
        raw_path / "envirostor",
        raw_path / "contamination",
    ]
    for subdir in subdirs:
        if subdir.exists():
            for ext in ["shp", "csv", "geojson"]:
                files = list(subdir.glob(f"*.{ext}"))
                if files:
                    print(f"  Loading EnviroStor: {files[0].name}")
                    return load_spatial_or_csv(files[0])

    return None


def load_geotracker(raw_dir):
    """Load GeoTracker data."""
    raw_path = Path(raw_dir)
    patterns = [
        "geotracker*", "GeoTracker*", "GEOTRACKER*", "swrcb*", "SWRCB*",
        "lust*", "LUST*",
    ]
    for pat in patterns:
        for ext in ["shp", "csv", "geojson", "json"]:
            files = list(raw_path.glob(f"**/{pat}.{ext}"))
            if files:
                print(f"  Loading GeoTracker: {files[0].name}")
                return load_spatial_or_csv(files[0])

    # Try well-known subdirectories for GeoTracker data
    subdirs = [
        raw_path / "geotracker",
        raw_path / "swrcb",
        raw_path / "contamination",
    ]
    for subdir in subdirs:
        if subdir.exists():
            for ext in ["shp", "csv", "geojson"]:
                files = list(subdir.glob(f"*.{ext}"))
                if files:
                    print(f"  Loading GeoTracker: {files[0].name}")
                    return load_spatial_or_csv(files[0])

    return None


def process_envirostor(gdf):
    """Extract and standardize fields from EnviroStor data."""
    result = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)
    result["source"] = "envirostor"

    name_col = find_field(gdf, [
        "SITE_NAME", "Site_Name", "SiteName", "NAME", "site_name",
    ])
    result["site_name"] = gdf[name_col].values if name_col else None

    type_col = find_field(gdf, [
        "SITE_TYPE", "Site_Type", "SiteType", "CLEANUP_STATUS", "site_type",
    ])
    if type_col:
        result["site_type"] = gdf[type_col].map(
            lambda x: ENVIROSTOR_SITE_TYPES.get(x, str(x).lower() if pd.notna(x) else "unknown")
        )
    else:
        result["site_type"] = "unknown"

    status_col = find_field(gdf, [
        "STATUS", "Status", "CLEANUP_STATUS", "SITE_STATUS", "status",
    ])
    raw_status = gdf[status_col] if status_col else pd.Series([None] * len(gdf))
    result["status"] = raw_status.apply(normalize_status)

    contam_col = find_field(gdf, [
        "CONTAMINANTS", "Contaminants", "PRIMARY_CONTAMINANT",
        "CONTAMINANT", "CHEMICALS", "contaminants",
    ])
    result["primary_contaminants"] = gdf[contam_col].values if contam_col else None

    return result


def process_geotracker(gdf):
    """Extract and standardize fields from GeoTracker data."""
    result = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)
    result["source"] = "geotracker"

    name_col = find_field(gdf, [
        "SITE_NAME", "Site_Name", "SiteName", "FACILITY_NAME", "NAME", "site_name",
    ])
    result["site_name"] = gdf[name_col].values if name_col else None

    type_col = find_field(gdf, [
        "SITE_TYPE", "Site_Type", "CASE_TYPE", "site_type",
    ])
    if type_col:
        result["site_type"] = gdf[type_col].map(
            lambda x: GEOTRACKER_SITE_TYPES.get(x, str(x).lower() if pd.notna(x) else "lust")
        )
    else:
        result["site_type"] = "lust"

    status_col = find_field(gdf, [
        "STATUS", "Status", "CASE_STATUS", "SITE_STATUS", "status",
    ])
    raw_status = gdf[status_col] if status_col else pd.Series([None] * len(gdf))
    result["status"] = raw_status.apply(normalize_status)

    contam_col = find_field(gdf, [
        "CONTAMINANTS", "Contaminants", "PRIMARY_COC", "SUBSTANCES",
        "POTENTIAL_CONTAMINANTS", "contaminants",
    ])
    result["primary_contaminants"] = gdf[contam_col].values if contam_col else None

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Process DTSC EnviroStor and SWRCB GeoTracker contamination data."
    )
    parser.add_argument(
        "--input-dir",
        default="data/raw",
        help="Directory containing raw contamination data (default: data/raw)",
    )
    parser.add_argument(
        "--output",
        default="data/processed/contamination_sites.geojson",
        help="Output GeoJSON path (default: data/processed/contamination_sites.geojson)",
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include fully closed cases (default: only active/open/restricted)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Contamination Sites Processing Pipeline")
    print("=" * 60)

    frames = []

    # Load EnviroStor
    print(f"\nLoading EnviroStor data from {args.input_dir}...")
    es_gdf = load_envirostor(args.input_dir)
    if es_gdf is not None:
        if es_gdf.crs and es_gdf.crs.to_epsg() != 4326:
            es_gdf = es_gdf.to_crs(epsg=4326)
        print(f"  Loaded {len(es_gdf)} EnviroStor sites")
        print(f"  Columns: {list(es_gdf.columns)}")
        es_processed = process_envirostor(es_gdf)
        frames.append(es_processed)
    else:
        print("  WARNING: No EnviroStor data found")

    # Load GeoTracker
    print(f"\nLoading GeoTracker data from {args.input_dir}...")
    gt_gdf = load_geotracker(args.input_dir)
    if gt_gdf is not None:
        if gt_gdf.crs and gt_gdf.crs.to_epsg() != 4326:
            gt_gdf = gt_gdf.to_crs(epsg=4326)
        print(f"  Loaded {len(gt_gdf)} GeoTracker sites")
        print(f"  Columns: {list(gt_gdf.columns)}")
        gt_processed = process_geotracker(gt_gdf)
        frames.append(gt_processed)
    else:
        print("  WARNING: No GeoTracker data found")

    if not frames:
        print("\nERROR: No contamination data loaded.", file=sys.stderr)
        sys.exit(1)

    # Combine
    combined = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    print(f"\nCombined: {len(combined)} sites")

    # Filter to active/open/restricted cases
    if not args.include_closed:
        before = len(combined)
        combined = combined[combined["status"] != "unknown"].copy()
        # Keep active, monitoring, and closed_restricted
        combined = combined[
            combined["status"].isin(["active", "monitoring", "closed_restricted"])
        ].copy()
        print(f"  Filtered to active/open/restricted: {before} -> {len(combined)}")

    # Filter to California bounding box (remove bad coordinates like 0,0)
    from shapely.geometry import box as shapely_box
    ca_bbox = shapely_box(-124.5, 32.5, -114.0, 42.0)
    before = len(combined)
    combined = combined[combined.geometry.notna() & combined.geometry.intersects(ca_bbox)].copy()
    dropped = before - len(combined)
    if dropped:
        print(f"  Dropped {dropped} sites outside California bounding box")

    # Fix invalid / drop null geometries
    if combined.geometry.notna().any():
        invalid_mask = ~combined.geometry.is_valid & combined.geometry.notna()
        if invalid_mask.any():
            combined.loc[invalid_mask, "geometry"] = combined.loc[
                invalid_mask, "geometry"
            ].apply(make_valid)

    null_geom = combined.geometry.isna() | combined.geometry.is_empty
    if null_geom.any():
        print(f"  Dropping {null_geom.sum()} sites with null/empty geometry")
        combined = combined[~null_geom]

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics")
    print("=" * 60)
    print(f"  Total sites: {len(combined):,}")

    print("\n  By source:")
    for src, count in combined["source"].value_counts().items():
        print(f"    {src}: {count:,}")

    print("\n  By site type:")
    for st, count in combined["site_type"].value_counts().head(10).items():
        print(f"    {st}: {count:,}")

    print("\n  By status:")
    for status, count in combined["status"].value_counts().items():
        print(f"    {status}: {count:,}")

    bounds = combined.total_bounds
    print(f"\n  Bounding box: [{bounds[0]:.4f}, {bounds[1]:.4f}] to "
          f"[{bounds[2]:.4f}, {bounds[3]:.4f}]")

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting to {args.output}...")
    combined.to_file(args.output, driver="GeoJSON")
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Output size: {size_mb:.1f} MB")

    print("\nDone.")


if __name__ == "__main__":
    main()
