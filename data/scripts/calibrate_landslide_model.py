#!/usr/bin/env python3
"""
Calibrate landslide annual rates from USGS inventory and susceptibility data.

Uses the Mirus et al. 2024 (AGU Advances) susceptibility map (n10 model) and
the USGS national landslide inventory (Belair et al. 2022) to derive annual
landslide probability as a function of susceptibility score.

The inventory is cumulative over decades of mapping. We estimate an effective
observation period and normalize to annual rates per susceptibility bin.

Output: a lookup table mapping n10 susceptibility (0-81) to annual probability
of a landslide occurring in that pixel.
"""

import pandas as pd
import numpy as np
import json

INVENTORY_PATH = 'data/raw/landslides.csv'
COUNTY_PATH = 'data/raw/landslide_county_analysis.csv'
OUTPUT_PATH = 'data/processed/landslide_rate_by_susceptibility.json'


def main():
    print("Loading data...")
    inv = pd.read_csv(INVENTORY_PATH, low_memory=False)
    counties = pd.read_csv(COUNTY_PATH)

    # Filter to California
    ca_inv = inv[(inv['Lon_E'] < -114) & (inv['Lon_E'] > -124.5) &
                 (inv['Lat_N'] > 32) & (inv['Lat_N'] < 42)]
    ca_counties = counties[counties['ST'] == 'CA']

    print(f"California: {len(ca_inv)} landslides, {len(ca_counties)} counties")

    # Effective observation period.
    #
    # The CGS inventory (138K of 174K CA landslides) spans roughly 1950-2020,
    # but coverage is uneven. The USGS seismic ground failure data adds post-
    # earthquake surveys. Crovelli & Coe (2008) estimated ~65 damaging
    # landslides/yr across the 10-county SF Bay. San Mateo's 4,975 mapped
    # landslides over ~70 years of geological survey = ~71/yr, which is
    # consistent with Crovelli's Bay-wide estimate considering San Mateo is
    # one of the most slide-prone counties.
    #
    # We use 50 years as a conservative effective period, acknowledging that
    # older inventories are incomplete (biasing rates downward = conservative
    # per CLAUDE.md).
    EFFECTIVE_YEARS = 50

    # Total susceptible area in California
    total_susc_km2 = ca_counties['susc_area'].sum()
    total_county_km2 = ca_counties['county_area'].sum()
    total_landslides = ca_counties['v2_ls_count'].sum()
    print(f"Total susceptible area: {total_susc_km2:.0f} km²")
    print(f"Total landslides: {total_landslides}")
    print(f"Statewide density: {total_landslides/total_susc_km2:.2f} per km² (cumulative)")
    print(f"Statewide annual rate: {total_landslides/total_susc_km2/EFFECTIVE_YEARS:.5f} per km² per year")

    # Annual rate per susceptibility bin.
    #
    # The n10 susceptibility value (0-81) represents the number of susceptible
    # 10m cells within each 90m cell. Higher values = more of the pixel is
    # susceptible terrain. We compute the rate as:
    #
    #   rate(n10) = N_landslides_in_bin / (N_pixels_in_bin * pixel_area_km2 * years)
    #
    # We need N_pixels_in_bin from the raster (not available yet), so instead
    # we use the relative landslide density: what fraction of landslides fall
    # in each bin, weighted by the fraction of susceptible area in that bin.
    #
    # Simpler approach: use the inventory directly. Group landslides by their
    # n10 value, normalize by the amount of terrain at each susceptibility level.

    # Bin landslides by n10 value
    bins = list(range(0, 82, 5))  # 0, 5, 10, ..., 80
    bins.append(82)  # Include 81
    ca_inv = ca_inv.copy()
    ca_inv['n10_bin'] = pd.cut(ca_inv['n10_90m'], bins=[-1] + bins, labels=bins)
    ls_by_bin = ca_inv.groupby('n10_bin').size()

    print("\nLandslides by n10 bin:")
    for b, count in ls_by_bin.items():
        print(f"  n10={b}: {count}")

    # Without the actual raster pixel counts per bin, we estimate the
    # relative rate by assuming the susceptible-area fraction scales
    # roughly linearly with n10 (higher n10 = more of the pixel is susceptible).
    #
    # The absolute calibration comes from matching the county-level densities.
    # San Mateo: 5.43 landslides/km²/50yr ≈ 0.109/km²/yr
    # For a 90m pixel (0.0081 km²): ~0.00088/yr at average susceptibility.
    #
    # Scale by relative density: n10=81 has ~30% of landslides but much less
    # than 30% of susceptible area (it's the highest concentration). The rate
    # at n10=81 is perhaps 5-10x the average.

    # Use empirical density ratio: rate(n10) proportional to landslide count / susceptible fraction
    total_ls = len(ca_inv)
    pixel_area_km2 = 0.09 * 0.09  # 90m pixel

    # Statewide average annual rate for susceptible pixels
    avg_annual_rate_per_km2 = total_landslides / total_susc_km2 / EFFECTIVE_YEARS

    # Build lookup: for each n10 value, compute relative rate
    # based on what fraction of landslides occur at that susceptibility level
    n10_values = list(range(82))
    lookup = {}

    # Assign each landslide to nearest n10 value
    ca_inv['n10_int'] = ca_inv['n10_90m'].fillna(0).astype(int).clip(0, 81)
    ls_per_n10 = ca_inv.groupby('n10_int').size()

    # Without pixel counts per n10, use a simple model:
    # The fraction of terrain at each n10 level is approximately uniform
    # for susceptible terrain (n10 > 0). This is a simplification.
    # Non-susceptible (n10=0) covers ~55% of CA terrain.
    #
    # More precisely: n10 captures how many of the 81 sub-cells are susceptible.
    # Higher n10 = denser concentration = less total area at that level.
    # This follows roughly an inverse relationship for the highest values.

    for n10 in n10_values:
        ls_count = ls_per_n10.get(n10, 0)
        if n10 == 0:
            # Non-susceptible: use the minimal rate from the 0.4% of landslides here
            # spread across ~55% of terrain
            rate_per_km2_yr = (ls_count / total_ls) * avg_annual_rate_per_km2 * (total_susc_km2 / (total_county_km2 * 0.55))
        else:
            # Susceptible: scale by concentration relative to average
            # Fraction of landslides at this n10 / fraction of susceptible terrain at this n10
            # Approximate terrain fraction as roughly uniform across n10 1-81
            frac_ls = ls_count / total_ls
            frac_terrain = 1.0 / 81  # Uniform approximation
            relative_density = frac_ls / frac_terrain if frac_terrain > 0 else 0
            rate_per_km2_yr = avg_annual_rate_per_km2 * relative_density

        # Convert to per-pixel annual probability
        annual_prob = rate_per_km2_yr * pixel_area_km2
        lookup[n10] = round(annual_prob, 8)

    # Print results
    print("\n=== Annual landslide probability by n10 susceptibility ===\n")
    print(f"{'n10':>5s}  {'rate/km²/yr':>12s}  {'per pixel/yr':>14s}  {'30yr':>8s}")
    print("-" * 45)
    for n10 in [0, 10, 20, 30, 40, 50, 60, 70, 75, 80, 81]:
        p = lookup.get(n10, 0)
        rate_km2 = p / pixel_area_km2 if pixel_area_km2 > 0 else 0
        p30 = 1 - (1 - p) ** 30
        print(f"{n10:5d}  {rate_km2:12.4f}  {p:14.8f}  {p30*100:7.2f}%")

    # Export
    result = {
        "model": "landslide_susceptibility_v1",
        "description": "Annual landslide probability per 90m pixel, by n10 susceptibility score (0-81). "
                       "Calibrated from USGS national landslide inventory (174K CA landslides) and "
                       "Mirus et al. 2024 susceptibility model.",
        "effective_observation_years": EFFECTIVE_YEARS,
        "pixel_size_m": 90,
        "citations": [
            "Mirus et al. 2024, AGU Advances, doi:10.1029/2024AV001214",
            "Belair et al. 2022, USGS landslide inventories, doi:10.5066/P9FZUX6N",
            "Crovelli & Coe 2008, USGS OFR 2008-1116",
        ],
        "annual_probability_by_n10": lookup,
    }

    with open(OUTPUT_PATH, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"\nExported to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
