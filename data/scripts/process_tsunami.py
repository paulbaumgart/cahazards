#!/usr/bin/env python3
"""Process CGS Tsunami Hazard Area data for California.

Loads tsunami hazard area polygons (975-year worst-case inundation extent),
simplifies geometries, and outputs a GeoJSON file suitable for downstream
hazard analysis.
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd


DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "raw" / "tsunami"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "processed" / "tsunami_zones.geojson"

SIMPLIFY_TOLERANCE = 0.0001  # ~11 m at California latitudes


def load_tsunami_data(input_path: Path) -> gpd.GeoDataFrame:
    """Load tsunami hazard polygons from the input path.

    Accepts a directory (reads all shapefiles/geojsons inside) or a single
    file path.
    """
    input_path = Path(input_path)

    if input_path.is_dir():
        files = (
            list(input_path.glob("*.shp"))
            + list(input_path.glob("*.geojson"))
            + list(input_path.glob("*.gpkg"))
        )
        if not files:
            sys.exit(f"Error: no spatial files found in {input_path}")
        gdfs = [gpd.read_file(f) for f in files]
        gdf = gpd.pd.concat(gdfs, ignore_index=True)
        gdf = gpd.GeoDataFrame(gdf, geometry="geometry")
    elif input_path.is_file():
        gdf = gpd.read_file(input_path)
    else:
        sys.exit(f"Error: input path does not exist: {input_path}")

    if gdf.empty:
        sys.exit("Error: loaded GeoDataFrame is empty")

    return gdf


def simplify_geometries(gdf: gpd.GeoDataFrame, tolerance: float) -> gpd.GeoDataFrame:
    """Simplify polygon geometries while preserving topology."""
    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].simplify(tolerance, preserve_topology=True)
    return gdf


def select_fields(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep geometry and any classification / identifier fields.

    Retains columns that look like classification, name, county, or class
    fields. Always keeps the geometry column.
    """
    keep_patterns = [
        "class", "type", "zone", "hazard", "county", "name", "fips",
        "id", "label", "category", "level", "source",
    ]
    keep_cols = []
    for col in gdf.columns:
        if col == "geometry":
            continue
        if any(pat in col.lower() for pat in keep_patterns):
            keep_cols.append(col)

    # If no classification columns matched, keep all non-geometry columns
    if not keep_cols:
        keep_cols = [c for c in gdf.columns if c != "geometry"]

    return gdf[keep_cols + ["geometry"]]


def print_summary(gdf: gpd.GeoDataFrame) -> None:
    """Print summary statistics about the processed dataset."""
    print(f"Polygons: {len(gdf)}")
    print(f"CRS:      {gdf.crs}")

    total_bounds = gdf.total_bounds
    print(f"Bounds:   [{total_bounds[0]:.4f}, {total_bounds[1]:.4f}, "
          f"{total_bounds[2]:.4f}, {total_bounds[3]:.4f}]")

    # Attempt to report coastal counties if a county-like column exists
    county_col = None
    for col in gdf.columns:
        if "county" in col.lower():
            county_col = col
            break
    if county_col:
        counties = sorted(gdf[county_col].dropna().unique())
        print(f"Coastal counties covered ({len(counties)}): {', '.join(str(c) for c in counties)}")
    else:
        print("County field: not found in source data")

    geom_types = gdf.geom_type.value_counts()
    print("Geometry types:")
    for gtype, count in geom_types.items():
        print(f"  {gtype}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process CGS Tsunami Hazard Area polygons for California."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Input path: directory of spatial files or a single file (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output GeoJSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    print(f"Loading tsunami hazard data from {args.input} ...")
    gdf = load_tsunami_data(args.input)
    print(f"  Loaded {len(gdf)} features")

    # Ensure WGS 84 for GeoJSON output
    if gdf.crs and not gdf.crs.equals("EPSG:4326"):
        print(f"  Reprojecting from {gdf.crs} to EPSG:4326")
        gdf = gdf.to_crs(epsg=4326)

    print("Simplifying geometries ...")
    gdf = simplify_geometries(gdf, SIMPLIFY_TOLERANCE)

    print("Selecting fields ...")
    gdf = select_fields(gdf)

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(args.output, driver="GeoJSON")
    print(f"Wrote {args.output}")

    print("\n--- Summary ---")
    print_summary(gdf)


if __name__ == "__main__":
    main()
