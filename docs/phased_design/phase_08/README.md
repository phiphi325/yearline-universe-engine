# Phase 8 — Retry-success (Track A): RS-1 delivered

**Status:** ◐ IN PROGRESS — **RS-1 (success labels + empirical base-rate estimator) DELIVERED**; RS-2/3/4 next.
**Part of:** the planner roadmap — see [`../planner/01_retry_success_plan.md`](../planner/01_retry_success_plan.md).
**Source analysis:** [`../../research/01_retry_success_probability_2026-06-08.md`](../../research/01_retry_success_probability_2026-06-08.md).
**Theme:** make retry **success** (*given an attempt, does it reclaim and **hold**?*) trustworthy —
distinct from the mature retry **occurrence** estimator (`P(retry ≤ H)`).

> Educational research only. Output is evidence context; not a trading signal.

---

## 1. What RS-1 builds

`src/yearline_universe/success_labels.py` (new), **capability-before-consumer** (nothing surfaced):

- **`build_success_dataset(tickers_data)`** — a leakage-safe, **attempt-level** success dataset pooled
  across the universe. One row per **completed** recovery transition; `y_success` = 1 iff the next
  attempt reclaimed and held (`event_detection.classify_attempt_outcome_v10_parity → "success"`,
  surfaced as `recovery_table.next_attempt_success`). **Pending/unresolved attempts are excluded**
  (`next_attempt_pending`) — censoring is leakage-safe by construction.
- **`build_empirical_success_reference` / `empirical_success_probability_for_row`** — the empirical
  "of *similar* historical attempts, what fraction succeeded?" estimator, mirroring the Phase-3 horizon
  estimator: a **bucket scope-ladder** (`group_transition_drawdown → group_transition →
  transition_drawdown → transition → group_drawdown → group → drawdown → universe_all`) with **Bayesian
  shrinkage** to the universe success rate. Floor `SUCCESS_MIN_REFERENCE_N = 15` (lower than the
  horizon estimator's 25 — attempts are scarcer); prior strength 6. This is the **calibrated baseline**
  the RS-2 classifier must beat.

## 2. The dataset (real universe — 9 tickers)

`config/universe_mvp_software_like.yaml` → **162 completed attempts**, success **base rate 0.352**
(57/162; matches the V12.10 benchmark ~0.354). Real structure exists in the *base rate*:

| Cut | Success rate (n) |
|---|---|
| ai_accelerator | 0.33 (15) |
| etf_context | 0.51 (35) |
| mega_cap_software_like | 0.30 (112) |
| transition 1→2 | 0.32 (59) · 2→3 0.41 (39) · 3→4 0.43 (23) · 4→5 0.38 (13) · later (6→7…) 0.00–0.20 (n≤5) |

## 3. The honest result — the empirical estimator does **not** beat the flat base rate (yet)

Leave-one-attempt-out, the empirical base-rate-by-bucket estimator vs predicting the flat 0.352:

| Metric | Empirical estimator | Flat base rate |
|---|---|---|
| **Brier** | **0.2320** | 0.2281 |
| **AUC** | **0.490** | 0.500 (by definition) |

It **does not beat the base rate** (Brier marginally worse; AUC ≈ random). **The static recovery-state
buckets (drawdown / below-MA250 depth / attempt# / transition / group) carry no out-of-sample signal
for *success*** at this sample size — even though the *base rate itself* varies by group/transition,
that variation doesn't generalize attempt-to-attempt under leave-one-out. (Scope usage: most queries
resolve at `group_transition` (97) or `group_drawdown` (28) / `transition` (24); the bucket-specific
drawdown scope rarely cleared the floor.)

This is the **honest "not yet"** the research note predicted, and it is the *point* of RS-1: it
establishes the **bar** — **AUC ≈ 0.49, Brier ≈ 0.228** — that RS-2's richer features must clear.

## 4. Files changed

- `src/yearline_universe/success_labels.py` (new) — `build_success_dataset`,
  `build_empirical_success_reference`, `empirical_success_probability_for_row`,
  `SUCCESS_STATE_FEATURES`, `SUCCESS_PROB_POLICY`.
- `src/yearline_universe/__init__.py` — exports.
- `tests/test_success_labels.py` (new, +4) — label correctness + **pending censoring**, estimator
  **signal** (shallow-drawdown ranks above deep), **shrinkage** (tiny buckets pulled to the universe
  rate), exclude-key + empty-input grace.
- `artifacts/` — `rs1_empirical_success_vs_base_rate.json`, `rs1_success_dataset.csv`,
  `rs1_success_rate_by_group.csv`, `rs1_success_rate_by_transition.csv`.

No existing output changes (capability before consumer); full per-file suite green;
no-hardcoded-ticker guard holds.

### Validation — event-detection alignment

Because RS-1's labels derive from the strict/loose attempt detector → canonical events, an audit
confirms those attempts are **properly aligned and time-correctly processed per ticker**:
[`event_detection_alignment_audit.md`](event_detection_alignment_audit.md). Across all 9 tickers:
chronological integrity is exact (monotonic, no duplicate bars, correct `date ↔ trading_loc` mapping,
no cross-ticker mixing), strict attempts are preserved 1:1 (184→184 strict-quality anchors), loose hits
merge correctly within the 2-day window (0 span warnings), rounds/attempts reset correctly after a
success, and strict/loose disagree on outcome in only 1 of 153 merged clusters. One non-blocking
hardening note (an unenforced single-ticker precondition in `build_canonical_events`) is recorded there.
Artifacts: `artifacts/event_detection_alignment_audit.{csv,json}`.

## 5. Reproduce

```python
from yearline_universe import load_universe_config
from yearline_universe.ticker_pipeline import _build_foundation
from yearline_universe.success_labels import (
    build_success_dataset, build_empirical_success_reference, empirical_success_probability_for_row)

uni = load_universe_config("config/universe_mvp_software_like.yaml")
pooled = {tc.ticker: {"peer_group": tc.peer_group,
                      "recovery_table": _build_foundation(tc, uni, cache_dir="data/price_cache",
                                                          provider="cache")["recovery"]}
          for tc in uni.tickers}
ds = build_success_dataset(pooled)                       # 162 completed attempts; base rate 0.352
ref = build_empirical_success_reference(ds)
p = empirical_success_probability_for_row(ds.iloc[0].to_dict(), ref,
                                          exclude_transition_key=ds.iloc[0]["transition_key"])
```

## 6. Decision gate → RS-2

The bar is set: **beat AUC ≈ 0.49 / Brier ≈ 0.228** under leave-one-*ticker*-out. RS-2 builds the
regularized-logistic **success classifier** on the **readiness (Phase-7 path) + cross-sectional**
features the static buckets lack, head-to-head vs this RS-1 baseline. Honest expectation: success may
be only weakly predictable from price-path features at this sample — RS-2 will report the lift (or its
absence) plainly, and the dominant lever remains **more labelled attempts** (a wider/multi-sector
universe + deeper history). Promotion (RS-4 surfacing) only if a horizon clears the trust gate.
