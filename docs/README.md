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
| [phased_design/README.md](phased_design/README.md) | The V13.3 value-first phased roadmap: **Phases 1–5 ✅ ALL DELIVERED** — Phase 1 gap×drawdown evidence, Phase 2 conditional timing, Phase 3 empirical-horizon hazard hardening (P40 fix), Phase 4 calibration + trust gate (V13.7), Phase 5 pooled training + data freshness (9 tickers current) — **pooling clears the gate at 10/20/40d** (AUC 0.74–0.82). Each phase wrapped with its spec + deliverables + artifacts. |

## Tutorials

| Doc | What it covers |
|---|---|
| [tutorials/performance_optimization_tutorial.md](tutorials/performance_optimization_tutorial.md) | a junior-engineer tutorial on performance optimization, taught from this engine's real optimizations |
| [tutorials/README.md](tutorials/README.md) | tutorials index |

## Conventions

- The engine emits **evidence context, never trades** (`must_not_auto_execute: true`). Educational research only.
- Machine-readable artifacts live under `../exports/` (per-ticker envelopes, universe bundle, run manifest, JSON schema, leakage audits).
- Reproduce performance numbers with `../scripts/profile_pipeline.py`.
