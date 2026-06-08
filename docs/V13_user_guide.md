# V13 Universe Statistical Context Engine — User Guide

`yearline_universe` turns daily OHLCV for a configured universe of tickers into a
**repo-ready statistical context** describing each ticker's MA250 / yearline
repair-and-trend state, exported as JSON for downstream consumers
(e.g. `option-mgmt-2026`).

> **Educational research only. Not financial advice.** The engine emits
> **evidence context, never trades.** The `option_overlay_research_hint` block is
> flagged `must_not_auto_execute: true` and has no broker/execution semantics.

---

## 1. Mental model

The "yearline" is the 250-day moving average (MA250). The engine tracks, for each
ticker, a sequence of **attempts** to reclaim the yearline from below, groups them
into **rounds** (a round ends on a confirmed success), and then runs two engines
with an explicit **handoff**:

```text
                 price below / testing MA250          price accepted above MA250
                 ┌───────────────────────────┐        ┌──────────────────────────┐
   detector ───► │ Repair / retry / hazard    │  ⇄     │ Post-confirmation trend   │
   episodes      │  - distance to MA250       │ handoff│  - trend quality          │
   recovery      │  - required rebound        │        │  - pullback quality       │
                 │  - retry hazard (survival) │        │  - overextension          │
                 └───────────────────────────┘        │  - deterioration risk     │
                                                       └──────────────────────────┘
```

`semantic.py` decides which engine is **active** on each date from the replay
`mode_state` and **gates** the inactive engine's metrics (e.g. retry-hazard fields
are `null` while the trend engine is active). The final per-ticker output is the
`SingleTickerStatisticalContextEnvelope`.

---

## 2. Install

Requires Python 3.9+.

```bash
cd v13_universe_engine
python -m venv .venv && source .venv/bin/activate
pip install -e .                  # core: numpy, pandas, scipy, scikit-learn, pyyaml
pip install -e ".[live,viz,dev]"  # + yfinance/curl_cffi (live), matplotlib (viz), pytest (dev)
```

Cache-only runs need no network and no `[live]` extras.

---

## 3. Quickstart

```python
from yearline_universe import load_universe_config, run_ticker_pipeline, export_single_ticker_context

uni = load_universe_config("config/universe_mega_cap_ai_infra.yaml")

# The SAME function runs any ticker — no ticker-specific branching.
res = run_ticker_pipeline(uni.get_ticker("MSFT"), uni, cache_dir="data/price_cache")
envelope = export_single_ticker_context(res)     # repo-ready JSON dict
print(envelope["active_engine_context"]["active_engine"], envelope["as_of"])
```

Whole universe (batch, with per-ticker failure isolation):

```python
from yearline_universe import run_universe_pipeline
result = run_universe_pipeline(uni, cache_dir="data/price_cache")
print(result.run_manifest["n_ok"], "/", result.run_manifest["n_tickers"], "ok")
bundle = result.universe_context_bundle      # UniverseStatisticalContextBundle
```

End-to-end via the runner script (writes all exports):

```bash
python scripts/run_universe_mvp.py config/universe_mega_cap_ai_infra.yaml --provider cache
```

---

## 4. Configuration

Universes are YAML files in `config/`. A ticker is just one entry; the top-level
unit is the universe.

```yaml
universe_name: mega_cap_ai_infra
benchmark: SPY
start: "2009-01-01"        # data window start
replay_start: "2020-01-01" # daily replay / trend history start
as_of: null                # null => latest available bar
rolling_windows:
  ma_fast: 200             # StudyConfig.ma_fast_len (V12-faithful; not 50)
  ma_yearline: 250         # the MA250 "yearline"
  atr: 14
tickers:
  - { ticker: MSFT, sector: Information Technology, peer_group: mega_cap_software, role: cloud_ai_platform }
  - { ticker: NVDA, sector: Information Technology, peer_group: ai_accelerator,    role: ai_compute_leader }
```

**Required per ticker:** `ticker`, `sector`, `peer_group`. Optional: `industry`,
`role`, `weight`. Duplicate tickers and missing required fields raise a clear
error at load time.

**Tuning the methodology:** detector/episode parameters live in `StudyConfig`
(`src/yearline_universe/config.py`) — band, confirm/hold days, gap thresholds,
horizons, etc. Defaults match the V12 reference. The universe YAML can override
`ma_fast` / `ma_yearline` / `atr` via `rolling_windows`; keep `ma_fast: 200` to
preserve the V10 parity guard.

To add tickers/sectors, append entries to `tickers:` and provide a price source
(cache CSV or live access — see §5).

---

## 5. Data providers

`data_loader.load_price_data` tries providers in order (`provider="auto"`):

| provider | source | notes |
|---|---|---|
| `cache` | `data/price_cache/{TICKER}.csv` | offline, reproducible; fully split/div-adjusted OHLCV (`auto_adjust` basis) |
| `yfinance` | live Yahoo via `yfinance` | needs `[live]` extra + network |
| `yahoo_chart` | Yahoo v8 chart API | `requests`/`curl_cffi` fallback |

- `provider="cache"` → offline only (fails fast on a missing ticker).
- `provider="auto"` → cache, then live, then chart API.
- **`as_of`**: each envelope records `source.data_as_of` (the last bar used). The
  bundled cache holds **9 tickers (MSFT, AAPL, GOOGL, AMZN, META, NVDA, QQQ, XLK, IGV)
  through 2026-06-05** (from the `mvp_universe` export in `docs/uploaded/`); prior caches
  are backed up under `data/price_cache/_backup_*`. Supply live data for newer bars.
- **Populating the cache:** drop a CSV at `data/price_cache/{TICKER}.csv` with
  columns `Date, Open, High, Low, Close, Volume` (split/dividend-adjusted), or run
  with `provider="auto"` once in an unblocked environment to fetch live.
- **Rate limits:** public Yahoo endpoints rate-limit shared IPs (HTTP 429); for
  reliable daily live scans, cache nightly or use a paid data feed adapter.

---

## 6. Running

| Goal | How |
|---|---|
| One ticker | `run_ticker_pipeline(uni.get_ticker("AAPL"), uni, cache_dir=...)` |
| Whole universe | `run_universe_pipeline(uni, cache_dir=...)` |
| Whole universe, parallel | `run_universe_pipeline(uni, cache_dir=..., n_jobs=4)` (`n_jobs<=0` = all cores) |
| Daily incremental | `run_universe_pipeline(uni, ..., incremental=True, state_dir="data/replay_state")` |
| **Pooled** hazard/reference/calibration (Phase 5) | `run_universe_pipeline(uni, ..., pool_hazard=True)` (opt-in; pools the universe so state-conditioned scopes discriminate) |
| With horizon calibration + trust gate | `run_universe_pipeline(uni, ..., calibrate=True)` (opt-in, V13.7; rescans the panel, slower) |
| **Trustworthy probability** (pooled + calibrated) | `run_universe_pipeline(uni, ..., pool_hazard=True, calibrate=True)` — gate passes at 10/20/40d |
| **Discriminative blend overlay** (Phase 7) | `run_universe_pipeline(uni, ..., pool_hazard=True, surface_blend=True)` (opt-in, pooled-only; adds `retry_hazard_context.direct_classifier_blend`) |
| With prototype ML timing/quality models | `run_ticker_pipeline(..., fit_ml_models=True)` (opt-in; not consumed by the envelope) |
| Full run + exports | `python scripts/run_universe_mvp.py <config.yaml> --provider cache [--n-jobs 4] [--incremental] [--pool-hazard] [--calibrate] [--fit-ml-models]` |
| Notebook | open `notebooks/yearline_v13_universe_mvp.ipynb` |

> `calibrate`, `fit_ml_models`, `pool_hazard`, and `surface_blend` are all **opt-in**
> (default off → the hot path stays fast and `calibration_context.available=false`).
> `calibrate` / `fit_ml_models` are **output-preserving when off**. `pool_hazard` is
> **output-changing for the hazard/calibration blocks** (it pools the universe so the
> empirical estimator can discriminate) — the descriptive/timing/trend blocks are
> unaffected. **`surface_blend`** (Phase 7, pooled-only) adds an **additive, gated**
> `direct_classifier_blend` overlay — a direct horizon classifier blended per-horizon with
> the empirical estimate; the empirical number stays **canonical** and with the switch off
> the envelope is byte-identical. To get a *trustworthy* surfaced probability (gate passing
> at ≤40d) use **`pool_hazard=True, calibrate=True`** together (see §7–§8 and
> `docs/phased_design/phase_05/`); add **`surface_blend=True`** for the Phase 7 overlay
> (`docs/phased_design/phase_07/` + tutorials 06–07).

Exports layout:

```text
exports/
  ticker_contexts/{TICKER}_statistical_context.json   # per-ticker envelope
  universe_contexts/{universe}_bundle.json            # universe bundle
  universe_contexts/{universe}_run_manifest.json      # status per ticker
  reports/statistical_context_schema.json             # JSON schema
  reports/{ml,hazard}_feature_leakage_audit.csv       # anti-leakage policy
```

---

## 7. Output reference — `SingleTickerStatisticalContextEnvelope`

```jsonc
{
  "schema_version": "v13_single_ticker_statistical_context_envelope",
  "as_of": "2026-06-05", "ticker": "AAPL",          // trend-active example (AAPL above MA250); MSFT is repair-active
  "sector": "consumer_tech", "peer_group": "mega_cap_software_like",
  "source": { "data_provider": "cache", "data_as_of": "2026-06-05", "replay_start": "2020-01-01", ... },
  "active_engine_context": { "active_engine": "post_confirmation_trend_engine", "mode_state": "accepted_above_watch" },
  "repair_retry_context":  { "active": false, "distance_to_ma250_pct": 2.46, "required_rebound_to_ma250_pct": 0.0, ... },
  "retry_hazard_context":  { "active": false, "p_retry_within_40d": null,           // gated off when trend engine active
                             "probability_policy": "v13_empirical_horizon_calibrated",  // canonical P(retry<=H) = empirical completed-path
                             "p_retry_within_40d_reference_scope": "...", "p_retry_within_40d_reference_n": 0,
                             "diagnostic_model_state_hold_forward": { ... } },        // the demoted, saturating model curve (diagnostic only)
  "post_confirmation_trend_context": { "active": true, "trend_state": "early_confirmation", "trend_quality_score": 0.58, ... },
  "retry_timing_context":  { "active": true, "conditioning_scope": "single_ticker_self_conditioned",   // gated off unless repair engine active
                             "setup": { "target_transition": "2_to_3", "drawdown_assumption_abs_pct": 10.01, "days_elapsed_since_latest_touch": 4, ... },
                             "consensus": { "central_remaining_days": 41.1, "remaining_days_range": [13.0, 73.2], "rough_central_retry_date_if_repair_continues": "2026-07-17", ... },
                             "estimators": [ /* median / matrix-interp / nearest-neighbor / Theil-Sen, each total+remaining+rough date */ ], ... },
  "calibration_context":   { "available": false, "warning": "run with calibrate=True for V13.7 horizon calibration + trust gate" },
                             // with calibrate=True: { "available": true, "summary":[per-horizon obs/pred/Brier/AUC/MACE],
                             //   "trust_gate": {"40": {"passed": false, ...}}, "isotonic_transforms": {...} }
                             // and retry_hazard_context gains p_retry_within_40d_calibrated + calibration_gate_40d + surfaced_probability_is_calibrated
  "option_overlay_research_hint": { "must_not_auto_execute": true, "research_hint": "...", "candidate_action_bias": [...] },
  "warnings": [...], "disclaimers": [...]
}
```

The machine-readable schema is at `exports/reports/statistical_context_schema.json`.
`export_universe_context_bundle` nests one envelope per ticker under
`ticker_contexts`, plus a **populated `pooled_context`** (V13.3) with, at
peer_group / sector / universe levels: a `gap_drawdown_matrix` (drawdown×gap
buckets → counts, median gap/drawdown, next-attempt success + Wilson interval,
interpretation), a `correlation` summary (Spearman of inter-attempt drawdown vs
days-to-next-touch with bootstrap CI), and an `attempt_success` classification, plus
a top-level `headline_correlation`. This is **descriptive historical evidence**
(sample sizes included; n<5 correlations suppressed), not a forward forecast.

**`retry_timing_context` (V13.3 Phase 2) — the conditional "days left" estimate.**
When the repair/retry engine is active (price below or testing MA250), this block
estimates the calendar days to the next MA250 touch four ways — historical median
gap, gap×drawdown matrix interpolation, nearest-neighbor (±2.5% drawdown band), and
a Theil-Sen robust fit — each at ALL (pooled) and peer-group scopes, conditioned on
the live drawdown-so-far and the target `transition`. Each method reports an
estimated total gap, the elapsed days subtracted, an `estimated_remaining_days`, a
rough retry date *if repair continues*, and a quality flag. The `consensus` block
reduces the non-fragile methods to a central remaining-day count + a min/max window.
The per-ticker envelope is **self-conditioned** on the ticker's own history; the
bundle's `pooled_context.retry_timing[<ticker>]` carries the richer **universe
-pooled** version. It is a **conditional range, not a forecast or a date**, and
assumes current drawdown is the maximum damage before the next retry. When the
trend engine is active (e.g. AAPL accepted above MA250) the block is a dormant stub
(`"active": false`).

**Downstream consumption (`option-mgmt-2026`):** treat the envelope as a
**read-only evidence overlay**. Respect `active_engine` (only the active engine's
metrics are authoritative; the other is gated), honor `must_not_auto_execute`,
and check `calibration_context.available` before relying on hazard probability
magnitudes.

---

## 8. Interpreting results

- **active_engine** — `repair_retry_hazard_engine` (below/testing MA250) vs
  `post_confirmation_trend_engine` (accepted above) vs `unknown_or_transition`.
- **mode_state** — coarse replay state (`accepted_above_watch`,
  `below_yearline_repair`, `failed_repair_deep_below`,
  `repair_retry_probability_building`, `transition_watch`).
- **Gated metrics** — retry-hazard, retry-timing, and trend fields are each
  populated only when their engine is active (repair vs trend); the inactive
  engine's block is a dormant stub.
- **days-left (`retry_timing_context`)** — read the `consensus` window, not a single
  number. The conditional methods (matrix-interpolation, nearest-neighbor) condition
  on the current drawdown and are usually the most relevant; the unconditional
  median and global Theil-Sen are baselines. A wide spread means thin or mixed-scope
  samples — prefer the pooled (`pooled_context.retry_timing`) view and watch the
  quality flags. It is descriptive evidence, recomputed daily as the state updates.
- **retry-hazard (V13.3 Phase 3)** — the canonical `p_retry_within_*` is the
  **empirical completed-path** estimate (how often *similar* historical states
  retouched within H trading days), tagged `probability_policy =
  v13_empirical_horizon_calibrated`, with its `reference_scope` / `reference_n`
  exposed. `hazard_today` is the logistic one-day hazard; the old saturating
  state-hold-forward curve lives under `diagnostic_model_state_hold_forward` and is
  **not** canonical. Prefer richer scopes (a wider universe lifts the
  state-conditioned scopes above the ≥25-row floor).
- **is the hazard probability trustworthy? (V13.7 / Phase 4)** — by default it is
  **uncalibrated** (`calibration_context.available=false`). Run with `calibrate=True`
  to fill `calibration_context` and a per-horizon **trust gate**; then read
  `retry_hazard_context.surfaced_probability_is_calibrated` and `calibration_gate_40d`.
  On thin single-ticker data the gate **correctly fails** (low AUC) — i.e. don't trust
  the magnitude until a wider universe (Phase 5) lets it pass. The gate's MACE is the
  **honest out-of-fold** isotonic-calibrated error (purged by transition; Phase 6) —
  `calibration_gate_40d.mace_gate_basis` shows whether it's `oof_isotonic_calibrated` or
  the `raw_reliability` fallback. Pooled (Phase 5) it passes at 10/20/40d; **60d stays a
  diagnostic abstention** (sample/regime-limited — recalibration can't fix it).

---

## 9. Validation & testing

```bash
pytest            # 22 tests: config, indicators, detection, pipeline, export, pooling, no-hardcoded-ticker
```

- `validate_ticker_sanity(result)` — dataset-independent structural guard
  (valid outcomes, monotonic rounds, attempt resets, finite distance).
- `validate_reference_parity(result, expected_*)` — optional exact comparison vs a
  frozen reference (supply your own reference values).
- `ml_feature_leakage_audit()` / `hazard_feature_leakage_audit()` — the
  anti-leakage policy tables (which features may train vs. score live).
- `tests/test_no_hardcoded_ticker.py` — fails the build if any ticker symbol is
  hardcoded as a string literal in library code.

---

## 10. Performance & scaling

A single ticker runs end-to-end in **~3.4 seconds** (down from ~56s — **~16× faster**;
uniform across tickers after vectorizing the daily replay and gating an unused ML
bootstrap). A **daily incremental step is ~1.3–1.7s** (only the new bar is scored).
See `docs/V13_performance_optimization_report.md`
for the profile, the optimization, the output-equivalence proof, and throughput
projections for daily multi-ticker / multi-sector scans (including parallelism
and incremental-update recommendations not yet implemented).

**Parallel batch runs:** `run_universe_pipeline(uni, n_jobs=N)` (or `--n-jobs N`)
runs tickers across `N` processes; `n_jobs<=0` uses all cores. Output is
**byte-identical** to the serial run (tickers are independent and deterministic),
and per-ticker failures stay isolated even if a worker process crashes. Speedup is
near-linear with cores for balanced universes — though one unusually slow ticker
caps the wall-clock (Amdahl). Measured: ~1.6× on a 2-core box for the 3-ticker
demo (the slowest ticker dominates); larger/balanced universes approach `N×`.

Rules of thumb for planning a daily scan:

- Cost scales ~linearly with `(number of tickers) × (replay span)`. Shorten
  `replay_start` for faster runs if you don't need deep daily history.
- Tickers are independent → set `n_jobs` ≈ your vCPU count.
- For daily cadence, use `incremental=True` (or `--incremental`): only the newest
  bar(s) are scored, and the cache auto-invalidates on splits/dividends or a model
  change (so output always matches a full run). Combine with `n_jobs` for the
  fastest daily scan (≈ a few minutes for a few-hundred-ticker universe).
- Cache prices nightly to avoid live rate limits.

**Optional prototype ML models (`fit_ml_models`, default `False`).** The
statistical-context envelope does **not** use the retry-timing / quality ML
predictions, so they're skipped by default — this is *the* reason a high-event
ticker like MSFT runs in ~3.6s instead of ~10s. **Default-off is not a
performance issue; it is the optimization, and it changes no output.** Enable it
(`run_ticker_pipeline(..., fit_ml_models=True)`, `run_universe_pipeline(...,
fit_ml_models=True)`, or `--fit-ml-models`) only when you actually want those
prototype predictions — research, the future pooled-hazard work, or a downstream
consumer. Cost of enabling, by ticker history:

| ticker | completed transitions | off | on |
|---|---|---|---|
| MSFT | 23 (≥20 → bootstrap runs) | 3.66s | 10.02s (**+6.4s**) |
| AAPL | 15 (<20 → short-circuits) | 3.19s | ~3.21s (negligible) |

The 300-fit timing bootstrap only runs for tickers with ≥20 completed
transitions; below that it short-circuits. The envelope is **byte-identical**
whether on or off — when on, the predictions are attached to
`result.manifest["ml_models"]`.

---

## 11. Troubleshooting / FAQ

| Symptom | Cause / fix |
|---|---|
| `could not load price data ... cache: no data` | No `data/price_cache/{TICKER}.csv`; add a CSV or use `provider="auto"` with `[live]`. |
| HTTP 429 on live pulls | Yahoo rate-limits shared IPs; cache nightly or use a paid feed. |
| `p_retry_within_*` is `null` | Expected — hazard is gated off while the trend engine is active. |
| Canonical `p_retry_within_*` looks saturated (≈1.0) | You're likely reading `diagnostic_model_state_hold_forward` (the demoted, saturating model curve). The **canonical** `p_retry_within_*` is the empirical estimate and is not pinned at 1.0 (V13.3 Phase 3); see §7–§8. |
| Is the hazard probability calibrated/trustworthy? | Default = no (`calibration_context.available=false`). Run `calibrate=True`; check `surfaced_probability_is_calibrated` + `calibration_gate_40d`. On thin single-ticker data the gate fails by design — needs a wider universe (Phase 5). |
| Can I get the probability a retry **succeeds** (reclaims and holds)? | Different question from `p_retry_within_*` (which is retry **occurrence**/timing). Retry **success** is a **prototype** only: run `fit_ml_models=True` and read `result.manifest["ml_models"]` (`p_next_retry_success` / `quality_bucket`). It is **uncalibrated, not in the envelope, and barely beats the base rate** — treat as exploratory. The plan to make it trustworthy (pool → readiness features → empirical baseline → classifier blend → calibrate + trust gate) and its validation are in `docs/research/01_retry_success_probability_2026-06-08.md`. |
| A ticker shows `status: "error"` in the manifest | Per-ticker failure isolation; read `manifest.error` / `traceback`. |

---

## 12. Deployment options

The engine is a **backend batch/CLI** that reads config + prices and writes JSON
artifacts. **A frontend is not included** (pending) — a future API/UI would serve
the `exports/` artifacts. Recommended boundary:

```text
[ scheduler ] -> [ yearline_universe batch run ] -> [ exports/*.json artifacts ] -> [ (future) API / frontend ]
```

### 12.1 Local / CLI + cron

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[live]"
python scripts/run_universe_mvp.py config/universe_mega_cap_ai_infra.yaml --provider auto
```

Daily at 02:30 via cron (writes timestamped logs):

```cron
30 2 * * *  cd /opt/yearline_universe && /opt/yearline_universe/.venv/bin/python \
            scripts/run_universe_mvp.py config/universe_mega_cap_ai_infra.yaml --provider auto \
            >> /var/log/yearline/scan_$(date +\%Y\%m\%d).log 2>&1
```

### 12.2 Docker

A `Dockerfile` ships at the repo root.

```bash
docker build -t yearline-universe:13.1 .

# Run the daily scan; persist exports (and optionally the price cache) on the host.
docker run --rm \
  -v "$PWD/exports:/app/exports" \
  -v "$PWD/data/price_cache:/app/data/price_cache" \
  yearline-universe:13.1 config/universe_mega_cap_ai_infra.yaml --provider cache
```

For live data, build with the live extra (uncomment `pip install ".[live]"` in the
Dockerfile) and pass `--provider auto`. Schedule by invoking `docker run` from host
cron, or run a scheduler sidecar.

### 12.3 VPS backend (planned target)

On a small VPS (see the performance report for sizing):

1. **Provision:** install Docker or a Python 3.11 venv; create `/opt/yearline_universe`.
2. **Deploy:** `git pull` (or copy the repo); `pip install -e ".[live]"` or `docker build`.
3. **Persistent storage:** keep `data/price_cache/` and `exports/` on a persistent
   volume (and back up `exports/` if downstream depends on history).
4. **Schedule** with a systemd timer (more robust than cron for logging/retries):

   `/etc/systemd/system/yearline-scan.service`
   ```ini
   [Unit]
   Description=Yearline universe daily scan
   [Service]
   Type=oneshot
   WorkingDirectory=/opt/yearline_universe
   Environment=PROVIDER=auto
   ExecStart=/opt/yearline_universe/.venv/bin/python scripts/run_universe_mvp.py config/universe_mega_cap_ai_infra.yaml --provider ${PROVIDER}
   ```
   `/etc/systemd/system/yearline-scan.timer`
   ```ini
   [Unit]
   Description=Run yearline scan daily
   [Timer]
   OnCalendar=*-*-* 02:30:00
   Persistent=true
   [Install]
   WantedBy=timers.target
   ```
   ```bash
   sudo systemctl enable --now yearline-scan.timer
   ```
5. **Serving results (future frontend):** point a lightweight read-only API (FastAPI/
   nginx static) at `exports/` so a future UI can read the latest envelopes/bundle.
   The engine itself stays a backend job.

### 12.4 Cloud scheduled-job pattern (generic)

For a managed cloud deployment, the portable pattern is **container + scheduler +
object storage**:

```text
[ cron/scheduled trigger ]
        -> [ container job: run_universe_mvp.py --provider auto ]
              -> writes exports/ to a mounted dir
                    -> sync exports/ to object storage, partitioned by as_of date
```

- Package the image (§12.2), push to a registry, and run it as a **scheduled batch
  job** (any "run this container daily" service: managed cron jobs, serverless
  container jobs, or a Kubernetes `CronJob`).
- Make runs **idempotent**: write artifacts under an `as_of=YYYY-MM-DD/` prefix in
  object storage so re-runs overwrite cleanly and history is preserved.
- Inject data credentials/config via environment variables or secrets; mount or
  sync the price cache so cold containers don't re-download everything.
- Keep the container stateless: all durable output goes to object storage; logs to
  the platform's log sink.

---

*See `docs/V13_universe_statistical_context_engine_development_spec.md` for the
architecture spec and roadmap, and `docs/V13_performance_optimization_report.md`
for the performance analysis. Educational research only — not financial advice.*
