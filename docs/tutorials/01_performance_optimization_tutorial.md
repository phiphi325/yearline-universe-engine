# Performance Optimization — A Practical Tutorial for Junior Engineers

**A field guide to making code faster *without breaking it*, taught through one real
optimization in this codebase: a daily-replay loop that went from ~56s to ~5.5s per
ticker (13× faster) with byte-identical outputs.**

By the end you will be able to:

- Decide *whether* and *what* to optimize (most code should never be optimized).
- Use a profiler to find the **real** bottleneck instead of guessing.
- Tell the difference between an **algorithmic** win and a **micro**-optimization,
  and reach for the algorithmic one first.
- Apply the core mechanisms: vectorization, removing redundant work, caching,
  batching, and (later) parallelism and incremental computation.
- **Prove** an optimization didn't change behavior, and benchmark it honestly.

> You do not need to know finance to follow this. The engine turns daily stock
> prices into a JSON "context" per ticker; all you need is that there was a slow
> loop, and we made it fast.

---

## Part 1 — Principles (read this first)

Performance work goes wrong in predictable ways. These principles are the
guardrails. Memorize them; the rest of the tutorial is just applying them.

### 1. Make it work → make it right → make it fast. In that order.
Correct, readable code first. You cannot optimize code you don't trust, and you
can't tell if you sped it up if you can't tell what "correct" was. Our engine was
**already working and tested** before we touched performance. That is what made
the optimization safe.

### 2. Don't guess. Measure.
> "Premature optimization is the root of all evil." — Donald Knuth
> (full quote: "We *should* forget about small efficiencies, say about 97% of the
> time.")

Engineers are *reliably wrong* about where their programs spend time. The slow
part is almost never the clever-looking math; it's usually an innocent line in a
loop that runs a million times. **A profiler tells you the truth; your intuition
guesses.** We'll prove this below — the real culprit was a humble `dict`/`Series`
assignment, not the model.

### 3. Optimize the thing that dominates (Amdahl's Law).
If a function is 88% of your runtime, making *everything else* infinitely fast
caps your win at 12%. If you make that 88% disappear, you get an ~8× speedup.
**Find the dominant cost and attack it; ignore the rest until it becomes the
dominant cost.** Optimizing a 2%-of-runtime function is wasted effort, no matter
how satisfying.

### 4. Algorithm beats micro-optimization.
Rewriting `x = x + 1` to be "clever" saves nanoseconds. Removing redundant work or
replacing an O(n²) approach with O(n) saves seconds-to-hours. **Always look for
the structural/algorithmic win first.** Our 13× came from *not doing* ~1,200
redundant computations, not from tuning any single line.

### 5. Never trade correctness for speed without a safety net.
A fast wrong answer is worthless — often worse than a slow right one, because it
hides. Before optimizing, capture a **baseline of the outputs**; after, prove the
new code produces the same outputs (within floating-point noise). No proof, no
merge.

### 6. Optimization shifts the bottleneck — know when to stop.
When you remove the #1 cost, the #2 cost becomes #1. There's always a next
bottleneck. Stop when it's "fast enough for the requirement," and **document the
next target** instead of chasing diminishing returns.

### 7. Readability has value too.
A 13× win that nobody can understand or maintain is a liability. Favor
optimizations that are *also* clear (good comments, a short rationale). If a fast
version is unavoidably subtle, explain *why it's correct* in the code.

---

## Part 2 — The optimization loop (the methodology)

Every disciplined optimization follows the same cycle. Do not skip steps 1, 2, or 6.

```text
   ┌──────────────────────────────────────────────────────────────┐
   │ 0. Confirm it's worth it (is slowness a real problem?)         │
   │ 1. BASELINE   measure current speed + snapshot current outputs │
   │ 2. PROFILE    find where the time actually goes                │
   │ 3. UNDERSTAND why is that part slow? what work is redundant?   │
   │ 4. HYPOTHESIZE the change + predict the win                    │
   │ 5. IMPLEMENT  the smallest change that addresses the bottleneck│
   │ 6. VERIFY     prove outputs are unchanged (correctness gate)   │
   │ 7. BENCHMARK  measure the real win; note the NEW bottleneck    │
   │ 8. DECIDE     ship + document, or revert if not worth it       │
   └──────────────────────────────────────────────────────────────┘
                         ↑___________ repeat if needed ___________│
```

The rest of this tutorial walks the loop on a real case.

---

## Part 3 — The case study

### 3.1 The symptom and why it mattered

The engine runs one ticker (one stock) through a pipeline and emits a JSON
"context." A single ticker took **~60 seconds**. That's fine for a one-off, but the
user's real goal was a **daily scan of many tickers across many sectors**. The cost
model is brutal:

```text
50 tickers  × 60s = 50 minutes
500 tickers × 60s = 8.3 hours    ← can't finish overnight
```

So "60s/ticker" wasn't a curiosity — it was the difference between a feasible and
an infeasible product. **That's the test for whether to optimize: does the slowness
block a real use case?** Here, yes.

### 3.2 Step 1 — Establish a baseline (and snapshot outputs)

Two things must be captured *before* changing anything:

1. **How slow is it?** (so we can prove a win)
2. **What does "correct" output look like?** (so we can prove we didn't break it)

```python
# bench_baseline.py (essentials)
for sym in ["MSFT", "AAPL", "NVDA"]:
    t0 = time.perf_counter()
    res = run_ticker_pipeline(uni.get_ticker(sym), uni, cache_dir=CACHE, provider="cache")
    dt = time.perf_counter() - t0
    # snapshot the outputs we must preserve:
    json.dump(export_single_ticker_context(res), open(f"baseline/{sym}_env.json", "w"), sort_keys=True)
    res.replay_history.to_pickle(f"baseline/{sym}_replay.pkl")
    print(f"{sym}: {dt:.2f}s")
```

Result:

```text
MSFT: 60.71s   AAPL: 53.28s   NVDA: 53.78s     (mean 55.9s)
```

> **Lesson:** Use `time.perf_counter()` (a monotonic high-resolution clock), not
> `time.time()`. Run on the same data each time. Capture outputs to disk now — this
> snapshot is your correctness contract for Step 6.

### 3.3 Step 2 — Profile (find the real bottleneck)

We used Python's built-in `cProfile`. Run it yourself:

```bash
python scripts/profile_pipeline.py --ticker MSFT --top 20
```

The output (trimmed) was the turning point:

```text
cumtime  ncalls    function
109.5s        1     run_ticker_pipeline
 96.6s        1       build_replay_history          ← 88% of the whole run
 92.8s     1231         _score_curve                ← called once per "as-of" day
 55.8s   443520           Series.__setitem__        ← 443k tiny assignments!
 20.7s     1233         prepare_hazard_design        ← get_dummies + concat, per day
```

**How to read a profile (you must know this):**

| Column | Meaning | What to look for |
|---|---|---|
| `ncalls` | how many times the function was called | a *huge* number on a *cheap* function = death by a thousand cuts |
| `tottime` | time *inside* the function, excluding callees | where raw CPU is burned |
| `cumtime` | time *including* everything it calls | follow this top-down to locate the hot subtree |

Reading top-down by `cumtime`: the whole run is 109.5s; `build_replay_history` is
96.6s of it; inside it, `_score_curve` runs **1,231 times**; inside *that*,
`Series.__setitem__` runs **443,520 times** for 55.8s.

> **This is principle #2 in action.** Nobody would have guessed that a *plain
> assignment* (`row["x"] = ...`) was the single biggest cost. The "smart" part (the
> statistical model) was a rounding error by comparison. The profiler told the
> truth.

### 3.4 Step 3 — Understand *why* it's slow

Here's what the slow code did, conceptually. For **each** of ~1,200 days, it built
a 90-row "future" table cell-by-cell, one-hot-encoded it, and asked the model to
score it:

```python
# BEFORE — runs ~1,200 times (once per as-of day)
def _score_curve(model, base_row):
    rows = []
    for h in range(1, 91):                 # 90 future days
        r = base_row.copy()
        r["future_horizon_day"] = h        # ← Series.__setitem__
        r["trading_days_since_touch"] = base_td + h   # ← Series.__setitem__
        r["calendar_days_since_touch"] = base_cd + h  # ← Series.__setitem__
        rows.append(r.to_dict())
    fut = pd.DataFrame(rows)               # build a 90-row frame
    X = prepare_hazard_design(train, fut)  # pd.get_dummies + concat  (EXPENSIVE)
    hazard = model.predict_proba(X)[:, 1]
    ...
```

Count the work: `1,200 days × 90 rows × ~4 assignments = ~432,000` cell writes,
plus `1,200 × (get_dummies + concat)`. The profiler's 443,520 and 1,233 line up
exactly.

Two redundancies jump out once you look:

1. **Re-encoding constants.** `prepare_hazard_design` (one-hot encoding) was redone
   every single day, even though the encoding scheme never changes.
2. **Re-deriving a predictable pattern.** Across the 90 future days, *almost
   everything is held constant* — only two columns (`trading_days_since_touch` and
   `calendar_days_since_touch`) change, and they just increment by `h`.

That second observation is the key. Hold that thought.

### 3.5 Step 4 — Find the algorithmic insight (the "aha")

This is the most valuable skill in the whole tutorial: **look for mathematical or
structural redundancy you can exploit.**

The model scoring each row is a **logistic regression**. For any feature row `x`:

```text
logit(x) = x · coef + intercept
hazard   = sigmoid(logit(x))            # sigmoid(z) = 1 / (1 + e^-z)
```

It's **linear** in the features. And across the 90 future days, the only features
that change are the two time counters, each `= base + h`. So:

```text
logit(day h) = base_logit + h · (coef_trading + coef_calendar)
             = base_logit + h · slope          ← slope is a single constant!
```

That means we **don't need to build or score 90 rows per day at all.** We need:

- `base_logit` for each day (one cheap dot product), and
- one constant `slope`.

Then the entire 90-day curve is closed-form arithmetic:

```text
hazard[day, h] = sigmoid(base_logit[day] + h · slope)
```

And we can compute **all days at once** with array math. The ~1,200 `get_dummies`
calls collapse to **one** (encode all the base rows together), and 432,000
assignments collapse to **zero**.

> **Lesson:** Before writing faster code, ask: *"What is this loop actually
> computing, and does it have structure I can exploit?"* Linearity, monotonic
> counters, repeated constants, and independence between iterations are all
> exploitable. The biggest wins come from *not computing* things.

### 3.6 Step 5 — Implement (vectorization)

"Vectorization" means replacing Python-level loops over elements with **whole-array
operations** that run in optimized C inside NumPy/pandas. One NumPy call over a
million numbers is vastly faster than a Python `for` loop doing the same thing,
because it avoids per-element Python object overhead, uses contiguous memory, and
loops at C speed.

```python
# AFTER — one design-matrix build for ALL days, then pure array math
def _batch_score_curves(model, feature_names, train, base_df, horizons, max_h=90):
    # 1) ONE one-hot encode for every as-of base row (not per-day):
    X_base = prepare_hazard_design(train, base_df).reindex(columns=feature_names, fill_value=0)

    coef = model.coef_.ravel()
    base_logit = X_base.to_numpy() @ coef + model.intercept_[0]     # (n_days,)
    slope = coef[feature_names.index("trading_days_since_touch")] \
          + coef[feature_names.index("calendar_days_since_touch")]  # one scalar

    # 2) broadcast over horizons h = 1..90  →  shape (n_days, 90), no Python loop
    H = np.arange(1, max_h + 1)
    logits = base_logit[:, None] + H[None, :] * slope
    hazard = 1.0 / (1.0 + np.exp(-logits))
    cum    = 1.0 - np.cumprod(1.0 - hazard, axis=1)   # cumulative retry prob
    ...
```

The two mechanisms at play:

- **Remove redundant work:** encode once, not 1,200×.
- **Vectorize:** `base_logit[:, None] + H[None, :] * slope` (NumPy *broadcasting*)
  computes the full `(days × horizons)` grid in one C-level operation instead of a
  nested Python loop.

A second, smaller fix (loop-invariant code motion — see §6.2) removed a repeated
indicator computation in the hazard panel.

> **Lesson:** Reach for vectorization when you're doing the *same arithmetic* across
> many rows/elements. Reach for "compute once, reuse" when you see the *same result*
> being recomputed inside a loop.

### 3.7 Step 6 — Prove it didn't change the answer (the gate)

This is non-negotiable. We had the baseline snapshots from Step 1; now we compare.

```python
# verify_optimization.py (essentials)
env_match = (json.dumps(new_env, sort_keys=True) == json.dumps(base_env, sort_keys=True))
# numeric columns: allow floating-point noise; categoricals: exact
ok = np.allclose(base_vals, new_vals, rtol=1e-7, atol=1e-9, equal_nan=True)
```

Result:

```text
MSFT: env_match=True  num_ok=True  cat_ok=True  maxabsdiff=2.03e-14
AAPL: env_match=True  num_ok=True  cat_ok=True  maxabsdiff=7.55e-15
NVDA: env_match=True  num_ok=True  cat_ok=True  maxabsdiff=5.11e-15
ALL OUTPUTS PRESERVED: True
+ pytest: 22/22 pass
```

**Why `allclose` and not `==` for numbers?** Floating-point math is not
associative: `(a + b) + c` can differ from `a + (b + c)` in the last bit. The old
code did a fresh matrix multiply per row; the new code does one multiply plus a
scalar add. Mathematically identical, but the bits differ at ~10⁻¹⁴ (machine
epsilon). That is *noise*, not a behavior change — so we assert "equal within a
tiny tolerance," and we check the exported JSON is byte-identical and the existing
test suite still passes.

> **Lesson:** Your correctness gate has three layers here: (1) byte-identical
> public output (the envelope JSON), (2) numerically-`allclose` internal data, and
> (3) the existing automated tests. If any layer fails, you do **not** ship — you
> debug or revert.

### 3.8 Step 7 — Benchmark honestly (and find the new bottleneck)

```text
            Before     After    Speedup
MSFT        60.71s    10.12s      6.0×
AAPL        53.28s     3.26s     16.3×
NVDA        53.78s     3.23s     16.7×
mean        55.9s      5.5s      13.0×
```

Two honest observations a junior engineer should *always* include:

- **Report the range, not just the best number.** "13× mean (6–17×)" is honest;
  "17×!" is cherry-picking.
- **Name the new bottleneck.** MSFT is now 10s while AAPL/NVDA are ~3s. Profiling
  again shows MSFT's residual time is a *different* thing — a bootstrap that refits
  a model 300 times (it only triggers for stocks with enough history). The replay
  is no longer the problem. Per principle #6, we **documented** that as the next
  target rather than chasing it now.

This is the loop closing: optimize → re-measure → the bottleneck moved → decide
whether the new one is worth another pass.

---

## Part 4 — The general toolbox (mechanisms)

The case study used three techniques. Here is the broader menu, roughly in the
order you should consider them (cheapest/biggest wins first).

| Technique | What it does | Reach for it when… | In our case study |
|---|---|---|---|
| **Don't do it** | Remove redundant/unneeded work | the same result is recomputed; work is thrown away | encode once, not per-day |
| **Better algorithm / complexity** | Lower the Big-O | nested loops, O(n²) scans, repeated sorts | closed-form replaced a per-day rebuild |
| **Vectorization** | Whole-array C ops vs Python loops | doing the same arithmetic over many elements | NumPy broadcasting over horizons |
| **Caching / memoization** | Store and reuse results | pure function called repeatedly with same inputs | indicators computed once per ticker |
| **Loop-invariant code motion** | Move constant work out of loops | a value inside a loop never changes per-iteration | indicator frame hoisted out |
| **Batching** | Amortize fixed per-call overhead | many small calls with high per-call cost | one `get_dummies` for all rows |
| **Lazy / incremental** | Compute only what's new | re-deriving history every run | *recommended next:* incremental daily mode |
| **Parallelism** | Use multiple cores | independent units of work, CPU-bound | *recommended next:* parallel tickers |
| **Better data structures** | O(1) vs O(n) lookups | repeated membership/lookup in lists | use `set`/`dict`, not `list`, for lookups |
| **Efficient I/O / formats** | Faster load/parse/serialize | CSV parsing, chatty network/DB calls | *recommended:* parquet cache |
| **Faster libraries** | Drop to C/Rust/compiled | hot numeric loops that can't vectorize | *if needed:* numba/polars |

**Profiling tools to know:**

- `cProfile` + `pstats` — function-level, built-in, zero setup. **Start here.**
- `time.perf_counter()` / `timeit` — quick wall-clock for a block or micro-bench.
- `line_profiler` (`@profile`) — line-by-line, when a function is hot but you don't
  know which *line*.
- `tracemalloc` / `memory_profiler` — when the problem is **memory**, not CPU.
- `py-spy` — sampling profiler that attaches to a running process (great for
  production / long jobs, no code changes).

---

## Part 5 — Pitfalls and anti-patterns

Junior engineers lose the most time here. Recognize these:

1. **Optimizing without profiling.** You'll "speed up" the 2% and feel productive
   while the 88% sits untouched. *Always profile first.*
2. **Premature optimization.** Don't harden hot-path code that runs once a week.
   Optimize when there's a measured need (principle #1, #3).
3. **No baseline.** If you didn't measure before, you can't prove a win — and "it
   feels faster" is not an engineering claim.
4. **Breaking correctness silently.** The scariest bug: faster code that's subtly
   wrong. The equivalence gate (Step 6) exists to catch exactly this.
5. **Micro-optimizing in an interpreted loop.** In Python, the loop *itself* is
   often the cost. Replacing `a*2` with `a<<1` inside a 1M-iteration Python loop is
   pointless; *removing the Python loop* (vectorize) is the win.
6. **Over-engineering.** Adding multiprocessing, caching layers, and a C extension
   to save 200ms on code that runs once. Match the effort to the payoff.
7. **Trusting a single timing.** Warm up, run a few times, watch for variance
   (GC, OS scheduling, cold caches). Report representative numbers.
8. **Sacrificing readability for a tiny gain.** A clever one-liner that saves 1% but
   takes an hour to understand is a net loss for the team.

---

## Part 6 — Two techniques in depth

### 6.1 Vectorization — why it's fast

A Python `for` loop over a list does, per element: a bytecode dispatch, attribute
lookups, creates/boxes Python objects, and bounds checks. NumPy stores numbers in a
**contiguous typed buffer** and runs the loop in **compiled C**, so the per-element
overhead nearly vanishes and the CPU can use cache-friendly, SIMD-friendly access.

Rule of thumb: **if you're writing a Python loop that does arithmetic on many
numbers, there is probably a vectorized form that's 10–100× faster.** Look for
`numpy` array ops, broadcasting (`a[:, None] + b[None, :]`), `cumprod`/`cumsum`,
boolean masks, and pandas column operations.

The catch: vectorization needs the computation to be expressible as array math.
Our case qualified *because the model was linear* — we found the algebra first,
then vectorized. Vectorizing blindly without the algebraic insight wouldn't have
worked.

### 6.2 Loop-invariant code motion — stop recomputing constants

The classic compiler optimization, done by hand:

```python
# slow: recomputes the same thing every iteration
for tr in transitions:
    df = add_indicators(price_df, config)   # identical every loop!
    ... use df ...

# fast: compute the invariant ONCE, reuse it
df = add_indicators(price_df, config)
for tr in transitions:
    ... use df ...
```

We did exactly this in the hazard panel: the rolling-average indicator frame was
being recomputed for every transition though it never changed. Hoisting it out is
trivial, safe, and free speed. **Whenever you see a function call inside a loop,
ask: does its result actually change each iteration? If not, move it out.**

---

## Part 7 — Exercises (do these in this repo)

1. **See the truth.** Run `python scripts/profile_pipeline.py --ticker MSFT --top 25`.
   Identify the top 3 functions by `cumtime`. Which has the largest `ncalls`?
2. **Compare tickers.** Profile `AAPL` and `NVDA`. MSFT is ~3× slower than the
   others — find the function responsible (hint: it fits a model many times) and
   write one sentence explaining why only MSFT hits it.
3. **Hypothesize a fix.** For that bootstrap bottleneck, propose two
   output-preserving (or output-*approximately*-preserving) optimizations. What
   would you measure to decide if the approximation is acceptable?
4. **Build an equivalence harness.** Write a script that snapshots `MSFT`'s envelope,
   then (pretend to) change code and assert the envelope is unchanged with
   `json.dumps(..., sort_keys=True)` equality. Why `sort_keys`?
5. **Loop-invariant hunt.** Grep the codebase for `add_indicators(` calls. Are any
   inside loops where the input doesn't change? (We fixed one — can you justify why
   the remaining calls are fine?)
6. **Scaling math.** Using the measured ~5.5s/ticker mean, compute the wall-clock
   for a 250-ticker daily scan: (a) serial, (b) on 8 cores. Then estimate it with an
   "incremental daily mode" that only scores the newest bar (~0.3s/ticker). Which
   change matters most at 250 tickers? At 5,000?

---

## Part 8 — Key takeaways (the cheat sheet)

```text
WHETHER to optimize:   only if measured slowness blocks a real requirement.
WHERE to optimize:     wherever the profiler says (it's never where you guessed).
WHAT to optimize:      the dominant cost (Amdahl); algorithm before micro-tuning.
HOW to optimize:       remove redundant work > vectorize > cache > batch > parallelize.
PROVE it's safe:       baseline snapshot → change → assert outputs unchanged → tests pass.
KNOW when to stop:     when "fast enough"; document the next bottleneck and move on.
STAY honest:           report ranges, name the new bottleneck, keep code readable.
```

The 13× win in this repo was not cleverness — it was **discipline**: measure,
find the dominant redundant work, exploit the structure (linearity) to *not do it*,
prove nothing changed, and report honestly. That process is reproducible on almost
any slow program you'll meet.

---

## Glossary

- **Baseline** — the measured speed and captured outputs *before* optimizing; your
  reference for proving a win and proving correctness.
- **Profiler** — a tool that measures where a program spends time/memory.
- **`cumtime` / `tottime` / `ncalls`** — cumulative time (incl. callees) / time in
  the function itself / number of calls (cProfile columns).
- **Amdahl's Law** — the max speedup is limited by the fraction of runtime you
  *don't* speed up.
- **Vectorization** — replacing element-wise Python loops with whole-array
  operations executed in compiled C (NumPy/pandas).
- **Broadcasting** — NumPy's rule for combining arrays of different shapes (e.g.
  a column vector + a row vector → a matrix) without explicit loops.
- **Loop-invariant code motion** — moving a computation whose result doesn't change
  out of a loop.
- **Memoization / caching** — storing the result of a pure function so repeated
  calls with the same inputs are free.
- **Output-preserving / refactor-safe** — a change that provably does not alter
  results (within floating-point tolerance).
- **`allclose`** — `numpy.allclose(a, b, rtol, atol)`: true if arrays are equal
  within a relative+absolute tolerance; the right way to compare floats.
- **Machine epsilon** — the tiny relative error inherent in floating-point
  arithmetic (~10⁻¹⁶ for float64); why reordered math differs in the last digits.
- **Incremental computation** — recomputing only what changed (e.g. today's new
  data) instead of redoing the whole history each run.

---

*Worked example, code, and measurements come from this repository. See
`docs/V13_performance_optimization_report.md` for the full case write-up, and
`scripts/profile_pipeline.py` to reproduce the profile. Educational material —
the finance content is incidental; the engineering lessons are the point.*
