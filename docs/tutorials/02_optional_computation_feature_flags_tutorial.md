# Optional Expensive Computation & Feature Flags — A Tutorial for Junior Engineers

**How to design a feature you *can* run but *shouldn't always* run — taught through
this engine's `fit_ml_models` flag, which keeps a ~6-second-per-ticker computation
out of the default path because nothing currently consumes it.**

By the end you will understand:

- The principle **"don't pay for what you don't consume"** (YAGNI + dead computation).
- The **capability-vs-default** pattern: keep a feature available, but default to the
  cheap/safe path.
- Why a good flag's default is **output-preserving** (turning it on/off changes
  *speed*, not *results*).
- **Data-dependent cost**: why the same flag is expensive for one input and free for
  another.
- **Layered API design**: threading a flag cleanly from a core function → pipeline →
  CLI, with consistent defaults.
- A **decision framework** for when to flip such a flag on.

> Companion to `performance_optimization_tutorial.md`. That one taught *finding* a
> bottleneck; this one teaches *designing the switch* that controls one. No finance
> background needed — the engineering ideas are the point.

---

## Part 1 — The story (why this flag exists)

The engine produces a per-ticker JSON "statistical context envelope." Internally it
has two model families:

1. A **discrete-time survival hazard** model → its outputs (retry probabilities)
   **are** consumed by the envelope.
2. A pair of **retry-timing / quality ML models** (a regression + a classifier) →
   their outputs are **not** read by the V13.1 envelope at all.

The timing model is fitted with a **bootstrap**: it refits a `HuberRegressor`
**300 times** on resampled data to estimate a prediction interval. That's expensive.

During performance work we profiled a ticker (MSFT) and found it spent ~6–7 seconds
in that 300-fit bootstrap — producing predictions that were then **thrown away**,
because the envelope never reads them. Classic dead computation.

The fix wasn't to make the bootstrap faster. It was to **not run it by default**,
while keeping the ability to run it when someone actually wants those predictions.
That switch is the boolean **`fit_ml_models`** (default `False`).

```text
Before:  always fit timing+quality models  → MSFT ~10s, output discarded
After:   fit_ml_models=False (default)      → MSFT ~3.6s, identical envelope
         fit_ml_models=True (opt-in)         → models run, predictions returned
```

---

## Part 2 — The principles

### Principle 1 — Don't pay for what you don't consume
The single most reliable speedup is **deleting work whose result is unused**. Before
optimizing a computation, ask the prior question: *does anything read its output?*
If not, the right move isn't "make it faster" — it's "don't run it."

This is a cousin of **YAGNI** ("You Aren't Gonna Need It"): don't build/run
machinery for a consumer that doesn't exist yet. The timing/quality models were
ported faithfully from the V12 research notebook (where a research report *did* read
them). In V13 the envelope doesn't — so in V13 they're optional, not mandatory.

### Principle 2 — Capability ≠ default
There are two separate questions for any expensive feature:

1. *Should the code be able to do this at all?* (capability)
2. *Should it do it on every run?* (default)

A junior instinct is to conflate them ("the code computes X, so it always computes
X"). Mature design **separates** them: keep the capability, gate it behind a flag,
and choose a default that serves the common case. Here: capability = the ML models
still exist and run on demand; default = off, because the common case (building the
envelope) doesn't need them.

### Principle 3 — A good default is output-preserving
The best kind of flag changes **performance, not results**. `fit_ml_models` flips
between fast and slow, but the envelope is **byte-identical** either way (because the
envelope never depended on the gated outputs). That property is gold:

- It's a *safe* default — turning the optimization on by default can't surprise
  anyone with changed numbers.
- It's *trivially verifiable* — diff the outputs on vs off; they must match.
- It de-risks rollout — you can flip the default without a behavior-change review.

Contrast with a flag that changes outputs (e.g. "use model v2"): that's a different,
riskier category (it needs migration, comparison, and a behavior-change decision).
Know which kind of flag you're building.

### Principle 4 — Know *where* the cost actually lands (data-dependent cost)
"This flag costs ~6s" is incomplete. The honest statement is: it costs ~6s **only
for inputs that trigger the expensive branch**. The timing bootstrap has a guard:

```python
# fit_retry_timing_model
if completed.empty or live.empty or len(completed) < 20:
    return ..., {"status": "insufficient_data"}   # short-circuit, no bootstrap
```

So a ticker with <20 completed transitions never pays for the bootstrap; the
function returns immediately. Measured:

| ticker | completed transitions | `fit_ml_models=False` | `=True` |
|---|---|---|---|
| MSFT | 23 (≥20 → bootstrap runs) | 3.66s | 10.02s (**+6.4s**) |
| AAPL | 15 (<20 → short-circuits) | 3.19s | ~3.21s (≈ free) |

Lesson: profile/measure across **representative inputs**, and state the *conditions*
under which a cost appears. "It depends on the data" is often the correct, precise
answer.

---

## Part 3 — How it's designed (the mechanics)

### 3.1 The gate, at the source
The flag lives where the expensive work is — in `run_hazard_layer`:

```python
def run_hazard_layer(..., fit_ml_models: bool = False):
    # Survival hazard (consumed by the envelope) — always runs.
    ...
    # ML timing/quality — OPTIONAL (not consumed by the V13.1 envelope).
    timing_pred, quality_pred = pd.DataFrame(), pd.DataFrame()
    timing_status = {"status": "skipped_not_consumed_by_envelope"}
    if fit_ml_models:
        ml_dataset = build_retry_transition_dataset(...)
        if not ml_dataset.empty:
            timing_pred, _, _, timing_status = fit_retry_timing_model(ml_dataset, ticker)   # 300-fit bootstrap
            quality_pred, _, _ = fit_retry_quality_classifier(ml_dataset, ticker)
    return {..., "timing_prediction": timing_pred, "quality_prediction": quality_pred, ...}
```

Design notes worth copying:
- **Default is `False`** (the cheap, output-preserving path).
- When skipped, it returns *well-formed empties* + an explicit
  `status="skipped_not_consumed_by_envelope"` — not `None`, not a crash. Downstream
  code and humans can tell *why* it's empty.
- The expensive branch is small and clearly commented as optional.

### 3.2 Threading the flag through the layers
A flag that only exists deep in one function is hard to use. We thread it up through
every layer a caller touches, **keeping the same default at each level**:

```text
run_hazard_layer(fit_ml_models=False)         # where the work is
   ▲
run_ticker_pipeline(fit_ml_models=False)      # per-ticker entry point
   ▲
run_universe_pipeline(fit_ml_models=False)    # batch entry point
   ▲
scripts/run_universe_mvp.py  --fit-ml-models  # CLI switch
```

> **Pitfall:** if you add a flag to the core but forget to thread it through the
> pipeline/CLI, the feature is unreachable in practice and your docs are lying. Add
> the flag *and* its path to every public entry point, with one consistent default.

### 3.3 Surfacing the result when enabled
Computing something and *still* discarding it would defeat the purpose. When the flag
is on, the pipeline attaches the predictions where a caller can find them:

```python
if fit_ml_models:
    manifest["ml_models"] = make_json_safe({
        "timing_status": hz.get("timing_status"),
        "timing_prediction": ...,   # the point estimate + p10..p90 interval
        "quality_prediction": ...,  # next-attempt-success probability
        "ml_dataset_rows": ...,
    })
```

So enabling the flag is *useful*, not just *slow*: the outputs land on
`result.manifest["ml_models"]`. (They are deliberately **not** injected into the
envelope — that schema is stable; see Part 4.)

### 3.4 Verifying the default is output-preserving
Because the claim is "on/off only changes speed," we prove it:

```python
off = run_ticker_pipeline(tc, uni, fit_ml_models=False)
on  = run_ticker_pipeline(tc, uni, fit_ml_models=True)
assert json.dumps(export_single_ticker_context(off), sort_keys=True) \
    == json.dumps(export_single_ticker_context(on),  sort_keys=True)   # byte-identical
```

This is the same discipline as any refactor: **state the invariant, then assert it.**

---

## Part 4 — Fundamentals: why the envelope doesn't need these models

A useful design question: *why are the hazard model's outputs consumed but the
timing/quality models' outputs not?*

- The **survival hazard** answers "what's the probability of a retry within N days?"
  — which maps directly onto the envelope's `retry_hazard_context`.
- The **timing/quality** models answer "what's the *expected gap* and the *next
  attempt's success probability*?" — richer, but the V13.1 envelope's schema simply
  doesn't have fields for them yet.

So they're not *wrong* or *useless* — they're **ahead of their consumer**. That's the
exact situation a capability flag is for: keep the work available for when a consumer
arrives (research, pooled-hazard training, a future schema), but don't make every run
pay for it now. Injecting them into the envelope *today* would be the wrong fix — it
would destabilize a published schema for data nobody reads.

---

## Part 5 — How to use it in this application

**Default (recommended) — fast, no ML models:**
```python
res = run_ticker_pipeline(uni.get_ticker("MSFT"), uni, cache_dir="data/price_cache")
# envelope is complete; res.manifest has no "ml_models" key
```

**Enable the prototype ML models:**
```python
res = run_ticker_pipeline(uni.get_ticker("MSFT"), uni, cache_dir="data/price_cache",
                          fit_ml_models=True)
ml = res.manifest["ml_models"]
ml["timing_prediction"]   # {predicted_gap_p50, p10..p90, rough_retry_date_p50, ...} or None
ml["quality_prediction"]  # {p_next_retry_success, quality_bucket, ...} or None
ml["timing_status"]       # {"status": "ok"} or {"status": "insufficient_data", ...}
```

**Whole universe / CLI:**
```python
run_universe_pipeline(uni, cache_dir="data/price_cache", fit_ml_models=True)
```
```bash
python scripts/run_universe_mvp.py config/universe_mega_cap_ai_infra.yaml \
    --provider cache --fit-ml-models
```

**The envelope is identical with or without the flag.** When on, the predictions are
metadata on `result.manifest["ml_models"]`; the timing model only does its bootstrap
for tickers with ≥20 completed transitions (others report `insufficient_data`), and
the quality classifier needs ≥30 labeled transitions and both classes or it reports
`suppressed_insufficient_labels`.

---

## Part 6 — Decision framework: when to turn it on

Default **off**. Turn it **on** only if you can answer "yes" to one of these:

```text
[ ] Am I doing research/inspection that needs the retry-gap distribution
    (p10..p90) or the next-attempt-success probability?
[ ] Am I working on V13.2 pooled-hazard training, where the models are fitted on
    the whole universe (more samples ⇒ the predictions become meaningful)?
[ ] Does a downstream consumer (e.g. option-mgmt-2026) now read these fields?
[ ] Am I about to add timing/quality fields to the envelope schema and need them
    populated?
```

If none apply, leaving it off is correct — you'd be paying ~6s/high-event-ticker for
output nobody reads.

---

## Part 7 — Generalizing the pattern

`fit_ml_models` is one instance of a recurring design need: **optional, expensive, or
side-effecting work that shouldn't run by default.** The same pattern applies to:

- **Verbose logging / debug artifacts** (`debug=False`).
- **Optional enrichment** (extra API calls, extra columns) a caller may not need.
- **Expensive validations / audits** (`strict=False`) you run in CI but not hot paths.
- **Plot/report generation** alongside a fast data computation.

Checklist for designing such a flag well:

```text
✔ Default to the cheap/safe path (and make that default output-preserving if you can).
✔ Name it for what it does (fit_ml_models), not how (run_bootstrap_300x).
✔ Thread it through every public entry point with ONE consistent default.
✔ When off, return well-formed, clearly-labeled "skipped" values — not None/garbage.
✔ When on, make the result reachable (don't compute-then-discard).
✔ Document the cost and the conditions under which it applies.
✔ Write a test that on/off agree on everything they should.
```

Anti-patterns to avoid:

```text
✗ A flag whose default silently changes outputs (that's a behavior change, not a perf flag).
✗ "Flag soup": dozens of booleans nobody understands; prefer a small, well-named set.
✗ Adding the flag to the core but not the pipeline/CLI (unreachable feature).
✗ Computing the expensive thing and discarding it (the bug this flag fixed).
✗ Flags that never get cleaned up after their experiment ends (flag debt).
```

---

## Part 8 — Exercises (in this repo)

1. **Measure it yourself.** Time `run_ticker_pipeline("MSFT", ..., fit_ml_models=False)`
   vs `True`. Reproduce the ~+6.4s. Then do `AAPL` and explain why it's ~free.
2. **Prove output-preservation.** Diff `export_single_ticker_context` on vs off
   (sorted-key JSON). Why must they be identical given how the envelope is built?
3. **Read the gate.** Open `fit_retry_timing_model`; find the `< 20` guard. What
   `timing_status` does a 12-transition ticker get? Where is that surfaced when the
   flag is on?
4. **Find another candidate.** Grep the pipeline for any other computation whose
   result isn't read by the envelope/manifest. Could it be gated too? What default
   would you choose, and is it output-preserving?
5. **Design a flag.** Sketch (don't necessarily build) a `make_plots=False` flag for
   the dashboard: where does it live, how does it thread through, what does "off"
   return, and how do you keep it output-preserving for the data outputs?

---

## Part 9 — Key takeaways

```text
• The fastest computation is the one you don't run — gate work whose output is unused.
• Separate CAPABILITY (can do it) from DEFAULT (do it every time).
• Prefer flags that change SPEED, not RESULTS; make the default output-preserving.
• Costs are data-dependent — state the conditions, measure on representative inputs.
• Thread a flag through every entry point with one consistent default; surface results when on.
• Default fit_ml_models OFF; turn it ON only when a real consumer needs the predictions.
```

A flag like `fit_ml_models` is small, but it encodes a lot of judgment: what's
essential vs. optional, how to keep defaults safe, and how to leave a capability
ready for a future that hasn't arrived yet.

---

## Glossary

- **Feature flag** — a parameter that turns a capability on/off without changing the
  code path that's already shipped.
- **YAGNI** ("You Aren't Gonna Need It") — don't build/run machinery for a
  hypothetical future consumer; add it when the consumer is real.
- **Dead computation / dead code** — work whose result is never used; pure cost.
- **Output-preserving** — a change (or flag) that does not alter results, only
  performance; verifiable by diffing outputs.
- **Short-circuit** — returning early before doing expensive work when a cheap
  precondition fails (e.g. `<20 transitions ⇒ skip the bootstrap`).
- **Bootstrap (statistics)** — estimating uncertainty by refitting a model many times
  on resampled data (here, 300 Huber fits) — accurate but expensive.
- **Capability vs. default** — whether code *can* do something vs. whether it does it
  *by default*; good design separates the two.
- **Lazy / opt-in computation** — computing something only when explicitly requested.
- **Flag debt** — accumulated, unused, or undocumented flags that make a system hard
  to reason about.

---

*Implementation: `src/yearline_universe/hazard.py` (`run_hazard_layer`),
`ticker_pipeline.py` (threading + `manifest["ml_models"]`),
`scripts/run_universe_mvp.py` (`--fit-ml-models`). Measurements and the broader
optimization story: `docs/V13_performance_optimization_report.md`. Educational
material — finance content is incidental.*
