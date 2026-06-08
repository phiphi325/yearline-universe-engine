# Going multi-sector — analysis & plan

This folder works through what it takes to widen the engine from a single, software-like peer
group to a **multi-sector universe**, and how to do it without the model quietly degrading.
It is motivated by the Phase 7 finding that the remaining lever is *data breadth*, not more code —
but more breadth only pays off if the **sector structure** is handled deliberately.

> Educational research only. Not financial advice. The engine emits evidence context, never trades.

## The two questions this answers

1. **If a ticker's behaviour is sector-dependent, how do we address that** when we pool many sectors?
2. **What else gets harder** going multi-sector?

## TL;DR

- **Today, sector is barely wired into the model.** `TickerConfig` carries both `sector` and
  `peer_group`, but only the *descriptive* pooling/evidence layer (`pooling.py`,
  `pooled_by_sector`) and the envelope metadata use `sector`. The three things that actually
  produce the retry probability — the **empirical estimator's scope ladder** (`hazard.py`), the
  **cross-sectional features** (`cross_sectional.py`), and the **classifier** (`models.py`) — key off
  `peer_group` and a **single broad proxy (QQQ)** / a **whole-universe** cross-section. Enlarging the
  universe *without* sectorizing those three would blend unrelated sectors and degrade signal.
- **The fix is not "more parameters" — it's sector-relativity + partial pooling.** In priority order:
  (A) **normalize features within (sector × date)**, (B) make the cross-sectional features
  **sector-relative** (per-sector proxy + within-sector breadth + within-sector peer-rank),
  (C) add **sector as a hierarchy level** in the empirical estimator with **shrinkage to the parent**
  (partial pooling), (D) give the classifier **sector fixed effects** (and interactions only if the
  episode count allows), (F) **calibrate and gate per sector**. A full **hierarchical / mixed-effects
  model** (E) is the statistically-correct but heavier stretch.
- **The binding constraint stays the same: episodes, not rows.** ~162 episodes spread across more
  sectors means *fewer episodes per sector*, so partial pooling, row-weighting, and **honest
  per-sector gates** matter more than model complexity. And a **contemporaneous** cross-section still
  can't capture **sector rotation** (a temporal phenomenon) — the same reason cross-sectional
  features didn't rescue 60d in Phase 7.

## Reports in this folder

| Doc | What it covers |
|---|---|
| [sector_dependence_and_challenges.md](sector_dependence_and_challenges.md) | The analysis. **Q1** — six concrete strategies for sector-dependent behaviour, mapped to exactly where the engine changes today. **Q2** — twelve challenges of going multi-sector (sample dilution, base-rate comparability, proxy collinearity, cross-section contamination, point-in-time membership, survivorship, event-definition robustness, compute, sector rotation, taxonomy, per-sector gating, back-compat). |
| [design_and_phased_plan.md](design_and_phased_plan.md) | The plan. Concrete per-module changes, a **phased PR roadmap** (MS-0 data/taxonomy → MS-1 sectorized cross-section → MS-2 sector in the estimator hierarchy → MS-3 classifier sector effects + leave-one-sector-out → MS-4 per-sector calibration/gating → MS-5 hierarchical/temporal stretch), **acceptance gates**, **data requirements**, and **risks**. Value-first / trust-last, each PR independently reviewable. |

## How this connects to what's already built

- The **partial-pooling** idea is the same "borrow strength from similar history, shrink to the
  parent" used by the empirical estimator's scope ladder (`docs/tutorials/03_…`) — multi-sector just
  adds a **sector** rung to that ladder.
- The **per-sector blend + gate** is the Phase 7 classifier↔empirical blend (`phase_07/`,
  `docs/tutorials/06_…`) evaluated and gated **stratified by sector** instead of universe-wide.
- The **leave-one-sector-out** evaluation generalizes Phase 7's leave-one-ticker-out
  (`generalization.py`) one level up — the right "does it transfer to an unseen *sector*?" test.

Nothing here is built yet; this is the analysis + plan for review.
