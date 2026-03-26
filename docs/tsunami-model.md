# Tsunami Risk Model

## What it does

Estimates the 30-year probability of major structural damage from tsunami inundation, accounting for the parcel's elevation above sea level.

## How it works

The model checks whether a parcel falls inside a [CGS (California Geological Survey) tsunami inundation zone](https://www.conservation.ca.gov/cgs/tsunami/maps), then applies an elevation-based attenuation.

**Step 1: Zone check.** CGS tsunami inundation maps represent worst-case scenarios — typically a Cascadia M9.0+ or near-source submarine landslide. If the parcel is outside all CGS zones, tsunami risk is zero.

**Step 2: Elevation attenuation.** Being inside the mapped zone does not mean equal risk. A site at 3 meters elevation faces catastrophic inundation, while a site at 20 meters within the same zone boundary is effectively safe. The model scales damage probability by elevation:

| Elevation | Damage factor | Reasoning |
|-----------|---------------|-----------|
| < 5m | 50% | Full inundation depth in worst-case event |
| 5-15m | Linear decay 50%→0% | Partial inundation, decreasing with height |
| > 15m | 1% | Residual risk from unmapped local effects |

When elevation data is unavailable, distance to coast is used as a proxy (< 200m assumed low-lying, > 1km assumed elevated).

**Step 3: Annual rate.** The base annual probability of a damaging tsunami at the California coast is approximately 0.1% (roughly a 1,000-year return period for a major event). This is multiplied by the elevation-dependent damage factor.

Maximum credible tsunami runup for California is 10-15 meters, from either a Cascadia M9.2 subduction earthquake or a near-source submarine landslide.

## What it doesn't do

**Incomplete coastal coverage.** CGS tsunami inundation mapping covers 13 of approximately 20 coastal California counties. Unmapped counties default to zero tsunami risk even if they have real exposure.

**No far-field tsunami modeling.** The model relies on CGS inundation zones, which primarily represent near-field sources (Cascadia, local submarine landslides). Trans-Pacific tsunamis from Chile or Japan are lower-amplitude but more frequent, and may not be fully captured in the CGS zones.

**No dynamic wave modeling.** The damage factor is based on elevation alone, not on wave velocity, debris loading, or building construction.

## Sources

- [CGS Tsunami Inundation Zone maps](https://www.conservation.ca.gov/cgs/tsunami/maps) (vector polygons)
- 10m elevation raster (USGS 3DEP)
