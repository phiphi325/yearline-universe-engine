# Phase 4 — Calibration & Gating (V13.7)

**Status:** ✅ DELIVERED (2026-06-07) — ports the V12.6 harness (which scores the
Phase 3 empirical estimator) + adds isotonic + purged transition-aware splits + a
per-horizon trust gate, wired in opt-in (`calibrate=True`).
**Part of:** the V13.3 phased roadmap (see `../README.md`)
**Theme:** make any surfaced forward probability **trustworthy** — reproduce the
V12.6 calibration/walk-forward diagnostics inside V13, add a calibration transform
and leakage-safe splits, and **gate** the envelope's hazard probability on passing.

> Educational research only. Not financial advice. A probability is surfaced only
> when it passes the calibration gate; otherwise it is suppressed/demoted.

---

## 1. Objective

Fill the envelope's `calibration_context` (today a prototype stub
`{"available": false, ...}`) with real horizon-level reliability, attach a
calibration **transform** so the surfaced probability is calibrated, and **gate**
`retry_hazard_context.p_retry_within_40d` (and the mode-state split) on passing.
This is the only step that makes a forward probability meaningful — per the
analysis doc §4(5)(d).

## 2. Ready-made baseline — the uploaded V12.6 report

`docs/uploaded/yearline_v12_calibration_walkforward_report_v12_6.pdf` already
implements the harness we port and sets the bar to meet/beat:

- **Hazard horizon calibration (n=4227):** 10d Brier 0.160 / log-loss 0.484 / AUC
  0.802 / MACE 0.064; 20d 0.195 / 0.570 / 0.763 / 0.070; 40d 0.190 / 0.568 / 0.745 /
  0.072; 60d 0.172 / 0.529 / 0.718 / 0.193. → **calibrated through 40d, weak at 60d.**
- **Hazard walk-forward (per fold):** daily AUC ≈ 0.72–0.98; model Brier generally ≤
  baseline; a few weak folds (AUC ~0.42–0.53).
- **Retry-quality classifier (LOTO, n=147):** Brier 0.223 vs 0.229 baseline; log-loss
  0.642 vs 0.650; AUC 0.616; MACE 0.075.
- **V12.6 §5 explicitly recommends next:** *purged transition-aware splits*,
  *isotonic-regression calibration transforms*, and a *repo-ready probability schema*
  — which become Phase 4's additions on top of the port.

## 3. Scope

In scope:
- **(1) Port the calibration harness** into V13 (new `calibration.py` or extend
  `validation.py`), reusing the **fixed cell-59** train-schema-authoritative design
  (no leakage): per-horizon observed vs predicted, Brier, log-loss, AUC, and
  mean-abs-calibration-error-by-bin; reliability-curve data; per-fold walk-forward.
- **(2) Calibration transform:** fit **isotonic regression** (Platt as fallback) on
  out-of-fold predictions; surface a *calibrated* probability.
- **(3) Leakage-safe splits:** **purged, transition-aware** CV (no attempt/round
  overlap across train/test), as V12.6 §5 recommends.
- **(4) Fill `calibration_context` + gate:** `available`, per-horizon metrics, the
  transform used, and a **`trust_gate`** (surface P only where MACE ≤ threshold and
  AUC ≥ threshold — V12.6 implies ≤40d passes, 60d does not). Gate
  `retry_hazard_context.p_retry_within_40d` and the `repair_retry_probability_building`
  mode-state on it.
- **(5) Repo-ready probability schema** (V12.6 §5): document the calibrated-probability
  fields for the downstream `option-mgmt-2026` adapter.

Out of scope:
- The hazard re-specification itself (**Phase 3**, prerequisite — Phase 4 calibrates
  the *hardened* curve).
- Pooled training + universe data freshness (**Phase 5**, improves the sample the
  calibration rests on).

## 4. Approach

Operate on the hardened Phase 3 hazard. Build the calibration set from the universe
walk-forward on the fixed cell-59 schema; fit the isotonic transform on OOF
predictions; write `calibration_context` and wire the trust gate into
`context_export.build_statistical_context_envelope` (additive — the key already
exists as a stub). Keep the envelope schema stable; only the stub's contents and the
gating behaviour change.

## 5. Acceptance criteria & results

| Criterion | Target | Result |
|---|---|---|
| Harness faithful | port the V12.6 horizon-reliability + reliability tables | ✅ `calibration.py`: observed/predicted/Brier/log-loss/AUC/MACE per horizon + reliability bins |
| Purged transition-aware splits | no own-outcome leakage | ✅ every prediction uses `exclude_transition_key` (leave-one-transition-out) |
| Isotonic transform | add the V12.6 §5 to-do | ✅ `fit_isotonic_per_horizon` (serializable knots); applied to the live P |
| Gate works | `available=true`; surfaced P suppressed when it fails | ✅ per-horizon `trust_gate`; on MSFT it **correctly FAILS all horizons** → `surfaced_probability_is_calibrated=false` |
| Opt-in, output-preserving default | `calibrate=False` unchanged | ✅ default leaves `calibration_context.available=false`; no gate fields added (like `fit_ml_models`) |
| Schema + guards + tests | additive; green | ✅ additive (`p_retry_within_40d_calibrated`, `calibration_gate_40d`, `calibration_context` filled); **45/45** tests (added `test_calibration.py`, +4) |

### Result — MSFT 2026-06-05 (single-ticker, opt-in `calibrate=True`, 35s)

783 calibration rows over 26 transitions, leave-one-transition-out:

| horizon | n | observed | predicted | Brier | **AUC** | MACE (raw) | trust gate |
|---|---|---|---|---|---|---|---|
| 10 | 783 | 0.275 | 0.329 | 0.216 | 0.524 | 0.155 | ❌ |
| 20 | 783 | 0.434 | 0.512 | 0.312 | 0.430 | 0.314 | ❌ |
| 40 | 783 | 0.625 | 0.680 | 0.277 | **0.462** | 0.330 | ❌ |
| 60 | 783 | 0.770 | 0.821 | 0.199 | 0.465 | 0.181 | ❌ |

Surfaced 40d: raw empirical **0.781** → isotonic-calibrated **0.665**, but
`calibration_gate_40d = {passed:false, auc:0.46, fail_reasons:[auc<0.6, mace_raw>0.1]}`
⇒ `surfaced_probability_is_calibrated = false`.

**This is the correct outcome, and it is the point of the phase.** On *single-ticker*
MSFT the empirical estimator falls back to a transition-only scope (Phase 3), so its
prediction barely varies across rows ⇒ **AUC ≈ 0.46 (no discrimination)**, and the gate
**refuses to bless it**. Contrast the V12.6 report's *8-ticker pooled* calibration
(n=4227): AUC 0.745 at 40d, MACE ≤ 0.072 — i.e. **pooling is what buys discrimination**.
So Phase 4 ships a *working gate that honestly says "not yet trustworthy"*; clearing
the gate is a **Phase 5** (pooled training + data-freshness) outcome, not a Phase 4 one.
We did not reproduce the V12.6 AUC because we do not yet have its 8-ticker pool — the
same data-thinness caveat as Phases 1–3.

> Note: the isotonic post-calibration MACE is **in-sample-optimistic** (fit and scored
> on the same rows ⇒ ≈0), so the gate deliberately uses **AUC + raw reliability MACE**
> (AUC is invariant to the monotonic isotonic map). A nested-CV isotonic eval is future
> work.

### Files changed (as-built)

- `src/yearline_universe/calibration.py` (new) — dataset (purged LOTO), horizon
  metrics + reliability, isotonic transform, trust gate, `build_calibration_context`.
- `src/yearline_universe/hazard.py` — `run_hazard_layer(calibrate=False)` returns a
  `calibration_context`.
- `src/yearline_universe/ticker_pipeline.py` — `calibrate` threaded through ticker +
  universe runners; fills `calibration_summary` from the real context when present.
- `src/yearline_universe/context_export.py` — surfaces `p_retry_within_40d_calibrated`
  + `calibration_gate_40d` + `surfaced_probability_is_calibrated` on `retry_hazard_context`.
- `scripts/run_universe_mvp.py` — `--calibrate`. `tests/test_calibration.py` (+4).

## 6. Decision gate → Phase 5

With timing (Phase 2) + a hardened (Phase 3) + calibrated-and-gated (Phase 4)
probability, **Phase 5** scales it: pooled hazard/timing training via the existing
`pooled_data` hook and a data-freshness step to bring NVDA and the rest of the
universe to current data — making both the evidence and the calibration robust.
