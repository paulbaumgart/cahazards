# California Hazards

Multi-hazard property risk reports for any California address. Combines public data from USGS, FEMA, CalFire, NOAA, and state agencies into a single probabilistic assessment.

**Live:** [cahazards.com](https://cahazards.com)

## What it reports

**Structural hazards** (30-year damage probability):
- Earthquake (USGS NSHMP hazard curves + HAZUS W1 fragility)
- Wildfire (FSim burn probability + DINS structural damage model)
- Flood (FEMA NFHL zones + depth-damage curves)
- Landslide (Sentinel-1 InSAR velocity + FEMA NRI + CGS zones)
- Tsunami (CGS inundation zones + elevation)
- Coastal erosion (USGS shoreline change transects)
- Dam failure (Cal OES breach inundation zones)

**Environmental & health:**
- CalEnviroScreen 4.0 percentile
- Contamination sites (GeoTracker + EnviroStor)
- Traffic pollution (Caltrans AADT proximity)
- Aviation lead exposure (FAA piston operations)
- Sea level rise (NOAA CoSMoS, 1ft–10ft scenarios)

**Seismic context:**
- All UCERF3 faults within 30 miles with distance, type, magnitude, and 30-year probability

## Architecture

Runs on Cloudflare's free tier: Worker (TypeScript) + R2 (spatial tiles) + D1 (address autocomplete).

```
frontend/          Vanilla JS single-page app
src/               Cloudflare Worker
  worker.ts        API routes, static serving, doc page renderer
  model/hazards.ts Hazard probability models (most sensitive code)
  tiles.ts         Spatial tile fetching from R2
  raster.ts        Binary raster sampling (elevation, burn probability, Vs30)
  spatial.ts       Point-in-polygon, nearest-feature queries
docs/              Model documentation (served as HTML at /docs/*)
data/scripts/      Python data pipeline (download, process, tile)
ideas/             Future feature designs
```

## Data pipeline

All spatial data is pre-processed into 0.1-degree tiles and uploaded to R2. The worker does no external API calls at request time (earthquake hazard curves are fetched client-side from USGS NSHMP).

```bash
# 1. Download raw data (~50GB)
bash data/scripts/download_all.sh
# Some datasets require manual download — the script will print instructions.
# FEMA NFHL, NOAA SLR, USDA SSURGO, and USGS 3DEP need manual steps.

# 2. Process into GeoJSON
python3 data/scripts/process_faults.py
python3 data/scripts/process_calfire.py
# ... (one script per dataset, see data/scripts/)

# 3. Tile for R2
python3 data/scripts/tile_all.py

# 4. Build derived products
python3 data/scripts/compute_fsim_calibration.py
python3 data/scripts/build_fire_model.py
python3 data/scripts/build_address_index.py
```

## Development

```bash
source .venv/bin/activate          # Python for data scripts
npx wrangler dev --port 8787       # Local Worker + R2
python3 scripts/seed-local-r2.py   # Seed tiles into local R2
```

## Deployment

Requires [rclone](https://rclone.org/) configured with a Cloudflare R2 remote named `cahazards`.

```bash
# First time
bash scripts/deploy.sh --init      # Create R2 bucket + D1 database
# Update wrangler.toml with the D1 database_id from output
bash scripts/deploy.sh --seed      # Upload tiles + addresses (~112K files)
bash scripts/deploy.sh --deploy    # Deploy worker

# Code-only updates
bash scripts/deploy.sh --seed-tiles  # Sync tiles/docs/frontend
bash scripts/deploy.sh --deploy      # Deploy worker
```

## Model documentation

Each hazard model is documented with methodology, thresholds, limitations, and sources:

- [Earthquake](/docs/earthquake-model)
- [Wildfire](/docs/wildfire-model)
- [Flood](/docs/flood-model)
- [Landslide](/docs/landslide-model)
- [Tsunami](/docs/tsunami-model)
- [Coastal Erosion](/docs/erosion-model)
- [Dam Failure](/docs/dam-inundation-model)
- [Traffic Pollution](/docs/traffic-pollution-model)
- [Aviation Lead](/docs/aviation-lead-model)

## Disclaimer

This tool is for informational purposes only and does not constitute professional geological, environmental, or engineering advice. Consult qualified professionals before making property decisions.

## License

MIT
