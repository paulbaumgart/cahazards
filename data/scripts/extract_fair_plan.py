#!/usr/bin/env python3
"""
Extract FAIR Plan residential policies-in-force by ZIP code from the CFP PDF.
Outputs a clean CSV with zip code and policy counts for 2021-2025.

Source: California FAIR Plan Association
https://www.cfpnet.com/key-statistics-data/
"""

import re
import subprocess
import csv
import sys

PDF_PATH = '/tmp/fair_plan_residential_pif.pdf'
OUT_PATH = 'data/processed/fair_plan_by_zip.csv'


def extract_with_pdftotext():
    """Use pdftotext to extract text, then parse the table structure."""
    result = subprocess.run(['pdftotext', '-layout', PDF_PATH, '-'],
                          capture_output=True, text=True)
    if result.returncode != 0:
        print(f"pdftotext failed: {result.stderr}")
        return None
    return result.stdout


def parse_fair_plan_text(text):
    """Parse the FAIR Plan PIF table from extracted text."""
    rows = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # Match lines starting with a 5-digit zip code
        # Format: ZIP  YoY%  Count  YoY%  Count  YoY%  Count  YoY%  Count  YoY%  Count
        m = re.match(r'^(\d{5})\s+(.+)$', line)
        if not m:
            continue

        zipcode = m.group(1)
        rest = m.group(2)

        # Extract all numbers from the rest of the line
        # Growth percentages have % sign, counts are plain numbers
        # Some entries have '-' for missing data
        parts = rest.split()

        # We expect pairs of (growth%, count) for 5 years
        # But growth% might be negative or have special chars
        numbers = []
        for part in parts:
            part = part.replace(',', '').replace('%', '').strip()
            if part == '-' or part == '':
                numbers.append(None)
            else:
                try:
                    numbers.append(part)
                except ValueError:
                    numbers.append(None)

        # We want the policy counts (every other value, starting from index 1)
        # Format: growth2025, count2025, growth2024, count2024, growth2023, count2023,
        #         growth2022, count2022, growth2021, count2021
        counts = {}
        years = [2025, 2024, 2023, 2022, 2021]
        idx = 0
        for year in years:
            if idx + 1 < len(numbers):
                try:
                    count = int(numbers[idx + 1]) if numbers[idx + 1] is not None else None
                    counts[year] = count
                except (ValueError, TypeError):
                    counts[year] = None
            idx += 2

        if any(v is not None for v in counts.values()):
            rows.append({'zip': zipcode, **{f'pif_{y}': counts.get(y) for y in years}})

    return rows


def main():
    print(f"Extracting FAIR Plan data from {PDF_PATH}")

    text = extract_with_pdftotext()
    if text is None:
        # Fallback: try with python
        print("pdftotext not available, trying pdfplumber...")
        try:
            import pdfplumber
            rows = []
            with pdfplumber.open(PDF_PATH) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if table is None:
                        continue
                    for row in table[1:]:  # skip header
                        if row and row[0] and re.match(r'^\d{5}$', str(row[0]).strip()):
                            zipcode = row[0].strip()
                            # Columns: ZIP, YoY2025, Count2025, YoY2024, Count2024, ...
                            counts = {}
                            years = [2025, 2024, 2023, 2022, 2021]
                            for i, year in enumerate(years):
                                col_idx = 2 + i * 2  # count columns at 2, 4, 6, 8, 10
                                try:
                                    val = str(row[col_idx]).replace(',', '').strip()
                                    counts[year] = int(val) if val and val != '-' else None
                                except (IndexError, ValueError):
                                    counts[year] = None
                            if any(v is not None for v in counts.values()):
                                rows.append({'zip': zipcode,
                                           **{f'pif_{y}': counts.get(y) for y in years}})
            if rows:
                write_csv(rows)
                return
        except ImportError:
            pass

        print("ERROR: Need pdftotext or pdfplumber to extract PDF tables")
        sys.exit(1)

    rows = parse_fair_plan_text(text)
    if rows:
        write_csv(rows)
    else:
        print("No data extracted. Trying pdfplumber fallback...")
        try:
            import pdfplumber
            rows = []
            with pdfplumber.open(PDF_PATH) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if table is None:
                        continue
                    for row in table:
                        if row and row[0] and re.match(r'^\d{5}$', str(row[0]).strip()):
                            zipcode = row[0].strip()
                            counts = {}
                            years = [2025, 2024, 2023, 2022, 2021]
                            for i, year in enumerate(years):
                                col_idx = 2 + i * 2
                                try:
                                    val = str(row[col_idx]).replace(',', '').strip()
                                    counts[year] = int(val) if val and val != '-' else None
                                except (IndexError, ValueError):
                                    counts[year] = None
                            if any(v is not None for v in counts.values()):
                                rows.append({'zip': zipcode,
                                           **{f'pif_{y}': counts.get(y) for y in years}})
            write_csv(rows)
        except ImportError:
            print("Install pdfplumber: pip install pdfplumber")
            sys.exit(1)


def write_csv(rows):
    import os
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['zip', 'pif_2025', 'pif_2024', 'pif_2023', 'pif_2022', 'pif_2021'])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} zip codes to {OUT_PATH}")

    # Check Arnold's zip (95223)
    for row in rows:
        if row['zip'] == '95223':
            print(f"\nArnold (95223): {row}")
            break

    # Show some high-count zips
    by_count = sorted([r for r in rows if r.get('pif_2025')],
                     key=lambda x: x['pif_2025'] or 0, reverse=True)
    print(f"\nTop 10 FAIR Plan zips (2025):")
    for r in by_count[:10]:
        print(f"  {r['zip']}: {r['pif_2025']:,} policies")


if __name__ == '__main__':
    main()
