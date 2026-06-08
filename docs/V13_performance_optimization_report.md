# V13 Performance Optimization Report

**Scope:** reduce the per-ticker compute cost so daily scans of many tickers
across many sectors are practical.
**Date:** 2026-06-07 · **Engine:** `yearline_universe` v13.1
**Result:** **~13× mean speedup** (55.9s → 5.5s per ticker, up to 16.7×), with
**proven zero change to outputs**.

---

## 1. Executive summary

The first cut ran a single ticker end-to-end in ~54–61 seconds. Profiling showed
**88% of the time was the daily replay**, which rebuilt a one-hot logistic design
matrix and 90 future-day rows *for every as-of date* (~1,200 days/ticker).

Because the hazard model is a **linear (logistic)** classifier and the
state-hold-forward scenario only varies **two** features across the horizon, the
entire 90-day curve per as-of day is a closed form. We replaced ~1,200 per-day
design-matrix builds with **one** build + vectorized arithmetic, and stopped
recomputing indicators per transition. Outputs are identical to floating-point
noise (max abs diff ~2×10⁻¹⁴; envelopes byte-identical).

| Ticker | Before | After | Speedup |
|---|---|---|---|
| MSFT | 60.71s | 10.12s | 6.0× |
| AAPL | 53.28s | 3.26s | 16.3× |
| NVDA | 53.78s | 3.23s | 16.7× |
| **mean** | **55.9s** | **5.5s** | **13.0×** |

Full 3-ticker universe run + all exports: **~3 min → 17s**. Test suite: **55s → 24s** (22/22 pass).

---

## 2. Methodology

- **Profiler:** Python `cProfile`, sorted by cumulative time, on a full MSFT
  `run_ticker_pipeline` (cache provider, replay from 2020-01-01).
- **Wall-clock:** `time.perf_counter()` around `run_ticker_pipeline` for
  MSFT/AAPL/NVDA, before and after.
- **Equivalence harness:** snapshot each ticker's exported envelope (JSON) and
  full `replay_history` (pickle) *before* the change; after the change, assert the
  envelope JSON is byte-identical and every replay-history numeric column matches
  within `rtol=1e-7, atol=1e-9` (and categoricals exactly). Scripts:
  `bench_baseline.py`, `verify_optimization.py`.

All numbers are measured on the three cached tickers (real adjusted OHLCV through
2024-11-29), not estimated.

---

## 3. Bottleneck analysis

cProfile of one MSFT run (109.5s under the profiler's overhead):

```text
cumtime  calls    function
109.5s     1      run_ticker_pipeline
 96.6s     1        build_replay_history          <-- 88% of the run
 92.8s   1231         _score_curve (per as-of day)
 55.8s 443520           Series.__setitem__        <-- building 90 future rows/day
 20.7s   1233         prepare_hazard_design        <-- get_dummies + concat/day
  9.8s     1        fit_retry_timing_model (300x HuberRegressor bootstrap)
```

Root cause: for each of ~1,200 as-of days the replay (a) constructed a 90-row
future DataFrame via per-cell `Series.__setitem__` (443,520 assignments) and
(b) re-ran `pd.get_dummies` + `concat` against the training panel, then
`predict_proba`. The per-day work was ~99% redundant.

---

## 4. Optimizations implemented (safe, output-preserving)

### 4.1 Vectorized replay hazard scoring (the big win)

The discrete-time hazard is `LogisticRegression`, so for a feature row *x*:

```text
logit(x) = x · coef + intercept ,   hazard = sigmoid(logit)
```

Under the **state-hold-forward** scenario the only features that change across
the future horizon `h = 1..90` are `trading_days_since_touch` and
`calendar_days_since_touch`, each incremented by `h`. Therefore:

```text
logit(as_of, h) = base_logit(as_of) + h · (coef_trading + coef_calendar)
                = base_logit(as_of) + h · slope
```

So we:

1. Build the design matrix **once** for all as-of base rows (one `get_dummies`).
2. Compute `base_logit` for every as-of via a single matrix product.
3. Broadcast `hazard[as_of, h] = sigmoid(base_logit[:,None] + H[None,:]·slope)`.
4. `cumulative = 1 − cumprod(1 − hazard)` along the horizon axis; read off the
   10/20/40/60/90-day values and the median-crossing day.

This is **mathematically exact** for the linear model (the previous code did the
identical arithmetic, just re-derived per day). Implementation:
`replay._batch_score_curves`.

### 4.2 Compute indicators once per ticker

`build_hazard_daily_panel` previously called `add_indicators` (rolling MA250/
MA200/ATR over the full series) inside `_make_daily_rows_for_transition` for
**every** transition. It now computes the indicator frame once per ticker and
passes it in (`ind_df`). Implementation: `hazard.py`.

> Neither change touches the methodology, the detector, the canonical-event
> taxonomy, or the model coefficients.

---

## 5. Output-equivalence proof

`verify_optimization.py` compared optimized vs baseline for all three tickers:

```text
MSFT: env_match=True  replay_shape_ok=True  num_ok=True  cat_ok=True  maxabsdiff=2.03e-14
AAPL: env_match=True  replay_shape_ok=True  num_ok=True  cat_ok=True  maxabsdiff=7.55e-15
NVDA: env_match=True  replay_shape_ok=True  num_ok=True  cat_ok=True  maxabsdiff=5.11e-15
ALL OUTPUTS PRESERVED: True
```

- Every exported envelope is **byte-identical** (sorted-key JSON).
- Replay histories match to ~10⁻¹⁴ (machine epsilon; matmul vs. incremental sum).
- Categorical columns (e.g. `mode_state_replay`) match exactly.
- `pytest` → 22/22 pass.

---

## 6. Benchmark results

See §1. Note MSFT's residual 10s is no longer the replay — it is now the
**retry-timing bootstrap** (300 `HuberRegressor` fits, which only runs for tickers
with ≥20 completed transitions). AAPL/NVDA skip it (insufficient transitions) and
land at ~3.2s. This is the next optimization target (§8).

---

## 7. Scaling projection for daily multi-ticker / multi-sector scans

Planning figures using the measured optimized cost (assume ~6s/ticker average,
~10s for high-event tickers; tickers are **independent → embarrassingly parallel**):

| Universe size | Serial (~6s/ticker) | 4 workers | 8 workers | Incremental daily mode* |
|---|---|---|---|---|
| 25 | ~2.5 min | ~40 s | ~20 s | ~seconds |
| 100 | ~10 min | ~2.5 min | ~75 s | ~10–30 s |
| 500 | ~50 min | ~12 min | ~6 min | ~1–2 min |

\* *Incremental daily mode (recommended, not yet implemented — §8): after the
first full backfill, a daily run only scores the newest bar(s) and reuses the
cached replay history, collapsing per-ticker cost from O(replay-span) to ~O(1).
This is the single biggest lever for large daily universes.*

Takeaways:
- Today's engine already makes a **few-hundred-ticker daily scan** feasible on a
  modest box (serial tens of minutes; parallel a few minutes).
- For large universes or intraday cadence, implement incremental mode (§8) before
  scaling ticker count.

---

## 8. Further optimizations (recommended next, NOT in this change)

Ordered by impact for a daily multi-ticker scan:

1. **Incremental daily-update mode** — ✅ **DONE (2026-06-07).** Implemented at the
   replay level (the part that *can* be made incremental): `run_ticker_pipeline(...,
   incremental=True, state_dir=...)` persists each ticker's replay history and a
   fingerprint of the inputs it depends on (adjusted OHLCV over the overlap +
   canonical events + the fitted model). A daily run appends only the new bar(s);
   any change to those inputs — **notably a split or dividend re-adjustment, which
   re-bases the whole adjusted series** — invalidates the cache and triggers a full
   recompute, so output is always identical to a full replay. Measured ~2.1–2.5×
   on a benign daily step. (See the Addendum.)
2. **Parallelize across tickers** — ✅ **DONE (2026-06-07).**
   `run_universe_pipeline(..., n_jobs=N)` runs tickers across processes
   (`ProcessPoolExecutor`, stdlib); `n_jobs<=0` = all cores. Output is
   byte-identical to serial (verified) and per-ticker failures stay isolated even
   at the worker-process level. Measured 1.63× on a 2-core box for the 3-ticker
   demo (Amdahl-capped by the slowest ticker, MSFT ~10s); larger/balanced
   universes approach N×. Note: combine with item 1 (incremental mode) and item 3
   (bootstrap) so no single ticker dominates the wall-clock.
3. **Retry-timing bootstrap** — ✅ **RESOLVED (2026-06-07) by elimination.**
   Profiling the post-replay run showed MSFT's residual ~6–7s was the timing
   model's 300-fit Huber bootstrap — and that its output (and the quality model's)
   is **never consumed by the V13.1 envelope**. It was dead work. `run_hazard_layer`
   now skips both by default (`fit_ml_models=False`), an output-preserving change
   that drops MSFT from ~10s → ~3.6s and makes per-ticker time uniform. The
   capability remains available via `fit_ml_models=True`. (See the Addendum.)
4. **Persist artifacts:** parquet price cache (faster than CSV parse), and pickle
   fitted hazard/ML models so re-runs skip refitting.
5. **Vectorize per-day state slicing** in the replay pass-1 loop (running
   cumulative min for `drawdown_so_far`) to remove the remaining Python loop.
6. **Optional engines:** `polars`/`numba` for the detector if ticker counts grow
   into the thousands.

---

## 9. VPS compute sizing (the planned deployment target)

- **CPU:** the work is CPU-bound and parallel per ticker. Set worker count ≈
  vCPUs. A **4 vCPU** VPS runs ~100 tickers in ~2–3 min (parallel) today; with
  incremental mode it handles many hundreds daily in well under a minute.
- **Memory:** each ticker holds a few ~4,000-row frames (a few MB); peak is
  modest. **4–8 GB** is comfortable for a few hundred tickers; keep per-process
  memory in mind when setting worker count.
- **Disk:** small — price cache (~1 MB/ticker CSV) + JSON exports (~few KB each).
- **Network:** only for live data. For reliability, **cache prices nightly** in a
  separate step (avoid Yahoo rate limits during the scan) and run the scan from
  cache.
- **Recommendation:** start on a **2–4 vCPU / 8 GB** VPS; schedule via systemd
  timer/cron (see the user guide §12.3); revisit only if you push toward
  thousand-ticker universes or intraday cadence (then do §8.1 + §8.2 first).

---

## Addendum — 2026-06-07: parallelism, incremental mode, and the "dead bootstrap"

Three follow-on changes, all **output-preserving** (verified byte-identical
envelopes; replay diffs ~10⁻¹⁵):

1. **Parallel runner** (§8.2) — `run_universe_pipeline(n_jobs=N)`; ~1.5× on 2 cores,
   scales toward N×.

2. **The "dead bootstrap" (the most instructive finding).** After the replay
   vectorization, MSFT still took ~10s while AAPL/NVDA took ~3s. Re-profiling
   showed the gap was the retry-timing model's **300-fit Huber bootstrap** — and
   that the timing/quality ML outputs are **never read by the V13.1 envelope**.
   It was computing an expensive result and throwing it away. Gating it off
   (`fit_ml_models=False`) is free: **MSFT 10.1s → 3.6s**, per-ticker time now
   uniform, **mean speedup vs. the original baseline rises to 16.3×** (3.4s mean),
   with envelopes byte-identical to the proven baseline.
   *Lesson: the fastest code is the code you don't run — always check whether a hot
   computation's output is actually consumed.*

   **The `fit_ml_models` flag (default `False`) — is "off" a performance issue?**
   No — *off is the optimization* (it skips the dead work and changes no output).
   The flag is threaded through `run_hazard_layer` / `run_ticker_pipeline` /
   `run_universe_pipeline` and the `--fit-ml-models` CLI switch. Turning it **on**
   re-adds the bootstrap cost, but only where the bootstrap actually runs —
   tickers with **≥20 completed transitions**:

   | ticker | completed transitions | off | on | delta |
   |---|---|---|---|---|
   | MSFT | 23 (≥20 → bootstrap runs) | 3.66s | 10.02s | **+6.4s** |
   | AAPL | 15 (<20 → short-circuits) | 3.19s | 3.21s | +0.0s |

   The envelope is **byte-identical** on or off (the predictions still aren't wired
   into it); when on, the predictions are attached to
   `result.manifest["ml_models"]`. **When to enable:** only when a consumer needs
   the prototype retry-timing/quality predictions — ad-hoc research, the future
   **V13.2 pooled-hazard** work (pooled training makes the models meaningful), or a
   downstream consumer that adds those fields. For the current statistical-context
   envelope, leave it off.

3. **Incremental daily mode** (§8.1) — replay-level caching with split/dividend-safe
   invalidation. ~2.1–2.5× on a benign daily step; identical to a full replay.

Updated picture (per ticker, cached data): **full run ~3.4s** (was ~56s, **~16×**);
**incremental daily step ~1.3–1.7s**; both ÷ cores under `n_jobs`. A 500-ticker
daily scan on 8 cores ≈ ~1–2 min.

| stage | MSFT | AAPL | NVDA | mean | vs. baseline |
|---|---|---|---|---|---|
| original | 60.7s | 53.3s | 53.8s | 55.9s | 1× |
| + replay vectorization | 10.1s | 3.3s | 3.2s | 5.5s | 13× |
| + gate dead ML bootstrap | 3.6s | 3.3s | 3.3s | 3.4s | **16.3×** |
| + incremental (daily step) | ~1.7s | ~1.3s | ~1.3s | ~1.4s | **~40×** |

---

## 10. Reproduce

```bash
python bench_baseline.py        # profile + baseline timings + 'before' snapshots
python verify_optimization.py   # replay-vectorization + ML-gating equivalence + speedup
python verify_parallel.py       # parallel == serial + speedup
python verify_incremental.py    # incremental == full + daily-step speedup
cd v13_universe_engine && pytest -q
```

*Educational research only. Not financial advice.*
