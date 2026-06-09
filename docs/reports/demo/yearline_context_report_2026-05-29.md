# Yearline Universe — Point-in-Time Statistical Context Report

**As of:** end of day **2026-05-29** (Friday) · **Universe:** `mvp_software_like` (9 tickers) ·
**Run:** pooled hazard + calibration + occurrence blend + success overlay, all enabled.

> **Point-in-time discipline.** This run sees price data **only through 2026-05-29** (every ticker's last
> bar is 2026-05-29; nothing after that Friday was available to the model). All states, probabilities, and
> gates below are what the engine would have produced *that evening*.
>
> **What this is.** A statistical-context **evidence overlay** — never a trade. Every block carries
> `must_not_auto_execute: true`. Educational research only; not financial advice.

---

## 1. Executive summary

The 9-name universe split into **three regimes** on 2026-05-29:

- **Repair (below the yearline, a retry is *live*): MSFT, META, IGV.** These are the names where the
  retry-occurrence and retry-success questions are directly meaningful.
- **Trend (above the yearline, in a post-confirmation uptrend): AAPL, AMZN, NVDA, QQQ, XLK.** The retry
  question is **dormant** here (`retry_hazard_context.active = false`).
- **Transitional: GOOGL** (`unknown_or_transition`, +37.8% above its yearline).

**The standout: MSFT.** A deep repair (drew down to ~23% below its MA250) that had rallied back to **just
−3.0%** below the yearline by 2026-05-29 (it jumped +5.4% that day). The model reads it as **very likely to
*touch*** the yearline soon — `P(retry ≤ 40d) ≈ 0.89` (blend) — but **conditionally unlikely to *hold***:
`P(success │ retry) ≈ 0.14`. The composite **`P(successful reclaim ≤ 40d) ≈ 0.12`** makes the bottleneck
explicit: *touching* isn't the hard part, *holding* is.

**Cross-sectional success ranking** (gated `P(success │ retry)`, the order is the gate-validated signal):
**NVDA 0.29 > XLK 0.25 ≈ QQQ 0.25 > AAPL 0.20 ≈ AMZN 0.20 > GOOGL 0.18 > META 0.15 > MSFT 0.14** (IGV: not
available). All sit at/below the ~0.35 historical base rate — the expected footprint of a
shrinkage-calibrated blend (trust the **ordering**; size gently on the **level** — see §6).

**Two honest gaps surfaced by this run** (details in §6): (a) the success **composite** is being surfaced
for **trend-mode** names even though their occurrence question is dormant — read those composites as N/A;
(b) **IGV** has no success surface at all.

---

## 2. How to read this report

The engine answers two *different* questions and multiplies them:

| quantity | meaning | surface used here |
|---|---|---|
| `P(retry ≤ H)` — **occurrence** | will price **touch** the yearline (MA250) within H trading days? | empirical completed-path estimator; **Phase-7 classifier↔empirical blend** where it gate-passes |
| `P(success │ retry)` — **success** | *given* a touch, will it **reclaim and hold** (≥70% over the hold window)? | RS-3 classifier↔empirical blend (w = 0.5) |
| `P(reclaim ≤ H)` — **composite** | joint: touch **and** hold within H | `P(retry ≤ H) × P(success │ retry)` |

**Trust gates.** A probability is only meant to be *shown* where its gate passes (AUC ≥ 0.60, MACE ≤ 0.10,
n ≥ 50). The composite is **surfaced only where _both_ the occurrence gate and the success gate pass**;
otherwise it is withheld (diagnostic). Horizons reported: **10 / 20 / 40 / 60** trading days.

---

## 3. Universe snapshot (as of 2026-05-29)

| Ticker | Peer group | Regime | Dist. to MA250 | Episode drawdown | `P(retry≤40d)` | `P(success│retry)` | gate | `P(reclaim≤40d)` |
|---|---|---|---:|---:|---:|---:|:--:|---:|
| **MSFT** | mega_cap_software_like | **Repair** | **−3.0%** | 23.4% | **0.89** (blend) | 0.137 | ✅ | **0.122** |
| **META** | mega_cap_software_like | **Repair** | −6.4% | 11.8% | **0.77** (blend) | 0.155 | ✅ | **0.119** |
| **IGV** | etf_context | **Repair** | −5.1% | 29.0% | 0.84 (empirical) | — | — | — |
| AAPL | mega_cap_software_like | Trend (overextended) | +24.0% | 4.4% | — (dormant) | 0.204 | ✅ | 0.183 ⚠️ |
| AMZN | mega_cap_software_like | Trend (healthy) | +18.1% | 0.7% | — (dormant) | 0.198 | ✅ | 0.168 ⚠️ |
| NVDA | ai_accelerator | Trend (pullback) | +15.8% | 4.2% | — (dormant) | **0.289** | ✅ | 0.281 ⚠️ |
| QQQ | etf_context | Trend (overextended) | +22.4% | 1.0% | — (dormant) | 0.249 | ✅ | 0.229 ⚠️ |
| XLK | etf_context | Trend (overextended) | +35.2% | 1.4% | — (dormant) | 0.251 | ✅ | 0.240 ⚠️ |
| GOOGL | mega_cap_software_like | Transitional | +37.8% | 6.6% | — (dormant) | 0.176 | ✅ | 0.151 ⚠️ |

⚠️ = composite surfaced but the **occurrence question is dormant** for this name (it's above its yearline);
treat the trend-row composites as **not applicable** for a point-in-time retry decision (see §6.1).

---

## 4. Repair-mode names — the live retry question

These three are **below** their yearline on 2026-05-29, so a retry (a touch from below) is genuinely
pending and the full occurrence → success → composite chain applies.

### 4.1 MSFT — deep repair, near reclaim; *touching is easy, holding is the question*
- **State:** −3.0% below MA250 (needs **+3.1%** to reclaim); episode drew down **23.4%** at its worst, so
  this is a *deep* repair that has rallied most of the way back (MSFT closed +5.4% at 450.24 on 2026-05-29).
- **Occurrence:** empirical `P(retry≤H)` = 0.41 / 0.70 / **0.94** / 0.96 (10/20/40/60d). The **Phase-7
  blend** (weight 0.75 toward the classifier; gate ✅ AUC 0.825, MACE 0.059, n≈4,677) tempers 40d to
  **0.89** and 60d to **0.94** — a touch within ~2 months is very likely. *(Note the isotonic-only
  calibration did **not** pass its 40d gate here; the blend is the gate-passing occurrence surface — §6.2.)*
- **Success:** `P(success│retry)` = **0.137** (blend of classifier 0.002 + empirical 0.271 over the
  `group_transition` scope; gate ✅). Low — deep repairs that touch often fail to hold.
- **Composite:** `P(reclaim≤40d)` = **0.122**, `P(reclaim≤60d)` = **0.128**. The dominant term is the low
  success probability, not occurrence: MSFT will probably *reach* its yearline, but the evidence says
  *holding* it is roughly a 1-in-7 proposition.

### 4.2 META — moderate repair, mid-distance
- **State:** −6.4% below MA250 (needs **+6.8%**); episode drawdown 11.8%.
- **Occurrence:** empirical 0.29 / 0.46 / 0.65 / 0.80; **blend** 40d **0.77**, 60d **0.87** (gate ✅).
- **Success:** **0.155** (classifier 0.0005 + empirical 0.309 over the `group` scope; gate ✅).
- **Composite:** `P(reclaim≤40d)` = **0.119**, `P(reclaim≤60d)` = **0.134**. Similar shape to MSFT — a
  moderate-to-likely touch, a low conditional hold.

### 4.3 IGV — deepest repair, but the success surface is unavailable
- **State:** −5.1% below MA250 (needs **+5.4%**); episode drawdown **29.0%** — the deepest in the universe
  (the software-ETF was hit hardest).
- **Occurrence:** empirical 0.42 / 0.65 / **0.84** / 0.89 — a touch is likely. *But* the Phase-7 occurrence
  **blend is unavailable** for IGV, and the **success overlay did not produce a block** (`retry_success_context`
  absent). So only the canonical empirical occurrence estimate is on offer; **no success probability and no
  composite.** This is a real coverage gap (§6.3), not a zero — *abstain*, don't infer.

---

## 5. Trend-mode names — retry dormant (above the yearline)

AAPL, AMZN, NVDA, QQQ, XLK are all comfortably **above** their yearline (+16% to +35%) in
post-confirmation uptrends; GOOGL (+37.8%) is in a transitional state. For all of these the engine
correctly reports the retry-occurrence question as **dormant** (`retry_hazard_context.active = false`,
`P(retry≤H) = None`) — there is no pending yearline touch to estimate.

| Ticker | Trend state | Dist. to MA250 | `P(success│retry)` (conditional) |
|---|---|---:|---:|
| NVDA | pullback_but_intact | +15.8% | **0.289** |
| XLK | overextended_trend | +35.2% | 0.251 |
| QQQ | overextended_trend | +22.4% | 0.249 |
| AAPL | overextended_trend | +24.0% | 0.204 |
| AMZN | healthy_trend | +18.1% | 0.198 |
| GOOGL | unknown_or_transition | +37.8% | 0.176 |

The `P(success│retry)` values here are best read as a **hypothetical conditional** — "*if* this name were
to fall back and attempt to reclaim its yearline, the model+history suggest this hold-probability." They
should **not** be paired with a live occurrence probability (there isn't one), and the composites the run
emitted for these rows are an artifact to be withheld — see §6.1.

---

## 6. Trust, gates, and limitations

### 6.1 ⚠️ The success **composite** is surfaced for trend-mode names (a wiring gap to tighten)
For the five trend names + GOOGL, `retry_hazard_context` correctly shows `active: false` and no
`P(retry≤H)` — yet `retry_success_context.successful_reclaim_within_horizon` still surfaced a composite
(e.g. **AAPL**: `p_retry_within_h: 0.896`, `occurrence_gate_passed: true`, `surfaced_probability: 0.183`).
That occurrence factor (0.896) is **not** AAPL's current chance of touching its yearline (it's +24% above
it) — it's the pooled blend scored on a **non-current** live-transition state.

**Root cause:** the RS-4 success overlay gates on *"the hazard layer ran"* (`hazard_context.available`),
whereas the occurrence **blend block** gates on *"the repair engine is active"* (`hazard_active`). The two
should match. **Recommended fix (one line, not applied in this report):** gate the success overlay — and
especially its composite — on the **repair engine being active** (or `repair_retry_context.active`), so
trend-mode names surface at most a clearly-labelled *hypothetical* `P(success│retry)` and **withhold the
composite**. Until then, **read every ⚠️ composite in §3/§5 as N/A.**

### 6.2 Occurrence calibration: the blend is doing the work
For the repair names, the **isotonic-only** occurrence calibration did **not** pass its 40d gate on this
as-of date (MSFT/META `calibration_gate_40d.passed = false`), while the **Phase-7 classifier↔empirical
blend passed** (MSFT 40d gate ✅ AUC 0.825 / MACE 0.059). This is exactly why the composite composes against
the blend — the long-/saturating-horizon calibration that isotonic can't hold, the blend can. (Background:
`phased_design/phase_08/rs4_composite_blend_times_blend.md`.)

### 6.3 IGV coverage gap
IGV produced a canonical empirical occurrence estimate but **no occurrence blend and no success surface**.
Likely causes: thin/edge cross-sectional or completed-attempt support for an ETF in the `etf_context` peer
group. Surfaced honestly as *unavailable* (abstain), not as a low probability.

### 6.4 Standing caveats (carry with every number)
- **Thin sample.** The success surface rests on ~162 completed attempts / 59 episodes / 9 tickers; gate
  passes are high-variance.
- **Shrinkage-calibrated levels.** ~87% of the success blend's calibration is base-rate shrinkage (see
  `phased_design/phase_08/reliability/`): **trust the ranking, not the precise level.** A "0.14" means
  "below the ~0.35 base rate," not a sharp 14.0%.
- **CV is leave-one-ticker-out, not walk-forward.** It can't yet detect *regime/period* overfit;
  re-validate walk-forward as history accrues.
- **Overlay, never a trade.** `must_not_auto_execute: true` on every block.

---

## 7. Provenance & reproduce

- **Data:** local price cache, every ticker truncated to **≤ 2026-05-29** (`StudyConfig.end = 2026-05-30`,
  exclusive). Confirmed: all `data_as_of = 2026-05-29`; universe `as_of = 2026-05-29`.
- **Config:** `config/universe_mvp_software_like.yaml` (MA250 yearline, MA200 fast, pooled peer groups).
- **Overlays:** `pool_hazard=True, calibrate=True, surface_blend=True, surface_success=True`.
- **Artifacts (this folder):**
  - `asof_2026-05-29_envelopes.json` — the full per-ticker statistical-context envelopes.
  - `asof_2026-05-29_summary.json` — the compact per-ticker summary behind §3.
- **Reproduce:**
  ```bash
  # truncates the data to ≤ 2026-05-29 (StudyConfig.end=2026-05-30) and runs the full universe + overlays
  python3 docs/reports/demo/run_asof_2026-05-29.py
  ```
  Equivalent in-product: load `universe_mvp_software_like.yaml` with `as_of: "2026-05-30"` (exclusive end ⇒
  last bar 2026-05-29) and run the universe pipeline with the four overlay flags above.

*Engine: V13 universe statistical-context engine (Phase 8 RS-1…RS-4 on main). Educational research only;
not financial advice; this is an evidence overlay and must not auto-execute.*
