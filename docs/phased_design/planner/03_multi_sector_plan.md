# Track C — Multi-sector build plan (MS-0 … MS-5) — DEFERRED / lower priority

*Condensed execution plan. Full analysis + per-PR detail live in
`docs/multi-sector/design_and_phased_plan.md` and `…/sector_dependence_and_challenges.md`. Lower
priority for now per the current roadmap; **gated on a multi-sector data upload (MS-0).** Educational
research; not financial advice.*

**Why deferred.** The binding constraint is **labelled samples per sector**, and the unlock is **data**
(more names/sectors + history), which only the user can provide. Tracks A (retry-success) and B
(option-mgmt) take priority and do not depend on this. When a multi-sector dataset lands, MS-0 opens.

**The grounding fact (from the analysis).** Today `sector` feeds only the *descriptive* pooling layer
(`pooling.py`) + envelope metadata. The estimator scope ladder (`hazard.py`), the cross-sectional
features (`cross_sectional.py`), and the classifier (`models.py`) key off `peer_group` + a single broad
proxy (QQQ) — **sector-blind**. Multi-sector work makes them sector-relative and sector-hierarchical.

## Phased plan (condensed)

| PR | Deliverable | Gate | Status |
|---|---|---|---|
| **MS-0** | **Data & taxonomy** — cached adjusted OHLCV for N sectors (~15–25+ names/sector), a `sector → ETF proxy` map, cohort taxonomy, point-in-time-membership decision, + a per-sector event-detector sanity report. | Per-sector episode counts sane; no detector blow-ups; provenance documented. **THE GATE — needs a user data upload.** | ☐ blocked on data |
| **MS-1** | **Sectorize the cross-section** (`cross_sectional.py`): per-sector proxy, within-sector breadth/dispersion/peer-rank, within-(sector×date) normalization; extend the leakage truncation test; re-run the Phase-7 ladder **per sector**. | Leakage test passes; sector-relative features add ≥ the Phase-7 lift in data-rich sectors. Capability-only. | ☐ |
| **MS-2** | **Sector in the estimator hierarchy** (`hazard.py`): `sector` column on the panel + sector rungs in `_HORIZON_SCOPE_LADDER` with shrink-to-parent (partial pooling). | Before/after per-sector base rates; single-sector run unchanged (back-compat); thin sectors shrink to parent. Output-changing ⇒ gated. | ☐ |
| **MS-3** | **Classifier sector effects + leave-one-sector-out** (`models.py` + `generalization.py`): sector fixed effects + within-sector-normalized features; leave-one-**sector**-out CV + per-sector metrics. | Per-sector **lift over the per-sector base rate**; leave-one-sector-out gap reported honestly. Capability-only. | ☐ |
| **MS-4** | **Per-sector calibration + gating + per-sector blend** (`calibration.py` + `blend_surface.py`): surface the overlay only where **that sector's** gate passes. | Default byte-identical; additive-only across the universe; per-sector gate. Output-changing ⇒ gated. | ☐ |
| **MS-5** | **Stretch**: hierarchical / mixed-effects model; temporal/macro **regime-rotation** features (the plausible 60d lever — a contemporaneous snapshot can't time rotation). | Only if it beats MS-3/MS-4 under leave-one-sector-out without calibration regression. | ☐ |

## Handling sector-dependence (the Q1 recipe, condensed)

In priority order: **(A)** normalize features within (sector × date) — cheapest, no new params; **(B)**
sector-relative cross-section (per-sector proxy, within-sector breadth/peer-rank); **(C)** a sector rung
in the scope ladder with shrinkage (partial pooling); **(D)** classifier sector fixed effects
(interactions only if episodes allow); **(E)** hierarchical/mixed-effects (stretch); **(F)** per-sector
calibration + gate. See `docs/multi-sector/sector_dependence_and_challenges.md` for the full treatment.

## Acceptance summary

Deferred until MS-0 data exists. When it does: do the **cheap, low-variance sectorization first**
(A/B/C/F), respect that **episodes-per-sector** is the ceiling, evaluate **leave-one-sector-out**, gate
**per sector** with honest abstention, and remember that **sector rotation is temporal** — cross-sectional
snapshots rank within a date but don't time rotation.
