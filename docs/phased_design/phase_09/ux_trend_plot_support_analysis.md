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
{ "available": true, "ticker": "MSFT", "as_of": "2026-05-29", "schema_version": "...",
  "series_version": "v13_8_1_yearline_trend_series_v1", "n": 180, "dates": ["2025-09-10", "..."],
  "close": [...], "ma20": [...], "ma50": [...], "ma250": [...],
  "distance_to_ma250_pct": [...],                 // the headline trend line (0 = yearline)
  "drawdown_so_far_pct": [...],
  "active_engine": ["repair_retry_hazard_engine","post_confirmation_trend_engine", ...], // regime bands (§6.4)
  "post_confirmation_trend_state": [...],
  "trend_quality": [...], "pullback_quality": [...],
  "overextension": [...], "deterioration": [...], // the TO-1 de-saturated scores over time
  "hazard_today": [...], "p_retry_40d": [...],     // gated ⇒ null off-regime (§6.3)
  "must_not_auto_execute": true }
```
> **Note:** `active_engine` / `post_confirmation_trend_state` are **internal identifiers** — map them to
> human labels before rendering (§6.4). The full consumer rendering contract is **§6**.

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

### 4.1 Gate status on the card — "is this number trustworthy *now*" vs "is the model *good*"
These are **two different questions**. The card answers the first today; the second needs a deliberate
(versioned) add — don't conflate them.

**(a) Gate *status* — already in `YearlineContext`; just render it.** Tells you, per snapshot, whether to
trust/show a number:
- **Per-horizon retry chips** — for H ∈ {10,20,40,60}: show the `p_retry[h]` bar where `gate_passed[h] ==
  true`; render **"withheld · building evidence"** where `false`. **Never hide a withheld horizon** — the
  withheld state *is* the signal ("the model declines to commit here").
- **Basis chip** — `p_retry_basis`: `blend` = the Phase-7 gated classifier surface; `empirical` = the
  isotonic-gated estimator; `null` = dormant (above the yearline).
- **Success / composite** — show `p_success` and `p_successful_reclaim[h]` **only where
  `success_gate_passed`** (each composite H is already `null` unless *both* gates passed).
- **Sample transparency** — `reference_scope` (which empirical bucket the estimate came from, e.g.
  `group_transition_state`).
- **Freshness + safety** — the `is_stale` badge and the "evidence, not advice / `must_not_auto_execute`"
  disclaimer.

That is enough to read *"the model is committing / declining, on which surface, from which bucket, how fresh."*

**(b) Model *performance* — NOT on the contract today (decision required).** The trust gate is computed from
**AUC, MACE (calibration error), and sample size `n`** against fixed thresholds (AUC ≥ 0.60, MACE ≤ 0.10,
n ≥ 50), but the adapter projects only the **boolean** `gate_passed` — **`YearlineContext` carries no AUC /
MACE / `n`** (only `reference_scope`). To answer *"is the model performing well?"* you'd add an **optional
`gate_diagnostics` block** (per-horizon `auc`, `mace`, `n` + the thresholds). The numbers already exist in
the envelope, so it's a **thin projection — but a contract-shape change** ⇒ **bump `ADAPTER_VERSION`** ⇒ a
coordinated **OM-Y1** pin bump.

**Design recommendation.** Keep the **end-user card** to *status* (chips + basis + scope + staleness +
disclaimer) — a holder shouldn't be reading AUC. Put AUC / MACE / `n` / calibration on a **separate internal
ops/health view**, fed by the optional `gate_diagnostics` block if/when you add it. Decision + rationale:
[`../../option-mgmt-integration/integration_design_and_plan.md`](../../option-mgmt-integration/integration_design_and_plan.md)
§1. So: **surface gate status now (free); decide `gate_diagnostics` separately** (cheap, but a versioned
contract change).

## 5. Recommendation — and what was delivered

1. **Keep `YearlineContext` scalar** — it is correct as the engine's gated decision contract.
2. ✅ **`YearlineTrendSeries` DELIVERED (V13.8.1).** `adapter.to_yearline_trend_series(semantic_history,
   price_df=…)` emits the §3 series (thin, deterministic, read-only; `series_version =
   v13_8_1_yearline_trend_series_v1`), with `export_yearline_trend_series()`, a JSON schema, and a real
   180-day MSFT fixture (`exports/yearline_context/fixture_msft_trend_series.json`; also in
   `phase_09/artifacts/`) — `tests/test_adapter.py` covers shape/alignment, NaN→None, the gated series,
   the price overlay, lookback, and export. No new modelling.
   - ✅ **V13.8.2 (docs + golden-fixture hardening).** Adds the **consumer rendering contract (§6)**, an
     `available:false` **empty-state golden fixture** (`fixture_unavailable_trend_series.json`), and fixes
     the §3 `series_version` example. **Docs + fixtures only — the `series_version` pin is unchanged**
     (`v13_8_1_yearline_trend_series_v1`); the artifact shape did not change, so no coordinated option-mgmt
     PR is required.
3. **In option-mgmt:** OM-Y3 renders the current-state card from `YearlineContext` **and** the trend line
   plot from `YearlineTrendSeries` (rendered per §6). Both are read-only (the `DailyDecision` stays
   byte-identical); only OM-Y4 lets the (scalar, gated) context *influence* a decision.

---

## 6. Rendering contract for OM-Y3 (consumer guidance) — V13.8.2

The series is delivered; this section is the **read-this-before-you-plot** guide so the panel is *correct*,
not merely present. (V13.8.2 is a **docs + golden-fixture** hardening — the `series_version` pin is
unchanged at `v13_8_1_yearline_trend_series_v1`; the artifact shape did not change.)

### 6.1 Panel / axis map — three scales, never one y-axis
The payload mixes three incompatible scales. Render as **stacked panels** sharing the `dates` x-axis
(exactly like the V12 dashboard), never overlaid on a single y-axis:

| panel | fields | axis / unit | notes |
|---|---|---|---|
| A — price | `close, ma20, ma50, ma250` | price ($) | the post-confirmation trend chart |
| B — distance | `distance_to_ma250_pct`, `drawdown_so_far_pct` | percent (%) | **0% line = the yearline** (headline) |
| C — trend scores | `trend_quality, pullback_quality, overextension, deterioration` | 0–1 | the TO-1 de-saturated scores |
| D — gated risk | `hazard_today, p_retry_40d` | 0–1 (probability) | gated ⇒ `null` off-regime (see §6.3) |

### 6.2 The right edge is **not** the current-state card
The plot and the scalar card come from **different surfaces**; only *some* fields agree at "today":

| field | series right edge (`…[-1]`) | scalar `YearlineContext` | agree? |
|---|---|---|---|
| `distance_to_ma250_pct` | daily value | `distance_to_ma250_pct` | ✅ same |
| `close / ma20 / ma50 / ma250` | daily value | (not on the card) | n/a |
| `p_retry_40d` | daily **empirical-gated** surface (e.g. `0.914`) | `p_retry["40"]` = the **Phase-7 blend** (e.g. `0.894`) | ❌ **differ by design** |
| `hazard_today` | daily gated hazard | (no single card field) | ❌ different surface |

`p_retry_40d` in the series is the **daily modeled history** (the *path*); the card's `p_retry["40"]` is the
**blended decision surface for today only** — it is *not* a per-day history column, and yearline deliberately
does **not** synthesize one (that would be new modelling). **Do not** draw the card's number as the line's
endpoint, and do not let users read the line's last point as "the" probability. If you want a "today" value
on the chart, render the card's blended `p_retry["40"]` as a **separate, labelled marker** ("today · blended")
distinct from the line. Only `distance_to_ma250_pct` and the price/MA overlays are guaranteed equal at the
right edge.

### 6.3 `null` means "not applicable in this regime" — gap, never interpolate
Gate-respect applies on the **time axis** too. In the series, `null` is **not zero** — it means the metric
doesn't apply to that day's regime:
- trend scores (`trend_quality / pullback_quality / overextension / deterioration`,
  `post_confirmation_trend_state`) are **`null` while the repair engine is active** (below the yearline);
- `hazard_today` / `p_retry_40d` are **`null` while the trend engine is active** (above the yearline).

So the chart **must gap the line across `null`** (e.g. Chart.js `spanGaps:false`, D3 `defined()`,
ECharts `connectNulls:false`) — interpolating across the gap draws a lie. Explain the gap visually with the
regime band (§6.4): *"trend scores inactive while below the yearline."* In the shipped MSFT fixture each
gated series is exactly **90 non-null / 90 null**, flipping at the regime boundary — a clean test of this.

### 6.4 Regime bands + labels — don't ship internal identifiers to the UI
Shade the chart background by **contiguous runs of `active_engine`** (run-length encode the array).
`active_engine` and `post_confirmation_trend_state` are **internal engine identifiers** — map them to human
labels in one place; never hardcode the raw strings into the view:

| `active_engine` | display label | band tint | meaning |
|---|---|---|---|
| `repair_retry_hazard_engine` | "Repair / retry watch" | amber | price **below** MA250 — a retry is live |
| `post_confirmation_trend_engine` | "Confirmed trend" | green | price **above** MA250 |

| `post_confirmation_trend_state` | display label | tone |
|---|---|---|
| `pullback_but_intact` | "Pullback, trend intact" | neutral |
| `indeterminate_trend` | "Indeterminate" | muted |
| `trend_deterioration_watch` | "Deterioration watch" | caution |
| `null` | (in repair — no trend state) | — |

Also draw the **0% reference line** on Panel B (the yearline) and an "as of `dates[-1]`" annotation. Default
window: the V12 dashboards show ~6–12 months; `to_yearline_trend_series(..., lookback_days=…)` can bound it.

### 6.5 Empty + stale states (build these, don't leave a blank chart)
- **No trend data:** the artifact is `{"available": false, "warning": "…", "series_version": "…",
  "must_not_auto_execute": true}` — render an explicit **"no trend history"** empty panel, not a blank
  canvas. Golden fixture: **`fixture_unavailable_trend_series.json`** (committed under
  `exports/yearline_context/` and `phase_09/artifacts/`).
- **Staleness:** the series carries no `is_stale` of its own — reuse the same-day
  `YearlineContext.is_stale` for the panel's stale badge. The card and series share `ticker` + `as_of`;
  **assert they match** before rendering them together (reject a mismatched card/series pair).

---

*Companion: `option_mgmt_handoff.md` (the cross-repo build guide) references this for the OM-Y3 panel; its
§6.1 carries the condensed rendering rules. Educational research only; not financial advice; every surface
is `must_not_auto_execute`.*
