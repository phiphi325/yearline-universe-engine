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
