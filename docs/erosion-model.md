# Coastal Erosion Risk Model

## What it does

Estimates the 30-year probability that coastal erosion will threaten a structure, based on USGS shoreline change data and distance from the coast.

## How it works

The model uses USGS coastal transect data ([Hapke & Plant 2010](https://doi.org/10.1016/j.margeo.2010.10.001)) that measures long-term shoreline change rates at points along the California coast. Each transect carries an erosion rate in meters per year (negative = retreating, positive = accreting).

For parcels within 2km of the coast:

1. **Find nearest transect** and extract the erosion rate.
2. **Compute years to threat**: distance to coast / erosion rate. A property 200m from a shoreline retreating at 0.5 m/yr has ~400 years at steady state.
3. **Apply two-component damage model**:
   - *Within 50m*: Imminent threat. Annual P scales with erosion rate (2%/yr baseline at 0.5 m/yr).
   - *50-200m*: Episodic bluff failure risk. Coastal bluffs can lose 5-20m in a single storm event, so the threat is not just steady-state retreat. Annual P based on rate and distance, following [Hapke & Plant 2010](https://doi.org/10.1016/j.margeo.2010.10.001) methodology.
   - *Beyond 200m*: Steady-state model. Risk diminishes with distance using a logistic function centered on years-to-threat.

The model reports both the 30-year damage probability and the estimated years until the erosion front reaches the property.

## What it doesn't do

**Episodic events are approximated.** Bluff collapses and storm-induced erosion can remove meters of coastline in a single event. The model captures this statistically but cannot predict specific events.

**Transect data gaps.** The USGS transect dataset does not cover every stretch of California coastline. Where transect data is missing or shows accretion, erosion risk is zero regardless of local conditions. Known limitation: Mirada Road (Moss Beach) shows 0% erosion due to USGS transects indicating accretion at that location.

**No sea level rise adjustment.** Rising sea levels will accelerate coastal erosion rates beyond the historical averages in the transect data.

**No armoring or seawall accounting.** Some eroding shorelines have been stabilized with riprap, seawalls, or beach nourishment. The model uses the historical unprotected erosion rate.

## Sources

- [Hapke & Plant 2010](https://doi.org/10.1016/j.margeo.2010.10.001), "Predicting coastal cliff erosion using a Bayesian probabilistic model", Marine Geology 278
- [USGS National Assessment of Shoreline Change](https://www.usgs.gov/tools/national-shoreline-change)
