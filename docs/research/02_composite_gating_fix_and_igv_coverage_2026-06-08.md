# 02 — RS-4 composite gating fix + IGV coverage gap (investigation & plan)

**Date:** 2026-06-08 · **Trigger:** the point-in-time demo report
(`docs/reports/demo/yearline_context_report_2026-05-29.md`) surfaced two issues when run as of 2026-05-29.
**Status:** investigation complete. **Part A — IMPLEMENTED (2026-06-09):** `context_export` now gates
`retry_success_context` on `hazard_active` (the repair engine being active), so trend-mode /
`unknown_or_transition` names no longer surface a composite off a non-current occurrence; test
`tests/test_success_surface.py::test_success_overlay_withheld_when_repair_engine_inactive`; validated by
re-running `docs/reports/demo/`. **Part B — open** (IGV coverage; lower priority). Educational research only.

This note (a) lays out the plan to fix the **trend-mode composite gap** and (b) records the **root-cause
investigation of the IGV coverage gap**. Both were found by running the engine point-in-time — neither is
visible on a single latest-bar run.

---

## Part A — Trend-mode composite gap (fix plan)

### A.1 Symptom
For names **above** their yearline (trend regime), the envelope's `retry_hazard_context` correctly reports
the retry question as **dormant** (`active: false`, `p_retry_within_*: null`) — yet `retry_success_context`
still surfaced a **composite**. Example (AAPL, 2026-05-29, +24% above MA250):

```jsonc
"retry_hazard_context": { "active": false, "p_retry_within_40d": null },   // dormant — correct
"retry_success_context": {
  "successful_reclaim_within_horizon": {
    "40": { "p_retry_within_h": 0.896, "occurrence_surface": "phase7_blend",
            "occurrence_gate_passed": true, "surfaced_probability": 0.183 } } }  // ← surfaced anyway
```

That `0.896` is **not** AAPL's current chance of touching its yearline (it's 24% above it). It's the pooled
occurrence blend scored on AAPL's most-recent **live-transition** state, which is stale relative to the
current trend regime. Surfacing a composite off it is misleading. (5 trend names + GOOGL are affected.)

### A.2 Root cause — inconsistent gating between the two overlays
The occurrence **blend block** and the **success overlay** gate on *different* conditions:

| overlay | gated on | where |
|---|---|---|
| occurrence blend BLOCK (`direct_classifier_blend`) | **`hazard_active`** = `active_engine == "repair_retry_hazard_engine"` | `context_export.build_statistical_context_envelope` |
| RS-4 success overlay + composite | **`hazard_context.available`** (the hazard *layer* ran) + a non-empty `panel[is_live_transition]` | `hazard.run_hazard_layer` |

So when a trend-mode name still has a (recently-resolved) live transition, the success block runs and the
composite multiplies `P(success│retry)` by a **phantom** occurrence the envelope itself does not surface.
The two overlays disagree about whether occurrence is "live."

### A.3 Principle
> The composite's occurrence factor must be the **same occurrence the envelope actually surfaces**. If
> `retry_hazard_context` is dormant (no active, gated occurrence), the composite has **no** occurrence
> input and must be **withheld** — never multiply by a phantom.

### A.4 Fix
1. **Gate the success overlay on the repair engine being active**, mirroring the blend block's
   `hazard_active` gate. Concretely, thread a `repair_active = (active_engine == "repair_retry_hazard_engine")`
   signal into the success-surfacing decision:
   - Smallest, lowest-risk: in `ticker_pipeline.run_ticker_pipeline`, pass
     `success_context = hz.get("success_context") if (surface_success and repair_active) else None`
     (the function already knows `active_engine`; it already does the analogous conditional for
     `blend_context`). Result: trend-mode names get **no** `retry_success_context` block.
   - Deeper/cleaner (preferred): also gate the computation inside `run_hazard_layer`'s success block on a
     repair-active flag derived from the live diagnostic, so we don't compute a discarded composite.
2. **Composite withholding rule** (defense in depth): inside `build_retry_success_context`, only mark a
   horizon `surfaced` when its occurrence input is the **live, gate-passing** surface — i.e. require
   `occurrence_probs[h]` to come from the active occurrence (blend/empirical) the envelope surfaces, not a
   stale live-transition score. If occurrence is dormant, set `surfaced_probability: null` with a reason.
3. **Optional product choice:** for trend-mode names, either (i) omit `retry_success_context` entirely
   (simplest, recommended), or (ii) surface *only* a clearly-labelled **hypothetical** `P(success│retry)`
   ("conditional on a future retry; no retry currently pending") with **no** composite. Pick (i) unless a
   consumer needs the standing conditional.

### A.5 Tests & validation
- **Unit:** a repair-inactive (trend) live state ⇒ no surfaced composite (and, per choice, no block); a
  repair-active state ⇒ composite still surfaces. Add to `tests/test_success_surface.py` /
  `tests/test_context_export.py`.
- **Integration:** re-run `docs/reports/demo/run_asof_2026-05-29.py`; **expected after fix** — only the
  repair-mode names surface composites (MSFT, META; IGV per Part B); the five trend rows + GOOGL no longer
  show ⚠️ composites. Update the report's §3/§5/§6.1 accordingly.
- Full per-file suite stays green (byte-identical-when-off preserved).

### A.6 Priority
**Highest** of the two — it produces *misleading surfaced numbers* for ~6 of 9 names. Do this first.

---

## Part B — IGV coverage gap (investigation)

### B.1 Symptom
IGV is **repair-active** in the envelope (below MA250; `p_retry_within_40d = 0.84`) but produced **no
occurrence blend** and **no success overlay** (`retry_success_context` absent).

### B.2 Diagnostic (as of 2026-05-29; `/tmp/diag_igv.py`, contrast = META)

| signal | IGV | META |
|---|---|---|
| `hazard_context.available` (live layer) | **False** | True |
| `panel[is_live_transition]` rows | **0** | 31 |
| occurrence blend available | ❌ | ✅ |
| success overlay available | ❌ | ✅ |
| completed attempts in success table | **6** (4✓ / 2✗) | 19 |
| `next_attempt_pending` across attempts | **all False (0 open)** | has open |
| cross-sectional features present | ✅ 4,378 rows, through 2026-05-29 | ✅ |
| in the success **training** table | ✅ (6 rows) | ✅ |

### B.3 Root cause
**IGV has no currently-open retry transition.** All 6 of its yearline attempts are **resolved**
(`next_attempt_pending = False` for every row), so the hazard daily panel marks **zero** `is_live_transition`
rows. The live overlays — the occurrence blend *and* the success overlay — both key off
`panel[is_live_transition]`; with none, neither can attach. Hence no blend, no success, no composite.

The envelope nevertheless shows IGV repair-active with `p_retry_within_40d = 0.84` because **that number
comes from a different path**: the **replay/semantic** layer scores each day's gated hazard from the
*price regime* (below MA250), independent of whether a formal attempt transition is open. So:

- **Regime/replay occurrence** (price below the yearline) → fires → `pR40 = 0.84` in the envelope.
- **Live-transition overlays** (blend + success) → need an *open attempt* to score → **dark** for IGV.

This is **not** a data-quality gap (cross-sectional features are present and current) and **not** a model
gap (IGV *is* in the success training table). It is a **live-transition coverage** gap: a below-yearline
*regime* with no open *attempt* for the overlays to score. IGV is also the **thinnest-history** name in the
universe (6 attempts vs 14–27 for the others), so even with a live transition its success anchor would be
shrinkage-dominated.

### B.4 Secondary finding — misleading skip warning (minor bug)
When the live overlays are skipped because `hazard_context.available` is False / there's no live
transition, `blend_context` and `success_context` retain their **default stub** warnings
(`"blend_not_requested_pass_surface_blend_true"`, `"success_not_requested_pass_surface_success_true"`) —
even though `surface_blend=True` / `surface_success=True` were passed. The warning says *"not requested"*
when the real reason is *"no live transition to score."*

**Fix:** when the guard fails despite the surface flag being set, overwrite the warning with the true
reason, e.g. `"no_live_transition"` / `"hazard_context_unavailable"`. Low effort; improves diagnosability.

### B.5 Recommendations
1. **Coherence labelling (do):** when the regime/replay path surfaces an occurrence (repair-active, `pR`
   present) but no live transition exists, the envelope should *explicitly state* why the overlays are
   absent (the B.4 warning fix), so a consumer reads "no open attempt to score," not "feature disabled."
2. **Design question (decide, don't rush):** should a below-yearline regime *without* an open transition
   instantiate a synthetic live transition so the overlays can score it? This may be **intended** (the
   overlays deliberately only score open attempts). Resolve explicitly rather than by accident.
3. **Low-coverage flag (do):** mark IGV (6 attempts) as near the sample floor; its success surface, when
   present, will be heavily shrinkage-anchored. Consider a per-name `coverage` field in the envelope.

### B.6 Priority
**Lower** than Part A. IGV currently **abstains correctly** (it surfaces no untrustworthy number); the only
real defect is the *misleading warning string* (B.4) and the *missing explanation* (B.5.1). The deeper
"should we synthesize a live transition" question (B.5.2) is a design decision, not a bug.

---

## Sequencing

1. **Part A** — gate the success overlay/composite on the repair engine being active (correctness; affects
   ~6 names). Tests + re-run the 2026-05-29 report.
2. **Part B.4 + B.5.1** — replace the stale skip warning with the true reason; label overlay-absence in the
   envelope.
3. **Part B.5.2** — design discussion on synthetic live transitions for below-yearline-no-open-attempt
   regimes (no code until decided).

**Done criteria:** re-running `docs/reports/demo/run_asof_2026-05-29.py` shows (i) no composites for
trend-mode names, (ii) IGV reporting an explicit "no live transition" reason rather than a stale
"not_requested" warning, (iii) MSFT/META unchanged, (iv) full suite green.

*Companion: `docs/reports/demo/` (the run that surfaced these), `phased_design/phase_08/` (RS-4 +
`rs4_composite_blend_times_blend.md`). Educational research only; not financial advice.*
