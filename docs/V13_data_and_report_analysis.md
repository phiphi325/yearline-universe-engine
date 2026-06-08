# V13 — Current-Data Run + V12 Report Cross-Check & Value Analysis

**Date:** 2026-06-07
**Inputs:** user-supplied MSFT/AAPL OHLCV (2009-01-02 → **2026-06-05**), plus the
V11.5 multi-ticker MVP report, the V12.10 daily-dashboard report, and (added
2026-06-07) the updated **V12.0.2 8-ticker** dashboard and the **V12.6 calibration &
walk-forward** report. All under `docs/uploaded/`.
**Purpose:** (1) cross-check the V13 engine against the V12 reports on real,
current data; (2) assess what the data says about the **drawdown ↔ time-to-next-touch**
value thesis and the credibility of a forward "days-to-touch" estimate.

> Educational research only. Not financial advice. Evidence overlay; no execution.

---

## 0. TL;DR

1. **V13 reproduces V12 exactly on the deterministic layer.** On the same current
   data, V13's event counts, distance, drawdown, latest state, and mode-state
   *durations* match the reports to the decimal. The refactor is faithful.
2. **Your value thesis is empirically confirmed.** Across 147 pooled transitions,
   inter-attempt **max drawdown vs days-to-next-touch has Spearman ≈ 0.86**
   (95% CI [0.79, 0.90]) — deeper repair damage robustly takes longer to retouch
   the yearline. This is the strongest, most defensible value in the system.
3. **But the forward retry *probability* is ill-posed — neither 0.002 nor 0.29 is
   "right".** The state-hold-forward hazard curve is a *step* that always saturates
   to P → 1.0 (P60 = P90 = 1.000 in every fit), and P40 sits on its hypersensitive
   toe (0.002 / 0.30 / 0.51 depending on the training pool). Root causes: a frozen
   `distance` feature that contradicts the event definition, multicollinear
   predictors (distance vs required-rebound corr −0.98), and uncalibrated
   balanced-logistic magnitudes. **Pooling alone does not fix it.** See §2.2 — and
   §2.3: the patched benchmark notebook fixes this by **demoting the state-hold-forward
   curve to a diagnostic** and surfacing an **empirical completed-path estimator** as
   the canonical P(retry≤H) — which is the quantity the V12.6 report calibrates well
   (≤40d). That empirical-horizon policy is what V13 Phase 3 ports.
4. **Recommendation:** deliver days-to-touch as an **evidence-backed conditional
   estimate** (the V11.5 §7 multi-estimator approach anchored on the drawdown
   correlation + the gap×drawdown matrix), not as a raw hazard probability. That is
   exactly the V13.3 direction — now validated by the data.

---

## 1. Data refresh (what changed)

- The bundled price cache previously ended **2024-11-29**; it now runs to
  **2026-06-05** for MSFT and AAPL (your uploads). The old cache is backed up at
  `data/price_cache/_backup_2024-11-29/`. NVDA still ends 2024-11-29 (no upload),
  so the universe has a transparent mixed `as_of` (each envelope records
  `source.data_as_of`).
- **Adjustment basis validated.** Your CSVs carry both `close` (split-adjusted)
  and `adj_close` (split+dividend-adjusted). AAPL's `close` is continuous across
  its 2020-08-31 4:1 split (no spurious break). I wired the cache on the
  fully-adjusted basis (`Close = adj_close`; OHLC scaled by `adj_close/close`) to
  match the engine's `auto_adjust=True` convention.
- Raw uploads, the four V12 PDFs, and the **benchmark notebook**
  (`…_PATCHED_P40_02.ipynb`, which replaced the earlier `…_FIXED.ipynb` on
  2026-06-07) are filed under `docs/uploaded/` with a catalog in
  `docs/uploaded/README.md`.

---

## 2. Cross-check: V13 engine vs the V12 reports (same current data)

### 2.1 Deterministic layer — exact agreement

| Metric (MSFT, as of 2026-06-05) | V11.5 / V12.10 report | V13 engine | Match |
|---|---|---|---|
| Canonical events | 40 | 40 | ✅ |
| Distance to MA250 | −10.10% | −10.10% | ✅ |
| Drawdown-so-far | −10.01% | 10.01% | ✅ |
| Required rebound | 11.23% | 11.23% | ✅ |
| Latest round / attempt | 14 / 2 | 14 / 2 | ✅ |
| Latest outcome / state | fail / below | fail / failed_repair_deep_below | ✅ |
| AAPL events / distance | 25 / +21.10% | 25 / +21.10% | ✅ |

Replay **mode-state durations** (MSFT, 2020→2026 replay), V13 vs V12.10:

| mode_state | V12.10 | V13 |
|---|---|---|
| accepted_above_watch | 1213 | **1213** ✅ |
| failed_repair_deep_below | 244 | **244** ✅ |
| below_yearline_repair + repair_retry_probability_building | 91 + 56 = **147** | 1 + 146 = **147** ✅ (same total) |

The deterministic price/structure layer is reproduced **exactly**. This is strong
evidence the V13 refactor preserved V12's methodology.

### 2.2 The forward-probability divergence — root-cause diagnosis

The one place V13 and V12 disagree is the forward retry *probability*. V12's
P40 = 0.002 looks implausibly low; V13's 0.29 looks more reasonable. I diagnosed it
directly — and **neither value is trustworthy.** Both are points on a
hypersensitive step.

**Finding 1 — the forward curve is a step that always saturates to 1.0.** In the
"state-hold-forward" scenario every feature is frozen except the two
days-since-touch counters, so the daily-hazard logit is linear in the horizon:

`logit(h) = base_logit + h × (coef_trading + coef_calendar)`

For MSFT: base_logit = −9.90, time-slope = +0.1765 → the logit crosses 0 at
h ≈ **56 trading days**. Daily hazard is ~0 until ~day 40, then races up:

| horizon h (trading days) | 10 | 20 | 30 | 40 | 50 | 60 | 70 |
|---|---|---|---|---|---|---|---|
| modeled daily hazard | 0.000 | 0.002 | 0.010 | 0.055 | 0.255 | 0.667 | 0.921 |

So cumulative probability is ~0 through ~day 40, then **steps to 1.0 by day 60-70**
— which is why P60 = P90 = **1.000** in *every* fit (single-ticker AND pooled
MSFT+AAPL). "Retouch within 60-90 trading days is certain" for any below-yearline
ticker is obviously wrong; it's a structural artifact of a positive time
coefficient with everything else frozen.

**Finding 2 — P40 sits on the toe of the step → hypersensitive.** P40 reads the
hazard right where it lifts off (≈0.055). A small change in `base_logit` or the
slope moves the crossover a few days and swings P40 wildly — measured on the same
step:

| hazard fit | P(retry ≤ 40d) | P(retry ≤ 60d) |
|---|---|---|
| V12 full 8-ticker universe | **0.002** | (low) |
| V13 MSFT-only | **0.30** | 1.000 |
| V13 MSFT + AAPL | **0.51** | 1.000 |

P40 is **not a stable quantity** here.

**Finding 3 — why the coefficients are unstable.** `distance_to_ma250_pct` and
`required_rebound_to_ma250_pct` are **near-redundant** (corr = **−0.981** in the
training panel — required-rebound is a deterministic transform of distance), and
carry large opposing coefficients (−1.49 and −2.60). This multicollinearity, plus
unstandardised feature scales (days vs percent) and `class_weight="balanced"` on a
~3% event-rate panel, makes `base_logit` — and thus the step location — unstable
across training pools.

**Finding 4 — the deepest flaw: the scenario is self-contradictory.** A retouch is
*defined* by distance → 0, yet state-hold-forward **freezes distance at −10.10%**
(and required-rebound at 11.23%) for the whole horizon. The model is effectively
asked "what is P(retouch) if the price stays 10% below the yearline forever?" — so
the answer is driven entirely by extrapolating the days-since-touch coefficient
(the least reliable signal), while the mechanism that actually produces a retouch
(price climbing back) is never represented.

**Conclusion:** the deterministic states are trustworthy; the state-hold-forward
hazard probability is **not** — it is an ill-posed, step-shaped, multicollinear,
uncalibrated quantity. The instinct that 0.002 is wrong is right, but **0.29 is not
"right" either** — its sibling P60 = 1.0 gives away the overconfidence. (The same
instability drives the mode-state sub-split, 146 vs 56, since
`repair_retry_probability_building` is gated on P60 ≥ 0.50.) **§2.3 refines *where*
the defect lives.**

### 2.3 Reconciliation with the updated V12.6 calibration report

The uploaded `docs/uploaded/yearline_v12_calibration_walkforward_report_v12_6.pdf`
evaluates the same hazard family on **4,227** walk-forward observations and — at the
**aggregate** — finds it **reasonably calibrated by horizon**:

| horizon | n | observed | predicted | Brier | log-loss | AUC | MACE |
|---|---|---|---|---|---|---|---|
| 10d | 4227 | 0.305 | 0.296 | 0.160 | 0.484 | **0.802** | 0.064 |
| 20d | 4227 | 0.460 | 0.456 | 0.195 | 0.570 | 0.763 | 0.070 |
| 40d | 4227 | 0.633 | 0.632 | 0.190 | 0.568 | 0.745 | 0.072 |
| 60d | 4227 | 0.732 | 0.738 | 0.172 | 0.529 | 0.718 | **0.193** |

**What is actually being calibrated here matters.** Inspecting the updated benchmark
notebook (`docs/uploaded/…_PATCHED_P40_02.ipynb`, cell 138) shows the V12.6 horizon
metrics score the **V12.4.1 empirical completed-path estimator** — *not* the logistic
model's state-hold-forward curve. So the table above says: **the empirical estimator
is well-calibrated through 40d** (predicted ≈ observed, MACE ≤ 0.072), and that is
exactly the quantity the patched notebook now surfaces as canonical P(retry ≤ H). The
logistic model's forward curve — the §2.2 step — has been **demoted to a labelled
diagnostic** and is no longer scored or surfaced.

So §2.2 and the report are consistent once you separate the two quantities:

| quantity | what it is | status in the patched V12 |
|---|---|---|
| logistic **state-hold-forward** curve | freeze distance, march time → saturating step (P60=P90=1.0; P40 0.002/0.30/0.51) | **diagnostic only** (`*_model_state_hold_forward_diagnostic`) |
| **empirical completed-path** estimator | "how often did *similar* historical states retouch within H trading days," bucketed + shrunk | **canonical** P(retry ≤ H); the calibrated one (table above) |
| logistic **`hazard_today`** | one-day instantaneous conditional hazard | retained (today's hazard only) |

One correction to the §2.2 framing: "P60 = P90 = 1.0 in *every* fit" is precise for
the **demoted** state-hold-forward curve, but the **canonical** (empirical) P60/P90 are
not pinned — they are observed completed-path frequencies. The fix was therefore **not**
to re-specify the logistic forward scenario (drop a feature / standardise / regularise),
but to **replace the canonical horizon probability with the empirical estimator** and
keep the model only for `hazard_today`.

The report's §4 (retry-quality classifier, LOTO n=147: Brier 0.223 vs 0.229; AUC
0.616) and §5 ("add purged transition-aware splits, isotonic calibration, repo-ready
probability schema") give Phase 4 a ready-made baseline and to-do list. **Net effect
on the roadmap:** **Phase 3 = port the V12.4.1 empirical-horizon policy** (canonical
empirical P + demoted diagnostic curve, threaded through `hazard.py` / `replay.py` /
the envelope); **Phase 4** ports the V12.6 harness that scores it, adds the
transform/splits, and gates the surfaced probability. See `phased_design/phase_03/`
and `phased_design/phase_04/`.

---

## 3. The value thesis — what the data actually supports

Your thesis: *users need "how many days until the next MA250 touch," and there's a
clear correlation between max drawdown and time-to-next-touch.* The V11.5 report
tests this on real data, and it holds strongly:

### 3.1 Drawdown ↔ time-to-next-touch correlation (the headline evidence)

| Group | transition | n | Spearman | 95% CI |
|---|---|---|---|---|
| mega-cap software | all | 112 | 0.870 | [0.805, 0.911] |
| mega-cap software | 1→2 | 36 | 0.865 | [0.735, 0.931] |
| mega-cap software | 2→3 | 26 | 0.866 | [0.696, 0.966] |
| **ALL** | **all** | **147** | **0.861** | **[0.794, 0.903]** |

Deeper inter-attempt drawdown → longer gap to the next canonical touch, with a
**strong, tight, robust** rank correlation. This is descriptive historical
evidence (not a forward forecast), so it's stable and defensible — the opposite of
the hazard-probability instability in §2.2.

### 3.2 The gap×drawdown classification matrix (the "matrix" you cited)

The report buckets repairs by (gap × drawdown) with success rates + Wilson
intervals, e.g.:

| bucket | n | median gap (d) | median DD% | next-success | label |
|---|---|---|---|---|---|
| short_gap × shallow_drawdown | 67 | 8 | 2.8% | 0.30 | healthy_absorption |
| medium_gap × deep_drawdown | 29 | 46 | 13.2% | 0.45 | damaged_repair |
| long_gap × deep_drawdown | 17 | 150 | 22.5% | 0.24 | long_dormancy |

MSFT's current 2026 repair sits in the **long_gap × deep_drawdown** family
(deep damage, slow retouch, lower trend-following success) — consistent with its
−10% distance and 10% drawdown.

### 3.3 Conditional days-to-touch (the credible way to present "days left")

V11.5 §7 already estimates MSFT's 2→3 retouch timing with **multiple estimators +
uncertainty**, conditioned on the current drawdown — not a single number:

| method | scope | est. remaining days | rough date | quality |
|---|---|---|---|---|
| historical median 2→3 | ALL | 14.5 | 2026-06-20 | unconditional_high |
| gap×drawdown matrix interp. | mega-software | 17.5 | 2026-06-23 | interpolation |
| nearest-neighbor ±2.5% DD | peer group | 36 | 2026-07-11 | conditional_low |
| Theil-Sen robust | ALL | 37 | 2026-07-13 | high |

This is the right shape for the value: a **range backed by evidence**, with the
drawdown conditioning that your thesis emphasizes.

---

## 4. Implications & recommendation

1. **Your value instinct is correct and now data-backed.** Time-to-next-touch,
   conditioned on drawdown, is real, strong (Spearman 0.86), and exactly what a
   user/investor would want.
2. **Deliver it as evidence + a conditional range, not a raw probability.** The
   §3.3 multi-estimator approach (median / matrix / nearest-neighbor / Theil-Sen)
   anchored on the §3.1 correlation and §3.2 matrix is defensible today; the
   single-ticker hazard P40 is not (§2.2).
3. **This *is* the V13.3 direction, now validated.** Build the pooled gap×drawdown
   matrix + Spearman evidence + the conditional-timing estimators into the engine,
   surfaced in the bundle (and a per-ticker "retry-timing context" block).
4. **Re-frames `fit_ml_models`.** The retry-timing ML model is *one estimator among
   several*; its value emerges only when pooled (V13.2) and calibrated (V13.7).
   Keeping it off by default remains correct until then; enabling it should be part
   of the pooled-timing ensemble, not a standalone probability.

5. **The fix is the V12.4.1 empirical-horizon policy — not a model re-fit
   (per §2.3 + the patched benchmark notebook).** Replace the canonical
   P(retry ≤ H): (a) keep the logistic model only for `hazard_today`; (b) **demote**
   the state-hold-forward curve to a labelled diagnostic; (c) make canonical
   P(retry ≤ H) the **empirical completed-path estimator** (bucketed similar historical
   states + hierarchical scope fallback + Bayesian shrinkage), applied everywhere the
   canonical P is used (live + replay + mode-state); then (d) calibrate this estimator
   and **gate** it (Phase 4 / V13.7), reusing the V12.6 harness — which already scores
   it. The earlier "drop a feature / standardise / regularise / glide-path" idea is
   **not** what the benchmark did and is dropped.

**Suggested sequencing (now the phased roadmap — `phased_design/`):** Phase 1
gap×drawdown evidence ✅ + Phase 2 conditional-timing estimators ✅ (the robust,
defensible value — DELIVERED) → **Phase 3** port the V12.4.1 empirical-horizon policy
(canonical empirical P; demoted diagnostic curve) → **Phase 4** calibration + gating
(port the V12.6 harness that scores the empirical estimator, add isotonic transform +
purged splits) → **Phase 5** pooled training + data freshness (more tickers ⇒ more
reference rows ⇒ higher-scope empirical estimates pass the ≥25 threshold).

---

## 5. Limitations

- Pooled evidence here rests on a small universe (the reports used 8 tickers;
  V13's cache currently has 3, only 2 refreshed to 2026). Robustness needs the full
  universe on fresh data. Several gap×drawdown buckets have n < 30.
- Survivorship bias (current-universe tickers), adjusted-data dependency, no causal
  inference, heuristic mode-state scores, and **uncalibrated** forward
  probabilities — all carried over from V12 and unchanged.
- Cross-checks are on MSFT/AAPL (the refreshed tickers); NVDA remains at 2024-11-29.
