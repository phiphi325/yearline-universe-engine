# 09 — MSFT, one state end-to-end: the retry-**success** overlay + the reclaim composite (Phase 8, worked example)

**Audience:** anyone on the project. **Running example:** **MSFT**, a live low-readiness repair state on the
real 9-ticker universe. **Companion code:** `success_surface.py`, `blend_surface.py`,
`docs/phased_design/phase_08/` (`run_rs4_success_overlay.py`, `artifacts/rs4_success_overlay_example.json`).

> Educational research only; not investment advice. Every number below is a research-overlay probability,
> not a trade. The block carries `must_not_auto_execute: true`.

Tutorial 08 covered the *why*; this one walks one real state through every number, the way tutorial 07 did
for retry *occurrence*. The question Phase 8 answers for this state:

> *If MSFT attempts to reclaim its yearline (MA250) now, will the attempt **hold**? And what's the joint
> chance it both retries within H days **and** that attempt succeeds?*

---

## 1. The two ingredients of `P(success │ retry)`

RS-4 surfaces the **RS-3 blend**: `w · classifier + (1 − w) · empirical`, with `w = 0.5` (the gate-passing
weight). For MSFT's live readiness state:

| component | value | what it is |
|---|---|---|
| `classifier_probability` | **0.00** | the RS-2 success classifier on this state's path + cross-sectional features |
| `empirical_probability` | **0.347** | the RS-1 empirical success rate of similar historical attempts (`scope = group_transition`, n = 26) |
| `blend_weight_classifier` | 0.5 | OOF-Brier-selected |
| **`p_success_given_retry`** | **0.174** | `0.5 × 0.00 + 0.5 × 0.347` |

**Read the 0.00 honestly.** The classifier, *fitted on all attempts and applied to this single live row*,
scores it at ~0.00 — an extreme call (this state looks unfavorable to it). The **blend tempers it to
0.174**. This is exactly the shrinkage behavior tutorial 08 §4 measured: the gate was validated on
out-of-fold predictions, so the **surfaced blend (0.174) is the trustworthy number; the raw classifier
point (0.00) is not**. Note 0.174 sits below the 0.352 base rate — the blend is saying "this particular
state looks somewhat *below*-average for holding," but gently (it can't, by construction, say 0.00).

**The gate it carries** (RS-3, on leave-one-ticker-out OOF): **AUC 0.702, MACE 0.036 → `gate_passed:
true`.** So `P(success │ retry) = 0.174` is allowed to be *shown*.

---

## 2. The occurrence factor — the Phase-7 blend (not the isotonic-only surface)

The composite needs `P(retry ≤ H)`. RS-4 uses the **Phase-7 occurrence blend** (tutorial 06/07), which is
the gate-passing occurrence surface at every horizon — including 60d, where the isotonic-only calibration
fails (MACE 0.130) but the blend passes (MACE 0.058). MSFT's blended occurrence probabilities (the same
numbers tutorial 07 walked through):

| H | `P(retry ≤ H)` (Phase-7 blend) |
|---|---|
| 10 | 0.218 |
| 20 | 0.306 |
| 40 | 0.548 |
| 60 | 0.696 |

Each horizon records `occurrence_surface: phase7_blend` so you can see which surface backed it.

---

## 3. The composite — *blend × blend*

$$P(\text{reclaim} \le H) = P(\text{retry} \le H)\times P(\text{success}\mid\text{retry})$$

| H | `P(retry ≤ H)` (blend) | × `P(success│retry)` | = `P(reclaim ≤ H)` | both gates pass? | surfaced |
|---|---|---|---|---|---|
| 10 | 0.218 | 0.174 | **0.038** | ✅ ✅ | 0.038 |
| 20 | 0.306 | 0.174 | **0.053** | ✅ ✅ | 0.053 |
| 40 | 0.548 | 0.174 | **0.095** | ✅ ✅ | 0.095 |
| 60 | 0.696 | 0.174 | **0.121** | ✅ ✅ | 0.121 |

All four horizons surface because **both** gates pass at each (occurrence via the Phase-7 blend, success
via RS-3). If, say, the occurrence gate had failed at 60d (as the *isotonic-only* surface does), that row
would show `surfaced_probability: null` and be labelled diagnostic — the **dual gate** never lets an
un-gated input through.

**What it means in words:** *over the next ~40 trading days, the joint chance MSFT both retries its
yearline and that attempt holds is ≈ 9.5%* — dominated here by the modest success probability (0.174), not
the occurrence probability (0.548). The composite makes the bottleneck explicit: for this state, *holding*
is the hard part, not *touching*.

---

## 4. The gated envelope block (abridged)

`run_universe_pipeline(..., surface_success=True)` attaches this to MSFT's envelope (default off ⇒ absent):

```jsonc
"retry_success_context": {
  "schema": "v13_phase8_retry_success_overlay",
  "policy": "gated_success_overlay_empirical_and_occurrence_remain_canonical",
  "p_success_given_retry": 0.174,
  "classifier_probability": 0.0, "empirical_probability": 0.347,
  "empirical_reference_scope": "group_transition", "empirical_reference_n": 26,
  "blend_weight_classifier": 0.5, "base_rate": 0.352,
  "gate": { "passed": true, "auc": 0.702, "mace": 0.036, "n": 162 },
  "gate_passed": true,
  "successful_reclaim_within_horizon": {
    "40": { "p_retry_within_h": 0.548, "occurrence_surface": "phase7_blend",
            "p_success_given_retry": 0.174, "p_successful_reclaim_within_h": 0.095,
            "occurrence_gate_passed": true, "success_gate_passed": true,
            "both_gates_passed": true, "surfaced_probability": 0.095 },
    "60": { "p_retry_within_h": 0.696, "occurrence_surface": "phase7_blend",
            "p_successful_reclaim_within_h": 0.121, "both_gates_passed": true,
            "surfaced_probability": 0.121 }
  },
  "caveats": [
    "Thin sample (low-hundreds attempts); the gate PASS is high-variance — re-validate walk-forward.",
    "The blend's calibration is largely base-rate shrinkage; trust the ranking, size gently on the level."
  ],
  "must_not_auto_execute": true
}
```

---

## 5. How to read it (and how not to)

- **Do** use the *ordering*: across the 9-ticker run, `P(success │ retry)` ranges 0.15 (META) → 0.32
  (NVDA). That ranking is the gate-validated signal (AUC 0.70). NVDA's state looks materially more likely
  to hold than META's.
- **Don't** over-trust the *level*. Because the blend is shrinkage-calibrated (tutorial 08 §4), 0.174 means
  "somewhat below the ~35% base rate," not a precise 17.4%. Size sizing curves gently in this probability.
- **Do** let the composite surface the bottleneck: a high occurrence × a modest success says "it'll
  probably *touch*, but *holding* is the coin-flip-or-worse" — useful context an occurrence-only number
  hides.
- **Don't** read a withheld horizon (`surfaced_probability: null`) as zero — it means "not gate-verified
  here," i.e. *abstain*, not *no*.

---

## 6. Reproduce

```bash
# the full live overlay on the real universe (writes the example block + per-ticker summary)
python3 docs/phased_design/phase_08/run_rs4_success_overlay.py
```

See `08_retry_success_overlay_tutorial.md` for the concepts, `phase_08/README.md` §11 for the wiring, and
`phase_08/reliability/` for why the success level is shrinkage-calibrated.
