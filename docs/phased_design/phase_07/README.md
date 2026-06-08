# Phase 7 — Discrimination, not recalibration (report-driven)

**Status:** ◐ IN PROGRESS — **PR-A (path features) + PR-B/C (direct classifier) + PR-D (cross-sectional features) DELIVERED**; PR-E next.
**Part of:** the V13.3 phased roadmap (see `../README.md`)
**Source:** `docs/uploaded/V12_V13_AUC_MACE_improvement_research_report.md`
**Theme:** Phase 6 proved 60d is **sample/regime-limited, not a calibration problem** —
so the next gains come from better **features + a direct horizon classifier**, i.e.
*discrimination (AUC)*, not more recalibration.

> Educational research only. Output is evidence context; not a trading signal.

---

## 1. The Phase 7 thesis (and the sequence)

The empirical estimator + pooling (Phases 3–5) already clear the gate at 10/20/40d, but
it conditions only on **static** buckets (distance, drawdown, days-since-touch). It
cannot represent whether a repair is *improving*. Per the research report, the
highest-ROI levers (in order) are: **path-dynamic features → direct horizon classifiers
→ peer/sector features → episode-aware validation + hierarchical shrinkage**. We lead
with features because they are the cheapest, largest AUC win.

Sequenced as small PRs:

| PR | Deliverable | Status |
|---|---|---|
| **A** | `features.py` — leakage-safe path-dynamic repair features | ✅ DELIVERED |
| **B/C** | `labels.py` (direct horizon labels + censoring) + `models.py` (regularized-logistic direct horizon classifier; GBM diagnostic) | ✅ DELIVERED |
| **D** | `cross_sectional.py` — peer / sector / market (cross-sectional) regime features | ✅ DELIVERED |
| E | episode-aware validation (leave-one-*ticker*-out) + row weighting + hierarchical shrinkage | ☐ next |

> Tempered expectations (see the report review): pooled AUC is already **0.74–0.82** at
> ≤40d, so expect *marginal* AUC + better **out-of-sample generalization**, not another
> 0.46→0.78 jump. Model complexity stays conservative — the effective sample is ~**162
> independent episodes**, not 4,765 daily rows, so regularized logistic is primary and
> GBM is diagnostic-only. 60d is expected to stay diagnostic.
>
> **PR-B/C result (§3) confirms exactly this:** the classifier wins by **+0.005…+0.024 AUC**
> at 20/40/60d and *improves* long-horizon calibration — a marginal, honest win, not a leap.
>
> **PR-D result (§4):** cross-sectional regime features *stack* a further **+0.018 AUC at 40d**
> (MACE 0.067→0.043) — but, against the going-in hypothesis, do **not** rescue 60d. The win is
> concentrated at 40d; honest negatives elsewhere.

## 2. PR-A — `features.py` (delivered)

Leakage-safe, backward-looking features (every column at date *t* uses only data ≤ *t*;
a test asserts it by comparing full-series vs truncated-series values):

- **time-series** (`build_price_path_features`): trailing returns (5/10/20d); short-MA
  trend (MA20/MA50 distance, above-flags, slope); **distance-to-yearline dynamics**
  (change 5/10/20d + slope) and the de-correlated `repair_gap_pct`; volatility level,
  252-day percentile, and 10-vs-50d range compression.
- **repair-relative** (`repair_path_features_at`, given the latest touch): drawdown so
  far, **bounce-from-low**, close-position-in-repair-range, reclaim speed, consecutive
  days-below-MA250.

### Why this matters — MSFT, 2026-06-05 (real)

The static estimator sees "10% below MA250." The path features add what it's blind to:

| feature | value | reading |
|---|---|---|
| `repair_gap_pct` | 10.10 | 10% below the yearline |
| `close_position_in_repair_range` | **0.04** | sitting **at the repair low**, not bouncing |
| `bounce_from_low_pct` | 0.55 | barely off the low |
| `distance_to_ma250_change_10d` | **−0.31** | gap *widening*, not closing |
| `return_5d` | −7.5 | sharp recent drop |
| `realized_vol_20d_pctile_252d` | **0.92** | volatility near a 1-yr high (repair-unfriendly) |

⇒ a **low-readiness** repair — a distinction the static buckets cannot make, and exactly
the signal a horizon classifier needs to rank "bouncing" repairs above "still falling" ones.

## 3. PR-B/C — direct horizon classifier (delivered)

### 3.1 What it is

Two new modules, **capability-before-consumer** (not wired into the envelope):

- **`labels.py`** — `build_direct_horizon_dataset(tickers_data)` builds the per-row
  modeling table. For each **completed** at-risk day it joins:
  * the Phase-7 **path-dynamic features** (`features.py`, leakage-safe),
  * the de-correlated static repair state already on the hazard panel,
  * **direct horizon labels** `y_H = 1[remaining_trading_days_to_retry ≤ H]`. Labels are
    leakage-safe *by construction*: only **completed** transitions have an observed event
    day, so a row is only labelled where its outcome is actually known — live/censored
    transitions are excluded, never silently labelled negative.
  * the empirical estimator's **leave-one-transition-out** prediction (`empirical_pred_H`)
    on the *same* rows, so the classifier can be scored head-to-head on identical data.
  * `MODEL_FEATURE_COLUMNS` = de-correlated static state + path dynamics (excludes raw MA
    levels and `required_rebound`, collinear with `repair_gap_pct`).
- **`models.py`** — the classifier and the evaluation harness:
  * **primary** = `make_direct_horizon_logistic` (median-impute → standardize → **L2
    logistic**): linear, low-variance, probabilities stay meaningful for a MACE read.
  * **diagnostic-only** = `make_direct_horizon_gbm` (shallow, sub-sampled GBM): an
    upper-bound on non-linear signal, **never promoted** (it overfits ~10² episodes).
  * `evaluate_direct_horizon_models` runs **episode-aware CV = GroupKFold purged by
    `transition_key`** (an entire transition is in train or test, never split, so
    autocorrelated within-episode rows can't leak). Because `empirical_pred_H` is itself
    leave-one-transition-out, the comparison is **held-out vs held-out**.
  * `fit_direct_horizon_models` fits the final per-horizon logistic on all rows (for
    PR-D/E to score the live state once a horizon is promoted).

A horizon is `promote_recommended` only if the classifier **beats the empirical AUC**
*and* its MACE is **not worse** by more than a small tolerance (0.02) *and* there are
≥50 rows. MACE uses the identical 10-bin / ≥10-per-bin definition as the Phase-4 gate.

### 3.2 The head-to-head — real universe, episode-aware OOF

`config/universe_mvp_software_like.yaml` (9 tickers) → **4,765 at-risk rows over 162
transitions**. GroupKFold (k≤5) purged by transition; AUC/MACE on the rows where both
estimators predict:

| H | base rate | empirical AUC | **logistic AUC** | GBM (diag) | ΔAUC | empirical MACE | **logistic MACE** | ΔMACE | promote |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 0.30 | **0.816** | 0.802 | 0.842 | −0.014 | 0.036 | 0.046 | +0.010 | ❌ |
| 20 | 0.45 | 0.779 | **0.785** | 0.788 | **+0.005** | 0.048 | 0.059 | +0.010 | ✅ |
| 40 | 0.62 | 0.762 | **0.775** | 0.774 | **+0.013** | 0.077 | **0.067** | −0.010 | ✅ |
| 60 | 0.72 | 0.738 | **0.762** | 0.814 | **+0.024** | 0.109 | **0.072** | −0.037 | ✅ |

**Reading the result:**

- **40d (the headline).** The classifier beats the empirical AUC (0.775 vs 0.762) **and
  improves calibration** (MACE 0.067 vs 0.077). This is the promotion case the phase set
  out to prove — path dynamics add discrimination exactly where the static buckets start
  to thin out.
- **20d.** A narrow AUC win (+0.005); MACE marginally worse but inside tolerance → promotable.
- **10d.** The **empirical estimator wins** (0.816 vs 0.802). At the short horizon, recent
  static state is abundant and highly predictive, and the linear model adds nothing — an
  honest negative. 10d stays with the empirical estimator.
- **60d.** The classifier wins on AUC (+0.024) and *substantially* improves calibration
  (MACE 0.072 vs 0.109) — its smoother probability surface even clears the 0.10 gate the
  bucket estimator fails at 60d. Treat cautiously regardless: Phase 6 showed 60d is
  sample/regime-limited and the base rate is already 0.72.
- **GBM (diagnostic).** Shows non-linear headroom at 10d (0.842) and 60d (0.814) but ties
  logistic at 20/40d → no evidence that justifies its overfit risk on 162 episodes. It
  stays diagnostic-only, never promoted.

Net: the predicted **marginal AUC gain + better long-horizon calibration**. The classifier
earns promotion at **20/40d** (plus a 60d calibration win); 10d remains empirical.

## 4. PR-D — cross-sectional (peer/sector/market) features (delivered)

### 4.1 What it is

`src/yearline_universe/cross_sectional.py` adds **leakage-safe regime context** the
per-name path features can't see — every value at date *t* combines each ticker's
*contemporaneous* (≤ *t*) state, so there is no look-ahead (a whole-panel truncation test
asserts row-*t* is identical when computed on a panel truncated at *t*):

- **market regime** (broad ETF proxy, default QQQ): distance to its own yearline,
  above/below flag, 20d return, 20d distance-change.
- **breadth / dispersion** over the equity names: fraction above their yearline,
  cross-sectional 20d-return dispersion.
- **peer-relative strength**: this name's 20d return and yearline distance *minus the
  equity cross-section median* (is it leading or lagging its peers?).

`labels.py` merges these by `(ticker, as_of_date)` and exposes `MODEL_FEATURE_COLUMNS_WITH_XS`;
`models.py` gains a `feature_columns` arg + `compare_feature_sets`, so the **path-only vs
path+cross-sectional** ladder runs through the *same* episode-aware head-to-head.

### 4.2 The ladder — real universe, episode-aware OOF

Same 4,765 rows / 162 transitions; logistic OOF AUC/MACE per feature set (empirical
baseline shown for reference):

| H | empirical AUC | path AUC | **path+xs AUC** | xs ΔAUC | path MACE | **path+xs MACE** | best set |
|---|---|---|---|---|---|---|---|
| 10 | **0.816** | 0.802 | 0.804 | +0.002 | 0.046 | 0.064 | empirical still best |
| 20 | 0.779 | **0.785** | 0.783 | −0.001 | 0.059 | 0.056 | path |
| 40 | 0.762 | 0.775 | **0.794** | **+0.018** | 0.067 | **0.043** | **path+xs** |
| 60 | 0.738 | **0.762** | 0.762 | 0.000 | 0.072 | 0.097 | path (xs hurts MACE) |

**Reading the result:**

- **40d — the standout.** Cross-sectional regime lifts AUC **+0.018 (0.775 → 0.794)** and
  *sharply* improves calibration (MACE **0.067 → 0.043**). It **stacks** on PR-C: the full
  stack now beats the empirical baseline by **+0.032 AUC** at 40d (0.762 → 0.794) with
  better calibration — the strongest single result of Phase 7.
- **60d — hypothesis refuted (honestly).** Regime features were *expected* to help the
  regime-limited horizon most; instead they add **zero AUC** and **worsen MACE**
  (0.072 → 0.097). A *contemporaneous* cross-sectional snapshot doesn't capture 60d's
  *temporal* regime drift, and over this tiny universe breadth is coarse and the market
  proxy is near-collinear with the mega-cap names. 60d stays diagnostic — cross-sectional
  features are **not** its fix at this scale.
- **10d / 20d — flat.** Where short-term own-state is already abundant and predictive, the
  regime context adds nothing (10d AUC +0.002 but MACE worse; 20d a wash). 10d stays empirical.

Net: **promote path+xs at 40d; keep path-only (or empirical) elsewhere.** Honest, narrow,
stackable. **Caveat:** the win is real but the universe is small (5–6 equities + 3 ETFs) —
breadth is coarse and the market proxy overlaps the names; a **wider / multi-sector
universe** is the lever most likely to extend the cross-sectional gains (and the only
plausible route to a 60d improvement). That is a *data* unlock, gated on more uploads.

## 5. Files changed

**PR-B/C:**
- `src/yearline_universe/labels.py` (new) — `build_direct_horizon_dataset`,
  `MODEL_FEATURE_COLUMNS`.
- `src/yearline_universe/models.py` (new) — `make_direct_horizon_logistic`,
  `make_direct_horizon_gbm`, `fit_direct_horizon_models`,
  `evaluate_direct_horizon_models`, `build_and_evaluate_direct_horizon_models`,
  `DIRECT_MODEL_VERSION`.
- `tests/test_models.py` (new, +5) — structure + planted-signal AUC; a
  **transition-purged-CV-is-not-optimistic** test (a within-episode label leak does *not*
  inflate OOF AUC to ~1.0 under transition-purged folds); the PR-D feature-set ladder;
  a scorable fitted pipeline; empty-input grace.

**PR-D:**
- `src/yearline_universe/cross_sectional.py` (new) — `build_cross_sectional_features`,
  `CROSS_SECTIONAL_FEATURE_COLUMNS`.
- `src/yearline_universe/labels.py` — `include_cross_sectional` merge +
  `MODEL_FEATURE_COLUMNS_WITH_XS`.
- `src/yearline_universe/models.py` — `feature_columns` arg + `compare_feature_sets`,
  `build_and_compare_cross_sectional`.
- `tests/test_cross_sectional.py` (new, +3) — shape, peer-relative-is-centered invariant,
  **whole-panel no-future-leakage**.
- `src/yearline_universe/__init__.py` — exports.

Full per-file suite green; no-hardcoded-ticker guard holds. **No existing output changes**
(capability before consumer).

## 6. Reproduce

```python
from yearline_universe import load_universe_config
from yearline_universe.ticker_pipeline import _build_foundation
from yearline_universe.models import (
    build_and_evaluate_direct_horizon_models, build_and_compare_cross_sectional)

uni = load_universe_config("config/universe_mvp_software_like.yaml")
pooled = {}
for tc in uni.tickers:
    f = _build_foundation(tc, uni, cache_dir="data/price_cache", provider="cache")
    pooled[tc.ticker] = {"peer_group": tc.peer_group, "price_df": f["price_df"],
                         "recovery_table": f["recovery"], "live_diagnostic": f["live"]}

# PR-B/C — classifier vs empirical baseline
hh = build_and_evaluate_direct_horizon_models(pooled, horizons=[10, 20, 40, 60])

# PR-D — path-only vs path+cross-sectional ladder
ladder = build_and_compare_cross_sectional(pooled, horizons=[10, 20, 40, 60])
for r in ladder["horizons"]:
    print(r["horizon_days"], r["sets"]["path"]["auc"],
          r["sets"]["path_plus_xs"]["auc"], r.get("xs_lift_auc"), r["best_set"])
```

## 7. Decision gate → PR-E

PR-A/B/C/D have proven the *capability*: the full feature stack (path + cross-sectional)
beats the empirical baseline most clearly at **40d** (AUC 0.762 → 0.794, MACE 0.077 → 0.043),
with smaller/contingent wins at 20/60d and 10d staying empirical. The honest negative — the
cross-sectional snapshot does **not** rescue 60d — points PR-E at *validation rigor and
generalization*, not more feature surface:

- **Leave-one-*ticker*-out** CV (not just leave-one-transition-out) — the true test of
  whether a model generalizes to an unseen name, and the right power check on ~162 episodes.
- **Row weighting** (≈ 1/√episode_rows) so long dormant episodes don't dominate the fit.
- **Hierarchical shrinkage** blending the classifier with the empirical estimator
  per-horizon (lean on the classifier where it wins, the estimator where it doesn't).
- **Richer calibration metrics** (ECE / quantile-MACE / reliability slope) beyond binned MACE.

Only after PR-E does a winning, per-horizon-gated model get wired into `retry_hazard_context`
behind the same trust gate as the empirical estimator (likely: **classifier at 40d**,
empirical at 10d, blend at 20/60d). 60d stays diagnostic. The largest remaining lever is a
**wider / multi-sector universe** (a data unlock), which would most extend the
cross-sectional gains. Educational research only; not a trading signal.
