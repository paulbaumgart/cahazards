# Wildfire Risk Model

## What it does

For every parcel in California, this model produces a single number: the probability of major structural damage from wildfire within 30 years.

The model has two components, multiplied together:

**P(fire arrives at this location in 30 years)** — from the USFS FSim burn probability model, calibrated against 30 years of actual California fire history (1996–2025).

**P(structure destroyed | fire arrives)** — from an XGBoost model trained on the DINS post-fire inspection dataset, using structure separation distance, flame length, and insurance market data as features.


## P(fire arrives): calibrated FSim

The USDA Forest Service's FSim simulator ([Dillon et al. 2023](https://www.fs.usda.gov/rds/archive/catalog/RDS-2020-0016-2)) models fire ignition, spread, and behavior across the landscape using fuel maps, topography, and historical weather patterns. It produces an annual burn probability at every 30-meter pixel in the United States. The Wildfire Risk to Communities project ([Scott et al. 2024](https://www.fs.usda.gov/rds/archive/catalog/RDS-2020-0016-2)) publishes this data as a public raster.

FSim has excellent spatial ranking — in California validation (2020-2023), 66% of subsequently burned area fell in the top 20% of predicted burn probability. But FSim systematically underestimates absolute fire frequency.

We computed calibration factors by comparing FSim's predicted burn probability against observed fire frequency from [CalFire's historical perimeter database](https://frap.fire.ca.gov/) (1996-2025) on a 0.5-degree grid. Statewide median correction: 5.7x. The correction varies by region: 3.7x in the Bay Area/Sierra, 8.9x in Southern California. The calibration preserves FSim's spatial ranking while fixing the absolute scale.

**The non-burnable pixel problem.** FSim uses LANDFIRE fuel maps that classify developed areas as "non-burnable." A house in Paradise gets near-zero burn probability even though the Camp Fire destroyed the town. The WRC team partially addresses this by "oozing" burn probability into developed areas via focal-mean smoothing (1,530m total spread). We extend this: for pixels still reading below the tile's 25th percentile after calibration, we promote to the tile median — but only if the pixel is in a [CalFire High or Very High Fire Hazard Severity Zone](https://osfm.fire.ca.gov/what-we-do/community-wildfire-preparedness-and-mitigation/fire-hazard-severity-zones). This ensures fire-prone developed areas (where CalFire has independently assessed high hazard) get realistic burn probabilities, without inflating risk for small brush fires in urban settings.

The FHSZ gate is justified by our finding that reburn probability is spatially flat out to 12km from historical fire perimeters — fire recurrence is a landscape-level property, not a distance-dependent one. A developed pixel in a fire-prone landscape has the same expected fire frequency as the surrounding wildland.

**Known underestimate.** Despite these corrections, the model underestimates risk in developed WUI communities like Paradise (2.1%) and Malibu (3.2%). These represent a new fire regime — extreme-wind-driven urban conflagrations that penetrate deep into developed areas — that neither FSim nor the historical record adequately captures. The 2017 Tubbs Fire, 2018 Camp Fire, 2021 Marshall Fire, 2023 Lahaina Fire, and 2025 Palisades/Eaton fires all demonstrated fire behavior that exceeds what current models predict.


## P(destroyed | fire): DINS model

Given that fire arrives at a structure, what's the probability it's destroyed?

We trained an XGBoost model on the [Zamanialaei et al. 2025](https://doi.org/10.1038/s41467-025-63386-2) DINS dataset: 40,731 structures inspected after five California wildfires, with damage assessed by CAL FIRE inspectors. 68% were destroyed (>50% loss), 28% had no damage, and 4% were in between — the distribution is bimodal.

Features (with importance):
- **[FAIR Plan](https://www.cfpnet.com) share (0.784).** The fraction of homes in each zip code insured through California's insurer of last resort. When private insurers pull out, it signals that the market has assessed high wildfire risk in the community. FAIR share captures community-level vulnerability — building age, code compliance, defensible space norms — that pixel-level features miss.
- **Structure Separation Distance (0.137).** Median distance between building footprints, computed from [Microsoft Building Footprints](https://github.com/microsoft/USBuildingFootprints) v2. Closer spacing enables structure-to-structure fire spread (ember ignition of adjacent buildings).
- **Conditional Flame Length (0.079).** Mean flame length if fire occurs, from USFS WildEST/FlamMap (216 weather scenarios). Higher flames = more radiant heat exposure.

5-fold cross-validation AUC: 0.834.

We excluded CalFire Fire Hazard Severity Zones from the damage model despite their availability. In the DINS data, FHSZ acts as a proxy for code-required fire hardening — structures in "Very High" zones are more likely to have fire-resistant construction, which makes them appear *less* vulnerable. The FHSZ signal is real but it confounds hazard exposure with mitigation response. We use FHSZ on the fire-arrival side instead, where it provides independent terrain-level hazard assessment.


## What it doesn't do

**Climate projection.** The model is calibrated to 1996-2025 fire frequency. Fire activity in California has been accelerating: annual area burned in the Sierra Nevada doubled from 2010-2020 compared to 1984-2009 ([Williams et al. 2023](https://doi.org/10.1002/ecs2.4397)). The next 30 years will likely see more fire than the last 30. This is a directional underestimate.

**Urban conflagration modeling.** FSim simulates fire spread through wildland fuels, not through developed urban areas. The non-burnable pixel problem means structures deep inside WUI communities receive lower risk scores than they should. The WRC team and fire modeling community are working toward coupled wildland-urban fire spread models. Until those exist, our calibration and FHSZ promotion partially bridge the gap.

**Post-fire debris flow.** The model does not capture the compounding risk where wildfire increases subsequent landslide/debris flow hazard. Montecito (2018) demonstrated this: the Thomas Fire burned the hillsides, then intense rainfall triggered debris flows that killed 23 people. This interaction is a planned future addition.

**Structure-level hardening.** The model cannot account for individual building features (roof material, eave type, vent screens, defensible space) because we don't have parcel-level inspection data at scale. These features matter — the DINS data shows significant variation in destruction rates by construction type. FAIR Plan share is our best available proxy for community-level building quality.


## Sources

- [Scott et al. 2024](https://www.fs.usda.gov/rds/archive/catalog/RDS-2020-0016-2), USFS Wildfire Risk to Communities v2.0
- [Dillon et al. 2023](https://www.fs.usda.gov/rds/archive/catalog/RDS-2020-0016-2), FSim burn probability modeling
- [Zamanialaei et al. 2025](https://doi.org/10.1038/s41467-025-63386-2), Nature Communications 16:8041 (DINS dataset)
- [Williams et al. 2023](https://doi.org/10.1002/ecs2.4397), Ecosphere 14:e4397 (Sierra Nevada fire trends)
- [Tortorelli et al. 2024](https://doi.org/10.1002/eap.3023), Ecological Applications 34:e3023 (reburn dynamics)
- [CalFire FRAP](https://frap.fire.ca.gov/) historical fire perimeters (1996-2025)
- [CalFire Fire Hazard Severity Zones](https://osfm.fire.ca.gov/what-we-do/community-wildfire-preparedness-and-mitigation/fire-hazard-severity-zones) (SRA + LRA)
- [California FAIR Plan Association](https://www.cfpnet.com)
- [Microsoft USBuildingFootprints](https://github.com/microsoft/USBuildingFootprints) v2 (structure separation distance)
- [Caggiano et al. 2020](https://doi.org/10.3390/fire3040073) (850m WUI destruction distance)
- [Storey et al. 2020](https://doi.org/10.3390/fire3020010), Fire 3(2):10 (spotting distance distribution)
