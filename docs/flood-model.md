# Flood Risk Model

## What it does

Converts FEMA flood zone designations into 30-year structural damage probabilities.

## How it works

Each parcel is checked against [FEMA's National Flood Hazard Layer (NFHL)](https://www.fema.gov/flood-maps/national-flood-hazard-layer) vector data for all 58 California counties. If the parcel falls inside a flood zone polygon, the model assigns an annual damage probability based on the zone type:

| Zone | Description | Annual event rate | Damage factor | Annual P(damage) |
|------|-------------|-------------------|---------------|-----------------|
| V/VE | Coastal high hazard (wave action) | 1% | 60% | 0.6% |
| AE | 100-year with base flood elevation | 1% | 40% | 0.4% |
| A | 100-year (approximate) | 1% | 40% | 0.4% |
| D | Undetermined | 0.5% | 30% | 0.15% |
| X | 500-year or minimal flood hazard | 0.02% | 10% | 0.002% |

The event rates are published FEMA return periods. The damage factors represent the expected fraction of building replacement cost lost in a flood event of that zone's severity, drawing from FEMA flood loss studies.

The 30-year probability is compounded: P(30yr) = 1 - (1 - annual)^30.

## What it doesn't do

**No depth or velocity modeling.** A parcel 1 foot above the base flood elevation gets the same rate as one 6 feet below it. Flood depth-damage curves (USACE) could refine this, but require elevation certificates we don't have at scale.

**No pluvial (rainfall) flooding.** The model only captures riverine and coastal flooding mapped by FEMA. Urban flash flooding from overwhelmed storm drains is not modeled.

**No climate adjustment.** FEMA flood maps are based on historical hydrology. Sea level rise and increased precipitation intensity will expand flood zones beyond current NFHL boundaries.

**Incomplete mapping.** FEMA maps are updated on a rolling basis. Some areas have outdated or approximate studies. The NFHL is the best available national dataset but is not perfectly current everywhere.

## Sources

[FEMA National Flood Hazard Layer (NFHL)](https://www.fema.gov/flood-maps/national-flood-hazard-layer), all 58 California counties. Vector polygons with FLD_ZONE attribute. Tiled at 0.1 degrees for point-in-polygon lookup.
