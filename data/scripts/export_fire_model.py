#!/usr/bin/env python3
"""
Export the trained fire damage model as a simple lookup table for the Worker.

Instead of shipping XGBoost to a Cloudflare Worker, we pre-compute P(damage|fire)
for a grid of (SSD, flame_length, fair_share) values and export as a JSON lookup.
The Worker does trilinear interpolation at query time.

This avoids the complexity of running ML inference in the Worker while preserving
the model's learned relationships.
"""

import json
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier
from scipy.spatial import cKDTree
import warnings
warnings.filterwarnings('ignore')

DINS_PATH = '/tmp/dins_repo/data/concatenated_df.csv'
FAIR_PATH = 'data/processed/fair_plan_by_zip.csv'
CENSUS_PATH = 'data/processed/census_housing_units_by_zip.csv'
OUT_PATH = 'data/processed/fire_damage_model.json'


def build_fair_share():
    fair = pd.read_csv(FAIR_PATH, dtype={'zip': str})
    census = pd.read_csv(CENSUS_PATH, dtype={'zip': str})
    merged = census.merge(fair[['zip', 'pif_2025']], on='zip', how='left')
    merged['pif_2025'] = merged['pif_2025'].fillna(0)
    merged['housing_units'] = merged['housing_units'].astype(float)
    merged['fair_share'] = np.where(
        merged['housing_units'] > 0,
        merged['pif_2025'] / merged['housing_units'],
        0
    )
    merged['fair_share'] = merged['fair_share'].clip(0, 1.0)
    return merged[['zip', 'fair_share']]


def assign_zipcodes(df):
    import pgeocode
    nomi = pgeocode.Nominatim('us')
    ca_zips = nomi._data[nomi._data['state_code'] == 'CA'][
        ['postal_code', 'latitude', 'longitude']].dropna()
    ca_zips = ca_zips.rename(columns={'postal_code': 'zip'})
    tree = cKDTree(ca_zips[['latitude', 'longitude']].values)
    _, idx = tree.query(df[['LATITUDE', 'LONGITUDE']].values)
    df['assigned_zip'] = ca_zips['zip'].values[idx]
    return df


def train_model():
    print("Loading and preparing data...")
    df = pd.read_csv(DINS_PATH)
    df = df[df['DAMAGE'].notna() & (df['DAMAGE'] != 'Inaccessible')].copy()
    df['damaged'] = df['DAMAGE'].isin([
        'Destroyed (>50%)', 'Major (26-50%)', 'Minor (10-25%)', 'Affected (1-9%)'
    ]).astype(int)

    df = assign_zipcodes(df)
    fair_share = build_fair_share()
    df = df.merge(fair_share, left_on='assigned_zip', right_on='zip', how='left')
    df['fair_share'] = df['fair_share'].fillna(0)

    features = ['Distance', 'FLAME', 'fair_share']
    sub = df.dropna(subset=features + ['damaged'])
    X = sub[features].values
    y = sub['damaged'].values

    print(f"Training on {len(X)} samples...")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = XGBClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.1,
        random_state=42, eval_metric='logloss',
    )
    model.fit(X_scaled, y)

    return model, scaler


def export_lookup(model, scaler):
    """Generate a 3D lookup table of P(damage|fire) values."""

    # Grid points for each dimension
    ssd_values = [2, 5, 10, 15, 20, 30, 50, 75, 100, 150, 200, 300]  # meters
    flame_values = [0, 1, 2, 4, 6, 8, 12, 16, 20, 30, 40, 60]  # feet
    fair_values = [0.0, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.65, 0.80, 1.0]

    # Pre-compute all grid points
    grid = []
    for ssd in ssd_values:
        for fl in flame_values:
            for fs in fair_values:
                grid.append([ssd, fl, fs])

    grid_arr = np.array(grid)
    grid_scaled = scaler.transform(grid_arr)
    probs = model.predict_proba(grid_scaled)[:, 1]

    # Reshape into 3D array
    n_ssd = len(ssd_values)
    n_fl = len(flame_values)
    n_fs = len(fair_values)
    prob_grid = probs.reshape(n_ssd, n_fl, n_fs)

    # Export
    result = {
        "model": "fire_damage_v1",
        "description": "P(structural damage | fire reaches area). "
                       "Trained on Zamanialaei et al. 2025 DINS dataset (47K structures, "
                       "5 CA fires 2017-2020) + FAIR Plan share (CFP/Census 2022-2025). "
                       "AUC=0.852 on 5-fold CV.",
        "features": {
            "ssd_m": {
                "description": "Structure Separation Distance (meters)",
                "values": ssd_values,
            },
            "flame_ft": {
                "description": "Conditional Flame Length (feet, from USFS WRC CFL)",
                "values": flame_values,
            },
            "fair_share": {
                "description": "FAIR Plan policies / housing units in zip code (0-1)",
                "values": fair_values,
            },
        },
        "probabilities": prob_grid.round(4).tolist(),
        "citations": [
            "Zamanialaei et al. 2025, Nature Communications 16:8041",
            "California FAIR Plan Association, cfpnet.com/key-statistics-data/",
            "US Census ACS 2022, Table B25001 (Housing Units by ZCTA)",
            "Scott et al. 2024, USFS Wildfire Risk to Communities v2.0",
        ],
    }

    with open(OUT_PATH, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"\nExported lookup table to {OUT_PATH}")
    print(f"  Grid: {n_ssd} × {n_fl} × {n_fs} = {n_ssd * n_fl * n_fs} points")
    print(f"  File size: {len(json.dumps(result)):,} bytes")

    # Print some key values
    print("\nSample P(damage|fire) values:")
    print(f"  {'Scenario':40s}  {'SSD':>5s}  {'FL':>4s}  {'FAIR':>5s}  {'P(dmg)':>7s}")
    print("  " + "-" * 65)
    samples = [
        ("Low risk (no FAIR, low flame)", 0, 0, 0),
        ("Montara-like", 3, 2, 4),
        ("Arnold-like", 3, 7, 9),
        ("Paradise-like", 0, 8, 6),
    ]
    for desc, si, fi, fsi in samples:
        p = prob_grid[si, fi, fsi]
        print(f"  {desc:40s}  {ssd_values[si]:4d}m  {flame_values[fi]:3d}ft  "
              f"{fair_values[fsi]:4.0%}  {p:6.1%}")


def main():
    model, scaler = train_model()
    export_lookup(model, scaler)

    # Also export the FAIR Plan share data as a simple zip->share lookup
    # for the Worker to use at query time
    fair_share = build_fair_share()
    fair_dict = {row['zip']: round(row['fair_share'], 4)
                 for _, row in fair_share.iterrows() if row['fair_share'] > 0}

    fair_out = 'data/processed/fair_share_by_zip.json'
    with open(fair_out, 'w') as f:
        json.dump(fair_dict, f)
    print(f"\nExported FAIR share lookup: {len(fair_dict)} zips to {fair_out}")
    print(f"  File size: {len(json.dumps(fair_dict)):,} bytes")


if __name__ == '__main__':
    main()
