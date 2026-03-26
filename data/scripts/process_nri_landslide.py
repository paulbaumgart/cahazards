#!/usr/bin/env python3
"""Process FEMA National Risk Index (NRI) v1.20 landslide data by census tract.

Extracts annual landslide failure rates for California census tracts from the
full NRI dataset.  The failure rate is computed as:

    annual_rate = LNDS_AFREQ * LNDS_HLRB

where LNDS_AFREQ is the annualized landslide frequency and LNDS_HLRB is the
historic loss ratio for buildings (fraction of building value damaged per
event).  The product gives the expected annual fraction of building value
lost to landslides, which we treat as a proxy for annual failure probability.

Source: FEMA National Risk Index v1.20
        https://hazards.fema.gov/nri/
        Census tract-level data, NRI_Table_CensusTracts.csv
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


DATA_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = DATA_DIR / "raw" / "NRI_Table_CensusTracts.csv"
DEFAULT_OUTPUT = DATA_DIR / "processed" / "nri_landslide_by_tract.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract NRI landslide annual failure rates for California census tracts."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Path to NRI census tract CSV (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"Error: input file not found: {args.input}")

    # Load only the columns we need to keep memory usage reasonable (~605 MB CSV).
    print(f"Loading NRI data from {args.input} ...")
    cols = ["TRACTFIPS", "STATEABBRV", "STATEFIPS", "LNDS_AFREQ", "LNDS_HLRB"]
    df = pd.read_csv(args.input, usecols=cols, dtype={"TRACTFIPS": str, "STATEFIPS": str})
    print(f"  Loaded {len(df):,} tracts (all states)")

    # Filter to California
    df = df[df["STATEABBRV"] == "CA"].copy()
    print(f"  California tracts: {len(df):,}")

    if df.empty:
        sys.exit("Error: no California tracts found in NRI data")

    # Compute annual landslide failure rate = frequency * building loss ratio
    df["annual_rate"] = df["LNDS_AFREQ"] * df["LNDS_HLRB"]

    # Drop tracts with missing values (NRI uses NaN for tracts with no data)
    before = len(df)
    df = df.dropna(subset=["annual_rate"])
    dropped = before - len(df)
    if dropped:
        print(f"  Dropped {dropped} tracts with missing LNDS_AFREQ or LNDS_HLRB")

    # Build output dict: {tract_fips: rate}
    result = dict(zip(df["TRACTFIPS"], df["annual_rate"]))

    # Summary statistics
    rates = df["annual_rate"]
    nonzero = rates[rates > 0]
    print(f"\n--- Summary ---")
    print(f"  Tracts in output:  {len(result):,}")
    print(f"  Tracts with rate > 0: {len(nonzero):,}")
    if len(nonzero) > 0:
        print(f"  Rate range: {nonzero.min():.6g} to {nonzero.max():.6g}")
        print(f"  Mean rate (nonzero): {nonzero.mean():.6g}")
        print(f"  Median rate (nonzero): {nonzero.median():.6g}")

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f)

    size_kb = args.output.stat().st_size / 1024
    print(f"\nWrote {args.output} ({size_kb:.1f} KB)")
    print("Done.")


if __name__ == "__main__":
    main()
