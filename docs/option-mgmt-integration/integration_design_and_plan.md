# Integration design & phased plan — yearline-universe → option-mgmt-2026

*Pairs with `assessment.md` (the why). Concrete, idiomatic to option-mgmt's conventions
(value-object inputs, ingestion/jobs layer, three-pin replay, Pydantic→TS codegen, ADR + enhancement
process, engine-version bump, no-execution). Educational research; not financial advice.*

---

## 1. The integration contract — `YearlineContext`

A small, **flat, JSON-serializable** value object (the *subset* of yearline's envelope the engine
needs). Pydantic on the option-mgmt side; emitted verbatim by yearline's V13.8 adapter. Illustrative:

```python
class YearlineContext(BaseModel, frozen=True):           # packages/engine/engine/yearline/types.py
    as_of: date
    ticker: str                                          # "MSFT"
    schema_version: str                                  # yearline envelope schema_version
    model_stack_version: str                             # yearline model_stack_version (pin)
    # --- structural regime ---
    repair_active: bool                                  # repair/hazard engine active (below MA250)
    distance_to_ma250_pct: float | None
    required_rebound_to_ma250_pct: float | None
    post_confirmation_trend_state: str | None            # when above MA250
    # --- gated retry probability (consume ONLY where gated) ---
    p_retry: dict[int, float]                             # {10,20,40,60} -> P(retry<=H)
    p_retry_basis: Literal["empirical", "blend"]          # which surface produced p_retry
    gate_passed: dict[int, bool]                          # per-horizon trust-gate pass
    # --- conditional timing ---
    days_to_touch_central: float | None
    days_to_touch_low: float | None
    days_to_touch_high: float | None
    # --- provenance ---
    reference_scope: str | None                           # e.g. "group_transition" (sample transparency)
    is_stale: bool                                        # yearline's freshness flag
    must_not_auto_execute: Literal[True] = True
```

- **Codegen (I7):** add to the Pydantic→TS generation so `apps/web` gets `YearlineContext.ts`,
  drift-checked in CI — same gate as `regimes.ts`/`profiles.ts`.
- **Versioning:** `schema_version` + `model_stack_version` travel with the object and enter the replay
  hash (below). Bump yearline's adapter version on any contract change; pin the accepted range in
  option-mgmt.

### 1.1 Gate *status* vs model *performance* — and the deferred `gate_diagnostics` block

`YearlineContext` deliberately carries the **outcome** of the trust gate, not its inputs: per-horizon
`gate_passed` (+ `success_gate_passed`), `p_retry_basis`, and `reference_scope` (the sample bucket). That is
enough for the consumer to **gate-respect** and for the Today-screen card to show *"trustworthy now /
withheld / which surface / which bucket / stale."*

It does **not** carry the gate's diagnostics — **AUC, MACE (calibration error), and sample size `n`** (scored
against AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50). So the contract answers *"is this number trustworthy now?"* but
**not** *"is the model performing well over time?"*

**Decision (V13.8.3): defer an optional `gate_diagnostics` block; keep it off the end-user card.**
- The diagnostics already exist in yearline's calibration context, so projecting a per-horizon
  `gate_diagnostics: {auc, mace, n, thresholds}` is a **thin projection — but a contract-shape change** ⇒
  **bump `ADAPTER_VERSION`** ⇒ a coordinated **OM-Y1** pin bump + a fixtures refresh. A *deliberate,
  versioned* add, not free.
- **When added**, it feeds an **internal ops/health view** (model monitoring), **not** the holder-facing
  card — an end user should read *gated/withheld*, never an AUC. Keeps the user surface honest and the
  decision contract lean.
- **Until then**, model health is read from yearline's own calibration/reliability reports (Phase 8
  `…/reliability`), and the card shows gate **status** only.

This resolves open question #5: *display all gated **status** on the card; treat AUC/MACE/`n` as a separate,
versioned `gate_diagnostics` add for an internal view — not the end-user surface.*

## 2. Data flow (idiomatic to option-mgmt's lifecycle)

```
nightly:  yearline-universe (pooled 9-ticker run) → emit MSFT envelope (V13.8 adapter)
ingest:   apps/api/app/jobs/ingest_yearline.py    → persist to Postgres `yearline_context`
                                                      (as_of, ticker, schema_version, model_stack_version,
                                                       payload JSONB, payload_hash)
request:  POST /engine/daily-plan                 → hydration service loads latest yearline_context row
                                                      → YearlineContext value object
engine:   produce_daily_decision(..., yearline_context=YearlineContext|None)   # pure; optional kwarg
persist:  DailyDecision row gains yearline_context_version pin (replay)
surface:  Today screen renders a yearline evidence panel; rules/confidence use it only where gated
```

This is the same shape as market-data ingestion → `inputs_hydration_service.py` → `produce_daily_decision`
→ `daily_decisions`. yearline is one more hydrated, versioned input.

## 3. Replay-hash extension (I3)

`compute_inputs_hash()` currently hashes `(as_of, ticker, chain, positions, profile, market_state,
flow_score)`. Add the yearline contract as a hashed input (only when present), e.g. canonical-JSON of
`{schema_version, model_stack_version, <consumed fields>}`. Persist a **fourth pin**
`yearline_context_version` on `DailyDecision`. Result: a yearline-influenced decision stays exactly
replayable; a decision with no yearline input hashes identically to today (back-compat).

## 4. Three coupling options (a spectrum, not a fork)

| Option | What | Coupling | Engine change? | When |
|---|---|---|---|---|
| **C — read-only evidence panel** | Persist + surface yearline context on the Today screen and a read endpoint; **decision unchanged** | lowest | none | **first** (fastest value, zero risk) |
| **A — gated engine input** | `produce_daily_decision(..., yearline_context=...)`; new `rules.yaml` clauses + a Confidence Composer component, used **only where the gate passes** | medium | yes (additive, gated) | **main goal** |
| **B — Market-State enrichment** | Feed yearline into the Market State classifier / a regime nuance | highest (touches the **locked** 6-regime taxonomy, ADR-0002) | yes (ADR-level) | **later / maybe** |

Recommended trajectory: **C → A**, with **B** only if A proves the signal and an ADR justifies
touching the taxonomy. C delivers visible value immediately and de-risks the contract before any
decision logic depends on it.

## 5. Phased PR roadmap

### option-mgmt-2026 side (mirrors its milestone + enhancement/ADR discipline)

| PR | Deliverable | Gate / acceptance |
|---|---|---|
| **OM-Y0 — enhancement + ADR** | A `docs/enhancements/` assessment of yearline (mirroring ADR-0008's enhancement-adoption process) **+ a proposed ADR**: "adopt yearline as an external statistical-context provider," fixing the boundary (value object; jobs-layer producer; replay-hash extension; gate-respect; no engine import). | ADR accepted; boundary + contract agreed; no code yet. |
| **OM-Y1 — the contract** | `YearlineContext` Pydantic model + TS codegen + fixtures (gated MSFT example + a stale/missing example). No behaviour change. | codegen drift gate green; fixtures load; mypy --strict clean. |
| **OM-Y2 — ingestion + persistence** | Alembic migration for `yearline_context`; `apps/api/app/jobs/ingest_yearline.py` (runs/loads yearline's adapter output, persists payload + version + hash); hydration service → `YearlineContext`. | idempotent persistence; stale/missing handled (row absent ⇒ `None`); smoke covers it. |
| **OM-Y3 — read-only surfacing (Option C)** | `GET /engine/yearline-context` (read-only) + a Today-screen **evidence panel** (repair state, gated `P(retry≤H)`, days-to-touch range, trend state) with disclaimer. **No decision change.** | panel renders from a fixture; decision payload byte-identical; disclaimer present. |
| **OM-Y4 — gated engine consumption (Option A)** | `produce_daily_decision(..., yearline_context=None)`; extend `compute_inputs_hash` + the `yearline_context_version` pin; new `rules.yaml` predicate clauses + a Confidence Composer component, **active only where `gate_passed`**; engine-version bump + ADR note + tests. | **output-changing → reviewed**; a decision *without* yearline hashes identically to pre-OM-Y4 (back-compat); gate-respect unit-tested; golden tests updated. |
| **OM-Y5 — stretch** | Market-State enrichment (Option B, ADR-0002 amendment) and/or collar-intent keyed off yearline readiness; deeper ML-node alignment. | only if OM-Y4 shows the signal earns it under option-mgmt's own evaluation. |

### yearline-universe side (this repo)

| PR | Deliverable | Note |
|---|---|---|
| **V13.8 — repo-integration adapter** (already a *pending* item in this repo's spec) | A stable, versioned export of the `YearlineContext` subset (+ JSON schema + the `schema_version`/`model_stack_version` pins); a thin adapter the option-mgmt job can consume (file/Postgres/object-store handoff — **not** a heavy import). | This integration *is* V13.8. Keep the universe pooling that makes MSFT trustworthy. |
| (ops) | A nightly pooled run + freshness flag already exist (incremental cache, staleness warnings); document the SLA option-mgmt depends on. | degrade-gracefully contract. |

## 6. Acceptance & guardrails (every PR)

- **Never import yearline into `packages/engine`** (I1). Heavy work stays in the jobs layer; the engine
  sees only the `YearlineContext` value object.
- **Back-compat:** a decision with no/absent yearline input is **byte-identical** to today and hashes
  identically (yearline is an *optional* kwarg, like the `futures_basis` stub).
- **Gate-respect:** the probability influences the decision **only where yearline's gate passes**;
  otherwise context-only.
- **Determinism:** the yearline contract version + consumed-field hash enter the replay identity.
- **option-mgmt house rules hold:** Pydantic→TS drift gate, `mypy --strict`, engine-version bump on
  engine changes, `check_no_broker_imports`, disclaimer gate, conventional-commit squash PRs.
- **No execution / educational-only** preserved end to end (both already enforce this).

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Heavy deps leak into the pure engine | value-object boundary + jobs-layer isolation; CI keeps `packages/engine` lean. |
| Schema/version drift across two repos | pinned contract + `schema_version`/`model_stack_version` in the replay hash + a **contract test** on both sides; a documented compatibility matrix. |
| Stale / missing yearline data | optional kwarg → engine degrades gracefully; yearline's `is_stale` surfaced; panel shows "context unavailable." |
| Pooling/operational dependency (MSFT trust needs the universe) | run the pooled 9-ticker job nightly even though only MSFT is consumed; monitor the gate. |
| Touching locked taxonomies (regimes/rules) | prefer rules-clauses + confidence-component (Option A) over regime change (Option B); any taxonomy change is an explicit ADR. |
| Over-trusting an un-gated probability | hard rule: consume `P(retry)` only where `gate_passed`; else context-only. |
| Educational-use / no-advice framing | align disclaimers; preserve `must_not_auto_execute` through to the UI. |

## 8. Open questions for you

1. **Delivery mechanism:** persisted-artifact handoff (Postgres/object-store) — *recommended* for loose
   coupling — vs yearline as a packaged dependency **in the jobs layer only** vs a small HTTP endpoint?
2. **First increment:** start with **Option C** (read-only panel — fastest, zero decision risk), or go
   straight to **OM-Y4** (gated engine consumption)?
3. **Where do the integration PRs live?** This assessment is in the yearline repo; the actual changes
   are in option-mgmt-2026 (OM-Y*) + this repo's V13.8 adapter. Want me to draft the OM-Y0
   enhancement/ADR doc against `option-mgmt-2026` next?
4. **Scope:** MSFT-only (matches option-mgmt Phase 1), or wire the contract for the broader universe now?
5. **Which yearline fields** should actually influence the decision vs. display-only? (Proposal:
   display all; let only gated `P(retry≤H)` + repair/trend state influence rules/confidence at first.)

## 9. Bottom line

The integration is **architecturally clean** because option-mgmt already consumes pre-computed,
versioned, validated context objects and already lives by yearline's own values (deterministic,
auditable, no-execution, gated). The work is **contract + ingestion + gated consumption**, not
re-architecture. Do **C first** (visible value, de-risks the contract), then **A** (the real prize),
and let yearline's **V13.8 adapter** be the stable bridge.
