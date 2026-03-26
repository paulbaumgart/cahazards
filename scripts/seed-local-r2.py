#!/usr/bin/env python3
"""
Seed local Miniflare R2 storage directly by writing the SQLite DB and blob files.

This is much faster than calling `wrangler r2 object put` per file.
Writes to .wrangler/state/v3/r2/miniflare-R2BucketObject/{hash}/

Usage:
    python scripts/seed-local-r2.py                  # all tiles
    python scripts/seed-local-r2.py --subset hmb     # just HMB area tiles
"""
import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

TILES_DIR = "data/tiles"

# Miniflare stores R2 in this structure:
# .wrangler/state/v3/r2/miniflare-R2BucketObject/{hash}.sqlite
# .wrangler/state/v3/r2/miniflare-R2BucketObject/blobs/{blob_id}
# We find the existing hash from the directory.

SCHEMA = """
CREATE TABLE IF NOT EXISTS _mf_objects (
    key TEXT PRIMARY KEY,
    blob_id TEXT,
    version TEXT NOT NULL,
    size INTEGER NOT NULL,
    etag TEXT NOT NULL,
    uploaded INTEGER NOT NULL,
    checksums TEXT NOT NULL,
    http_metadata TEXT NOT NULL,
    custom_metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS _mf_multipart_uploads (
    upload_id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    http_metadata TEXT NOT NULL,
    custom_metadata TEXT NOT NULL,
    state TINYINT DEFAULT 0 NOT NULL
);
CREATE TABLE IF NOT EXISTS _mf_multipart_parts (
    upload_id TEXT NOT NULL REFERENCES _mf_multipart_uploads(upload_id),
    part_number INTEGER NOT NULL,
    blob_id TEXT NOT NULL,
    size INTEGER NOT NULL,
    etag TEXT NOT NULL,
    checksum_md5 TEXT NOT NULL,
    object_key TEXT REFERENCES _mf_objects(key) DEFERRABLE INITIALLY DEFERRED,
    PRIMARY KEY (upload_id, part_number)
);
"""


def find_r2_dir():
    """Find the Miniflare R2 storage directory."""
    base = Path(".wrangler/state/v3/r2/miniflare-R2BucketObject")
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
    return base


def find_or_create_db(r2_dir: Path) -> tuple[Path, Path]:
    """Find existing SQLite DB or create a new one. Returns (db_path, blobs_dir)."""
    dbs = list(r2_dir.glob("*.sqlite"))
    if dbs:
        db_path = dbs[0]
        hash_id = db_path.stem
    else:
        # If no DB exists, create one with wrangler first so it gets the right hash
        print("  No existing R2 DB found. Creating via wrangler...")
        import subprocess
        # Write a dummy object to force wrangler to create the DB
        dummy = Path("/tmp/_r2_seed_dummy.txt")
        dummy.write_text("init")
        subprocess.run(
            ["npx", "wrangler", "r2", "object", "put", "cahazards-tiles/_init", "--file", str(dummy), "--local"],
            capture_output=True, timeout=30,
        )
        dummy.unlink()
        # Now find the DB it created
        dbs = list(r2_dir.glob("*.sqlite"))
        if not dbs:
            print("ERROR: Failed to create R2 DB", file=sys.stderr)
            sys.exit(1)
        db_path = dbs[0]

    # Blobs are stored in a SEPARATE directory named after the bucket, not alongside the DB
    # DB lives in: .wrangler/state/v3/r2/miniflare-R2BucketObject/{hash}.sqlite
    # Blobs live in: .wrangler/state/v3/r2/{bucket_name}/blobs/
    blobs_dir = r2_dir.parent / "cahazards-tiles" / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)

    return db_path, blobs_dir


def collect_files(tiles_dir: str, subset: str = None) -> list[tuple[str, str]]:
    """Collect (filepath, r2_key) pairs. Optionally filter to a subset."""
    files = []
    for root, _, filenames in os.walk(tiles_dir):
        for fname in filenames:
            if not (fname.endswith(".json") or fname.endswith(".bin")):
                continue
            filepath = os.path.join(root, fname)
            # R2 key: tiles/faults/37.5_-122.5.json
            key = filepath.replace(f"{tiles_dir}/", "tiles/") if tiles_dir in filepath else filepath
            key = key.lstrip("./")
            if not key.startswith("tiles/"):
                key = "tiles/" + key.split("tiles/", 1)[-1]
            files.append((filepath, key))

    if subset == "hmb":
        # Half Moon Bay area: 37.3-37.7, -122.6 to -122.2
        filtered = []
        for fp, key in files:
            fname = os.path.basename(key).replace(".json", "").replace(".bin", "")
            parts = fname.split("_")
            if len(parts) >= 2:
                try:
                    lat = float(parts[0])
                    lon = float(parts[1])
                    # Include HMB area tiles + 0.5-degree tiles that cover it
                    if (37.3 <= lat <= 37.7 and -122.6 <= lon <= -122.2) or \
                       (37.0 <= lat <= 37.5 and -123.0 <= lon <= -122.0 and
                        os.path.dirname(key).endswith(("contamination", "airports"))):
                        filtered.append((fp, key))
                except ValueError:
                    pass
        files = filtered

    return files


def seed(tiles_dir: str, subset: str = None):
    r2_dir = find_r2_dir()
    db_path, blobs_dir = find_or_create_db(r2_dir)

    files = collect_files(tiles_dir, subset)
    if not files:
        print("No files to seed.")
        return

    print(f"Seeding {len(files)} files into {db_path}")
    print(f"Blobs dir: {blobs_dir}")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)

    # Get existing keys to skip
    existing = set(row[0] for row in conn.execute("SELECT key FROM _mf_objects").fetchall())
    to_insert = [(fp, key) for fp, key in files if key not in existing]
    print(f"  {len(existing)} already seeded, {len(to_insert)} new files")

    now_ms = int(time.time() * 1000)
    inserted = 0

    for filepath, key in to_insert:
        with open(filepath, "rb") as f:
            data = f.read()

        # Generate blob ID (random, like Miniflare does)
        blob_id = secrets.token_urlsafe(32)
        # Write blob file
        blob_path = blobs_dir / blob_id
        blob_path.write_bytes(data)

        # Compute metadata
        md5 = hashlib.md5(data).hexdigest()
        version = secrets.token_hex(16)
        size = len(data)

        content_type = "application/json" if key.endswith(".json") else "application/octet-stream"
        http_metadata = json.dumps({"contentType": content_type})
        checksums = json.dumps({"md5": md5})

        conn.execute(
            "INSERT OR REPLACE INTO _mf_objects (key, blob_id, version, size, etag, uploaded, checksums, http_metadata, custom_metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, blob_id, version, size, md5, now_ms, checksums, http_metadata, "{}"),
        )

        inserted += 1
        if inserted % 1000 == 0:
            conn.commit()
            print(f"  {inserted}/{len(to_insert)}...")

    conn.commit()
    conn.close()
    print(f"Done. Inserted {inserted} objects.")


def main():
    parser = argparse.ArgumentParser(description="Seed local Miniflare R2 with tile data")
    parser.add_argument("--tiles-dir", default=TILES_DIR, help=f"Tiles directory (default: {TILES_DIR})")
    parser.add_argument("--subset", choices=["hmb"], help="Seed only a subset (hmb = Half Moon Bay area)")
    args = parser.parse_args()

    seed(args.tiles_dir, args.subset)


if __name__ == "__main__":
    main()
