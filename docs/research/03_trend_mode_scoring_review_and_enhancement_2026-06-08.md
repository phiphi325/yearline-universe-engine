# 03 — Trend-mode scoring: V12 ↔ V13 review + enhancement plan

**Date:** 2026-06-08 · **Status:** investigation complete; **enhancements planned, not implemented**
(documentation-before-implementation, per request). Educational research only; not financial advice.

**Why now.** Doc `02`'s Part A fix correctly **withholds the retry-success composite for trend-mode names**
(their retry question is dormant). But the 2026-05-29 demo run showed **5–6 of 9 names are in trend mode**,
so once the composite is gated off, the **trend engine's own scoring becomes their primary surfaced
context.** That scoring is currently a hand-tuned, unvalidated MVP — it deserves the same evidence
discipline the repair side earned through Phase 8. This note reviews it (V12 Module G → V13 `trend.py`) and
lays out an enhancement plan.

---

## 1. How trend scoring works today

**Engine handoff (`semantic.py`).** A name is routed to the trend engine only when its replayed
`mode_state` is in `POST_CONFIRMATION_PROXY_STATES` — which contains exactly **one** state,
`"accepted_above_watch"`. Repair states route to the repair engine; **everything else → `unknown_or_transition`.**

**Scores (`trend.py`, `build_post_confirmation_trend_state_history`).** For each day above MA250 in a
post-confirmation run, four scores are computed, each as the `nanmean` of **clipped linear transforms** of
moving-average distance/slope/spread, drawdown-from-peak, RSI and ATR:

| score | rough construction (clipped to [0,1], then averaged) |
|---|---|
| `trend_quality_score` | (dist250+2)/10, (ma50−ma250 spread+1)/6, (ma250 slope+1)/4, 1−dd_peak/15 |
| `pullback_quality_score` | 1−dd_peak/15, (dist250+1)/8, (ma250 slope+0.5)/3, {dist50≥−5 ? 1 : 0.2} |
| `overextension_score` | dist50/12, dist250/25, (RSI−60)/20, ext_atr_mult/4 |
| `deterioration_risk_score` | dd_peak/15, (−dist50)/8, (−ma50 slope)/3, {dist250<1 ? 1 : 0} |

A rule machine (`_assign_state`) maps the scores to a state via hand-tuned thresholds
(`det≥0.70 → deterioration_watch`; `over≥0.70 & tQ≥0.50 → overextended`; `days≤20 & tQ≥0.45 → early_confirmation`;
`dd≥4 & pullQ≥0.45 → pullback_but_intact`; `tQ≥0.65 → healthy_trend`; else → early_confirmation).

**V12 parity.** This is a **faithful port of V12.11 Module G** ("shifts the objective from retry timing to
trend quality, pullback quality, overextension, and deterioration monitoring"). V12 shipped it as a
**`completed_mvp` descriptive** module — there is no V12 trend validation/calibration step that V13 dropped;
the gap is shared by both versions.

---

## 2. What the 2026-05-29 run reveals

| Ticker | Engine | State | `tQ` | `pullQ` | `overext` | `deterio` | days_conf |
|---|---|---|---:|---:|---:|---:|---:|
| AAPL | trend | overextended | **0.998** | **0.998** | 0.990 | 0.002 | 202 |
| XLK | trend | overextended | **1.000** | **1.000** | 0.903 | 0.000 | 41 |
| QQQ | trend | overextended | 0.986 | **1.000** | 0.818 | 0.000 | 41 |
| AMZN | trend | healthy | 0.938 | 0.968 | 0.633 | 0.026 | 36 |
| GOOGL | **unknown_or_transition** | (pullback) | 0.908 | 0.908 | 0.675 | 0.092 | 232 |
| NVDA | trend | pullback | 0.826 | 0.826 | 0.381 | 0.174 | 41 |

Four problems, all visible above:

1. **Saturation — the scores don't discriminate.** `trend_quality` and `pullback_quality` are pinned near
   the ceiling for every name (1.000 for XLK, 0.998 for AAPL). The clip-then-mean construction with fixed
   magic denominators (÷10, ÷6, ÷15) means any solidly-above-MA250 name maxes out. A "quality" score that
   reads ~1.0 for all six names **cannot rank them** — the opposite of its purpose.
2. **Collinearity.** `trend_quality ≈ pullback_quality` for *every* row (AAPL 0.998=0.998, NVDA 0.826=0.826,
   GOOGL 0.908=0.908) — they share the `1−dd_peak/15` and `dist250` terms and carry almost no independent
   information. Two of the four scores are nearly redundant.
3. **Coverage gap / orphaned names.** **GOOGL** is +37.8% above its yearline with perfectly valid trend
   scores (`tQ` 0.908) — yet it's routed to **`unknown_or_transition`**, not the trend engine, because its
   `mode_state` isn't the single accepted proxy (`accepted_above_watch`). Its rich trend context is computed
   but never surfaced as the active engine. The trend engine's coverage hinges on one mode-state label.
4. **Descriptive only — no validation, calibration, or gate.** Unlike the repair side (RS-1→RS-4: labelled
   outcome → discrimination → calibration → trust gate → gated surface), the trend scores are never checked
   against a **forward outcome**, never turned into a **probability**, and never **gated**. A consumer
   cannot size on them, and we have no evidence a high `trend_quality` actually predicts trend continuation.

Minor: `trend_context.distance_to_ma250_pct` comes back `null` in the latest context (computed in history,
not threaded into the live card); `early_confirmation` doubles as both a real state and the catch-all
`else`, conflating "genuinely early" with "none of the above."

---

## 3. Enhancement plan (design only)

**Guiding principle:** bring the trend side up to the repair side's discipline —
**capability → resolution → validation → calibration → gated surface** — and close the coverage gap so
trend-mode names get a *discriminating, trustworthy* context. Phased, quick-wins first.

### E0 — Coverage & hygiene *(quick; low risk)*
- **Fix the handoff** so clearly-above-MA250 names reliably route to the trend engine: expand
  `POST_CONFIRMATION_PROXY_STATES` (or route on the price condition `price_above_ma250` + a confirmation
  guard) so a name like GOOGL is no longer orphaned in `unknown_or_transition`. Make `unknown_or_transition`
  a genuine rarity, not a catch-all for trending names.
- **Thread `distance_to_ma250_pct`** (and peak-drawdown) into the live trend context; **split**
  `early_confirmation` from the `else` fallback (add an explicit `indeterminate_trend`).

### E1 — Score resolution *(quick)*
- **De-saturate** via **cross-sectional normalization**: rank each raw feature as a percentile/z-score
  **within the universe and regime** (reuse `cross_sectional.py`), so "overextended" means *relative to
  peers now*, not against a fixed ÷12 constant. Recalibrate any remaining absolute denominators to the
  empirical distribution so the scores actually spread across [0,1].
- **De-collinearize** `trend_quality` vs `pullback_quality`: give them disjoint feature bases (e.g. trend =
  slope/spread/structure; pullback = depth/recovery-from-dip/MA50 response), or drop one.

### E2 — Forward outcome & validation *(medium — the RS-1/RS-2 analog)*
- Define the **trend analog of the success label**, e.g. **`trend_continuation`** = price *stays above
  MA250 / no breakdown-to-repair* within H days (and/or **forward max drawdown-from-peak ≤ X%**, and/or
  time-to-`deterioration_watch`).
- Measure whether the current rule-based scores — and a small L2-logistic on the same features — **predict**
  that outcome under **episode-aware GroupKFold + leave-one-ticker-out** CV (exactly the Phase-8 protocol).
  Report AUC / Brier / MACE. This establishes the bar and whether the hand-tuned scores carry real signal.

### E3 — Calibration + trust gate *(medium — the RS-3 analog)*
- If a surface discriminates, calibrate it to a probability — **`P(trend continues ≤ H)`** and/or
  **`P(deterioration ≤ H)`** — with honest out-of-fold isotonic, and apply the **same trust gate**
  (AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50) and the **classifier↔baseline blend** lever. Expect the same
  thin-sample / shrinkage caveats (trend episodes are scarce too).

### E4 — Gated surfacing *(additive)*
- Add a **`trend_outlook_context`** block (the trend analog of `retry_success_context`): the **gated
  probability** + the (now-discriminating) rule-based scores as provenance. **Opt-in** (`surface_trend=True`),
  default **byte-identical**, never auto-executes — mirroring the Phase-7/8 wiring discipline.

---

## 4. Sequencing, priority & constraints

- **E0 + E1 are quick wins** (coverage + resolution) and can ship alongside / right after doc `02`'s Part A
  fix — they make the trend context immediately more useful for the majority of the universe.
- **E2 → E4** are a proper new **track** (propose as planner *Track C — trend outlook* / a Phase 11),
  data-gated like everything else. Until E2 shows discrimination, the trend probability stays *capability,
  not consumer*.
- **Thin-sample reality:** trend episodes are scarce (a handful of post-confirmation runs per ticker), so
  E2/E3 will lean on pooling + shrinkage and should re-validate walk-forward — the same honesty the repair
  side carries.
- **Constraints:** V12 material (`docs/uploaded/`) stays gitignored — this note describes methodology at the
  design level only. Every surfaced number remains a research overlay (`must_not_auto_execute`).

### Done criteria
- **E0/E1:** re-run `docs/reports/demo/run_asof_2026-05-29.py` → trend scores **spread** (resolution
  restored), GOOGL routes to the **trend engine** (no `unknown_or_transition` for clearly-trending names),
  `distance_to_ma250_pct` populated in the trend context.
- **E2+:** a gated `trend_outlook_context` that either **passes** the trust gate or **honestly abstains** —
  never an un-validated probability surfaced as if trustworthy.

*Companion: `docs/research/02_composite_gating_fix_and_igv_coverage_2026-06-08.md` (Part A — why trend
context matters more now), `src/yearline_universe/trend.py` + `semantic.py` (the code under review),
`docs/reports/demo/` (the run that surfaced this). Educational research only; not financial advice.*
