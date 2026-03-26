#!/usr/bin/env python3
"""
Build wildfire structural damage raster tiles.

P(destroyed, 30yr) = P(fire arrives, 30yr) × P(destroyed | fire)

Component 1 — P(fire arrives):
  FSim burn probability (WRC v2.0) × regional calibration factor.
  FSim has good spatial ranking (AUC benchmarking) but systematically
  underestimates absolute fire frequency by ~5-20x depending on region.
  Calibration factors computed from observed CalFire fire frequency
  (1996-2025) vs FSim BP on a 0.5-degree grid, smoothed 3x3.
  For zero-BP pixels in FHSZ zones, fill from 5km focal mean.

Component 2 — P(destroyed | fire):
  XGBoost trained on DINS dataset (23K structures with CFL + SSD + FHSZ).
  Features: CFL, SSD, FHSZ class. 5-fold CV AUC ~0.76.

Output: data/tiles/fire_risk/{lat}_{lon}.bin + .json
  uint16, value = P(destroyed, 30yr) × 10000

Citations:
  CalFire FRAP historical fire perimeters (1996-2025)
  Zamanialaei et al. 2025, Nature Communications 16:8041 (DINS)
  Scott et al. 2024, USFS Wildfire Risk to Communities v2.0
  CalFire Fire Hazard Severity Zones (SRA + LRA)
  Dillon et al. 2023, FSim calibration
"""

import json
import pickle
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.ndimage import zoom
from scipy.signal import fftconvolve
from scipy.spatial import cKDTree
from shapely.geometry import box, Point
from shapely.strtree import STRtree
from rasterio.features import rasterize as rio_rasterize
from rasterio.transform import from_bounds
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from xgboost import XGBClassifier

# ── Paths ──

DINS_PATH = Path("/tmp/dins_repo/data/concatenated_df.csv")
CFL_TILES = Path("data/tiles/cfl")
SSD_TILES = Path("data/tiles/ssd")
FHSZ_TILES = Path("data/tiles/fire_zones")
BP_TILES = Path("data/tiles/burn_probability")
CALIBRATION = Path("data/processed/fsim_calibration_factors.json")
OUTPUT_TILES = Path("data/tiles/fire_risk")
MODEL_PATH = Path("data/processed/fire_damage_model.pkl")

CA_LAT_MIN, CA_LAT_MAX = 32.5, 42.0
CA_LON_MIN, CA_LON_MAX = -124.5, -114.0

FHSZ_ENCODE = {"Very High": 3, "High": 2, "Moderate": 1, "NonWildland": 0, "": 0}

# Focal mean radius for filling zero-BP pixels (5km at 30m)
FILL_RADIUS_PX = 167


# ── Tile I/O ──

def load_uint16_tile(tiles_dir, key):
    bpath = tiles_dir / f"{key}.bin"
    jpath = tiles_dir / f"{key}.json"
    if not bpath.exists() or not jpath.exists():
        return None, None
    with open(jpath) as f:
        meta = json.load(f)
    raw = np.frombuffer(bpath.read_bytes(), dtype=np.uint16)
    rows, cols = meta["rows"], meta["cols"]
    if len(raw) < rows * cols:
        return None, None
    return raw[:rows * cols].reshape(rows, cols), meta


def rasterize_fhsz(key, rows, cols, transform):
    """Rasterize FHSZ zones into encoded class grid."""
    from shapely.geometry import shape as shapely_shape
    jpath = FHSZ_TILES / f"{key}.json"
    result = np.zeros((rows, cols), dtype=np.float64)
    if not jpath.exists():
        return result
    with open(jpath) as f:
        fc = json.load(f)
    for target_cls in ["Very High", "High", "Moderate"]:
        val = FHSZ_ENCODE[target_cls]
        shapes = []
        for feat in fc.get("features", []):
            if feat.get("properties", {}).get("hazard_class") == target_cls:
                try:
                    shapes.append((shapely_shape(feat["geometry"]), val))
                except Exception:
                    pass
        if shapes:
            mask = rio_rasterize(shapes, out_shape=(rows, cols),
                                 transform=transform, fill=0, dtype=np.float64)
            result = np.maximum(result, mask)
    return result


def nearby_max_cfl(cfl_data, search_radius=7):
    rows, cols = cfl_data.shape
    result = cfl_data.astype(np.float64)
    sr = min(search_radius, rows // 10)
    for r, c in zip(*np.where(cfl_data == 0)):
        sub = cfl_data[max(0, r-sr):min(rows, r+sr+1),
                       max(0, c-sr):min(cols, c+sr+1)]
        mx = sub.max()
        if mx > 0:
            result[r, c] = mx
    return result


# ── Calibration ──

def load_calibration():
    """Load FSim→observed calibration factors."""
    with open(CALIBRATION) as f:
        cal = json.load(f)
    return (
        np.array(cal["factors"]),
        cal["lat_min"], cal["lon_min"],
        cal["grid_res"], cal["n_lat"], cal["n_lon"],
    )


_fair_tree = None
_fair_shares = None

def get_fair_share(lat, lon):
    """Look up FAIR Plan share for a lat/lon via nearest zip centroid."""
    global _fair_tree, _fair_shares
    if _fair_tree is None:
        import pgeocode
        nomi = pgeocode.Nominatim("us")
        ca_zips = nomi._data[nomi._data["state_code"] == "CA"][
            ["postal_code", "latitude", "longitude"]
        ].dropna()
        with open(Path("data/processed/fair_share_by_zip.json")) as f:
            fair_map = json.load(f)
        valid = [(row["latitude"], row["longitude"], fair_map.get(row["postal_code"], 0))
                 for _, row in ca_zips.iterrows()]
        coords = np.array([(v[0], v[1]) for v in valid])
        _fair_shares = np.array([v[2] for v in valid])
        _fair_tree = cKDTree(coords)
    _, idx = _fair_tree.query([lat, lon])
    return _fair_shares[idx]


def get_calibration_factor(cal_data, lat, lon):
    """Look up calibration factor for a lat/lon."""
    factors, lat_min, lon_min, res, n_lat, n_lon = cal_data
    i = int((lat - lat_min) / res)
    j = int((lon - lon_min) / res)
    i = max(0, min(i, n_lat - 1))
    j = max(0, min(j, n_lon - 1))
    return factors[i][j]


# ── P(destroyed|fire) model ──

def train_damage_model():
    if MODEL_PATH.exists():
        print(f"Loading cached model from {MODEL_PATH}")
        with open(MODEL_PATH, "rb") as f:
            return pickle.load(f)

    print("Training P(destroyed|fire) model on DINS data...")
    dins = pd.read_csv(DINS_PATH)
    dins = dins[dins["DAMAGE"].notna() & (dins["DAMAGE"] != "Inaccessible")].copy()
    dins["destroyed"] = (dins["DAMAGE"] == "Destroyed (>50%)").astype(int)
    print(f"  {len(dins)} structures, {dins['destroyed'].mean()*100:.0f}% destroyed")

    # P(destroyed|fire) uses CFL + SSD + FAIR Plan share.
    # FHSZ dropped from damage model (acts as mitigation proxy in DINS).
    # FAIR share captures community-level vulnerability (building age,
    # code compliance, defensible space) that pixel-level features miss.

    # Add FAIR share
    import pgeocode
    nomi = pgeocode.Nominatim("us")
    ca_zips = nomi._data[nomi._data["state_code"] == "CA"][
        ["postal_code", "latitude", "longitude"]
    ].dropna()
    zip_tree = cKDTree(ca_zips[["latitude", "longitude"]].values)
    _, zip_idx = zip_tree.query(dins[["LATITUDE", "LONGITUDE"]].values)
    dins["zip"] = ca_zips["postal_code"].values[zip_idx]

    import json as _json
    with open(Path("data/processed/fair_share_by_zip.json")) as f:
        fair_map = _json.load(f)
    dins["fair_share"] = dins["zip"].map(fair_map).fillna(0)

    features = ["FLAME", "Distance", "fair_share"]
    sub = dins.dropna(subset=features + ["destroyed"])
    X = sub[features].values
    y = sub["destroyed"].values
    print(f"  {len(sub)} usable structures")

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    aucs = []
    for train_idx, test_idx in skf.split(X, y):
        m = XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.1,
                          min_child_weight=10, random_state=42, eval_metric="logloss")
        m.fit(X[train_idx], y[train_idx])
        aucs.append(roc_auc_score(y[test_idx], m.predict_proba(X[test_idx])[:, 1]))
    print(f"  5-fold CV AUC: {np.mean(aucs):.3f} (±{np.std(aucs):.3f})")

    model = XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.1,
                          min_child_weight=10, random_state=42, eval_metric="logloss")
    model.fit(X, y)

    imp = model.feature_importances_
    print("  Feature importance:")
    for f, i in sorted(zip(features, imp), key=lambda x: -x[1]):
        print(f"    {f:20s}: {i:.3f}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    return model


# ── Main ──

def main():
    OUTPUT_TILES.mkdir(parents=True, exist_ok=True)

    damage_model = train_damage_model()
    cal_data = load_calibration()
    print(f"\nLoaded calibration factors")

    written = 0
    t0 = time.time()

    for tile_lat in np.round(np.arange(CA_LAT_MIN, CA_LAT_MAX, 0.1), 1):
        for tile_lon in np.round(np.arange(CA_LON_MIN, CA_LON_MAX, 0.1), 1):
            key = f"{tile_lat:.1f}_{tile_lon:.1f}"

            bp_data, bp_meta = load_uint16_tile(BP_TILES, key)
            if bp_data is None:
                continue

            rows, cols = bp_data.shape
            south = bp_meta["bounds"]["south"]
            west = bp_meta["bounds"]["west"]
            transform = from_bounds(west, south, west + 0.1, south + 0.1, cols, rows)

            # ── P(fire arrives) = calibrated FSim BP ──
            #
            # FSim has good spatial ranking but underestimates absolute rates
            # by a regional factor. Calibration fixes the scale.
            #
            # For non-burnable pixels (developed areas), FSim reads near-zero
            # even after WRC oozing. These pixels get the tile median BP,
            # because fire frequency is spatially autocorrelated over ~12km
            # (empirically flat reburn rate from 0 to 12km from fire edge).
            # The tile median is the best estimate of the local fire climate.

            cal_factor = get_calibration_factor(cal_data, tile_lat + 0.05, tile_lon + 0.05)
            bp_calibrated = bp_data.astype(np.float64) / 100000.0 * cal_factor

            # For developed pixels where FSim reads near-zero, promote to
            # tile median BP — BUT only if the pixel is in a High or Very High
            # FHSZ zone. This prevents over-weighting small urban fires in
            # low-hazard areas while correctly capturing WUI exposure.
            # Justified by empirically flat reburn autocorrelation to 12km.
            fhsz_grid = rasterize_fhsz(key, rows, cols, transform)
            nonzero = bp_calibrated[bp_calibrated > 0]
            if len(nonzero) > 0:
                threshold = np.percentile(nonzero, 25)
                tile_median = np.median(nonzero)
                # Only promote pixels in High (2) or Very High (3) FHSZ zones
                promote_mask = (bp_calibrated < threshold) & (fhsz_grid >= 2)
                bp_calibrated = np.where(promote_mask, tile_median, bp_calibrated)

            # Fill remaining zeros with 5km focal mean
            zero_mask = bp_calibrated == 0
            if zero_mask.any() and (~zero_mask).any():
                sr = min(FILL_RADIUS_PX, rows // 2)
                y, x = np.ogrid[-sr:sr+1, -sr:sr+1]
                kernel = (x**2 + y**2 <= sr**2).astype(float)
                kernel /= kernel.sum()
                smoothed = fftconvolve(bp_calibrated, kernel, mode="same")
                bp_calibrated = np.where(zero_mask, smoothed, bp_calibrated)

            # Convert to 30yr probability
            p_fire_30 = 1 - np.power(1 - np.clip(bp_calibrated, 0, 1), 30)

            if p_fire_30.max() < 0.001:
                continue

            # ── P(destroyed | fire) ──

            cfl_data, _ = load_uint16_tile(CFL_TILES, key)
            cfl = nearby_max_cfl(cfl_data) if cfl_data is not None else np.zeros((rows, cols))

            ssd_raw, _ = load_uint16_tile(SSD_TILES, key)
            if ssd_raw is not None and ssd_raw.shape != (rows, cols):
                ssd = zoom(ssd_raw.astype(np.float64),
                           (rows / ssd_raw.shape[0], cols / ssd_raw.shape[1]), order=0)
            elif ssd_raw is not None:
                ssd = ssd_raw.astype(np.float64)
            else:
                ssd = np.zeros((rows, cols))

            # P(destroyed|fire) from CFL + SSD + FAIR share
            # FAIR share per tile (zip-level, looked up from nearest zip centroid)
            fair_val = get_fair_share(tile_lat + 0.05, tile_lon + 0.05)
            n = rows * cols
            X = np.column_stack([cfl.ravel(), ssd.ravel(), np.full(n, fair_val)])
            p_destroyed = damage_model.predict_proba(X)[:, 1].reshape(rows, cols)

            # ── Combined ──
            p_total = p_fire_30 * p_destroyed
            encoded = (p_total * 10000).clip(0, 65534).astype(np.uint16)

            if encoded.max() == 0:
                continue

            (OUTPUT_TILES / f"{key}.bin").write_bytes(encoded.tobytes())
            with open(OUTPUT_TILES / f"{key}.json", "w") as f:
                json.dump({
                    "rows": rows, "cols": cols,
                    "bounds": {"south": float(south), "west": float(west)},
                    "dtype": "uint16", "scale": 10000,
                    "units": "P(destroyed_30yr)",
                }, f)

            written += 1
            if written % 100 == 0:
                print(f"  {written} tiles ({time.time() - t0:.0f}s)...")

    print(f"\n  {written} tiles written ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
