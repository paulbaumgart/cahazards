# Aviation Lead Exposure Model

## What it does

Identifies properties near airports where piston-engine aircraft use leaded aviation fuel (100LL), and flags the associated lead exposure risk.

Leaded aviation gasoline (avgas 100LL) is the largest remaining source of lead emissions in the United States. The EPA [issued a final endangerment finding](https://www.epa.gov/regulations-emissions-vehicles-and-engines/regulations-lead-emissions-aircraft) in 2023, and the FAA is working toward an unleaded replacement, but as of 2026 most piston-engine aircraft still burn leaded fuel.

## How it works

For each parcel, the model finds the nearest airport with piston-engine operations (from FAA data) and assigns a risk level based on distance:

| Distance | Risk level | Reasoning |
|----------|-----------|-----------|
| < 0.3 miles | High | Within the typical lead deposition footprint documented in EPA and state health studies |
| 0.3-0.6 miles | Moderate | Elevated blood lead levels observed in children at this range |
| 0.6-1.2 miles | Elevated | At the outer edge of detectable exposure |
| > 1.2 miles | Low | Background levels |

The model reports the nearest airport name, distance, and annual piston operations. Higher piston ops mean more lead deposited.

## What it doesn't do

**No atmospheric dispersion modeling.** Lead deposition depends on wind patterns, runway orientation, and flight paths. Two homes equidistant from an airport may have very different exposures depending on which is downwind of the approach path.

**No blood lead prediction.** The model flags proximity, not dose. Actual exposure depends on time outdoors, soil contact, home ventilation, and other factors.

**No timeline for phase-out.** The FAA's transition to unleaded avgas is underway but incomplete. The risk level reflects current (2026) fuel usage patterns.

## Sources

- [FAA Terminal Area Forecast](https://www.faa.gov/data_research/aviation/taf) (piston operations by airport)
- [FAA airport locations](https://adip.faa.gov/agis/public/#/airportSearch/advanced) (AGIS database)
- [EPA endangerment finding](https://www.epa.gov/regulations-emissions-vehicles-and-engines/regulations-lead-emissions-aircraft) for lead emissions from piston-engine aircraft (2023)
