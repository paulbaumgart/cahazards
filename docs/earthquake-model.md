# Earthquake Risk Model

## What it does

For every parcel in California, this model produces a single number: the probability of major structural damage from earthquake shaking and liquefaction within 30 years.

Both shaking and liquefaction damage are combined into one earthquake number. From a homeowner's perspective, "my house was damaged by the earthquake" regardless of whether the foundation cracked from shaking or the ground liquefied beneath it.


## How it works

**P(damage, 30yr) = ∫ P(PGA exceed) × P(damage | PGA) dPGA**

PGA (Peak Ground Acceleration) is the maximum horizontal acceleration of the ground during an earthquake, measured in fractions of g (gravitational acceleration). A PGA of 0.1g is strongly felt; 0.3g causes significant nonstructural damage; 0.6g+ causes major structural damage to older buildings.

We integrate over the full USGS hazard curve for the site, multiplying the probability of each ground-motion level by the probability of structural damage at that level.

### Seismic hazard: USGS NSHMP

The [USGS National Seismic Hazard Model](https://earthquake.usgs.gov/nshmp/) (2014 edition) provides hazard curves via a live API. For any site, the API returns the annual probability of exceeding 20 ground-motion levels (PGA from 0.005g to 4.0g), incorporating all sources: the UCERF3 fault model (all known California faults with their rupture rates, magnitudes, and correlations), background seismicity, and site amplification based on Vs30 (shear wave velocity in the top 30 meters).

We fetch the hazard curve at the site's actual Vs30. The NSHMP API supports specific Vs30 values (180, 259, 360, 537, 760, 1150, 2000 m/s); we use the nearest supported value. Vs30 is sampled from the [CGS Vs30](https://doi.org/10.1785/0120130309) raster.

This is not a fault-by-fault independence calculation. UCERF3 properly handles fault interactions, multi-segment ruptures, and time-dependent recurrence. The NSHMP curve integrates all of that.

### Structural fragility: HAZUS W1

We convert ground motion to structural damage using the [FEMA HAZUS](https://www.fema.gov/flood-maps/products-tools/hazus) fragility curve for W1 (light wood-frame residential construction), which represents the vast majority of California housing. The fragility curve gives P(damage state ≥ Extensive | PGA) — where "Extensive" corresponds to >20% replacement cost, our threshold for "major damage."

The integration sums over all 20 PGA levels: for each level, multiply the probability of exceeding that PGA by the probability of extensive/complete damage at that PGA.

### Liquefaction

Liquefaction damage is computed separately using the NSHMP hazard curve:

P(liquefaction damage) = P(PGA > 0.1g) × P(liquefaction | shaking) × P(damage | liquefaction)

Where P(liquefaction | shaking) depends on whether the site is in a CGS-designated liquefaction zone (binary), and P(damage | liquefaction) is a published damage factor.

The liquefaction and shaking damage probabilities are combined: P(earthquake damage) = 1 - (1 - P_shaking) × (1 - P_liquefaction).

### Seismic retrofit

The model supports a retrofit toggle that reduces shaking damage by 0.3x, per [FEMA P-807](https://atcouncil.org/files/FEMA%20P-807Indexed.pdf) guidelines for cripple wall bracing of older wood-frame homes. This does not affect liquefaction damage.


## What it doesn't do

**Building-specific vulnerability.** The HAZUS W1 fragility curve assumes a generic light wood-frame structure. Actual vulnerability varies with age, foundation type, soft-story conditions, and retrofit status. We offer a single retrofit toggle but don't model the full range of structural conditions.

**Non-structural damage.** The model focuses on structural damage (the building frame). Contents damage, business interruption, and displacement costs are not included.

**Aftershock sequences.** The NSHMP hazard curve represents mainshock hazard. Aftershock-triggered damage is not modeled separately.

**Tsunami from local earthquakes.** Tsunami is modeled as a separate hazard using CGS inundation zones. The correlation between earthquake shaking and tsunami arrival is not captured (they're treated as independent).


## Sources

- [USGS National Seismic Hazard Model 2014 (NSHMP)](https://earthquake.usgs.gov/nshmp/), live API
- [FEMA HAZUS](https://www.fema.gov/flood-maps/products-tools/hazus)-MH MR5, W1 wood-frame fragility curves
- [FEMA P-807](https://atcouncil.org/files/FEMA%20P-807Indexed.pdf), seismic retrofit effectiveness
- [CGS Vs30 raster](https://doi.org/10.1785/0120130309) (shear wave velocity)
- [CGS Seismic Hazard Zone maps](https://www.conservation.ca.gov/cgs/geohazards/eq-zapp) (liquefaction zones)
- [USGS Quaternary Fault and Fold Database](https://www.usgs.gov/natural-hazards/earthquake-hazards/faults) (displayed for context, not used in probability calculation)
- [Boore, Stewart, Seyhan & Atkinson 2014](https://doi.org/10.1193/070113EQS184M) (BSSA14) for fault MMI display
- [Worden et al. 2012](https://doi.org/10.1785/0120110156) (PGA to MMI conversion)
