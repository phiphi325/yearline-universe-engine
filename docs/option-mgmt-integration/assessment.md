# Assessment — yearline-universe → option-mgmt-2026

*Analysis only. The concrete design + phased plan are in `integration_design_and_plan.md`. Educational
research; not financial advice; neither system executes trades.*

---

## 1. What `option-mgmt-2026` is (the target)

A **monorepo** (pnpm + uv) whose product is `packages/engine` — **pure-function Python, no I/O** —
surfaced by `apps/api` (FastAPI) and `apps/web` (Next.js "Today screen"). It answers one question per
day for a long-term MSFT holder running a tactical options overlay: *what should I do today?*

**Engine pipeline** (`engine.decision.produce_daily_decision`, v1.6.0):

```
MarketStateResult ─┐
FlowScore ─────────┼─► recommend() ─► select_strikes() ─► assess()/downgrade ─► compose() ─► DailyDecision
ChainSnapshot ─────┤    (YAML rules)     (BS Δ-match)       (fill feasibility)   (confidence)   (+collar dispatch)
PositionState ─────┤
UserStrategyProfile┘
```

**Invariants that govern any integration** (from `docs/architecture.md`, the ADRs, CI guards):

| # | Invariant | Consequence for us |
|---|---|---|
| I1 | **Engine-first / pure** — no I/O, DB, or network in `packages/engine` (ADR-0001/0005; CI bump + coverage gates) | yearline (heavy + I/O) **cannot** be imported into the engine. |
| I2 | **Upstream value objects** — `MarketStateResult`, `FlowScore`, `ChainSnapshot` are computed *outside* and passed *in* | yearline plugs into this exact slot as a `YearlineContext` value object. |
| I3 | **Auditable three-pin replay** — `inputs_hash` (SHA-256 over `as_of, ticker, chain, positions, profile, market_state, flow_score`) + `engine_version` + `weights_version` | a yearline-influenced decision must fold the **yearline contract version + a hash of consumed fields** into the replay identity. |
| I4 | **No execution** — zero broker write paths; `check_no_broker_imports.sh` | perfect alignment: yearline is `must_not_auto_execute: true`, evidence-only. |
| I5 | **Locked taxonomies** — 6 regimes (ADR-0002), 8 V1 rules (`rules.yaml`), multiplicative confidence (ADR-0003) | enriching Market State / adding a regime is an **ADR-level** change → defer; adding **rule clauses + a confidence component** is the lighter path. |
| I6 | **Deterministic V1 → ML node-swap in Phase 4** — ML replaces a node without changing interfaces; V1 stubs plumbed through kwargs | yearline mirrors this (empirical baseline → gated classifier blend); it slots in as an **optional kwarg** like the `futures_basis=0` stub. |
| I7 | **Pydantic→TS codegen, drift-checked** (`packages/shared-types`) | the `YearlineContext` contract gets a Pydantic model + generated TS, drift-gated in CI. |
| I8 | **Disclaimer gate** on every surface | yearline already ships disclaimers; align text. |
| I9 | **Ingestion/jobs layer** (`apps/api/app/jobs/`, P2; CSV in MVP) writes snapshots to Postgres | yearline runs **here**, not in the engine. |

The key realization: **option-mgmt was *built* to consume pre-computed, validated, versioned context
objects.** yearline is precisely such a producer. The architecture leaves a yearline-shaped hole.

## 2. What yearline-universe emits (the source)

A per-ticker `SingleTickerStatisticalContextEnvelope` (JSON; schema
`exports/reports/statistical_context_schema.json`; `schema_version` + `model_stack_version`):

- `active_engine_context` — repair/hazard engine **vs** post-confirmation trend engine (which is live).
- `repair_retry_context` — distance to MA250, required rebound, drawdown.
- `retry_hazard_context` — **canonical empirical `P(retry ≤ {10,20,40,60}d)`** + `hazard_today`;
  `calibration_gate_40d.passed` + `surfaced_probability_is_calibrated`; and (Phase 7, opt-in) a gated
  `direct_classifier_blend` overlay.
- `retry_timing_context` — conditional **days-to-next-touch** estimators (a range, not a point).
- `post_confirmation_trend_context` — trend-quality/pullback/overextension when above MA250.
- `option_overlay_research_hint` — **`must_not_auto_execute: true`** (literally a hint for an options
  overlay — the designed handshake).
- `calibration_context` + `warnings` + `disclaimers`.

It is **deterministic** (fixed seed), and **honest about trust**: the probability is only surfaced as
trustworthy where a per-horizon **trust gate** (AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50) passes — which is
why it is computed on the **pooled universe** (single-ticker AUC ≈ 0.46 → pooled 0.74–0.82).

## 3. What yearline *adds* that option-mgmt lacks

| option-mgmt today | yearline contributes | Why it matters to an overlay |
|---|---|---|
| **Short-horizon** market state (IV, gamma flow, ADX, breakout, events) | **Medium-horizon structural state**: yearline repair vs trend regime | a different, slower clock the engine doesn't model |
| no notion of "distance to / recovery toward the 1-yr trend" | distance-to-MA250, **required rebound**, repair drawdown | frames whether the holder is in drawdown-repair or trend |
| no time-to-event estimate | **conditional days-to-next-touch** range | informs **expiry / DTE** selection |
| no probability of a structural retouch | **calibrated, gated `P(retry ≤ H)`** (+ classifier blend) | informs **directional bias** and collar **intent** |
| `OPEN_COLLAR` fires on `HIGH_IV_EVENT`/`POST_EVENT_REPRICE` only | "**low-readiness repair**" context (at the low, gap widening, high vol) | a principled extra trigger/temper for **defensive vs zero-cost** collars |

Concretely, the MSFT 2026-06-05 example (yearline tutorial 07): a *low-readiness* repair where the
classifier **tempers** near-term retouch odds. That is exactly the kind of signal that should bias an
overlay toward caution — context option-mgmt cannot currently derive.

## 4. Boundary analysis — where yearline runs (the crux)

**Not** in `packages/engine` (I1): yearline's `pandas/numpy/scipy/scikit-learn` + price-cache I/O +
universe pooling would violate the pure-function discipline, the lean-deps posture, the 100 %-coverage
gate on `engine.scoring`, and the engine-version-bump guard.

**Yes** in the **ingestion/jobs layer** (I9), exactly like market-data ingestion:

```
[yearline-universe]  (heavy, scheduled, pooled universe run)
        │  emits SingleTickerStatisticalContextEnvelope (MSFT)
        ▼
[apps/api/app/jobs/ yearline ingestion]  ── persists ──►  Postgres: yearline_context (JSONB + version + hash)
        ▼
[apps/api hydration service]  ── loads consumed subset ──►  YearlineContext (Pydantic value object)
        ▼
[engine.decision.produce_daily_decision(..., yearline_context=...)]   ← pure; consumes the value object
```

This mirrors `inputs_hydration_service.py` (M1.17.5), which already hydrates `MarketStateResult` /
`FlowScore` from the DB into the engine call. yearline becomes one more hydrated input — heavy work
stays **upstream**, the engine stays **pure**.

## 5. The contract, determinism, and gate-respect

- **Contract.** Define the **subset** of the envelope the engine consumes as a Pydantic `YearlineContext`
  (e.g. repair-active flag, distance/required-rebound, gated `P(retry≤H)` + `gate_passed`,
  days-to-touch central/range, trend state). Generate TS via the existing drift-checked codegen (I7).
  yearline emits exactly this subset via its **already-planned V13.8 "repo-integration adapter"** — so
  this integration *is* yearline V13.8.
- **Determinism / replay (I3).** Extend `compute_inputs_hash()` to include the yearline contract:
  its `schema_version` + `model_stack_version` + a canonical hash of the consumed fields. yearline is
  deterministic, so a decision stays exactly replayable. Practically a **fourth pin**
  (`yearline_context_version`) or a sub-key in `inputs_hash`.
- **Gate-respect (the most important rule).** Use yearline's `P(retry≤H)` as a *decision* input **only
  where `surfaced_probability_is_calibrated` / `calibration_gate_*` passes**; otherwise treat yearline
  as **context-only** (display, or a weak prior). This maps cleanly onto option-mgmt's
  **Confidence Composer** (a gated yearline component) and the **rules predicate vocabulary** (a clause
  like `yearline_gate_passed AND yearline_p_retry_40d_gte: X`). Honest abstention on both sides — same
  philosophy.

## 6. ML-node-swap alignment (a bonus fit)

option-mgmt's Phase-4 plan: *ML replaces specific engine nodes without changing interfaces; V1 stubs
are plumbed through explicit kwargs.* yearline is congruent on **both** counts: it is delivered as an
**optional kwarg** (absent/stale → engine degrades gracefully, exactly like `futures_basis=0`), and its
**own** internals already follow "deterministic baseline → gated ML upgrade" (empirical estimator →
classifier↔empirical blend). yearline can therefore start as a *context* node and deepen over time
without interface churn — precisely option-mgmt's intended evolution path.

## 7. Fit / risk scorecard

| Dimension | Verdict |
|---|---|
| Language / runtime | ✅ both Python (3.14 target); value-object boundary keeps deps isolated |
| Architectural pattern | ✅ near-perfect — option-mgmt consumes pre-computed value objects by design |
| Philosophy (no-exec, deterministic, auditable, disclaimer) | ✅ identical on both sides |
| Signal additivity | ✅ orthogonal time scale; not redundant with Market State |
| Pure-engine purity | ⚠️ must isolate heavy deps in jobs layer (never import into `packages/engine`) |
| Replay determinism | ⚠️ requires extending `inputs_hash` with the yearline version/hash |
| Trust / calibration | ✅ yearline self-gates; consume gated probability only |
| Locked taxonomies | ⚠️ Market-State/regime enrichment is ADR-level → defer; rules+confidence is the light path |
| Operational (data freshness, universe pooling) | ⚠️ must run the pooled universe nightly; degrade gracefully if stale/missing |
| Two-repo coordination | ⚠️ pin + version + contract-test the boundary |

**Net:** a high-fit, low-architectural-friction integration whose risks are all **operational/contract**
(isolatable), not **architectural**. The single hard rule — *never import yearline into the pure
engine; deliver a versioned, gated value object* — falls naturally out of option-mgmt's own design.
