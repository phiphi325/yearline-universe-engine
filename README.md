# V13 — Universe Statistical Context Engine

`yearline_universe` is a clean, **ticker-agnostic, universe-first, sector-aware**
rebuild of the V12 MSFT-centered research notebook. It computes, for any configured
universe of tickers, each ticker's **MA250 / yearline** repair-and-trend state and
exports **repo-ready statistical context** for downstream consumers
(e.g. `option-mgmt-2026`) as an *evidence overlay*.

> Educational research only. Not financial advice. This engine emits **evidence
> context, never trades** — there are no broker/execution semantics anywhere.

```text
MSFT is one ticker in a universe. It is not the center of the system.
```

## What this is (and isn't)

| | |
|---|---|
| **Is** | A modular pipeline that turns daily OHLCV into a versioned statistical-context envelope per ticker, plus a universe bundle. |
| **Isn't** | A trading system. The `option_overlay_research_hint` block is flagged `must_not_auto_execute: true`. |

V12 is the frozen research proof / reference implementation
(`docs/uploaded/…_PATCHED_P40_02.ipynb`). V13 is the production-style engine: the math
is ported faithfully from V12, but every module is ticker-parametrized — no hardcoded
MSFT logic (an AST test enforces it).

## Status — Phases 1–8 delivered; Phase 11 (trend outlook) in progress

The forward "days-to-touch" probability is now a credible, **calibrated, trust-gated**
quantity at ≤40 days on current, pooled data; Phase 7 adds a learned **discriminative
overlay**; Phase 8 makes retry **success** trustworthy; Phase 11 is upgrading the
post-confirmation **trend** engine:

| Phase | Delivered | Headline |
|---|---|---|
| **1 — evidence** | pooled gap×drawdown matrix + Spearman + attempt-success | universe Spearman ≈ 0.86 (drawdown↔time) |
| **2 — conditional timing** | `retry_timing_context`: median / matrix-interp / nearest-neighbor / Theil-Sen | a "days-left" *range*, not a point |
| **3 — hazard hardening** | empirical completed-path `P(retry ≤ H)` replaces the saturating model curve | P60 no longer pinned at 1.0 |
| **4 — calibration + gate (V13.7)** | reliability/Brier/AUC/MACE + isotonic + purged splits + a per-horizon **trust gate** | the gate honestly *refuses* a non-discriminating probability |
| **5 — pooling + data freshness** | `pool_hazard` over the universe; cache refreshed to 9 current tickers | **gate PASSES at 10/20/40d** (AUC 0.74–0.82, MACE 0.04–0.08) |
| **6 — honest gate** | gate on an out-of-fold (purged) isotonic MACE, not the in-sample-optimistic value | tightens 40d; 60d shown sample/regime-limited (honest abstention) |
| **7 — discrimination** | path + cross-sectional features → a direct horizon **classifier**, blended with the empirical estimator; **opt-in additive gated overlay** (`surface_blend`) | unseen-name (leave-one-ticker-out) **blend AUC 0.79–0.84**, gate passes at all 4 horizons |
| **8 — retry-success (RS-1…RS-4)** | success labels + empirical baseline → classifier → calibrate+gate+blend → **opt-in gated `retry_success_context`** (`surface_success`) + the `P(reclaim≤H)=P(retry≤H)×P(success│retry)` composite | leave-one-ticker-out **blend AUC 0.70 / MACE 0.036** (gate passes); a reliability diagnostic shows ~87% of the calibration is base-rate shrinkage |
| **11 — trend outlook (TO-0/TO-1)** | trend-engine handoff coverage + **de-saturated / de-collinearized** trend scores (efficiency ratio, ADX, R², variance ratio) | `trend_quality` spreads **0.38–0.76** (was ≈1.0 saturated); orphaned names now route to the trend engine; TO-2…TO-4 remain |

See `docs/phased_design/` for each phase's spec, results, and artifacts; `docs/phased_design/planner/`
for the forward roadmap (Tracks A–D).

## Architecture

The notebook orchestrates; all logic lives in `src/yearline_universe/`:

```text
config.py            UniverseConfig / TickerConfig / StudyConfig + YAML loader
data_loader.py       price providers: cache -> yfinance -> yahoo_chart
indicators.py        MA250/MA200, ATR, HV30, distance-to-yearline, gap state
event_detection.py   V10-parity strict/loose detector + canonical events
episodes.py          episode / recovery tables, mode-transition scoring, live diagnostic
hazard.py            discrete-time survival hazard (hazard_today) + EMPIRICAL horizon estimator (P3) + optional ML timing/quality
timing.py            conditional days-to-next-touch estimators: median / matrix-interp / NN / Theil-Sen (P2)
replay.py            daily replay / monitoring backfill (vectorized; incremental cache)
trend.py             post-confirmation trend engine (active above MA250)
semantic.py          active-engine handoff (repair/hazard <-> trend), field gating
pooling.py           peer/sector/universe pooling: gap×drawdown evidence + Spearman + attempt-success (P1)
calibration.py       horizon reliability + Brier/AUC/MACE + isotonic + purged LOTO + trust gate (P4 / V13.7)
features.py          path-dynamic repair features, leakage-safe (P7-A)
cross_sectional.py   peer/sector/market regime features, leakage-safe (P7-D)
labels.py            direct horizon labels y_H + the modeling table (P7-B)
models.py            direct horizon classifier (logistic; GBM diagnostic) + episode-aware head-to-head (P7-C)
generalization.py    leave-one-ticker-out CV + row weighting + classifier<->empirical blend (P7-E)
blend_surface.py     compute-once blend model + live gated overlay, opt-in surface_blend (P7 consumer wiring)
context_export.py    SingleTickerStatisticalContextEnvelope + UniverseStatisticalContextBundle
validation.py        anti-leakage audit + structural sanity gate + optional reference parity
dashboard.py         cross-sectional universe table  (V13.4 — table now)
reporting.py         markdown/PDF reports            (skeleton)
ticker_pipeline.py   run_ticker_pipeline + run_universe_pipeline (+ pooled hazard, P5)
```

**Two engines, one handoff.** (1) a **repair / retry / hazard engine** active while
price is below / testing the yearline; (2) a **post-confirmation trend engine** active
after acceptance. `semantic.py` decides which is active each day and **gates** the
inactive engine's metrics to `NaN`.

**The retry probability (P3–P5).** `hazard_today` is the logistic one-day hazard; the
canonical `P(retry ≤ H)` is an **empirical completed-path** estimate (how often
*similar* historical states retouched within H days, via a bucket scope-ladder +
Bayesian shrinkage). The old saturating "state-hold-forward" model curve is retained
only as a labelled diagnostic. With `calibrate=True` the engine fills
`calibration_context` and a per-horizon **trust gate**; with `pool_hazard=True` the
estimate is computed on the pooled universe (which is what makes it pass the gate).

**Occurrence vs success — two different probabilities.** `P(retry ≤ H)` above is retry
**occurrence** (*will it retouch the yearline within H days?*). The distinct question of retry
**success** (*given an attempt, will it reclaim and **hold** above the line?*) is answered only by a
**prototype** quality classifier (`fit_retry_quality_classifier`, opt-in `fit_ml_models=True`,
uncalibrated, **not surfaced** in the envelope). Making that success probability trustworthy is a
research track — see `docs/research/01_retry_success_probability_2026-06-08.md`.

**The discriminative overlay (P7).** With `surface_blend=True` (opt-in, pooled-only) the
envelope gains an **additive**, gated `direct_classifier_blend` block: a direct horizon
classifier (path + cross-sectional features) **blended** per-horizon with the empirical
estimate. The empirical number stays **canonical** — the blend is a labelled overlay that
*ranks* better (it tempers near-term probabilities for low-readiness repairs). Default off ⇒
the envelope is byte-identical. See `docs/phased_design/phase_07/` and tutorials 06–07.

## Install

```bash
pip install -e .                    # core (cache-only runs)
pip install -e ".[live,viz,dev]"    # + live data, matplotlib, pytest
```

Core runtime: numpy, pandas, scipy, scikit-learn, pyyaml. Live data
(`yfinance` / Yahoo chart API) is optional — the engine reads a local cache by default.

## Quick start

```python
from yearline_universe import load_universe_config, run_ticker_pipeline, export_single_ticker_context

uni = load_universe_config("config/universe_mvp_software_like.yaml")   # 9 tickers, current to 2026-06-05

# Any ticker runs through the identical code path — no ticker-specific branching.
res = run_ticker_pipeline(uni.get_ticker("MSFT"), uni, cache_dir="data/price_cache")
envelope = export_single_ticker_context(res)   # repo-ready JSON dict
```

Whole universe, with **pooled** hazard/reference/calibration (the trustworthy mode):

```python
from yearline_universe import run_universe_pipeline
result = run_universe_pipeline(uni, cache_dir="data/price_cache",
                               pool_hazard=True, calibrate=True)   # both opt-in
print(result.run_manifest["n_ok"], "/", result.run_manifest["n_tickers"])
```

Or the MVP end-to-end (writes all exports):

```bash
python scripts/run_universe_mvp.py config/universe_mvp_software_like.yaml \
    --provider cache [--n-jobs 4] [--incremental] [--pool-hazard] [--calibrate]
```

## Configuration

Universes are YAML (`config/*.yaml`); a ticker is one entry, the universe is the unit.
Two are bundled:

- `config/universe_mega_cap_ai_infra.yaml` — 6 mega-caps (singleton peer groups).
- `config/universe_mvp_software_like.yaml` — **the Phase 5 universe**: 9 tickers
  matching the data export — MSFT/AAPL/GOOGL/AMZN/META (`mega_cap_software_like`),
  NVDA (`ai_accelerator`), QQQ/XLK/IGV (`etf_context`). The 5-member peer group is what
  lets pooled, state-conditioned estimates discriminate.

`rolling_windows.ma_fast` is kept at **200** (V12-faithful), not the spec's
illustrative `50`, so the V10 parity regression guard holds.

## Data provenance for the bundled cache

`data/price_cache/{TICKER}.csv` holds fully split/dividend-adjusted daily OHLCV
(yfinance `auto_adjust` basis) for **9 tickers — MSFT, AAPL, GOOGL, AMZN, META, NVDA,
QQQ, XLK, IGV — through 2026-06-05**, derived from the user's `mvp_universe` yfinance
export (`docs/uploaded/mvp_universe_yfinance_exports_20260605.zip`, 0 missing / 0
duplicates). Real market data for a reproducible offline demo; the `data_loader` also
supports live `yfinance` / Yahoo-chart pulls (`provider="auto"`). Prior caches are
backed up under `data/price_cache/_backup_*`.

## Output: `SingleTickerStatisticalContextEnvelope`

```jsonc
{
  "schema_version": "v13_single_ticker_statistical_context_envelope",
  "as_of": "2026-06-05", "ticker": "MSFT",
  "sector": "software", "peer_group": "mega_cap_software_like",
  "active_engine_context": { "active_engine": "repair_retry_hazard_engine", "mode_state": "..." },
  "repair_retry_context": { "active": true, "distance_to_ma250_pct": -10.1, "required_rebound_to_ma250_pct": 11.2, ... },
  "retry_hazard_context": {                       // canonical P(retry<=H) = EMPIRICAL completed-path
     "active": true, "hazard_today": 0.0001,
     "p_retry_within_40d": 0.78, "probability_policy": "v13_empirical_horizon_calibrated",
     "p_retry_within_40d_reference_scope": "...", "p_retry_within_40d_reference_n": 239,
     "diagnostic_model_state_hold_forward": { ... },          // the demoted saturating curve
     "calibration_gate_40d": { "passed": true, ... }, "surfaced_probability_is_calibrated": true,  // when calibrate+pool
     "direct_classifier_blend": {                             // P7 overlay, only when surface_blend=True
        "policy": "gated_discriminative_overlay_empirical_remains_canonical",
        "per_horizon": { "40": { "blend_probability": 0.548, "classifier_probability": 0.492,
                                 "empirical_probability": 0.603, "blend_weight_classifier": 0.5,
                                 "gate_passed": true } }, "any_gate_passed": true }
  },
  "retry_timing_context": { "active": true, "consensus": { "central_remaining_days": ..., ... }, ... },   // P2
  "post_confirmation_trend_context": { "active": false, ... },
  "calibration_context": { "available": true, "summary": [per-horizon obs/pred/Brier/AUC/MACE], "trust_gate": {...} },  // when calibrate=True
  "option_overlay_research_hint": { "must_not_auto_execute": true, ... },
  "warnings": [...], "disclaimers": [...]
}
```

JSON schema: `exports/reports/statistical_context_schema.json`.

## Phase status (engine versions)

| Phase | Scope | Status |
|---|---|---|
| V13.0–V13.2 | skeleton, single-ticker pipeline, universe batch runner + bundle | ✅ |
| **V13.3** | peer/sector/universe analytics — **roadmap Phases 1–7 all delivered** (see above) | ✅ |
| V13.4 | cross-sectional dashboard | core table; plots pending |
| V13.5 | universe context bundle export | ✅ |
| V13.7 | calibration + gating | ✅ (Phase 4, opt-in `calibrate`) |
| **V13.9** | discriminative overlay — direct horizon classifier ↔ empirical blend | ✅ (Phase 7, opt-in `surface_blend`) |
| **Phase 8** | retry-**success** overlay — success classifier ↔ empirical blend + `P(reclaim≤H)` composite, gated | ✅ (opt-in `surface_success`) |
| **Phase 11** | trend-outlook — handoff coverage + de-saturated/de-collinearized trend scores (TO-0/TO-1) | ◐ in progress (TO-2…TO-4 remain) |
| V13.6 / V13.8 | universe replay sweep, repo-integration adapter | planned |

## Tests

```bash
pytest                       # 68 tests (fine on a machine with ample RAM)
bash scripts/run_tests.sh    # process-isolated (one file per process) — for memory-constrained envs/CI
```

Several tests run the full real-data universe pipeline and hold large pandas frames, so
a single `pytest` process can OOM on a constrained machine. A `gc` sweep runs after each
test to bound peak memory; for guaranteed isolation use `scripts/run_tests.sh` (each
test file in its own process). Both report the same **68 passing** (16 files).

Includes parity/structure gates, parallel==serial & incremental==full equivalence,
an AST guard against hardcoded tickers, and per-phase tests (evidence, timing, empirical
hazard, calibration, pooling).

## Documentation

See **`docs/README.md`** for the full index. Highlights:

- `docs/V13_user_guide.md` — install, configuration, running (single / batch / parallel
  / incremental / **pooled** / **calibrated**), output reference, interpretation,
  troubleshooting, and **deployment** (local/cron, Docker, VPS, cloud scheduled-job).
- `docs/phased_design/` — the V13.3 phased roadmap, each phase wrapped with spec +
  results + artifacts (Phases 1–7).
- `docs/V13_data_and_report_analysis.md` — the data/report cross-check + the hazard
  step diagnosis (§2.2) and the empirical-estimator reconciliation (§2.3).
- `docs/V13_universe_statistical_context_engine_development_spec.md` — architecture spec + roadmap.
- `docs/V13_performance_optimization_report.md` — profiling + the replay vectorization (~13×) + parallel/incremental.
- `docs/tutorials/` — **7 numbered tutorials**: 01 performance optimization · 02 optional-computation
  feature flags · 03 empirical-estimator-over-model-extrapolation (P3) · 04 calibration & trust-gating
  (P4) · 05 AUC & calibration for ML students · 06 direct horizon classifier & the blend (P7) ·
  07 the MSFT 2026-06-05 low-readiness-repair blend walkthrough (P7 worked example).
- `docs/research/` — research notes on improving/validating specific estimators. **`01_retry_success_probability_2026-06-08.md`** — occurrence vs **success** probability, the prototype quality classifier today, and the playbook + validation to make retry-success trustworthy.
- `docs/multi-sector/` — forward-looking analysis + phased plan for widening to a multi-sector universe (sector-dependence handling + challenges + MS-0…MS-5). *Plan only.*
- `docs/option-mgmt-integration/` — assessment + design/plan + **two-repo strategy** for feeding this engine's context into the `option-mgmt-2026` decision engine. *Plan only.*
- `docs/uploaded/` — the V12 benchmark notebook, the V12 reports, and the 9-ticker data export (catalog in its README).

## Performance

The daily replay is vectorized and an unused ML bootstrap is gated off (see the perf
report; ~13–16× on the pre-P3 path). For scale: **parallel** (`run_universe_pipeline(n_jobs=N)`
/ `--n-jobs N`) and **daily incremental** (`incremental=True, state_dir=...` /
`--incremental`, split/dividend-safe cache invalidation) — both **output-preserving**.
The P3 empirical-horizon estimator adds per-day reference lookups, and `calibrate` /
`pool_hazard` are heavier **opt-in** modes (they rescan / pool the panel); the default
path stays fast. A Docker image (`Dockerfile`) + deployment patterns are in the user guide.

## How the retry probability earns trust (P3 → P5)

Single-ticker, the empirical estimator can't fill its state-conditioned buckets, so it
falls back to a near-constant rate (AUC ≈ 0.46) and the **trust gate correctly fails**.
Pooling the 9-ticker universe fills those buckets (n: 783 → 4,765) so the estimate
**discriminates** (AUC 0.74–0.82) and **calibrates** (MACE 0.04–0.08) — the gate passes
at 10/20/40d (60d marginal). The probability is surfaced as trustworthy only where the
gate passes; otherwise it is flagged uncalibrated. This is the value-first / trust-last
design: ship the defensible evidence, and make the forward probability earn its place.
