# V13 — Universe Statistical Context Engine
## Development Plan and Technical Specification

**Status:** V13.0 + V13.1 delivered; V13.2 batch runner delivered (stretch). V13.3+ in progress.
**Builds on:** Finalized V12 research notebook
**Primary goal:** Clean, ticker-agnostic, universe-first, sector-aware statistical context engine
**Target downstream integration:** `option-mgmt-2026` as optional evidence context
**Spec revision:** 2026-06-07 (annotated against the as-built `yearline_universe` package)

---

## 0. Build status — updated 2026-06-07

> This section is an addition to the original spec. It records where the
> implementation actually is. Everything below section 1 is the original
> specification, annotated in place with `> Status:` notes.

**Delivered this build (V13.0 + V13.1, plus V13.2 as a stretch):**

```text
✅ V13.0  clean architecture skeleton, config parser, dataclasses
✅ V13.1  generic single-ticker pipeline + per-ticker repo-ready envelope
✅ V13.2  universe batch runner: per-ticker failure isolation + run manifest + bundle (stretch)
✅ V13.3  Phases 1–7 DELIVERED: evidence + conditional timing + empirical-horizon hazard hardening + calibration/gating (V13.7) + pooled training & data freshness (9 tickers) + honest out-of-fold gate (P6) + discriminative classifier↔empirical blend overlay (P7). Pooling clears the trust gate at 10/20/40d (AUC 0.74–0.82); the P7 blend lifts AUC to 0.79–0.84 under leave-one-ticker-out and clears the gate at all four horizons
◐  V13.4  dashboard — cross-sectional TABLE only (interactive plots pending)
✅ V13.5  universe context bundle export (nests per-ticker envelopes; pooled_context now FILLED — evidence + conditional timing + pooled hazard)
☐  V13.6  historical universe replay (single-ticker replay exists; universe sweep pending)
◐  V13.7  calibration/gating DELIVERED (Phase 4, opt-in calibrate=True): horizon reliability + isotonic + purged LOTO + trust gate
✅ V13.9  discriminative overlay DELIVERED (Phase 7, opt-in surface_blend): path + cross-sectional features → direct horizon classifier ↔ empirical blend, gated + additive; empirical stays canonical
☐  V13.8  repo integration bundle (schema + dataclass proposal emitted; adapter pending)
```

**Package:** `yearline_universe` — 24 modules (incl. `timing.py`, `calibration.py`, and the
Phase 7 stack `features.py` / `cross_sectional.py` / `labels.py` / `models.py` /
`generalization.py` / `blend_surface.py`), notebook orchestrates only.

**Verification (real data, `as_of = 2026-06-05`):** the universe runs through the
identical `run_ticker_pipeline` code path and passes the structural sanity gate. On the
refreshed 9-ticker cache the mega-cap batch reports **6/6 OK** (was 3/6); the Phase 5
`mvp_software_like` universe runs **9/9**. `pytest` → **68/68 pass** (16 files, run per-file; the
heavy real-data tests can spike memory in one process), including an AST guard asserting
no hardcoded ticker literal exists anywhere in `src/`.

| ticker | canonical events | active engine | sanity |
|---|---|---|---|
| MSFT | 40 | repair_retry_hazard_engine (−10% below MA250) | PASS |
| AAPL | 25 | post_confirmation_trend_engine | PASS |
| NVDA | 24 | post_confirmation_trend_engine | PASS |
| AMZN | 40 | post_confirmation_trend_engine | PASS |
| GOOGL | 37 | unknown_or_transition | PASS |
| META | 27 | repair_retry_hazard_engine | PASS |

**Performance, default-path optimizations output-preserving (envelopes byte-identical):**
- Vectorized the daily replay (closed-form scoring of the linear hazard model): 56s → 5.5s/ticker.
- Gated an unused ML bootstrap (the timing/quality models the envelope never reads): ~16× vs the original on the pre-Phase-3 path.
- **Parallel runner** (`n_jobs`) + **incremental daily mode** (`incremental=True`, split/dividend-safe invalidation, ~1.4s/step).
- Phase 3's empirical-horizon estimator adds per-day reference lookups; `calibrate` and
  `pool_hazard` are heavier **opt-in** modes (they rescan / pool the panel). The default
  path stays fast. See `docs/V13_performance_optimization_report.md`.

**Deliverables:**
- `v13_universe_engine.zip` — the full repo.
- `notebooks/yearline_v13_universe_mvp.ipynb` — orchestration notebook.
- `docs/V13_user_guide.md` — install/config/run (single/batch/parallel/incremental/**pooled**/**calibrated**)/output/interpretation/troubleshooting + **deployment**.
- `docs/phased_design/` — the V13.3 roadmap, Phases 1–5, each wrapped with spec + results + artifacts.
- `docs/V13_performance_optimization_report.md`, `docs/V13_data_and_report_analysis.md`.
- `docs/tutorials/` — **5 tutorials** (performance · feature flags · empirical-estimator (P3) · calibration & gating (P4) · AUC/calibration for ML students) + `tutorials/README.md`.
- `Dockerfile` + `.dockerignore`; `scripts/run_universe_mvp.py` + `scripts/profile_pipeline.py`.

**Data note:** Live `yfinance` is rate-limited (HTTP 429) from the build sandbox's
shared egress IP. The reproducible demo runs on the user's `mvp_universe` yfinance
export — **9 tickers, fully split/dividend-adjusted, through 2026-06-05** — cached in
`data/price_cache/` (provenance: `docs/uploaded/mvp_universe_yfinance_exports_20260605.zip`;
prior caches backed up under `_backup_*`). The `data_loader` supports live providers
(`provider="auto"` → cache → yfinance → Yahoo chart API) for newer bars; every envelope
records its `source.data_as_of`.

---

## 1. Why V13 exists

V12 proved the methodology:

```text
MA250 / yearline repair-retry analysis
retry hazard / survival modeling
daily replay
dashboard reporting
post-confirmation trend state
semantic engine handoff
repo-ready context export
```

However, V12 remains fundamentally MSFT-centered. It was expanded into multi-ticker research, but its architecture was not designed from day one as a universe engine.

V13 should not be another patch on top of V12.

V13 should be a clean rebuild with this principle:

```text
MSFT is one ticker in a universe.
It is not the center of the system.
```

> Status: ACHIEVED. The detector, episode, hazard, replay, trend, semantic and
> export layers are all ticker-parametrized. A static AST test
> (`tests/test_no_hardcoded_ticker.py`) fails the build if any ticker symbol is
> hardcoded as a string literal in library code.

---

## 2. V13 mission

V13 should answer:

```text
For any configured universe of tickers,
what is each ticker's MA250 repair / trend state,
how does that state compare to its peer group / sector / universe,
and what repo-ready statistical context should be exported?
```

V13 must support:

```text
1. any ticker in the universe                  ✅ run_ticker_pipeline
2. multiple peer groups                         ✅ TickerConfig.peer_group
3. multiple sectors                             ✅ TickerConfig.sector
4. ticker-level context                         ✅ SingleTickerStatisticalContextEnvelope
5. peer-group pooled context                    ◐ basic (build_pooled_context, V13.3 to expand)
6. sector pooled context                        ◐ basic
7. universe-level context bundle                ✅ UniverseStatisticalContextBundle
8. daily run / replay mode                      ✅ per-ticker replay (universe sweep = V13.6)
9. repo-ready JSON export                       ✅ envelopes + bundle + JSON schema
```

---

## 3. V13 design principles

### 3.1 Ticker-agnostic
No hardcoded MSFT logic. Every function accepts `ticker: str` or a ticker config object.
> Status: ACHIEVED.

### 3.2 Universe-first
The top-level unit is `UniverseConfig`, not a single ticker.
> Status: ACHIEVED.

### 3.3 Sector-aware
Pooling happens at multiple levels: `ticker / peer_group / sector / universe`.
> Status: levels are first-class in config and `build_pooled_context(group_by=...)`;
> rich pooled statistics are the V13.3 expansion.

### 3.4 Pipeline-based
Avoid a single giant notebook. Use clear modules.
> Status: ACHIEVED — see the as-built module map in section 4.

### 3.5 Notebook as orchestration only
The notebook calls modules and displays outputs. Core logic lives in reusable Python files.
> Status: ACHIEVED — `notebooks/yearline_v13_universe_mvp.ipynb` contains no analysis logic.

### 3.6 Repo-ready from day one
V13 output directly supports `SingleTickerStatisticalContextEnvelope` and `UniverseStatisticalContextBundle`.
> Status: ACHIEVED — both are emitted with a stable, versioned schema.

### 3.7 No execution semantics
V13 remains a research / evidence engine. It does not emit trades.
> Status: ACHIEVED — the `option_overlay_research_hint` block is flagged
> `must_not_auto_execute: true` and carries no broker semantics.

---

## 4. Proposed V13 project structure

> Status: BUILT, with two additions vs. the original proposal:
> `ticker_pipeline.py` (the pipeline entry point) and `semantic.py` (the V12
> active-engine handoff, which had no home in the original module list). A
> `data/price_cache/` directory and a `scripts/` runner were also added.

As-built layout:

```text
v13_universe_engine/
  README.md
  pyproject.toml
  Dockerfile              # backend container image (deployment)
  .dockerignore
  .gitignore

  config/
    universe_mega_cap_ai_infra.yaml
    universe_sp500_sectors_sample.yaml

  notebooks/
    yearline_v13_universe_mvp.ipynb

  docs/
    V13_universe_statistical_context_engine_development_spec.md   # this file
    V13_user_guide.md                       # usage + deployment guide
    V13_performance_optimization_report.md  # profiling + optimization case study
    tutorials/
      README.md
      performance_optimization_tutorial.md  # teaching material (junior engineers)

  data/
    price_cache/            # 9 tickers (MSFT/AAPL/GOOGL/AMZN/META/NVDA + QQQ/XLK/IGV), adjusted OHLCV thru 2026-06-05

  scripts/
    run_universe_mvp.py     # end-to-end runner + export writer
    profile_pipeline.py     # cProfile + timing aid (used by the perf tutorial)

  src/
    yearline_universe/
      __init__.py
      config.py             # StudyConfig, TickerConfig, UniverseConfig, *PipelineResult, loader
      data_loader.py        # cache -> yfinance -> yahoo_chart providers
      indicators.py
      event_detection.py    # V10-parity detector + canonical events
      episodes.py           # episodes, recovery, mode features, live diagnostic
      hazard.py             # ML timing/quality + discrete-time survival hazard
      replay.py
      trend.py
      semantic.py           # NEW vs spec: active-engine handoff + field gating
      pooling.py
      context_export.py
      dashboard.py
      reporting.py          # validation.py also present
      validation.py
      ticker_pipeline.py    # NEW vs spec: run_ticker_pipeline + run_universe_pipeline

  tests/
    test_config.py  test_indicators.py  test_event_detection.py
    test_ticker_pipeline.py  test_context_export.py  test_pooling.py
    test_no_hardcoded_ticker.py

  exports/
    ticker_contexts/        # {TICKER}_statistical_context.json
    universe_contexts/      # {universe}_bundle.json, {universe}_run_manifest.json
    reports/                # JSON schema + ML/hazard leakage audits
```

---

## 5. Universe configuration

V13 uses YAML config. Example (`config/universe_mega_cap_ai_infra.yaml`):

```yaml
universe_name: mega_cap_ai_infra
benchmark: SPY
start: "2009-01-01"
replay_start: "2020-01-01"
as_of: null

rolling_windows:
  ma_fast: 200          # NOTE: V12-faithful (StudyConfig.ma_fast_len), not 50 — see deviations
  ma_yearline: 250
  atr: 14

tickers:
  - { ticker: MSFT, sector: Information Technology, industry: Software,      peer_group: mega_cap_software, role: cloud_ai_platform }
  - { ticker: AAPL, sector: Information Technology, industry: Hardware,      peer_group: mega_cap_hardware, role: consumer_hardware_platform }
  - { ticker: NVDA, sector: Information Technology, industry: Semiconductors, peer_group: ai_accelerator,    role: ai_compute_leader }
  - { ticker: AMZN, sector: Consumer Discretionary, industry: Internet Retail, peer_group: cloud_platform,  role: cloud_ai_platform }
  - { ticker: GOOGL, sector: Communication Services, industry: Interactive Media, peer_group: search_ads_ai, role: ai_platform }
  - { ticker: META, sector: Communication Services, industry: Interactive Media, peer_group: social_ai,      role: ai_platform }
```

> Status: BUILT. A second cross-sector config (`universe_sp500_sectors_sample.yaml`)
> exercises Financials / Energy / Health Care / Staples / Industrials.
> `rolling_windows.ma_fast` is intentionally **200**, not the spec's illustrative
> `50` — see section 8.9.

---

## 6. Core dataclasses

`UniverseConfig`, `TickerConfig`, `TickerPipelineResult`, `UniversePipelineResult`
are implemented in `config.py`.

> Status: BUILT. Deltas vs. the original proposal:
> - A `StudyConfig` (faithful port of V12 `YearlineStudyConfig`) carries the
>   detector / window parameters; `UniverseConfig` holds one and exposes
>   `study_for(ticker_config)` so per-ticker/sector overrides can be added later.
> - `TickerPipelineResult` is a **superset** of the spec contract: it adds
>   `status` / `error` (for batch failure isolation) and intermediate frames
>   (`source_attempts`, `recovery_table`, `mode_features`, `hazard_history`,
>   `trend_history`, `manifest`).
> - `UniversePipelineResult` adds a `run_manifest`.

```python
@dataclass(frozen=True)
class TickerConfig:
    ticker: str
    sector: str
    peer_group: str
    industry: str | None = None
    role: str | None = None
    weight: float | None = None

@dataclass(frozen=True)
class UniverseConfig:
    universe_name: str
    benchmark: str | None
    start: str
    replay_start: str
    tickers: tuple[TickerConfig, ...]
    as_of: str | None = None
    study: StudyConfig = ...          # as-built addition
```

---

## 7. Core pipeline functions

```python
load_universe_config(path) -> UniverseConfig                       # ✅ config.py
run_ticker_pipeline(ticker_config, universe_config) -> TickerPipelineResult   # ✅ ticker_pipeline.py
run_universe_pipeline(config) -> UniversePipelineResult            # ✅ ticker_pipeline.py
build_pooled_context(ticker_results, group_by) -> pd.DataFrame     # ◐ basic, pooling.py
export_single_ticker_context(result) -> dict                       # ✅ context_export.py
export_universe_context_bundle(result) -> dict                     # ✅ context_export.py
```

> Status: the second argument of `run_ticker_pipeline` is named
> `universe_config` (the spec wrote `global_config`); it also accepts optional
> `cache_dir`, `provider`, and `pooled_data` (the hook for V13.2 pooled hazard
> training). All other signatures match the spec.

---

## 8. V13 phase plan

### V13.0 — Architecture skeleton ✅ DELIVERED
Clean project structure, config parser, dataclasses, pipeline.
Acceptance: UniverseConfig loads from YAML ✅; TickerConfig validates required fields ✅; no MSFT hardcoding ✅ (AST-tested).

### V13.1 — Generic single-ticker pipeline ✅ DELIVERED
Any ticker runs through the full pipeline via the same function.
Acceptance: same function for all tickers ✅; no ticker-specific branching ✅; context schema stable ✅ (test_ticker_pipeline asserts identical top-level keys across MSFT/AAPL/NVDA).
> Note: per the agreed scope, V13.1 ships **full envelope parity** — hazard,
> trend and semantic layers are all populated, not just the foundation.

### V13.2 — Universe batch runner ✅ DELIVERED (stretch)
`run_universe_pipeline` runs all configured tickers.
Acceptance: all configured tickers run ✅; failures captured per ticker without killing the run ✅; manifest records status per ticker ✅ (`{universe}_run_manifest.json`).
> Update (2026-06-07): the runner is now **parallel** — `run_universe_pipeline(..., n_jobs=N)`
> runs tickers across processes (`n_jobs<=0` = all cores), output byte-identical to
> serial, with worker-process-level failure isolation. Measured ~1.6× on 2 cores
> for the demo; scales toward N× on larger/balanced universes.
> Open item for V13.2+: optional **pooled hazard/ML training** across the
> universe (the `pooled_data` hook exists; not yet wired in the batch runner).

### V13.3 — Peer-group / sector / universe pooling ✅ DELIVERED (Phases 1–5)
**Evidence layer DELIVERED (2026-06-07, Phase 1):** `build_pooled_evidence` produces,
at peer_group / sector / universe, the **gap×drawdown matrix** (drawdown_bucket ×
gap_bucket → counts, median gap/DD, next-attempt success + Wilson interval,
interpretation label), the **Spearman correlation** of inter-attempt drawdown vs
days-to-next-touch with bootstrap CI, and the **attempt-success classification** —
all surfaced in the universe bundle's `pooled_context`. On current cached data the
universe Spearman is **0.91** (n=55, CI [0.83, 0.94]), reproducing the V11.5 report's
~0.86. `build_pooled_context` still returns the lightweight per-group counts.

**Conditional timing DELIVERED (2026-06-07, Phase 2):** new module `timing.py`
ports the V11.5 §7 multi-estimator conditional days-to-next-touch — historical
median (by transition/group), gap×drawdown matrix interpolation, nearest-neighbor
(±2.5% drawdown band), and Theil-Sen robust fit — each with elapsed/remaining/rough
-date + quality flags and a consensus window. Surfaced as an **additive,
repair-regime-gated** per-ticker `retry_timing_context` (self-conditioned) plus a
universe-pooled view in `pooled_context.retry_timing`. Output-additive (existing
envelope fields byte-identical; 16→17 keys). At the report's 10.3% assumption the
conditional methods reproduce V11.5 §7 within 1–2 days (matrix-interp 15.9d vs 17.5d;
nearest-neighbor 34.5d vs 36d); unconditional methods run longer on the thin 3-ticker
pool (Phase 5 data-freshness caveat). See `phased_design/phase_02/`.

**Hazard hardening DELIVERED (2026-06-07, Phase 3):** ported the user's V12.4.1
**empirical-horizon policy** (benchmark notebook `…_PATCHED_P40_02.ipynb`). The
logistic hazard now supplies only `hazard_today`; the saturating state-hold-forward
curve is demoted to a labelled diagnostic; and the canonical P(retry≤H) is an
**empirical completed-path estimator** (similar historical states via a bucketed
scope ladder + Bayesian shrinkage), threaded through `hazard.py` / `replay.py` /
`context_export.py`. MSFT 2026-06-05: canonical P60 = 0.924 (was the 1.000 step),
P40 = 0.781 stable single==pooled (was 0.002/0.30/0.51), with transparent
`reference_scope`/`reference_n`. 41/41 tests. See `phased_design/phase_03/`.

**Calibration + gating DELIVERED (2026-06-07, Phase 4 / V13.7, opt-in `calibrate=True`):**
`calibration.py` ports the V12.6 harness scoring this empirical estimator + isotonic +
purged transition-aware (LOTO) splits + a per-horizon trust gate. On single-ticker MSFT
the gate correctly fails (AUC ≈ 0.46) ⇒ `surfaced_probability_is_calibrated=false`. See
`phased_design/phase_04/`.

**Pending:** pooled training + data freshness (Phase 5) — the step that lets the
empirical estimator discriminate (state-conditioned scopes qualify) so the Phase 4 gate
can pass.

### V13.4 — Universe dashboard MVP ◐ PARTIAL
`build_cross_sectional_dashboard` returns the full core table (ticker, sector,
peer_group, active_engine, mode_state, distance_to_ma250_pct,
required_rebound_to_ma250_pct, p_retry_within_40d_gated,
post_confirmation_trend_state, trend_quality_score, overextension_score,
deterioration_risk_score). **Pending:** interactive/plot rendering.

### V13.5 — Universe context bundle export ✅ DELIVERED
`export_universe_context_bundle` nests each ticker's envelope under
`ticker_contexts`; `pooled_context` blocks are placeholders until V13.3.

### V13.6 — Historical universe replay ☐ PLANNED
Per-ticker daily replay exists (`build_replay_history`). Pending: sweep the whole
universe and emit `universe_daily_state_history.csv`,
`ticker_daily_state_history.csv`, `sector_daily_state_history.csv`.

### V13.7 — Calibration by sector / peer group ◐ DELIVERED (phased **Phase 4**, opt-in)
**DELIVERED (2026-06-07, Phase 4):** `calibration.py` ports the V12.6 horizon-reliability
harness (which scores the Phase 3 **empirical** estimator), adds an **isotonic** transform
and **purged transition-aware** (leave-one-transition-out) splits, and fills
`calibration_context` + a per-horizon **trust gate** when run with `calibrate=True`
(opt-in, like `fit_ml_models`; default off keeps the hot path fast and the context
`available:false`). On single-ticker MSFT the gate **correctly fails** (AUC ≈ 0.46, no
discrimination on transition-only fallback) ⇒ `surfaced_probability_is_calibrated=false`;
clearing it needs the 8-ticker-style pool (Phase 5). See `phased_design/phase_04/`.
Below is the original (pre-Phase-4) note, retained for context:
Re-scoped 2026-06-07 around the uploaded **V12.6 calibration & walk-forward report**
(`docs/uploaded/yearline_v12_calibration_walkforward_report_v12_6.pdf`), which is the
baseline to meet/beat (hazard horizon calibration n=4227: 10d Brier 0.160 / AUC 0.802;
40d 0.190 / 0.745; MACE ≤0.072 ≤40d, 0.193 at 60d). Pending: port the horizon-reliability
+ walk-forward harness on the **fixed cell-59** dataset, add an **isotonic** transform
and **purged transition-aware** splits (V12.6 §5), fill `calibration_context`, and
**gate** the surfaced probability on passing. Sequenced **after** Phase 3 (hazard
hardening), which makes the live forward curve well-posed first. Details:
`docs/phased_design/phase_04/`.

### V13.8 — Repo integration bundle ☐ PLANNED
The JSON schema and a dataclass proposal are emitted
(`exports/reports/statistical_context_schema.json`). Pending: the validated
adapter + `SectorPooledContext` / `PeerGroupPooledContext` payloads for
`option-mgmt-2026`.

### 8.9 Implementation deviations and decisions (as-built)

> This subsection is an addition recording where the implementation
> deliberately diverges from, or sharpens, the original spec.

1. **`semantic.py` added.** The V12 active-engine handoff (repair/hazard ↔
   post-confirmation trend, with metric gating) had no module in the spec list.
   It is its own module; everything else maps onto the spec's names.
2. **`ma_fast` kept at 200, not 50.** `StudyConfig` defaults match V12 exactly so
   the V10 parity regression guard holds. The spec's `ma_fast: 50` is treated as
   illustrative; the shipped YAMLs use 200.
3. **Single-ticker hazard self-fit.** In V12 the hazard/ML models trained on the
   pooled universe. A standalone V13.1 run cannot pool, so each ticker fits on
   its own history (`training_scope = single_ticker_self_fit`) and the context is
   flagged as a low-sample prototype. With `class_weight="balanced"` on a
   rare-event daily panel the raw hazard is deliberately uncalibrated (V12
   parity); the semantic handoff gates it off for above-yearline tickers. The
   `pooled_data` argument on `run_hazard_layer` / `run_ticker_pipeline` is the
   forward hook for universe-pooled training (V13.2+).
4. **Validation generalized.** The MSFT V10 parity gate compared against values
   hardcoded for V12's frozen 2026 dataset, which cannot match a different data
   window. It is split into `validate_ticker_sanity` (dataset-independent
   structural guard, the real regression test) and `validate_reference_parity`
   (optional exact comparison only when a frozen reference is supplied). The
   anti-leakage audit tables are ported verbatim.
5. **Data provenance.** Live yfinance is IP-rate-limited from the build sandbox;
   the demo uses the user's `mvp_universe` yfinance export — 9 tickers, adjusted,
   through 2026-06-05 — in `data/price_cache/`. The loader remains live-capable via
   `provider="auto"`.
6. **Option overlay is non-executable.** Preserved verbatim from V12 but flagged
   `must_not_auto_execute: true` — consistent with principle 3.7.

---

## 9. Key differences between V12 and V13

| Area | V12 | V13 |
|---|---|---|
| Center | MSFT-centered | Universe-centered ✅ |
| Architecture | Long research notebook | Modular pipeline ✅ |
| Multi-ticker | Added later | Native design ✅ |
| Sector support | Limited | First-class ✅ |
| Context export | Single ticker | Ticker + universe bundle ✅ |
| Dashboard | Mostly MSFT / replay | Cross-sectional table ◐ (plots pending) |
| Maintainability | Research prototype | Engine-like system ✅ |

---

## 10. Recommended V13 MVP scope

> Status: COMPLETE. The first session implemented V13.0 + V13.1 (and folded in
> V13.2 because full envelope parity had already ported most of V12):

```text
✅ 1. clean architecture skeleton
✅ 2. UniverseConfig and TickerConfig
✅ 3. generic single-ticker pipeline wrapper
✅ 4. run MSFT / AAPL / NVDA with the same code path
✅ 5. export per-ticker context JSON
✅ (stretch) universe batch runner + manifest + bundle
```

The single-ticker pipeline is stable, so sector pooling (V13.3) is now unblocked.

---

## 11. Recommended next session

> Status: REVISED 2026-06-07 — replay performance is now DONE. Re-prioritized
> around the stated production goal: a **daily scan of many tickers across many
> sectors, deployed on a VPS**. Two tracks below; the recommended *immediate* step
> is marked ★.

```text
DONE since V13.1:
  A. Replay performance — vectorized, output-preserving, ~13x faster (56s -> 5.5s/
     ticker). See docs/V13_performance_optimization_report.md. (Residual cost: the
     retry-timing bootstrap on high-event tickers — item 4 below.)

Track 1 — SCALE & OPERATE  (unblocks the daily multi-ticker scan)
    1. Parallelize run_universe_pipeline — DONE (2026-06-07). n_jobs across processes
       (n_jobs<=0 = all cores); byte-identical to serial; worker-level failure
       isolation. ~1.6x on 2 cores (demo), scales toward N x on balanced universes.
    2. Incremental daily-update mode — DONE (2026-06-07). incremental=True + state_dir
       persists per-ticker replay history with split/dividend-safe fingerprint
       invalidation; a daily run appends only the new bar(s). Output identical to a
       full replay; ~2.1-2.5x on a benign daily step.
    4. Retry-timing bootstrap — DONE (2026-06-07) by elimination. The 300-fit Huber
       bootstrap (MSFT ~6-7s) produced timing/quality outputs the envelope never
       consumed; now gated off by default (fit_ml_models=False), output-preserving.
       MSFT 10s -> 3.6s; per-ticker time uniform; mean speedup vs baseline now 16.3x.
    3. Data-freshness — DONE (2026-06-07, Phase 5). The cache was refreshed from the
       user's mvp_universe yfinance export to **9 tickers through 2026-06-05** (NVDA
       refreshed, AMZN/GOOGL/META + ETFs added). A *nightly automated* refresh job
       (vs the current manual export) remains a production nice-to-have given the 429
       limits, but the data is now current.

Track 2 — DEEPEN THE ANALYTICS  (the "across sectors" payoff) — now run as the
value-first, trust-last PHASED roadmap (see docs/phased_design/):
    5. V13.3 pooling — DELIVERED. Phase 1: pooled gap×drawdown evidence (matrix +
       Spearman + attempt-success) at peer_group/sector/universe in pooled_context.
       Phase 2: conditional days-to-touch estimators (timing.py) surfaced as the
       additive retry_timing_context (per-ticker self + universe-pooled in the bundle).
    3'. Phase 3 — hazard HARDENING (port the V12.4.1 empirical-horizon policy) —
       DONE (2026-06-07). Logistic model kept for hazard_today only; saturating
       state-hold-forward curve demoted to a diagnostic; canonical P(retry<=H) is an
       empirical completed-path estimator (bucketed scope ladder + shrinkage), threaded
       through hazard/replay/envelope. MSFT P60 0.924 (was 1.0); P40 0.781 single==pooled
       (was 0.002/0.30/0.51). 41/41 tests. See phased_design/phase_03/.
    4'. Phase 4 — CALIBRATION + gating (V13.7) — DONE (2026-06-07, opt-in calibrate=True).
       calibration.py ports the V12.6 horizon-reliability harness scoring the empirical
       estimator + isotonic + purged transition-aware (LOTO) splits; fills
       calibration_context + a per-horizon trust gate. On single-ticker MSFT the gate
       correctly fails (AUC~0.46) ⇒ surfaced_probability_is_calibrated=false. 45/45 tests.
    5'. Phase 5 — pooled training + data freshness — DONE (2026-06-07). pool_hazard mode
       (run_universe_pipeline / --pool-hazard) threads pooled_data so hazard + empirical
       reference + calibration pool the universe; cache refreshed to 9 current tickers
       (NVDA refreshed, AMZN/GOOGL/META added) from the user's export. Pooling lifts the
       empirical AUC to 0.74–0.82 ⇒ the Phase 4 trust gate PASSES at 10/20/40d (60d
       marginal). Reproduces the V12.6 pooled metrics. See phased_design/phase_05/.

  ROADMAP COMPLETE: Phases 1–7 delivered (P6 honest gate; P7 direct classifier +
  cross-sectional + leave-one-ticker-out + gated blend overlay surface_blend). Natural
  follow-ons (not yet scheduled): per-sector/peer calibration; nightly data-refresh job;
  V13.4 dashboard plots; V13.6 universe replay sweep; V13.8 adapter. Forward-looking
  analysis docs on main: docs/multi-sector/ (widen the universe), docs/option-mgmt-integration/
  (feed option-mgmt-2026 + two-repo strategy), docs/research/01_retry_success_probability
  (make the retry-SUCCESS/quality probability trustworthy — currently a gated-off prototype;
  distinct from the mature retry-OCCURRENCE P(retry<=H)).
    6. Phase 5 — V13.2 pooled hazard/ML training via the existing pooled_data hook
       (training_scope -> pooled_universe; envelope schema unchanged) + a
       data-freshness step (Track 1 item 3) to bring the universe to current data.
    7. Phase 4 — V13.7 sector/peer CALIBRATION (port Module F walk-forward, reusing
       the fixed cell-59 dataset; gate any forward probability on it); V13.4 plots.

Recommended immediate step: Phase 3 (hazard hardening). With a credible, validated
days-to-touch surface now shipping (Phases 1–2), the forward hazard *probability*
can be demoted/gated while its root cause (the step artifact) is fixed, before any
probability is calibrated (Phase 4) or trusted. Track 1 item 3 (data-freshness) and
Phase 5 pooled training remain the production prerequisites and run as supporting work.

Constraints (unchanged): universe-first, sector-aware, notebook orchestrates only,
no execution semantics. Do not regress the V13.1 envelope schema or the
no-hardcoded-ticker guard.
```

---

## 12. Final recommendation

V12 stays frozen as the research proof and single-ticker context export.

V13 is now a working, tested, universe-first engine through V13.1 (+V13.2), and as
of 2026-06-07 it is also **performance-optimized** (~13× faster per ticker) and
**documented** (user guide, performance report, junior-engineer tutorial, Docker +
deployment guidance). The foundation is stable enough to build pooled evidence
(V13.3) and the `option-mgmt-2026` adapter (V13.8) on top without reworking the
per-ticker contract. The next milestone is operational scale for daily multi-ticker
scans (parallelism + incremental mode + a data-freshness step) — see section 11.

```text
V12 = research proof and single-ticker context export
V13 = production-style universe statistical context engine (V13.0–V13.2 live; V13.3+ next)
```
