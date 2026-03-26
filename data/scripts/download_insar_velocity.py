#!/usr/bin/env python3
"""
Download OPERA-DISP-S1 InSAR velocity data and classify landslide deposits
as active (>2mm/yr) or dormant.

Uses NASA's OPERA Surface Displacement product (Sentinel-1 InSAR) to measure
ground velocity at each USGS landslide inventory polygon.

Velocity is computed by linear regression through multiple displacement
acquisitions spanning at least one full year, which removes seasonal bias
from wet-season/dry-season displacement cycles.

For each polygon, all pixels within the boundary are sampled. Both the maximum
and mean velocity are reported. The maximum is used for active/dormant
classification — a deposit with an active head scarp and a stable toe is
still an active deposit.

Requirements:
  - NASA Earthdata Login credentials (free: https://urs.earthdata.nasa.gov)
  - Set environment variables EARTHDATA_USER and EARTHDATA_PASS, or create
    ~/.netrc with: machine urs.earthdata.nasa.gov login <user> password <pass>
  - pip install asf_search h5py geopandas numpy pyproj rasterio scipy

Data source:
  OPERA L3 DISP-S1 V1, ASF DAAC
  doi:10.5067/SNWG/OPL3DISPS1-V1

Output:
  data/processed/landslide_inventory_velocity.geojson
    — inventory polygons with velocity_max_mm_yr, velocity_mean_mm_yr,
      is_active, and landslide_tier properties

Citations:
  OPERA Project, JPL/Caltech, 2024
  Belair et al. 2025, USGS landslide inventory v3
  Handwerger et al. 2019, JGR Earth Surface, doi:10.1029/2019JF005035
"""

import argparse
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import h5py
import numpy as np
from scipy import stats

try:
    import asf_search as asf
except ImportError:
    print("ERROR: asf_search not installed. Run: pip install asf_search", file=sys.stderr)
    sys.exit(1)

try:
    from pyproj import Transformer
except ImportError:
    print("ERROR: pyproj not installed. Run: pip install pyproj", file=sys.stderr)
    sys.exit(1)

try:
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    from shapely.ops import transform as shapely_transform
except ImportError:
    print("ERROR: rasterio/shapely not installed. Run: pip install rasterio shapely", file=sys.stderr)
    sys.exit(1)


# California bounding box (generous)
CA_BBOX = (-124.5, 32.0, -114.0, 42.5)

# Active/dormant velocity threshold (mm/yr).
# 3.5 mm/yr = 2× typical Sentinel-1 velocity standard deviation (~1.75 mm/yr).
# Below this threshold, signal is indistinguishable from InSAR noise.
# Matches the convention in the Italian landslide InSAR literature
# (Zinno et al. 2021, Sentinel-1 P-SBAS benchmarking).
VELOCITY_THRESHOLD_MM_YR = 3.5

# Minimum confidence for inventory polygons
MIN_CONFIDENCE = 3

# Minimum number of granules needed to compute velocity.
# Need enough temporal spread to fit a trend that isn't dominated by noise.
MIN_GRANULES = 4

# Minimum time span in years. Must cover a full seasonal cycle.
MIN_TIME_SPAN_YEARS = 1.0

INVENTORY_PATH = Path("data/raw/US_Landslide_v3_shp/us_ls_v3_poly.shp")
OUTPUT_PATH = Path("data/processed/landslide_inventory_velocity.geojson")


def get_earthdata_session():
    """Create an authenticated ASF session using Earthdata credentials."""
    user = os.environ.get("EARTHDATA_USER")
    password = os.environ.get("EARTHDATA_PASS")
    if user and password:
        return asf.ASFSession().auth_with_creds(user, password)

    # Try .netrc
    netrc_path = Path.home() / ".netrc"
    if netrc_path.exists():
        import netrc as netrc_mod
        try:
            creds = netrc_mod.netrc()
            auth = creds.authenticators("urs.earthdata.nasa.gov")
            if auth:
                login, _, password = auth
                return asf.ASFSession().auth_with_creds(login, password)
        except Exception:
            pass

    print("ERROR: No Earthdata credentials found.", file=sys.stderr)
    print("  Set EARTHDATA_USER and EARTHDATA_PASS environment variables,", file=sys.stderr)
    print("  or create ~/.netrc with:", file=sys.stderr)
    print("  machine urs.earthdata.nasa.gov login <user> password <pass>", file=sys.stderr)
    sys.exit(1)


def find_granules_per_frame():
    """Find OPERA-DISP granules covering California, grouped by frame.

    Returns dict of frame_id -> list of (granule, acquisition_date) sorted by date.
    """
    print("Searching for OPERA-DISP granules covering California...")

    results = asf.search(
        dataset=asf.DATASET.OPERA_S1,
        processingLevel='DISP-S1',
        intersectsWith=(
            f"POLYGON(({CA_BBOX[0]} {CA_BBOX[1]}, {CA_BBOX[2]} {CA_BBOX[1]}, "
            f"{CA_BBOX[2]} {CA_BBOX[3]}, {CA_BBOX[0]} {CA_BBOX[3]}, "
            f"{CA_BBOX[0]} {CA_BBOX[1]}))"
        ),
        maxResults=50000,
    )

    print(f"  Found {len(results)} total granules")

    # Group by frame
    by_frame: dict[str, list[tuple]] = {}
    for r in results:
        props = r.properties
        frame_id = props.get("frameNumber")
        if not frame_id:
            continue
        start_time = props.get("startTime", "")
        try:
            acq_date = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        by_frame.setdefault(frame_id, []).append((r, acq_date))

    # Sort each frame's granules by date
    for fid in by_frame:
        by_frame[fid].sort(key=lambda x: x[1])

    print(f"  {len(by_frame)} unique frames")
    for fid, granules in sorted(by_frame.items())[:5]:
        dates = [g[1].strftime("%Y-%m-%d") for g in granules]
        print(f"    Frame {fid}: {len(granules)} granules, {dates[0]} to {dates[-1]}")
    if len(by_frame) > 5:
        print(f"    ... and {len(by_frame) - 5} more frames")

    return by_frame


def select_granules_for_velocity(granules, n_target=8):
    """Select a subset of granules well-distributed across time for velocity estimation.

    Picks granules spread across the full time range, ensuring coverage of
    different seasons to avoid seasonal bias.

    Args:
        granules: list of (granule, date) sorted by date
        n_target: target number of granules to select

    Returns:
        selected subset of (granule, date) tuples
    """
    if len(granules) <= n_target:
        return granules

    # Check time span
    span_years = (granules[-1][1] - granules[0][1]).days / 365.25
    if span_years < MIN_TIME_SPAN_YEARS:
        return granules  # Use all, even if we'd prefer fewer

    # Select evenly spaced granules across the time range
    indices = np.linspace(0, len(granules) - 1, n_target, dtype=int)
    return [granules[i] for i in indices]


def read_displacement_from_granule(granule, session, tmpdir):
    """Download and read short_wavelength_displacement from an HDF5 granule.

    Returns (displacement_2d, geotransform_dict) or (None, None) on failure.
    The displacement is in meters, cumulative from the frame's reference date.
    """
    filename = granule.properties.get("fileName", "granule.h5")
    filepath = Path(tmpdir) / filename

    # Download if not already cached
    if not filepath.exists():
        granule.download(path=tmpdir, session=session)

    if not filepath.exists():
        h5_files = list(Path(tmpdir).glob("*.h5")) + list(Path(tmpdir).glob("*.nc"))
        if not h5_files:
            return None, None
        filepath = h5_files[-1]

    with h5py.File(filepath, "r") as f:
        # Read short_wavelength_displacement (local deformation with tectonic/
        # atmospheric signals removed — this is the correct field for landslides)
        if "short_wavelength_displacement" not in f:
            print(f"      WARNING: No short_wavelength_displacement in {filename}")
            return None, None
        disp = f["short_wavelength_displacement"][:].astype(np.float32)

        # Apply recommended mask (1 = valid pixel)
        if "recommended_mask" in f:
            mask = f["recommended_mask"][:]
            disp = np.where(mask == 1, disp, np.nan)

        # Extract geotransform from coordinate arrays
        geo = {}
        if "x" in f and "y" in f:
            x = f["x"][:]
            y = f["y"][:]
            geo["x_first"] = float(x[0])
            geo["y_first"] = float(y[0])
            geo["x_step"] = float(x[1] - x[0]) if len(x) > 1 else 30.0
            geo["y_step"] = float(y[1] - y[0]) if len(y) > 1 else -30.0

        # Extract CRS from spatial_ref dataset.
        # OPERA-DISP uses UTM projections (e.g., EPSG:32611 for zone 11N).
        # The crs_wkt attribute contains the full WKT string.
        if "spatial_ref" in f:
            sr = f["spatial_ref"]
            attrs = dict(sr.attrs) if hasattr(sr, "attrs") else {}
            geo["crs"] = attrs.get("crs_wkt", attrs.get("spatial_ref", ""))
            # Also grab GeoTransform if available (more reliable than x/y arrays)
            if "GeoTransform" in attrs:
                gt = str(attrs["GeoTransform"]).split()
                if len(gt) >= 6:
                    geo["x_first"] = float(gt[0])
                    geo["x_step"] = float(gt[1])
                    geo["y_first"] = float(gt[3])
                    geo["y_step"] = float(gt[5])

    # Clean up this file to save disk space
    filepath.unlink(missing_ok=True)

    return disp, geo


def compute_velocity_stack(frame_granules, session, tmpdir):
    """Compute pixel-wise velocity from multiple displacement granules.

    Uses linear regression through displacement values at each pixel.

    Returns (velocity_mm_yr, residual_mm_yr, geotransform, crs) or Nones.
    velocity is in mm/yr (LOS direction, positive = toward satellite).
    """
    selected = select_granules_for_velocity(frame_granules)

    if len(selected) < MIN_GRANULES:
        return None, None, None, None

    span = (selected[-1][1] - selected[0][1]).days / 365.25
    if span < MIN_TIME_SPAN_YEARS:
        return None, None, None, None

    # Read all displacement grids
    displacements = []
    dates = []
    geo = None

    for granule, acq_date in selected:
        disp, g = read_displacement_from_granule(granule, session, tmpdir)
        if disp is not None:
            displacements.append(disp)
            dates.append(acq_date)
            if geo is None and g:
                geo = g

    if len(displacements) < MIN_GRANULES:
        return None, None, None, None

    # Stack into 3D array (time, rows, cols)
    # All grids should be the same size for a given frame
    try:
        stack = np.stack(displacements, axis=0)
    except ValueError:
        # Shape mismatch between granules — skip this frame
        print(f"    WARNING: Granule shape mismatch, skipping frame")
        return None, None, None, None

    # Time axis in years from first acquisition
    t0 = dates[0]
    t_years = np.array([(d - t0).days / 365.25 for d in dates])

    # Pixel-wise linear regression
    # velocity = slope of displacement vs time
    n_times, n_rows, n_cols = stack.shape

    # Reshape for vectorized regression
    stack_2d = stack.reshape(n_times, -1)  # (n_times, n_pixels)

    # Count valid observations per pixel
    valid_mask = ~np.isnan(stack_2d)
    n_valid = valid_mask.sum(axis=0)

    velocity_flat = np.full(n_rows * n_cols, np.nan)
    residual_flat = np.full(n_rows * n_cols, np.nan)

    # Only compute where we have enough temporal samples
    compute_mask = n_valid >= MIN_GRANULES

    if compute_mask.sum() == 0:
        return None, None, None, None

    # Vectorized linear regression using numpy
    # For pixels with no NaN, we can use a fast path
    all_valid = (n_valid == n_times)

    if all_valid.sum() > 0:
        # Fast path: no missing data
        y = stack_2d[:, all_valid]  # (n_times, n_good_pixels)
        x = t_years

        # Linear regression: slope = (n*sum(xy) - sum(x)*sum(y)) / (n*sum(x²) - (sum(x))²)
        n = len(x)
        sx = x.sum()
        sx2 = (x**2).sum()
        sy = y.sum(axis=0)
        sxy = (x[:, np.newaxis] * y).sum(axis=0)

        denom = n * sx2 - sx**2
        slopes = (n * sxy - sx * sy) / denom

        # Residual (RMSE of detrended displacement)
        intercepts = (sy - slopes * sx) / n
        predicted = x[:, np.newaxis] * slopes[np.newaxis, :] + intercepts[np.newaxis, :]
        residuals = np.sqrt(((y - predicted) ** 2).mean(axis=0))

        velocity_flat[all_valid] = slopes
        residual_flat[all_valid] = residuals

    # Slow path: pixels with some missing data
    partial = compute_mask & ~all_valid
    partial_indices = np.where(partial)[0]

    for idx in partial_indices:
        col = stack_2d[:, idx]
        good = ~np.isnan(col)
        if good.sum() >= MIN_GRANULES:
            slope, _, _, _, _ = stats.linregress(t_years[good], col[good])
            velocity_flat[idx] = slope
            predicted = slope * t_years[good]
            residual_flat[idx] = np.sqrt(((col[good] - predicted) ** 2).mean())

    # Reshape and convert to mm/yr
    velocity_mm_yr = (velocity_flat * 1000.0).reshape(n_rows, n_cols)
    residual_mm_yr = (residual_flat * 1000.0).reshape(n_rows, n_cols)

    crs = geo.get("crs") if geo else None

    return velocity_mm_yr, residual_mm_yr, geo, crs


def sample_polygon_velocity(velocity, residual, geo, crs, polygons_gdf):
    """Sample velocity at all pixels within each polygon.

    Returns arrays of (max_velocity, mean_velocity, max_residual) per polygon.
    Max velocity is used for active/dormant classification.
    Mean velocity characterizes the bulk motion.
    """
    n = len(polygons_gdf)
    max_vel = np.full(n, np.nan)
    mean_vel = np.full(n, np.nan)
    max_res = np.full(n, np.nan)

    if velocity is None or geo is None or "x_first" not in geo:
        return max_vel, mean_vel, max_res

    rows, cols = velocity.shape
    x_first = geo["x_first"]
    y_first = geo["y_first"]
    x_step = geo["x_step"]
    y_step = geo["y_step"]

    # Raster bounds
    x_min = x_first
    x_max = x_first + cols * x_step
    y_max = y_first
    y_min = y_first + rows * y_step  # y_step is negative
    if y_min > y_max:
        y_min, y_max = y_max, y_min

    # Reproject polygons to raster CRS
    if crs:
        try:
            polys_proj = polygons_gdf.to_crs(crs)
        except Exception:
            polys_proj = polygons_gdf
    else:
        polys_proj = polygons_gdf

    for i, (idx, row) in enumerate(polys_proj.iterrows()):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        # Check if polygon intersects the raster extent
        bx = geom.bounds  # (minx, miny, maxx, maxy)
        if bx[2] < x_min or bx[0] > x_max or bx[3] < y_min or bx[1] > y_max:
            continue

        # Clip polygon bounds to raster bounds
        clip_xmin = max(bx[0], x_min)
        clip_xmax = min(bx[2], x_max)
        clip_ymin = max(bx[1], y_min)
        clip_ymax = min(bx[3], y_max)

        # Convert to pixel coords
        col_start = max(0, int((clip_xmin - x_first) / x_step))
        col_end = min(cols, int((clip_xmax - x_first) / x_step) + 1)
        row_start = max(0, int((clip_ymax - y_first) / y_step))  # y_step negative
        row_end = min(rows, int((clip_ymin - y_first) / y_step) + 1)

        if col_start >= col_end or row_start >= row_end:
            continue

        # For small polygons (< ~10 pixels), sample all pixels in bbox
        # For larger ones, rasterize the polygon boundary
        n_pixels = (row_end - row_start) * (col_end - col_start)

        if n_pixels <= 100:
            # Small polygon: check all pixels in bbox
            sub_vel = velocity[row_start:row_end, col_start:col_end]
            sub_res = residual[row_start:row_end, col_start:col_end] if residual is not None else None

            # Point-in-polygon check for each pixel center
            vals = []
            res_vals = []
            for r in range(row_start, row_end):
                for c in range(col_start, col_end):
                    px = x_first + (c + 0.5) * x_step
                    py = y_first + (r + 0.5) * y_step
                    from shapely.geometry import Point
                    if geom.contains(Point(px, py)):
                        v = velocity[r, c]
                        if not np.isnan(v):
                            vals.append(v)
                            if sub_res is not None:
                                res_vals.append(residual[r, c])
        else:
            # Larger polygon: rasterize using the polygon geometry
            sub_transform = from_bounds(
                x_first + col_start * x_step,
                y_first + row_end * y_step,
                x_first + col_end * x_step,
                y_first + row_start * y_step,
                col_end - col_start,
                row_end - row_start,
            )
            try:
                poly_mask = rasterize(
                    [(geom, 1)],
                    out_shape=(row_end - row_start, col_end - col_start),
                    transform=sub_transform,
                    fill=0,
                    dtype=np.uint8,
                )
            except Exception:
                continue

            sub_vel = velocity[row_start:row_end, col_start:col_end]
            masked_vel = sub_vel[poly_mask == 1]
            vals = masked_vel[~np.isnan(masked_vel)].tolist()

            if residual is not None:
                sub_res = residual[row_start:row_end, col_start:col_end]
                masked_res = sub_res[poly_mask == 1]
                res_vals = masked_res[~np.isnan(masked_res)].tolist()
            else:
                res_vals = []

        if vals:
            abs_vals = [abs(v) for v in vals]
            max_vel[i] = max(abs_vals)
            mean_vel[i] = np.mean(abs_vals)
            if res_vals:
                max_res[i] = max(abs(r) for r in res_vals)

    return max_vel, mean_vel, max_res


def main():
    parser = argparse.ArgumentParser(
        description="Download OPERA-DISP InSAR velocity and classify landslide deposits."
    )
    parser.add_argument(
        "--inventory", type=Path, default=INVENTORY_PATH,
        help=f"Path to USGS inventory shapefile (default: {INVENTORY_PATH})",
    )
    parser.add_argument(
        "--output", type=Path, default=OUTPUT_PATH,
        help=f"Output path (default: {OUTPUT_PATH})",
    )
    parser.add_argument(
        "--threshold", type=float, default=VELOCITY_THRESHOLD_MM_YR,
        help=f"Active/dormant threshold in mm/yr (default: {VELOCITY_THRESHOLD_MM_YR})",
    )
    parser.add_argument(
        "--max-frames", type=int, default=None,
        help="Limit number of frames to process (for testing)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Search for data but don't download",
    )
    args = parser.parse_args()

    # Checkpoint file for resume support.
    # Stores per-polygon velocity results and list of completed frames.
    checkpoint_path = args.output.parent / (args.output.stem + "_checkpoint.npz")

    # Load inventory
    print(f"Loading inventory from {args.inventory}...")
    inv = gpd.read_file(args.inventory)

    # Filter to California, C>=3
    ca = inv.cx[-124.5:-114, 32:42.5]
    ca = ca[ca["Confidence"] >= MIN_CONFIDENCE].copy()
    print(f"  {len(ca)} California polygons with C>={MIN_CONFIDENCE}")

    # Initialize velocity columns — or restore from checkpoint
    completed_frames: set[int] = set()
    if checkpoint_path.exists():
        print(f"  Resuming from checkpoint: {checkpoint_path}")
        ckpt = np.load(checkpoint_path, allow_pickle=True)
        ca["velocity_max_mm_yr"] = ckpt["velocity_max"]
        ca["velocity_mean_mm_yr"] = ckpt["velocity_mean"]
        ca["velocity_residual_mm_yr"] = ckpt["velocity_residual"]
        completed_frames = set(ckpt["completed_frames"].tolist())
        n_done = len(completed_frames)
        n_with = np.isfinite(ckpt["velocity_max"]).sum()
        print(f"  Restored {n_done} completed frames, {n_with} polygons with data")
    else:
        ca["velocity_max_mm_yr"] = np.nan
        ca["velocity_mean_mm_yr"] = np.nan
        ca["velocity_residual_mm_yr"] = np.nan

    # Find all granules grouped by frame
    by_frame = find_granules_per_frame()

    if args.dry_run:
        n_processable = sum(1 for g in by_frame.values() if len(g) >= MIN_GRANULES)
        n_remaining = n_processable - len(completed_frames & set(by_frame.keys()))
        print(f"\nDry run complete.")
        print(f"  {len(by_frame)} frames found")
        print(f"  {n_processable} frames with >={MIN_GRANULES} granules (processable)")
        print(f"  {len(completed_frames)} frames already completed")
        print(f"  {n_remaining} frames remaining")
        return

    # Authenticate
    session = get_earthdata_session()

    # Sort frames by center longitude (west to east) to prioritize coastal
    # California where most landslides are, rather than desert border frames.
    def frame_sort_key(fid):
        granules = by_frame[fid]
        lon = granules[0][0].properties.get("centerLon", 0)
        return lon  # most negative = most western = coastal
    frame_ids = sorted(by_frame.keys(), key=frame_sort_key)
    if args.max_frames:
        frame_ids = frame_ids[:args.max_frames]

    # Filter out already-completed frames
    frame_ids = [fid for fid in frame_ids if fid not in completed_frames]
    n_frames = len(frame_ids)
    n_skipped = len(completed_frames)
    if n_skipped:
        print(f"\nSkipping {n_skipped} already-completed frames, {n_frames} remaining")

    def save_checkpoint():
        """Save current state so we can resume after interruption."""
        np.savez_compressed(
            checkpoint_path,
            velocity_max=ca["velocity_max_mm_yr"].values,
            velocity_mean=ca["velocity_mean_mm_yr"].values,
            velocity_residual=ca["velocity_residual_mm_yr"].values,
            completed_frames=np.array(sorted(completed_frames)),
        )

    # Process each frame
    for i, frame_id in enumerate(frame_ids):
        frame_granules = by_frame[frame_id]
        print(f"\nFrame {frame_id} ({n_skipped + i + 1}/{n_skipped + n_frames}): "
              f"{len(frame_granules)} granules")

        if len(frame_granules) < MIN_GRANULES:
            print(f"  Skipping: only {len(frame_granules)} granules (need {MIN_GRANULES})")
            completed_frames.add(frame_id)
            continue

        span = (frame_granules[-1][1] - frame_granules[0][1]).days / 365.25
        if span < MIN_TIME_SPAN_YEARS:
            print(f"  Skipping: only {span:.1f} year span (need {MIN_TIME_SPAN_YEARS})")
            completed_frames.add(frame_id)
            continue

        print(f"  Time span: {frame_granules[0][1].date()} to "
              f"{frame_granules[-1][1].date()} ({span:.1f} yr)")

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                velocity, residual, geo, crs = compute_velocity_stack(
                    frame_granules, session, tmpdir
                )

                if velocity is None:
                    print(f"  No velocity computed for frame {frame_id}")
                    completed_frames.add(frame_id)
                    save_checkpoint()
                    continue

                n_valid = (~np.isnan(velocity)).sum()
                print(f"  Velocity grid: {velocity.shape}, {n_valid} valid pixels")

                # Sample polygons
                frame_max, frame_mean, frame_res = sample_polygon_velocity(
                    velocity, residual, geo, crs, ca
                )

                # Update: keep max velocity across frames for each polygon
                for j in range(len(ca)):
                    if not np.isnan(frame_max[j]):
                        idx = ca.index[j]
                        existing = ca.at[idx, "velocity_max_mm_yr"]
                        if np.isnan(existing) or frame_max[j] > existing:
                            ca.at[idx, "velocity_max_mm_yr"] = frame_max[j]
                            ca.at[idx, "velocity_mean_mm_yr"] = frame_mean[j]
                            if not np.isnan(frame_res[j]):
                                ca.at[idx, "velocity_residual_mm_yr"] = frame_res[j]

                n_sampled = (~np.isnan(frame_max)).sum()
                n_active = (frame_max > args.threshold).sum()
                print(f"  Sampled {n_sampled} polygons, {n_active} above threshold")

        except Exception as e:
            print(f"  ERROR processing frame {frame_id}: {e}", file=sys.stderr)
            # Don't mark as completed — will retry on next run
            save_checkpoint()
            continue

        completed_frames.add(frame_id)
        save_checkpoint()
        print(f"  Checkpoint saved ({len(completed_frames)} frames complete)")

    # Classify active/dormant
    ca["is_active"] = ca["velocity_max_mm_yr"].abs() > args.threshold
    ca["landslide_tier"] = 2  # Default: dormant (Tier 2)
    ca.loc[ca["is_active"] == True, "landslide_tier"] = 1  # Active: Tier 1

    # Summary
    n_total = len(ca)
    n_with_velocity = ca["velocity_max_mm_yr"].notna().sum()
    n_active = (ca["is_active"] == True).sum()
    n_dormant = n_total - n_active

    print(f"\n{'='*50}")
    print(f"Classification Results")
    print(f"{'='*50}")
    print(f"  Total deposits (C>={MIN_CONFIDENCE}): {n_total:,}")
    print(f"  With InSAR velocity:  {n_with_velocity:,} ({100*n_with_velocity/n_total:.1f}%)")
    print(f"  Active (>{args.threshold} mm/yr): {n_active:,} ({100*n_active/n_total:.1f}%)")
    print(f"  Dormant:              {n_dormant:,} ({100*n_dormant/n_total:.1f}%)")
    print(f"  No InSAR data:        {n_total - n_with_velocity:,}")

    if n_active > 0:
        active_vels = ca.loc[ca["is_active"] == True, "velocity_max_mm_yr"]
        print(f"\n  Active deposit velocity (max per polygon):")
        print(f"    Range:  {active_vels.min():.1f} - {active_vels.max():.1f} mm/yr")
        print(f"    Median: {active_vels.median():.1f} mm/yr")
        print(f"    Mean:   {active_vels.mean():.1f} mm/yr")

    if n_with_velocity > 0:
        mean_vels = ca.loc[ca["velocity_mean_mm_yr"].notna(), "velocity_mean_mm_yr"]
        print(f"\n  All deposits velocity (mean per polygon):")
        print(f"    Range:  {mean_vels.min():.1f} - {mean_vels.max():.1f} mm/yr")
        print(f"    Median: {mean_vels.median():.1f} mm/yr")

    # Save output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ca.to_file(args.output, driver="GeoJSON")
    print(f"\nSaved to {args.output}")
    print(f"  File size: {args.output.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
