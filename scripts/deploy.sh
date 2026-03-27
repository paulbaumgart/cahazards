#!/bin/bash
set -euo pipefail

# Deploy California Hazards to Cloudflare Workers + R2 + D1
#
# Prerequisites:
#   - wrangler authenticated: npx wrangler login
#   - All tiles built in data/tiles/
#   - Address DB built in data/processed/addresses.db
#   - Docs in docs/
#   - Frontend in frontend/
#
# First-time setup:
#   1. Run this script with --init to create R2 bucket and D1 database
#   2. Run this script with --seed to upload tiles and address data
#   3. Run this script with --deploy to deploy the worker
#
# Updates:
#   - Code changes: ./scripts/deploy.sh --deploy
#   - Tile changes: ./scripts/deploy.sh --seed-tiles
#   - Address DB:   ./scripts/deploy.sh --seed-addresses

BUCKET="cahazards-tiles"
DB_NAME="cahazards-db"

# ── Init: create R2 bucket and D1 database ──

init() {
    echo "Creating R2 bucket..."
    npx wrangler r2 bucket create "$BUCKET" 2>/dev/null || echo "  Bucket already exists"

    echo "Creating D1 database..."
    npx wrangler d1 create "$DB_NAME" 2>/dev/null || echo "  Database already exists"

    echo ""
    echo "IMPORTANT: Update wrangler.toml with the D1 database_id from above."
    echo "Then run: ./scripts/deploy.sh --seed"
}

# ── Seed R2: upload tiles, docs, frontend, data lookups ──

RCLONE_REMOTE="cahazards:$BUCKET"

seed_tiles() {
    echo "Syncing tiles to R2 via rclone..."
    rclone sync data/tiles/ "$RCLONE_REMOTE/tiles/" --transfers 32 --checkers 16 --progress
    echo "  Tiles done."

    echo "Syncing docs..."
    rclone sync docs/ "$RCLONE_REMOTE/docs/" --include "*.md" --transfers 8 --progress
    echo "  Docs done."

    echo "Syncing frontend..."
    rclone copyto frontend/index.html "$RCLONE_REMOTE/frontend/index.html"
    rclone copyto frontend/app.js "$RCLONE_REMOTE/frontend/app.js"
    rclone copyto frontend/report.js "$RCLONE_REMOTE/frontend/report.js"
    rclone copyto frontend/favicon.svg "$RCLONE_REMOTE/frontend/favicon.svg"
    rclone copyto frontend/sitemap.xml "$RCLONE_REMOTE/frontend/sitemap.xml"
    echo "  Frontend done."

    echo "Syncing data lookups..."
    rclone copyto data/processed/fair_share_by_zip.json "$RCLONE_REMOTE/data/fair_share_by_zip.json"
    rclone copyto data/processed/nri_landslide_by_tract.json "$RCLONE_REMOTE/data/nri_landslide_by_tract.json"
    echo "  Data lookups done."
}

# ── Seed D1: upload address database and create geocode cache ──

seed_addresses() {
    echo "Setting up D1 database..."

    # Export address DB as chunked SQL dumps
    echo "Exporting addresses to SQL chunks..."
    python3 scripts/export_addresses_sql.py

    # Upload each chunk to D1 sequentially
    echo "Uploading to D1..."
    for f in /tmp/addresses_chunks/chunk_*.sql; do
        echo "  Uploading $(basename $f)..."
        npx wrangler d1 execute "$DB_NAME" --remote --file="$f"
    done
    rm -rf /tmp/addresses_chunks

    echo "  D1 seeding complete."
}

# ── Deploy worker ──

inject_config() {
    if [ -f .deploy.env ]; then
        source .deploy.env
        if [ -n "${D1_DATABASE_ID:-}" ]; then
            sed -i '' "s/YOUR_D1_DATABASE_ID/$D1_DATABASE_ID/" wrangler.toml
            trap 'sed -i "" "s/$D1_DATABASE_ID/YOUR_D1_DATABASE_ID/" wrangler.toml' EXIT
        fi
    else
        echo "ERROR: .deploy.env not found. Copy .deploy.env.example and fill in your D1 database ID."
        exit 1
    fi
}

deploy_worker() {
    inject_config
    echo "Deploying worker..."
    npx wrangler deploy
    echo "Done."
}

# ── Main ──

case "${1:-}" in
    --init)
        init
        ;;
    --seed)
        seed_tiles
        seed_addresses
        ;;
    --seed-tiles)
        seed_tiles
        ;;
    --seed-addresses)
        seed_addresses
        ;;
    --deploy)
        deploy_worker
        ;;
    --all)
        init
        seed_tiles
        seed_addresses
        deploy_worker
        ;;
    *)
        echo "Usage: $0 {--init|--seed|--seed-tiles|--seed-addresses|--deploy|--all}"
        echo ""
        echo "  --init             Create R2 bucket and D1 database"
        echo "  --seed             Upload all tiles, docs, frontend, and addresses"
        echo "  --seed-tiles       Upload tiles, docs, frontend only"
        echo "  --seed-addresses   Upload address autocomplete data to D1"
        echo "  --deploy           Deploy the worker"
        echo "  --all              Do everything"
        exit 1
        ;;
esac
