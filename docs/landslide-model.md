# Landslide Risk Model

## What it does

For every parcel in California, this model produces a single number: the probability of major structural damage from landslide activity within 30 years.

It answers an actuarial question, not a geotechnical one. We are not modeling the physics of slope failure. We are organizing data that already exists — satellite measurements of ground movement, mapped deposits, state hazard zones, and federal loss records — into a parcel-level risk score.


## Four tiers

Every parcel falls into one of four tiers based on spatial overlay against public datasets. The tiers are determined by data, not by our judgment.

**Tier 1: Active ground movement.** If Sentinel-1 InSAR satellite data shows ground velocity exceeding 3.5 mm/year at the parcel, or a licensed geologic study has documented active movement, the parcel is Tier 1 regardless of whether a deposit polygon exists in any inventory. InSAR is the primary classifier — it sees what mappers missed. The 3.5 mm/year threshold equals twice the typical Sentinel-1 velocity standard deviation ([Sadeghi et al. 2021](https://doi.org/10.1016/j.rse.2021.112306)), the minimum for reliable detection.

P(major damage, 30yr) = 50%. Derived from documented California active complexes: Portuguese Bend destroyed 140 of 170 homes (1956-58), La Conchita destroyed 13 homes and killed 10 people (2005). This is a single rate across all active velocities — conservative for fast-moving complexes, aggressive for slow ones. Velocity-graded rates require California-specific fragility data for wood-frame construction that does not exist.

**Tier 2: Dormant deposit.** The parcel sits on a mapped deposit in the [USGS Landslide Inventory](https://doi.org/10.5066/P9FZUX6N) (confidence ≥ 3) but InSAR shows no measurable movement, or InSAR data is unavailable due to vegetation decorrelation.

P(major damage, 30yr) ≈ 8%. This is the midpoint of a plausible range (2-14%) derived from two independent sources:

- *Lower bound (~2-3%)*: [Handwerger et al. 2019](https://doi.org/10.1029/2019JF005035) observed 193/6,500 mapped deposits reactivated in a single extreme wet year in the Eel River study area (3.0%). Not all reactivations cause structural damage.
- *Upper bound (~14%)*: [Crovelli & Coe 2008](https://pubs.usgs.gov/of/2008/1116/) (USGS OFR 2008-1116) estimated 65 damaging landslides per year in the 10-county SF Bay Area, which has approximately 30,000 mapped deposits. If each event damages ~3 structures and 70% occur on mapped deposits, the 30-year rate is ~14%.

The range is wide because we don't know (1) what fraction of the 65 annual damaging events in the Bay Area occur on mapped deposits vs first-time failures, and (2) how many structures each event damages. The midpoint is the least biased estimate given this uncertainty. It should be updated when California-specific damage-to-deposit linkage data becomes available.

**Tier 3: CGS Seismic Hazard Zone.** The parcel falls inside a California Geological Survey earthquake-induced landslide zone but has no mapped deposit. Uses the [Mirus et al. 2024](https://doi.org/10.1029/2024AV001214) n10 susceptibility score at the point, calibrated against the USGS inventory. CGS zones correspond to n10 values of 76-78 (median), giving approximately 0.5%/30yr.

**Tier 4: Background.** None of the above. [FEMA National Risk Index](https://hazards.fema.gov/nri/landslide) tract-level loss rate, computed as LNDS_AFREQ × LNDS_HLRB from the NRI v1.20 data (December 2025).


## How it works

The model overlays five datasets to classify each parcel:

**[USGS Landslide Inventory v3](https://doi.org/10.5066/P9FZUX6N) (February 2025).** 135,497 California deposit polygons with confidence ≥ 3. Each carries InSAR velocity properties from OPERA-DISP processing.

**[NASA OPERA-DISP Sentinel-1 InSAR](https://doi.org/10.5067/SNWG/OPL3DISPS1-V1).** Surface displacement velocity from Sentinel-1 C-band radar. We processed all 102 OPERA-DISP frames covering California: downloaded 8 displacement granules per frame spanning the full time series, computed pixel-wise velocity via linear regression (removes seasonal bias), and sampled all pixels within each inventory polygon boundary. Maximum velocity per polygon is used for classification. 70% of inventory polygons have velocity data; 30% are decorrelated (dense vegetation, primarily in the Bay Area coastal fog belt and northern Coast Ranges).

**[CGS Seismic Hazard Zone maps](https://www.conservation.ca.gov/cgs/geohazards/eq-zapp).** Earthquake-induced landslide susceptibility zones mapped by the California Geological Survey under state mandate.

**[FEMA National Risk Index](https://hazards.fema.gov/nri/landslide) v1.20 (December 2025).** Census tract-level expected annual loss from landslides, derived from SHELDUS disaster loss data.

**Supplemental layer.** Hand-curated polygons for known active complexes that fall through all automated data sources. Each entry is sourced from a site-specific geologic investigation by a licensed professional:
- Seal Cove, Moss Beach: [Cotton, Shires and Associates Report E6195](https://www.smcgov.org/planning/sealcoveinfo) (December 2025), prepared for San Mateo County. Active rotational landslide, 84 mm/yr, road closures and red-tagged structures. Polygon boundaries validated against 21 geocoded addresses from the report's hazard zone map.
- La Conchita, Ventura County: [USGS Open-File Report 2005-1067](https://pubs.usgs.gov/of/2005/1067/) (Jibson, 2005). Debris runout zone of ancient landslide complex. 10 fatalities in 2005.


## What it doesn't do

This is not a slope stability analysis. It does not compute factor of safety, model pore water pressure, or predict where new landslides will initiate. If someone needs that answer, they need a geotechnical engineer, a site investigation, and a budget.

**Unmapped deposits.** If a landslide was never mapped, it doesn't appear in our model. LiDAR-based mapping continues to reveal previously unknown deposits. The supplemental layer addresses the highest-profile gaps.

**InSAR blind spots.** C-band InSAR decorrelates in dense vegetation, on slopes facing toward/away from the satellite orbit, and where ground displacement exceeds about 1 meter per year. Some active deposits will be misclassified as dormant. NISAR L-band data (when available) will improve vegetation penetration.

**Climate change.** The model is calibrated against the recent historical record. Extreme precipitation frequency is increasing in California, which will increase both reactivation rates and first-time failure rates. This is a known, directional underestimate.

**Seismic triggering.** Earthquake-triggered reactivation of dormant deposits is not modeled as a separate term. A dormant deposit near the San Andreas Fault has higher reactivation probability than one far from any fault.

**Post-fire debris flow.** Montecito-type events (debris flows in recently burned terrain during intense rainfall) are not modeled. This is a planned future addition.


## Sources

- [Belair et al. 2025](https://doi.org/10.5066/P9FZUX6N), USGS Landslide Inventories v3
- [Handwerger et al. 2019](https://doi.org/10.1029/2019JF005035), JGR Earth Surface
- [Cruden & Varnes 1996](https://onlinepubs.trb.org/Onlinepubs/sr/sr247/sr247-003.pdf), TRB Special Report 247, pp.36-75
- [Mirus et al. 2024](https://doi.org/10.1029/2024AV001214), AGU Advances
- [Sadeghi et al. 2021](https://doi.org/10.1016/j.rse.2021.112306), Remote Sensing (InSAR velocity precision)
- [FEMA National Risk Index](https://hazards.fema.gov/nri/landslide) v1.20, December 2025
- [Cotton, Shires and Associates 2025, Seal Cove Geologic Study, Report E6195](https://www.smcgov.org/planning/sealcoveinfo)
- [Jibson 2005](https://pubs.usgs.gov/of/2005/1067/), USGS OFR 2005-1067
- [OPERA L3 DISP-S1 V1](https://doi.org/10.5067/SNWG/OPL3DISPS1-V1), ASF DAAC

Contains modified Copernicus Sentinel data (2016–2025), processed by NASA JPL OPERA project.
