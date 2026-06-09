# Phase 8 — Retry-success (Track A): RS-1…RS-4 delivered

**Status:** ✅ DELIVERED — **RS-1 (labels + baseline) + RS-2 (classifier) + RS-3 (calibrate + blend + gate) + RS-4 (gated live surfacing) DELIVERED.** The blend **clears the trust gate** and is now surfaced as an opt-in, additive `retry_success_context` overlay (with the occurrence×success composite), default-off ⇒ envelope byte-identical.
**Part of:** the planner roadmap — see [`../planner/01_retry_success_plan.md`](../planner/01_retry_success_plan.md).
**Source analysis:** [`../../research/01_retry_success_probability_2026-06-08.md`](../../research/01_retry_success_probability_2026-06-08.md).
**Theme:** make retry **success** (*given an attempt, does it reclaim and **hold**?*) trustworthy —
distinct from the mature retry **occurrence** estimator (`P(retry ≤ H)`).

> Educational research only. Output is evidence context; not a trading signal.

---

## 1. What RS-1 builds

`src/yearline_universe/success_labels.py` (new), **capability-before-consumer** (nothing surfaced):

- **`build_success_dataset(tickers_data)`** — a leakage-safe, **attempt-level** success dataset pooled
  across the universe. One row per **completed** recovery transition; `y_success` = 1 iff the next
  attempt reclaimed and held (`event_detection.classify_attempt_outcome_v10_parity → "success"`,
  surfaced as `recovery_table.next_attempt_success`). **Pending/unresolved attempts are excluded**
  (`next_attempt_pending`) — censoring is leakage-safe by construction.
- **`build_empirical_success_reference` / `empirical_success_probability_for_row`** — the empirical
  "of *similar* historical attempts, what fraction succeeded?" estimator, mirroring the Phase-3 horizon
  estimator: a **bucket scope-ladder** (`group_transition_drawdown → group_transition →
  transition_drawdown → transition → group_drawdown → group → drawdown → universe_all`) with **Bayesian
  shrinkage** to the universe success rate. Floor `SUCCESS_MIN_REFERENCE_N = 15` (lower than the
  horizon estimator's 25 — attempts are scarcer); prior strength 6. This is the **calibrated baseline**
  the RS-2 classifier must beat.

## 2. The dataset (real universe — 9 tickers)

`config/universe_mvp_software_like.yaml` → **162 completed attempts**, success **base rate 0.352**
(57/162; matches the V12.10 benchmark ~0.354). Real structure exists in the *base rate*:

| Cut | Success rate (n) |
|---|---|
| ai_accelerator | 0.33 (15) |
| etf_context | 0.51 (35) |
| mega_cap_software_like | 0.30 (112) |
| transition 1→2 | 0.32 (59) · 2→3 0.41 (39) · 3→4 0.43 (23) · 4→5 0.38 (13) · later (6→7…) 0.00–0.20 (n≤5) |

## 3. The honest result — the empirical estimator does **not** beat the flat base rate (yet)

Leave-one-attempt-out, the empirical base-rate-by-bucket estimator vs predicting the flat 0.352:

| Metric | Empirical estimator | Flat base rate |
|---|---|---|
| **Brier** | **0.2320** | 0.2281 |
| **AUC** | **0.490** | 0.500 (by definition) |

It **does not beat the base rate** (Brier marginally worse; AUC ≈ random). **The static recovery-state
buckets (drawdown / below-MA250 depth / attempt# / transition / group) carry no out-of-sample signal
for *success*** at this sample size — even though the *base rate itself* varies by group/transition,
that variation doesn't generalize attempt-to-attempt under leave-one-out. (Scope usage: most queries
resolve at `group_transition` (97) or `group_drawdown` (28) / `transition` (24); the bucket-specific
drawdown scope rarely cleared the floor.)

This is the **honest "not yet"** the research note predicted, and it is the *point* of RS-1: it
establishes the **bar** — **AUC ≈ 0.49, Brier ≈ 0.228** — that RS-2's richer features must clear.

## 4. Files changed

- `src/yearline_universe/success_labels.py` (new) — `build_success_dataset`,
  `build_empirical_success_reference`, `empirical_success_probability_for_row`,
  `SUCCESS_STATE_FEATURES`, `SUCCESS_PROB_POLICY`.
- `src/yearline_universe/__init__.py` — exports.
- `tests/test_success_labels.py` (new, +4) — label correctness + **pending censoring**, estimator
  **signal** (shallow-drawdown ranks above deep), **shrinkage** (tiny buckets pulled to the universe
  rate), exclude-key + empty-input grace.
- `artifacts/` — `rs1_empirical_success_vs_base_rate.json`, `rs1_success_dataset.csv`,
  `rs1_success_rate_by_group.csv`, `rs1_success_rate_by_transition.csv`.

No existing output changes (capability before consumer); full per-file suite green;
no-hardcoded-ticker guard holds.

### Validation — event-detection alignment

Because RS-1's labels derive from the strict/loose attempt detector → canonical events, an audit
confirms those attempts are **properly aligned and time-correctly processed per ticker**:
[`event_detection_alignment_audit.md`](event_detection_alignment_audit.md). Across all 9 tickers:
chronological integrity is exact (monotonic, no duplicate bars, correct `date ↔ trading_loc` mapping,
no cross-ticker mixing), strict attempts are preserved 1:1 (184→184 strict-quality anchors), loose hits
merge correctly within the 2-day window (0 span warnings), rounds/attempts reset correctly after a
success, and strict/loose disagree on outcome in only 1 of 153 merged clusters. One non-blocking
hardening note (an unenforced single-ticker precondition in `build_canonical_events`) is recorded there.
Artifacts: `artifacts/event_detection_alignment_audit.{csv,json}`.

## 5. Reproduce

```python
from yearline_universe import load_universe_config
from yearline_universe.ticker_pipeline import _build_foundation
from yearline_universe.success_labels import (
    build_success_dataset, build_empirical_success_reference, empirical_success_probability_for_row)

uni = load_universe_config("config/universe_mvp_software_like.yaml")
pooled = {tc.ticker: {"peer_group": tc.peer_group,
                      "recovery_table": _build_foundation(tc, uni, cache_dir="data/price_cache",
                                                          provider="cache")["recovery"]}
          for tc in uni.tickers}
ds = build_success_dataset(pooled)                       # 162 completed attempts; base rate 0.352
ref = build_empirical_success_reference(ds)
p = empirical_success_probability_for_row(ds.iloc[0].to_dict(), ref,
                                          exclude_transition_key=ds.iloc[0]["transition_key"])
```

## 6. Decision gate → RS-2 (RS-1's bar)

RS-1 set the bar: **beat AUC ≈ 0.49 / Brier ≈ 0.228** under leave-one-*ticker*-out. RS-2 builds the
regularized-logistic **success classifier** on the **readiness (Phase-7 path) + cross-sectional**
features the static buckets lack, head-to-head vs this RS-1 baseline.

## 7. RS-2 — direct success classifier (delivered)

`src/yearline_universe/success_models.py` (new): `build_success_model_table` joins the RS-1 attempts
with **path-dynamic readiness features at the attempt's touch date** (leakage-safe ≤ date) + the
**cross-sectional regime** + the RS-1 empirical baseline (leave-one-attempt-out); `evaluate_success_models`
runs an **L2 logistic** under **episode-purged GroupKFold + leave-one-*ticker*-out** (reusing the Phase-7
`generalization` harness), head-to-head vs the empirical baseline and the flat base rate.

### The result — discrimination **yes**, calibration **not yet** (real universe, 162 attempts / 59 episodes)

| Surface | AUC | Brier | MACE | Brier lift vs base |
|---|---|---|---|---|
| Empirical baseline (RS-1) | 0.490 | 0.232 | 0.185 | −0.004 |
| Classifier — episode-purged | 0.647 | 0.243 | 0.134 | −0.015 |
| **Classifier — leave-one-ticker-out** | **0.710** | 0.215 | 0.128 | **+0.013** |

- **The classifier finds real success signal the static buckets missed.** It **beats the empirical
  baseline on AUC** (0.71 vs 0.49) and **beats the flat base rate** under the unseen-name test
  (Brier 0.215 < 0.228; AUC 0.71 > 0.5). Success *is* partially predictable from path/regime
  **readiness** — the same lever that worked for *occurrence* (Phase 7).
- **But it is not yet trustworthy as a probability.** Calibration is poor — **MACE ≈ 0.128 > the 0.10
  gate**, reliability slope ≈ 0.62 (over-confident); episode-purged Brier is even *worse* than the base
  rate. The classifier **ranks** well but its raw probabilities are miscalibrated.
- **Small-sample caveats:** 162 attempts / 59 episodes / 9 tickers; the ticker-LOO AUC (0.71) exceeding
  the episode-purged AUC (0.65) — a *negative* generalization gap (−0.064) — partly reflects larger
  per-fold training in LOTO + small-sample variance. Treat magnitudes as indicative, not precise.

**Net:** RS-2 clears RS-1's bar on **discrimination** but **would fail the trust gate on calibration**.
That is precisely the ranker-vs-calibrated split Phase 7 hit — so the next step is **RS-3** (isotonic
calibration + a classifier↔empirical **blend** + the gate). Surfacing stays gated until then.

### Files (RS-2)

- `src/yearline_universe/success_models.py` (new) — `build_success_model_table`,
  `evaluate_success_models`, `build_and_evaluate_success_models`, `SUCCESS_MODEL_FEATURES`.
- `src/yearline_universe/success_labels.py` — `build_success_dataset` now also keeps `to_date` (to merge
  features at the attempt date). `__init__.py` — exports.
- `tests/test_success_models.py` (new, +3) — full-feature structure, **signal detected on focused
  features**, empty/single-class grace.
- `artifacts/` — `rs2_success_classifier_headtohead.json`, `rs2_headtohead_summary.csv`.

## 8. Decision gate → RS-3

RS-2 proved the **signal exists** (AUC ≈ 0.65–0.71, beating both the empirical baseline and the base
rate) but is **mis-calibrated** (MACE ≈ 0.13). RS-3 isotonic-recalibrates (purged OOF), **blends** the
classifier with the empirical baseline (Phase-7 lever), and applies the **trust gate** (AUC ≥ 0.60,
MACE ≤ 0.10, n ≥ 50), abstaining where it fails.

## 9. RS-3 — calibration + blend + trust gate (delivered) — **gate PASSES**

`src/yearline_universe/success_calibration.py` (new): `evaluate_success_calibration_gate` produces the
classifier's leave-one-ticker-out predictions, then computes five candidate surfaces and gates each with
**honest out-of-fold isotonic** MACE (a second episode-purged GroupKFold — not in-sample-optimistic):

| Surface | AUC | MACE | reliability slope | gate |
|---|---|---|---|---|
| classifier (raw) | 0.710 | 0.128 | 0.62 | ✗ MACE |
| classifier (isotonic) | 0.679 | 0.103 | 0.66 | ✗ MACE (*just* misses) |
| empirical baseline (RS-1) | 0.490 | 0.185 | 0.02 | ✗ AUC + MACE |
| **blend (0.5·clf + 0.5·emp)** | **0.702** | **0.036** | 1.12 | **✅ PASS** |
| blend (isotonic) | 0.645 | 0.096 | 0.50 | ✅ |

**Recommended surface = the blend** (the gate-passing surface with the highest AUC): **AUC 0.702,
out-of-fold MACE 0.036** under leave-one-ticker-out. The classifier↔empirical blend keeps almost all of
the classifier's discrimination **and** is well-calibrated — the Phase-7 result, reproduced for success.

**Reading it honestly:**
- The blend works because the components err oppositely: the classifier is **over-confident** (slope
  0.62) and discriminating; the empirical baseline is nearly **flat** (slope 0.02, ≈ the base rate).
  Averaging tempers the classifier's spread toward the base rate → well-calibrated (slope 1.12). That is
  genuine (out-of-fold), but the very low MACE **partly reflects predictions clustering near the base
  rate** — a "safe" calibration, not a precision claim.
- **Isotonic alone *just* missed** (MACE 0.103); applying isotonic *on top of* the blend made it **worse**
  (the blend is already calibrated — don't over-process it). So the **raw blend** is the recommended surface.
- **Small sample** (162 attempts / 59 episodes / 9 tickers): binned MACE is noisy and the PASS is
  high-variance. Treat as "**clears the gate on current data**," to be **re-validated walk-forward** as the
  universe/history grow (per `../planner/04_macro_factors_feature_analysis.md`'s period-validation point).

**Files (RS-3):** `src/yearline_universe/success_calibration.py` (new) +
`__init__.py` exports; `tests/test_success_calibration.py` (new, +2);
`artifacts/rs3_calibration_gate.json`, `artifacts/rs3_gate_summary.csv`.

### 9.1 Reliability deep-dive — how much of the 0.036 is *true calibration* vs. *base-rate shrinkage*?

The §9 caveat ("the low MACE partly reflects predictions clustering near the base rate") deserved a
number, not a hand-wave. The RS-3 **reliability diagnostic** (`success_reliability.py` + the runnable
`reliability/` folder) decomposes the blend's calibration win on the **same** leave-one-ticker-out
surfaces, via a Brier/Murphy split, a variance-shrinkage index, and a *pure-shrinkage counterfactual*
(shrink the raw classifier toward the base rate by the same variance factor, using **no** empirical info).

**The answer: ~87% of the blend's MACE gain is base-rate shrinkage, only ~13% is genuine empirical
information.**

| Quantity | Value |
|---|---|
| total MACE gain (raw 0.128 → blend 0.036) | **0.0921** |
| gain reproduced by pure shrinkage (no empirical info) | **0.0799 (86.8%)** |
| gain from the empirical anchor's actual information | **0.0121 (13.2%)** |
| variance-shrinkage index `1 − var(blend)/var(raw)` | **0.724** |
| resolution (sharpness) lost: 0.0406 → 0.0256 | **−0.0150** |

So the blend is **calibrated-by-shrinkage**: honest and safe for sizing (a "0.55" is genuinely
above-base-rate), but **less sharp** than the raw classifier and intentionally under-confident at the
extremes — it never predicts above ~0.62 or below ~0.17. The upgrade path is therefore **a sharper raw
classifier (better features / more episodes)**, not more calibration tuning — re-tuning the blend weight
cannot recover more than the 0.012 the empirical anchor's information is worth. Full method, figures, and
reading-for-RS-4 in **[`reliability/README.md`](reliability/README.md)**.

## 10. Decision gate → RS-4

The success probability now has a **gate-passing surface** (the blend: AUC 0.702 / MACE 0.036 under
leave-one-ticker-out). RS-4 may therefore surface it — the same way Phase 7 surfaced the occurrence
blend: an **opt-in, additive, gated** `retry_success_context` block (the success analog of
`retry_hazard_context`) attached **only where the gate passes**, plus the
`P(successful reclaim within H) = P(retry ≤ H) × P(success │ retry)` composite **only where both gates
pass**. Default off ⇒ envelope byte-identical. The standing caveat (thin sample; re-validate
walk-forward; the dominant lever is more labelled attempts) travels with it.

## 11. RS-4 — gated success overlay, live (delivered)

RS-4 turns the RS-3 gate-passing surface into a **live, opt-in, additive overlay**, mirroring Phase-7's
occurrence-blend wiring (`blend_surface.py`) exactly. New module **`success_surface.py`**:

- **`build_success_surface_model(tickers_data)`** — compute-once (universe-level, live-ticker-independent):
  build the RS-2 success table, take the RS-3 blend weight + **trust gate** on the recommended (blend)
  surface, and fit the classifier on all completed attempts for live scoring. Built once by the universe
  runner and reused per ticker.
- **`apply_success_live(...)`** — score the live readiness state, blend with the live empirical success
  probability, attach the gate.
- **`build_retry_success_context(...)`** — assemble the live success-state row (current recovery state +
  leakage-safe path/cross-sectional features at as-of) and emit the **`retry_success_context`** block:
  the single **P(success │ retry)** (gated blend) + provenance + gate, and the per-horizon composite
  **P(reclaim ≤ H) = P(retry ≤ H) × P(success │ retry)**.

**Surfacing rules (capability-before-consumer, conservative):**
- Opt-in via **`surface_success=True`** (pooled-only — cross-sectional features need the universe).
  **Default off ⇒ the envelope is byte-identical** (the `retry_success_context` key is simply absent).
- Success is a **single** probability (not horizon-indexed); only the composite is per-horizon.
- A horizon's composite is **surfaced** (`surfaced_probability`) only where **both gates pass** — the
  occurrence gate *and* the success gate (RS-3). Otherwise the raw product is retained but labelled
  diagnostic (`surfaced_probability: null`), so a consumer never mistakes an un-gated number for a trusted
  one.
- **The occurrence gate prefers the Phase-7 blend** (see §11.2): per horizon, RS-4 takes the
  classifier↔empirical blend's probability + gate where the blend passes, else falls back to the
  canonical empirical estimator gated by the Phase-4 isotonic trust gate. Each horizon records which one
  backed it (`occurrence_surface`).
- The empirical and occurrence estimators stay **canonical**; this overlay never overwrites them.

**Wiring:** `hazard.run_hazard_layer(surface_success=, success_model=)` → `ticker_pipeline`
(`run_universe_pipeline(surface_success=True)` builds the compute-once success **and** blend models and
threads them) → `context_export.build_statistical_context_envelope(success_context=)` attaches the
top-level block. Enabling `surface_success` computes the Phase-7 blend for the occurrence gate **without**
attaching the blend *block* (that stays gated on `surface_blend`), so the hazard block is unchanged. The
JSON schema gains an **optional** `retry_success_context` property (not in `required`, preserving parity).

**Reproduce:** `python3 docs/phased_design/phase_08/run_rs4_success_overlay.py` →
`artifacts/rs4_success_overlay_example.json` (one full live block) + `artifacts/rs4_success_overlay_summary.csv`
(per-ticker P(success│retry), gate, composite@40d/@60d + occurrence_surface) + a byte-identical-when-off check.

### 11.1 Live result (real universe, 9 tickers)

Running the overlay live (`run_rs4_success_overlay.py`) attaches an available block to **all 9 tickers**,
all gate-passing. `P(success │ retry)` spans **0.15 (META) → 0.32 (NVDA)** — near/below the 0.352 base
rate, the expected footprint of the shrinkage-calibrated blend (trust the ordering, not the level).

All 9 tickers surface the composite at **every** horizon (10/20/40/60d), each backed by the Phase-7 blend
occurrence surface. Example — **MSFT** (`artifacts/rs4_success_overlay_example.json`):

| field | value |
|---|---|
| `p_success_given_retry` (blend) | **0.174** |
| classifier / empirical components | 0.00 / 0.347 (w=0.5) |
| success gate | ✅ AUC 0.702, MACE 0.036 |
| composite `P(reclaim ≤ 40d)` | 0.548 × 0.174 = **0.095** (surfaced; `occurrence_surface = phase7_blend`) |
| composite `P(reclaim ≤ 60d)` | 0.696 × 0.174 = **0.121** (surfaced; `occurrence_surface = phase7_blend`) |

Two things this demonstrates on real data: (1) the **occurrence side rides the Phase-7 blend** — 60d is
surfaced (not withheld) because the blend calibrates that horizon where the isotonic-only surface can't
(§11.2); (2) the **blend tempers extreme single-row classifier calls** — the fitted-on-all classifier
scored MSFT's live readiness state at ~0.00, which the 0.5 blend pulls to 0.174 (the gate was validated on
OOF, so the *surfaced* blend is the trustworthy number; the raw classifier point is not). Per-ticker table
in `artifacts/rs4_success_overlay_summary.csv`.

### 11.2 Which occurrence surface backs the composite (and why 60d is *not* withheld)

The composite needs a trustworthy `P(retry ≤ H)`. There are two occurrence surfaces, and they disagree at
long horizons (pooled, leave-one-ticker-out):

| H | isotonic-only gate (Phase 4) | Phase-7 blend gate | composite uses |
|---|---|---|---|
| 10 | ✅ MACE 0.055 | ✅ AUC 0.833, MACE 0.045 | blend |
| 20 | ✅ MACE 0.047 | ✅ AUC 0.806, MACE 0.068 | blend |
| 40 | ✅ MACE 0.056 | ✅ AUC 0.807, MACE 0.054 | blend |
| 60 | ❌ MACE **0.130** | ✅ AUC 0.792, MACE **0.058** | blend |

The isotonic-only surface's calibration degrades monotonically with horizon (`mace_raw` 0.036→0.048→0.077→
0.109) and **fails the gate at 60d** — saturation (P(retry≤60d) is high, so predictions compress near the
top) plus per-step hazard error compounding, and at 60d the OOF isotonic even *overfits* (0.130 > the 0.109
raw). Phase 7 already solved this: averaging the discriminating classifier with the empirical estimator
(w=0.5) tempers the long-horizon over-confidence, so the **blend passes at 60d (MACE 0.058) with higher AUC
than isotonic at every horizon**. RS-4 therefore composes against the blend where it passes — so the 60d
composite is **surfaced, not withheld**. The isotonic gate remains the labelled fallback for any horizon
where the blend is unavailable/failing.

**Files (RS-4):** `src/yearline_universe/success_surface.py` (new) + `__init__.py` exports;
`hazard.py`, `ticker_pipeline.py`, `context_export.py` (additive wiring + optional schema key);
`tests/test_success_surface.py` (new, +5); `run_rs4_success_overlay.py` + artifacts.

> **Caveats travel with the surface.** The gate PASS is thin-sample and high-variance; the blend's
> calibration is largely base-rate shrinkage (see [`reliability/`](reliability/README.md)) — trust the
> ranking, size gently on the level, and re-validate walk-forward as labelled attempts accumulate.
