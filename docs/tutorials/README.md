# Tutorials

Teaching material built from real work in this repository. Files are numbered in the order the
ideas were built (roughly the project's phase order).

| # | Tutorial | What you'll learn | Companion code |
|---|---|---|---|
| 01 | [01_performance_optimization_tutorial.md](01_performance_optimization_tutorial.md) | A complete, principle-driven method for making code faster *without breaking it* — profiling, finding the real bottleneck, algorithmic vs micro optimization, vectorization, caching, proving output-equivalence, and honest benchmarking. Worked through the real 13× speedup of this engine's daily replay. | `scripts/profile_pipeline.py` |
| 02 | [02_optional_computation_feature_flags_tutorial.md](02_optional_computation_feature_flags_tutorial.md) | Designing optional/expensive computation behind a feature flag — "don't pay for what you don't consume," capability-vs-default, output-preserving defaults, data-dependent cost, and threading a flag through function → pipeline → CLI. Worked through this engine's `fit_ml_models` flag. | `run_ticker_pipeline(..., fit_ml_models=True)` |
| 03 | [03_empirical_estimator_over_model_extrapolation_tutorial.md](03_empirical_estimator_over_model_extrapolation_tutorial.md) | When a model is mis-specified for an extrapolation task, **replace the quantity, not the model**: separate instantaneous vs horizon questions, estimate empirically by "borrowing strength from similar history" (bucketing → scope fallback → Bayesian shrinkage), ship provenance, demote (don't delete) the old output, and handle an **output-*changing*** edit. Worked through the Phase 3 "P40 fix." | `hazard.py`, `test_hazard_empirical.py` |
| 04 | [04_calibration_and_trust_gating_tutorial.md](04_calibration_and_trust_gating_tutorial.md) | How to decide whether a probability is good enough to **show**: **calibration vs discrimination**, the metrics (reliability/Brier/log-loss/AUC/MACE), **leakage-safe purged splits**, the **in-sample optimism trap**, why a monotone transform fixes calibration but **can't add discrimination**, and a **trust gate** that honestly says "not yet." Worked through Phase 4 / V13.7. | `calibration.py`, `test_calibration.py` |
| 05 | [05_auc_and_calibration_for_ml_students.md](05_auc_and_calibration_for_ml_students.md) | **(ML-student audience.)** A from-first-principles course on **AUC** (ROC, ranking interpretation, monotone-invariance) and **calibration / MACE** (reliability diagrams, MACE vs ECE, Brier decomposition), the discrimination-vs-calibration 2×2, **why pooling lifted AUC 0.46→0.78** (a sample-complexity story), and what those metrics do and don't buy an investor. | Phases 4–5 numbers |
| 06 | [06_direct_horizon_classifier_and_blend_tutorial.md](06_direct_horizon_classifier_and_blend_tutorial.md) | **(Phase 7.)** Adding a learned model *on top of* a working estimator without fooling yourself: **discrimination over recalibration**, leakage-safe path + cross-sectional features (with a truncation test), counting **episodes not rows**, episode-aware + **leave-one-ticker-out** CV, the **ranker/estimator tradeoff**, the **hierarchical-shrinkage blend**, and shipping it **opt-in + additive + gated**. Reports the honest negatives (60d) too. | `features.py`, `cross_sectional.py`, `models.py`, `generalization.py`, `blend_surface.py` |
| 07 | [07_msft_low_readiness_repair_blend_walkthrough.md](07_msft_low_readiness_repair_blend_walkthrough.md) | **(Phase 7, worked example.)** One real state — **MSFT, 2026-06-05**, a low-readiness repair — end to end: what the static count sees, what the path features add, how the classifier **tempers** the near-term probabilities (blend 10d 0.262→0.218, 20d 0.418→0.306, 40d 0.603→0.548, converging at 60d 0.687→0.696), the gate, and the exact gated envelope block. | `blend_surface.py`, `phase_07/` |

Audiences: tutorials 01–04 target **junior software engineers** (engineering lessons; finance
incidental); 05–06 target a **college ML student / ML engineer** (metric & modeling theory, the
engine as the running example); 07 is a concrete **worked example** anyone on the project can read.
None of this is investment advice.

Related: `docs/V13_performance_optimization_report.md` (the perf case study) and
`docs/phased_design/` (the phase-by-phase case studies the tutorials teach from — especially
`phase_03/`, `phase_04/`, and `phase_07/`).
