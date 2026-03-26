#!/usr/bin/env python3
"""
Process FAA airport data for California general aviation airports.

Identifies GA airports with piston-engine operations, joins with
EPA NEI lead emissions data, computes downwind cone polygons for
aviation gasoline lead exposure analysis.
"""

import argparse
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, Polygon
from shapely.validation import make_valid


# Hardcoded annual piston operations for major CA GA airports
# Source: FAA ATADS, airport master records, and local counts
PISTON_OPERATIONS = {
    "RHV": 120000,  # Reid-Hillview
    "PAO": 85000,   # Palo Alto
    "SQL": 75000,   # San Carlos
    "HAF": 38000,   # Half Moon Bay
    "HWD": 75000,   # Hayward
    "LVK": 80000,   # Livermore
    "CCR": 55000,   # Concord
    "SJC": 20000,   # San Jose Intl (piston only)
    "SMO": 85000,   # Santa Monica
    "VNY": 180000,  # Van Nuys
    "TOA": 110000,  # Torrance
    "CPM": 70000,   # Compton
    "WHP": 55000,   # Whiteman
    "EMT": 70000,   # El Monte
    "POC": 80000,   # Brackett
    "AJO": 40000,   # Corona
    "RAL": 60000,   # Riverside
    "REI": 35000,   # Redlands
    "MYF": 100000,  # Montgomery-Gibbs
    "SDM": 55000,   # Brown Field
    "SEE": 75000,   # Gillespie
    "CRQ": 50000,   # McClellan-Palomar
    "RNM": 25000,   # Ramona
    "SAC": 60000,   # Sacramento Exec
    "MHR": 25000,   # Sacramento Mather
}

# Hardcoded prevailing wind directions (degrees from north, i.e., the direction
# wind blows FROM). The downwind cone extends in the opposite direction.
# Source: NOAA/NWS wind rose data for CA airports.
PREVAILING_WIND_FROM = {
    "RHV": 320,  # NW (typical Bay Area)
    "PAO": 310,
    "SQL": 290,
    "HAF": 280,  # W-WNW (coastal)
    "HWD": 290,
    "LVK": 250,  # WSW (Livermore gap)
    "CCR": 250,
    "SJC": 320,
    "SMO": 250,  # WSW (LA basin)
    "VNY": 250,
    "TOA": 250,
    "CPM": 240,
    "WHP": 240,
    "EMT": 250,
    "POC": 250,
    "AJO": 270,
    "RAL": 270,
    "REI": 270,
    "MYF": 290,  # WNW (San Diego)
    "SDM": 290,
    "SEE": 270,
    "CRQ": 290,
    "RNM": 270,
    "SAC": 180,  # S (Sacramento delta breeze)
    "MHR": 180,
}

# Cone parameters
CONE_RADIUS_M = 2000       # 2 km downwind
CONE_HALF_ANGLE_DEG = 45   # 90-degree cone = 45 degrees each side


def make_downwind_cone(lon, lat, wind_from_deg, radius_m=CONE_RADIUS_M,
                       half_angle_deg=CONE_HALF_ANGLE_DEG, n_points=32):
    """
    Create a 90-degree downwind cone polygon extending from an airport.

    Parameters
    ----------
    lon, lat : float
        Airport coordinates in WGS84.
    wind_from_deg : float
        Direction wind blows FROM (meteorological convention, degrees from north).
    radius_m : float
        Cone radius in meters.
    half_angle_deg : float
        Half-width of the cone in degrees (45 = 90-degree total cone).
    n_points : int
        Number of arc points for the cone edge.

    Returns
    -------
    shapely.geometry.Polygon
    """
    # Downwind direction is opposite of wind-from
    downwind_deg = (wind_from_deg + 180) % 360

    # Convert radius from meters to approximate degrees
    # At California latitudes (~34-42N), 1 degree lat ~ 111km, 1 degree lon ~ 85-92km
    lat_rad = math.radians(lat)
    m_per_deg_lat = 111320
    m_per_deg_lon = 111320 * math.cos(lat_rad)

    # Generate cone arc points
    start_angle = downwind_deg - half_angle_deg
    end_angle = downwind_deg + half_angle_deg

    points = [(lon, lat)]  # apex at airport
    for i in range(n_points + 1):
        angle_deg = start_angle + (end_angle - start_angle) * i / n_points
        angle_rad = math.radians(angle_deg)
        # Geographic bearing: 0=N, 90=E
        dx = radius_m * math.sin(angle_rad) / m_per_deg_lon
        dy = radius_m * math.cos(angle_rad) / m_per_deg_lat
        points.append((lon + dx, lat + dy))
    points.append((lon, lat))  # close polygon

    return Polygon(points)


def load_faa_airports(raw_dir):
    """Load FAA airport data from the raw directory."""
    raw_path = Path(raw_dir)

    # Try various formats
    for pattern in ["**/*.shp", "**/*.csv", "**/*.geojson", "**/*.json", "**/*.gdb"]:
        files = list(raw_path.glob(pattern))
        # Filter out non-airport files (NEI, wind, runways)
        files = [f for f in files if "nei" not in f.name.lower()
                 and "wind" not in f.name.lower()
                 and "runway" not in f.name.lower()]
        # Prefer files with "airport" in the name
        airport_files = [f for f in files if "airport" in f.name.lower()]
        if airport_files:
            files = airport_files
        if files:
            f = files[0]
            print(f"  Loading airport data: {f.name}")
            if f.suffix == ".csv":
                df = pd.read_csv(f)
                # Try to find lat/lon columns
                lat_col = next(
                    (c for c in df.columns if c.lower() in
                     ["latitude_deg", "lat", "latitude", "arplat", "arp_latitude"]),
                    None,
                )
                lon_col = next(
                    (c for c in df.columns if c.lower() in
                     ["longitude_deg", "lon", "long", "longitude", "arplon", "arp_longitude"]),
                    None,
                )
                if lat_col and lon_col:
                    geometry = [Point(xy) for xy in zip(df[lon_col], df[lat_col])]
                    return gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
                else:
                    print(f"  WARNING: Cannot find lat/lon columns in {f.name}")
                    print(f"  Columns: {list(df.columns)}")
                    return gpd.GeoDataFrame(df)
            else:
                return gpd.read_file(f)

    print(f"ERROR: No airport data found in {raw_dir}", file=sys.stderr)
    sys.exit(1)


def find_column(gdf, candidates):
    """Find the first matching column name from candidates."""
    for c in candidates:
        if c in gdf.columns:
            return c
        # Case-insensitive match
        for col in gdf.columns:
            if col.lower() == c.lower():
                return col
    return None


def filter_california_ga(gdf):
    """Filter to California airports with GA/piston operations."""
    # Find state column
    state_col = find_column(gdf, [
        "state", "STATE", "iso_region", "REGION", "STATE_CODE",
        "LOCST", "LOC_ST",
    ])

    if state_col:
        # Filter to California
        ca_mask = gdf[state_col].astype(str).str.contains(
            r"(?i)^(CA|US-CA|California|06)$", regex=True, na=False
        )
        gdf = gdf[ca_mask].copy()
        print(f"  Filtered to California: {len(gdf)} airports")

    # Find facility type / use columns
    type_col = find_column(gdf, [
        "type", "TYPE", "FACILITY_TYPE", "type_code", "SITE_TYPE_CODE",
    ])
    use_col = find_column(gdf, [
        "use", "USE", "OWNERSHIP", "FACILITY_USE",
    ])

    # We keep all airports for now; GA filtering done via operations data
    id_col = find_column(gdf, [
        "ident", "IDENT", "LOCID", "LOC_ID", "FAA_ID", "ARPT_ID",
        "icao", "ICAO", "iata_code", "local_code",
    ])

    name_col = find_column(gdf, [
        "name", "NAME", "FACILITY_NAME", "ARPT_NAME", "airport_name",
    ])

    return gdf, id_col, name_col, type_col


def main():
    parser = argparse.ArgumentParser(
        description="Process FAA airport data for CA GA airports with lead exposure analysis."
    )
    parser.add_argument(
        "--input-dir",
        default="data/raw/airports",
        help="Directory containing raw airport data (default: data/raw/airports)",
    )
    parser.add_argument(
        "--output-airports",
        default="data/processed/airports.geojson",
        help="Output airport points GeoJSON (default: data/processed/airports.geojson)",
    )
    parser.add_argument(
        "--output-cones",
        default="data/processed/airport_wind_cones.geojson",
        help="Output wind cone GeoJSON (default: data/processed/airport_wind_cones.geojson)",
    )
    parser.add_argument(
        "--cone-radius",
        type=float,
        default=CONE_RADIUS_M,
        help=f"Downwind cone radius in meters (default: {CONE_RADIUS_M})",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Airport GA / Lead Exposure Processing Pipeline")
    print("=" * 60)

    # Load airport data
    print(f"\nLoading airports from {args.input_dir}...")
    gdf = load_faa_airports(args.input_dir)
    print(f"  Loaded {len(gdf)} airports")

    # Reproject if needed
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    # Filter to California GA airports
    print("\nFiltering to California...")
    gdf, id_col, name_col, type_col = filter_california_ga(gdf)

    if len(gdf) == 0:
        print("ERROR: No California airports found.", file=sys.stderr)
        sys.exit(1)

    # Build output airport GeoDataFrame
    airports_out = gpd.GeoDataFrame(geometry=gdf.geometry, crs=gdf.crs)

    if id_col:
        airports_out["airport_id"] = gdf[id_col].values
    if name_col:
        airports_out["airport_name"] = gdf[name_col].values
    if type_col:
        airports_out["facility_type"] = gdf[type_col].values

    # Add piston operations from hardcoded data
    print("\nAdding piston operation counts...")
    airports_out["annual_piston_ops"] = None
    if id_col:
        for idx, row in airports_out.iterrows():
            aid = str(row.get("airport_id", "")).strip().upper()
            # Try with and without K prefix (FAA vs ICAO)
            if aid in PISTON_OPERATIONS:
                airports_out.at[idx, "annual_piston_ops"] = PISTON_OPERATIONS[aid]
            elif aid.startswith("K") and aid[1:] in PISTON_OPERATIONS:
                airports_out.at[idx, "annual_piston_ops"] = PISTON_OPERATIONS[aid[1:]]

    # Try to join with NEI lead emissions data
    nei_path = Path(args.input_dir) / "nei_lead.csv"
    if nei_path.exists():
        print(f"\nJoining with NEI lead emissions data: {nei_path.name}")
        nei = pd.read_csv(nei_path)
        nei_id_col = find_column(nei, ["airport_id", "faa_id", "LOCID", "facility_id"])
        nei_lead_col = find_column(nei, [
            "lead_emissions_tpy", "lead_tpy", "pb_emissions", "LEAD",
        ])
        if nei_id_col and nei_lead_col and id_col:
            nei_map = dict(zip(nei[nei_id_col].str.strip().str.upper(), nei[nei_lead_col]))
            airports_out["lead_emissions_tpy"] = airports_out["airport_id"].apply(
                lambda x: nei_map.get(str(x).strip().upper())
            )
            matched = airports_out["lead_emissions_tpy"].notna().sum()
            print(f"  Matched {matched} airports with lead emissions data")
        else:
            print(f"  WARNING: Could not match NEI columns. Columns: {list(nei.columns)}")
    else:
        print("\n  NEI lead emissions file not found; using operations-based estimates only.")

    # Try to load wind rose data
    wind_path = Path(args.input_dir) / "wind_roses.csv"
    wind_data = {}
    if wind_path.exists():
        print(f"\nLoading wind rose data: {wind_path.name}")
        wind_df = pd.read_csv(wind_path)
        wind_id_col = find_column(wind_df, ["airport_id", "faa_id", "LOCID", "station"])
        wind_dir_col = find_column(wind_df, [
            "prevailing_wind_from", "wind_from_deg", "prevailing_direction",
        ])
        if wind_id_col and wind_dir_col:
            wind_data = dict(
                zip(wind_df[wind_id_col].str.strip().str.upper(),
                    wind_df[wind_dir_col])
            )
            print(f"  Loaded wind data for {len(wind_data)} airports")
    else:
        print("\n  Wind rose CSV not found; using hardcoded prevailing wind directions.")

    # Merge wind data: prefer loaded data, fall back to hardcoded
    merged_wind = {**PREVAILING_WIND_FROM, **wind_data}
    airports_out["prevailing_wind_from_deg"] = None
    if id_col:
        for idx, row in airports_out.iterrows():
            aid = str(row.get("airport_id", "")).strip().upper()
            if aid in merged_wind:
                airports_out.at[idx, "prevailing_wind_from_deg"] = merged_wind[aid]
            elif aid.startswith("K") and aid[1:] in merged_wind:
                airports_out.at[idx, "prevailing_wind_from_deg"] = merged_wind[aid[1:]]

    # Generate downwind cone polygons
    print("\nGenerating downwind cone polygons...")
    cone_records = []
    for idx, row in airports_out.iterrows():
        wind_dir = row.get("prevailing_wind_from_deg")
        if wind_dir is None or pd.isna(wind_dir):
            continue
        if row.geometry is None or row.geometry.is_empty:
            continue

        lon, lat = row.geometry.x, row.geometry.y
        cone = make_downwind_cone(
            lon, lat, float(wind_dir), radius_m=args.cone_radius
        )
        cone_records.append({
            "airport_id": row.get("airport_id"),
            "airport_name": row.get("airport_name"),
            "annual_piston_ops": row.get("annual_piston_ops"),
            "prevailing_wind_from_deg": wind_dir,
            "cone_radius_m": args.cone_radius,
            "geometry": cone,
        })

    cones_gdf = gpd.GeoDataFrame(cone_records, crs="EPSG:4326")
    print(f"  Generated {len(cones_gdf)} downwind cones")

    # Summary stats
    print("\n" + "=" * 60)
    print("Summary Statistics")
    print("=" * 60)
    print(f"  Total CA airports:           {len(airports_out)}")
    ops_count = airports_out["annual_piston_ops"].notna().sum()
    print(f"  Airports with ops data:      {ops_count}")
    if ops_count > 0:
        total_ops = airports_out["annual_piston_ops"].dropna().sum()
        print(f"  Total annual piston ops:     {total_ops:,.0f}")
    print(f"  Airports with wind data:     {airports_out['prevailing_wind_from_deg'].notna().sum()}")
    print(f"  Downwind cones generated:    {len(cones_gdf)}")
    if "lead_emissions_tpy" in airports_out.columns:
        lead_count = airports_out["lead_emissions_tpy"].notna().sum()
        if lead_count > 0:
            print(f"  Airports with NEI lead data: {lead_count}")

    # Write outputs
    for output_path, data, label in [
        (args.output_airports, airports_out, "airports"),
        (args.output_cones, cones_gdf, "wind cones"),
    ]:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f"\nWriting {label} to {output_path}...")
        data.to_file(output_path, driver="GeoJSON")
        size_mb = out.stat().st_size / (1024 * 1024)
        print(f"  Output size: {size_mb:.1f} MB")

    print("\nDone.")


if __name__ == "__main__":
    main()
