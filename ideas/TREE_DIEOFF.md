# Tree Die-Off Climate Adjustment -- Patch to Base Fire Risk Model

## What This Adds

Mortality exposure features to the existing DINS structural damage model. These capture how nearby tree die-off affects structural outcomes during wildfire. The die-off model projects mortality forward under climate scenarios, making this the climate adjustment layer.

**Base model:**
```
annual_risk = FSim_BP x DINS_model(SSD, FAIR_share, year_built)
```

**With this patch:**
```
annual_risk = FSim_BP x DINS_model(SSD, FAIR_share, year_built, 
                                    mortality_exposure_features...)
```


## Architecture

Two models, connected by a raster.

**Model A: Die-off model.** Predicts per-pixel tree mortality probability on all forested pixels statewide. Trained on USFS Aerial Detection Survey data. Driven by climatic water deficit (CWD). Outputs a 30m probability raster for each climate scenario.

**Mortality exposure raster.** The die-off probability surface from Model A, precomputed statewide for each scenario (historical, SSP2-4.5, SSP5-8.5).

**Model B: DINS structural damage model.** Predicts P(major damage | fire arrival) for a structure. The mortality exposure raster is sampled at multiple scales and directions around each parcel to produce features. These features are added to the existing DINS feature set. Model B learns from data which scales and directions matter.


## Mortality Exposure Features

Precompute the die-off probability raster from Model A. For each parcel, sample descriptors from the raster at multiple scales. Generate many features. Let the DINS model determine which carry signal.

**Feature generation:**

```python
RADII = [100, 250, 500, 1000, 2000]

def compute_mortality_features(lat, lon, dieoff_raster, fire_wind_bearing):
    """
    Sample the die-off probability raster around a parcel at multiple
    scales and directions. Returns a flat dict of scalar features.
    """
    features = {}
    
    for r in RADII:
        pixels = get_pixels_in_radius(dieoff_raster, lat, lon, r)
        probs = [p.dieoff_prob for p in pixels]
        
        if not probs:
            features[f'mean_dieoff_{r}m'] = 0.0
            features[f'max_dieoff_{r}m'] = 0.0
            features[f'mean_dieoff_{r}m_upwind'] = 0.0
            features[f'mean_dieoff_{r}m_downwind'] = 0.0
            continue
        
        # Omnidirectional
        features[f'mean_dieoff_{r}m'] = mean(probs)
        features[f'max_dieoff_{r}m'] = max(probs)
        
        # Directional: split into upwind and downwind half-circles
        # relative to fire-weather wind bearing from nearest RAWS station
        upwind = [p.dieoff_prob for p in pixels 
                  if is_upwind(lat, lon, p, fire_wind_bearing)]
        downwind = [p.dieoff_prob for p in pixels 
                    if not is_upwind(lat, lon, p, fire_wind_bearing)]
        
        features[f'mean_dieoff_{r}m_upwind'] = mean(upwind) if upwind else 0.0
        features[f'mean_dieoff_{r}m_downwind'] = mean(downwind) if downwind else 0.0
    
    # Distance to nearest high-probability pixel
    features['dist_nearest_dieoff'] = nearest_pixel_distance(
        dieoff_raster, lat, lon, threshold=0.5, max_search=2000
    )
    
    # How much forest exists nearby (context for the other features)
    features['forest_fraction_1000m'] = forest_pixel_count_1000m / total_pixel_count_1000m
    
    return features  # 22 features total
```

**Feature summary (22 features):**

| Features per radius | Count | Description |
|-------------------|-------|-------------|
| `mean_dieoff_{r}m` | 5 | Mean die-off probability, all directions |
| `max_dieoff_{r}m` | 5 | Worst pixel die-off probability |
| `mean_dieoff_{r}m_upwind` | 5 | Mean die-off probability, upwind half-circle |
| `mean_dieoff_{r}m_downwind` | 5 | Mean die-off probability, downwind half-circle |
| `dist_nearest_dieoff` | 1 | Distance to nearest pixel with dieoff_prob > 0.5 |
| `forest_fraction_1000m` | 1 | Fraction of land within 1km that is forested |

Most will be redundant. Features at adjacent radii will be highly correlated. The upwind/downwind split may or may not matter. The DINS model's feature importances determine what survives. After training, prune features with near-zero importance and retrain with the reduced set for the production model.

**Fire-weather wind bearing:** Derived from the nearest RAWS station. Compute the mean wind direction during all historical Red Flag Warning days. This is a per-station constant, not a per-event variable. This simplification assumes the critical fire weather direction is consistent for a given area. In the Sierra foothills and Bay Area (Diablo/Mono winds) this holds well -- the wind bearing is predictable. In Southern California, Santa Ana corridors shift enough that a single bearing per RAWS station may miss the variance. If the upwind/downwind features survive pruning as significant predictors, revisit whether a distribution of bearings (e.g., top 2-3 wind directions weighted by frequency) improves performance in SoCal.

**For parcels with no forest within 2km:** All features are 0.0 and dist_nearest_dieoff is set to a sentinel (9999). The DINS model learns that these values mean no forest mortality exposure.


## Model A: Die-Off Model

### Core Variable: Climatic Water Deficit (CWD)

CWD = potential evapotranspiration - actual evapotranspiration. Measures how much more water plants want than they can get. In water-limited Sierra Nevada forests, tree mortality is "unambiguously best modeled by climatic water deficit" (Das et al. 2013). CWD integrates temperature, precipitation, soil moisture, and evaporative demand into a single variable that directly drives the mechanism chain:

drought stress --> bark beetle susceptibility --> mass mortality --> standing dead fuel --> worse structural outcomes in fire

### Training Data

**Labels:** USFS Aerial Detection Survey (ADS)
- Source: https://www.fs.usda.gov/detail/r5/forest-grasslandhealth/
- Annual aerial surveys of California forests, mid-1990s to present
- Multiple drought cycles available: 2007-2009, 2012-2016, 2020-2021
- Coverage: forested land only (national forests, timberland). No chaparral, grassland, or urban.
- **Label quality:** ADS surveys are aerial visual assessments with known inconsistencies: different observers, different flight paths, variable spatial coverage across years. The raw ADS polygons include severity classes (light, moderate, severe). If these are available, training on an ordinal or continuous target may yield more signal than a binary threshold. If using binary, the 25% basal area loss cutoff is a judgment call that compresses a continuous process. Worth testing sensitivity to this threshold (15%, 25%, 40%) during model development.

### Features

| Feature | Source | Resolution | Why |
|---------|--------|-----------|-----|
| CWD, 3-year cumulative (current + 2 prior years) | USGS Basin Characterization Model (historical), Cal-Adapt (projected) | 270m | Primary driver. Cumulative multi-year deficit is more predictive than single-year. |
| Stand density (canopy cover) | LANDFIRE CC | 30m | Denser stands = more competition for water = higher mortality |
| Species composition (EVT class) | LANDFIRE EVT | 30m | Ponderosa pine far more susceptible than white fir or cedar. Model learns species-specific CWD thresholds. |
| Tree size (canopy height proxy) | LANDFIRE CH | 30m | Larger trees amplify mortality in high-CWD conditions. Critical cross-scale interaction with bark beetles. |
| Elevation | USGS 3DEP DEM | 10m | Lower elevations are water-limited and more vulnerable |
| Aspect (southness) | Derived from DEM | 10m | South-facing slopes dry faster, higher mortality |
| Slope | Derived from DEM | 10m | Steeper slopes shed water faster |
| Soil water storage capacity | NRCS SSURGO | Variable | Shallow soils on granite = low water storage = earlier drought stress |
| Prior mortality events | ADS historical | Annual | Die-off recurs in the same susceptible locations |

### Key Interaction

CWD x tree size. Larger trees have higher mortality in hot/dry conditions but not in cool/wet conditions (Fettig et al. 2019, Young et al. 2017). Use gradient-boosted trees (XGBoost, LightGBM, or similar) to capture this non-linearity automatically.

### CWD Data Sources

**Historical (for training and current-conditions inference):**
- USGS Basin Characterization Model (BCM)
- https://www.usgs.gov/software/basin-characterization-model
- 270m resolution, monthly, all of California, 1896 to present
- CWD = PET - AET
- **Resolution mismatch:** BCM at 270m against LANDFIRE at 30m means a single CWD pixel covers ~17 acres. Two parcels 200m apart can have very different topographic moisture conditions (north-facing ravine vs. south-facing ridge) but receive the same CWD value. The DEM-derived features (aspect, slope, elevation) partially compensate -- the model learns CWD x topography interactions -- but spatially resolved CWD would be better. If BCM or another source releases a higher-resolution CWD product, it's a drop-in upgrade.

**Projected (for forward-looking inference):**
- Cal-Adapt
- https://cal-adapt.org/
- Downscaled CMIP6 projections for California
- SSP2-4.5 (moderate) and SSP5-8.5 (high)
- Monthly, out to 2100

### Training

```python
# For each ADS grid cell and survey year:
label = 1 if significant_mortality_event else 0

features = {
    'cwd_3yr':         sum of CWD for current + 2 prior years (from BCM),
    'canopy_cover':    LANDFIRE CC at cell,
    'evt_class':       LANDFIRE EVT categorical (species group),
    'canopy_height':   LANDFIRE CH at cell,
    'elevation':       DEM elevation,
    'aspect_south':    cos(aspect) transformed so south=1, north=0,
    'slope':           slope in degrees,
    'soil_awc':        SSURGO available water capacity,
    'prior_mortality': count of ADS events at same cell in prior 10 years
}

# Stratified train/test split by drought cycle to prevent temporal leakage
# Train on 2007-2009 + 2020-2021, test on 2012-2016 (the big one)
# If the model can predict the 129-million-tree drought without seeing it,
# it generalizes.

# Any gradient-boosted tree framework works (XGBoost, LightGBM, CatBoost, etc.)
model = GradientBoostedClassifier(objective='binary')
model.fit(X_train, y_train)
```

### Statewide Raster Output

Run Model A on every forested pixel (LANDFIRE EVT) statewide. Produce one raster per scenario:

```python
# Precompute once per scenario, cache as GeoTIFF
for scenario in ['historical', 'ssp245', 'ssp585']:
    for pixel in all_forested_pixels_statewide:
        cwd_3yr = get_cwd(pixel, target_year, scenario)
        pixel.dieoff_prob = model.predict_proba(pixel.features | {'cwd_3yr': cwd_3yr})
    
    save_raster(f'dieoff_{scenario}.tif', resolution=30m)
```

This raster is the input to the mortality exposure feature computation. Computing features for a parcel becomes a set of raster lookups and spatial aggregations -- no model inference at query time.


## Model B: DINS Integration

### Training Features for DINS

For each structure in the DINS dataset, compute the 22 mortality exposure features using *observed* ADS mortality in the 5 years preceding the fire:

```python
for structure in dins_dataset:
    fire_year = structure.fire_year
    
    # Build observed mortality raster from ADS data for [fire_year-5, fire_year-1]
    observed_mortality = rasterize_ads_mortality(fire_year - 5, fire_year - 1)
    
    # Get fire-weather wind bearing from nearest RAWS station
    fire_wind = get_raws_red_flag_wind_bearing(structure.lat, structure.lon)
    
    # Compute same 22 features used at inference time
    mort_features = compute_mortality_features(
        structure.lat, structure.lon, observed_mortality, fire_wind
    )
    
    structure.features.update(mort_features)
```

At training time: observed ADS mortality raster.
At inference time: predicted die-off raster from Model A.
Same feature definitions. The DINS model learns the relationship between nearby dead trees and structural outcomes without knowing whether the input is observed or predicted.

### Full DINS Model

```python
# 3 existing features + 22 mortality exposure features = 25 total
damage_prob = dins_model.predict_proba(
    SSD,                          # Microsoft Building Footprints
    FAIR_share,                   # CDI + FAIR Plan (normalized by housing units)
    year_built,                   # County assessor
    mean_dieoff_100m,             # \
    max_dieoff_100m,              #  |
    mean_dieoff_100m_upwind,      #  |
    mean_dieoff_100m_downwind,    #  |
    mean_dieoff_250m,             #  |
    max_dieoff_250m,              #  |
    mean_dieoff_250m_upwind,      #  |
    mean_dieoff_250m_downwind,    #  |
    mean_dieoff_500m,             #  |  Mortality exposure features
    max_dieoff_500m,              #  |  (from raster sampling)
    mean_dieoff_500m_upwind,      #  |
    mean_dieoff_500m_downwind,    #  |
    mean_dieoff_1000m,            #  |
    max_dieoff_1000m,             #  |
    mean_dieoff_1000m_upwind,     #  |
    mean_dieoff_1000m_downwind,   #  |
    mean_dieoff_2000m,            #  |
    max_dieoff_2000m,             #  |
    mean_dieoff_2000m_upwind,     #  |
    mean_dieoff_2000m_downwind,   #  |
    dist_nearest_dieoff,          #  |
    forest_fraction_1000m         # /
)

annual_risk = fsim_bp * damage_prob
```

### Feature Pruning

After training, examine feature importances. Expect heavy redundancy between adjacent radii and between mean/max at the same radius. Prune features with near-zero importance. Retrain with the reduced set.

Likely survivors (hypothesis, to be tested):
- One or two radii for omnidirectional mean (probably 250m and 1000m)
- The upwind/downwind split at one radius (if directional exposure matters)
- dist_nearest_dieoff (if threshold proximity matters independently of density)
- forest_fraction_1000m (context feature)

If the upwind/downwind features don't outperform omnidirectional, drop them and the RAWS dependency. Simpler is better if predictive power is equivalent.


## Full Pipeline

```
For a given parcel at (lat, lon), holding period, climate scenario:

1. Look up precomputed die-off raster for the selected scenario.

2. Compute 22 mortality exposure features by sampling the raster
   at multiple scales and directions around the parcel.

3. FSim_BP = sample burn probability raster at (lat, lon).

4. damage_prob = dins_model.predict(
     SSD, FAIR_share, year_built, mortality_exposure_features...
   )

5. annual_risk = FSim_BP x damage_prob

6. holding_period_risk = 1 - (1 - annual_risk) ^ holding_years

7. expected_loss = holding_period_risk x replacement_cost x 1.35 (demand surge)
```


## User-Facing Climate Toggle

Three settings. User selects one. Default is Projected.

| Setting | Label | CWD source | What it means |
|---------|-------|-----------|---------------|
| Historical | "Current conditions" | BCM historical average | Assumes climate conditions stay as they have been. |
| Projected | "Expected trajectory" (default) | Cal-Adapt SSP2-4.5 | Where current global policy is heading. ~25 ft/yr effective elevation shift in the Sierra. |
| Accelerated | "High emissions" | Cal-Adapt SSP5-8.5 | If emissions increase substantially. ~40 ft/yr effective elevation shift. |

Changing the toggle swaps which precomputed die-off raster is sampled. The 22 features are recomputed from the new raster. Everything downstream updates.

For parcels with no forest within 2km, the toggle has no effect. Report notes: "Climate adjustment not applicable -- no forested area near parcel."

For parcels near forest, the report shows:
- How much the climate adjustment changes annual risk and holding period risk
- Distance to nearest predicted die-off zone
- Dominant direction of mortality exposure


## Validation

### Die-Off Model (Model A)

**Primary test:** Train on 2007-2009 and 2020-2021 drought cycles. Predict 2012-2016. If the model trained without the 129-million-tree drought can predict where die-off occurred during it, it generalizes. Report AUC, spatial accuracy, and false positive rate.

**Secondary test:** Leave-one-year-out cross-validation across all ADS survey years. Report stability of feature importances and AUC across folds.

### DINS Model (Model B)

**Primary test:** Compare AUC of DINS model with and without mortality exposure features. The features should improve prediction. If they don't, tree die-off doesn't meaningfully affect structural outcomes beyond what SSD, FAIR share, and year built already capture.

**Feature importance analysis:** Which radii matter? Does the upwind/downwind split carry signal? Does max outperform mean at any radius? Use this to prune to the minimal feature set.

### End-to-End Sanity Checks

- Arnold (forested, Sierra) should show meaningful increase in risk from historical to projected scenario
- Montara (coastal scrub, no forest nearby) should show no change across climate toggles
- A cleared lot 200m from dense conifer forest should show non-zero mortality exposure features
- A cleared lot 3km from any forest should show zero mortality exposure features
- Parcels in ADS-documented high-mortality zones should have elevated mortality density at all scales
- Die-off probability on forested pixels should correlate with CWD gradient along an elevation transect
- **WUI edge behavior:** Spot-check parcels at the forest/development boundary where the 30m LANDFIRE grid classifies the parcel as developed but forest starts within 100m. These edge cases are where the model's predictions matter most and where raster resolution creates the sharpest artifacts. Verify that the multi-scale sampling picks up the nearby forest and that feature values change smoothly across the transition, not as a step function at the LANDFIRE classification boundary.


## Data Sources

| Dataset | URL | Format | Resolution |
|---------|-----|--------|-----------|
| USFS ADS mortality surveys | fs.usda.gov/detail/r5/forest-grasslandhealth/ | Shapefile | Variable polygons |
| USGS BCM (historical CWD) | usgs.gov/software/basin-characterization-model | GeoTIFF | 270m |
| Cal-Adapt (projected CWD) | cal-adapt.org | API / GeoTIFF | ~6km downscaled |
| LANDFIRE EVT, CC, CH | landfire.gov | GeoTIFF | 30m |
| USGS 3DEP DEM | nationalmap.gov | GeoTIFF | 10m |
| NRCS SSURGO (soils) | websoilsurvey.nrcs.usda.gov | Shapefile | Variable |
| RAWS fire weather | mesowest.utah.edu | Station data | Per-station |



## Independent Review
This is well-engineered. The two-model architecture is the right call. Separating "where will trees die" from "does dead trees near a structure change outcomes" lets you validate each piece independently and swap climate scenarios without retraining the DINS model.

A few things I'd push on:

**The CWD resolution problem is bigger than you're acknowledging.** 270m BCM pixels against 30m LANDFIRE means your primary driver variable has ~80x less spatial resolution than your vegetation data. You note this and say aspect/slope "partially compensate." I'd go further and say this is probably your single largest source of prediction error in Model A. Two parcels 100m apart on opposite sides of a ridge get identical CWD but wildly different actual water stress. The interaction terms help but they're learning a correction to a blurred input. If you can get your hands on TopoWx or PRISM at 800m and compute your own CWD from higher-res PET/AET, it's worth the effort.

**The ADS training data is weaker than it looks.** Aerial visual surveys with different observers, flight paths, and coverage year to year. You mention this but then proceed to train a binary classifier on it. The label noise is non-random. Survey effort correlates with accessibility, which correlates with proximity to roads, which correlates with elevation and forest type. You'll learn the survey bias along with the mortality signal. Consider weighting training samples by a survey effort proxy (distance to flight path, if that metadata exists) or at minimum checking whether your model's false positive rate varies systematically with remoteness.

**The temporal split is smart but insufficient.** Training on 2007-2009 + 2020-2021 and testing on 2012-2016 is good for testing generalization across drought events. But there's a subtler issue: the 2012-2016 drought killed trees that were weakened during 2007-2009. Your prior_mortality feature captures this, but only if the 2007-2009 ADS data is complete in the areas that later experienced massive die-off during 2012-2016. If ADS coverage was spotty in 2007-2009, prior_mortality will be systematically underestimated for the test set, and your model will learn to underweight it.

**The 5-year mortality window for DINS training is a judgment call that deserves sensitivity testing.** Dead trees fall over. A tree that died 5 years before the fire may have already lost its crown and collapsed, which actually reduces crown fire risk but increases surface fuel loading. A tree that died 1 year ago is still standing with dry needles, which is the worst case for fire behavior. The mechanism changes with time since death. Consider testing 1-year, 3-year, and 5-year windows separately.

**The upwind/downwind feature is clever but the RAWS simplification may kill it.** Using mean Red Flag wind direction as a per-station constant assumes fire spread direction is predictable from climatology. For Diablo and Santa Ana corridors that's true. For fires in complex terrain (Sierra canyons, coastal valleys) the local wind during the actual fire event can be completely different from the RAWS mean. If this feature survives pruning, great. My guess is it won't in most of California, and you'll end up with omnidirectional features only. That's fine. Don't fight for it.

**One thing that's missing: post-fire debris flow.** You have a tree die-off model that predicts where forests are stressed and likely to burn with high severity. High-severity burn areas are exactly where post-fire debris flows originate. This connects directly to your landslide model. A parcel downslope of a predicted high-severity burn zone has elevated landslide risk that neither model currently captures independently. Worth flagging as a future integration point even if you don't build it now.

**The user-facing climate toggle is the best part.** Three scenarios, one default, clear labels, swap a raster and recompute. No user-facing complexity. The "not applicable" message for parcels away from forest is a nice touch that builds trust.

Overall: this is ready to build. The architecture is sound, the validation plan is real, and the known limitations are documented. The ADS label quality and CWD resolution are the two things most likely to limit performance in practice.
