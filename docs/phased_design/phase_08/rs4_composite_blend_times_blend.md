# RS-4 composite — *blend × blend*, both gate-passing (not a raw classifier)

**Phase 8 · RS-4 clarification** · Educational research only; not financial advice.

A short, load-bearing clarification about what the surfaced composite actually multiplies:

$$P(\text{successful reclaim} \le H) \;=\; \underbrace{P(\text{retry} \le H)}_{\text{occurrence}} \;\times\; \underbrace{P(\text{success}\mid\text{retry})}_{\text{success}}$$

Both factors are **gate-passing classifier↔empirical blends** — *not* raw classifier outputs and *not*
bare empirical estimates. This matters because a raw classifier is discriminating but **over-confident**
(it would inflate the composite at the extremes), while a bare empirical estimate is calibrated but
**flat** (it would wash out the signal). The blend is the surface that cleared the trust gate; the
composite rides it on *both* axes.

---

## Clarification 1 — there are TWO blends, and the composite uses both

| factor | which blend | weight | gate it carries | module |
|---|---|---|---|---|
| `P(success │ retry)` | **RS-3 success blend** — RS-2 success classifier ↔ RS-1 empirical success estimator | w = 0.5 (OOF-Brier selected) | RS-3 trust gate (AUC 0.702, MACE 0.036) | `success_surface.py` / `success_calibration.py` |
| `P(retry ≤ H)` | **Phase-7 occurrence blend** — direct-horizon classifier ↔ empirical completed-path estimator | w by OOF Brier per horizon | Phase-7 per-horizon gate | `blend_surface.py` |

So the surfaced composite is **blend × blend**. Neither factor is a raw classifier point estimate; each
is the convex blend that the relevant phase validated and gated.

**Why the occurrence factor uses the Phase-7 blend (and not the older isotonic-only surface).** The
occurrence side has two candidate calibrated surfaces, and they disagree at long horizons (pooled,
leave-one-ticker-out):

| H | isotonic-only gate (Phase 4) | Phase-7 blend gate | composite uses |
|---|---|---|---|
| 10 | ✅ MACE 0.055 | ✅ AUC 0.833, MACE 0.045 | blend |
| 20 | ✅ MACE 0.047 | ✅ AUC 0.806, MACE 0.068 | blend |
| 40 | ✅ MACE 0.056 | ✅ AUC 0.807, MACE 0.054 | blend |
| 60 | ❌ MACE **0.130** | ✅ AUC 0.792, MACE **0.058** | blend |

The isotonic-only surface's calibration degrades monotonically with horizon and **fails the gate at 60d**
(saturation near P≈1 + per-step hazard error compounding; the OOF isotonic even overfits there, 0.130 >
the 0.109 raw). Phase 7 already fixed this: averaging the discriminating classifier with the empirical
estimator tempers the long-horizon over-confidence, so the **blend passes at every horizon** with higher
AUC throughout. RS-4 therefore composes against the **blend's gate-passing occurrence probability** where
it passes, falling back to empirical + the isotonic gate otherwise. Each horizon records which surface
backed it in `occurrence_surface` (`phase7_blend` or `empirical_isotonic`).

**Consequence:** the 60d composite is **surfaced, not withheld** — because a gate-passing occurrence
surface *does* exist at 60d (the blend), even though the isotonic-only one doesn't. Earlier wiring that
read only the isotonic gate spuriously withheld 60d; reading the Phase-7 blend is the fix.

---

## Clarification 2 — single source of truth (RS-4 doesn't re-decide anything)

RS-4 is **consumer wiring**, not a new model. It does not re-select weights or re-define gates; it *reads*
the surfaces the earlier phases already validated:

- The **success** weight + gate come straight from `evaluate_success_calibration_gate` (RS-3). RS-4 stores
  `blend_weight_classifier` and the blend surface's gate verbatim. The only `0.5` literal in RS-4
  (`_DEFAULT_SUCCESS_W`) is a defensive fallback used **only** if the gate evaluator returns no weight —
  never in the normal path.
- The **occurrence** probability + gate per horizon come from the Phase-7 blend overlay (`blend_surface.py`),
  falling back to the Phase-4 isotonic trust gate.

Because the decisions live upstream, RS-4 inherits any future change automatically: if RS-3's weight
selection evolves (e.g. the deferred *maximize-resolution-subject-to-bootstrap-p95-MACE* rule from the
`reliability/` analysis), or the occurrence blend re-weights, RS-4 picks it up with **no code change**.
One place decides "what is the trustworthy surface"; RS-4 just multiplies the two trustworthy surfaces.

---

## What this is *not*

- **Not** `raw_classifier(success) × raw_classifier(occurrence)` — that would compound two over-confident
  rankers into a doubly-miscalibrated number.
- **Not** an un-gated number dressed up as trusted — a horizon's composite is surfaced
  (`surfaced_probability`) only where **both** the occurrence gate **and** the success gate pass; otherwise
  the product is retained but labelled diagnostic (`surfaced_probability: null`).
- **Not** a trade. The composite is a research-overlay probability; the engine must not auto-execute.

## Worked number (MSFT, real universe)

`P(success │ retry)` = 0.5 × 0.00 (classifier) + 0.5 × 0.347 (empirical) = **0.174** (RS-3 blend, gate ✅).
Occurrence (Phase-7 blend): 10d 0.218, 20d 0.306, 40d 0.548, 60d 0.696. Composite (blend × blend), all
surfaced via `phase7_blend`:

| H | P(retry≤H) (blend) | × P(success│retry) | = P(reclaim≤H) |
|---|---|---|---|
| 10 | 0.218 | 0.174 | **0.038** |
| 20 | 0.306 | 0.174 | **0.053** |
| 40 | 0.548 | 0.174 | **0.095** |
| 60 | 0.696 | 0.174 | **0.121** |

See `README.md` §11.2 and `artifacts/rs4_success_overlay_example.json` for the full live block, and
`reliability/` for why the success blend's level is shrinkage-calibrated (trust the ordering; size gently
on the level).
