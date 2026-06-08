# AUC, Calibration & MACE — A Tutorial for Machine-Learning Students

**A from-first-principles guide to the two questions every probability model must
answer — *can it rank?* (AUC) and *are its numbers right?* (calibration / MACE) —
worked through a real model that predicts "will this stock retouch its 250-day moving
average within H days?", and how pooling data lifted both metrics at the 10/20/40-day
horizons.**

> Audience: a college student who has seen logistic regression and train/test splits,
> but not necessarily ROC curves or calibration. No finance background needed; the
> finance is the running example. **Educational only — nothing here is investment
> advice.**

### What you'll be able to do by the end
- Define **AUC** precisely (ROC curve + the ranking/probability interpretation) and
  compute it by hand.
- Define **calibration**, draw a **reliability diagram**, and compute **MACE** (and see
  how it relates to **ECE** and the **Brier score**).
- Explain *why discrimination and calibration are different things* and why a model
  needs both.
- Explain, in ML terms, **why pooling more data raised AUC from ~0.46 to ~0.78 and
  cut MACE from ~0.3 to <0.08** at short horizons.
- Translate those metrics into what they would (and would not) let an investor do.
- List concrete next steps to push the metrics further.

---

## 1. The prediction problem

Our model answers, for a stock that is below its 250-day moving average ("the
yearline") and "trying" to climb back:

> *What is the probability it retouches the yearline within **H trading days**?*
> (for H = 10, 20, 40, 60). Call it `P(retry ≤ H)`.

For each horizon H this is a **binary probabilistic classifier**: input = today's
"state" (how far below, how long it's been, how deep the drawdown), output = a number
in [0, 1]; the label is 1 if the retouch actually happened within H days, else 0.

To judge such a model you ask two *independent* questions:

| Question | Property | Metric here |
|---|---|---|
| Can it tell "soon" cases from "not-soon" cases? (does a higher score mean more likely?) | **Discrimination** | **AUC** |
| When it says 0.40, does the event happen ~40% of the time? | **Calibration** | **MACE** (and reliability curve, Brier) |

A model can be good at one and terrible at the other. That is the single most important
idea in this tutorial, so let's build both up carefully.

---

## 2. AUC — measuring discrimination

### 2.1 Thresholds, TPR, FPR

A classifier outputs a *score* (here a probability). To turn scores into yes/no
decisions you pick a **threshold** t: predict "yes" if score ≥ t. For any t you get:

- **TPR** (true-positive rate, "recall," "sensitivity") = TP / (TP + FN) — of the
  events that *did* happen, what fraction did we flag?
- **FPR** (false-positive rate, 1 − specificity) = FP / (FP + TN) — of the non-events,
  what fraction did we *wrongly* flag?

Lower the threshold → you flag more things → both TPR and FPR rise. There's a
trade-off, and the *right* threshold depends on your costs (more on that in §7).

### 2.2 The ROC curve and AUC

Sweep the threshold from 1 down to 0 and plot **TPR (y) vs FPR (x)**. That traces the
**ROC curve** (Receiver Operating Characteristic). The **AUC** is the *area under that
curve*.

- A model that ranks **perfectly** (every event scored above every non-event) hugs the
  top-left corner → **AUC = 1.0**.
- A model that scores **randomly** sits on the diagonal → **AUC = 0.5**.
- Rough rule of thumb (domain-dependent!): 0.5 useless · 0.6 weak · 0.7 fair · 0.8 good
  · 0.9 strong.

### 2.3 The interpretation that makes AUC click

> **AUC = the probability that a randomly chosen *positive* gets a higher score than a
> randomly chosen *negative*.** (This equals the normalized Mann–Whitney U statistic.)

So AUC is purely about **ranking**, not about the score's absolute level. Two
consequences you must internalize:

1. **AUC is threshold-free** — it summarizes *all* thresholds at once.
2. **AUC is invariant to any monotone (order-preserving) transform of the scores.**
   If you replace every score `s` by `f(s)` for an increasing `f`, the ranking — and
   therefore the AUC — is unchanged. (Remember this for §6: a recalibration map is
   monotone, so it can fix calibration but **cannot** change AUC.)

### 2.4 Compute it by hand

Four examples, `(score, label)`: (0.90, 1), (0.60, 0), (0.55, 1), (0.30, 0).
Positives = {0.90, 0.55}; negatives = {0.60, 0.30}. Check all 2×2 positive–negative
pairs — does the positive outrank the negative?

| pair (pos vs neg) | pos higher? |
|---|---|
| 0.90 vs 0.60 | ✓ |
| 0.90 vs 0.30 | ✓ |
| 0.55 vs 0.60 | ✗ |
| 0.55 vs 0.30 | ✓ |

3 of 4 pairs correct → **AUC = 0.75**. (Ties count as ½.) That's the whole idea.

### 2.5 Caveats every student should know
- **AUC ignores calibration.** Scores of {0.01, 0.02} and {0.81, 0.82} give the same
  AUC if the ordering is the same. AUC will not tell you the numbers are usable as
  probabilities (that's §3).
- **Class imbalance:** with very rare positives, ROC-AUC can look flattering; the
  **precision–recall AUC (PR-AUC)** is often more informative. (Our horizons are not
  badly imbalanced — base rates ~0.3–0.7 — so ROC-AUC is fine here.)
- **AUC is a ranking summary, not a decision.** You still choose a threshold for action.

---

## 3. Calibration and MACE — measuring whether the numbers are *true*

### 3.1 What "calibrated" means

A model is **calibrated** if, among all the times it says "0.40," the event really
happens about 40% of the time — for every probability level. Formally, calibration
asks that `E[Y | p̂ = p] = p`.

Discrimination (AUC) said *"higher score ⇒ more likely."* Calibration says *"a score of
0.4 means 0.4."* You can have one without the other (§5).

### 3.2 The reliability diagram

You can't condition on an exact value `p̂ = 0.4` with finite data, so you **bin**.
Here we use 10 equal-width bins [0,0.1), [0.1,0.2), …, [0.9,1.0]. In each bin compute:

- `predicted_mean` = average predicted probability in the bin (x-axis),
- `observed_rate` = fraction of actual events in the bin (y-axis).

Plot observed (y) vs predicted (x). **Perfect calibration = the 45° diagonal.** Points
**below** the diagonal = the model is **over-confident** (predicts more than happens);
**above** = under-confident.

### 3.3 MACE (and its cousin ECE)

Define each bin's calibration gap as `|observed_rate − predicted_mean|`. Then:

- **MACE** (Mean Absolute Calibration Error, as used in this project) = the **simple
  average** of that gap over **"usable" bins** (we require ≥ 10 samples in a bin so a
  3-point bin can't dominate).
- **ECE** (Expected Calibration Error) = the **sample-weighted** average of the same
  gap (bins with more points count more). ECE ≈ "typical error a random prediction
  suffers"; MACE ≈ "typical error across the probability range." They usually agree;
  MACE is stricter about sparse high/low bins.

Both are in probability units: **MACE = 0.05 means "off by ~5 percentage points on
average."** Lower is better; 0 is perfect.

### 3.4 Compute MACE by hand

Suppose a 40-day model produced this reliability table (one row per usable bin):

| bin | n | predicted_mean | observed_rate | \|gap\| |
|---|---|---|---|---|
| 0.5–0.6 | 120 | 0.55 | 0.60 | 0.05 |
| 0.6–0.7 | 200 | 0.65 | 0.62 | 0.03 |
| 0.7–0.8 | 90 | 0.74 | 0.66 | 0.08 |

**MACE** = (0.05 + 0.03 + 0.08) / 3 = **0.053**. (ECE would weight by n=120/200/90:
(0.05·120 + 0.03·200 + 0.08·90)/410 = **0.044** — close, slightly lower because the
worst bin is the smallest.)

### 3.5 The Brier score ties it together

The **Brier score** = mean squared error of the probabilities, `mean((p̂ − y)²)`. It's
a **proper scoring rule** (minimized by the true probabilities) and it rewards *both*
calibration and discrimination. Murphy's decomposition makes that explicit:

```
Brier = reliability − resolution + uncertainty
        (calibration  (discrimination,   (base-rate variance,
         error, ↓)     ↑ is good)          irreducible)
```

Lesson: a model can post a so-so Brier with **zero resolution** (predict the base rate
for everyone) — which is exactly the failure AUC catches and Brier alone can hide. So
report **AUC + a calibration metric (MACE) together**, not a single number.

---

## 4. The two-axis picture (why you need both)

```
                 calibrated?            discriminating (AUC)?
A  ideal             yes                       yes        ← deploy
B  base-rate         yes (on average)          NO (≈0.5)  ← "calibrated but useless"
C  shifted/over-conf NO                        yes        ← FIXABLE by recalibration
D  bad               no                        no         ← back to the drawing board
```

- **B** is the trap: a model that outputs the overall event rate (say 0.62) for
  *everyone* is perfectly calibrated on average yet ranks nothing (AUC ≈ 0.5). Useless
  for any decision that needs to *choose between* cases.
- **C** is the good news: if a model **ranks** well (high AUC) but its levels are off,
  a monotone **recalibration** map (isotonic regression or Platt scaling) fits a curve
  from raw score → empirical frequency and repairs calibration **without touching AUC**
  (because the map is monotone — §2.3).

This is why our project surfaces a probability only when it passes **both** an AUC
floor *and* a MACE ceiling.

---

## 5. The case study: how pooling fixed both metrics

The model is an **empirical, "borrow-strength-from-similar-history" estimator** (a
cousin of k-nearest-neighbors). To predict `P(retry ≤ H)` for today's state it:

1. **buckets** the state (how-far-below × how-long × how-deep),
2. finds historical days in **similar** buckets via a specific→general **scope ladder**
   (this ticker → its peer group → the whole universe), stopping at the first scope
   with **≥ 25** samples,
3. takes the fraction of those neighbors that retouched within H days, and **shrinks**
   it toward the universe average (a Bayesian prior, strength 8) so tiny bins can't
   scream "100%!".

Everything below is evaluated **leakage-safe**: each prediction excludes its own
episode (leave-one-transition-out), so the metrics estimate true generalization, not
memorization.

### 5.1 The numbers

Trained/evaluated on **one ticker** (MSFT, n=783 state-days) vs the **pooled
9-ticker universe** (n=4,765 state-days, 162 episodes):

| horizon | single-ticker AUC / MACE | **pooled AUC / MACE** | gate (AUC≥0.6, MACE≤0.10)? |
|---|---|---|---|
| 10d | 0.52 / 0.155 | **0.816 / 0.036** | single ✗ → **pooled ✓** |
| 20d | 0.43 / 0.314 | **0.779 / 0.048** | ✗ → **✓** |
| 40d | 0.46 / 0.330 | **0.762 / 0.077** | ✗ → **✓** |
| 60d | 0.47 / 0.181 | 0.738 / 0.109 | ✗ → ✗ (just misses on MACE) |

Pooling moved AUC from "coin flip" (~0.46) to "good" (0.74–0.82) and cut MACE from
~0.3 to <0.08 at 10/20/40d. The pooled `predicted_mean` also matches `observed_rate`
almost exactly (e.g. 40d: predicted 0.625 vs observed 0.620).

### 5.2 Why pooling raised AUC (discrimination)

This is a **sample-complexity** story — the heart of the ML lesson.

To *discriminate*, the model must output **different** probabilities for **different**
states. With one ticker, the fine-grained buckets (e.g. "10% below, 30 days in, deep
drawdown") almost never reach the 25-sample floor, so the scope ladder **falls back to
a near-universal, near-constant prediction**. Constant prediction ⇒ no ranking ⇒
**AUC ≈ 0.5** — exactly what we saw (0.43–0.52). It wasn't that MSFT is unpredictable;
it's that **one stock doesn't contain enough similar situations** to estimate
state-specific rates.

Pool nine tickers (n: 783 → 4,765; episodes 26 → 162) and those state-specific buckets
**fill up**. Now the estimator can say "deep-and-long looks like ~25% within 10d" but
"shallow-and-recent looks like ~55%," i.e. it **varies by state** → it ranks →
**AUC jumps to ~0.78.** More relevant data ⇒ finer conditioning ⇒ discrimination.
(This is also the bias–variance trade-off: small per-bucket samples were too **noisy**
to trust, so the estimator stayed coarse/biased; more data cut the variance and let it
use sharper, less-biased buckets.)

### 5.3 Why pooling cut MACE (calibration)

Two reasons. (i) **Bigger bins are less noisy:** an `observed_rate` computed from 300
neighbors wobbles far less around its true value than one from 20, so the reliability
points sit closer to the diagonal. (ii) **Conditioning reduces systematic bias:** when
the model could only predict a blended average, whole regions were systematically
over/under-stated (large gaps → MACE 0.3); state-specific predictions remove that bias.
Net: MACE 0.3 → <0.08.

### 5.4 Why we trust the lift is real (not overfitting)
- **Leakage-safe evaluation** (leave-one-transition-out): a day's own future never
  enters its neighbor pool, so the AUC/MACE estimate generalization.
- **External agreement:** the pooled numbers match an independent reference report on a
  different 8-ticker pool (10d AUC ≈ 0.80, 40d ≈ 0.745) — two roads, same place.
- **Shrinkage** guards the small bins that remain, so a lucky 3-of-3 bucket can't fake
  a confident probability.

---

## 6. What this means in practice (investing) — and its limits

> Reminder: descriptive, conditional **evidence**, not advice or a forecast. It says
> "states like today's, historically, retouched within H days about X% of the time."

**What good AUC buys you — prioritization.** AUC ≈ 0.78 means: given two below-yearline
setups, the model ranks the one that *actually* retouches sooner higher ~78% of the
time. That's a *relative* tool — which of today's candidates are "closer to repairing"
— useful for **watchlist ordering, monitoring cadence, and where to look first.** AUC
says nothing about the *level*, so it alone can't size anything.

**What good calibration (low MACE) buys you — usable numbers.** Calibration is what
lets a probability enter a **decision rule**:

- **Expected value / position sizing:** a payoff that depends on the event only has a
  meaningful expected value if `P` is true. With a 40% stated probability that really
  occurs ~40% of the time (MACE 0.077), `EV = P·win − (1−P)·loss` is trustworthy; with
  an uncalibrated 40% it's garbage-in-garbage-out. (Kelly-style sizing needs calibrated
  `P` even more — it's very sensitive to probability error.)
- **Option-style timing:** the horizons map to real expiries — **10d ≈ two weeks,
  20d ≈ a month, 40d ≈ two months.** A calibrated `P(retry ≤ H)` is exactly the kind of
  input an options/overlay researcher would want for choosing an expiry window. (In this
  system that's flagged research-only, `must_not_auto_execute`.)
- **Honest abstention:** the **60d** horizon *fails* the MACE gate (0.109 > 0.10), so
  the system **does not** present it as trustworthy. "We don't know well enough yet at
  2–3 months" is a feature — a calibrated system that abstains beats a confident one
  that's wrong.

**Hard limits to keep front-of-mind.** It's conditional on "today's drawdown is the
worst it gets"; it's descriptive history (regime changes, survivorship of today's
mega-caps, only 9 names); a retouch is not a profit (it's a *level being reached*); and
none of this models transaction costs, slippage, or your risk constraints. AUC 0.78 is
"useful tilt," not "edge you can bet the farm on."

---

## 7. Future directions — how to push AUC & MACE further

**Get the 60d gate (and everything) over the line, honestly:**
- **Cross-validated / nested isotonic recalibration.** We fit an isotonic map but its
  *in-sample* error is optimistic (≈0 by construction). Fit it on out-of-fold folds and
  measure on held-out folds → an **honest** calibrated MACE that could legitimately
  pull 60d under 0.10. (Use **Platt scaling** when data is thin — fewer parameters,
  smoother.)
- **More and longer data.** §5 says discrimination is sample-limited. More tickers
  (sectors beyond mega-cap tech), more history, and higher-quality labels fill the
  state buckets → higher AUC and lower MACE, especially at 60d.

**Better signal (raise the AUC ceiling):**
- **Richer features** with proper leakage controls: relative strength vs the sector
  ETF, volatility regime, breadth/macro context, distance-velocity (is it climbing?).
- **Proper survival models with time-varying covariates** (Cox / discrete-time hazard
  done right) instead of a frozen-state forward projection — the conditioning that's
  done empirically here could be learned.
- **Hierarchical / partial-pooling Bayes:** we shrink to one universe prior; a
  multi-level model would shrink each ticker toward its *peer group* toward the
  universe — usually better than all-or-nothing pooling.

**Better evaluation & guarantees:**
- **Purged + embargoed time-series CV** to be airtight about temporal leakage.
- **Conformal prediction** to turn point probabilities into **calibrated prediction
  intervals** with finite-sample coverage guarantees ("retouch within 18–46 days, 80%
  coverage").
- **PR-AUC** and **decision-curve / net-benefit** analysis when you care about a
  specific operating threshold, not the whole ROC.
- **Drift monitoring:** recompute reliability over time; recalibrate when MACE creeps —
  calibration is not "set once."

---

## 8. Exercises

1. **AUC by hand.** Scores/labels: (0.8,1),(0.7,1),(0.6,0),(0.4,1),(0.2,0). Count the
   positive>negative pairs (6 pairs). What's the AUC? What does it say vs 0.5?
2. **Reliability + MACE.** You bin a model into [0.2,0.3]→(pred 0.25, obs 0.40, n=50),
   [0.5,0.6]→(0.55, 0.52, n=80), [0.8,0.9]→(0.85, 0.70, n=15). Compute MACE and ECE.
   Which bin is over-confident? Is any bin "thin"?
3. **Spot the quadrant.** A model outputs 0.62 for *every* state and the base rate is
   0.62. What is its MACE? Its AUC? Which box in §4 — and would you ship it?
4. **Why pooling.** In one paragraph, explain to a friend why the *same* estimator had
   AUC 0.46 on one ticker and 0.78 on nine. Use the words "buckets," "samples," and
   "scope ladder."
5. **Monotone invariance.** Apply `p → p²` (monotone on [0,1]) to a set of scores.
   Argue why AUC is unchanged but Brier/MACE generally change.
6. **Design a study.** You want the 60d gate to pass. Propose a *leakage-safe* plan
   (data, features, recalibration, evaluation) and state what metric you'd report.

---

## 9. Key takeaways

```
• A probability model must pass TWO tests: discrimination (AUC) and calibration (MACE).
• AUC = P(a random positive outranks a random negative); threshold-free; monotone-invariant.
• Calibration = "0.4 means 0.4"; read it off a reliability diagram; summarize with MACE/ECE; Brier mixes both.
• They're independent: a base-rate predictor is calibrated yet AUC≈0.5 (useless).
• Pooling raised AUC (more samples ⇒ finer state-conditioning ⇒ ranking) AND cut MACE (bigger, less-biased bins).
• In practice: AUC ⇒ prioritization/ranking; calibration ⇒ usable probabilities for EV/sizing/timing; abstain when the gate fails.
• Push further with honest (CV) recalibration, more data, richer features, survival models, conformal intervals, drift monitoring.
```

The deepest lesson: **more relevant data didn't just make the model "more accurate" —
it changed what the model could do.** With one ticker it could only state an average
(no ranking); with nine it could condition on the situation (real discrimination *and*
better calibration). Sample size is not a footnote — it sets the ceiling on what any
estimator can learn.

---

## Glossary

- **Discrimination** — ability to rank events above non-events (AUC).
- **Calibration** — predicted probabilities match observed frequencies.
- **ROC curve** — TPR vs FPR as the decision threshold sweeps; AUC is its area.
- **TPR / FPR** — true-positive rate (recall) / false-positive rate (1 − specificity).
- **AUC** — area under ROC = P(random positive scored above random negative); 0.5=chance.
- **PR-AUC** — area under the precision–recall curve; better than ROC-AUC under heavy imbalance.
- **Reliability diagram** — observed rate vs predicted probability, by bin; diagonal = perfect.
- **MACE** — mean (over usable bins) of |observed − predicted|; calibration error in prob. units.
- **ECE** — sample-weighted version of the same gap.
- **Brier score** — mean squared error of probabilities; proper score; = reliability − resolution + uncertainty.
- **Isotonic regression / Platt scaling** — monotone recalibration maps (raw score → frequency); fix calibration, not AUC.
- **Leave-one-transition-out / purged CV** — leakage-safe evaluation that holds out a whole correlated episode.
- **Shrinkage (Bayesian prior)** — pulling a small-sample estimate toward a prior so tiny bins aren't over-confident.
- **Conformal prediction** — wraps a model in finite-sample coverage-guaranteed intervals.

## Further reading (find these by title)
- Hanley & McNeil (1982), *The meaning and use of the area under an ROC curve.*
- Fawcett (2006), *An introduction to ROC analysis.*
- Niculescu-Mizil & Caruana (2005), *Predicting good probabilities with supervised learning.*
- Guo et al. (2017), *On calibration of modern neural networks* (ECE, temperature scaling).
- Platt (1999) and Zadrozny & Elkan (2002) — Platt scaling / isotonic calibration.
- Murphy (1973), *A new vector partition of the probability score* (Brier decomposition).
- Angelopoulos & Bates (2021), *A gentle introduction to conformal prediction.*

---

*Running example: the V13 yearline engine's `P(retry ≤ H)` (`src/yearline_universe/`
`hazard.py` empirical estimator, `calibration.py` metrics + gate). Numbers from
`docs/phased_design/phase_04/` and `phase_05/`. Educational material — finance content
is incidental and is not investment advice.*
