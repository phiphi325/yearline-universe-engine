# MSFT, 2026-06-05 — reading a low-readiness repair

*A single, fully worked example of the Phase 7 blend overlay in action. It shows exactly how the
direct horizon classifier **tempers** the empirical retry probabilities for one real state, why,
and how the gated overlay is assembled. Educational research only — **not** investment advice, and
**not** a trading signal. The engine emits evidence context, never trades.*

> Read `06_direct_horizon_classifier_and_blend_tutorial.md` first for the concepts. This tutorial is
> the concrete payoff: one date, one ticker, every number explained.

---

## 1. The scene

On **2026-06-05**, MSFT is **~10% below its MA250 (yearline)** — `distance_to_ma250_pct ≈ −10.1`,
so a retouch needs an ~11.2% rebound. The **repair / retry hazard engine** is active (price is
below / testing the yearline). The question the engine answers is *not* "will it retouch?" but
**"how soon — P(rettouch within H trading days) for H = 10, 20, 40, 60?"**

## 2. What the static estimator sees

The canonical estimator (Phases 3–5) is an **empirical completed-path count**: among *similar*
historical at-risk states — bucketed by distance, drawdown, and days-since-touch — how often did the
next retouch land within H days? For MSFT on this date it returns:

| H (trading days) | empirical P(rettouch ≤ H) |
|---|---|
| 10 | 0.262 |
| 20 | 0.418 |
| 40 | 0.603 |
| 60 | 0.687 |

This is well-calibrated at ≤40d and it is the number the engine **trusts and surfaces canonically**.
But it conditions only on *static buckets* — it sees "10% below," not *how* the stock got there or
where it's heading.

## 3. What the path features add — the "readiness" of the repair

The Phase 7 path features (all leakage-safe, computed only from data ≤ 2026-06-05) describe the
*shape* of this repair. For MSFT on this date:

| feature | value | reading |
|---|---|---|
| `repair_gap_pct` | 10.10 | 10% below the yearline (matches the static view) |
| `close_position_in_repair_range` | **0.04** | sitting **at the repair low**, not bouncing |
| `bounce_from_low_pct` | 0.55 | barely off the low |
| `distance_to_ma250_change_10d` | **−0.31** | the gap is **widening**, not closing |
| `return_5d` | −7.5 | a sharp recent drop |
| `realized_vol_20d_pctile_252d` | **0.92** | volatility near a 1-year high (repair-unfriendly) |

Read together, these say **low readiness**: this isn't a repair coiling to reclaim the line — it's
one still making lower lows in high volatility. The static buckets are blind to that distinction;
two states "10% below" can be worlds apart, and this is the unfavourable kind.

## 4. What the classifier does with that

The direct horizon classifier (L2 logistic on the path + cross-sectional features, fit on the pooled
universe of completed repairs) converts "low readiness" into **lower near-term retouch
probabilities** than the static count:

| H | empirical | **classifier** |
|---|---|---|
| 10 | 0.262 | **0.085** |
| 20 | 0.418 | **0.195** |
| 40 | 0.603 | **0.492** |
| 60 | 0.687 | **0.704** |

Notice the *shape* of the disagreement: the classifier is far more bearish on a **near-term**
retouch (10–20d) — a stock at its repair low with a widening gap rarely snaps back in two to four
weeks — but by **60d** it essentially agrees with the count (0.704 vs 0.687). The signal is "not
soon," not "never."

## 5. The blend — combining the count and the ranker

We don't pick one. Phase 7's leave-one-ticker-out study showed the classifier **ranks** better while
the empirical count **calibrates** better, so the surfaced number is a per-horizon convex blend
`w·classifier + (1 − w)·empirical`, with `w` chosen out-of-fold by Brier (lean on the empirical at
10d, balanced thereafter):

| H | empirical | classifier | w (classifier) | **blend** | arithmetic |
|---|---|---|---|---|---|
| 10 | 0.262 | 0.085 | 0.25 | **0.218** | 0.25·0.085 + 0.75·0.262 |
| 20 | 0.418 | 0.195 | 0.50 | **0.306** | 0.50·0.195 + 0.50·0.418 |
| 40 | 0.603 | 0.492 | 0.50 | **0.548** | 0.50·0.492 + 0.50·0.603 |
| 60 | 0.687 | 0.704 | 0.50 | **0.696** | 0.50·0.704 + 0.50·0.687 |

So the overlay **tempers the near-term** retouch probabilities — **10d 0.262 → 0.218, 20d 0.418 →
0.306, 40d 0.603 → 0.548** — and **converges at 60d (0.687 → 0.696)**. In plain language: *similar
states usually retouch this often, but this particular repair is still falling, so discount the near
term.* That is discrimination earning its keep on a single real state.

## 6. The gate — and why the empirical stays canonical

Each horizon's blend carries the **same trust gate** as the calibrator (AUC ≥ 0.60, MACE ≤ 0.10,
n ≥ 50, measured out-of-fold on the pooled panel). For this model **all four horizons pass**
(blend AUC ≈ 0.83 / 0.81 / 0.81 / 0.79; MACE ≈ 0.05 / 0.07 / 0.05 / 0.06). Even so:

- the **empirical estimate remains the canonical** `p_retry_within_{h}d` — the blend never
  overwrites it;
- the blend is attached as a **clearly-labelled, gated discriminative overlay**;
- it is **opt-in** (`surface_blend=True`) and **pooled-only** (the cross-sectional features need the
  universe). With the switch off, the envelope is byte-identical to before.

This is the project's value-first / trust-last stance: surface the defensible canonical number
always; surface the sharper learned number only where it has earned a passing gate, and only as an
overlay a consumer can choose to read.

## 7. What lands in the envelope

With `surface_blend=True`, MSFT's `retry_hazard_context` gains:

```jsonc
"direct_classifier_blend": {
  "available": true,
  "schema": "v13_phase7_direct_classifier_blend_overlay",
  "policy": "gated_discriminative_overlay_empirical_remains_canonical",
  "any_gate_passed": true,
  "per_horizon": {
    "10": { "blend_probability": 0.218, "classifier_probability": 0.085,
            "empirical_probability": 0.262, "blend_weight_classifier": 0.25, "gate_passed": true },
    "20": { "blend_probability": 0.306, "classifier_probability": 0.195,
            "empirical_probability": 0.418, "blend_weight_classifier": 0.50, "gate_passed": true },
    "40": { "blend_probability": 0.548, "classifier_probability": 0.492,
            "empirical_probability": 0.603, "blend_weight_classifier": 0.50, "gate_passed": true },
    "60": { "blend_probability": 0.696, "classifier_probability": 0.704,
            "empirical_probability": 0.687, "blend_weight_classifier": 0.50, "gate_passed": true }
  },
  "must_not_auto_execute": true
}
```

The canonical `p_retry_within_10d … _60d` fields are unchanged beside it.

## 8. Reproduce

```python
from yearline_universe import load_universe_config, run_universe_pipeline

uni = load_universe_config("config/universe_mvp_software_like.yaml")     # 9 tickers, to 2026-06-05
result = run_universe_pipeline(uni, cache_dir="data/price_cache", provider="cache",
                               pool_hazard=True, surface_blend=True)       # pooled + overlay on
env = result.ticker_results["MSFT"].latest_context
blend = env["retry_hazard_context"]["direct_classifier_blend"]
for h, v in blend["per_horizon"].items():
    print(h, v["empirical_probability"], "→", v["blend_probability"], "(w=", v["blend_weight_classifier"], ")")
```

(Lower-level path: `build_blend_model(pooled_data)` once, then `build_blend_context(...)` per ticker —
see `src/yearline_universe/blend_surface.py`.)

## 9. How to read it (and how not to)

- **Do** read it as evidence that this *specific* repair looks slower-to-retouch than the historical
  average for its bucket — a context overlay.
- **Don't** read any number here as a forecast, a probability you can trade, or advice. The block is
  flagged `must_not_auto_execute: true`; the engine produces research context only.
- The empirical canonical probability is still right there next to the blend — compare them; the
  *gap* between them (count vs. path-aware) is itself the interesting signal.

### Companion code
`src/yearline_universe/blend_surface.py`, `features.py`, `cross_sectional.py`; the delivery write-up
`docs/phased_design/phase_07/README.md` §9; the concepts tutorial
`06_direct_horizon_classifier_and_blend_tutorial.md`.
