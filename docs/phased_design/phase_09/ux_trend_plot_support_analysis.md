# Phase 9 UX note — does the V13.8 contract support the V12-style trend plot?

**Date:** 2026-06-09 · **Question:** can the option-mgmt frontend render a *trend plot* like the uploaded
V12 dashboards, using the **current Phase 9 deliverable** (the `YearlineContext` adapter)?
**Short answer: not the time-series plot — by design. It can power a rich current-state card, but the plot
needs a separate, thin "trend series" export (recommended below).** Educational research only.

---

## 1. What the V12 dashboard "trend plot" actually is — **time series**

The uploaded V12 daily/post-confirmation dashboards (`docs/uploaded/yearline_v12_daily_dashboard_report_*`)
plot **histories over `as_of_date`**, not a single snapshot. From the V12 notebook's plotting cells, the
trend visual is built from:

- **price + moving averages over time:** `Close`, `MA20`, `MA50`, `MA250` (the post-confirmation trend chart);
- **distance-to-MA250 over time:** `distance_to_ma250_pct` vs `as_of_date` (with a 0% line = the yearline);
- **trend-score history:** `trend_quality / pullback_quality / overextension / deterioration` over time;
- **regime context over time:** repair vs trend bands, `drawdown_so_far_pct`, daily `hazard_today`, and the
  `P(retry ≤ H)` curve.

Every one of these is a **sequence** (one value per day across the replay window). A line/area plot is
inherently a time series.

## 2. What the V13.8 `YearlineContext` provides — a **scalar snapshot**

The Phase 9 contract (`adapter.to_yearline_context`) is a **flat, point-in-time value object** — the
*current* state only, deliberately lean (it is the **engine's gated decision input**, not a chart feed):

| group | fields (all scalar / current) |
|---|---|
| identity | `as_of, ticker, schema_version, model_stack_version, adapter_version` |
| regime | `repair_active, distance_to_ma250_pct, required_rebound_to_ma250_pct, post_confirmation_trend_state` |
| gated occurrence | `p_retry{10,20,40,60}` (one number each), `p_retry_basis`, `gate_passed{…}` |
| timing | `days_to_touch_central / low / high` (three numbers) |
| gated success | `p_success, success_gate_passed, p_successful_reclaim{…}` |
| provenance/safety | `reference_scope, is_stale, must_not_auto_execute` |

There is **no array / history field** anywhere in it. So:

> **Verdict.** The current Phase 9 deliverable **cannot** render the V12 time-series trend plot. It carries
> *today's* values, not the path. It **can** power a **current-state evidence card** (see §4).

This is **intentional**, not a gap in the build: the integration boundary (assessment §4, ADR-0005 in
option-mgmt) keeps the value object the pure engine consumes **small and series-free**. A plot is a
**read-only UI concern** (the OM-Y3 panel), separate from the engine's decision input (OM-Y4).

## 3. What's needed for the plot — a thin **`YearlineTrendSeries`** presentation artifact

The data already exists in the engine; it just isn't exported. A per-ticker `TickerPipelineResult` holds:
`semantic_history` (per-day `distance_to_ma250_pct`, `drawdown_so_far_pct`, gated `hazard_today` /
`p_retry_within_*d`, `active_engine`, `post_confirmation_trend_state` + the four trend scores merged in),
`trend_history`, `hazard_history`, and `price_df` (Close + MAs). A **second, presentation-only adapter**
would project these to a compact series the frontend plots:

```jsonc
// YearlineTrendSeries (presentation artifact — NOT the engine decision input)
{ "as_of": "...", "ticker": "MSFT", "schema_version": "...", "series_version": "v13_8_1_trend_series_v1",
  "dates": ["2025-..","..."],
  "close": [...], "ma20": [...], "ma50": [...], "ma250": [...],
  "distance_to_ma250_pct": [...],                 // the headline trend line (0 = yearline)
  "active_engine": ["repair","trend",...],        // regime band shading
  "post_confirmation_trend_state": [...],
  "trend_quality": [...], "pullback_quality": [...],
  "overextension": [...], "deterioration": [...], // the TO-1 de-saturated scores over time
  "hazard_today": [...], "p_retry_40d": [...],     // optional secondary panels
  "must_not_auto_execute": true }
```

Properties: **thin + deterministic, no new modelling** (a projection over existing history), **versioned
separately** (`series_version`) so the heavy chart payload never bloats or churns the lean decision
contract, and **read-only** (it never enters the engine's replay hash). It is the natural data source for
**OM-Y3's "Today-screen evidence panel."**

## 4. What the scalar contract *can* render today (so it's not nothing)

Even without the series, `YearlineContext` already powers a useful **current-state card / badges**:
- regime chip (`repair_active` / `post_confirmation_trend_state`), distance-to-MA250 + required-rebound;
- **gated `P(retry ≤ H)` bars** (10/20/40/60) shown only where `gate_passed[h]` (grey/withheld otherwise);
- days-to-touch **range** (central + low/high);
- `P(success│retry)` and the `P(reclaim ≤ H)` composite where `success_gate_passed`;
- a staleness badge (`is_stale`) and the "evidence, not advice / `must_not_auto_execute`" disclaimer.

That card is genuinely useful for OM-Y3; the **line plot** is the increment that needs §3.

## 5. Recommendation — and what was delivered

1. **Keep `YearlineContext` scalar** — it is correct as the engine's gated decision contract.
2. ✅ **`YearlineTrendSeries` DELIVERED (V13.8.1).** `adapter.to_yearline_trend_series(semantic_history,
   price_df=…)` emits the §3 series (thin, deterministic, read-only; `series_version =
   v13_8_1_yearline_trend_series_v1`), with `export_yearline_trend_series()`, a JSON schema, and a real
   180-day MSFT fixture (`exports/yearline_context/fixture_msft_trend_series.json`; also in
   `phase_09/artifacts/`) — `tests/test_adapter.py` covers shape/alignment, NaN→None, the gated series,
   the price overlay, lookback, and export. No new modelling.
3. **In option-mgmt:** OM-Y3 renders the current-state card from `YearlineContext` **and** the trend line
   plot from `YearlineTrendSeries`. Both are read-only (the `DailyDecision` stays byte-identical); only
   OM-Y4 lets the (scalar, gated) context *influence* a decision.

*Companion: `option_mgmt_handoff.md` (the cross-repo build guide) references this for the OM-Y3 panel.
Educational research only; not financial advice; every surface is `must_not_auto_execute`.*
