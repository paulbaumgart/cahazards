#!/bin/bash
# Download all 3DEP 1/3 arc-second tiles for California
# Skips already-downloaded files. Run with: bash data/scripts/download_3dep.sh

set -euo pipefail
DIR="data/raw/elevation"
URLS="$DIR/tile_urls.txt"

if [ ! -f "$URLS" ]; then
    echo "ERROR: $URLS not found"
    exit 1
fi

total=$(wc -l < "$URLS" | tr -d ' ')
done=0
skipped=0

while IFS= read -r url; do
    fname=$(basename "$url")
    dest="$DIR/$fname"
    if [ -f "$dest" ] && [ "$(stat -f%z "$dest" 2>/dev/null || stat -c%s "$dest" 2>/dev/null)" -gt 1000 ]; then
        skipped=$((skipped + 1))
        continue
    fi
    curl -sS -L -o "$dest" "$url" &
    done=$((done + 1))
    # Run 6 in parallel
    if [ $((done % 6)) -eq 0 ]; then
        wait
        echo "  Downloaded $((done + skipped))/$total tiles..."
    fi
done < "$URLS"
wait

echo "Done. Total files: $(ls "$DIR"/*.tif 2>/dev/null | wc -l)"
du -sh "$DIR"
