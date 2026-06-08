# Phase 2 — V13.3 Conditional Days-to-Next-Touch Estimators

**Status:** ✅ DELIVERED (2026-06-07)
**Part of:** the V13.3 phased roadmap (see `../README.md`)
**Theme:** the *credible* "days left until the next MA250 touch" — a multi-method,
uncertainty-bearing **range** derived from descriptive history, NOT the ill-posed
forward hazard probability (that step artifact is deferred to Phase 3 hardening +
Phase 4 calibration).

> Educational research only. Not financial advice. Evidence overlay; no execution.

---

## 1. Objective

Phase 1 shipped the *evidence* (drawdown↔time Spearman + gap×drawdown matrix).
Phase 2 turns that evidence into the deliverable the user asked for — "how many
days are likely left before the next MA250 touch" — as an **additive, repair-regime
-gated per-ticker `retry_timing_context`** block, computed four independent ways
with elapsed/remaining/rough-date and quality flags, plus a consensus window.

It is a faithful, de-globalised port of the V11.5 §7 conditional retry-timing cell
(V12 `build_live_retry_setup` / `build_estimator_comparison`).

## 2. Scope

In scope:
- New module `src/yearline_universe/timing.py` with four estimator families,
  conditioned on the live ticker's drawdown-so-far + days elapsed since the last
  canonical touch, for the live `transition` (e.g. `2_to_3`):
  - **(a) historical median** gap by transition — ALL (pooled) + peer-group scopes;
  - **(b) gap×drawdown matrix interpolation** between adjacent drawdown anchors;
  - **(c) nearest-neighbor** median within ±2.5% of the current drawdown;
  - **(d) Theil-Sen** robust fit of gap ~ drawdown, bootstrap p10–p90.
- A `consensus` headline window (central = median of non-fragile method
  remaining-days; min/max range; rough dates) + base distribution + nearest
  observations.
- Additive per-ticker envelope key `retry_timing_context` (self-conditioned on the
  ticker's own history) and a richer **universe-pooled** version in the bundle's
  `pooled_context.retry_timing` (for repair-active tickers).

Out of scope (later phases):
- Hazard-model hardening (Phase 3) — the forward *probability* step fix.
- Calibration (Phase 4 / V13.7); pooled training + data freshness (Phase 5).

## 3. Approach

`timing.py` ports the V11.5 §7 functions verbatim in method, de-globalised:
`required_rebound_to_ma250_pct`, `build_live_retry_setup` (now reads the V13
**live diagnostic** dict instead of a notebook snapshot), `_distribution_summary`,
`build_transition_gap_summary`, `build_nearest_neighbor_summary`,
`interpolate_gap_from_matrix`, `theilsen_gap_estimate`, and
`build_estimator_comparison`. The orchestrator `build_retry_timing_context(...)`
gates on the active engine, assembles the setup + estimators + consensus, and
emits the block with disclaimers.

Wiring (mirrors Phase 1's per-ticker-self / bundle-pooled split):
- `run_ticker_pipeline` builds `retry_timing_context` from **the ticker's own**
  recovery + matrix (`conditioning_scope: single_ticker_self_conditioned`) and
  passes it to `build_statistical_context_envelope` as a new additive key.
- `export_universe_context_bundle` re-derives, for each repair-active ticker, a
  **universe-pooled** block from the pooled cross-ticker recovery
  (`conditioning_scope: universe_pooled`) under `pooled_context.retry_timing`.

The drawdown assumption defaults to the live drawdown-so-far (10.01% for MSFT),
with an explicit override (the report used 10.3%).

## 4. Acceptance criteria & results

| Criterion | Target | Result |
|---|---|---|
| Four estimator families ported & wired | yes | ✅ median / matrix-interp / NN±2.5% / Theil-Sen, ALL + peer scopes |
| Conditional estimators reproduce V11.5 §7 (at the report's 10.3%) | within a few days | **matrix-interp 15.9d (2026-06-21) vs 17.5d (06-23); NN±2.5% 34.5d (2026-07-10) vs 36d (07-11)** ✅ |
| MSFT live setup matches the report | transition 2→3, dist −10.10%, rebound 11.23% | ✅ transition `2_to_3`, dist −10.10%, req rebound **11.23%**, elapsed 4d |
| Repair-regime gated | only when below/testing MA250 | ✅ MSFT active; AAPL (accepted-above) → dormant stub; NVDA (transition) → dormant |
| Output-additive (existing fields byte-identical) | byte-identical | ✅ 16→17 envelope keys; **all prior fields byte-identical** vs the pre-Phase-2 baseline |
| Consensus robust to fragile samples | n=1/2 don't swing it | ✅ `consensus_basis: reliable_methods_only` (drops `very_low`/`n_lt_5`) |
| Honesty | sample sizes + quality flags everywhere | ✅ `sample_quality`, `n`, `is_descriptive_evidence_not_forecast`, disclaimers |
| Tests green | all pass | **38/38** (added 7) |
| No hardcoded ticker | guard holds | ✅ (AST guard; ticker flows from the live diagnostic) |

MSFT pooled consensus (engine default, 10.01%): central **41 days remaining**
(~2026-07-17), range **13–73d** (2026-06-19 → 2026-08-18). The spread is the honest
uncertainty: universe-pooled ALL methods cluster early (mid-June → late-July);
peer/self methods (MSFT is the lone `mega_cap_software` ticker, with long-dormancy
gaps) run late.

## 5. Cross-check vs the V11.5 report (§7)

Report = 8 tickers; this run = 3 cached (MSFT/AAPL current, NVDA at 2024-11-29;
AMZN/GOOGL/META absent), so the pooled 2→3 sample is **n=14** (9 of them MSFT's own).
At the report's **10.3%** assumption:

| method (scope) | V11.5 report | Phase 2 (3-ticker pool) | Δ |
|---|---|---|---|
| **matrix interp** (ALL) | 17.5d → 2026-06-23 | **15.9d → 2026-06-21** | ~2d ✅ |
| **nearest-neighbor ±2.5%** (ALL) | 36d → 2026-07-11 | **34.5d → 2026-07-10** | ~1d ✅ |
| historical median 2→3 (ALL) | 14.5d → 2026-06-20 | 29.5d → 2026-07-05 | longer ⚠ |
| Theil-Sen robust (ALL) | 37d → 2026-07-13 | 54.0d → 2026-07-30 | longer ⚠ |

**Reading:** the methods that actually *condition on the 10.3% drawdown*
(matrix-interpolation, nearest-neighbor) reproduce the report within 1–2 days. The
*unconditional* median and the *global* Theil-Sen run longer purely because this
3-ticker pool's 2→3 gaps (median 33.5d, n=14) lack the rapid-retry observations the
report's 8-ticker pool had — the same data-freshness caveat as Phase 1 (Spearman
0.91 vs 0.86). Robustness improves once the universe is on current data (Phase 5).

## 6. Files changed

- `src/yearline_universe/timing.py` — **new**: the four estimators + `build_retry_timing_context`.
- `src/yearline_universe/context_export.py` — `build_statistical_context_envelope` takes an additive `retry_timing_context`; `export_universe_context_bundle` fills `pooled_context.retry_timing`; schema property added.
- `src/yearline_universe/ticker_pipeline.py` — builds the self-conditioned block and passes it to the envelope.
- `src/yearline_universe/__init__.py` — exports `build_retry_timing_context` / `build_estimator_comparison` / `build_live_retry_setup`.
- `tests/test_timing.py` — **new**, +7 tests; `tests/test_ticker_pipeline.py` — schema key set updated (17 keys).
- Docs: spec §0/§8/§11, user guide §7.

## 7. Reproduce

```bash
python scripts/run_universe_mvp.py config/universe_mega_cap_ai_infra.yaml --provider cache
# MSFT envelope now carries retry_timing_context (self); bundle.pooled_context.retry_timing carries the pooled view
pytest -q tests/test_timing.py
```

```python
from yearline_universe import load_universe_config, run_universe_pipeline
res = run_universe_pipeline(load_universe_config("config/universe_mega_cap_ai_infra.yaml"),
                            cache_dir="data/price_cache", provider="cache")
print(res.ticker_results["MSFT"].latest_context["retry_timing_context"]["consensus"])
print(res.universe_context_bundle["pooled_context"]["retry_timing"]["MSFT"]["consensus"])
```

## 8. Artifacts (snapshots in `artifacts/`)

- `MSFT__retry_timing_context__self_conditioned.json` — the per-ticker envelope block.
- `MSFT__retry_timing__universe_pooled.json` — the bundle's pooled block.
- `MSFT__estimator_comparison__pooled__{live_10_01pct,report_10_3pct}.csv` — the two-assumption tables.
- `MSFT__base_distribution_2_to_3__pooled.csv` — the unconditional 2→3 gap distribution.
- `MSFT__statistical_context_envelope__phase2.json`, `mega_cap_ai_infra_bundle__phase2.json` — full snapshots.

## 9. Limitations & decision gate

- 3-ticker pool (NVDA stale; AMZN/GOOGL/META absent); `mega_cap_software` peer = MSFT
  alone, so peer-scope ≈ self. Unconditional/global methods are composition-sensitive;
  conditional methods (matrix/NN) are the most report-faithful. Needs Phase 5.
- This is a **conditional range**, explicitly assuming current drawdown is the maximum
  damage before the next retry. It is descriptive evidence, not a forecast or a date.

**Decision gate → Phase 3 (hazard hardening):** with a credible, validated
days-to-touch surface now shipping, fix the forward hazard *probability* by porting
the V12.4.1 empirical-horizon policy (the user's patched benchmark notebook): keep the
logistic model for `hazard_today` only, demote the saturating state-hold-forward curve
to a diagnostic, and surface an empirical completed-path P(retry≤H) as canonical —
targeting P60/P90 no longer pinned at 1.0 and a pool-stable P40, with a documented
before/after.
