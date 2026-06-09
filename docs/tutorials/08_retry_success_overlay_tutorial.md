# 08 — Retry **success**: building a trustworthy "will the attempt hold?" overlay (Phase 8)

**Audience:** ML engineer / quantitatively-minded developer. **Running example:** this engine's Phase 8
(RS-1 → RS-4). **Companion code:** `success_labels.py`, `success_models.py`, `success_calibration.py`,
`success_reliability.py`, `success_surface.py`, `docs/phased_design/phase_08/`.

> Educational research only. Nothing here is investment advice; the engine emits a research overlay and
> must not auto-execute.

This tutorial teaches a complete, honest method for adding a **second, harder** probability on top of a
working one — *given* an event happens, will it *succeed* — and only surfacing it once it's earned trust.
The lessons generalize far beyond finance: **separate the two questions, set an honest bar, find
discrimination, fix calibration with a blend, measure what your calibration is actually made of, and ship
it gated.**

---

## 0. The two questions (don't conflate them)

The engine already answers retry **occurrence**: *will price retry the yearline (MA250) within H days?* —
a mature, calibrated, gated estimator (Phases 3–7). Phase 8 asks the **conditional** question:

> **Given an attempt at the yearline, will it reclaim and _hold_ (vs. get rejected)?** — `P(success │ retry)`

These are different events with different label economics:

| | occurrence `P(retry ≤ H)` | success `P(success │ retry)` |
|---|---|---|
| unit of observation | a **day** at risk | an **attempt** (much scarcer) |
| label | did a touch happen within H? | did the touch confirm + hold ≥70% over the hold window? |
| sample size (this universe) | thousands of rows | **162** completed attempts / 59 episodes / 9 tickers |
| canonical estimator | empirical completed-path (Phase 3) + blend (Phase 7) | **the subject of Phase 8** |

The binding constraint is sample size: success is labelled per attempt, so the floor is lower and every
modeling choice has to be more conservative.

**Label definition (leakage-safe).** The success label is the attempt's realized outcome
(`classify_attempt_outcome_v10_parity`): close > MA250 for `confirm_days`, then hold ≥70% over
`success_hold_days`. Unresolved attempts (`next_attempt_pending`) are **excluded** (censoring), never
guessed.

---

## 1. RS-1 — the empirical baseline, and the honest negative

**Lesson: set the bar with a calibrated baseline before you reach for a model.** RS-1 builds the same
"borrow strength from similar history" estimator the occurrence side uses (`success_labels.py`): bucket
each attempt by recovery state (drawdown depth, below-MA250 depth, attempt #, peer group, transition),
walk a **scope ladder** (group×transition×drawdown → … → universe) to the first scope with ≥15 attempts,
and **shrink** that bucket's success rate toward the universe prior (Bayesian, strength 6).

The result is a deliberately **honest negative**: the empirical static-bucket estimator scores
**AUC ≈ 0.49** — *no* out-of-sample discrimination. The static recovery-state buckets don't tell you which
attempts will hold. That's not a failure; it's the **bar**: anything we add must beat a calibrated coin.

> Engineering takeaway: a baseline that *can't* discriminate but *is* calibrated is the right thing to
> measure against — it tells you whether new signal is real, and it's a safe fallback.

---

## 2. RS-2 — a classifier finds discrimination (but is over-confident)

**Lesson: discrimination and calibration are different axes — win the right one first.** RS-2
(`success_models.py`) fits an L2 logistic on the **readiness** features that lifted the occurrence problem
— path dynamics (returns, distance-to-MA slopes, vol) + cross-sectional regime — all computed
leakage-safe *at the attempt's touch date*.

Validated with **episode-purged GroupKFold + leave-one-ticker-out** (the unseen-name test), it posts
**AUC ≈ 0.71** — genuine discrimination, beating the RS-1 baseline and the base rate. **But** its
calibration is off: **MACE ≈ 0.128 > the 0.10 gate** — it's over-confident (predicts 0.87 where reality is
0.69). So: *it ranks well, but its probabilities can't be shown as-is.*

> The same lesson as Phase 7: **a good ranker is not a good probability.** Don't surface it yet.

---

## 3. RS-3 — the blend clears the gate (the Phase-7 lever, reproduced)

**Lesson: blend a discriminating-but-over-confident ranker with a calibrated-but-flat estimator.**
`success_calibration.py` forms a convex blend `w·classifier + (1−w)·empirical`, picks `w` by out-of-fold
Brier, and applies the **trust gate** (AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50) on honest leave-one-ticker-out OOF
predictions. The blend (w = 0.5) **passes**: **AUC 0.702, MACE 0.036.** It keeps essentially all of the
classifier's ranking and is well-calibrated — because the two components err oppositely (the classifier is
over-confident; the empirical anchor is nearly flat), and averaging tempers the spread.

Isotonic recalibration *alone* just missed (MACE 0.103); isotonic *on top of* the blend made it worse (the
blend is already calibrated — don't over-process). So the **raw blend** is the recommended surface.

---

## 4. RS-3 reliability — *what is that 0.036 actually made of?*

**Lesson: a low calibration error is necessary, not sufficient — measure whether it's _sharpness_ or
_shrinkage_.** `success_reliability.py` decomposes the blend's calibration win on the same OOF surfaces:

- **Brier/Murphy decomposition** (`reliability − resolution + uncertainty`) — separates calibration from
  *informative sharpness* (resolution).
- **Variance-shrinkage index** `1 − var(blend)/var(raw)` — how far the blend collapsed toward center.
- **Pure-shrinkage counterfactual** — shrink the raw classifier toward the base rate by the *same*
  variance factor, using **no** empirical information; compare its MACE to the blend's.

The verdict on real data: **~87% of the calibration win is base-rate shrinkage**, only ~13% is genuine
empirical information (variance-shrinkage index 0.724; resolution falls 0.041 → 0.026). The blend is
**calibrated-by-shrinkage**: honest and safe for sizing, but *not a sharper model* — it never predicts far
from the base rate.

> The product consequence (carried into RS-4's caveats): **trust the ranking, size gently on the level.**
> The real upgrade lever is a sharper *raw* classifier (better features / more episodes), not more
> calibration tuning — re-tuning the weight can't recover more than the ~0.012 the anchor's information is
> worth. (See `phase_08/reliability/`.)

---

## 5. RS-4 — surface it, gated and additive

**Lesson: capability-before-consumer — wire the trustworthy surface in opt-in, additive, and gated.**
`success_surface.py` mirrors Phase-7's occurrence-blend wiring exactly:

- **compute-once** model (`build_success_surface_model`): the RS-3 blend weight + gate + a classifier
  fitted on all completed attempts for live scoring;
- **cheap live apply** (`apply_success_live`): the gated blend `P(success │ retry)` for the current state;
- **the block** (`build_retry_success_context`): a top-level `retry_success_context` (the success analog of
  `retry_hazard_context`).

Three rules keep it safe:
1. **Opt-in** (`surface_success=True`), pooled-only. **Default off ⇒ the envelope is byte-identical** (the
   key is simply absent).
2. **Single probability**, not horizon-indexed — success is "given an attempt, does it hold?"
3. **Empirical/occurrence estimators stay canonical** — the overlay never overwrites them.

### The composite — *blend × blend*

RS-4 also surfaces the headline business quantity:

$$P(\text{reclaim} \le H) = P(\text{retry} \le H)\times P(\text{success}\mid\text{retry})$$

Both factors are **gate-passing blends** (success-side RS-3 blend × occurrence-side Phase-7 blend) — never
raw classifiers. A horizon's composite is **surfaced only where _both_ gates pass** (the **dual gate**);
otherwise the product is retained but labelled diagnostic (`surfaced_probability: null`).

### Why the occurrence factor uses the Phase-7 blend (and 60d is *surfaced*)

The occurrence side has two calibrated surfaces. The pre-Phase-7 **isotonic-only** calibration **fails the
gate at 60d** (MACE 0.130 — long-horizon saturation + per-step compounding). Phase 7's
classifier↔empirical blend already fixed that: it **passes at every horizon** (60d MACE 0.058) with higher
AUC throughout. So RS-4 composes against the **blend's** occurrence probability where it passes — and the
60d composite is **surfaced, not withheld**. (Full detail: `phase_08/rs4_composite_blend_times_blend.md`.)

> Meta-lesson: **one source of truth.** RS-4 doesn't re-decide weights or gates; it reads the surfaces the
> earlier phases validated, so it inherits any future improvement automatically.

---

## 6. The transferable checklist

1. **Separate the questions.** Occurrence ≠ success; label economics differ. Name the conditional event
   precisely and label it leakage-safe (exclude censored cases — don't impute them).
2. **Baseline first.** A calibrated, non-discriminating empirical baseline *is* the bar.
3. **Win discrimination, then fix calibration.** A ranker (AUC) and a probability (MACE) are different
   deliverables; a monotone/convex fix can add calibration but not discrimination.
4. **Blend opposite errors.** Over-confident ranker + flat-but-calibrated estimator → calibrated blend.
5. **Audit the calibration.** Decompose it — is the low MACE *resolution* or *shrinkage*? Say so honestly.
6. **Gate before you surface.** AUC + MACE + n, on the deployment-relevant CV (leave-one-ticker-out).
7. **Ship opt-in / additive / gated**, default byte-identical, with a single source of truth upstream.
8. **Compose trustworthy surfaces, not raw ones** — and surface a composite only where *every* input is
   gate-verified.

**Next:** `09_rs4_success_composite_walkthrough.md` runs one real state (MSFT) through all of this, number
by number.
