# Phase 5 — Pooled Training + Data Freshness (the step that clears the gate)

**Status:** ✅ DELIVERED (2026-06-07)
**Part of:** the V13.3 phased roadmap (see `../README.md`)
**Theme:** the supporting work that makes the forward probability *trustworthy* — pool
the universe so the empirical estimator can discriminate, and refresh the cache so the
whole universe is on current data. **Result: the Phase 4 trust gate now passes at
10/20/40 days.**

> Educational research only. Not financial advice. Evidence overlay; no execution.

---

## 1. Objective

Phases 1–4 built and gated the forward probability; on single-ticker data the gate
**correctly failed** (no discrimination). Phase 5 supplies the two things that fix
that — **pooling** and **data freshness** — and verifies they lift the empirical
estimator past the trust gate.

## 2. What was delivered

### 2a. Data freshness — the full universe, current

The user ran a yfinance export notebook and supplied
`docs/uploaded/mvp_universe_yfinance_exports_20260605.zip`: **9 tickers, all
2009-01-02 → 2026-06-05** (META from its 2012-05-18 IPO), clean (0 missing / 0
duplicates — see `artifacts/universe_data_quality.csv`):

- mega-caps **MSFT, AAPL, GOOGL, AMZN, META** (peer `mega_cap_software_like`),
- **NVDA** (`ai_accelerator`),
- ETFs **QQQ, XLK, IGV** (`etf_context`).

All 9 were written to `data/price_cache/{TICKER}.csv` on the fully-adjusted basis
(`Close = adj_close`; OHLC × `adj_close/close`). This **refreshes NVDA** (was stale at
2024-11-29) and **adds AMZN/GOOGL/META** (previously absent) — the universe is no
longer 3 mixed-date tickers but 9 current ones. Prior cache backed up under
`data/price_cache/_backup_pre_phase5_20260607/`. New config
`config/universe_mvp_software_like.yaml` mirrors the export's grouping.

### 2b. Pooled training/reference/calibration

New `pool_hazard` mode on `run_universe_pipeline` (CLI `--pool-hazard`): build every
ticker's foundation once, then run each ticker's hazard fit, **empirical-horizon
reference (Phase 3)**, and **calibration (Phase 4)** on the **pooled** cross-ticker
panel. Because the empirical estimator's scope ladder can now reach state-conditioned
scopes with ≥25 rows across the universe, it **discriminates** instead of falling back
to a near-constant transition-only rate.

Mechanics: extracted `_build_foundation` (so foundations build once and feed back via
`run_ticker_pipeline(prebuilt_foundation=…)` — no double compute); the universe runner
assembles `pooled_data` and threads it. Output-changing for the hazard/calibration
blocks only; descriptive/timing/trend blocks unchanged.

## 3. Acceptance criteria & results

| Criterion | Target | Result |
|---|---|---|
| Data freshness | universe on current data | ✅ 9/9 tickers to 2026-06-05; NVDA refreshed, AMZN/GOOGL/META added |
| Pooling wired | `pooled_data` threads hazard + reference + calibration | ✅ `pool_hazard=True` / `--pool-hazard`; foundations built once |
| **Pooling lifts discrimination past the gate** | AUC ≥ 0.60, MACE ≤ 0.10 at usable horizons | ✅ **gate PASSES at 10/20/40d** (AUC 0.74–0.82, MACE 0.036–0.077); 60d marginal (MACE 0.109) |
| Reproduces V12.6 pooled metrics | ~AUC 0.80 (10d) / 0.745 (40d) | ✅ 9-ticker: 10d AUC **0.816**, 40d **0.762** — matches the report |
| Determinism preserved | parallel == serial | ✅ (unchanged; foundation refactor is output-preserving for the default path) |
| Tests green | all pass | ✅ **46/46** (added a pooled-vs-single hazard test; fixed the now-cached AMZN failure-isolation case) |

### Before / after — calibration of the empirical estimator (`artifacts/calibration_single_vs_pooled.csv`)

| horizon | single-ticker MSFT (n=783) | **pooled 9-ticker (n=4765, 162 transitions)** |
|---|---|---|
| 10d | AUC 0.524, MACE 0.155 → ❌ | **AUC 0.816, MACE 0.036 → ✅** |
| 20d | AUC 0.430, MACE 0.314 → ❌ | **AUC 0.779, MACE 0.048 → ✅** |
| 40d | AUC 0.462, MACE 0.330 → ❌ | **AUC 0.762, MACE 0.077 → ✅** |
| 60d | AUC 0.465, MACE 0.181 → ❌ | AUC 0.738, MACE 0.109 → ✗ (marginal) |

Pooled predicted ≈ observed almost exactly (40d: 0.625 vs 0.620). **This is the
capstone**: the same trust gate that honestly refused the single-ticker number now
passes it on the pooled universe — i.e. the forward "days-to-touch" probability is
finally credible at ≤40d, on real, current, multi-ticker data.

## 4. Files changed (as-built)

- `data/price_cache/*.csv` — refreshed to 9 current tickers (backup retained).
- `config/universe_mvp_software_like.yaml` (new) — the Phase 5 universe (export grouping).
- `src/yearline_universe/ticker_pipeline.py` — `_build_foundation` extraction;
  `run_ticker_pipeline(prebuilt_foundation=…)`; `run_universe_pipeline(pool_hazard=…)`
  two-pass pooling.
- `scripts/run_universe_mvp.py` — `--pool-hazard`.
- `tests/test_ticker_pipeline.py` — pooled-vs-single hazard test; uncached-ticker fix.
- Docs: this README + `artifacts/`, spec §0/§8/§11, `docs/uploaded/README.md`.

## 5. Reproduce

```bash
# pooled hazard + calibration over the fresh 9-ticker universe
python scripts/run_universe_mvp.py config/universe_mvp_software_like.yaml \
    --provider cache --pool-hazard --calibrate
# MSFT calibration_context.trust_gate now passes at 10/20/40d
```

> Cost note: `--pool-hazard --calibrate` recomputes the (universe-level) pooled
> calibration per ticker — the calibration metrics are identical across tickers, so a
> single run characterises the universe. A future optimisation could compute it once
> and share. `--pool-hazard` alone (pooled hazard/reference, no calibration) is cheap.

## 6. Limitations & decision gate

- 60d still misses the MACE gate (0.109 vs 0.10) — a bigger/cleaner universe or a
  cross-validated isotonic transform would likely close it. 10/20/40d pass.
- ETFs (QQQ/XLK/IGV) rarely breach MA250, so they add mostly non-event reference rows;
  the discrimination comes chiefly from the 6 equities.
- The pooled calibration is universe-level (same for every ticker); per-sector / per
  -peer calibration is a natural extension.

**Roadmap status:** Phases 1–5 DELIVERED. The forward probability is now a credible,
calibrated, gated quantity at ≤40d on current pooled data — the value-first / trust-last
arc is complete. Natural follow-ons: per-sector calibration, the cost optimisation
above, and a nightly data-refresh job (the cache is now a drop-in 9-ticker set).
