# Track B — option-mgmt integration build plan (V13.8 adapter + OM-Y0 … OM-Y5)

*Spec-grade execution plan to feed this engine's context into
[`option-mgmt-2026`](https://github.com/phiphi325/option-mgmt-2026). Derived from
`docs/option-mgmt-integration/`. Educational research; not financial advice; neither system executes.*

> **Yearline-side work delivered as `docs/phased_design/phase_09/`** (the V13.8 adapter + the
> `YearlineContext` contract + schema + fixtures + the cross-repo contract test; `README.md` +
> `artifacts/`). The **`OM-Y*` milestones are tracked in `option-mgmt-2026`'s own `docs/phased-design/`**,
> not here — `phase_09/` records only the yearline side. `phase_08` (Track A) and `phase_09` can be in
> flight in parallel.

**The one hard rule (from the assessment).** `option-mgmt-2026/packages/engine` is **pure, no-I/O,
lean-deps** (ADR-0005, CI-enforced). yearline is heavy + does I/O → it **must never be imported into the
pure engine**. It runs in option-mgmt's **ingestion/jobs layer**, persists its envelope, and the engine
consumes a lightweight, **gated** `YearlineContext` value object — exactly how `MarketStateResult` /
`FlowScore` are hydrated today. Delivery is a **persisted, versioned artifact** (loose coupling), not a
library dependency of the engine.

**Repo ownership.** **V13.8** lives in **this repo**. **OM-Y0…Y5** are PRs against **`option-mgmt-2026`**
(read-only so far — we have not modified it). Each OM-Y* is a separate, reviewed PR there.

---

## V13.8 — yearline repo-integration adapter (THIS repo) — do first

- **Objective.** A stable, **versioned** export of the exact `YearlineContext` *subset* option-mgmt
  consumes — the integration contract — plus its JSON schema and an option-mgmt-side fixture.
- **Deliverable / modules.**
  - `src/yearline_universe/adapter.py` (new): `to_yearline_context(envelope) -> dict` projecting the
    envelope to the flat contract: `as_of, ticker, schema_version, model_stack_version, adapter_version,
    repair_active, distance_to_ma250_pct, required_rebound_to_ma250_pct, post_confirmation_trend_state,
    p_retry{10,20,40,60}, p_retry_basis, gate_passed{...}, days_to_touch_central/low/high,
    reference_scope, is_stale, must_not_auto_execute=True` — **plus reserved, optional, gated-off
    `p_success` / `p_successful_reclaim` fields** (Track-A forward-compat, so the contract won't churn).
  - A JSON schema for the contract under `exports/` + a committed **fixture** (a gated MSFT example and a
    stale/empty example) for option-mgmt's contract test.
  - An export entry point (CLI / function) that writes the artifact (JSON) to `exports/` or a store.
    `ADAPTER_VERSION` pin, bumped on any contract change.
- **Approach.** Thin projection over the existing Phase-7 envelope; deterministic; **no new modelling**.
- **Acceptance.** Schema-valid; deterministic; fields match `docs/option-mgmt-integration` §1; fixtures
  load; `ADAPTER_VERSION` + `schema_version` + `model_stack_version` present.
- **Tests.** `tests/test_adapter.py`: schema conformance, version pins, gated-field correctness, the
  reserved success fields present-but-null.
- **Dependencies.** The Phase-7 envelope (done). **Risks.** Contract drift → pin + fixture + the
  cross-repo contract test (OM-Y1).

## OM-Y0 — enhancement assessment + ADR (in `option-mgmt-2026`) — do early, parallel to V13.8

- **Objective.** Adopt yearline as an **external statistical-context provider**, fixing the boundary.
- **Deliverable.** A `docs/enhancements/` assessment of yearline (mirroring option-mgmt's ADR-0008
  enhancement-adoption process) **+ a proposed ADR**: value-object boundary; jobs-layer producer;
  replay-hash extension (a 4th pin `yearline_context_version`); **gate-respect** (consume `P` only where
  gated); **no engine import**.
- **Acceptance.** ADR accepted; boundary + contract agreed; **no code**.
- **Dependencies.** The V13.8 **contract draft** (not the full build).

## OM-Y1 — the `YearlineContext` contract (in `option-mgmt-2026`)

- **Objective.** A Pydantic `YearlineContext` model (the consumed subset) + TS codegen + fixtures.
- **Deliverable.** `packages/engine/engine/yearline/types.py` (frozen Pydantic); add to the Pydantic→TS
  drift-checked codegen; load the V13.8 fixtures (gated + stale). **No behaviour change.**
- **Acceptance.** codegen drift gate green; `mypy --strict` clean; fixtures parse; pins the accepted
  yearline `schema_version` range. **Dependencies.** V13.8 contract.

## OM-Y2 — ingestion + persistence (in `option-mgmt-2026`)

- **Objective.** Land yearline's artifact in option-mgmt's data layer.
- **Deliverable.** Alembic migration for a `yearline_context` table (`as_of, ticker, schema_version,
  model_stack_version, payload JSONB, payload_hash`); `apps/api/app/jobs/ingest_yearline.py` (reads the
  V13.8 artifact, persists idempotently); a hydration service (mirrors `inputs_hydration_service.py`) →
  `YearlineContext | None`.
- **Acceptance.** idempotent persistence; missing/stale ⇒ `None` (graceful); covered by `make smoke`.
  **Dependencies.** OM-Y1.

## OM-Y3 — read-only surfacing (Option C — fastest user value, in `option-mgmt-2026`)

- **Objective.** Show yearline context **without changing any decision**.
- **Deliverable.** `GET /engine/yearline-context` (read-only) + a **Today-screen evidence panel**
  (repair/trend state, gated `P(retry ≤ H)`, days-to-touch range) with the disclaimer + trust/staleness
  shown honestly.
- **Acceptance.** panel renders from a fixture; the `DailyDecision` payload is **byte-identical**;
  disclaimer present. **Dependencies.** OM-Y2.

## OM-Y4 — gated engine consumption (Option A — the prize, in `option-mgmt-2026`)

- **Objective.** Let yearline **influence** the decision — only where its gate passes.
- **Deliverable.** `produce_daily_decision(..., yearline_context: YearlineContext | None = None)` (pure;
  optional kwarg, like the `futures_basis` stub); extend `compute_inputs_hash` + persist the 4th pin
  `yearline_context_version`; new `rules.yaml` predicate clauses + a Confidence-Composer component,
  **active only where `gate_passed`**; engine-version bump + an ADR note + tests + golden-test updates.
- **Acceptance.** **output-changing ⇒ reviewed**; a decision *without* yearline hashes **identically**
  to pre-OM-Y4 (back-compat); gate-respect unit-tested; `check_no_broker_imports` + disclaimer gate hold.
  **Dependencies.** OM-Y2 (and ideally RS-4 so the success/composite fields are populated).

## OM-Y5 — stretch (in `option-mgmt-2026`)

- Market-State enrichment (Option B — touches the **locked** 6-regime taxonomy ⇒ ADR-0002 amendment) or
  collar-intent keyed off yearline readiness; ML-node alignment. **Only if OM-Y4 shows the signal earns
  it.**

---

## Track-B acceptance summary

Ship **V13.8** (contract) + **OM-Y0** (ADR) first; then **OM-Y1→Y3** (contract → ingest → read-only
panel) for fast, zero-risk user value; then **OM-Y4** (gated consumption) as the reviewed,
output-changing prize. The boundary is the whole game: **never import yearline into the pure engine; the
engine sees only a versioned, gated `YearlineContext` value object.** Coordinate the two repos only at
that contract (pin + fixture + a cross-repo contract test).
