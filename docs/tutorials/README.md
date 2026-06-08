# Tutorials

Teaching material built from real work in this repository.

| Tutorial | What you'll learn | Companion code |
|---|---|---|
| [performance_optimization_tutorial.md](performance_optimization_tutorial.md) | A complete, principle-driven method for making code faster *without breaking it* — profiling, finding the real bottleneck, algorithmic vs micro optimization, vectorization, caching, proving output-equivalence, and honest benchmarking. Worked through the real 13× speedup of this engine's daily replay. | `scripts/profile_pipeline.py` (reproduce the profile) |
| [optional_computation_feature_flags_tutorial.md](optional_computation_feature_flags_tutorial.md) | Designing optional/expensive computation behind a feature flag — "don't pay for what you don't consume," capability-vs-default, output-preserving defaults, data-dependent cost, and threading a flag through function → pipeline → CLI. Worked through this engine's `fit_ml_models` flag. | `run_ticker_pipeline(..., fit_ml_models=True)` |
| [empirical_estimator_over_model_extrapolation_tutorial.md](empirical_estimator_over_model_extrapolation_tutorial.md) | When a model is mis-specified for an extrapolation task, **replace the quantity, not the model**: separate the instantaneous vs horizon question, estimate empirically by "borrowing strength from similar history" (bucketing → hierarchical scope fallback → Bayesian shrinkage), ship provenance, demote (don't delete) the old output, and handle an **output-*changing*** edit (before/after + cache/version bumps) vs an output-preserving one. Worked through the Phase 3 "P40 fix." | `src/yearline_universe/hazard.py`, `tests/test_hazard_empirical.py` |
| [calibration_and_trust_gating_tutorial.md](calibration_and_trust_gating_tutorial.md) | How to decide whether a probability is good enough to **show**: **calibration vs discrimination**, the metrics (reliability/Brier/log-loss/AUC/MACE), **leakage-safe purged (transition-aware) splits**, the **in-sample optimism trap**, why a monotonic transform (isotonic/Platt) fixes calibration but **can't add discrimination** (AUC-invariant), and a **trust gate** that honestly says "not yet." Worked through Phase 4 / V13.7, where the gate correctly fails on thin single-ticker data. | `src/yearline_universe/calibration.py`, `tests/test_calibration.py` |
| [auc_and_calibration_for_ml_students.md](auc_and_calibration_for_ml_students.md) | **(ML-student audience.)** A from-first-principles course on **AUC** (ROC, the ranking interpretation, compute-by-hand, monotone-invariance) and **calibration / MACE** (reliability diagrams, MACE vs ECE, Brier decomposition), the discrimination-vs-calibration 2×2, **why pooling lifted AUC 0.46→0.78 and cut MACE 0.3→<0.08 at 10/20/40d** (a sample-complexity story), what those metrics do and don't buy an investor, and concrete ways to improve further (CV isotonic, more data, survival models, conformal). | Phases 4–5 numbers; `hazard.py` / `calibration.py` |

Audiences: tutorials 1–4 target **junior software engineers** (engineering lessons; finance incidental).
The AUC/calibration tutorial targets a **college ML student** (metric theory; the engine is the running example).
None of this is investment advice.

Related: `docs/V13_performance_optimization_report.md` (the perf case study) and
`docs/phased_design/phase_03/` + `phase_04/` (the hazard-hardening and
calibration/gating case studies the last two tutorials teach from).
