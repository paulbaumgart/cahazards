#!/usr/bin/env bash
# Download FEMA NFHL data for all California counties
set -euo pipefail

DIR="data/raw/fema_nfhl"
mkdir -p "$DIR"

URLS=(
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06001C&state=CALIFORNIA&county=ALAMEDA%20COUNTY&fileName=06001C_20241119.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06003C&state=CALIFORNIA&county=ALPINE%20COUNTY&fileName=06003C_20231115.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06005C&state=CALIFORNIA&county=AMADOR%20COUNTY&fileName=06005C_20220630.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06007C&state=CALIFORNIA&county=BUTTE%20COUNTY&fileName=06007C_20220630.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06009C&state=CALIFORNIA&county=CALAVERAS%20COUNTY&fileName=06009C_20220630.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06011C&state=CALIFORNIA&county=COLUSA%20COUNTY&fileName=06011C_20240327.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06013C&state=CALIFORNIA&county=CONTRA%20COSTA%20COUNTY&fileName=06013C_20240723.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06015C&state=CALIFORNIA&county=DEL%20NORTE%20COUNTY&fileName=06015C_20220811.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06017C&state=CALIFORNIA&county=EL%20DORADO%20COUNTY&fileName=06017C_20220825.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06019C&state=CALIFORNIA&county=FRESNO%20COUNTY&fileName=06019C_20240519.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06021C&state=CALIFORNIA&county=GLENN%20COUNTY&fileName=06021C_20220825.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06023C&state=CALIFORNIA&county=HUMBOLDT%20COUNTY&fileName=06023C_20220825.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06025C&state=CALIFORNIA&county=IMPERIAL%20COUNTY&fileName=06025C_20231018.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06027C&state=CALIFORNIA&county=INYO%20COUNTY&fileName=06027C_20231018.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=060298&state=CALIFORNIA&county=%20CITY%20OF%20SAN%20FRANCISCO&fileName=060298_20221004.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06029C&state=CALIFORNIA&county=KERN%20COUNTY&fileName=06029C_20250430.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06031C&state=CALIFORNIA&county=KINGS%20COUNTY&fileName=06031C_20211111.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06033C&state=CALIFORNIA&county=LAKE%20COUNTY&fileName=06033C_20241009.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06035C&state=CALIFORNIA&county=LASSEN%20COUNTY&fileName=06035C_20221004.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06037C&state=CALIFORNIA&county=LOS%20ANGELES%20COUNTY&fileName=06037C_20260127.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=060394&state=CALIFORNIA&county=SUTTER%20COUNTY*%20&fileName=060394_20221004.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06039C&state=CALIFORNIA&county=MADERA%20COUNTY&fileName=06039C_20231018.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06041C&state=CALIFORNIA&county=MARIN%20COUNTY&fileName=06041C_20231018.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06043C&state=CALIFORNIA&county=MARIPOSA%20COUNTY&fileName=06043C_20231018.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06045C&state=CALIFORNIA&county=MENDOCINO%20COUNTY&fileName=06045C_20250918.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06047C&state=CALIFORNIA&county=MERCED%20COUNTY&fileName=06047C_20221004.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06049C&state=CALIFORNIA&county=MODOC%20COUNTY&fileName=06049C_20221004.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06051C&state=CALIFORNIA&county=MONO%20COUNTY&fileName=06051C_20221117.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06053C&state=CALIFORNIA&county=MONTEREY%20COUNTY&fileName=06053C_20240715.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06055C&state=CALIFORNIA&county=NAPA%20COUNTY&fileName=06055C_20240625.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06057C&state=CALIFORNIA&county=NEVADA%20COUNTY&fileName=06057C_20240820.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06059C&state=CALIFORNIA&county=ORANGE%20COUNTY&fileName=06059C_20250714.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06061C&state=CALIFORNIA&county=PLACER%20COUNTY&fileName=06061C_20231221.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06063C&state=CALIFORNIA&county=PLUMAS%20COUNTY&fileName=06063C_20221004.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06065C&state=CALIFORNIA&county=RIVERSIDE%20COUNTY&fileName=06065C_20251208.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06067C&state=CALIFORNIA&county=SACRAMENTO%20COUNTY&fileName=06067C_20260120.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06069C&state=CALIFORNIA&county=SAN%20BENITO%20COUNTY&fileName=06069C_20221004.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06071C&state=CALIFORNIA&county=SAN%20BERNARDINO%20COUNTY&fileName=06071C_20260215.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06073C&state=CALIFORNIA&county=SAN%20DIEGO%20COUNTY&fileName=06073C_20260302.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06077C&state=CALIFORNIA&county=SAN%20JOAQUIN%20COUNTY&fileName=06077C_20260208.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06079C&state=CALIFORNIA&county=SAN%20LUIS%20OBISPO%20COUNTY&fileName=06079C_20250722.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06081C&state=CALIFORNIA&county=SAN%20MATEO%20COUNTY&fileName=06081C_20250813.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06083C&state=CALIFORNIA&county=SANTA%20BARBARA%20COUNTY&fileName=06083C_20231004.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06085C&state=CALIFORNIA&county=SANTA%20CLARA%20COUNTY&fileName=06085C_20260118.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06087C&state=CALIFORNIA&county=SANTA%20CRUZ%20COUNTY&fileName=06087C_20190710.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06089C&state=CALIFORNIA&county=SHASTA%20COUNTY&fileName=06089C_20260120.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06091C&state=CALIFORNIA&county=SIERRA%20COUNTY&fileName=06091C_20190927.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06093C&state=CALIFORNIA&county=SISKIYOU%20COUNTY&fileName=06093C_20251210.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06095C&state=CALIFORNIA&county=SOLANO%20COUNTY&fileName=06095C_20250817.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06097C&state=CALIFORNIA&county=SONOMA%20COUNTY&fileName=06097C_20260215.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06099C&state=CALIFORNIA&county=STANISLAUS%20COUNTY&fileName=06099C_20221023.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06103C&state=CALIFORNIA&county=TEHAMA%20COUNTY&fileName=06103C_20221023.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06105C&state=CALIFORNIA&county=TRINITY%20COUNTY&fileName=06105C_20221023.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06107C&state=CALIFORNIA&county=TULARE%20COUNTY&fileName=06107C_20250511.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06109C&state=CALIFORNIA&county=TUOLUMNE%20COUNTY&fileName=06109C_20221021.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06111C&state=CALIFORNIA&county=VENTURA%20COUNTY&fileName=06111C_20250722.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06113C&state=CALIFORNIA&county=YOLO%20COUNTY&fileName=06113C_20250611.zip"
"https://hazards.fema.gov/femaportal/NFHL/Download/ProductsDownLoadServlet?DFIRMID=06115C&state=CALIFORNIA&county=YUBA%20COUNTY&fileName=06115C_20240605.zip"
)

echo "Downloading FEMA NFHL for ${#URLS[@]} California counties..."
echo "Target directory: $DIR"
echo ""

downloaded=0
failed=0
skipped=0

for url in "${URLS[@]}"; do
    # Extract filename from URL
    fname=$(echo "$url" | sed 's/.*fileName=//;s/&.*//')
    dest="$DIR/$fname"

    if [ -f "$dest" ]; then
        skipped=$((skipped + 1))
        continue
    fi

    if curl -sS -L -o "$dest" "$url" 2>/dev/null; then
        size=$(ls -lh "$dest" | awk '{print $5}')
        echo "  OK: $fname ($size)"
        downloaded=$((downloaded + 1))
    else
        echo "  FAIL: $fname"
        rm -f "$dest"
        failed=$((failed + 1))
    fi
done

echo ""
echo "Done. Downloaded: $downloaded, Skipped: $skipped, Failed: $failed"
echo "Total files: $(ls "$DIR"/*.zip 2>/dev/null | wc -l)"
echo "Total size: $(du -sh "$DIR" | cut -f1)"
