# Track D — Trend-outlook build plan (TO-0 … TO-4)

*Spec-grade execution plan to make the **post-confirmation trend engine** discriminating and trustworthy.
Derived from `docs/research/03_trend_mode_scoring_review_and_enhancement_2026-06-08.md` (the V13/V12 review)
and `docs/research/04_trend_mode_sota_review_2026-06-08.md` (state-of-the-art). Educational research; not
financial advice.*

> **Delivered as `docs/phased_design/phase_11/`.** TO-0…TO-4 are the sub-PRs within phase 11 (the way Phase
> 7 held PR-A…E and Phase 8 held RS-1…RS-4); each updates `phase_11/README.md` + `phase_11/artifacts/` per
> the phase convention and cross-links back to this spec.

**Goal.** Today the trend engine (`trend.py`, a faithful port of V12 Module G) emits four **hand-tuned,
saturated, collinear, never-validated** scores and a rule-assigned state; the handoff
(`semantic.assign_active_engine`) routes only one mode-state (`accepted_above_watch`) to it, orphaning
clearly-trending names (GOOGL → `unknown_or_transition`). With Track A's Part A fix withholding the
retry-success composite for trend names, these scores are the **primary surfaced context for the majority
of the universe** — so they must earn the same discipline the repair side did: **capability → resolution →
validation → calibration → gated surface.** TO-0…TO-4 apply the SOTA remedies from research 04 on top of the
engine's **existing** gate machinery (proper scores; reliability−resolution−uncertainty; OOF isotonic;
classifier↔baseline blend) — and surface a trend probability **only if it earns a gate**.

**Invariants (every milestone).** Capability-before-consumer; output-changing steps gated + reviewed +
**byte-identical-when-off**; additive schema; no-hardcoded-ticker; branch→PR→squash; tests via
`scripts/run_tests.sh`; honest trust-gating / abstention; thin-sample reality ⇒ pool + shrink + walk-forward.

---

## TO-0 — Coverage & hygiene *(quick; low risk; output-additive)*
- **Objective.** Stop orphaning trending names and clean up state semantics, so the trend engine reliably
  covers above-MA250 names before we change any scores.
- **Deliverable / modules.**
  - `semantic.py`: widen the trend handoff — route to `post_confirmation_trend_engine` on the **price/regime
    condition** (`price_above_ma250` + a confirmation guard) rather than the single `accepted_above_watch`
    proxy; reserve `unknown_or_transition` for genuinely ambiguous bars. (Coordinate with TO-2's regime
    probability once it exists.)
  - `trend.py` / `context_export.py`: thread `distance_to_ma250_pct` (and peak drawdown) into the **live**
    trend context (currently `null`); split `early_confirmation` from the catch-all `else` (add
    `indeterminate_trend`).
- **Acceptance.** On the 2026-05-29 fixture, GOOGL routes to the trend engine (no `unknown_or_transition`
  for clearly-trending names); trend context carries a non-null distance; states no longer conflate "early"
  with "fallback." Envelope diff is **additive** (no regression on repair/occurrence blocks).
- **Tests.** `tests/test_semantic.py` (handoff coverage), `tests/test_trend.py` (state split + distance
  threading). **Risks.** Don't mis-route a true repair state into trend — gate strictly on confirmed-above.

## TO-1 — Score resolution: volatility-normalized + cross-sectional features *(quick)*
- **Objective.** Fix the **saturation** and **collinearity** so the scores actually rank trending names.
- **Deliverable / modules.**
  - `trend.py`: replace the fixed clip-then-mean denominators with **(a) volatility normalization** (scale
    distances/slopes by ATR/realized-vol — the TSMOM volatility-scaling lesson: un-normalized trend signals
    aren't comparable across names/time) and **(b) cross-sectional percentile/z-score** within the
    universe+peer group (reuse `cross_sectional.py` / `pooling.py`) so "overextended" means *relative to
    peers now*, not vs a constant.
  - **De-collinearize** into disjoint axes: a **strength/persistence** axis (regression slope + R²
    "tightness", Kaufman efficiency ratio, ADX, and a **Hurst** persistence estimate with significance
    guard) vs a **pullback/depth** axis (drawdown-from-peak, MA50 response, recovery shape). Optionally add
    an **adaptive multi-lookback** strength in [−1,1] (ATSMOM-style) instead of one window.
- **Acceptance.** Re-run the 2026-05-29 report → trend scores **spread** across [0,1] (resolution restored;
  no more 0.99–1.0 clustering); `corr(trend_quality, pullback_quality)` materially drops. Hurst computed
  only where ≥~150 bars and flagged when its CI straddles 0.5 (per research 04's caveat). Still
  **descriptive / capability** — not yet a probability.
- **Tests.** `tests/test_trend.py`: monotonic feature responses, normalization correctness, no saturation on
  synthetic strong/weak trends, Hurst guard. **Risks.** Hurst variance/heavy-tail bias — use DFA + bootstrap
  significance, treat as a soft feature, never a standalone signal.

## TO-2 — Regime probability (HMM / changepoint) *(medium)*
- **Objective.** Replace the deterministic mode-state gate with a **calibrated regime probability**, both to
  drive the TO-0 handoff and as a trend feature.
- **Deliverable / modules.**
  - `src/yearline_universe/regime.py` (new): a parsimonious **Markov-switching / Gaussian-mixture HMM** (or a
    changepoint detector — BOCPD/PELT) on returns+volatility → `P(bull/trend)`, `P(turbulent)`,
    and a changepoint flag; pooled-fit, ticker-applied (compute-once like the calibration/blend models).
- **Acceptance.** Regime probabilities are out-of-sample (no look-ahead), stable, and improve handoff
  coverage; ablation vs the rule-based handoff on the fixture. **Capability — not surfaced** until TO-4.
- **Tests.** `tests/test_regime.py`: no leakage, regime-prob shape, turning-point recall on a labelled
  fixture. **Risks.** HMMs are data-hungry/identifiability-sensitive — keep states few, pool, regularize.

## TO-3 — Forward trend label + validation *(medium — the RS-1/RS-2 analog)*
- **Objective.** Establish the **forward outcome** and whether the TO-1/TO-2 features actually predict it.
- **Deliverable / modules.**
  - `src/yearline_universe/trend_labels.py` (new): leakage-safe forward labels — **`trend_continuation`**
    (stays above MA250 / no breakdown-to-repair within H), and/or **forward max drawdown-from-peak ≤ X%**,
    and/or **time-to-`deterioration_watch`**. Episode = a post-confirmation run (`above_run_id`).
  - `src/yearline_universe/trend_models.py` (new): L2-logistic (+ optional gradient-boosted trees) on the
    TO-1/TO-2 features, validated with **episode-purged GroupKFold + leave-one-ticker-out** (the Phase-8
    protocol), head-to-head vs the rule-based scores and the base rate. Report AUC / Brier / **resolution**.
- **Acceptance.** Honest LOTO metrics; "beats the bar" = LOTO AUC > 0.5 **and** Brier-lift over base > 0
  **and** positive **resolution**. Report the honest negative if the hand-tuned scores don't discriminate.
  **Capability — not surfaced.**
- **Tests.** `tests/test_trend_labels.py` (label/censoring correctness), `tests/test_trend_models.py`
  (CV shape, signal on a focused feature set). **Risks.** Trend episodes are scarce + trend-following tests
  are low-power (research 04) — pool, shrink, and prefer abstention.

## TO-4 — Calibration + trust gate + gated surfacing *(the RS-3/RS-4 analog)*
- **Objective.** Turn a discriminating surface into a **calibrated, gated** probability and surface it
  additively — or abstain.
- **Deliverable / modules.**
  - Calibrate to **`P(trend continues ≤ H)`** and/or **`P(deterioration ≤ H)`** with **OOF isotonic**
    (reuse `success_calibration`'s primitives); blend classifier↔rule/empirical baseline by OOF Brier.
  - **Gate** = AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50 **plus a resolution floor** (the SOTA-informed addition from
    research 04 + the `phase_08/reliability` shrinkage lesson: a probability that passes MACE only by
    collapsing to the base rate has zero resolution and must NOT pass). Optionally CRPS for a forward-
    drawdown *distribution*.
  - `context_export.py`: a new top-level **`trend_outlook_context`** block (the trend analog of
    `retry_success_context`) — the gated probability + the TO-1 scores + regime prob as provenance.
    **Opt-in** (`surface_trend=True`); **default off ⇒ envelope byte-identical**; `must_not_auto_execute`.
- **Acceptance.** The surface either **passes** the gate (incl. resolution) and is surfaced opt-in, or
  **abstains**; default envelope byte-identical; full per-file suite green; the 2026-05-29 report re-run
  shows a trustworthy (or honestly-absent) trend outlook for the trend-mode names.
- **Tests.** `tests/test_trend_surface.py` (gate incl. resolution floor, blend math, byte-identical-when-off,
  abstention), `tests/test_context_export.py` (optional schema key, parity). **Risks.** Over-surfacing a
  shrinkage-calibrated probability — the resolution floor is the guard.

---

## Sequencing, priority & coupling
- **TO-0 + TO-1 are quick wins** — ship right after Track A's Part A fix (both touch the trend-mode path);
  they make the trend context immediately more useful for the ~5–6 trend names without claiming a probability.
- **TO-2 → TO-4** are the validation/calibration arc (a proper phase), **data-gated** like Track C; until
  TO-3 shows discrimination **and** resolution, the trend probability stays **capability, not consumer**.
- **Coupling:** TO-0's handoff should consume TO-2's regime probability once available; TO-4 reuses the
  Track A gate/calibration/blend primitives verbatim (one source of truth for trust).
- **Priority:** **MEDIUM** — above multi-sector (Track C, deferred) but behind in-flight Track A finish and
  Track B; TO-0/TO-1 are cheap enough to interleave now.

### Done criteria (track)
1. No clearly-trending name is orphaned in `unknown_or_transition`; trend context carries distance (TO-0).
2. Trend scores **spread** and are de-collinearized; cross-sectionally comparable (TO-1).
3. A calibrated regime probability exists and improves the handoff (TO-2).
4. A forward trend label + honest LOTO validation exist; signal (or its absence) is reported (TO-3).
5. A **gated** `trend_outlook_context` is surfaced opt-in **only if** it clears AUC/MACE/**resolution**/n —
   else abstains; default byte-identical (TO-4).

*Cross-links: research `03`/`04`; `src/yearline_universe/trend.py`, `semantic.py` (under review);
`phase_08/` (the gate/calibration/reliability machinery reused here); `phase_08/reliability/` (the
resolution-vs-shrinkage lesson that motivates TO-4's resolution floor). Educational research only.*
