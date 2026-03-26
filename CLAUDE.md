# CLAUDE.md

## Project: CAHazards — California Address Hazard Report

A web app that generates multi-hazard risk reports for California addresses using public GIS data and probabilistic models.

## Critical principle: Scientific defensibility

This tool produces numbers that people will use to make major financial decisions (buying a home, choosing insurance, planning retrofits). Every model parameter, threshold, and assumption must be:

1. **Traceable to a published source.** If a number appears in the model, there should be a citation. "I tuned this to look reasonable" is not acceptable. If no published source exists for a parameter, say so explicitly in a comment and explain the reasoning.

2. **Conservative in the right direction.** When uncertain, err toward overstating risk rather than understating it. A false sense of safety is worse than unnecessary caution. But don't be so conservative that the tool loses credibility — if every address shows 50% risk, no one will trust it.

3. **Honest about limitations.** If the model can't distinguish between two meaningfully different situations (e.g., a fog-belt coastal town vs a dry Sierra ridge, both classified "Very High" fire zone), that's a limitation to acknowledge and fix, not paper over with a fudge factor.


## Modeling standards

- **Don't invent attenuation curves or coefficients.** Use published models (BSSA14, HAZUS, Wells & Coppersmith, etc.) even if simplified. Document what was simplified and why.
- **Don't add fudge factors to make numbers "look right."** If the model disagrees with intuition, either the model is wrong (fix it) or the intuition is wrong (explain why). Hand-tuned constants are technical debt.
- **Structural damage thresholds matter.** We report probability of "major damage" (HAZUS extensive + complete states, >20% of replacement cost). Not cosmetic damage, not any damage. Be precise about what's being measured.

## Architecture

- `data/scripts/` — Python data pipeline (download, process, tile)
- `data/tiles/` — Spatial tiles for R2 (raster uint8 zones, uint16 elevation, float32 vs30, vector GeoJSON)
- `src/` — Cloudflare Worker (TypeScript)
- `src/model/hazards.ts` — All hazard probability models. This is the most sensitive code in the project.
- `frontend/` — Single-page app (vanilla JS)
- `reference/` — Manual hazard analyses for validation

## Development

```bash
source .venv/bin/activate          # Python for data scripts
npx wrangler dev --port 8787       # Local Worker + R2
python3 scripts/seed-local-r2.py   # Seed tiles into local R2
```
