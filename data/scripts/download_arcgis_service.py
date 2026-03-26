#!/usr/bin/env python3
"""
Download all features from an ArcGIS Feature Service, handling pagination.
Outputs GeoJSON. Works without ogr2ogr.
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.parse


def query_features(base_url, where="1=1", offset=0, count=2000):
    """Query features from an ArcGIS Feature Service with pagination."""
    params = {
        "where": where,
        "outFields": "*",
        "f": "geojson",
        "resultOffset": offset,
        "resultRecordCount": count,
    }
    url = f"{base_url}/query?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "cahazards-downloader/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def get_feature_count(base_url, where="1=1"):
    """Get total feature count."""
    params = {"where": where, "returnCountOnly": "true", "f": "json"}
    url = f"{base_url}/query?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "cahazards-downloader/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
        return data.get("count", 0)


def download_all_features(base_url, where="1=1", page_size=2000):
    """Download all features with pagination."""
    total = get_feature_count(base_url, where)
    print(f"  Total features: {total}")

    all_features = []
    offset = 0
    while offset < total:
        data = query_features(base_url, where, offset, page_size)
        features = data.get("features", [])
        if not features:
            break
        all_features.extend(features)
        offset += len(features)
        print(f"  Downloaded {offset}/{total} features...", flush=True)
        time.sleep(0.5)  # Be polite to the server

    # Build combined GeoJSON
    result = {
        "type": "FeatureCollection",
        "features": all_features,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Download features from ArcGIS Feature Service")
    parser.add_argument("url", help="Feature Service layer URL (e.g., .../FeatureServer/0)")
    parser.add_argument("-o", "--output", required=True, help="Output GeoJSON file path")
    parser.add_argument("--where", default="1=1", help="SQL WHERE clause (default: 1=1)")
    parser.add_argument("--page-size", type=int, default=2000, help="Features per page (default: 2000)")
    args = parser.parse_args()

    print(f"Downloading from: {args.url}")
    data = download_all_features(args.url, args.where, args.page_size)
    print(f"  Total downloaded: {len(data['features'])} features")

    with open(args.output, "w") as f:
        json.dump(data, f)
    print(f"  Written to: {args.output}")


if __name__ == "__main__":
    main()
