# Planner — the execution roadmap for what's next

This folder turns the **analysis** docs (`docs/research/`, `docs/option-mgmt-integration/`,
`docs/multi-sector/`) into **spec-grade, sequenced build plans** — the guiding plan for the next phase
of work. Analysis = *why/what*; planner = *how/when, PR by PR*.

> Educational research only. Not financial advice. Every track preserves the engine's invariants:
> capability-before-consumer, output-changing steps gated + reviewed + byte-identical-when-off,
> additive schema, the no-hardcoded-ticker guard, branch→PR→squash, tests via `scripts/run_tests.sh`,
> and honest trust-gating / abstention.

## Priorities

| Track | Scope | Priority | Phase folder | Lives in |
|---|---|---|---|---|
| **A — Retry-success** | RS-1…RS-4: make `P(success │ retry)` trustworthy | ✅ **DELIVERED** (`phase_08/`) | **`phase_08/`** | this repo |
| **B — option-mgmt integration** | V13.8 adapter (this repo) + OM-Y0…Y5 (option-mgmt-2026) | **HIGH (next)** — not started | **`phase_09/`** (yearline side) | this repo + `option-mgmt-2026` |
| **C — Multi-sector** | MS-0…MS-5: widen the universe | **LOWER / deferred** (gated on a data upload) | **`phase_10/`** | this repo |
| **D — Trend outlook** | TO-0…TO-4: make the post-confirmation **trend engine** discriminating + gated (the trend analog of Track A) | **MEDIUM** (TO-0/TO-1 ✅ delivered; TO-2…TO-4 next) | **`phase_11/`** | this repo |

The detailed specs:

- [`01_retry_success_plan.md`](01_retry_success_plan.md) — Track A (RS-1…RS-4), spec-grade.
- [`02_option_mgmt_integration_plan.md`](02_option_mgmt_integration_plan.md) — Track B (V13.8 adapter + OM-Y0…Y5).
- [`03_multi_sector_plan.md`](03_multi_sector_plan.md) — Track C (MS-0…MS-5), condensed (deferred).
- [`04_macro_factors_feature_analysis.md`](04_macro_factors_feature_analysis.md) — **cross-cutting analysis:** would macro factors (10yr rates, VIX, market breadth) improve accuracy? Short answer: causally plausible but **sample-starved** (macro features are market-level/temporally autocorrelated → effective sample = # regimes, not # attempts), they don't fix RS-2's *calibration* gap (RS-3 does), and they're a **data-unlocked** lever to validate **walk-forward** — not now.
- [`05_trend_outlook_plan.md`](05_trend_outlook_plan.md) — Track D (TO-0…TO-4), spec-grade. Brings the trend engine up to the repair side's discipline (resolution → validation → calibration → gated surface), grounded in `docs/research/03` (V12/V13 review) + `docs/research/04` (SOTA: TSMOM/vol-scaling, HMM/changepoint regimes, ADX⊗Hurst, proper-scoring calibration). Surfaces a gated `trend_outlook_context` only if it clears AUC/MACE/**resolution**/n.

## Phase folders — where the built work is recorded (`phase_08+`)

This `planner/` folder is the **cross-track roadmap** (the *why/what/when*). The **delivered-phase
record** for each track lives in a numbered `docs/phased_design/phase_NN/` folder, following the existing
convention (a `README.md` with objective / scope / approach / acceptance + results, and an `artifacts/`
snapshot subfolder) — exactly like `phase_01/…phase_07/`. **New phase-specific docs start at `phase_08`.**

| Track | Phase folder | What goes there | Created |
|---|---|---|---|
| A — retry-success | **`phase_08/`** | RS-1…RS-4 as sub-PRs within the phase (the way Phase 7 held PR-A…E); `README.md` + `artifacts/` (head-to-head vs base rate, calibration, gate) + `reliability/` | ✅ **DELIVERED** (RS-1…RS-4 + reliability) |
| B — option-mgmt (yearline side) | **`phase_09/`** | the V13.8 adapter + the `YearlineContext` contract + schema + fixtures + the cross-repo contract test; `README.md` + `artifacts/` | when V13.8 starts |
| C — multi-sector | **`phase_10/`** | MS-0…MS-5 (deferred, data-gated) | when MS-0 data lands |
| D — trend outlook | **`phase_11/`** | TO-0…TO-4 as sub-PRs (coverage/hygiene → score resolution → regime prob → forward label+validation → calibration+gate+`trend_outlook_context`); `README.md` + `artifacts/` | **TO-0/TO-1 delivered** (TO-2…TO-4 next) |

Notes:
- **`phase_08` and `phase_09` can be in flight in parallel** — the numbers are record IDs, not a strict
  delivery order (just as the V13.x version tags interleave).
- **The `OM-Y*` milestones (Track B) are tracked in `option-mgmt-2026`'s own `docs/phased-design/`** —
  `phase_09/` here records only the **yearline-side** adapter/contract work.
- Each `phase_NN/README.md` cross-links back to its planner spec here (`planner/0N_*.md`) and to the
  source analysis doc (`docs/research/`, `docs/option-mgmt-integration/`, `docs/multi-sector/`).

## Sequencing (how the two priority tracks interleave)

> **Current state (2026-06-09):** **Track A (Phase 8) is DELIVERED** (RS-1…RS-4 + reliability + the Part A
> composite-gating hardening). **Track D (Phase 11)** TO-0/TO-1 are delivered; TO-2…TO-4 remain. **Track B
> (Phase 9 / OM-Y\*) has not started** and is the main open priority; **Track C** stays deferred. The
> original A↔B interleaving below is retained as design rationale for the (still-pending) Track B build.

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
6. **Track D (TO-0/TO-1)** — the trend handoff-coverage + score-resolution quick wins — **interleave right
   after Track A's Part A fix** (both touch the trend-mode path); TO-2…TO-4 (regime prob → forward label →
   calibration/gate) are a medium-priority arc that reuses Track A's gate/calibration primitives and is
   data-gated like Track C.

Suggested **next** PRs (Track A done): **V13.8 adapter** + **OM-Y0 (ADR draft)** to start Track B, and
**TO-2** (regime probability) to continue Track D — in parallel.

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

## Status

**Track A (Phase 8) ✅ DELIVERED · Track D (Phase 11) TO-0/TO-1 ✅, TO-2…TO-4 next · Track B (Phase 9) next
· Track C (Phase 10) deferred.**

| Phase | Item | Status |
|---|---|---|
| `phase_08` | RS-1 success labels + empirical baseline | ✅ delivered (PR #13) |
| `phase_08` | RS-2 success classifier + LOTO eval | ✅ delivered (PR #14) |
| `phase_08` | RS-3 calibration + gate + blend (+ reliability diagnostic) | ✅ delivered (PR #16) |
| `phase_08` | RS-4 gated `retry_success_context` + composite | ✅ delivered (PR #17); composite gating hardened in Part A (PR #22) |
| `phase_09` | V13.8 adapter (YearlineContext export) | ☐ not started (Track B, next) |
| `option-mgmt-2026` | OM-Y0 enhancement + ADR | ☐ not started |
| `option-mgmt-2026` | OM-Y1…Y4 contract → ingest → panel → gated consumption | ☐ not started |
| `option-mgmt-2026` | OM-Y5 stretch | ☐ not started |
| `phase_10` | MS-0 data & taxonomy (deferred) | ☐ (blocked on data) |
| `phase_11` | TO-0 trend handoff coverage + hygiene | ✅ delivered (PR #23) |
| `phase_11` | TO-1 score resolution (de-saturated + de-collinearized indicators) | ✅ delivered (PR #23) |
| `phase_11` | TO-2 regime probability (HMM / changepoint) | ☐ next |
| `phase_11` | TO-3 forward trend label + LOTO validation | ☐ |
| `phase_11` | TO-4 calibration + gate (resolution floor) + gated `trend_outlook_context` | ☐ |
