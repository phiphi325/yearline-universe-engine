# Phase 6 — Honest Out-of-Fold Calibration Gate

**Status:** ✅ DELIVERED (2026-06-08) — PR `phase-06-calibration-hardening`
**Part of:** the V13.3 phased roadmap (see `../README.md`)
**Theme:** make the Phase 4 trust gate **honest** — gate on a leakage-safe,
out-of-fold isotonic-calibrated MACE instead of the in-sample-optimistic value — and
report, truthfully, what that does to each horizon (including the fact that it does
**not** rescue 60d).

> Educational research only. Not financial advice. Output-changing for the calibration
> block → gated before/after review (this PR).

---

## 1. Objective (what this PR actually delivers)

Phase 4 surfaced an isotonic recalibration but its post-calibration MACE was **in-sample
-optimistic** (the transform was fit and scored on the same rows → ≈0 by construction),
so the gate side-stepped it and used the raw reliability MACE. This phase replaces that
with an **honest** estimate and gates on it:

- the isotonic transform is now evaluated **out-of-fold** via `GroupKFold` **purged by
  `transition_key`** (no episode leaks across folds), giving a true calibrated MACE;
- the trust gate uses **AUC (raw, transform-invariant) + the honest OOF-calibrated MACE
  + n** — the in-sample calibrated MACE is reported for reference only.

This is a correctness upgrade to the gate: it can no longer be fooled by an
optimistic-looking transform.

## 2. Result — MSFT, pooled 9-ticker universe (n=4,765, 162 episodes)

| horizon | AUC | raw MACE | **honest OOF-calibrated MACE** | gate (AUC≥0.60, MACE≤0.10) |
|---|---|---|---|---|
| 10d | 0.816 | 0.036 | 0.055 | ✅ PASS |
| 20d | 0.779 | 0.048 | 0.047 | ✅ PASS |
| 40d | 0.762 | 0.077 | **0.056** (isotonic *tightens* it) | ✅ PASS |
| 60d | 0.738 | 0.109 | **0.130** (isotonic can't help) | ❌ honest fail |

**The honest finding:** recalibration *helps where there is monotone miscalibration to
fix* (40d: 0.077 → 0.056) but **cannot rescue 60d** — out-of-fold it is slightly *worse*
(0.109 → 0.130). 60d's gap is **sample/regime-limited**, not a recalibration problem
(at a ~0.72 base rate the separation is compressed, episodes overlap, and macro/regime
drift dominates the local repair state). So 60d stays an **honest abstention**; the path
to 60d is *more data + better features* (Phase 7), not another transform.

> Why merge a change where 60d still fails? Because the goal was an **honest gate**, and
> that succeeded — 10/20/40d pass on a leakage-safe metric and 40d's margin improved.
> The alternative (the old in-sample isotonic) *looked* perfect (~0) and would have
> **falsely** certified 60d. A trust gate that says "not yet" is doing its job.

## 3. Files changed

- `src/yearline_universe/calibration.py` — `_isotonic_for_horizon` (final knots +
  out-of-fold GroupKFold-purged-by-transition calibrated MACE/Brier); `fit_isotonic_per_horizon`
  wraps it; `_gate_for_horizon` gates on the honest OOF MACE (falls back to raw if too
  few transition groups); summary exposes `mace_calibrated_oof` (+ `*_in_sample` for reference).
- `src/yearline_universe/context_export.py` — `calibration_gate_40d` now surfaces
  `mace_gate` + `mace_gate_basis`.
- `tests/test_calibration.py` — asserts OOF method + honest calibrated MACE present;
  gate exposes its MACE basis. **46/46 tests pass.**
- `docs/phased_design/phase_06/` — this README + `artifacts/calibration_honest_oof_isotonic.csv`.

## 4. Scope — two PRs

- **PR #1 (merged):** the **honest-gate** slice (this document's §1–§3).
- **PR #2 (follow-up):** **WS1 compute-once pooled calibration** + **WS3 test-suite OOM
  mitigation** (see §7). Split so the calibration-correctness change reviews cleanly on
  its own.

## 5. Reproduce

```bash
python scripts/run_universe_mvp.py config/universe_mvp_software_like.yaml \
    --provider cache --pool-hazard --calibrate
# retry_hazard_context.calibration_gate_40d.mace_gate_basis == "oof_isotonic_calibrated"
pytest -q tests/test_calibration.py
```

## 6. Follow-up PR — compute-once calibration + test OOM (delivered)

**WS1 — compute-once pooled calibration.** The pooled calibration model is
live-ticker-*independent* (it depends only on the pooled panel's completed rows), so
recomputing it per ticker was pure waste. `calibration.py` now splits into
`build_calibration_model(panel)` (the expensive LOTO dataset + metrics + OOF isotonic +
gate, a small JSON-serializable dict) and `apply_calibration_live(model, reference,
live_row)` (the cheap per-ticker raw+calibrated P + gate). `run_universe_pipeline(
pool_hazard=True, calibrate=True)` builds the model **once** and threads it to every
ticker (`run_ticker_pipeline(calibration_model=…)` → `run_hazard_layer`), and the
universe **bundle surfaces it once** at `pooled_context.calibration` (per-ticker live
block stripped). Verified: the full 9-ticker pooled+calibrate run is **one** model build
(n=4,765, 162 transitions) instead of ~9× LOTO rebuilds; bundle `pooled_context.calibration`
populated; per-ticker envelopes still carry their gate + calibrated P (gate {10/20/40 pass,
60 fail}, unchanged). A test asserts model-reuse is byte-identical to the inline build.

**WS3 — test-suite OOM mitigation.** The real-data tests hold large pandas frames, so a
single `pytest` process can OOM on a constrained machine. Added a `gc.collect()` autouse
fixture (bounds peak memory) and `scripts/run_tests.sh` (one file per process — the
reliable, dependency-free isolation path), documented in the README "Tests" section.
Both report the same **46 passing**.

## 7. Decision gate → Phase 7

With the gate now honest and 60d shown to be sample/feature-limited (not a calibration
problem), the next effort is **discrimination, not recalibration**: per the
`V12_V13_AUC_MACE_improvement_research_report.md`, add **path-dynamic repair features**
(gap-slope, bounce-from-low, short-term returns, MA20/50 state, volatility compression)
and move from the bucketed empirical estimator toward **direct, calibrated horizon
classifiers** with episode-aware (leave-one-episode/ticker-out) validation. Lead with
path-dynamic features — the current estimator cannot see "improving vs deteriorating,"
which is the biggest cheap AUC win.
