# Replacing a Mis-Specified Model Output with an Empirical Estimator — A Tutorial for Junior Engineers

**When a model is asked a question it was never built to answer, the fix is usually
not to tune the model — it's to replace the *quantity* with a better-posed estimator.
Taught through this engine's "P40 fix" (Phase 3): how a saturating hazard *forecast*
was demoted to a diagnostic and the canonical "probability of a retouch within H days"
became an empirical, data-driven estimate.**

By the end you will understand:

- How to tell when a **model is mis-specified for an extrapolation task** (and why a
  perfectly-trained model can still produce nonsense when you project it forward).
- The principle **"separate the instantaneous question from the horizon question"** —
  use a model for what it's good at, and a different estimator for what it isn't.
- The **empirical "borrow strength from similar history"** pattern: bucketing, a
  **hierarchical scope fallback**, and **Bayesian shrinkage** to a prior.
- **Keep the old computation as a labelled *diagnostic*** instead of deleting it.
- **Provenance** as a first-class output (`reference_scope`, `reference_n`).
- The difference between an **output-preserving** change and an **output-changing**
  one — and why the second needs a *gated before/after review*.
- How to thread a behaviour change cleanly through `model → replay → envelope` and
  invalidate stale caches.

> Companion to `performance_optimization_tutorial.md` (finding a bottleneck) and
> `optional_computation_feature_flags_tutorial.md` (designing a switch). Those changed
> *speed*. **This one changes *results*** — a different, riskier category, handled
> deliberately. No finance background needed; the engineering ideas are the point.

---

## Part 1 — The story (a probability that was "certain" and unstable at once)

The engine emits a per-ticker JSON envelope. One block, `retry_hazard_context`,
answers: *"what's the probability the price retouches its 250-day average within
10 / 20 / 40 / 60 / 90 days?"* (call these P10…P90 — see **Appendix A** for the exact
definition, and why these are *not* the P10/P50/P90 *percentiles* some readers expect).

The original implementation built those from a **discrete-time logistic hazard model**
projected forward under a **"state-hold-forward"** scenario: freeze today's features,
march time forward one day at a time, accumulate the daily hazard into a cumulative
probability. For our live example (MSFT, ~10% below its yearline) it produced:

```text
P10 = 0.001   P20 = 0.010   P40 = 0.286   P60 = 1.000   P90 = 1.000
```

Two things are wrong here, and they point in *opposite* directions:

1. **P60 = P90 = 1.000.** The model says a retouch within 60 days is *certain* — for
   essentially any below-yearline state. That's obviously false.
2. **P40 = 0.286 was also unstable.** Refit the model on a different pool of tickers
   and the same P40 jumped around wildly: **0.002** (8-ticker fit) / **0.30**
   (MSFT-only) / **0.51** (MSFT+AAPL). A number that swings 250× with the training
   pool is not a number you can surface.

So the headline probability was simultaneously **over-confident** (saturates to 1.0)
and **unstable** (P40 hypersensitive). That's the bug Phase 3 fixes.

> The deep dive on *why* lives in `docs/V13_data_and_report_analysis.md` §2.2–§2.3.
> This tutorial teaches the *engineering decision* and the *pattern* that fixed it.

---

## Part 2 — The diagnosis: a well-trained model, asked the wrong question

Here's the trap, and it's a common one for juniors: **the model wasn't broken — the
way we used it was.**

A discrete-time hazard model estimates a *one-day* conditional probability:
"given today's state, what's the chance the event happens *today*?" That's a fine,
well-calibrated thing (our walk-forward report confirms it; see §2.3 of the analysis
doc). The trouble starts when you turn that one-day model into a *multi-day forecast*
by **freezing the state and extrapolating**:

```text
logit(hazard on future day h) = base_logit + h × (slope on the time-since-touch features)
```

Because everything except the day-counters is held constant, the future-day logit is
**linear in h**. A positive time slope means the daily hazard eventually races toward
1, so the cumulative probability **steps to 1.0** — every time, for any state that
hasn't retouched yet. Worse, the model freezes `distance_to_ma250` at today's value,
which **contradicts the event's own definition** (a "retouch" *is* distance → 0). You
asked the model: *"what's P(retouch) if the price stays 10% below the line forever?"*
— and then acted surprised that the answer was incoherent.

Two lessons already:

- **A calibrated model can still be mis-specified for a task.** "Calibrated on
  historical one-day hazards" ≠ "valid as a frozen-state multi-day forecast."
- **Check the question against the definition.** If the scenario you simulate
  contradicts the event you're predicting, the output is meaningless no matter how
  good the fit is.

### The decision point

You have two roads:

| Road | What it means | Verdict here |
|---|---|---|
| **Re-specify the model** | drop collinear features, standardise, regularise, invent a "glide path" for the frozen feature | treats a *symptom*; still a frozen-state extrapolation; lots of knobs |
| **Replace the quantity** | keep the model for the one-day hazard; estimate the *horizon* probability a different, better-posed way | ✅ what we (and the source research) did |

When a quantity is **ill-posed**, tuning the estimator rarely rescues it. Step back and
ask whether a *different kind of estimate* answers the question more honestly.

---

## Part 3 — The principle: separate "instantaneous" from "horizon"

The fix splits one conflated output into two, each produced by the right tool:

```text
hazard_today      → KEEP the logistic model   (it's good at one-day conditional hazard)
P(retry ≤ H days) → EMPIRICAL completed-path estimate   (no frozen extrapolation)
```

The empirical idea is almost embarrassingly simple, and that's the point:

> *Instead of asking "what does my model extrapolate for a frozen −10% state," ask
> "historically, when the stock was in a **similar** state, how often did it actually
> retouch within H days?"*

That's a **non-parametric, data-driven** estimate. It cannot saturate to 1.0 unless
the data really did retouch every time; it doesn't extrapolate a frozen feature; and
it degrades gracefully (you can always widen "similar"). This is the same family as
k-nearest-neighbours and the conditional-timing estimators from Phase 2 — "borrow
strength from comparable history" rather than "trust a parametric extrapolation."

---

## Part 4 — The empirical completed-path estimator (the core idea)

Three ingredients. Read them as a reusable recipe, not finance.

### 4.1 Build the reference: "how long was left, from each historical day?"

Take every **completed** episode in history. For each *day* inside it, label how many
trading days *remained* until the event actually happened:

```python
# remaining_trading_days_to_retry = event_day − this_day   (per completed transition)
event_days = d[d.event_retry_today == 1].groupby("transition_key")["trading_days_since_touch"].max()
d = d.merge(event_days.rename("event_trading_day"), on="transition_key")
d["remaining_trading_days_to_retry"] = d["event_trading_day"] - d["trading_days_since_touch"]
d = d[d["remaining_trading_days_to_retry"] >= 0]
```

Now "P(retry ≤ H)" for a state is just: *of the reference rows similar to this state,
what fraction had `remaining ≤ H`?* No model, no extrapolation — a frequency.

### 4.2 Define "similar": bucket the state

Continuous features make exact matches impossible, so we **bucket** them:

```python
days_since_touch_bucket   # 0–5, 6–10, 11–20, 21–40, 41–60, 61–90, 91–120, 121+
distance_to_ma250_bucket   # <−20, −20..−15, … , −2.5..0, 0..2.5, >2.5  (%)
drawdown_so_far_bucket     # 0–3, 3–5, 5–8, 8–12, 12–20, 20+  (%)
```

Bucketing is a judgment call: too fine ⇒ empty buckets; too coarse ⇒ you condition on
nothing. Pick edges that match how the domain actually clusters.

### 4.3 Borrow strength: a hierarchical scope ladder

The most specific bucket (this ticker + this transition + this exact state) is the most
relevant — but it's often **too thin** to trust. So we walk a ladder from specific to
general and **stop at the first scope with enough rows** (≥ 25):

```text
ticker + transition + quality + (days × distance × drawdown)     ← most specific
ticker + transition + (days × distance)
group  + transition + (days × distance)
universe + transition + (days × distance)
group  + (days × distance)
universe + (days × distance)
group  + transition
universe + transition
all completed transitions                                         ← always succeeds
```

```python
for scope, cols in LADDER:
    sample = ref[(ref[cols] == row[cols]).all(axis=1)]
    if len(sample) >= MIN_REFERENCE_N or scope == "all_completed_transitions":
        return sample, scope          # first scope that clears the bar wins
```

This is the **specificity-vs-sample-size tradeoff** made explicit. Crucially, we
**return the scope we landed on** — the caller learns whether the estimate was
state-conditioned or had to fall back (see Part 4.5).

### 4.4 Don't trust a tiny sample raw: shrink toward a prior

Even at ≥ 25 rows, a raw fraction is noisy. We **shrink** it toward the universe-wide
rate with a Beta-style prior of strength `S = 8`:

```python
p = (k + S * prior_rate) / (n + S)     # k successes in n rows; prior_rate = universe rate
```

Read it as "start from the universe average, then let `n` real observations pull you
toward the sample fraction." Small `n` ⇒ stay near the prior; large `n` ⇒ trust the
data. This is the antidote to a 3-row bucket screaming "100%!".

### 4.5 Make the sample auditable

The estimate ships with its provenance, so a human can judge it:

```json
"p_retry_within_40d": 0.781,
"p_retry_within_40d_reference_scope": "group_transition",
"p_retry_within_40d_reference_n": 239,
"probability_policy": "v13_empirical_horizon_calibrated"
```

If `reference_scope` is a *general* level (like `group_transition`), you know the
estimate is **not** strongly state-conditioned — it fell back because the specific
buckets were too thin. That's honest, and it tells you exactly what more data would buy
(see the Phase 5 note below).

---

## Part 5 — Engineering it (the mechanics that matter)

### 5.1 Keep the old computation — as a *diagnostic*, not a deletion

We did **not** delete the saturating curve. We **demoted** it: every place it appeared,
it's preserved under an explicit `*_model_state_hold_forward_diagnostic` name, and the
**empirical** value takes the canonical slot.

```python
rr["p_retry_within_40d"] = empirical[40]["cumulative_retry_probability"]     # canonical
rr["p_retry_within_40d_model_state_hold_forward_diagnostic"] = model_curve[40]  # kept, labelled
```

Why keep it? (a) It's a real diagnostic — comparing canonical vs diagnostic is how you
*show* the saturation is gone. (b) Deleting outputs others might depend on is a
breaking change you don't need to make. **Demote, don't delete.**

### 5.2 Build the reference once, score many rows

The historical replay scores hundreds of as-of days. Re-deriving + re-bucketing the
reference per day would be wasteful, so we build (and bucket) it **once** and reuse it:

```python
horizon_reference = build_empirical_horizon_reference(hazard_fit.train_panel)  # once
for row in replay_rows:
    emp = empirical_horizon_probabilities_for_row(row, horizon_reference, HORIZONS)
```

(Inside, the estimator skips re-bucketing when the reference already carries bucket
columns — a cheap guard that matters inside a hot loop. This is the
`performance_optimization_tutorial.md` lesson applied in passing.)

### 5.3 Thread the change through every layer that surfaces the quantity

The canonical probability appears in three places; all three had to switch together, or
the engine would contradict itself:

```text
hazard.py        run_hazard_layer  → hazard_context.horizon_probabilities = empirical
                                     + diagnostic_model_state_hold_forward (kept)
replay.py        per as-of day     → p_retry_within_{h}d = empirical (canonical)
                                     + *_model_state_hold_forward_diagnostic (kept)
                                     → mode_state now derives from EMPIRICAL P60, not the step
context_export   retry_hazard_context → canonical + policy tag + reference_scope/n + diagnostic block
```

> **Pitfall:** if you'd "fixed" only the envelope but left the replay's `mode_state`
> deriving from the model's saturating P60, the engine's *state machine* would still be
> driven by the broken number. Find **every** consumer of the quantity, not just the
> one you were looking at.

### 5.4 This is an OUTPUT-CHANGING change → bump caches, gate the review

Contrast with the feature-flag tutorial: that flag was **output-preserving** (on/off
changed speed, not results). This change **deliberately changes results** — so it's a
different discipline:

- The daily replay has a persistent cache keyed by a schema version. Because the row
  schema changed (new columns, new canonical values), we **bumped the cache schema**
  (`v13_replay_cache_1 → _2`) so any stale cache is invalidated and recomputed. Forget
  this and a user silently gets old-schema rows → crashes or wrong numbers.
- An output-changing edit is **not** something you flip silently. It's a **gated
  before/after review**: you show the old and new numbers side by side and let a human
  accept the change. (That's exactly what the Phase 3 doc does — Part 6.)

```text
output-preserving change  → prove equivalence (diff must be empty), ship quietly
output-changing change    → show before/after, bump versions, get explicit sign-off
```

Knowing which kind you're making is half of doing it safely.

---

## Part 6 — Verifying (show the before/after, keep the invariants)

### The headline before/after (MSFT, live state)

| horizon | **canonical** (empirical) | diagnostic (old model curve) |
|---|---|---|
| P10 | 0.304 | 0.001 |
| P20 | 0.512 | 0.010 |
| P40 | **0.781**  (scope `group_transition`, n=239) | 0.286 |
| P60 | **0.924** | **1.000** ← the step |

- Canonical P60 is **0.924**, a data-driven frequency — *not* pinned at 1.0.
- Canonical **P40 = 0.781 single-ticker == 0.781 pooled** — the 0.002/0.30/0.51 swing is gone.
- The saturating step is still visible — *as the diagnostic*, exactly where it belongs.

> **Read `single == pooled` honestly — two different things are going on, and only one
> is general.**
> 1. *General (the real win):* a frequency-with-shrinkage barely moves when the fit
>    pool changes, whereas the logistic step's *toe* (P40) was hypersensitive — that's
>    the robustness you actually bought.
> 2. *Specific to THIS run (an artifact):* the winning scope was `group_transition` =
>    peer group `mega_cap_software`, and **MSFT is the only ticker in that peer group**
>    (AAPL → `mega_cap_hardware`, NVDA → `ai_accelerator`). So adding AAPL/NVDA to the
>    *universe* added **zero** rows *at the scope that won* — the 239 reference rows are
>    MSFT's own history either way. Here `single == pooled` is therefore *also* a
>    peer-composition coincidence, **not** proof that pooling never matters.
>
> This is precisely why the `reference_scope` / `reference_n` provenance (Part 4.5)
> earns its keep: it tells you the estimate is *transition-only MSFT history*, so you
> know a fuller peer group (Phase 5) would let a *state-conditioned* scope win and make
> pooling genuinely contribute. **Never report a pooled-looking number without checking
> the scope it actually came from.**

### Invariants that must STILL hold

An output-changing edit is not a license to break unrelated guarantees. The suite
still asserts:

- **`parallel == serial`** — running the universe on 2 processes yields byte-identical
  envelopes to 1 process. (Determinism survived the change.)
- **`incremental == full`** — the daily incremental replay equals a full recompute.
  (Because we threaded the empirical estimator through the *one* `build_replay_history`
  both paths share, and bumped the cache schema.)
- **schema additive** — `retry_hazard_context` kept its keys; we *added* sub-fields
  (`probability_policy`, `reference_scope/n`, `diagnostic_model_state_hold_forward`).
- **no hard-coded ticker** — an AST test forbids ticker literals in `src/`.

Plus new tests that **lock the fix**: the canonical P60 is `< 0.999` (not saturated),
the diagnostic P60 is `≥ 0.99` (the step is preserved), and the estimate carries a
non-empty `reference_scope`/`reference_n`.

```python
assert rhc["p_retry_within_60d"] < 0.999                 # canonical: not the step
assert rhc["diagnostic_..."]["p_retry_within_60d"] >= 0.99   # diagnostic: still the step
assert rhc["p_retry_within_40d_reference_n"] and rhc["p_retry_within_40d_reference_scope"]
```

**State the invariant, then assert it** — the same discipline whether the change
preserves outputs or changes them.

---

## Part 7 — Generalizing the pattern

"Replace a mis-specified model output with an empirical estimator" recurs far beyond
finance:

- **ETA / time-to-completion**: instead of extrapolating a rate, ask "for jobs in a
  similar state, how long did they actually take?" (bucket by size/queue/host).
- **Conversion / churn within N days**: empirical completed-path frequency from
  similar cohorts beats a frozen-feature survival extrapolation.
- **"Will this support ticket resolve within a day?"**: nearest-cohort historical rate
  with fallback + shrinkage.
- **Capacity/SLA breach probability**: observed frequency in similar load regimes.

The reusable checklist:

```text
✔ Is the model being asked an EXTRAPOLATION question it wasn't built for? (frozen state, definition contradiction)
✔ Can you reframe it as "how often did SIMILAR history do X within H?" (empirical)
✔ Bucket the state at a domain-sensible granularity.
✔ Walk a specific→general scope ladder; stop at the first with enough samples.
✔ Shrink small-sample rates toward a sensible prior.
✔ Return the scope + n (provenance), so consumers can judge trust.
✔ Keep the old computation as a labelled diagnostic; don't delete.
✔ It's OUTPUT-CHANGING: bump caches/versions, show before/after, get sign-off.
✔ Thread it through EVERY consumer; re-assert determinism/equivalence invariants.
```

Anti-patterns to avoid:

```text
✗ Tuning a model (more features, regularisation, "glide paths") to rescue an ill-posed extrapolation.
✗ A raw fraction from a 3-row bucket presented as a confident probability (no shrinkage).
✗ Conditioning so finely that every bucket is empty (no fallback ladder).
✗ Hiding the fallback — surfacing 0.78 without telling the caller it's transition-only, not state-conditioned.
✗ Deleting the old output others may consume (vs. demoting it to a diagnostic).
✗ Fixing the visible consumer (the envelope) but leaving a hidden consumer (the state machine) on the broken value.
✗ Changing outputs without bumping the cache schema → stale-cache corruption.
```

---

## Part 8 — Exercises (in this repo)

1. **See the saturation, then see it gone.** Run the MSFT pipeline; print
   `retry_hazard_context`. Compare canonical `p_retry_within_60d` (≈0.92) with
   `diagnostic_model_state_hold_forward["p_retry_within_60d"]` (≈1.0). Explain each in
   one sentence.
2. **Trace the scope ladder.** For MSFT, `p_retry_within_40d_reference_scope` is
   `group_transition` (not a state-conditioned scope). Open
   `empirical_horizon_probabilities_for_row`; explain *why* it fell back there (hint:
   MSFT is alone in its peer group, so the state buckets don't reach 25 rows).
3. **Feel the shrinkage.** In `empirical_horizon_probabilities_for_row`, change
   `HORIZON_PRIOR_STRENGTH` from 8 to 0 and to 50. How do thin-bucket estimates move?
   Why is 0 dangerous and 50 over-smoothed?
4. **Prove the invariants survived.** Run the `parallel == serial` and
   `incremental == full` tests. Why does threading the estimator through *one*
   `build_replay_history` (not the incremental wrapper) keep `incremental == full` true?
5. **Find the hidden consumer.** Grep for where `mode_state_replay` is computed. Show
   that it now reads the empirical P60, not the diagnostic. What would break if it
   still read the model curve?
6. **Predict Phase 5.** The estimator falls back to `group_transition` because the
   universe is thin. If you added 20 more tickers to the peer group, which
   `reference_scope` would you expect MSFT's P40 to land on, and why would that be a
   *better* estimate?

---

## Part 9 — Key takeaways

```text
• A calibrated model can still be MIS-SPECIFIED for an extrapolation task — check the question, not just the fit.
• Separate the instantaneous question (model: hazard_today) from the horizon question (empirical: P(retry≤H)).
• "Borrow strength from similar history": bucket → hierarchical fallback → shrink to a prior.
• Ship PROVENANCE (reference_scope, reference_n) so consumers can judge an estimate's trust.
• DEMOTE, don't delete: keep the old output as a labelled diagnostic.
• Know if your change is output-PRESERVING (prove equivalence) or output-CHANGING (before/after + sign-off + version bumps).
• Thread a behaviour change through EVERY consumer; re-assert determinism/equivalence invariants.
```

The whole fix is, in one line: *we stopped asking a one-day model to forecast a
horizon, and started asking history how often similar states actually retouched.*

---

## Appendix A — What "P10 / P20 / P40 / P60 / P90" mean here

This codebase uses `P<H>` as shorthand everywhere (`retry_hazard_context`, the replay
columns `p_retry_within_{H}d`, and this tutorial). Be exact about it, because the
notation **collides with a different finance convention**.

**In this engine, `P<H>` = P(retry ≤ H *trading* days)** — the *cumulative probability*
that the next canonical MA250 / yearline retouch (the "retry") happens within `H`
trading days of the as-of date:

```text
P<H> = cumulative_retry_probability at horizon H = 1 − survival_probability(H)
```

- **Horizons** `H ∈ {10, 20, 40, 60, 90}`. So `P40` means *"probability of a retouch
  within 40 trading days"* — **not** the 40th percentile of anything.
- **Trading days, not calendar days.** ~21 trading days ≈ one calendar month, so
  `P40` ≈ "within ~2 calendar months." (Contrast Phase 2's *timing* estimators, which
  report *calendar*-day gaps — a deliberate but easy-to-trip-over difference.)
- **Cumulative ⇒ monotone non-decreasing in H:** `P10 ≤ P20 ≤ P40 ≤ P60 ≤ P90`, always
  (more time only adds retouch opportunities). A test asserts this.
- **Survival** `survival_{H}d = 1 − P<H>` = "still hasn't retouched by day H."
- **`hazard_today` is a different quantity:** the **one-day** conditional hazard,
  P(retouch *today* | not yet) — from the logistic model, not the empirical estimator.
  A tiny `hazard_today` (e.g. 0.0001) alongside a moderate `P40` is normal: one
  specific day is unlikely, but 40 of them accumulate.

**Do not confuse this with the percentile (decile) convention.** In some forecasting
domains (oil & gas reserves, project ETAs), `P10 / P50 / P90` are *percentiles of an
outcome distribution* — e.g. "P90 = the value the outcome exceeds 90% of the time" —
where the values are in the outcome's **units** (days, barrels) and `P10 < P50 < P90`.
**Here the shape is the opposite:** the subscript is the **horizon in days** and the
value is a **probability in [0, 1]**. When in doubt, expand `P40` to its column name
`p_retry_within_40d` and the ambiguity disappears.

Worked example — MSFT, 2026-06-05 (canonical, empirical):

| field | value | plain-English reading |
|---|---|---|
| `hazard_today` | 0.0001 | ~0% chance it retouches *today* |
| `P10` | 0.304 | ~30% chance within 10 trading days |
| `P20` | 0.512 | ~51% within 20 |
| `P40` | 0.781 | ~78% within 40 (~2 calendar months) |
| `P60` | 0.924 | ~92% within 60 |
| `survival_60d` | 0.076 | ~8% chance it's *still* below the line after 60 |

(All conditioned on "similar historical states" at the `group_transition` scope,
n = 239 — see Part 4.5 on provenance. They are descriptive evidence, not a forecast,
and are uncalibrated until Phase 4.)

---

## Glossary

- **Discrete-time (logistic) hazard** — a model of the *one-day* conditional event
  probability ("given today's state, chance the event is today").
- **State-hold-forward / frozen-state extrapolation** — projecting a model forward by
  holding features constant and only advancing time; the source of the saturation.
- **Saturation (the "step")** — a cumulative probability that races to 1.0 regardless
  of state; a hallmark of a mis-specified forward extrapolation.
- **Empirical / non-parametric estimate** — a quantity computed directly from observed
  frequencies rather than a parametric model.
- **Completed-path reference** — historical rows labelled with how many days *remained*
  until the event actually occurred.
- **Bucketing** — discretising continuous features into ranges so "similar" states can
  be grouped.
- **Scope ladder / hierarchical fallback** — trying specific match criteria first and
  falling back to broader ones until enough samples are found.
- **Bayesian shrinkage (Beta prior)** — pulling a small-sample rate toward a prior so a
  few observations can't produce an over-confident estimate; strength `S` sets how much
  data it takes to override the prior.
- **Provenance** — metadata describing *how* a value was produced (here: which scope and
  how many reference rows), so its trustworthiness is auditable.
- **Output-preserving vs output-changing** — a change that alters only performance vs.
  one that alters results; the second needs a before/after review and version bumps.
- **Demote (not delete)** — relabel a superseded output as a diagnostic instead of
  removing it.
- **`P<H>` / horizon probability** — here, `P(retry ≤ H trading days)`, the *cumulative*
  probability of a retouch within `H` days (`p_retry_within_{H}d`). See **Appendix A**;
  not a percentile.
- **`hazard_today`** — the one-day conditional hazard, `P(retouch today | not yet)`,
  from the logistic model. Distinct from the horizon `P<H>`.
- **Survival probability** — `1 − P<H>`; the chance the event has *not* happened by
  horizon `H`.
- **Trading day** — a market session (~21 per calendar month); the unit the horizons
  are measured in (Phase 2 timing, by contrast, uses calendar days).
- **Retry / retouch** — the next canonical MA250 / yearline touch event the
  probabilities are about.

---

*Implementation: `src/yearline_universe/hazard.py`
(`build_empirical_horizon_reference`, `empirical_horizon_probabilities_for_row`,
scope ladder + shrinkage; `run_hazard_layer`), `replay.py` (canonical empirical +
`*_diagnostic` columns, `mode_state` from empirical P, cache-schema bump),
`context_export.py` (`retry_hazard_context` policy + provenance + diagnostic block).
Tests: `tests/test_hazard_empirical.py`. Case study + before/after:
`docs/phased_design/phase_03/`. Diagnosis: `docs/V13_data_and_report_analysis.md`
§2.2–§2.3. The fix ports the user's V12.4.1 policy from the benchmark notebook in
`docs/uploaded/`. Educational material — finance content is incidental.*
