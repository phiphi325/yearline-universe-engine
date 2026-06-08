# Phase 7 — Discrimination, not recalibration (report-driven)

**Status:** ◐ IN PROGRESS — **PR-A (path-dynamic features) DELIVERED**; PR-B/C/D/E next.
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
| B/C | `labels.py` (direct horizon labels + censoring) + `models.py` (regularized-logistic direct horizon classifier; GBM diagnostic) | ☐ next |
| D | peer / sector / market features (cross-sectional) | ☐ |
| E | episode-aware validation (leave-one-episode/ticker-out) + row weighting + hierarchical shrinkage | ☐ |

> Tempered expectations (see the report review): pooled AUC is already **0.74–0.82** at
> ≤40d, so expect *marginal* AUC + better **out-of-sample generalization**, not another
> 0.46→0.78 jump. Model complexity stays conservative — the effective sample is ~**162
> independent episodes**, not 4,765 daily rows, so regularized logistic is primary and
> GBM is diagnostic-only. 60d is expected to stay diagnostic.

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

These are **not yet consumed** by the envelope (capability before consumer); the direct
horizon classifier that uses them is PR-B/C. No existing output changes.

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

## 3. Files changed (PR-A)

- `src/yearline_universe/features.py` (new) — `build_price_path_features`,
  `repair_path_features_at`, `PATH_FEATURE_COLUMNS`, `REPAIR_PATH_FEATURE_KEYS`.
- `src/yearline_universe/__init__.py` — exports.
- `tests/test_features.py` (new, +4) — shape, **no-future-leakage**, V-shape repair,
  real-ticker finiteness. Full suite green; no-hardcoded-ticker guard holds.

## 4. Reproduce

```python
from yearline_universe import load_universe_config, build_price_path_features
from yearline_universe.ticker_pipeline import _build_foundation
uni = load_universe_config("config/universe_mvp_software_like.yaml")
f = _build_foundation(uni.get_ticker("MSFT"), uni, cache_dir="data/price_cache", provider="cache")
feats = build_price_path_features(f["price_df"], f["study"])   # leakage-safe, per-date
```

## 5. Decision gate → PR-B/C

With the feature foundation in place, build **direct horizon labels** (`y_H` per active
repair row, properly censored) and a **regularized-logistic direct horizon classifier**
that consumes these features, and measure its AUC/MACE **against the empirical estimator
baseline** under **episode-aware** (leave-one-episode/ticker-out) validation. Promote it
only if it beats the baseline without calibration regression.
