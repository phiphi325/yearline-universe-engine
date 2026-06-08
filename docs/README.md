# Documentation index

All docs for the V13 Universe Statistical Context Engine.

## Start here

| Doc | Audience | What it covers |
|---|---|---|
| [V13_user_guide.md](V13_user_guide.md) | users / operators | install, configuration, running (single + batch + **parallel**), output reference, interpretation, troubleshooting, and **deployment** (local/cron, Docker, VPS, cloud scheduled-job) |
| [V13_universe_statistical_context_engine_development_spec.md](V13_universe_statistical_context_engine_development_spec.md) | maintainers | architecture spec, build status, phase plan, implementation deviations, and the prioritized roadmap (§11) |

## Engineering notes

| Doc | What it covers |
|---|---|
| [V13_performance_optimization_report.md](V13_performance_optimization_report.md) | profiling, the output-preserving replay vectorization (~13×) and the parallel runner, equivalence proofs, scaling projections, VPS sizing |

## Roadmap / phased design

| Doc | What it covers |
|---|---|
| [option-mgmt-integration/README.md](option-mgmt-integration/README.md) | **Integration analysis & plan** for feeding this engine's statistical-context envelope into the `option-mgmt-2026` MSFT option risk-management engine: the fit (engine-first, no-execution, value-object inputs on both sides), the **pure-engine boundary** (yearline runs in the ingestion/jobs layer, never imported into `packages/engine`), the `YearlineContext` contract + replay-hash + **gate-respect**, three coupling options (read-only panel → gated engine input → market-state), and a phased PR roadmap (OM-Y0…Y5 + the yearline-side V13.8 adapter). **Analysis/plan only — not yet built.** |
| [multi-sector/README.md](multi-sector/README.md) | **Forward-looking analysis & plan** for widening the engine to a multi-sector universe: how to handle sector-dependent behaviour (within-sector normalization, sector-relative cross-section, a sector rung in the estimator hierarchy, sector fixed effects, per-sector gating) and the challenges (sample dilution, base-rate comparability, proxy collinearity, cross-section contamination, point-in-time membership, survivorship, sector rotation, …) + a phased PR roadmap (MS-0…MS-5). **Analysis/plan only — not yet built.** |
| [phased_design/README.md](phased_design/README.md) | The V13.3 phased roadmap: **Phases 1–7 ✅ ALL DELIVERED** — P1 gap×drawdown evidence, P2 conditional timing, P3 empirical-horizon hazard hardening (P40 fix), P4 calibration + trust gate (V13.7), P5 pooled training + data freshness (9 tickers) — **pooling clears the gate at 10/20/40d** (AUC 0.74–0.82); P6 honest (out-of-fold) gate; **P7 discrimination** — a direct horizon classifier ↔ empirical **blend** (opt-in `surface_blend` overlay) that, under leave-one-ticker-out, lifts blend AUC to 0.79–0.84 and clears the gate at all four horizons. Each phase wrapped with its spec + deliverables + artifacts. |

## Tutorials

Seven numbered tutorials (built in roughly the project's phase order). See the index for the full table.

| Doc | What it covers |
|---|---|
| [tutorials/README.md](tutorials/README.md) | **tutorials index** (01–07) |
| 01–05 | performance optimization · optional-computation feature flags · empirical-estimator-over-model-extrapolation (P3) · calibration & trust-gating (P4) · AUC & calibration for ML students |
| 06–07 | **(P7)** direct horizon classifier & the blend · the **MSFT 2026-06-05 low-readiness-repair** worked walkthrough |

## Conventions

- The engine emits **evidence context, never trades** (`must_not_auto_execute: true`). Educational research only.
- Machine-readable artifacts live under `../exports/` (per-ticker envelopes, universe bundle, run manifest, JSON schema, leakage audits).
- Reproduce performance numbers with `../scripts/profile_pipeline.py`.
