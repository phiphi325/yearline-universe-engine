# Track A — Retry-success build plan (RS-1 … RS-4)

*Spec-grade execution plan to make `P(success │ retry)` trustworthy. Derived from
`docs/research/01_retry_success_probability_2026-06-08.md`. Educational research; not financial advice.*

**Goal.** Today retry **occurrence** (`P(retry ≤ H)`) is mature (calibrated, gated, surfaced); retry
**success** (given an attempt, does it *reclaim and hold*?) is a **gated-off, uncalibrated prototype**
(`fit_retry_quality_classifier`) that barely beats the ~0.35 base rate. RS-1…RS-4 apply the exact
playbook that fixed occurrence in Phases 3–7 — and surface the result **only if it earns a gate**.

**Definition (fixed, do not redefine).** Success = `classify_attempt_outcome_v10_parity` returns
`"success"`: close > MA250 for `confirm_days` consecutive days, then hold (≥70% closes above) over
`success_hold_days`. `"fail"` = rejected within `new_attempt_gap`, or pending past `max_scan_days`.
Label only **completed** attempts (leakage-safe censoring); never label a live/pending attempt.

---

## RS-1 — Success labels + empirical base-rate-by-bucket estimator (pooled)

- **Objective.** A leakage-safe **attempt-level** success dataset + an **empirical** "of similar
  historical attempts, what fraction succeeded?" estimator — the calibrated baseline everything else
  must beat.
- **Deliverable / modules.**
  - `src/yearline_universe/success_labels.py` (new): `build_success_dataset(tickers_data)` →
    attempt-level rows keyed by `transition_key` / attempt, with `y_success ∈ {0,1}` from the canonical
    `outcome`, pending excluded; carries the static repair state + (for RS-2) the merge keys for
    path/cross-sectional features.
  - `build_empirical_success_reference()` + `empirical_success_probability_for_row()` — mirror
    `hazard.build_empirical_horizon_reference` / `empirical_horizon_probabilities_for_row`: a
    **scope-ladder** (ticker → peer/transition → sector → universe) with a row-count floor + **Bayesian
    shrinkage** to the parent success rate. Pool the universe.
- **Approach.** Reuse the recovery/episode tables (`episodes.py`, `next_attempt_success`) + the
  detector outcome (`event_detection.py`). Bucket by distance / drawdown / readiness / regime (same
  bucketers as the occurrence estimator).
- **Acceptance.** Labels match `classify_attempt_outcome` on fixtures; pending excluded (leakage test);
  empirical estimate reports `reference_n` + `scope` (provenance); measured vs the flat base rate.
  **Capability-before-consumer — not surfaced.**
- **Tests.** `tests/test_success_labels.py`: label correctness, censoring, estimator shape +
  shrinkage + provenance, real-ticker finiteness.
- **Dependencies.** `event_detection`, `episodes`, `pooling`. **Risks.** Tiny sample (attempts, not
  rows); class imbalance.

## RS-2 — Direct success classifier + episode/attempt-aware & leave-one-ticker-out CV

- **Objective.** A **regularized-logistic** success classifier on the **readiness + cross-sectional**
  features the prototype lacks; honest OOF head-to-head vs the RS-1 empirical baseline.
- **Deliverable / modules.** `src/yearline_universe/success_models.py` (or extend `models.py`): reuse
  `make_direct_horizon_logistic` (impute→scale→L2 logistic) + GBM diagnostic; build the success
  modeling table = RS-1 labels + `features.build_price_path_features` + `cross_sectional` features +
  the empirical baseline column. Reuse `generalization._grouped_oof` for **purged-by-attempt** *and*
  **leave-one-ticker-out** CV + `episode_row_weights`.
- **Acceptance.** Report **AUC, Brier, log-loss, MACE/ECE, reliability slope, and lift over the success
  base rate**; the leave-one-ticker-out generalization gap; head-to-head vs RS-1. Honest negatives.
  Capability-only.
- **Tests.** `tests/test_success_models.py`: planted-signal AUC; transition-purged-CV-not-optimistic;
  structure; empty/degenerate grace.
- **Dependencies.** RS-1, `features.py`, `cross_sectional.py`, `generalization.py`. **Risks.** Overfit
  on tens of attempts → logistic primary, GBM diagnostic-only; **expect weak discrimination** and say so.

## RS-3 — Calibration + trust gate + classifier↔empirical blend (abstain until gated)

- **Objective.** Calibrate the success probability and decide, honestly, whether it is good enough to
  show.
- **Deliverable / modules.** Reuse `calibration.py` (isotonic, purged OOF, honest MACE) + the Phase-7
  **blend** (`generalization`-style convex `w·classifier + (1−w)·empirical`, `w` by OOF Brier) for
  success; the per-bucket **trust gate** (AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50). Add a **walk-forward
  backtest** (V12.6 style) over time/regimes.
- **Acceptance.** Out-of-fold gate computed and reported per bucket/scope; **abstain** where it fails.
  **Honest expectation: the gate likely FAILS on current data** → the deliverable is the validated
  method + a documented "not yet" + the data ask. Blend bounded in [0,1].
- **Tests.** Gate pass/fail well-formed; blend bounds; walk-forward harness runs.
- **Dependencies.** RS-2.

## RS-4 — Gated, additive surfacing: `retry_success_context` + the occurrence×success composite

- **Objective.** Surface the success probability the same disciplined way the Phase-7 blend was surfaced
  — opt-in, additive, gated — and add the genuinely useful composite.
- **Deliverable / modules.** `context_export.py`: an **additive** `retry_success_context` block (the
  success analog of `retry_hazard_context`) — `p_success` + per-bucket `gate_passed` + basis +
  provenance — attached **only** when `surface_success=True` (opt-in) **and** the gate passes. Add
  `p_successful_reclaim_within_{H}` = `P(retry ≤ H) × P(success │ retry)` **only where both gates pass**.
  Thread `surface_success` + a compute-once success model through `run_hazard_layer` /
  `run_ticker_pipeline` / `run_universe_pipeline` exactly like `surface_blend` / `calibration_model`.
  The empirical **occurrence** estimate stays canonical; success is a **separate, labelled** block —
  never overwrites anything.
- **Acceptance.** Default (`surface_success=False`) ⇒ envelope **byte-identical** (verified additive-only
  across the universe). Output-changing ⇒ **gated review** + before/after on MSFT. Surfaced only where
  the gate passes; `must_not_auto_execute` preserved.
- **Tests.** `tests/test_success_surface.py`: envelope additive-and-gated; byte-identity-when-off;
  composite only when both gates pass.
- **Dependencies.** RS-3; the `surface_blend` wiring (template).

---

## Track-A acceptance summary

Promote to surfaced (RS-4) **only** if, under **leave-one-ticker-out**, the blended success probability
**beats the base rate on AUC without MACE regression and clears the gate**. Otherwise ship RS-1…RS-3 as
**capability + an honest "not yet,"** and record that the binding lever is **more labelled attempts** (a
wider/multi-sector universe + deeper history — Track C). Cross-track note: the **V13.8 `YearlineContext`
contract (Track B) must reserve optional, gated-off `p_success` / `p_successful_reclaim` fields now** so
RS-4 doesn't force a contract change later.
