# Phase 11 — Trend outlook (Track D): TO-0 + TO-1 delivered

**Status:** ◐ IN PROGRESS — **TO-0 (coverage & hygiene) + TO-1 (score resolution) DELIVERED**; TO-2…TO-4
(regime probability → forward label → calibration/gate) remain.
**Spec:** [`../planner/05_trend_outlook_plan.md`](../planner/05_trend_outlook_plan.md).
**Analysis:** [`../../research/03_trend_mode_scoring_review_and_enhancement_2026-06-08.md`](../../research/03_trend_mode_scoring_review_and_enhancement_2026-06-08.md)
(V12/V13 review) + [`../../research/04_trend_mode_sota_review_2026-06-08.md`](../../research/04_trend_mode_sota_review_2026-06-08.md) (SOTA).

> Educational research only. The trend scores are a descriptive evidence overlay (not yet a gated
> probability — that is TO-3/TO-4); not financial advice.

---

## Why
The 2026-05-29 demo run showed 5–6 of 9 names in trend mode, and the trend engine (a faithful port of V12
Module G) was **hand-tuned, saturated, collinear, and partly orphaned**. With Track A's Part A fix
withholding the retry-success composite for trend names, the trend scores are now their **primary surfaced
context** — so they had to earn resolution and coverage.

## TO-0 — coverage & hygiene (`semantic.py`, `context_export.py`, `trend.py`)
- **Handoff coverage.** `build_semantic_history` now **promotes** `unknown_or_transition` bars to
  `post_confirmation_trend_engine` wherever a computed post-confirmation trend state exists (authoritative:
  `trend.py` only emits a state when price is above MA250). Fixes the orphaning of clearly-trending names.
- **Distance threading.** `trend_context` now surfaces `distance_to_ma250_pct` (read from the existing
  replay column — *not* re-merged, which would `_x/_y`-collide and null both contexts' distance).
- **State split.** `_assign_state` returns a distinct **`indeterminate_trend`** for the catch-all (no longer
  mislabeled `early_confirmation`).

## TO-1 — score resolution (`trend.py`)
Replaced the fixed-denominator clip-then-mean scores (which pegged at ≈1.0) with **bounded SOTA indicators
that spread** (research 04): Kaufman **efficiency ratio**, trend **R²** (log-price linearity), Wilder
**ADX**, and a Lo-MacKinlay **variance-ratio** persistence proxy — all vectorized. The two quality scores
now use **disjoint feature bases**: `trend_quality` = strength/persistence (ER, R², ADX, VR);
`pullback_quality` = depth/recovery (drawdown-from-peak, MA50 position).

## Results (2026-05-29 real universe; `artifacts/to01_trend_scores_2026-05-29.csv`)

| | before (V12 port) | after (TO-0/TO-1) |
|---|---|---|
| GOOGL engine | `unknown_or_transition` (orphaned) | **`post_confirmation_trend_engine`** ✅ |
| trend names routed to trend engine | 5 of 6 | **6 of 6** |
| `trend_quality` across trend names | ≈1.0 (saturated: 0.83–1.00) | **spreads 0.384 → 0.757** |
| `corr(trend_quality, pullback_quality)` | ≈1.0 (collinear) | **0.27–0.48** |
| `trend_context.distance_to_ma250_pct` | absent / `null` | populated (AAPL +24.0, GOOGL +37.8) |

Per-name `trend_quality` now: NVDA **0.384** (choppy pullback, efficiency ratio 0.15) < AMZN 0.612 <
GOOGL 0.637 < XLK 0.708 < QQQ 0.722 < AAPL **0.757** (clean trend) — a usable ranking where the old scores
gave none.

## Acceptance
- New `tests/test_trend.py` (5) + `tests/test_semantic.py` (3): bounded indicators, unit-interval scores,
  de-saturation (clean ≫ choppy), de-collinearization, `indeterminate_trend` fallback, handoff promotion,
  distance survives the merge. **Full per-file suite green (23 files).** Envelope changes are additive
  (`distance_to_ma250_pct` added to trend_context; repair/occurrence blocks unchanged).

## Reproduce
```bash
python3 docs/reports/demo/run_asof_2026-05-29.py   # regenerates the envelopes used above
```

## Still capability, not consumer
TO-1 makes the scores **discriminating and descriptive** — they are **not** yet a calibrated, gated
probability. TO-2 (regime probability) → TO-3 (forward trend label + LOTO validation) → TO-4 (calibration +
**resolution-floor** gate + opt-in `trend_outlook_context`) remain, per the spec.

*Files: `src/yearline_universe/trend.py`, `semantic.py`, `context_export.py`; `tests/test_trend.py`,
`tests/test_semantic.py`. Educational research only; not financial advice.*
