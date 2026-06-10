# Handoff — integrating yearline-universe into `option-mgmt-2026`

**Audience:** the agent/developer continuing in the **`option-mgmt-2026`** repo (the OM-Y0…Y5 build).
**Date:** 2026-06-09 · **Producer side (this repo):** V13.8 `YearlineContext` adapter — **delivered**
(`phase_09/`). **This doc is self-contained** — you do not need to read the yearline codebase; everything
the consumer needs is the versioned contract + fixtures described here.

> **Educational research only; not financial advice. Neither system executes.** Every yearline surface
> carries `must_not_auto_execute: true`, and probabilities are **gated** — *use them only where the gate
> says so* (§3.2).

---

## 0. TL;DR + the one hard rule

yearline-universe is a **headless, nightly batch** that scores, per ticker, the MA250 "yearline"
repair/trend situation and emits a small **`YearlineContext`** value object (a persisted JSON artifact).
`option-mgmt-2026` ingests that artifact, hydrates a Pydantic value object, and lets the **pure engine**
optionally consume it — **only where its trust gate passes**.

> **THE HARD RULE (from your ADR-0005, CI-enforced):** `packages/engine` is pure, no-I/O, lean-deps.
> **Never import `yearline-universe` into the engine.** yearline is heavy + does I/O. It runs in your
> **jobs/ingestion layer**, persists the artifact, and the engine consumes only the lightweight
> `YearlineContext` value object — exactly how `MarketStateResult` / `FlowScore` are hydrated today.
> Coupling is a **persisted, versioned artifact**, not a library dependency.

---

## 1. What yearline provides

A nightly per-ticker artifact. The **consumed contract** is `YearlineContext` (the subset your engine
needs), emitted verbatim by yearline's V13.8 adapter. Pin: **`ADAPTER_VERSION = "v13_8_yearline_context_adapter_v1"`**.

**Where the assets live (this repo, for your contract test):**
- **Decision contract** (`YearlineContext`): schema `exports/yearline_context/yearline_context_schema.json`;
  fixtures `…/fixture_msft_gated.json`, `…/fixture_stale_empty.json`.
- **Presentation series** (`YearlineTrendSeries`, V13.8.1 — for the trend plot, §6): schema
  `…/yearline_trend_series_schema.json`; fixtures `…/fixture_msft_trend_series.json` (available) and
  `…/fixture_unavailable_trend_series.json` (the `available:false` empty state — added V13.8.2).
- All mirrored in `docs/phased_design/phase_09/artifacts/`.

These are the bytes to vendor into `option-mgmt-2026` as your golden fixtures.

---

## 2. The boundary (where each piece runs)

```
nightly (yearline, this repo):  run_universe_pipeline(... surface_blend=True, surface_success=True)
                                 → adapter.export_yearline_context(envelope) → yearline_context_{TICKER}_{as_of}.json
deliver:                         publish artifact (Release asset / object store / committed data branch)
ingest (option-mgmt jobs):       apps/api/app/jobs/ingest_yearline.py → persist row (payload JSONB + hash)
hydrate (option-mgmt api):       inputs_hydration_service → YearlineContext | None
engine (option-mgmt, PURE):      produce_daily_decision(..., yearline_context: YearlineContext | None = None)
```
yearline never imports into the engine; the engine never imports yearline. They meet **only** at the
JSON contract.

---

## 3. The `YearlineContext` contract

### 3.1 Fields (flat, JSON-serializable)

| field | type | meaning |
|---|---|---|
| `as_of` | date (str) | the data date of this context |
| `ticker` | str | e.g. "MSFT" |
| `schema_version`, `model_stack_version` | str | yearline envelope pins (fold into your replay hash) |
| `adapter_version` | str | **contract pin** — pin an accepted range; mismatch ⇒ treat as incompatible |
| `repair_active` | bool | repair/hazard engine active (price **below** MA250 → a retry is live) |
| `distance_to_ma250_pct` | num\|null | signed % distance to the yearline (neg = below) |
| `required_rebound_to_ma250_pct` | num\|null | % rebound needed to reclaim |
| `post_confirmation_trend_state` | str\|null | when **above** MA250 (e.g. `overextended_trend`); null in repair |
| `p_retry` | {int→num} | `P(retry ≤ H)` for H∈{10,20,40,60}; **empty {} when dormant** (above MA250) |
| `p_retry_basis` | "empirical"\|"blend"\|null | which surface produced `p_retry` |
| `gate_passed` | {int→bool} | **per-horizon trust gate** — consume `p_retry[h]` ONLY where true |
| `days_to_touch_central/low/high` | num\|null | descriptive days-to-touch range (not a forecast) |
| `p_success` | num\|null | `P(success │ retry)` — given a touch, does it reclaim+hold? |
| `success_gate_passed` | bool | gate for `p_success` / the composite |
| `p_successful_reclaim` | {int→num\|null} | composite `P(retry≤H)×P(success│retry)`; **null per-H unless both gates pass** |
| `reference_scope` | str\|null | empirical bucket scope (sample transparency, e.g. `group_transition_state`) |
| `is_stale` | bool | freshness flag (also re-check `as_of` vs your run date) |
| `must_not_auto_execute` | const true | hard invariant |

### 3.2 Gate-respect — the rule that makes it safe
- Use `p_retry[h]` **only if** `gate_passed[h] is True`. Otherwise show "not yet / withheld," never the number.
- Use `p_success` / `p_successful_reclaim[h]` **only if** `success_gate_passed is True` (and, per horizon,
  the composite is already null unless *both* gates passed).
- `repair_active == False` ⇒ `p_retry == {}` (the retry question is dormant above the yearline) → read
  `post_confirmation_trend_state` instead; do **not** synthesize a retry probability.
- `is_stale == True` (or `as_of` too old for your run) ⇒ treat as `None` (abstain).

**Surfacing the gate state (the OM-Y3 card).** Gate-respect is also a *display* contract: show the gated-in
`P(retry≤H)` bars where `gate_passed[h]`, render the rest as **"withheld · building evidence"** (don't hide
them — the withheld state is the signal), and badge `p_retry_basis` (blend/empirical), `success_gate_passed`,
`reference_scope` (sample bucket), and `is_stale`. That answers *"is this number trustworthy right now."* It
does **not** answer *"is the model performing well"* — the contract carries only the **boolean** gate, not
the **AUC / MACE / `n`** behind it. A model-health readout needs an **optional `gate_diagnostics` block** (a
contract-shape change ⇒ `ADAPTER_VERSION` bump ⇒ a coordinated OM-Y1 pin bump); keep AUC/MACE on an internal
ops view, **not** the end-user card. Decision + rationale:
[`../../option-mgmt-integration/integration_design_and_plan.md`](../../option-mgmt-integration/integration_design_and_plan.md) §1.

### 3.3 Gated example — MSFT, 2026-05-29 (`fixture_msft_gated.json`)
```jsonc
{ "as_of":"2026-05-29","ticker":"MSFT","adapter_version":"v13_8_yearline_context_adapter_v1",
  "repair_active":true,"distance_to_ma250_pct":-2.967,"required_rebound_to_ma250_pct":3.058,
  "post_confirmation_trend_state":null,
  "p_retry":{"10":0.54,"20":0.648,"40":0.894,"60":0.936},"p_retry_basis":"blend",
  "gate_passed":{"10":true,"20":true,"40":true,"60":true},
  "days_to_touch_central":0.0,"days_to_touch_low":0.0,"days_to_touch_high":35.18,
  "p_success":0.137,"success_gate_passed":true,
  "p_successful_reclaim":{"10":0.074,"20":0.089,"40":0.122,"60":0.128},
  "reference_scope":"group_transition_state","is_stale":false,"must_not_auto_execute":true }
```
The **stale/empty** fixture shows the abstention shape (`repair_active:false`, `p_retry:{}`, `is_stale:true`).

---

## 4. The OM-Y0 … OM-Y5 roadmap (tasks for `option-mgmt-2026`)

Each is a **separate, reviewed PR** in `option-mgmt-2026`. Acceptance bars mirror your existing invariants
(pure engine, three-pin replay, Pydantic→TS codegen, disclaimer/no-broker gates).

**OM-Y0 — enhancement ADR (no code).** Adopt yearline as an external statistical-context provider. Write
`docs/enhancements/` assessment + a proposed ADR fixing: value-object boundary; jobs-layer producer; a
**4th replay pin** `yearline_context_version`; **gate-respect**; **no engine import**.
*Hyperagent prompt:* "Draft an enhancement assessment + ADR to adopt an external `YearlineContext`
statistical-context provider as a jobs-layer-hydrated value object the pure engine optionally consumes;
specify the boundary, the replay-hash extension, and gate-respect. No code."

**OM-Y1 — the `YearlineContext` contract.** `packages/engine/engine/yearline/types.py` (frozen Pydantic
mirroring §3.1) + add to the Pydantic→TS drift-checked codegen; vendor the V13.8 fixtures (gated + stale)
and a **contract test** that parses them and pins the accepted `adapter_version`/`schema_version` range.
No behavior change. *Acceptance:* codegen drift gate green; `mypy --strict` clean; fixtures parse.

**OM-Y2 — ingestion + persistence.** Alembic migration `yearline_context (as_of, ticker, schema_version,
model_stack_version, payload JSONB, payload_hash)`; `apps/api/app/jobs/ingest_yearline.py` (reads the
artifact, idempotent upsert); a hydration service → `YearlineContext | None` (missing/stale ⇒ `None`).
*Acceptance:* idempotent; graceful `None`; covered by `make smoke`.

**OM-Y3 — read-only surfacing (fastest user value).** `GET /engine/yearline-context` + a **Today-screen
evidence panel**: regime chip, gated `P(retry≤H)` bars, days-to-touch range, `P(success│retry)` / composite,
staleness badge + disclaimer. **No decision changes** — `DailyDecision` byte-identical. *(For the
time-series **trend plot**, you also need yearline's `YearlineTrendSeries` presentation artifact — see
`ux_trend_plot_support_analysis.md`; the scalar `YearlineContext` powers the current-state card but not the
line chart.)*

**OM-Y4 — gated engine consumption (the prize).** `produce_daily_decision(..., yearline_context=None)`
(pure optional kwarg, like the `futures_basis` stub); extend `compute_inputs_hash` + persist the 4th pin
`yearline_context_version`; new `rules.yaml` predicate clauses + a Confidence-Composer component **active
only where `gate_passed`**; engine-version bump + ADR note + golden-test updates. *Acceptance:*
output-changing ⇒ reviewed; a decision **without** yearline hashes identically to pre-OM-Y4 (back-compat);
gate-respect unit-tested; `check_no_broker_imports` + disclaimer gate hold.

**OM-Y5 — stretch (only if OM-Y4 earns it).** Market-State enrichment (touches the locked 6-regime taxonomy
⇒ ADR-0002 amendment) or collar-intent keyed off yearline readiness.

---

## 5. Determinism / replay (your invariant I3)
A yearline-influenced decision must fold the contract into replay identity: add a **4th pin**
`yearline_context_version` = a canonical hash of `{adapter_version, schema_version, model_stack_version} +
the consumed fields`. A decision that consumes **no** yearline context must hash **identically** to the
pre-OM-Y4 world (back-compat is acceptance-gated).

## 6. The trend plot (UX) — `YearlineTrendSeries` (V13.8.1, **delivered**)
The V12-style **trend line plot** is a *time series* (distance-to-MA250, the trend scores, hazard over the
replay window, price+MA overlays). The scalar `YearlineContext` **cannot** feed it — so yearline now also
emits a small, separate, versioned **`YearlineTrendSeries`** presentation artifact (read-only; **not** an
engine input; never enters the replay hash). Pin: **`series_version = "v13_8_1_yearline_trend_series_v1"`**.

Shape (parallel arrays aligned to `dates`):
```jsonc
{ "available": true, "ticker": "MSFT", "as_of": "2026-05-29", "series_version": "v13_8_1_yearline_trend_series_v1",
  "n": 180, "dates": ["2025-09-10", …],
  "distance_to_ma250_pct": [...],            // headline trend line (0 = yearline)
  "active_engine": ["repair_retry_hazard_engine", …],   // regime band shading
  "post_confirmation_trend_state": [...],
  "trend_quality": [...], "pullback_quality": [...], "overextension": [...], "deterioration": [...],
  "drawdown_so_far_pct": [...], "hazard_today": [...], "p_retry_40d": [...],   // gated ⇒ null off-regime
  "close": [...], "ma20": [...], "ma50": [...], "ma250": [...],               // price/MA overlay
  "must_not_auto_execute": true }
```
**OM-Y3 plots from this** (the current-state *card* still comes from `YearlineContext`). Vendor
`yearline_trend_series_schema.json` + **both** fixtures — `fixture_msft_trend_series.json` (available) and
`fixture_unavailable_trend_series.json` (empty state) — as the panel's golden fixtures.

### 6.1 Rendering rules for the OM-Y3 trend plot (read before you build)
The artifact is delivered; these rules keep the panel **correct**, not merely present. Full treatment:
`ux_trend_plot_support_analysis.md` §6. *(V13.8.2 = docs + golden-fixture hardening; the `series_version`
pin is **unchanged** — the artifact shape did not change, so no coordinated yearline/option-mgmt PR pair
is required.)*

1. **Three scales → stacked panels, never one y-axis.** Panel A price (`close/ma20/ma50/ma250`); Panel B
   percent (`distance_to_ma250_pct`, `drawdown_so_far_pct`, with a **0% line = the yearline**); Panel C 0–1
   trend scores (`trend_quality/pullback_quality/overextension/deterioration`); Panel D 0–1 gated risk
   (`hazard_today`, `p_retry_40d`).
2. **The right edge ≠ the current-state card.** `distance_to_ma250_pct[-1]` and the price/MA overlays equal
   the card's same-day values, but `p_retry_40d` (the daily **empirical-gated** history) ≠ the card's
   `p_retry["40"]` (the **Phase-7 blend**, today-only). Plot the line as history; if you want a "today"
   value, show the card's blended number as a **separate labelled marker** — never as the line's endpoint.
3. **`null` = "not applicable in this regime," not zero — gap the line, don't interpolate.** Trend scores
   are `null` while in repair; `hazard_today`/`p_retry_40d` are `null` while in trend. Use `spanGaps:false`
   (or equivalent) and explain the gap with the regime band.
4. **Shade by `active_engine` runs; map internal names to labels** (never hardcode the raw strings):
   `repair_retry_hazard_engine` → "Repair / retry watch" (amber, below MA250);
   `post_confirmation_trend_engine` → "Confirmed trend" (green, above MA250). Trend-state enum:
   `pullback_but_intact` → "Pullback, trend intact"; `indeterminate_trend` → "Indeterminate";
   `trend_deterioration_watch` → "Deterioration watch"; `null` → (no trend state, in repair).
5. **Empty/stale states.** `{"available": false, …}` ⇒ render an explicit "no trend history" panel (golden
   fixture `fixture_unavailable_trend_series.json`), **not** a blank chart. The series has no `is_stale` of
   its own — reuse the same-day `YearlineContext.is_stale`, and assert the card and series share
   `ticker` + `as_of` before rendering them together.

Full analysis: [`ux_trend_plot_support_analysis.md`](ux_trend_plot_support_analysis.md).

## 7. Versioning + cross-repo contract test + maintenance
- **yearline owns** the envelope/adapter schema (`schema_version`, `model_stack_version`, `adapter_version`).
  **option-mgmt owns** the consumer `YearlineContext` model and **pins an accepted version range.**
- **Contract test on both sides** against the shared fixtures (in this repo: `tests/test_adapter.py`; in
  yours: OM-Y1's fixture-parse test). A yearline release that bumps `adapter_version` ⇒ a coordinated PR
  pair; same fixtures both sides catch drift in CI.
- Coordinate the two repos **only at the contract boundary** — nowhere else.

## 8. Guardrails (carry into every OM-Y PR)
`must_not_auto_execute: true` on every surface; **gate-respect** (consume `P` only where gated; honest
abstention on staleness/dormancy); **no engine import of yearline**; `check_no_broker_imports` + the
disclaimer gate stay green; output-changing steps (OM-Y4) reviewed with byte-identical back-compat.

## 9. Suggested first hyperagent prompts (in `option-mgmt-2026`)
1. "Read this handoff + the vendored `YearlineContext` schema/fixtures; draft **OM-Y0** (enhancement
   assessment + ADR) for adopting yearline as a jobs-layer-hydrated, gated value object — no code."
2. "Implement **OM-Y1**: a frozen Pydantic `YearlineContext` + TS codegen entry + a contract test that
   parses the two vendored fixtures and pins the accepted `adapter_version` range. No behavior change."
3. "Implement **OM-Y2 + OM-Y3**: ingest/persist the artifact (idempotent, graceful `None`) and a read-only
   Today-screen evidence panel from `YearlineContext` (no decision change; `DailyDecision` byte-identical)."

## 10. Operating the producer — the nightly run + *when* to enable the schedule
yearline is a **headless nightly batch**, not a service (full deployment analysis:
[`../../option-mgmt-integration/two_repo_strategy_and_deployment.md`](../../option-mgmt-integration/two_repo_strategy_and_deployment.md)
§3). The producer side is **already ready** to schedule — both pins are frozen and the contract abstains
gracefully (`is_stale`, `available:false`). The right trigger to flip on a cron is **consumer-readiness +
ops hygiene, not a date:**

1. **Don't schedule before OM-Y2.** A daily artifact with nowhere to land is noise. Order: **OM-Y1**
   (contract + fixtures pinned) → **OM-Y2** (ingest + persist) → *then* turn on `schedule:`.
2. **Ship it `workflow_dispatch`-first (manual).** Produce specific `as_of` dates on demand (demo/backfill)
   and prove idempotency, with **no cron**. Template: [`ci/yearline_nightly.yml`](ci/yearline_nightly.yml)
   — it lives under `docs/` so it is **inert**; copy it to `.github/workflows/` to activate. **NB: this repo
   has no CI yet** — adding CI + the cross-repo contract test is itself a prerequisite (step 4).
3. **Gate the cron behind:**
   - **Deterministic run + idempotent publish** — re-running the same `as_of` overwrites cleanly (key the
     artifact by `{ticker}_{as_of}`); no duplicate rows downstream.
   - **Market-calendar awareness** — skip weekends/holidays, or emit `available:false` / a stale envelope on
     a no-new-bar day rather than a half-built one.
   - **Secrets + budget** — the nightly job does the data pull (price cache); put the data-API key in repo
     secrets and respect rate-limit/runner cost. Run the **pooled universe** (MSFT trust needs it), not MSFT
     alone.
   - **Contract test green in CI** — validate the emitted artifact against `yearline_context_schema.json` +
     `yearline_trend_series_schema.json` **before** publishing, so a bad push can't poison the feed.
   - **Never let the nightly job bump `adapter_version` / `series_version`** — the schedule is for *data*
     freshness, never a contract change (those stay reviewed PRs with a coordinated OM-Y1 bump).
4. **Flip `workflow_dispatch` → `schedule:`** the day OM-Y2 can ingest it **and** the contract test is wired
   into CI. Until then, manual `workflow_dispatch` is the correct, safe state.

---

*Producer-side reference (this repo): `src/yearline_universe/adapter.py`, `phase_09/README.md`,
`planner/02_option_mgmt_integration_plan.md`, `docs/option-mgmt-integration/`. Educational research only.*
