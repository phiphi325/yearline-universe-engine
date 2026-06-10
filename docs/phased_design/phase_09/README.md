# Phase 9 — option-mgmt integration (Track B): V13.8 adapter (yearline side) delivered

**Status:** ◐ IN PROGRESS — **V13.8 `YearlineContext` adapter (this repo) DELIVERED**; the `OM-Y0…Y5`
milestones live in `option-mgmt-2026`'s own `docs/phased-design/` (that repo is **read-only** from here —
not modified).
**Spec:** [`../planner/02_option_mgmt_integration_plan.md`](../planner/02_option_mgmt_integration_plan.md).
**Analysis:** [`../../option-mgmt-integration/`](../../option-mgmt-integration/) (assessment + contract design).

> Educational research only; not financial advice. The contract carries `must_not_auto_execute: true` and
> is **gated** — the consumer must use `p_retry[h]` only where `gate_passed[h]`, and `p_success` only where
> `success_gate_passed`.

---

## What V13.8 delivers
A **thin, deterministic projection** of the full statistical-context envelope onto the small, flat,
JSON-serializable **`YearlineContext`** contract `option-mgmt-2026` consumes — `src/yearline_universe/adapter.py`:

- **`to_yearline_context(envelope) -> dict`** — the contract: `as_of, ticker, schema_version,
  model_stack_version, adapter_version, repair_active, distance_to_ma250_pct,
  required_rebound_to_ma250_pct, post_confirmation_trend_state, p_retry{10,20,40,60}, p_retry_basis,
  gate_passed{…}, days_to_touch_central/low/high, reference_scope, is_stale, must_not_auto_execute=True` —
  **plus the now-populated Track-A success fields** `p_success`, `success_gate_passed`,
  `p_successful_reclaim{…}` (RS-4 is delivered, so these are real, not reserved-null).
- **`export_yearline_context(envelope, out_dir)`** — writes the persisted, versioned artifact (the loose-
  coupling hand-off the option-mgmt jobs layer ingests).
- **`YEARLINE_CONTEXT_JSON_SCHEMA`** + **`ADAPTER_VERSION`** (the contract pin; bump on any shape change).

### V13.8.1 — `YearlineTrendSeries` (presentation artifact for the trend plot)
- **`to_yearline_trend_series(semantic_history, price_df=…) -> dict`** — a thin, deterministic, **read-only**
  time-series projection over the engine's existing per-day history (`distance_to_ma250_pct`, the de-saturated
  trend scores, gated `hazard_today` / `p_retry_40d`, `active_engine` regime band + optional `close`/`MA`
  overlays), aligned to `dates`. The data source for option-mgmt's **OM-Y3 trend plot** — **separate** from
  the gated decision contract (it never enters the replay hash), so the chart payload never bloats it.
  `export_yearline_trend_series()` + `YEARLINE_TREND_SERIES_JSON_SCHEMA` + `TREND_SERIES_VERSION` pin; a real
  180-day MSFT fixture is committed. Rationale: [`ux_trend_plot_support_analysis.md`](ux_trend_plot_support_analysis.md).
- **V13.8.2 (docs + golden-fixture hardening).** Adds the **consumer rendering contract** for OM-Y3
  (`ux_trend_plot_support_analysis.md` §6 + `option_mgmt_handoff.md` §6.1: stacked panel/axis map, the
  *right-edge ≠ card* surface note, the `null`-gap rule, the engine/trend-state display-label table) and an
  `available:false` **empty-state golden fixture** (`fixture_unavailable_trend_series.json`, both dirs) with a
  schema-conformance test. **Docs + fixtures only — the `series_version` pin is unchanged** (the artifact
  shape did not change), so no coordinated option-mgmt PR is required.
- **V13.8.3 (docs-only — gate-status + producer ops).** Documents (1) the **card gate-status spec** —
  gate *status* (`gate_passed`/`success_gate_passed`/`p_retry_basis`/`reference_scope`/`is_stale`) is on the
  contract today; model *performance* (AUC/MACE/`n`) is **not**, and would need an optional `gate_diagnostics`
  block = a versioned contract change (`ux_trend_plot_support_analysis.md` §4.1, `option_mgmt_handoff.md`
  §3.2, decision in `../../option-mgmt-integration/integration_design_and_plan.md` §1.1); and (2) **when to
  enable the nightly GitHub Action** — `workflow_dispatch`-first, flip to `schedule:` only after OM-Y2 + a CI
  contract test (`option_mgmt_handoff.md` §10, `two_repo_strategy_and_deployment.md` §3), with an inert
  workflow template at [`ci/yearline_nightly.yml`](ci/yearline_nightly.yml). **No contract change — both pins
  frozen.**

### Design rules honored (from the assessment)
- **No new modelling** — a pure projection over the existing Phase-7/8 envelope; deterministic.
- **Gate-respect baked in.** Occurrence `p_retry` prefers the **Phase-7 blend** (the gate-passing surface)
  where surfaced, else the empirical estimator gated by the Phase-4 isotonic trust gate; **dormant** (no
  horizons) when above the yearline — the consumer then reads `post_confirmation_trend_state` instead.
- **The boundary is the whole game.** yearline (heavy, I/O) is **never** imported into option-mgmt's pure
  engine; the engine sees only this versioned, gated value object, hydrated from the persisted artifact.

## Worked example — MSFT, 2026-05-29 (`artifacts/fixture_msft_gated.json`)
```jsonc
{ "as_of": "2026-05-29", "ticker": "MSFT", "adapter_version": "v13_8_yearline_context_adapter_v1",
  "repair_active": true, "distance_to_ma250_pct": -2.967, "required_rebound_to_ma250_pct": 3.058,
  "p_retry": {"10":0.54,"20":0.648,"40":0.894,"60":0.936}, "p_retry_basis": "blend",
  "gate_passed": {"10":true,"20":true,"40":true,"60":true},
  "days_to_touch_central": 0.0, "days_to_touch_low": 0.0, "days_to_touch_high": 35.18,
  "p_success": 0.137, "success_gate_passed": true,
  "p_successful_reclaim": {"10":0.074,"20":0.089,"40":0.122,"60":0.128},
  "reference_scope": "group_transition_state", "is_stale": false, "must_not_auto_execute": true }
```
A **stale/empty** example (`artifacts/fixture_stale_empty.json`) shows the abstention shape: `repair_active:
false`, `p_retry: {}`, `is_stale: true` — the consumer treats it as "no usable context."

## Acceptance
- `tests/test_adapter.py` (7): contract shape + `additionalProperties:false`, version pins, blend-preference
  + per-horizon gates, empirical fallback via the trust gate, dormant-trend (no `p_retry`), populated &
  gated success fields, staleness, and the export entry point. **Full per-file suite green.**
- Schema + two committed fixtures under `exports/yearline_context/` (and mirrored here) for option-mgmt's
  **cross-repo contract test** (OM-Y1).

## What's next (in `option-mgmt-2026`, read-only here)
**OM-Y0** (enhancement ADR) → **OM-Y1** (Pydantic `YearlineContext` + TS codegen, pins the
`adapter_version`/`schema_version` range, loads these fixtures) → **OM-Y2** (ingest + persist) → **OM-Y3**
(read-only evidence panel) → **OM-Y4** (gated engine consumption — the prize). Per the spec, those are
separate reviewed PRs in that repo.

*Files: `src/yearline_universe/adapter.py` (new) + `__init__.py` exports; `tests/test_adapter.py` (new);
`exports/yearline_context/` (schema + fixtures). Educational research only; not financial advice.*
