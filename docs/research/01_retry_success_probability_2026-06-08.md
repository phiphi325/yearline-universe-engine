# Estimating the probability that a yearline retry *succeeds* — current state, how to improve, how to validate

*Research note. 2026-06-08. Educational research only — not financial advice; the engine emits
evidence context, never trades.*

---

## 0. The question, and the distinction that matters

> *"How do I use this tool to tell the probability of a retry being **successful** when it happens —
> and how do I improve and validate that, given it's still statistical-based today?"*

There are **two different probabilities** in this engine, and they must not be conflated:

| | What it answers | Status today |
|---|---|---|
| **Retry *occurrence*** — `P(retry ≤ H)` | *Will price retouch the MA250/yearline within H trading days?* (timing) | **Mature**: empirical completed-path estimator + Phase-7 classifier blend; **calibrated + trust-gated** at ≤40d; surfaced in `retry_hazard_context`. |
| **Retry *success*** — `P(success │ retry)` | *Given an attempt at the yearline, will it **reclaim and hold** (vs get rejected)?* (quality) | **Prototype, statistical, NOT surfaced**: `fit_retry_quality_classifier`, gated off, uncalibrated. |

Your recollection is correct: the **success** probability is still a prototype. This note is about
that second number — what "success" means here, how the tool produces it today, and the concrete path
to make it trustworthy and validated.

## 1. What "retry success" means in this engine (the label)

Success is **not** "did it touch the line" — it is "did the attempt *hold above* it." The canonical
definition lives in `event_detection.classify_attempt_outcome_v10_parity` (V10-parity state machine):

- **success** — price closes above `MA250` for `confirm_days` consecutive days, **and** then holds
  (Close > MA250 on ≥ 70% of days) over the next `success_hold_days`.
- **fail** — price falls back below `MA250` for `new_attempt_gap` days before confirming (rejected),
  **or** never resolves within `max_scan_days` (pending → treated as fail at scan end).
- **pending** — not yet resolved (no realised outcome).

That `outcome` (and the derived `next_attempt_success` on the recovery table) is the **label** any
success estimator must predict. It is a *realised forward outcome*: known only for **completed**
attempts, unknown for **pending/live** ones — so, exactly like the occurrence labels, **censoring must
be leakage-safe** (label only attempts whose outcome is actually observed; never label a live attempt).

## 2. How the tool answers it *today* (the honest current state)

`hazard.fit_retry_quality_classifier` — a port of V12 Module C — fits a **logistic regression** on
`next_attempt_success` and returns `p_next_retry_success` + a `quality_bucket`
(low / low_to_medium / medium / high). But note every guardrail on it:

- **Gated OFF by default.** It only runs when `run_*_pipeline(..., fit_ml_models=True)`; the default
  pipeline skips it (it is not consumed by the envelope). When run, it lands in
  `manifest["ml_models"]` / the hazard layer's `quality_prediction`, **not** in
  `SingleTickerStatisticalContextEnvelope`.
- **Explicitly labelled `prototype_uncalibrated`**, with a `model_warning` that the probability is not
  production-calibrated.
- **Suppressed on thin data** — returns `suppressed_insufficient_labels` if `< 30` labelled attempts or
  only one class is present.
- **Static features only** — drawdown-so-far, below-MA250 depth, attempt number, overshoots, + a few
  categoricals (transition, peer group, source quality). It does **not** see the path-dynamic /
  cross-sectional "readiness" features added in Phase 7.

**What the benchmark says about it.** The V12.10 report (8-ticker, target `2→3`): success **base rate
≈ 0.354**, classifier `p(success) ≈ 0.321`, leave-one-transition-out **Brier 0.223 vs 0.231** baseline
and **log-loss 0.642 vs 0.654**. Translation: it *barely* beats "just predict the base rate" — weak
discrimination, uncalibrated. That is why it is a labelled prototype and is **not surfaced**.

**How to pull the number today (for research only):**

```python
res = run_ticker_pipeline(uni.get_ticker("MSFT"), uni, cache_dir="data/price_cache",
                          fit_ml_models=True)          # opt-in; prototype
ml = res.manifest["ml_models"]                          # quality_prediction lives here
# treat p_next_retry_success as a prototype, NOT a trustworthy probability
```

So today the tool *can* compute a retry-success probability, but it is **not validated and not
trustworthy** — use it only as exploratory context.

## 3. Why this is hard (the binding constraints)

1. **The sample is *attempts*, not rows — even scarcer than the occurrence problem.** Occurrence labels
   are per at-risk *day* (4,765 pooled rows over ~162 transitions). Success labels are per **completed
   attempt with a known outcome** — on the order of *tens* single-ticker, *low hundreds* pooled. The
   effective sample is tiny, which caps model complexity and stability of any metric.
2. **Base-rate dominance + imbalance.** With ~35% success, a naive "predict 0.35" is hard to beat; the
   bar is *lift over the base rate*, not raw accuracy.
3. **Censoring.** Pending/live attempts have no outcome; including them naively leaks or biases.
4. **Path/regime dependence.** A high-readiness reclaim (bouncing hard, gap closing, falling vol) is a
   very different animal from a weak poke at the line in high vol — but the current classifier is blind
   to that (static features only).

## 4. How to improve it — apply the playbook that already worked for *occurrence*

The occurrence probability went from "single-ticker AUC ≈ 0.46, gate fails" to "pooled AUC 0.74–0.82 +
gated + a classifier blend" across Phases 3–7. **The same moves apply to success**, in priority order:

- **(a) Rigorous, leakage-safe labels.** Use the canonical `outcome` (§1); label **only completed
  attempts**; exclude pending; define the attempt-level row keyed by `transition_key`/attempt.
- **(b) Pool the universe (the single biggest lever — Phase 5 lesson).** Single-ticker success data is
  hopeless; pool the universe so state-conditioned success rates have enough labelled attempts to
  discriminate. This is what made the occurrence estimator pass its gate.
- **(c) Better features (Phase 7 PR-A/D).** Feed the **readiness** signals the static classifier lacks:
  `close_position_in_repair_range`, `bounce_from_low_pct`, `reclaim_from_low_speed`, distance-to-MA250
  *slope*, vol level/percentile, **and** the cross-sectional regime (market/sector proxy, breadth,
  peer-relative strength). A reclaim *with the sector* is likelier to hold than one against it.
- **(d) An empirical base-rate-by-bucket success estimator (the Phase-3 analog).** Before any model:
  "of similar historical attempts (bucketed by distance/drawdown/readiness/regime), what fraction
  **succeeded**?" — with the same scope-ladder + Bayesian shrinkage. This is the *calibrated baseline*
  and the honest thing to beat.
- **(e) Direct classifier + blend (Phase 7 PR-C/E).** A **regularized logistic** (primary, low-variance)
  on the richer features; GBM diagnostic-only; then **blend** the classifier (ranker) with the
  empirical base-rate estimator (calibrated) per the Phase-7 result that the blend beats either alone.
- **(f) Partial pooling across sectors** (see `docs/multi-sector/`) for differing success base rates by
  sector — shrink thin sectors toward the parent.

Stay **conservative** (tiny sample): regularized-linear primary, abstain by default, never surface an
un-gated number.

## 5. How to validate it — the trust discipline (the crux of "moving forward")

Validation, not a single in-sample number, is what makes it trustworthy. Reuse the Phase-4/6/7 harness:

- **Out-of-fold, leakage-safe CV.** Purge by `transition_key`/attempt (an attempt is wholly in train or
  test), **and** the harder **leave-one-*ticker*-out** test — does it predict success for a name it has
  never trained on? (Phase-7 `generalization.py`.)
- **Metrics that matter for a probability:**
  - **AUC** (discrimination — can it rank winners above losers?),
  - **Brier + log-loss** (proper scoring),
  - **MACE / ECE + reliability slope** (calibration — does "0.6" mean 60%?),
  - **Lift over the success base rate** (the honest bar — it must beat predicting ≈0.35 flat; today it
    *barely* does).
- **The trust gate + honest abstention.** Same gate as the occurrence estimator
  (AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50, out-of-fold). **Surface the success probability only where it
  passes.** On *current* data it will very likely **fail** (too few attempts) — and saying "not yet" is
  the correct, honest output, exactly as the occurrence gate did pre-pooling.
- **Walk-forward backtest (V12.6 style).** Out-of-sample, across time/regimes: do predicted `p(success)`
  deciles match realised success rates? Does it stay calibrated as regimes drift?
- **Beat the baseline, explicitly.** Always report the model **vs the empirical base-rate-by-bucket
  estimator** head-to-head (the Phase-7 pattern) — promote only if it wins on AUC without MACE regression.
- **Data is the lever.** The dominant constraint is *labelled attempts*. The single most effective
  improvement is **more data** — a wider / multi-sector universe and deeper history (see
  `docs/multi-sector/`). Method changes help at the margin; data changes move the gate.

## 6. A concrete phased plan (mirrors the engine's PR discipline)

| PR | Deliverable | Acceptance |
|---|---|---|
| **RS-1** | Attempt-level success dataset (leakage-safe labels) + an **empirical base-rate-by-bucket success estimator** (pooled, scope-ladder + shrinkage). | Capability-before-consumer; measured vs the flat base rate; not surfaced. |
| **RS-2** | Direct **success classifier** (regularized logistic + readiness/cross-sectional features; GBM diagnostic) + **attempt-aware & leave-one-ticker-out** CV; head-to-head vs RS-1. | Report AUC / Brier / MACE / **lift over base rate**; honest negatives. |
| **RS-3** | Calibration (isotonic, purged OOF) + **trust gate** + classifier↔empirical **blend**; abstain until gated. | Gate likely **fails** on current data → ship the validated *method* + an honest "not yet" + the data ask. |
| **RS-4** | Surface (opt-in, additive, gated) a `retry_success_context` block — the success analog of `retry_hazard_context`; never overwrites; only where gated. | Default envelope byte-identical; output-changing → reviewed (Phase-3/7 discipline). |

## 7. The genuinely useful composite (occurrence × success)

For an options overlay the actionable quantity is **not** either probability alone but their
**product**: `P(successful reclaim within H) = P(retry ≤ H) × P(success │ retry)`. Surface that
composite **only where both gates pass**; otherwise present each as context with its own trust state.
That product — *"how likely is MSFT to retouch its yearline within H days **and** actually hold it"* —
is the number a long-term holder's collar/overlay decision actually wants.

## 8. Bottom line

- Today's retry-**success** probability (`fit_retry_quality_classifier`) is a **gated-off, uncalibrated
  prototype** that barely beats the base rate — correctly **not surfaced**.
- To improve it, run the **exact playbook that fixed the occurrence probability**: leakage-safe labels →
  **pool the universe** → **readiness + cross-sectional features** → an **empirical base-rate baseline** →
  a **regularized classifier blended** with it → **calibrate + gate + abstain**.
- To validate it: **out-of-fold, leave-one-ticker-out, lift-over-base-rate, calibration + the trust
  gate, and a walk-forward backtest** — and **surface only where the gate passes**.
- The honest expectation: on current data the gate will likely say **"not yet"** — and the most
  effective lever is **more labelled attempts (a wider/multi-sector universe + deeper history)**, not a
  fancier model.
