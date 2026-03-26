#!/usr/bin/env python3
"""
Train wildfire structure loss model using DINS dataset + normalized FAIR Plan share.

Features:
  - Distance (SSD) — structure separation distance
  - FLAME — flame length (ft)
  - YEARBUILT — year built
  - fair_share — FAIR Plan policies / total housing units in zip (0-1 scale)

FAIR Plan share normalized by Census housing units avoids the raw-count
problem where large zips dominate. A share of 0.65 means 65% of homes
in the zip rely on the insurer of last resort.
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import accuracy_score, roc_auc_score, precision_score, recall_score
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier
from scipy.spatial import cKDTree
import warnings
warnings.filterwarnings('ignore')

DINS_PATH = '/tmp/dins_repo/data/concatenated_df.csv'
FAIR_PATH = 'data/processed/fair_plan_by_zip.csv'
CENSUS_PATH = 'data/processed/census_housing_units_by_zip.csv'


def build_fair_share():
    """Compute FAIR Plan share = policies / housing units by zip."""
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
    # Cap at 1.0 (some tiny zips have >100% due to zip/ZCTA mismatch)
    merged['fair_share'] = merged['fair_share'].clip(0, 1.0)
    return merged[['zip', 'fair_share', 'housing_units', 'pif_2025']]


def assign_zipcodes(df):
    """Assign zip codes to structures using nearest CA zip centroid."""
    import pgeocode
    nomi = pgeocode.Nominatim('us')
    ca_zips = nomi._data[nomi._data['state_code'] == 'CA'][
        ['postal_code', 'latitude', 'longitude']].dropna()
    ca_zips = ca_zips.rename(columns={'postal_code': 'zip'})

    tree = cKDTree(ca_zips[['latitude', 'longitude']].values)
    coords = df[['LATITUDE', 'LONGITUDE']].values
    _, idx = tree.query(coords)
    df['assigned_zip'] = ca_zips['zip'].values[idx]
    return df


def load_and_prepare():
    print("Loading DINS data...")
    df = pd.read_csv(DINS_PATH)
    df = df[df['DAMAGE'].notna() & (df['DAMAGE'] != 'Inaccessible')].copy()
    df['damaged'] = df['DAMAGE'].isin([
        'Destroyed (>50%)', 'Major (26-50%)', 'Minor (10-25%)', 'Affected (1-9%)'
    ]).astype(int)
    print(f"  {len(df)} labeled structures")

    print("Assigning zip codes from lat/lon...")
    df = assign_zipcodes(df)

    print("Computing FAIR Plan share...")
    fair_share = build_fair_share()
    df = df.merge(fair_share[['zip', 'fair_share']], left_on='assigned_zip',
                  right_on='zip', how='left')
    df['fair_share'] = df['fair_share'].fillna(0)

    matched = (df['fair_share'] > 0).sum()
    print(f"  FAIR share matched: {matched}/{len(df)} ({matched/len(df)*100:.0f}%)")
    print(f"  FAIR share range: {df['fair_share'].min():.3f} - {df['fair_share'].max():.3f}")
    print(f"  Mean (where >0): {df.loc[df['fair_share']>0, 'fair_share'].mean():.3f}")

    return df


def evaluate(df, features, name):
    sub = df.dropna(subset=features + ['damaged'])
    X = sub[features].values
    y = sub['damaged'].values

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    metrics = {'acc': [], 'auc': []}

    for train_idx, test_idx in skf.split(X, y):
        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X[train_idx])
        X_te = scaler.transform(X[test_idx])

        model = XGBClassifier(
            n_estimators=200, max_depth=6, learning_rate=0.1,
            random_state=42, eval_metric='logloss',
        )
        model.fit(X_tr, y[train_idx])

        y_pred = model.predict(X_te)
        y_prob = model.predict_proba(X_te)[:, 1]

        metrics['acc'].append(accuracy_score(y[test_idx], y_pred))
        metrics['auc'].append(roc_auc_score(y[test_idx], y_prob))

    print(f"  {name:45s} N={len(X):6d}  "
          f"Acc={np.mean(metrics['acc']):.3f}  AUC={np.mean(metrics['auc']):.3f}")
    return np.mean(metrics['auc'])


def scenario_predictions(df):
    # Train Dist + Flame + FAIR share model
    features = ['Distance', 'FLAME', 'fair_share']
    sub = df.dropna(subset=features + ['damaged'])
    X = sub[features].values
    y = sub['damaged'].values

    scaler = StandardScaler()
    model = XGBClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.1,
        random_state=42, eval_metric='logloss',
    )
    model.fit(scaler.fit_transform(X), y)

    imp = model.feature_importances_
    print("\nFeature importance (Dist + Flame + FAIR share):")
    for fname, importance in sorted(zip(features, imp), key=lambda x: -x[1]):
        print(f"  {fname:20s}: {importance:.3f}")

    scenarios = [
        # (description, SSD_m, flame_ft, fair_share)
        ("Downtown SF (no FAIR)",                 5,   0, 0.00),
        ("Suburban, low flame, low FAIR",        15,   2, 0.03),
        ("Montara (mod FAIR, coastal)",          20,   3, 0.15),
        ("WUI edge, mod flame, mod FAIR",        30,   6, 0.20),
        ("Arnold (65% FAIR share)",              30,  12, 0.65),
        ("Paradise-like (dense, extreme)",        8,  20, 0.40),
        ("Extreme: rural, max flame, max FAIR", 100,  30, 0.90),
    ]

    print("\nP(damage | fire) predictions:")
    print(f"  {'Scenario':45s}  {'SSD':>5s}  {'FL':>4s}  {'FAIR%':>6s}  {'P(dmg)':>8s}")
    print("  " + "-" * 75)
    for desc, ssd, flame, fs in scenarios:
        x = scaler.transform([[ssd, flame, fs]])
        prob = model.predict_proba(x)[0][1]
        print(f"  {desc:45s}  {ssd:4d}m  {flame:3d}ft  {fs:5.0%}  {prob:7.1%}")

    # Also train full model with FAIR share
    print("\n\nFull model + FAIR share:")
    features_full = ['Distance', 'FLAME', 'YEARBUILT', 'EMBER', 'fair_share']
    sub_full = df.dropna(subset=features_full + ['damaged'])
    X_f = sub_full[features_full].values
    y_f = sub_full['damaged'].values
    scaler_f = StandardScaler()
    model_f = XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.1,
                            random_state=42, eval_metric='logloss')
    model_f.fit(scaler_f.fit_transform(X_f), y_f)
    imp_f = model_f.feature_importances_
    for fname, importance in sorted(zip(features_full, imp_f), key=lambda x: -x[1]):
        print(f"  {fname:20s}: {importance:.3f}")


def main():
    df = load_and_prepare()

    print("\n=== Model comparison ===\n")

    configs = [
        ("Dist only",                              ['Distance']),
        ("Dist + Flame",                           ['Distance', 'FLAME']),
        ("Dist + FAIR share",                      ['Distance', 'fair_share']),
        ("Dist + Flame + FAIR share",              ['Distance', 'FLAME', 'fair_share']),
        ("Dist + Flame + Year",                    ['Distance', 'FLAME', 'YEARBUILT']),
        ("Dist + Flame + Year + FAIR share",       ['Distance', 'FLAME', 'YEARBUILT', 'fair_share']),
        ("Full (Dist+Flame+Year+Ember)",           ['Distance', 'FLAME', 'YEARBUILT', 'EMBER']),
        ("Full + FAIR share",                      ['Distance', 'FLAME', 'YEARBUILT', 'EMBER', 'fair_share']),
    ]

    for name, features in configs:
        evaluate(df, features, name)

    print("\n=== Scenario predictions ===")
    scenario_predictions(df)


if __name__ == '__main__':
    main()
