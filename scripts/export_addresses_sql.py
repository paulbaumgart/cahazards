#!/usr/bin/env python3
"""Export address database as chunked SQL dumps for D1 upload.

D1 has memory limits that prevent loading 14M rows in a single transaction.
This exports multiple SQL files of ~1M rows each.
"""

import os
import sqlite3

DB_PATH = "data/processed/addresses.db"
OUTPUT_DIR = "/tmp/addresses_chunks"
ROWS_PER_CHUNK = 100_000
BATCH = 50

os.makedirs(OUTPUT_DIR, exist_ok=True)

conn = sqlite3.connect(DB_PATH)
count = conn.execute("SELECT COUNT(*) FROM addresses WHERE city IS NOT NULL AND city <> ''").fetchone()[0]
print(f"Exporting {count:,} addresses in chunks of {ROWS_PER_CHUNK:,}...")

cursor = conn.execute(
    "SELECT id, address, city, postcode, display, lat, lon FROM addresses WHERE city IS NOT NULL AND city <> ''"
)

chunk_idx = 0
rows_in_chunk = 0
out = None

def open_chunk(idx):
    path = os.path.join(OUTPUT_DIR, f"chunk_{idx:03d}.sql")
    f = open(path, "w")
    if idx == 0:
        # Schema in first chunk only
        f.write("DROP TABLE IF EXISTS addresses;\n")
        f.write("CREATE TABLE addresses (\n")
        f.write("  id INTEGER PRIMARY KEY,\n")
        f.write("  address TEXT NOT NULL,\n")
        f.write("  city TEXT,\n")
        f.write("  postcode TEXT,\n")
        f.write("  display TEXT NOT NULL,\n")
        f.write("  lat REAL NOT NULL,\n")
        f.write("  lon REAL NOT NULL\n")
        f.write(");\n\n")
        f.write("CREATE TABLE IF NOT EXISTS geocode_cache (\n")
        f.write("  address TEXT PRIMARY KEY,\n")
        f.write("  lat REAL, lon REAL,\n")
        f.write("  census_tract TEXT, full_address TEXT,\n")
        f.write("  created_at INTEGER DEFAULT (strftime('%s','now'))\n")
        f.write(");\n\n")
    return f

out = open_chunk(0)
total = 0

while True:
    rows = cursor.fetchmany(BATCH)
    if not rows:
        break

    values = []
    for r in rows:
        parts = [str(r[0])]
        for v in r[1:5]:
            if v is None:
                parts.append("NULL")
            else:
                parts.append("'" + str(v).replace("'", "''") + "'")
        parts.append(str(r[5]))
        parts.append(str(r[6]))
        values.append("(" + ",".join(parts) + ")")

    out.write("INSERT INTO addresses VALUES\n")
    out.write(",\n".join(values))
    out.write(";\n")

    rows_in_chunk += len(rows)
    total += len(rows)

    if rows_in_chunk >= ROWS_PER_CHUNK:
        out.close()
        chunk_idx += 1
        print(f"  Chunk {chunk_idx}: {total:,}/{count:,}")
        out = open_chunk(chunk_idx)
        rows_in_chunk = 0

# Index in last chunk
out.write("\nCREATE INDEX IF NOT EXISTS idx_address ON addresses(address COLLATE NOCASE);\n")
out.close()

n_chunks = chunk_idx + 1
print(f"Done. {n_chunks} chunks in {OUTPUT_DIR}/")
for f in sorted(os.listdir(OUTPUT_DIR)):
    path = os.path.join(OUTPUT_DIR, f)
    print(f"  {f}: {os.path.getsize(path) / 1e6:.1f} MB")
conn.close()
