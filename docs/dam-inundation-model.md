# Dam Inundation Risk Model

## What it does

Checks whether a parcel falls within a dam inundation zone and assigns a low-probability, high-consequence risk estimate.

## How it works

The [California Division of Safety of Dams (DSOD)](https://water.ca.gov/Programs/All-Programs/Division-of-Safety-of-Dams) publishes inundation maps showing areas that would be flooded if a dam failed. These are worst-case scenarios assuming complete, instantaneous dam failure.

If a parcel is inside any DSOD inundation zone:

P(damage, annual) = 0.01% × 90% = 0.009%

- **0.01% annual failure rate**: Approximate rate for well-maintained dams. Published dam failure rates vary widely (0.001% to 0.1% depending on dam type and age). We use the upper end of the range for modern regulated dams per CLAUDE.md's directive to err toward overstating risk.
- **90% damage factor**: Dam failure inundation is catastrophic. Structures in the flood path sustain near-total damage.

P(damage, 30yr) = 1 - (1 - 0.009%)^30 ≈ 0.27%

## What it doesn't do

**No depth or wave arrival modeling.** A parcel 1 mile from the dam and one 20 miles downstream get the same rate. In reality, flood depth, velocity, and warning time vary enormously along the inundation path.

**No dam condition assessment.** All dams are treated equally. A well-maintained modern concrete dam has far lower failure probability than an aging earthfill dam. DSOD inspection data is not incorporated.

**No cascade failure.** If one dam fails and overwhelms a downstream dam, the combined flood is not modeled.

## Sources

- [California Division of Safety of Dams (DSOD)](https://water.ca.gov/Programs/All-Programs/Division-of-Safety-of-Dams) inundation zone maps
