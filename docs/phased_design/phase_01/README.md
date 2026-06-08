# Phase 1 — V13.3 Pooled Gap×Drawdown Evidence

**Status:** ✅ DELIVERED (2026-06-07)
**Part of:** the V13.3 phased roadmap (see `../README.md`)
**Theme:** the most defensible value — descriptive historical evidence that backs
the "days-to-next-touch" thesis. No forward forecast, no model risk.

> Educational research only. Not financial advice. Evidence overlay; no execution.

---

## 1. Objective

Deliver the empirical evidence the user identified as core value: the relationship
between **inter-attempt max drawdown** and **time to the next MA250 touch**, plus a
**classification matrix** of repair regimes — pooled at peer-group, sector, and
universe levels and surfaced in the repo-ready universe bundle.

This is Phase 1 of a value-first, trust-last roadmap. It is intentionally
*descriptive* (historical), so it is stable and shippable now — unlike the forward
hazard probability, which is ill-posed and deferred to Phases 3–4.

## 2. Scope

In scope:
- Pooled **gap×drawdown matrix** (drawdown_bucket × gap_bucket → counts, median
  gap/drawdown, next-attempt success rate + Wilson interval, interpretation label).
- **Spearman correlation** of drawdown vs days-to-next-touch, with bootstrap 95% CI,
  by group × transition.
- **Attempt-success classification** (by attempt bucket) with Wilson/Beta intervals.
- Computed at **peer_group / sector / universe**; surfaced in the universe bundle's
  `pooled_context`.

Out of scope (later phases):
- Conditional days-to-touch *estimators* (Phase 2).
- Hazard-model hardening (Phase 3) and calibration (Phase 4 / V13.7).
- Pooled hazard training + universe data freshness (Phase 5).

## 3. Approach

Faithful, de-globalised port of V12 Module B into `src/yearline_universe/pooling.py`:
`wilson_interval`, `beta_binomial_summary`, `gap_bucket`, `drawdown_bucket`,
`matrix_interpretation`, `build_gap_drawdown_matrix`, `build_gap_drawdown_corr_summary`
(Spearman + bootstrap CI via scipy), `build_pooled_attempt_success`, and an
orchestrator `build_pooled_evidence(ticker_results)` that pools each ticker's
`recovery_table` / `canonical_events` (tagging `peer_group` + `sector`) and emits the
three blocks at all levels. `export_universe_context_bundle` now fills
`pooled_context` from it. The lightweight `build_pooled_context` (V13.1 per-group
counts) is retained.

## 4. Acceptance criteria & results

| Criterion | Target | Result |
|---|---|---|
| Drawdown↔time Spearman reproduced | ~0.86 (V11.5 report) | **0.91** universe (n=55, CI [0.83, 0.94]) ✅ |
| Matrix populated at all levels | yes | peer_group / sector / universe ✅ |
| Attempt-success classification | yes | attempts 1/2/3+ with Wilson bands ✅ |
| Output-additive (no envelope change) | byte-identical envelopes | ✅ (only `pooled_context` added) |
| Honesty | sample sizes; n<5 suppressed | ✅ + `pooled_evidence_low_sample` warning |
| Tests green | all pass | **31/31** (added 4) |

Per-transition Spearman: 1→2 = 0.87 (n=19), 2→3 = 0.93 (n=14). Matrix highlights:
`short_gap×shallow_drawdown` → "healthy_absorption" (median gap 7d, succ 0.20);
`long_gap×deep_drawdown` → "structural damage / long dormancy" (median gap ~140d,
median DD ~22%, succ 0.25). These match the V11.5 report's structure.

## 5. Cross-check vs the V11.5 report

| Metric | V11.5 report (8 tickers) | Phase 1 (3 cached tickers) |
|---|---|---|
| Universe Spearman (all transitions) | 0.861 (n=147) | 0.907 (n=55) |
| 1→2 Spearman | 0.865 | 0.868 |
| 2→3 Spearman | 0.866 | 0.930 |
| Attempt-1 success | 0.421 | 0.387 |

Same strong positive relationship and matrix structure on a smaller (3-ticker)
sample. Robustness improves once the rest of the universe is on current data
(Phase 5 / data freshness).

## 6. Files changed

- `src/yearline_universe/pooling.py` — V13.3 evidence functions + `build_pooled_evidence`.
- `src/yearline_universe/context_export.py` — `export_universe_context_bundle` fills `pooled_context`.
- `tests/test_pooling.py` — +4 tests (Wilson bounds, matrix+correlation, attempt-success, real-data evidence).
- Docs: spec §0/§8, user guide §7.

## 7. Reproduce

```bash
python scripts/run_universe_mvp.py config/universe_mega_cap_ai_infra.yaml --provider cache
# bundle.pooled_context now carries headline_correlation + per-level matrix/correlation/attempt_success
pytest -q tests/test_pooling.py
```

```python
from yearline_universe import load_universe_config, run_universe_pipeline
from yearline_universe.pooling import build_pooled_evidence
uni = load_universe_config("config/universe_mega_cap_ai_infra.yaml")
res = run_universe_pipeline(uni, cache_dir="data/price_cache", provider="cache")
ev = build_pooled_evidence(res.ticker_results)
print(ev["headline_correlation"])
```

## 8. Artifacts (snapshots in `artifacts/`)

- `pooled_evidence.json` — the full `pooled_context` (all levels + headline).
- `{universe,peer_group,sector}__gap_drawdown_matrix.csv`
- `{universe,peer_group,sector}__correlation.csv`
- `{universe,peer_group,sector}__attempt_success.csv`

## 9. Limitations & decision gate

- Only MSFT/AAPL are current (2026-06-05); NVDA is at 2024-11-29; pooled n=55 is
  small vs the report's 147 → flagged low-sample. Robustness needs Phase 5.
- Evidence is descriptive (not causal, not a forecast).

**Decision gate → Phase 2:** with the evidence backbone in place, build the
conditional days-to-touch *estimators* (median / matrix-interpolation /
nearest-neighbor / Theil-Sen, with uncertainty) and surface them as an additive,
repair-regime-gated per-ticker `retry_timing_context`.
