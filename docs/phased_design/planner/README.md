# Planner — the execution roadmap for what's next

This folder turns the **analysis** docs (`docs/research/`, `docs/option-mgmt-integration/`,
`docs/multi-sector/`) into **spec-grade, sequenced build plans** — the guiding plan for the next phase
of work. Analysis = *why/what*; planner = *how/when, PR by PR*.

> Educational research only. Not financial advice. Every track preserves the engine's invariants:
> capability-before-consumer, output-changing steps gated + reviewed + byte-identical-when-off,
> additive schema, the no-hardcoded-ticker guard, branch→PR→squash, tests via `scripts/run_tests.sh`,
> and honest trust-gating / abstention.

## Priorities

| Track | Scope | Priority | Lives in |
|---|---|---|---|
| **A — Retry-success** | RS-1…RS-4: make `P(success │ retry)` trustworthy (today a gated-off prototype) | **HIGH (now)** | this repo |
| **B — option-mgmt integration** | V13.8 adapter (this repo) + OM-Y0…Y5 (option-mgmt-2026) | **HIGH (now)** | this repo + `option-mgmt-2026` |
| **C — Multi-sector** | MS-0…MS-5: widen the universe | **LOWER / deferred** (gated on a data upload) | this repo |

The detailed specs:

- [`01_retry_success_plan.md`](01_retry_success_plan.md) — Track A (RS-1…RS-4), spec-grade.
- [`02_option_mgmt_integration_plan.md`](02_option_mgmt_integration_plan.md) — Track B (V13.8 adapter + OM-Y0…Y5).
- [`03_multi_sector_plan.md`](03_multi_sector_plan.md) — Track C (MS-0…MS-5), condensed (deferred).

## Sequencing (how the two priority tracks interleave)

These two tracks are **mostly parallel** but share one important coupling:

1. **Start in parallel** (both low-dependency, both in this repo):
   - **RS-1** (success labels + empirical base-rate estimator) and **V13.8** (the adapter that emits the
     `YearlineContext` contract) can begin immediately and independently.
2. **Design the contract once, for both.** The **`YearlineContext`** schema (V13.8 / Track B) should
   **anticipate the Track-A success fields** — include *optional, gated-off* `p_success` /
   `p_successful_reclaim` (the occurrence×success composite) placeholders **now**, so when RS-4 lands
   the contract does **not** churn. **This is the single cross-track dependency to respect.**
3. **OM-Y0** (the enhancement assessment + ADR in `option-mgmt-2026`) needs only the V13.8 **contract
   draft**, not the full Track-A build — so it can proceed once V13.8's schema is drafted.
4. **RS-2/RS-3** (classifier → calibrate/gate/blend) and **OM-Y1…Y4** (contract → ingest → panel →
   gated consumption) then proceed independently; **RS-4** (surfacing the success block) enriches what
   OM-Y3/Y4 consume.
5. **Track C (MS-0)** is **deferred** until a multi-sector data upload exists; it does not block A or B.

Suggested first three PRs: **RS-1**, **V13.8 adapter**, **OM-Y0 (ADR draft)** — in that order or in
parallel.

## Cross-cutting acceptance bar (applies to every PR in every track)

- **Capability-before-consumer**: ship a module + tests + a measurement first; surface nothing until it
  has earned a gate.
- **Honest gating**: AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50, **out-of-fold**; abstain where it fails (say
  "not yet"). Validate under **leave-one-ticker-out**, not just purged k-fold; report **lift over the
  base rate**.
- **Output-changing ⇒ gated + reviewed**: opt-in flag, default OFF ⇒ envelope **byte-identical**;
  before/after diff in the PR.
- **Additive schema**, **no-hardcoded-ticker** AST guard holds, full per-file suite green.
- **Data is the lever**: where a gate fails for lack of sample, the deliverable is the *validated method*
  + an honest "needs more data," not a forced number.

## Status (all planned)

| Item | Status |
|---|---|
| RS-1 success labels + empirical baseline | ☐ |
| RS-2 success classifier + LOTO eval | ☐ |
| RS-3 calibration + gate + blend | ☐ |
| RS-4 gated `retry_success_context` + composite | ☐ |
| V13.8 adapter (YearlineContext export) | ☐ |
| OM-Y0 enhancement + ADR (option-mgmt-2026) | ☐ |
| OM-Y1…Y4 contract → ingest → panel → gated consumption | ☐ |
| OM-Y5 stretch | ☐ |
| MS-0 data & taxonomy (deferred) | ☐ (blocked on data) |
