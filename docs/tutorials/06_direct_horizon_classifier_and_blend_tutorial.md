# Discrimination over recalibration — a direct horizon classifier, and the blend

*Audience: ML students and engineers. The finance is incidental; the lesson is how to add a
learned model **on top of** a working statistical estimator without fooling yourself — and how
to ship it honestly. Worked through this engine's Phase 7. Not investment advice.*

---

## 0. The one-paragraph version

We already had a **calibrated count**: "of similar historical at-risk states, how often did the
stock retouch its yearline within H days?" (the empirical completed-path estimator, Phases 3–5).
It clears the trust gate at ≤40 days. Phase 7 asked a different question: not *"is the probability
well-calibrated?"* (it is) but *"can we **rank** better?"* — separate the repairs that are about to
retouch from the ones still falling. That is **discrimination (AUC)**, and the lever for it is
**better features + a model**, not more recalibration. The arc below builds that model, proves it
generalizes to unseen names, and ships it as a **gated, opt-in overlay** that never overwrites the
trusted canonical number.

## 1. Calibration vs discrimination — why a *new* model at all

Two different virtues of a probability (the previous tutorial, `04_…calibration…`, covers this):

- **Calibration** — when you say 0.60, does it happen ~60% of the time? Fixed by *recalibration*
  (isotonic/Platt), which is a monotone transform.
- **Discrimination (AUC)** — do *higher* scores correspond to *sooner* retouches? A monotone
  transform **cannot change AUC**. The only way to discriminate better is to give the model
  information it didn't have.

The empirical estimator conditions on **static buckets** (distance, drawdown, days-since-touch). It
literally cannot tell "10% below the yearline and *bouncing hard*" from "10% below and *still making
lower lows*." Those have very different retouch timing. So the cheapest, largest AUC win is
**features that encode the path**, then a model that uses them. Recalibration was the wrong tool;
discrimination is the goal.

## 2. Leakage-safe features (the part most people get wrong)

Two feature families, every column **backward-looking** — value at date *t* uses only data ≤ *t*:

- **Path-dynamic** (`features.py`): trailing returns, short-MA trend/slope, **distance-to-yearline
  dynamics** (is the gap closing or widening?), the de-correlated repair gap, volatility level /
  percentile / range-compression, and repair-relative "how far has it reclaimed off the low, how
  fast?"
- **Cross-sectional / regime** (`cross_sectional.py`): a market-regime proxy (a broad ETF's own
  yearline state), equity **breadth** and return **dispersion**, and **peer-relative strength**
  (this name minus the cross-section median).

**The discipline that makes this trustworthy:** a test recomputes each feature on the *full* series
and on a series *truncated at t*, and asserts row-*t* is identical (`test_features.py`,
`test_cross_sectional.py`). If a feature ever peeks at the future, that test fails. Cross-sectional
features at *t* combine other names' *contemporaneous* (≤ *t*) values — observable at *t*, no
look-ahead. Get this wrong and every downstream number is a lie; the truncation test is cheap
insurance.

## 3. Labels and the sample you *actually* have

For each **completed** at-risk day, label `y_H = 1` if the realised next retouch was within H trading
days. Two non-obvious points:

- **Censoring is leakage-safe by construction.** Only completed transitions have a known event day,
  so only they are labelled — a live/censored row is never silently labelled "negative."
- **Think in episodes, not rows.** The pooled table is **4,765 daily rows but only ~162 independent
  transitions (episodes)**. Rows inside one episode are autocorrelated. Your *effective* sample is
  ~162. This governs everything: model complexity, cross-validation, and how much to believe a
  decimal place.

So cross-validation must be **episode-aware**: `GroupKFold` purged by `transition_key`, so an entire
episode is in train or test, never split. (A diagnostic in `test_models.py` plants a within-episode
label leak and confirms purged folds do *not* inflate AUC to ~1.0.)

## 4. The model: regularized logistic, primary; GBM, diagnostic-only

With ~162 episodes, a flexible model will memorize. So:

- **Primary = L2 logistic** (impute → standardize → logistic). Linear, low-variance, and its
  probabilities stay meaningful for a calibration read.
- **Diagnostic-only = gradient boosting** (shallow, sub-sampled). Reported as an *upper bound* on
  non-linear signal, **never promoted** — it would overfit 10² episodes. (In practice it tied the
  logistic at 20/40d, so there was no case to take its variance.)

**Head-to-head, episode-aware OOF** (classifier vs the empirical baseline on identical rows; the
baseline column is itself leave-one-transition-out, so it's held-out vs held-out):

| H | empirical AUC | logistic AUC (path) | +cross-sectional | 
|---|---|---|---|
| 10 | **0.816** | 0.802 | 0.804 |
| 20 | 0.779 | 0.785 | 0.783 |
| 40 | 0.762 | 0.775 | **0.794** |
| 60 | 0.738 | 0.762 | 0.762 |

The classifier wins at 20/40/60d and improves long-horizon calibration; **10d stays empirical** —
where short-term static state is abundant, the buckets are already excellent. Cross-sectional
features **stack** a clear win at **40d** (+0.018 AUC, MACE 0.067→0.043) — but, against the
going-in hypothesis, **do not rescue 60d**. We report that negative as loudly as the wins.

## 5. The test that matters most: an *unseen name*

Transition-purged CV still lets a model learn a name's quirks from its *other* episodes. Deployment
doesn't work that way — you'll score names with little or no history. So we also run
**leave-one-*ticker*-out** (hold out a whole name) and measure the **generalization gap**:

| H | transition-purged AUC | leave-one-ticker-out AUC | gap |
|---|---|---|---|
| 10 | 0.811 | 0.815 | ≈ 0 |
| 20 | 0.790 | 0.811 | ≈ 0 |
| 40 | 0.796 | 0.800 | ≈ 0 |
| 60 | 0.779 | 0.773 | ≈ 0 |

**The gap is ≈ 0.** Holding out a whole name doesn't collapse AUC — the signal lives in
*generalizable* path/regime features, not memorized ticker identity. (Two safeguards help here:
**episode row-weighting** ≈ 1/√(rows-per-episode), so a 200-day dormant episode doesn't outvote a
10-day one in the fit; it improves AUC at every horizon, most at 60d.)

## 6. The ranker/estimator tradeoff → the blend

Under the unseen-name test a clean split emerges: the **classifier ranks better** (AUC) but
**calibrates worse** (it's a touch over-confident), while the shrunk **empirical count calibrates
better**. Don't choose — **blend** them. A per-horizon convex mix `w·classifier + (1−w)·empirical`,
with `w` chosen out-of-fold by Brier:

| H | classifier MACE | empirical MACE | blend w | **blend AUC** | **blend MACE** |
|---|---|---|---|---|---|
| 10 | 0.103 | 0.047 | 0.25 | **0.835** | 0.050 |
| 20 | 0.094 | 0.041 | 0.50 | **0.819** | 0.043 |
| 40 | 0.078 | 0.045 | 0.50 | **0.806** | 0.041 |
| 60 | 0.085 | 0.080 | 0.50 | **0.786** | 0.068 |

The blend **beats both standalone surfaces on AUC at every horizon** *and* restores near-empirical
calibration — the best numbers of the whole phase, with MACE clearing the 0.10 gate at all four
horizons, **including a finally-defensible 60d**. This is "hierarchical shrinkage" in spirit: lean
on the high-variance learner where it discriminates, on the low-variance estimator where it doesn't.

## 7. Capability before consumer — and shipping output-changing work safely

Everything above was built **without changing a single envelope field** ("capability before
consumer"): each PR added a module + tests and a head-to-head measurement, nothing surfaced. Only
once the blend won under the unseen-name test did we wire it in — and even then **opt-in and
additive**:

- A **compute-once** model (`build_blend_model`) is built once per universe and threaded per ticker
  (like the calibration model); a cheap **live apply** scores the current state.
- `surface_blend=False` by default ⇒ the envelope is **byte-identical** to before. Turn it on and
  the envelope gains an *additive*, labelled, **gated** `direct_classifier_blend` block. The
  empirical estimate stays **canonical**; the blend never overwrites it. A universe-wide check
  confirms: stripping the new key reproduces the old envelope for every ticker and field.

This is the same pattern as the engine's other expensive, optional work (`calibrate`,
`fit_ml_models`): *don't pay for what you don't consume, and don't change output until you opt in.*

## 8. What to take away (the method, not the numbers)

1. **Name the virtue you're improving.** Calibration and discrimination are different; only one of
   them is fixed by recalibration. Pick the right tool.
2. **Spend on features before model complexity** — and prove they don't leak with a truncation test.
3. **Count episodes, not rows.** It sets your model complexity *and* your CV scheme.
4. **Validate the way you'll deploy** — leave-one-*group*-out, not just purged k-fold.
5. **Blend a ranker with a calibrated estimator** instead of choosing; pick the weight out-of-fold.
6. **Report the negatives** (60d) as clearly as the wins.
7. **Ship behind an opt-in, additive, gated switch**, with a byte-identity check on the default path.

### Companion code
`features.py`, `cross_sectional.py`, `labels.py`, `models.py`, `generalization.py`,
`blend_surface.py`; tests `test_features.py`, `test_cross_sectional.py`, `test_models.py`,
`test_generalization.py`, `test_blend_surface.py`; the phase write-up in
`docs/phased_design/phase_07/`. For the concrete live example, see
`07_msft_low_readiness_repair_blend_walkthrough.md`.
