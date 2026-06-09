# V13.3 Phased Design

A value-first, trust-last roadmap for delivering **time-to-next-touch** as
credible, evidence-backed context — and for fixing the forward hazard probability
before it is ever surfaced. Each phase is an independently-approvable deliverable
with its own spec + acceptance gate, wrapped in its own folder here.

Guiding principle: the descriptive evidence (drawdown↔time correlation, the
gap×drawdown matrix) and the gap-based conditional timing are the robust, defensible
value and ship first; the forward hazard *probability* is an ill-posed step
(see `../V13_data_and_report_analysis.md` §2.2) and must be hardened then calibrated
before it is trusted. Pooling + data freshness are supporting work, not the step fix.

**Refinement (2026-06-07), from the user's patched benchmark notebook
(`…_PATCHED_P40_02.ipynb`) + the V12.6 report:** the fix is a **probability-policy**
change, not a model re-fit. The logistic model is kept only for `hazard_today`; the
saturating state-hold-forward curve is **demoted to a labelled diagnostic**; and the
canonical P(retry ≤ H) becomes an **empirical completed-path estimator** (similar
historical states, bucketed + shrunk). That empirical estimator is what the V12.6
report actually calibrates (well through 40d; see §2.3). So **Phase 3 = port the
V12.4.1 empirical-horizon policy** (no feature-dropping / standardising / regularising),
and Phase 4 ports the V12.6 harness that scores it.

## Phases

| Phase | Deliverable | Status | Folder |
|---|---|---|---|
| **1** | V13.3 pooled gap×drawdown **evidence** (matrix + Spearman + attempt-success) in the bundle | ✅ DELIVERED | [phase_01/](phase_01/) |
| **2** | Conditional **days-to-touch estimators** (median / matrix-interp / nearest-neighbor / Theil-Sen) as an additive per-ticker `retry_timing_context` | ✅ DELIVERED | [phase_02/](phase_02/) |
| **3** | Hazard **hardening** — port the V12.4.1 empirical-horizon policy: empirical completed-path P(retry≤H) as canonical, model kept for `hazard_today`, state-hold-forward demoted to diagnostic (threaded through hazard/replay/envelope) | ✅ DELIVERED | [phase_03/](phase_03/) |
| **4** | **Calibration** (V13.7) — port the V12.6 horizon-reliability harness (scores the empirical estimator), add isotonic + purged transition-aware splits, fill `calibration_context` + a per-horizon **trust gate** (opt-in `calibrate=True`) | ✅ DELIVERED | [phase_04/](phase_04/) |
| **5** | Supporting — **pooled** hazard/reference/calibration training + universe **data freshness** (9 tickers current). **Pooling clears the Phase 4 trust gate at 10/20/40d.** | ✅ DELIVERED | [phase_05/](phase_05/) |
| **6** | **Honest gate** — gate on an out-of-fold (purged-by-transition) isotonic-calibrated MACE, not the in-sample-optimistic value. Tightens 40d; shows 60d is sample/regime-limited (honest abstention). | ✅ DELIVERED | [phase_06/](phase_06/) |
| **7** | **Discrimination, not recalibration** — leakage-safe path + cross-sectional features → a direct horizon **classifier**, validated under **leave-one-ticker-out**; the classifier↔empirical **blend** beats both on AUC + calibration at every horizon (incl. a gate-passing 60d). Shipped as an **opt-in, additive, gated** envelope overlay (`surface_blend`); empirical stays canonical. | ✅ DELIVERED | [phase_07/](phase_07/) |
| **8** | **Retry-success (Track A)** — make `P(success │ retry)` trustworthy (distinct from retry *occurrence*). **RS-1:** leakage-safe attempt dataset (162 attempts, base rate 0.352) + empirical baseline (AUC ≈ 0.49 — no signal; sets the bar) + an event-detection alignment audit. **RS-2:** a direct success classifier on readiness + cross-sectional features **beats** the baseline and the base rate on **discrimination** (leave-one-ticker-out AUC ≈ 0.71) but is mis-calibrated (MACE ≈ 0.13). **RS-3:** the classifier↔empirical **blend clears the trust gate** (AUC 0.702, honest OOF MACE 0.036) — gate-passing surface for the success probability (thin sample; re-validate walk-forward; a reliability diagnostic shows ~87% of the calibration win is base-rate shrinkage). **RS-4:** the gated blend is surfaced live as an opt-in, additive `retry_success_context` overlay + the composite `P(reclaim≤H)=P(retry≤H)×P(success│retry)` (surfaced only where both gates pass); default off ⇒ envelope byte-identical. | ✅ DELIVERED | [phase_08/](phase_08/) |

Recommended order: 1 → 2 (ship validated value) → 3 → 4 (make any probability
trustworthy), with 5 supporting throughout, then 6 (honest gate) and 7 (discrimination).
Execution pauses for a go-check between phases. **Status: Phases 1–7 ALL DELIVERED.** The
forward "days-to-touch" probability is now a credible, calibrated, gated quantity at ≤40d on
current pooled data (Phase 5 pooling lifted the empirical estimator's AUC to 0.74–0.82; the
trust gate passes at 10/20/40d). **Phase 7** adds a learned **discriminative overlay**: a
direct horizon classifier blended with the empirical estimator that, under the unseen-name
(leave-one-ticker-out) test, lifts blend AUC to 0.79–0.84 and clears the gate at all four
horizons — surfaced opt-in beside the canonical empirical number, never replacing it. The
largest remaining lever is a **wider / multi-sector universe** (a data unlock), not more code.

## Planner — what's next

[`planner/`](planner/) holds the **forward execution roadmap** (spec-grade, sequenced) that turns the
analysis docs into PR-by-PR build plans:

- [`planner/01_retry_success_plan.md`](planner/01_retry_success_plan.md) — **Track A (HIGH):** retry-success RS-1…RS-4 → delivered as **`phase_08/`**.
- [`planner/02_option_mgmt_integration_plan.md`](planner/02_option_mgmt_integration_plan.md) — **Track B (HIGH):** V13.8 adapter + OM-Y0…Y5 → yearline side delivered as **`phase_09/`** (OM-Y* tracked in `option-mgmt-2026`).
- [`planner/03_multi_sector_plan.md`](planner/03_multi_sector_plan.md) — **Track C (LOWER / deferred, data-gated):** MS-0…MS-5 → delivered as **`phase_10/`**.

**New phase folders start at `phase_08`.** Each planner track is recorded as a numbered
`phase_NN/` folder (README + `artifacts/`, like `phase_01…07`) when its build starts; `phase_08` and
`phase_09` may run in parallel. See [`planner/README.md`](planner/README.md) for priorities, cross-track
sequencing, the phase mapping, and the shared acceptance bar.

## Conventions for each phase folder

```text
phase_NN/
  README.md      # objective, scope, approach, acceptance criteria + results,
                 # cross-check, files changed, reproduce, limitations, decision gate
  artifacts/     # self-contained snapshots of that phase's outputs
```

## Related docs

- `../V13_data_and_report_analysis.md` — the data/report cross-check that motivated this roadmap (incl. the §2.2 hazard-step diagnosis).
- `../V13_universe_statistical_context_engine_development_spec.md` — the overall architecture spec & phase plan (§8/§11).
- `../V13_performance_optimization_report.md`, `../V13_user_guide.md`.
