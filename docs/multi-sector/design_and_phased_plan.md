# Multi-sector — design & phased plan

*The concrete engine changes and the build order. Pairs with `sector_dependence_and_challenges.md`
(the why). Value-first / trust-last; every step independently reviewable; schema additive;
no-hardcoded-ticker guard holds throughout. Educational research only.*

---

## 1. Per-module changes (what touches what)

| Module | Change | Output-changing? |
|---|---|---|
| `config.py` + a new `config/universe_multisector_*.yaml` | A multi-sector universe (sectors × names). Add an optional **`sector → ETF proxy` map** (and optional size/liquidity **cohort** field) — additive to `TickerConfig` / `UniverseConfig`. | no (config only) |
| `cross_sectional.py` | Sectorize: **per-sector regime proxy** (sector ETF) + broad-market proxy; **within-sector breadth & dispersion**; **peer-relative vs the sector** median; a **within-(sector × date) normalization** helper. Extend the leakage truncation test. | capability only (not surfaced) |
| `hazard.py` (`build_hazard_daily_panel`, `_HORIZON_SCOPE_LADDER`) | Carry a **`sector`** column on the panel; insert sector rungs into the scope ladder (`ticker → peer/industry → sector → universe`) with the existing ≥25-row floor + shrink-to-parent. | **yes** (estimator values change) — gated before/after |
| `labels.py` | Add `sector` to the modeling table; expose sector-normalized feature columns. | capability only |
| `models.py` | Optional **sector fixed effects** (one-hot) + **within-sector-normalized** features; keep L2 logistic primary; GBM diagnostic. | capability only |
| `generalization.py` | Add **leave-one-sector-out** CV (generalize the grouping arg); **per-sector** metrics + per-sector blend weights. | capability only |
| `calibration.py` | **Per-sector** isotonic + **per-sector trust gate** (AUC≥0.60 / MACE≤0.10 / n≥50), with honest per-sector abstention. | capability/measurement |
| `blend_surface.py` | **Per-sector** blend weight + gate; surface the overlay only where **that sector's** gate passes. | **yes** (opt-in `surface_blend`) — gated |
| `pooling.py` | Already sector-aware (`pooled_by_sector`); reuse as the descriptive baseline + per-sector base-rate reference. | no |

Guiding rule (unchanged from Phase 7): **capability before consumer.** Each modelling change lands as
a module + tests + a measurement first; nothing new is surfaced in the envelope until it has earned a
**per-sector** gate, and even then **opt-in and additive**.

## 2. Phased PR roadmap

| PR | Deliverable | Gate / acceptance |
|---|---|---|
| **MS-0 — data & taxonomy** | Assemble the multi-sector universe: cached adjusted OHLCV for ~N sectors (see §3), the `sector → ETF` map, the cohort taxonomy, a point-in-time-membership decision, and a **per-sector sanity report** for the canonical-event detector (episode counts, censoring %, degenerate cases). | Per-sector episode counts are sane; no detector blow-ups; no-hardcoded-ticker guard holds; data provenance documented. **This is the real unlock — mostly data + config.** |
| **MS-1 — sectorized cross-section** | `cross_sectional.py`: per-sector proxy, within-sector breadth/dispersion/peer-rank, within-(sector×date) normalization. Extend leakage test. Re-run the Phase-7 **ladder per sector**. | Whole-panel truncation leakage test passes; sector-relative features add ≥ the Phase-7 lift **within** at least the data-rich sectors (capability, not surfaced). |
| **MS-2 — sector in the estimator hierarchy** | `hazard.py`: `sector` on the panel + sector rungs in the scope ladder with shrink-to-parent. | **Before/after** per-sector base rates + scopes; the universe-pooled result is unchanged when there is only one sector (back-compat); thin sectors visibly shrink toward the parent. |
| **MS-3 — classifier sector effects + leave-one-sector-out** | `models.py` sector fixed effects + normalized features; `generalization.py` **leave-one-sector-out** CV + per-sector metrics. | Per-sector **lift over the per-sector base rate** reported; **leave-one-sector-out** generalization gap measured and reported honestly (expect it to be larger than leave-one-ticker-out). |
| **MS-4 — per-sector calibration & gating + per-sector blend** | `calibration.py` per-sector isotonic + gate; `blend_surface.py` per-sector blend weight + gate; surface overlay only where the sector gate passes. **Opt-in, additive, gated.** | Default output byte-identical; per-sector blocks appear only for gate-passing sectors; additive-only verification across the universe (the Phase-7 check, stratified). |
| **MS-5 — stretch** | Hierarchical / mixed-effects logistic (random per-sector intercepts/slopes shrunk to a global mean); **temporal / macro regime** features for sector rotation (the 60d lever). | Treated as research; promote only if it beats MS-3/MS-4 under leave-one-sector-out without per-sector calibration regression. |

Recommended order is exactly MS-0 → MS-4 (then MS-5 if warranted): **get the data and the
relativity right before adding model machinery.** MS-0 and MS-1 carry most of the value at the least
risk; MS-2 and MS-4 are the output-changing, gated steps.

## 3. Data requirements (MS-0 is the gate on everything)

- **Breadth per sector:** enough equities per sector that **within-sector breadth and peer-rank are
  meaningful** and there are enough **episodes** to (partially) pool — target on the order of
  ~15–25+ names per sector, more for thin/defensive sectors.
- **Sectors:** a principled set (e.g. the GICS 11) with a **`sector → ETF proxy`** map — e.g.
  `XLK` tech, `XLF` financials, `XLE` energy, `XLV` health care, `XLY`/`XLP` discretionary/staples,
  `XLI` industrials, `XLB` materials, `XLU` utilities, `XLRE` real estate, `XLC` comms — plus a
  broad-market proxy (`SPY`/`QQQ`).
- **History depth:** long enough per name to fill MA250 + multiple repair episodes; mind unequal
  history across names.
- **Format:** same as the current cache — fully split/dividend-adjusted daily OHLCV
  (`Date,Open,High,Low,Close,Volume`, `auto_adjust` basis), one CSV per ticker under
  `data/price_cache/`, current to a shared `as_of`.
- **Survivorship:** ideally include **delisted / failed** names (or document the survivorship caveat);
  the recovery study is biased upward if only winners are present.
- **Point-in-time membership:** ideally a date-stamped sector map; otherwise use stable coarse sector
  labels and document the look-ahead caveat.

(The data arrives the way the Phase-5 cache did — a user-provided export saved under
`docs/uploaded/` and decoded into `data/price_cache/`. This is the dependency MS-0 blocks on.)

## 4. Acceptance & guardrails (every PR)

- **Schema additive / stable**; **no-hardcoded-ticker** AST guard holds; tests stay green
  (`scripts/run_tests.sh`, per-file).
- **Capability before consumer**: measure first; surface only behind a **per-sector** gate, opt-in.
- **Output-changing steps (MS-2, MS-4) are gated** with explicit **before/after** and a
  **byte-identity-when-off** check (the Phase-7 discipline).
- **Honest metrics**: per-sector base rate, lift, calibration, and **leave-one-sector-out**
  generalization — never a single universe number that hides a failing sector.
- **Evidence/research only**; disclaimers + `must_not_auto_execute` preserved.

## 5. Risks & honest expectations

- **Per-sector sample is the ceiling.** Until the data is deep per sector, expect partial pooling to
  lean heavily on parent levels and several sectors to **fail their gate** (correctly). That is a
  feature, not a bug — it's the trust gate doing its job.
- **Cross-sectional features help ranking, not regime timing.** Sector **rotation** is temporal; the
  contemporaneous snapshot won't fix 60d (consistent with Phase 7). Real 60d / rotation gains need
  **MS-5 temporal/macro features**, which are harder and may not clear the gate.
- **Collinearity / proxy quality** varies by sector; a concentrated sector ETF that *is* basically
  its names adds little. Measure the per-sector lift; don't assume it.
- **Compute** grows with universe size; rely on compute-once models, the incremental cache, `n_jobs`,
  and the OOM-safe runner.

## 6. What success looks like

A multi-sector run where: each sector's retry probability borrows strength sensibly (own rate where
rich, parent where thin); the cross-sectional features are computed **within** sector and add
measurable per-sector AUC; the classifier carries per-sector base rates via fixed effects; and the
**per-sector trust gate** surfaces the blend overlay **only** where that sector has earned it — with
leave-one-sector-out evidence that the model transfers to a sector it hasn't trained on. The honest
1-liner: *broader coverage, the same trust discipline — applied one sector at a time.*
