# Calibration & Trust-Gating a Probability — A Tutorial for Junior Engineers

**How to decide whether a probability is good enough to *show* — and how to build a
gate that honestly says "not yet." Taught through Phase 4 (V13.7): calibrating the
empirical retry probability, and a trust gate that — correctly — refuses to certify it
on thin single-ticker data.**

By the end you will understand:

- The two *different* questions a probability must pass: **calibration** ("are the
  numbers right on average?") and **discrimination** ("do they separate events from
  non-events?") — and why you need both.
- The standard metrics — **reliability curve, Brier, log-loss, AUC, MACE** — what each
  one catches and what it misses.
- **Leakage-safe evaluation**: purged, *transition-aware* (leave-one-transition-out)
  splits, and the **in-sample optimism trap** (why fitting and scoring a calibrator on
  the same rows is cheating).
- **Calibration transforms** (isotonic) — and the crucial fact that a *monotonic*
  transform can fix calibration but **cannot create discrimination** (AUC is invariant
  to it).
- The engineering payoff: a **trust gate** that surfaces a probability only when it
  earns it — and why "the gate fails" was the *right* result here.

> Third in the series. `performance_optimization_tutorial.md` made code faster;
> `optional_computation_feature_flags_tutorial.md` designed an opt-in switch;
> `empirical_estimator_over_model_extrapolation_tutorial.md` *built* this probability.
> This one asks: **should anyone trust it?** No finance background needed.

---

## Part 1 — The story (a number that looks fine and is still untrustworthy)

Phase 3 replaced a broken hazard *forecast* with an **empirical** "probability of a
retouch within H trading days" (`P10/P20/P40/P60` — see that tutorial's Appendix A).
For MSFT it produced a clean-looking `P40 = 0.781`.

But "looks clean" is not "is trustworthy." Before you surface a probability to a human
or a downstream system, you owe two questions an honest answer:

1. **Calibration** — when the model says 0.40, does the event happen ~40% of the time?
2. **Discrimination** — does the model give *higher* scores to the cases that actually
   happen than to those that don't?

Phase 4 builds the machinery to answer both, plus a **gate** that only lets a
probability through if it passes. The punchline up front: for single-ticker MSFT, the
gate **fails** — and that is the system working correctly.

---

## Part 2 — The core idea: calibration ≠ discrimination

These are independent properties. A junior instinct is to treat "accuracy" as one
thing; mature evaluation separates them.

```text
                 well-calibrated?          discriminating?
A. perfect              yes                      yes        ← what you want
B. "always predict the base rate"   yes (on avg) NO (AUC≈0.5)  ← useless but "calibrated"
C. confident but shifted   NO        yes (ranks correctly)    ← fixable by recalibration
```

- **Calibration** is about *levels*: bucket all the times you said ~0.4; did ~40% of
  them happen? A reliability curve plots predicted (x) vs observed (y); perfect
  calibration is the diagonal.
- **Discrimination** is about *ranking*: across all pairs of (event, non-event), how
  often did the event get the higher score? That's **AUC** (0.5 = coin flip, 1.0 =
  perfect).

Case **B** is the trap this phase exposes: a predictor that just returns the
overall base rate for everyone is *perfectly calibrated on average* and *completely
useless* (AUC ≈ 0.5). **Calibration alone is not enough.** You must check discrimination
too — which is exactly why the gate includes AUC.

---

## Part 3 — The metrics (what each one catches)

For a set of predicted probabilities `p` and binary outcomes `y`:

| metric | measures | catches | blind to |
|---|---|---|---|
| **Reliability curve** | calibration, by bin | *where* it's over/under-confident | overall accuracy |
| **MACE** (mean abs calibration error) | avg \|observed − predicted\| over usable bins | miscalibration magnitude | discrimination |
| **Brier** = mean((p−y)²) | calibration **+** discrimination (a proper score) | overall probabilistic accuracy | which of the two is wrong |
| **Log-loss** | same, punishes confident mistakes harder | overconfidence | (sensitive to clipping) |
| **AUC** | discrimination only | ranking power | calibration (invariant to monotone maps) |

The set is complementary on purpose: **Brier/log-loss** give a single proper score,
**MACE/reliability** isolate calibration, **AUC** isolates discrimination. Reading them
together tells you not just *that* a probability is bad but *which way*.

> **Brier decomposition (worth knowing):** Brier = reliability − resolution +
> uncertainty. A predictor can have a decent Brier purely from low "uncertainty" (a
> skewed base rate) while having near-zero "resolution" (no discrimination). Don't read
> Brier alone.

---

## Part 4 — Evaluating honestly (the hard part)

A metric is only as trustworthy as the split it's computed on. Two ways to fool
yourself, both avoided here:

### 4.1 Leakage — purged, transition-aware splits

If you predict a historical day's outcome using a reference pool that **includes that
day's own episode**, you've leaked the answer. So every prediction in the calibration
set **excludes its own transition** (`exclude_transition_key`) — *leave-one-transition-
out (LOTO)*:

```python
emp = empirical_horizon_probabilities_for_row(row, reference, horizons,
                                              exclude_transition_key=row["transition_key"])
```

This is the "purged, transition-aware" split: the unit of leakage is the *transition*
(an episode spans many correlated daily rows), so you must hold out the whole
transition, not a random row. (Random k-fold over correlated rows is a classic
time-series evaluation blunder.)

### 4.2 The in-sample optimism trap

We add an isotonic **recalibration transform** (Part 5). The temptation is to fit it on
the calibration set and then report its MACE *on that same set*. That MACE comes out
≈ **0.000** — and it's meaningless: isotonic can fit any monotone wiggle in-sample.

```text
mace_calibrated (in-sample) = 0.000   ← looks perfect, proves nothing
```

So the gate **does not use** the in-sample calibrated MACE. It uses the **raw**
reliability MACE and **AUC**. Which leads to the single most important property here:

### 4.3 AUC is invariant to monotonic transforms

Isotonic regression is monotonic. A monotonic map **cannot change the ranking** of
scores — so it cannot change AUC. Therefore:

> A recalibration transform can fix *calibration*, but it can **never manufacture
> *discrimination***. If AUC ≈ 0.5, no amount of isotonic/Platt scaling will save it.

That's why AUC is the honest, transform-proof core of the gate.

---

## Part 5 — The calibration transform (isotonic)

When a model *discriminates* but is *miscalibrated* (Case C), you can fix the levels
with a monotonic map fit on out-of-fold predictions:

- **Isotonic regression** — non-parametric, monotone step function; flexible, needs a
  fair amount of data, can overfit (hence out-of-fold + the in-sample caveat above).
- **Platt scaling** — fit a logistic on the scores; fewer parameters, smoother, better
  for small samples. (Fallback if isotonic is too hungry.)

We store the fitted transform as **serializable knots** (x/y thresholds) so it travels
in JSON and re-applies with a one-line interpolation — no pickled sklearn object:

```python
calibrated_p = np.interp(raw_p, x_thresholds, y_thresholds)   # apply_isotonic_knots
```

For MSFT the transform mapped the surfaced `P40 = 0.781 → 0.665`. But — see Part 6 —
that calibrated number still doesn't get surfaced, because discrimination fails.

---

## Part 6 — The trust gate (the payoff) and why "fail" was correct

A probability is surfaced as *trustworthy* only if it clears all three:

```text
trust_gate.passed  ⟺  AUC ≥ 0.60   AND   raw MACE ≤ 0.10   AND   n ≥ 50
```

The MSFT result (single-ticker, opt-in `calibrate=True`; 783 rows, 26 transitions,
leave-one-transition-out):

| horizon | n | observed | predicted | AUC | raw MACE | gate |
|---|---|---|---|---|---|---|
| 10 | 783 | 0.275 | 0.329 | 0.524 | 0.155 | ❌ |
| 20 | 783 | 0.434 | 0.512 | 0.430 | 0.314 | ❌ |
| 40 | 783 | 0.625 | 0.680 | **0.462** | 0.330 | ❌ |
| 60 | 783 | 0.770 | 0.821 | 0.465 | 0.181 | ❌ |

So the envelope reports `surfaced_probability_is_calibrated = false`, with
`calibration_gate_40d = {passed:false, auc:0.46, fail_reasons:[auc<0.6, mace_raw>0.1]}`.

**Why does it fail, and why is that right?** On *single-ticker* MSFT, the Phase 3
estimator falls back to a transition-only scope — it predicts roughly the same number
for every row, so it can't *rank* (AUC ≈ 0.46, barely a coin flip). A gate that let
that through would be surfacing a number with no discriminating power. **Refusing to
certify it is the feature.** Compare the V12.6 report's *8-ticker pooled* calibration
(n = 4227): AUC 0.745 at 40d — i.e. **pooling buys discrimination**, which is precisely
Phase 5's job.

> Design lesson: **"the gate fails" is a successful outcome for a gate.** The goal of
> calibration+gating is not to make every probability pass — it's to *only pass the
> ones that should*. A system that honestly withholds an untrustworthy number is more
> valuable than one that always emits a confident-looking guess.

---

## Part 7 — Engineering it

- **Opt-in (expensive) computation.** Building the calibration set rescans the panel
  with LOTO (≈ O(rows²) per horizon) — ~35s for one ticker. So it sits behind
  `calibrate=False` (threaded `run_hazard_layer → run_ticker_pipeline →
  run_universe_pipeline → --calibrate`), exactly the capability-vs-default pattern from
  the feature-flag tutorial. Default off keeps the hot path fast; the default envelope's
  `calibration_context.available` stays `false` and the probability is flagged
  uncalibrated (the safe default).
- **Schema-additive.** When calibration runs, it *fills* the existing
  `calibration_context` stub and *adds* `p_retry_within_40d_calibrated`,
  `calibration_gate_40d`, `surfaced_probability_is_calibrated` to `retry_hazard_context`
  — no existing field changes shape.
- **Provenance everywhere.** Every horizon ships `n`, `reference_scope`, raw vs
  calibrated metrics, and `fail_reasons` — so a consumer sees *why* a gate passed/failed.

---

## Part 8 — Generalizing the pattern

Any time you surface a model probability to a human or a downstream decision, run this
playbook:

```text
✔ Measure BOTH calibration (reliability/MACE/Brier) AND discrimination (AUC). Never one alone.
✔ Evaluate on a LEAKAGE-SAFE split (purge by the correlated unit — transaction, user, episode, time).
✔ If miscalibrated but discriminating → recalibrate (isotonic/Platt) on out-of-fold predictions.
✔ Remember a monotone transform can't add discrimination (AUC is invariant) — fix the model/data for that.
✔ NEVER report a calibrator's in-sample error; it's optimistic by construction.
✔ Gate surfacing on objective thresholds; let the gate say "no" and surface that honestly.
✔ Make calibration opt-in/cached if it's expensive; keep the default safe.
```

Where it applies: fraud/risk scores, medical risk, ETA confidence, lead-scoring,
ranking "match %", LLM confidence — anywhere a number implies "X% chance."

Anti-patterns:

```text
✗ Reporting AUC only ("0.9, ship it") while the probabilities are wildly miscalibrated.
✗ Reporting calibration only — a base-rate predictor is "calibrated" and useless.
✗ Random k-fold over correlated rows (leakage) → inflated metrics.
✗ Fitting and scoring a calibrator on the same rows → fake ~0 error.
✗ Surfacing a probability with no gate "because we computed it."
✗ Treating "the gate failed" as a bug to suppress rather than a signal to fix the data/model.
```

---

## Part 9 — Exercises (in this repo)

1. **Run it.** `run_ticker_pipeline("MSFT", ..., calibrate=True)`; print
   `calibration_context["summary"]`. Which horizon is closest to passing, and on which
   criterion does each fail?
2. **Prove AUC-invariance.** Take the 40d OOF predictions, apply the isotonic knots,
   recompute AUC. Confirm it's unchanged. Explain why in one sentence.
3. **Expose the in-sample trap.** Compare `mace_raw` (≈0.33 at 40d) with
   `mace_calibrated_in_sample` (≈0). Why is the second meaningless? How would you get an
   honest calibrated MACE? (hint: nested CV.)
4. **Break the purge.** Temporarily drop `exclude_transition_key` in the calibration
   dataset and recompute AUC/MACE. Do they improve? Why is that improvement fake?
5. **Predict Phase 5.** The gate fails on AUC because MSFT alone → transition-only
   scope. If pooling 8 tickers lets a *state-conditioned* scope win, why would AUC rise?
   What's the smallest change that could make the 40d gate pass?
6. **Design a Platt fallback.** Sketch where you'd add Platt scaling for horizons with
   too little data for isotonic, and how you'd choose between them.

---

## Part 10 — Key takeaways

```text
• A probability must pass TWO tests: calibration (levels right) AND discrimination (ranking right).
• Brier/log-loss = proper scores; MACE/reliability = calibration; AUC = discrimination. Read them together.
• Evaluate on leakage-safe, purged splits (hold out the correlated UNIT, e.g. the transition).
• A monotone recalibration (isotonic/Platt) fixes calibration but CANNOT add discrimination (AUC-invariant).
• Never trust an in-sample calibrator error; it's ~0 by construction.
• Gate surfacing on objective thresholds — and treat an honest "not yet" as success, not failure.
• Make expensive calibration opt-in; keep the default fast and the unproven number flagged.
```

The whole phase, in one line: *we didn't just compute a probability — we built the
machinery to decide whether it has earned the right to be shown, and accepted the
answer "not on this data, not yet."*

---

## Glossary

- **Calibration** — agreement between predicted probability and observed frequency
  ("when you say 0.4, it happens ~40% of the time").
- **Discrimination** — ability to rank events above non-events; measured by **AUC**.
- **Reliability curve** — predicted (x) vs observed (y) by probability bin; diagonal =
  perfect calibration.
- **Brier score** — mean squared error of probabilities; a *proper* score (rewards both
  calibration and discrimination). Lower is better.
- **Log-loss** — mean negative log-likelihood; punishes confident errors hardest.
- **AUC (ROC)** — probability a random event outranks a random non-event; 0.5 = chance.
  Invariant to monotonic transforms.
- **MACE** — mean absolute calibration error over (usable) reliability bins.
- **Isotonic regression** — monotone, non-parametric recalibration map; flexible, can
  overfit small samples.
- **Platt scaling** — logistic recalibration map; smoother, better for small data.
- **Purged / transition-aware split** — holding out the whole correlated unit (here a
  transition) to prevent leakage between train and test.
- **Leave-one-transition-out (LOTO)** — predict each transition's rows using a reference
  pool that excludes that transition.
- **In-sample optimism** — over-good metrics from evaluating a model on the same data it
  was fit on (an isotonic transform scored in-sample gives MACE ≈ 0).
- **Trust gate** — objective thresholds (AUC, MACE, n) a probability must clear before
  it is surfaced as trustworthy.

---

*Implementation: `src/yearline_universe/calibration.py` (dataset, metrics, isotonic,
gate, `build_calibration_context`), `hazard.py` (`run_hazard_layer(calibrate=True)`),
`ticker_pipeline.py` (threading), `context_export.py` (gate fields on
`retry_hazard_context`), `scripts/run_universe_mvp.py` (`--calibrate`). Tests:
`tests/test_calibration.py`. Case study + before/after: `docs/phased_design/phase_04/`.
Baseline: `docs/uploaded/yearline_v12_calibration_walkforward_report_v12_6.pdf`.
Educational material — finance content is incidental.*
