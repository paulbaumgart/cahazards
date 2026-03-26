#!/usr/bin/env python3
"""Process FEMA National Flood Hazard Layer (NFHL) data for California.

Loads raw NFHL flood zone polygons, simplifies geometries based on zone
importance, retains key fields, and writes a processed GeoJSON suitable
for downstream hazard analysis.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
from shapely.validation import make_valid

# Fields to retain from the raw NFHL dataset, with common aliases.
# The first element of each list is the canonical name used internally.
FIELD_ALIASES = {
    "FLD_ZONE": ["FLD_ZONE", "FLDZONE", "Fld_Zone", "ZONE", "FLOOD_ZONE", "FloodZone"],
    "ZONE_SUBTY": ["ZONE_SUBTY", "ZONESUBTY", "Zone_Subty"],
    "SFHA_TF": ["SFHA_TF", "SFHA", "sfha_tf"],
}
KEEP_FIELDS = list(FIELD_ALIASES.keys())

# Special Flood Hazard Area zone designations (high-risk).
SFHA_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}

# Simplification tolerances in degrees (~WGS 84).
TOLERANCE_SFHA = 0.0001  # ~11 m — moderate, preserves important detail
TOLERANCE_OTHER = 0.001  # ~111 m — aggressive, for minimal-risk zones


def _resolve_field(columns, canonical_name):
    """Return the actual column name matching *canonical_name* using aliases.

    Tries exact matches from the alias list first, then falls back to
    case-insensitive matching against all columns.  Returns ``None`` if
    no match is found.
    """
    aliases = FIELD_ALIASES.get(canonical_name, [canonical_name])
    for alias in aliases:
        if alias in columns:
            return alias
    # Fallback: case-insensitive search across all columns.
    lower_map = {c.lower(): c for c in columns}
    for alias in aliases:
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


def _build_field_map(columns):
    """Build a mapping from canonical field names to actual column names.

    Returns a dict ``{canonical_name: actual_column_name}`` for every
    field that could be resolved, plus a list of canonical names that
    could not be found.
    """
    field_map = {}
    missing = []
    for canonical in KEEP_FIELDS:
        actual = _resolve_field(columns, canonical)
        if actual is not None:
            field_map[canonical] = actual
        else:
            missing.append(canonical)
    return field_map, missing


def load_nfhl(input_path: Path) -> gpd.GeoDataFrame:
    """Load NFHL flood zone polygons from *input_path*.

    Accepts any format readable by geopandas (shapefile, GeoJSON,
    GeoPackage, geodatabase, etc.).  If *input_path* is a directory the
    function attempts to find a shapefile inside it.
    """
    input_path = Path(input_path)

    if input_path.is_dir():
        # Search recursively for supported spatial formats.
        candidates = (
            list(input_path.rglob("*.shp"))
            + list(input_path.rglob("*.geojson"))
            + list(input_path.rglob("*.gpkg"))
        )
        if candidates:
            input_path = candidates[0]
        else:
            # Geodatabases are directories themselves; search for .gdb dirs.
            gdbs = list(input_path.rglob("*.gdb"))
            if gdbs:
                input_path = gdbs[0]
            else:
                sys.exit(
                    f"No shapefiles, GeoJSON, GeoPackage, or geodatabases "
                    f"found in {input_path}"
                )

    print(f"Loading NFHL data from {input_path} ...")
    gdf = gpd.read_file(input_path)
    print(f"  Loaded {len(gdf):,} features")
    return gdf


def filter_fields(gdf: gpd.GeoDataFrame):
    """Keep only the relevant attribute fields.

    Returns ``(filtered_gdf, field_map)`` where *field_map* maps
    canonical field names to the actual column names present in the data.
    """
    field_map, missing = _build_field_map(gdf.columns)

    if missing:
        print(f"  Warning: could not find fields {missing}")
        print(f"  Available columns: {list(gdf.columns)}")

    if not field_map:
        print("  ERROR: none of the expected fields were found.")
        print(f"  Available columns: {list(gdf.columns)}")
        sys.exit(1)

    # Rename matched columns to canonical names so downstream code is stable.
    rename = {actual: canonical for canonical, actual in field_map.items() if actual != canonical}
    if rename:
        gdf = gdf.rename(columns=rename)
        print(f"  Renamed columns: {rename}")

    keep = list(field_map.keys()) + ["geometry"]
    return gdf[keep].copy(), field_map


def simplify_geometries(gdf: gpd.GeoDataFrame, fld_zone_col: str = "FLD_ZONE") -> gpd.GeoDataFrame:
    """Simplify geometries with tolerance varying by zone importance.

    SFHA zones (A, AE, V, VE, etc.) get moderate simplification to
    preserve flood-risk detail.  All other zones (X, D, etc.) get
    aggressive simplification to cut file size.

    *fld_zone_col* is the column name that holds the flood zone code.
    """
    print("Simplifying geometries ...")

    # Ensure geometries are valid before simplifying.
    gdf["geometry"] = gdf["geometry"].apply(
        lambda g: make_valid(g) if g is not None and not g.is_valid else g
    )

    is_sfha = gdf[fld_zone_col].isin(SFHA_ZONES)

    gdf.loc[is_sfha, "geometry"] = (
        gdf.loc[is_sfha, "geometry"]
        .simplify(tolerance=TOLERANCE_SFHA, preserve_topology=True)
    )
    gdf.loc[~is_sfha, "geometry"] = (
        gdf.loc[~is_sfha, "geometry"]
        .simplify(tolerance=TOLERANCE_OTHER, preserve_topology=True)
    )

    # Drop any features whose geometry collapsed to empty after simplification.
    before = len(gdf)
    gdf = gdf[~gdf.geometry.is_empty].copy()
    dropped = before - len(gdf)
    if dropped:
        print(f"  Dropped {dropped:,} features with empty geometries")

    print(f"  {len(gdf):,} features after simplification")
    return gdf


def print_summary(gdf: gpd.GeoDataFrame, fld_zone_col: str = "FLD_ZONE") -> None:
    """Print zone counts and total area by zone type."""
    print("\n--- Summary Statistics ---")

    # Zone counts.
    counts = gdf[fld_zone_col].value_counts().sort_index()
    print("\nFeature count by flood zone:")
    for zone, count in counts.items():
        print(f"  {zone:>6s}: {count:>8,}")
    print(f"  {'TOTAL':>6s}: {len(gdf):>8,}")

    # Area by zone (in the CRS units — degrees if WGS 84, so we project
    # to an equal-area CRS for a meaningful km^2 estimate).
    try:
        gdf_ea = gdf.to_crs(epsg=6414)  # NAD83(2011) / California Albers
        gdf_ea["area_km2"] = gdf_ea.geometry.area / 1e6
        area_by_zone = gdf_ea.groupby(fld_zone_col)["area_km2"].sum().sort_index()
        print("\nApproximate area by flood zone (km^2):")
        for zone, area in area_by_zone.items():
            print(f"  {zone:>6s}: {area:>12,.2f}")
        print(f"  {'TOTAL':>6s}: {area_by_zone.sum():>12,.2f}")
    except Exception as exc:
        print(f"\n  Could not compute area (projection failed): {exc}")

    # SFHA vs non-SFHA breakdown.
    if "SFHA_TF" in gdf.columns:
        sfha_counts = gdf["SFHA_TF"].value_counts()
        print(f"\nSFHA breakdown (SFHA_TF field):")
        for val, count in sfha_counts.items():
            label = "In SFHA" if str(val).upper() in ("T", "TRUE", "1") else "Not in SFHA"
            print(f"  {label}: {count:,}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process FEMA NFHL flood zone data for California."
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Path to raw NFHL data (shapefile, GeoJSON, GDB, or directory). "
             "Default: tries data/raw/fema/ then data/raw/fema_nfhl/",
    )
    parser.add_argument(
        "--output",
        default="data/processed/flood_zones.geojson",
        help="Output path for processed GeoJSON. "
             "Default: data/processed/flood_zones.geojson",
    )
    args = parser.parse_args()

    input_path = args.input
    if input_path is None:
        # Try common default locations in order.
        for candidate in ["data/raw/fema/", "data/raw/fema_nfhl/"]:
            if Path(candidate).exists():
                input_path = candidate
                break
        if input_path is None:
            input_path = "data/raw/fema/"  # will error with a clear message

    gdf = load_nfhl(input_path)
    gdf, field_map = filter_fields(gdf)

    # Determine the actual flood-zone column (canonical after rename).
    fld_zone_col = "FLD_ZONE" if "FLD_ZONE" in field_map else None
    if fld_zone_col is None:
        print("  Warning: FLD_ZONE field not found; skipping zone-aware simplification")
    gdf = simplify_geometries(gdf, fld_zone_col=fld_zone_col) if fld_zone_col else gdf

    # Ensure output directory exists.
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nWriting processed data to {output_path} ...")
    gdf.to_file(output_path, driver="GeoJSON")
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Written {size_mb:.1f} MB")

    if fld_zone_col:
        print_summary(gdf, fld_zone_col=fld_zone_col)
    print("\nDone.")


if __name__ == "__main__":
    main()
