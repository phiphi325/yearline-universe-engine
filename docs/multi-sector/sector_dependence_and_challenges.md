# Sector dependence & the challenges of a multi-sector universe

*Analysis only — the design and the phased build are in `design_and_phased_plan.md`. Educational
research; not financial advice.*

---

## 0. Where `sector` lives in the engine today (the starting point)

Before answering "how do we handle sector dependence," it's worth being exact about what the engine
*currently* does with sector, because that defines the work.

| Layer | Module | Uses `sector`? | Keys off |
|---|---|---|---|
| Config | `config.py` | **yes** — `TickerConfig.sector` and `.peer_group` are both required; `UniverseConfig.sectors()` exists | — |
| Descriptive pooling / evidence | `pooling.py` | **yes** — produces `pooled_by_sector` (gap×drawdown matrix, Spearman, attempt-success per sector) | sector, peer_group, universe |
| Empirical retry estimator | `hazard.py` `_HORIZON_SCOPE_LADDER` | **no** | `ticker → peer_group ("group") → universe` + state buckets |
| Cross-sectional features | `cross_sectional.py` | **no** | one broad proxy (QQQ) + the **whole-equity** cross-section |
| Direct classifier | `models.py` / `labels.py` | **no** | pooled rows, one coefficient vector, no sector term |
| Calibration & trust gate | `calibration.py` | **no** | universe-wide (purged by transition) |
| Generalization CV | `generalization.py` | partial | leave-one-**ticker**-out (not sector) |

**Takeaway:** sector is rich in the *descriptive* layer and the metadata, but the three components
that actually generate the surfaced probability — the empirical estimator, the cross-sectional
features, and the classifier — are **sector-blind** (they lean on `peer_group` and a single broad
proxy). A single peer group ("mega_cap_software_like") *was* effectively one sector, so this was
fine. Pooling many sectors makes it actively wrong: a "breadth" number that mixes utilities and
biotech, or one logistic coefficient on `distance_to_ma250_pct` shared across all sectors, blends
signals that don't belong together.

---

## Q1 — If behaviour is sector-dependent, how do we address it?

"Sector-dependent" can mean three distinct things, and they need different fixes:

- **Different base rates** — a cyclical sector retouches its yearline within 40d more often than a
  defensive one (the label's prior `P(y_H=1)` differs by sector).
- **Different feature *scales*** — 30% realized vol is extreme for utilities, ordinary for biotech;
  a −10% gap means different things in different volatility regimes.
- **Different feature *relationships*** — the slope of "gap closing → sooner retouch" may genuinely
  differ by sector (the model's coefficients differ).

Six strategies, ordered by **value-per-overfit-risk** (the engine's small-sample discipline says
prefer the cheap, low-variance ones first):

### A. Normalize features *within (sector × date)* — the highest-value, lowest-risk lever
Most path features are only meaningful **relative to the sector's own distribution at that time**.
Replace (or augment) raw features with their **within-(sector, date) z-score or rank**: e.g.
`realized_vol_20d` → "how extreme is this name's vol *vs its sector today*." This dissolves the
"different scales" problem **without adding a single parameter**, so it can't overfit the way
sector×feature interactions can. The engine already computes cross-sectional median/dispersion in
`cross_sectional.py`; this extends that to *per-sector* and feeds the normalized values to the model.
→ **Do this first.**

### B. Make the cross-sectional features *sector-relative*
The Phase-7 cross-sectional block must be re-pointed from "whole universe" to "this name's sector":
- **peer-relative strength** vs the **sector** cross-section median (not the universe median);
- a **sector regime proxy** — the sector ETF (`XLK`, `XLF`, `XLE`, …) — alongside the broad-market
  proxy. The engine already has an `etf_context` peer group and per-ticker `sector`, so a
  `sector → ETF` map is the natural addition;
- **within-sector breadth** (fraction of the *sector* above its yearline) — a sector-rotation signal
  that a universe-wide breadth number washes out.

### C. Add `sector` as a hierarchy level in the empirical estimator (partial pooling)
This is the principled answer to **different base rates**. Add a `sector` column to the hazard panel
and insert sector rungs into `_HORIZON_SCOPE_LADDER`:

```
ticker → peer_group/industry → SECTOR → universe        (+ state buckets at each rung)
```

With the existing ≥25-row floor and Bayesian shrinkage to the parent rate, this is exactly **partial
pooling**: a data-rich sector uses its own retouch rate; a thin sector borrows from the universe and
is shrunk toward it. It is the same mechanism the estimator already uses (ticker → peer → universe) —
multi-sector just inserts the missing rung. No new model, no overfit cliff.

### D. Give the classifier sector **fixed effects** (and interactions only if earned)
A **per-sector intercept** (one-hot `sector` dummy in the logistic) lets each sector have its own
base log-odds — the cheap, robust way to absorb different base rates in the *learned* model. Add
**sector × feature interactions** (different slopes) **only where the episode count supports it** —
which, at first, it won't. So the conservative recipe is **within-sector-normalized features (A) +
sector intercepts (D)**, *not* a thicket of interactions. (Normalization (A) already captures much of
what interactions would, at zero parameter cost.)

### E. Hierarchical / mixed-effects model — the "right," heavier answer (stretch)
The statistically-correct formulation is a **mixed-effects logistic**: random intercepts (and maybe
random slopes on one or two key features) **per sector**, shrunk to a global mean — "let each sector
differ, but only as much as its data justifies." This is partial pooling for the *classifier* (C is
partial pooling for the *estimator*). It is a real lift in complexity (and fitting / validation cost),
so it is a later stretch; the **Phase-7 blend / shrinkage we already built is the pragmatic stand-in**
in the meantime.

### F. Calibrate and gate **per sector**
Discrimination *and* calibration differ by sector, so a single universe gate hides per-sector
failures. Run isotonic recalibration and the AUC≥0.60 / MACE≤0.10 / n≥50 **trust gate per sector**
(or per sector×cohort), and **abstain per sector** where the evidence is thin — the same
"honestly say not-yet" stance as the current gate, just stratified.

### Recommended package
**A + B + C + D + F first** (normalization, sector-relative cross-section, sector rung in the ladder,
sector intercepts, per-sector gate). **E** (hierarchical model) and temporal/macro regime features
are the later stretch. This keeps the binding constraint — *episodes per sector* — front and centre
and avoids buying variance the data can't support.

---

## Q2 — Other challenges of going multi-sector

### 1. Sample dilution / power per sector (the binding constraint)
~162 independent episodes today, across one peer group. Split across, say, 11 GICS sectors and many
of them have a handful of episodes — far too few to fit per-sector slopes or trust per-sector
metrics. **Everything else is downstream of this.** Mitigations: partial pooling (C/E), row-weighting
(already built), honest per-sector gating (F), and — most of all — **more data** (more names *and*
more history per sector).

### 2. Heterogeneous base rates → metrics aren't comparable across sectors
Retouch base rate already ranges 0.30–0.72 across horizons in one peer group; across sectors the
spread is wider. **Raw AUC is not comparable across sectors**, and a universe-pooled AUC can look
fine while a sector is useless. Report **lift over the per-sector base rate** (and per-sector
calibration), and evaluate **within sector**, not just pooled.

### 3. Market/sector proxy selection & collinearity
The current single proxy (QQQ) is a *tech* proxy — the wrong "market" for energy or financials.
Multi-sector needs **per-sector ETF proxies** plus a broad-market proxy. Watch **collinearity**: in
Phase 7, cross-sectional features barely helped partly because QQQ was near-collinear with the
mega-cap names themselves. Per-sector proxies reduce that, but a proxy that *is* basically the names
(a concentrated sector ETF) adds little.

### 4. Cross-sectional aggregation contamination (a correctness trap)
`breadth`, `dispersion`, and `peer-relative` computed over a **mixed** universe blend unrelated
sectors into one number — that's not a feature, it's noise. Enlarging the universe *without*
sectorizing the cross-section (Q1-B) would **silently degrade** the very features Phase 7 added.
This is the single most likely way to "go multi-sector and get worse."

### 5. Point-in-time sector membership (a subtle leakage/label issue)
Sectors get reclassified (GICS changes; a company pivots). Using **today's** sector label on
**historical** rows is a mild look-ahead and can mis-bucket episodes. Ideally use **point-in-time**
sector membership; if only current labels are available, document the caveat and prefer stable,
coarse sector buckets.

### 6. Survivorship & data alignment
Multi-sector pulls in names with different listing dates, history depths, and **delistings**. A
universe of only *current* constituents is **survivorship-biased** (the failures dropped out). Plus
corporate actions, index reconstitution, and keeping a much larger cache fresh. The repair/retry
study is especially sensitive because it's about *recovery* — excluding names that never recovered
biases the base rate up.

### 7. Event-definition robustness across sectors
The MA250 repair/retry canonical-event detector + censoring was validated on mega-cap software. A
structurally-declining name (perpetually below MA250) vs a choppy mean-reverter generate very
different episode structures; the detector should be **sanity-checked per sector** (episode counts,
censoring fraction, degenerate cases) — and the **no-hardcoded-ticker AST guard** must still hold.

### 8. Compute & cross-validation cost at scale
The empirical estimator's per-row scope-ladder and the **ticker-LOO empirical recompute** are
`O(rows × reference)`. At 100k+ rows this is the dominant cost; **compute-once models**, the
**incremental cache**, **`n_jobs` parallelism**, and the **per-file OOM-safe test runner** all become
load-bearing. Leave-one-sector-out / leave-one-ticker-out CV multiplies fits.

### 9. Regime & temporal confounding (sector rotation)
Sector performance is driven by **macro regime and rotation** — a *temporal* phenomenon. A
**contemporaneous** cross-sectional snapshot can rank names *within a date* but cannot time rotation
— exactly why Phase 7's cross-sectional features did **not** rescue the 60d horizon. Multi-sector
amplifies this. Honest framing: cross-sectional features improve *relative* discrimination; **macro /
temporal-regime features are a separate, harder lever** (and the most likely route to 60d).

### 10. Taxonomy & config design
Need a principled hierarchy: **sector → (industry / peer group) → size·liquidity cohort**, plus the
`sector → ETF proxy` map. Decide which rungs the estimator ladder and the classifier fixed effects
use. Config is mostly ready (`TickerConfig.sector` exists); additions are likely a `sector_etf`
mapping and optional cohort field — kept **schema-additive**.

### 11. Per-sector trust governance & surfacing
With many sectors, some pass the gate and some don't. The envelope/overlay must **communicate
per-sector trust honestly** — surface the blend only where *that sector's* gate passes, and never let
a single universe "passed" mask a failing sector. Same value-first / trust-last discipline, stratified.

### 12. Back-compatibility & change discipline
Keep all schema changes **additive**; preserve the **no-hardcoded-ticker** guard; treat any
output-changing step as **gated + before/after reviewed** (the Phase-3 / Phase-7-wiring pattern).
A multi-sector run must not alter a single-peer-group run's output unless explicitly enabled.

---

## One-paragraph synthesis

Going multi-sector is **mostly a data + relativity problem, not a modelling-horsepower problem.** The
engine already has the right *primitives* — a shrinkage scope ladder, cross-sectional features, a
calibrated gate, episode-aware CV, and a blend — but they currently key off `peer_group` and a single
broad proxy. The work is to **make them sector-relative and sector-hierarchical** (normalize within
sector, sector-relative cross-section, a sector rung in the ladder, sector intercepts, per-sector
gates), while respecting that the binding constraint is **episodes per sector** and that **sector
rotation is temporal** and won't be solved by a contemporaneous snapshot. Do the cheap, low-variance
sectorization first; treat the hierarchical model and temporal/macro features as later stretches.
