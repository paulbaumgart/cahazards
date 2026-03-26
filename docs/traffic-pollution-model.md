# Traffic Pollution Model

## What it does

Identifies properties near high-traffic roads and flags the associated air pollution exposure risk. Traffic-related air pollution (TRAP) is the primary source of nitrogen dioxide (NO₂), ultrafine particles, and carbon monoxide in residential areas.

Living near a major road is associated with increased rates of asthma, cardiovascular disease, lung cancer, and adverse birth outcomes. The health gradient is steepest within 100–200 meters of high-volume roads.

## How it works

For each parcel, the model finds the nearest major road segment from Caltrans traffic count data and assigns a risk level based on distance and Annual Average Daily Traffic (AADT):

| Condition | Risk level | Reasoning |
|-----------|-----------|-----------|
| < 100m and AADT > 100,000 | Severe | Within the ultrafine particle plume of a freeway-class road; strongest health associations |
| < 200m and AADT > 20,000 | Elevated | Within the zone where NO₂ and PM₂.₅ remain significantly above background ([HEI Panel 2010](https://www.healtheffects.org/publication/traffic-related-air-pollution-critical-review-literature-emissions-exposure-and-health)) |
| All other | Low | Beyond the primary TRAP exposure gradient |

The model reports the nearest major road's distance and AADT (vehicles per day).

## What it doesn't do

**No dispersion modeling.** Pollutant concentrations depend on wind direction, terrain, sound walls, vegetation barriers, and road geometry (elevated vs. at-grade vs. depressed). Two homes at the same distance may have very different exposures.

**No pollutant concentration estimate.** The model flags proximity and traffic volume, not actual NO₂ or PM₂.₅ levels. For measured air quality, see [CalEnviroScreen](https://oehha.ca.gov/calenviroscreen/report/calenviroscreen-40) pollution indicators (also shown in this report).

**No truck fraction.** Diesel truck traffic produces disproportionate PM₂.₅ and black carbon. AADT counts all vehicles equally. A road with 50,000 AADT and 30% trucks is worse than one with 50,000 AADT and 5% trucks.

**No indoor exposure adjustment.** Actual exposure depends on home ventilation, air filtration, and time spent outdoors. Newer construction with good air sealing reduces indoor penetration of outdoor pollutants.

## Sources

- [Caltrans AADT traffic counts](https://dot.ca.gov/programs/traffic-operations/census) (Annual Average Daily Traffic by road segment)
- [HEI Panel on the Health Effects of Traffic-Related Air Pollution](https://www.healtheffects.org/publication/traffic-related-air-pollution-critical-review-literature-emissions-exposure-and-health) (2010) — critical review establishing the 100–300m health gradient
- [CalEnviroScreen 4.0](https://oehha.ca.gov/calenviroscreen/report/calenviroscreen-40) — Traffic indicator methodology
