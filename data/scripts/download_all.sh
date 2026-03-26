#!/usr/bin/env bash
#
# download_all.sh
#
# Downloads all raw GIS datasets needed for the California hazards project.
# Idempotent: skips files that already exist. Re-run safely after interruptions.
#
# Usage:
#   chmod +x download_all.sh
#   ./download_all.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_DIR="$(cd "$SCRIPT_DIR/../raw" && pwd)"

# ---------------------------------------------------------------------------
# Requirements check
# ---------------------------------------------------------------------------
missing=()
for cmd in wget unzip ogr2ogr gdalinfo; do
    if ! command -v "$cmd" &>/dev/null; then
        missing+=("$cmd")
    fi
done

if [ ${#missing[@]} -ne 0 ]; then
    echo "ERROR: The following required tools are missing:"
    for cmd in "${missing[@]}"; do
        echo "  - $cmd"
    done
    echo ""
    echo "Install them with:"
    echo "  brew install wget gdal"
    echo "  (unzip is usually pre-installed on macOS/Linux)"
    exit 1
fi

echo "All required tools found."
echo "Raw data directory: $RAW_DIR"
echo "=========================================="

# ---------------------------------------------------------------------------
# Helper: download a file if it does not already exist
# ---------------------------------------------------------------------------
download() {
    local url="$1"
    local dest="$2"

    if [ -f "$dest" ]; then
        echo "  SKIP (already exists): $(basename "$dest")"
        return 0
    fi

    echo "  Downloading: $(basename "$dest")"
    wget --no-verbose --tries=3 --continue --timeout=60 -O "$dest" "$url"
}

# ---------------------------------------------------------------------------
# 1. USGS Quaternary Fault Database
# ---------------------------------------------------------------------------
echo ""
echo "[1/21] USGS Quaternary Fault Database (Qfaults shapefile)"
dir="$RAW_DIR/qfaults"
mkdir -p "$dir"
dest="$dir/Qfaults_GIS.zip"
download "https://earthquake.usgs.gov/static/lfs/nshm/qfaults/Qfaults_GIS.zip" "$dest"
if [ -f "$dest" ] && [ ! -d "$dir/SHP" ]; then
    echo "  Extracting..."
    unzip -qo "$dest" -d "$dir"
fi

# ---------------------------------------------------------------------------
# 2. CGS Seismic Hazard Zones (Liquefaction + Landslide)
#
# These are served via CGS ArcGIS REST services. There is no single ZIP
# download. You can bulk-export from:
#   Liquefaction: https://gis.data.ca.gov/datasets/CGS::cgs-seismic-hazard-zones-liquefaction/
#   Landslide:    https://gis.data.ca.gov/datasets/CGS::cgs-seismic-hazard-zones-landslides/
#
# The GeoJSON API endpoint (paginated, max 2000 features per request):
#   https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/CGS_Seismic_Hazard_Zones_Liquefaction/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson
#   https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/CGS_Seismic_Hazard_Zones_Landslides/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson
#
# For full download, use the "Download" button on the data.ca.gov pages
# above (choose Shapefile or File Geodatabase), or use esri2geojson / ogr2ogr
# to page through the REST API.
# ---------------------------------------------------------------------------
echo ""
echo "[2/21] CGS Seismic Hazard Zones (Liquefaction + Landslide)"
dir="$RAW_DIR/cgs_seismic_hazard_zones"
mkdir -p "$dir"

# TODO: Replace with direct download URLs if CA open data portal provides stable links.
# As of writing, the most reliable approach is to download from the CA open data portal UI.
LIQUEFACTION_URL="https://gis-cnra.hub.arcgis.com/api/download/v1/items/d5e0710599684346ae25ca9c4c943fce/shapefile?layers=0"
LANDSLIDE_URL="https://gis-cnra.hub.arcgis.com/api/download/v1/items/0860546963f147c78db3da52cd7ee413/shapefile?layers=0"

dest_liq="$dir/cgs_liquefaction_zones.zip"
dest_ls="$dir/cgs_landslide_zones.zip"
download "$LIQUEFACTION_URL" "$dest_liq"
download "$LANDSLIDE_URL" "$dest_ls"
for z in "$dest_liq" "$dest_ls"; do
    base="$(basename "$z" .zip)"
    if [ -f "$z" ] && [ ! -d "$dir/$base" ]; then
        echo "  Extracting $(basename "$z")..."
        unzip -qo "$z" -d "$dir/$base"
    fi
done

# ---------------------------------------------------------------------------
# 3. FEMA National Flood Hazard Layer (NFHL) - California
#
# The full state extract is very large (~4 GB). FEMA provides it via:
#   https://hazards.fema.gov/femaportal/NFHL/searchResult
# Select "California" and request a download. A link is emailed to you.
#
# Alternatively, query individual counties from the NFHL REST service:
#   https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer
#
# A direct statewide download URL (may change):
# ---------------------------------------------------------------------------
echo ""
echo "[3/21] FEMA NFHL - California Flood Zones"
dir="$RAW_DIR/fema_nfhl"
mkdir -p "$dir"

# TODO: FEMA does not provide a persistent direct download URL for statewide NFHL.
# Request the statewide extract at https://hazards.fema.gov/femaportal/NFHL/searchResult
# and save the resulting ZIP into: $dir/NFHL_06_California.zip
FEMA_URL="https://hazards.fema.gov/nfhlv2/output/County/060001_20240329.zip"
echo "  NOTE: FEMA NFHL requires manual download or county-by-county fetch."
echo "  Visit: https://hazards.fema.gov/femaportal/NFHL/searchResult"
echo "  Save statewide ZIP to: $dir/"
# Uncomment below to download a single county as an example:
# download "$FEMA_URL" "$dir/NFHL_06001.zip"

# ---------------------------------------------------------------------------
# 4. CAL FIRE Fire Hazard Severity Zones (SRA + LRA)
# ---------------------------------------------------------------------------
echo ""
echo "[4/21] CAL FIRE Fire Hazard Severity Zones"
dir="$RAW_DIR/calfire_fhsz"
mkdir -p "$dir"

# NOTE: FHSZ is now served as a single combined layer via ArcGIS Feature Service.
# Use the download_arcgis_service.py helper instead:
#   python3 data/scripts/download_arcgis_service.py \
#     "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/Fire_Hazard_Severity_Zones/FeatureServer/0" \
#     -o "$dir/fhsz_combined.geojson"
# The old separate SRA/LRA download URLs no longer work.
SRA_URL="https://gis.data.ca.gov/api/download/v1/items/5ac1dae0cb1f4a5e8b2158e1d68968e3/shapefile?layers=0"
LRA_URL="https://gis.data.ca.gov/api/download/v1/items/16466e0c26714eb3b6e5c036bc1e28e0/shapefile?layers=0"

download "$SRA_URL" "$dir/fhsz_sra.zip"
download "$LRA_URL" "$dir/fhsz_lra.zip"
for z in "$dir"/fhsz_*.zip; do
    base="$(basename "$z" .zip)"
    if [ -f "$z" ] && [ ! -d "$dir/$base" ]; then
        echo "  Extracting $(basename "$z")..."
        unzip -qo "$z" -d "$dir/$base"
    fi
done

# ---------------------------------------------------------------------------
# 5. CGS Tsunami Hazard Areas
#
# Available from CA open data portal:
#   https://gis.data.ca.gov/datasets/CGS::cgs-tsunami-hazard-area/
# ---------------------------------------------------------------------------
echo ""
echo "[5/21] CGS Tsunami Hazard Areas"
dir="$RAW_DIR/cgs_tsunami"
mkdir -p "$dir"

# NOTE: Tsunami data from CalOES has very large polygons. Download per-feature:
#   python3 data/scripts/download_arcgis_service.py \
#     "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/CA_Tsunami_Hazard_Area_Evacuate/FeatureServer/0" \
#     -o "$dir/tsunami_hazard_area.geojson"
TSUNAMI_URL="https://gis.data.ca.gov/api/download/v1/items/b874d75ddea14484bba24ce5b16e55f1/shapefile?layers=0"
download "$TSUNAMI_URL" "$dir/cgs_tsunami_hazard_area.zip"
if [ -f "$dir/cgs_tsunami_hazard_area.zip" ] && [ ! -d "$dir/cgs_tsunami_hazard_area" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/cgs_tsunami_hazard_area.zip" -d "$dir/cgs_tsunami_hazard_area"
fi

# ---------------------------------------------------------------------------
# 6. USGS Shoreline Change transects (California)
#
# National Assessment of Shoreline Change - Pacific coast:
#   https://www.sciencebase.gov/catalog/item/5e774a0fe4b01d509270e3bf
#   https://coastal.er.usgs.gov/shoreline-change/
# ---------------------------------------------------------------------------
echo ""
echo "[6/21] USGS Shoreline Change Transects (California)"
dir="$RAW_DIR/usgs_shoreline_change"
mkdir -p "$dir"

# Pacific coast long-term shoreline change rates
SHORELINE_URL="https://www.sciencebase.gov/catalog/file/get/5e774a0fe4b01d509270e3bf?f=__disk__e1%2F5a%2F06%2Fe15a0634be4e81b5c9cff0f85fabe0e04de01dbc"
# TODO: The ScienceBase URL above may change. If it fails, visit:
#   https://www.sciencebase.gov/catalog/item/5e774a0fe4b01d509270e3bf
# and download the transect shapefile manually.
download "$SHORELINE_URL" "$dir/pacific_shoreline_change_transects.zip"
if [ -f "$dir/pacific_shoreline_change_transects.zip" ] && [ ! -d "$dir/transects" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/pacific_shoreline_change_transects.zip" -d "$dir/transects"
fi

# ---------------------------------------------------------------------------
# 7. NOAA Medium Resolution Shoreline
# ---------------------------------------------------------------------------
echo ""
echo "[7/21] NOAA Medium Resolution Shoreline"
dir="$RAW_DIR/noaa_shoreline"
mkdir -p "$dir"

NOAA_SHORE_URL="https://coast.noaa.gov/htdata/Shoreline/us_medium_shoreline.zip"
download "$NOAA_SHORE_URL" "$dir/us_medium_shoreline.zip"
if [ -f "$dir/us_medium_shoreline.zip" ] && [ ! -d "$dir/us_medium_shoreline" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/us_medium_shoreline.zip" -d "$dir/us_medium_shoreline"
fi

# ---------------------------------------------------------------------------
# 8. NOAA Sea Level Rise Inundation Layers (1ft - 10ft, California)
#
# NOAA provides these via the Digital Coast / SLR Viewer:
#   https://coast.noaa.gov/slrdata/
# Individual GeoTIFFs are organized by region. For California, the relevant
# tiles cover the coast from San Diego to Crescent City.
#
# The data can be accessed from NOAA's SLR ArcGIS REST service:
#   https://coast.noaa.gov/arcgis/rest/services/dc_slr/
# ---------------------------------------------------------------------------
echo ""
echo "[8/21] NOAA Sea Level Rise Inundation Layers (1ft - 10ft)"
dir="$RAW_DIR/noaa_slr"
mkdir -p "$dir"

# TODO: NOAA SLR data does not have a single bulk download for California.
# Download via the NOAA Digital Coast Data Access Viewer:
#   https://coast.noaa.gov/dataviewer/#/lidar/search/where:ID=8483
# Or use the SLR ArcGIS REST service to export tiles:
#   https://coast.noaa.gov/arcgis/rest/services/dc_slr/
#
# Example: individual foot-level GeoTIFFs for the San Francisco Bay region:
SLR_BASE="https://coast.noaa.gov/htdata/Inundation/SLR"
for ft in $(seq 1 10); do
    # TODO: Actual file paths vary by region. Replace with correct paths.
    echo "  NOTE: SLR ${ft}ft data requires manual download from NOAA Digital Coast."
done
echo "  Visit: https://coast.noaa.gov/slrdata/"
echo "  Save GeoTIFFs to: $dir/"

# ---------------------------------------------------------------------------
# 9. Cal OES Dam Breach Inundation Zones
#
# Available from CA open data portal:
#   https://gis.data.ca.gov/datasets/CalOES::dam-breach-inundation/
#
# Also available from DSOD (Division of Safety of Dams).
# ---------------------------------------------------------------------------
echo ""
echo "[9/21] Cal OES Dam Breach Inundation Zones"
dir="$RAW_DIR/caloes_dam_inundation"
mkdir -p "$dir"

DAM_URL="https://gis.data.ca.gov/api/download/v1/items/07e2b78491a14b5eb7acc87eeed8b85d/shapefile?layers=0"
# TODO: The item ID above may not be stable. If download fails, visit:
#   https://gis.data.ca.gov/datasets/CalOES::dam-breach-inundation/
# and download the shapefile from the portal UI.
download "$DAM_URL" "$dir/dam_breach_inundation.zip"
if [ -f "$dir/dam_breach_inundation.zip" ] && [ ! -d "$dir/dam_breach_inundation" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/dam_breach_inundation.zip" -d "$dir/dam_breach_inundation"
fi

# ---------------------------------------------------------------------------
# 10. DTSC EnviroStor Data Download
#
# DTSC provides bulk data exports:
#   https://www.envirostor.dtsc.ca.gov/public/data_download
# ---------------------------------------------------------------------------
echo ""
echo "[10/21] DTSC EnviroStor Data"
dir="$RAW_DIR/dtsc_envirostor"
mkdir -p "$dir"

ENVIROSTOR_URL="https://www.envirostor.dtsc.ca.gov/public/data_download/data_download.csv"
# TODO: Verify current URL. DTSC periodically changes their download endpoint.
# If this fails, visit https://www.envirostor.dtsc.ca.gov/public/data_download
download "$ENVIROSTOR_URL" "$dir/envirostor_sites.csv"

# ---------------------------------------------------------------------------
# 11. State Water Board GeoTracker Data Download
#
# GeoTracker bulk data:
#   https://geotracker.waterboards.ca.gov/data_download
# ---------------------------------------------------------------------------
echo ""
echo "[11/21] GeoTracker Data"
dir="$RAW_DIR/geotracker"
mkdir -p "$dir"

GEOTRACKER_URL="https://geotracker.waterboards.ca.gov/data_download/geo_by_county_csv.zip"
# TODO: Verify current URL. The Water Board may require accepting terms first.
# If this fails, visit https://geotracker.waterboards.ca.gov/data_download
download "$GEOTRACKER_URL" "$dir/geotracker_by_county.zip"
if [ -f "$dir/geotracker_by_county.zip" ] && [ ! -d "$dir/geotracker_by_county" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/geotracker_by_county.zip" -d "$dir/geotracker_by_county"
fi

# ---------------------------------------------------------------------------
# 12. FAA Airport Database
#
# FAA provides the NASR (National Airspace System Resources) data:
#   https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/
# ---------------------------------------------------------------------------
echo ""
echo "[12/21] FAA Airport Database"
dir="$RAW_DIR/faa_airports"
mkdir -p "$dir"

# The FAA 56-day cycle data (airports, runways, etc.)
FAA_URL="https://ourairports.com/data/airports.csv"
FAA_RUNWAYS_URL="https://ourairports.com/data/runways.csv"
# Using OurAirports.com (public domain, sourced from FAA) as a stable alternative
download "$FAA_URL" "$dir/airports.csv"
download "$FAA_RUNWAYS_URL" "$dir/runways.csv"

# ---------------------------------------------------------------------------
# 13. CalTrans AADT Traffic Data
#
# CalTrans publishes Annual Average Daily Traffic counts:
#   https://gis.data.ca.gov/datasets/Caltrans::california-traffic-volumes-aadt/
# ---------------------------------------------------------------------------
echo ""
echo "[13/21] CalTrans AADT Traffic Data"
dir="$RAW_DIR/caltrans_aadt"
mkdir -p "$dir"

# NOTE: CalTrans AADT is best downloaded via ArcGIS Feature Service (returns POINT data):
#   python3 data/scripts/download_arcgis_service.py \
#     "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Traffic_AADT/FeatureServer/0" \
#     -o "$dir/traffic_aadt.geojson"
AADT_URL="https://gis.data.ca.gov/api/download/v1/items/1d5a4893af1d45a9bc5a6cdb285a3342/shapefile?layers=0"
# TODO: Item ID may change. If download fails, use the ArcGIS method above.
download "$AADT_URL" "$dir/caltrans_aadt.zip"
if [ -f "$dir/caltrans_aadt.zip" ] && [ ! -d "$dir/caltrans_aadt" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/caltrans_aadt.zip" -d "$dir/caltrans_aadt"
fi

# ---------------------------------------------------------------------------
# 14. OEHHA CalEnviroScreen 4.0
#
# CalEnviroScreen 4.0 shapefile:
#   https://oehha.ca.gov/calenviroscreen/report/calenviroscreen-40
# ---------------------------------------------------------------------------
echo ""
echo "[14/21] OEHHA CalEnviroScreen 4.0"
dir="$RAW_DIR/calenviroscreen"
mkdir -p "$dir"

CES_URL="https://oehha.ca.gov/media/downloads/calenviroscreen/document/calenviroscreen40shpf2021shp.zip"
download "$CES_URL" "$dir/calenviroscreen40.zip"
if [ -f "$dir/calenviroscreen40.zip" ] && [ ! -d "$dir/calenviroscreen40" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/calenviroscreen40.zip" -d "$dir/calenviroscreen40"
fi

# ---------------------------------------------------------------------------
# 15. USDA SSURGO Soil Data for California
#
# SSURGO data is distributed by the USDA Web Soil Survey:
#   https://websoilsurvey.nrcs.usda.gov/
# Or via the SSURGO direct download:
#   https://nrcs.app.box.com/v/soils/
#
# California statewide gSSURGO (gridded) is more practical for GIS:
#   https://nrcs.app.box.com/v/soils/folder/191785692827
# ---------------------------------------------------------------------------
echo ""
echo "[15/21] USDA SSURGO Soil Data (California)"
dir="$RAW_DIR/usda_ssurgo"
mkdir -p "$dir"

# TODO: SSURGO data requires navigating the NRCS Box folder or using the
# Web Soil Survey tool. The gSSURGO statewide geodatabase for California
# is ~1.5 GB. Download from:
#   https://nrcs.app.box.com/v/soils/folder/191785692827
# Look for: gSSURGO_CA.gdb.zip
# Save to: $dir/gSSURGO_CA.gdb.zip
echo "  NOTE: SSURGO data requires manual download from NRCS."
echo "  Visit: https://nrcs.app.box.com/v/soils/folder/191785692827"
echo "  Download gSSURGO_CA.gdb.zip and save to: $dir/"

# ---------------------------------------------------------------------------
# 16. EPA Radon Zone Map + CGS Radon Data
#
# EPA radon zones:
#   https://www.epa.gov/radon/epa-map-radon-zones
# CGS indoor radon data:
#   https://www.conservation.ca.gov/cgs/minerals/radon
# ---------------------------------------------------------------------------
echo ""
echo "[16/21] EPA Radon Zones + CGS Radon Data"
dir="$RAW_DIR/radon"
mkdir -p "$dir"

# EPA radon zone shapefile (national)
EPA_RADON_URL="https://www.epa.gov/sites/default/files/2014-08/epa_radon_zones.zip"
# TODO: The EPA URL above may not serve a shapefile directly. If it fails:
#   1. Visit https://www.epa.gov/radon/epa-map-radon-zones
#   2. Download the GIS data for California (Zone 2 for most of the state)
#
# CGS radon potential data:
# TODO: CGS does not provide a direct bulk download. Contact CGS or scrape from:
#   https://www.conservation.ca.gov/cgs/minerals/radon
download "$EPA_RADON_URL" "$dir/epa_radon_zones.zip" || echo "  WARN: EPA radon download failed. See TODO in script."
if [ -f "$dir/epa_radon_zones.zip" ] && [ ! -d "$dir/epa_radon_zones" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/epa_radon_zones.zip" -d "$dir/epa_radon_zones"
fi

# ---------------------------------------------------------------------------
# 17. USGS 3DEP Elevation Tiles for California
#
# 3DEP (1/3 arc-second, ~10m resolution) DEM tiles:
#   https://apps.nationalmap.gov/downloader/
#
# There are hundreds of tiles for California. The TNM (The National Map) API
# can be used to query and download them:
#   https://tnmaccess.nationalmap.gov/api/v1/products?datasets=National+Elevation+Dataset+(NED)+1/3+arc-second&bbox=-124.5,32.5,-114.0,42.0&max=100
# ---------------------------------------------------------------------------
echo ""
echo "[17/21] USGS 3DEP Elevation Tiles"
dir="$RAW_DIR/usgs_3dep"
mkdir -p "$dir"

# TODO: Downloading all 3DEP tiles for California is a large operation
# (~50+ GB for 1/3 arc-second statewide). Use The National Map Downloader:
#   https://apps.nationalmap.gov/downloader/
# Select: Elevation Products (3DEP) > 1/3 arc-second DEM
# Draw bounding box around California, then download selected tiles.
#
# Alternatively, use the TNM API to generate a download list:
#   curl "https://tnmaccess.nationalmap.gov/api/v1/products?datasets=National%20Elevation%20Dataset%20(NED)%201/3%20arc-second&bbox=-124.5,32.5,-114.0,42.0&max=500" | python3 -c "import sys,json; [print(i['downloadURL']) for i in json.load(sys.stdin)['items']]" > "$dir/tile_urls.txt"
#   wget -i "$dir/tile_urls.txt" -P "$dir" --no-clobber
echo "  NOTE: 3DEP elevation tiles are very large (~50+ GB statewide)."
echo "  Use The National Map Downloader to select and download tiles."
echo "  Save to: $dir/"

# ---------------------------------------------------------------------------
# 18. CGS Vs30 Map (Shear Wave Velocity)
#
# CGS Vs30 map for California:
#   https://www.conservation.ca.gov/cgs/vs30
#   https://gis.data.ca.gov/datasets/CGS::cgs-vs30-map/
# ---------------------------------------------------------------------------
echo ""
echo "[18/21] CGS Vs30 Map"
dir="$RAW_DIR/cgs_vs30"
mkdir -p "$dir"

VS30_URL="https://gis.data.ca.gov/api/download/v1/items/b60a3f674a014743af5af453c2884b0d/shapefile?layers=0"
# TODO: Item ID may change. If download fails, visit:
#   https://gis.data.ca.gov/datasets/CGS::cgs-vs30-map/
download "$VS30_URL" "$dir/cgs_vs30.zip"
if [ -f "$dir/cgs_vs30.zip" ] && [ ! -d "$dir/cgs_vs30" ]; then
    echo "  Extracting..."
    unzip -qo "$dir/cgs_vs30.zip" -d "$dir/cgs_vs30"
fi

# ---------------------------------------------------------------------------
# 19. CAL FIRE FRAP Fire Perimeters (1996-present)
#
# Historical fire perimeters from CAL FIRE's Fire and Resource Assessment
# Program (FRAP). Available via ArcGIS REST service:
#   https://egis.fire.ca.gov/arcgis/rest/services
# Or direct download from FRAP:
#   https://frap.fire.ca.gov/frap-projects/fire-perimeters/
#
# The existing raw file is ca_fire_perimeters_1996_2025.geojson, downloaded
# via download_arcgis_service.py from the CAL FIRE ArcGIS Feature Service.
# ---------------------------------------------------------------------------
echo ""
echo "[19/21] CAL FIRE FRAP Fire Perimeters"
dir="$RAW_DIR/calfire_perimeters"
mkdir -p "$dir"

# NOTE: The full fire perimeter dataset is best downloaded via ArcGIS Feature
# Service using the download_arcgis_service.py helper:
#   python3 data/scripts/download_arcgis_service.py \
#     "https://egis.fire.ca.gov/arcgis/rest/services/FRAP/FirePerimeters/FeatureServer/0" \
#     -o "$dir/ca_fire_perimeters_1996_2025.geojson"
#
# If you prefer a direct ZIP download (may not include most recent years):
FRAP_URL="https://frap.fire.ca.gov/media/mn1f5fw1/fire22_1.zip"
dest="$dir/fire22_1.zip"
download "$FRAP_URL" "$dest"
if [ -f "$dest" ] && [ ! -d "$dir/fire22_1" ]; then
    echo "  Extracting..."
    unzip -qo "$dest" -d "$dir/fire22_1"
fi
# Convert to GeoJSON if the combined file does not exist yet
if [ -d "$dir/fire22_1" ] && [ ! -f "$dir/ca_fire_perimeters_1996_2025.geojson" ]; then
    echo "  Converting to GeoJSON with ogr2ogr..."
    shp=$(find "$dir/fire22_1" -name "*.shp" | head -1)
    if [ -n "$shp" ]; then
        ogr2ogr -f GeoJSON "$dir/ca_fire_perimeters_1996_2025.geojson" "$shp" \
            -where "YEAR_ >= 1996"
    else
        echo "  WARN: No .shp found in fire22_1. Use download_arcgis_service.py instead."
    fi
fi

# ---------------------------------------------------------------------------
# 20. Census Bureau TIGER/Line Tract Shapefiles (California)
#
# TIGER/Line shapefiles for Census tracts:
#   https://www.census.gov/cgi-bin/geo/shapefiles/index.php?year=2023&layergroup=Census+Tracts
# FIPS code 06 = California.
# ---------------------------------------------------------------------------
echo ""
echo "[20/21] Census Bureau TIGER/Line Tract Shapefiles (California)"
dir="$RAW_DIR/census_tracts"
mkdir -p "$dir"

CENSUS_TRACT_URL="https://www2.census.gov/geo/tiger/TIGER2023/TRACT/tl_2023_06_tract.zip"
dest="$dir/tl_2023_06_tract.zip"
download "$CENSUS_TRACT_URL" "$dest"
if [ -f "$dest" ] && [ ! -f "$dir/tl_2023_06_tract.shp" ]; then
    echo "  Extracting..."
    unzip -qo "$dest" -d "$dir"
fi

# ---------------------------------------------------------------------------
# 21. FEMA National Risk Index (NRI) - Census Tract Level
#
# NRI data provides composite and per-hazard risk scores at the Census tract
# level. Download from:
#   https://hazards.fema.gov/nri/data-resources
# Select "NRI Table - Census Tracts" CSV download.
# ---------------------------------------------------------------------------
echo ""
echo "[21/21] FEMA National Risk Index (NRI) - Census Tract Level"
dir="$RAW_DIR"

NRI_URL="https://hazards.fema.gov/nri/Content/StaticDocuments/DataDownload/NRI_Table_CensusTracts/NRI_Table_CensusTracts.zip"
dest="$dir/NRI_Table_CensusTracts.zip"
download "$NRI_URL" "$dest"
if [ -f "$dest" ] && [ ! -f "$dir/NRI_Table_CensusTracts.csv" ]; then
    echo "  Extracting..."
    unzip -qo "$dest" -d "$dir"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=========================================="
echo "Download script complete."
echo ""
echo "Datasets requiring manual download:"
echo "  [3]  FEMA NFHL - request from FEMA portal"
echo "  [8]  NOAA SLR  - download from Digital Coast"
echo "  [15] SSURGO    - download from NRCS Box"
echo "  [17] 3DEP      - download from National Map"
echo ""
echo "Datasets with potentially unstable URLs (check TODOs if failed):"
echo "  [2]  CGS Seismic Hazard Zones"
echo "  [6]  USGS Shoreline Change"
echo "  [9]  Cal OES Dam Breach"
echo "  [10] DTSC EnviroStor"
echo "  [11] GeoTracker"
echo "  [13] CalTrans AADT"
echo "  [16] EPA Radon"
echo "  [18] CGS Vs30"
echo "  [19] CAL FIRE FRAP Perimeters"
echo "  [21] FEMA NRI"
echo ""
echo "All downloaded files are in: $RAW_DIR"
