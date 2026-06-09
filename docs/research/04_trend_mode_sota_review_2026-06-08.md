# 04 — Trend-mode scoring: state-of-the-art review

**Date:** 2026-06-08 · **Type:** analysis (why/what). **Pairs with:** `03_trend_mode_scoring_review_and_enhancement_2026-06-08.md`
(the V13/V12 review + first-pass plan) and the refined build plan `planner/05_trend_outlook_plan.md`.
Educational research only; not financial advice.

**Purpose.** Doc 03 showed V13's trend engine is a faithful port of V12's *descriptive, hand-tuned,
unvalidated* MVP (saturated scores, collinear quality measures, coverage gap, no forward outcome / no
gate). This note surveys the **state-of-the-art** for scoring and managing a name that is in an established
uptrend, so the enhancement is grounded in literature rather than re-tuned magic constants. The
**through-line**: the SOTA *evaluation* discipline (proper scoring rules; the reliability−resolution−
uncertainty decomposition; isotonic/PAV calibration; "sharpness subject to calibration") is **the same
machinery the repair side already uses** (RS-3 gate + reliability diagnostic). So we can adopt SOTA trend
*signals* on top of the engine's existing *trust* discipline.

---

## 1. Trend strength / quality measurement

| Method | Outputs | Window / data | Calibrated prob? | Single-name? | Notes / failure modes |
|---|---|---|---|---|---|
| **ADX / DMI** (Wilder 1978) | trend-strength index 0–100 (+DI/−DI) | short (~14 bars) | no | yes | "is a trend happening *now*"; lags; high ADX can mark the *final* leg before reversal |
| **Hurst exponent** (R/S, DFA; Lo 1991 modified R/S) | persistence H∈[0,1] (H>0.5 trend, <0.5 mean-revert) | long (100–500+ bars) | no | yes (needs history) | regime-level; **high finite-sample variance** (SE>0.03 at N≈1000), heavy-tail/estimator bias — **H≠0.5 is *not* proof of predictability** (random-walk-compatible). Use DFA + significance tests (block bootstrap, surrogate, Lo). |
| **Kaufman Efficiency Ratio** | fractal efficiency 0–1 (net move ÷ summed path) | medium | no | yes | cheap "trendiness"; agnostic to direction; noisy on short windows |
| **Regression slope + R²** ("trend tightness") | slope (drift) + R² (linearity) | medium | no | yes | R² is an interpretable "how clean is the trend"; sensitive to window length |
| **Choppiness / Vortex / Aroon** | range-vs-trend, swing indices | medium | no | yes | complementary range-vs-trend discriminators |
| **MA-ribbon alignment, Donchian/Keltner, Supertrend** | structural state (stacked MAs, channel position) | medium | no | yes | structural confirmation; what V13 already leans on (MA50−MA250 spread, dist-to-MA) |

**The high-value SOTA combination (and the antidote to V13's saturation):** pair a **regime-level**
persistence measure (Hurst) with a **tactical** strength measure (ADX), and read their **divergence** —
*high ADX + low Hurst ⇒ trend exhaustion*; *low ADX + high Hurst ⇒ compression before breakout*; *both
high ⇒ high-confidence trend.* This two-layer view (regime ⊗ tactical) is precisely what a single
clipped-mean "trend_quality" score collapses away.

---

## 2. Market-regime detection (is the trend regime intact?)

| Method | Outputs | Notes |
|---|---|---|
| **HMM / Markov-switching** (Hamilton 1989) | posterior **probability** of latent regime (bull/range/turbulent) | gives a *probabilistic* regime read; unsupervised; needs enough data to estimate the transition matrix |
| **Adaptive Hierarchical HMM** (meta-regime, e.g. VIX-conditioned) | regime probs with state-dependent transitions | captures structural change (GFC, COVID, 2022 tightening); better VaR coverage than fixed-transition HMM |
| **Gaussian-mixture HMM (gmHMM)** | regime probs from return-shape features | multivariate (open-close, high-low) regime separation |
| **Changepoint detection** (BOCPD, PELT, TDA / persistent homology; cross-sectional changepoints) | timestamped structural breaks | "has the trend regime *just broken*"; cross-section breaks are robust across methods, not explained by Fama-French factors |
| **Volatility-regime models** (vol HMM, vol targeting) | high/low-vol state, scaled exposure | underpins the momentum risk-management result in §3 |

**Relevance to V13.** The engine's `mode_state` machine + `assign_active_engine` is a *deterministic* regime
classifier with a one-state trend gate. An **HMM/changepoint layer would give a calibrated regime
probability** (and the handoff would route on it), directly addressing doc 03's coverage gap (GOOGL
orphaned in `unknown_or_transition`).

---

## 3. Trend continuation vs. breakdown / forward-drawdown

- **Time-series momentum** (Moskowitz, Ooi & Pedersen 2012, *JFE*): a name's own past 1–12m return predicts
  its next-period return (persistence, then partial reversal at longer horizons). The canonical
  "trend continues" signal; evidence replicated in US, European, and emerging equity markets.
- **The volatility-scaling caveat** (Kim, Tse & Wald 2016; Baltas & Kosowski): **much of TSMOM's headline
  alpha is *volatility scaling* (risk-parity), not the raw trend signal** — unscaled TSMOM ≈ buy-and-hold.
  ⇒ **any trend score must be volatility-normalized** to be comparable across names/time (a direct fix for
  V13's absolute, un-normalized scores).
- **Adaptive TSMOM** (Elaut & Erdős 2019; Baltas & Kosowski): aggregate the signal over **many lookbacks
  (10…250d)** into a **continuous strength in [−1,1]** instead of a binary sign — a far better "trend
  quality" than a single clipped mean, and naturally regime-adaptive.
- **Momentum crashes & risk management** (Daniel & Moskowitz; Barroso & Santa-Clara): trend/momentum
  suffers severe **left-tail crashes** at regime turns; **volatility-managed** (vol-targeted) momentum
  sharply improves the tail. ⇒ the deterioration/forward-drawdown question is the *risk* half of trend
  scoring and deserves its own surface.
- **Low statistical power**: tests of trend-following profitability are low-power on short single-name
  samples — reinforcing the engine's pooling + shrinkage + walk-forward discipline.

---

## 4. ML & probabilistic approaches (and how trend probabilities are evaluated)

**Models.**
- **Deep Momentum Networks** (Lim, Zohren & Roberts 2019, arXiv:1904.04912): an LSTM learns trend
  *estimation and position sizing jointly*, trained to optimize the **Sharpe ratio** on top of the vol-
  scaling framework. **Momentum Transformer** (Wood et al. 2021, arXiv:2112.08534) adds attention +
  interpretability; **X-Trend** (2023, arXiv:2310.10500) uses **few-shot** cross-attention to adapt fast to
  *new regimes* (relevant to thin per-name history); multi-task variants (DeepUnifiedMom) forecast several
  horizons jointly. **Caveat:** these are data-hungry and optimize *returns*, not *calibrated probabilities*
  — useful as feature extractors / ranking, less so as a trustworthy P() on a single thin-history name.
- **Gradient-boosted trees** on trend features: the pragmatic single-name analog (mirrors RS-2's logistic);
  cheap, robust, and directly calibratable.

**Evaluation — the part that maps 1:1 onto the engine's existing gate.**
- **Proper scoring rules** (Gneiting & Raftery 2007; Brier 1950): a score is *proper* iff truthful
  probabilities are optimal. **Brier** and **log-loss** for binary trend-continuation; **CRPS** for a
  forward-drawdown *distribution*. Log-loss punishes overconfidence hardest.
- **Sharpness subject to calibration** (Gneiting, Balabdaoui & Raftery 2007): maximize sharpness *only*
  among calibrated forecasts — the formal statement of "honest first, sharp second."
- **The Brier decomposition `reliability − resolution + uncertainty`** (Murphy): identical to what the RS-3
  reliability diagnostic already computes. The canonical lesson — *"always predict the base rate is
  perfectly calibrated but has zero resolution and is useless"* — is **exactly the shrinkage finding** from
  `phase_08/reliability/`. So a trend probability must be gated on **resolution**, not just MACE.
- **Isotonic regression / pool-adjacent-violators (PAV)** for calibration + reliability diagrams (recent:
  conditional/T-calibration, a universal R²): **already the engine's calibration primitive** (OOF isotonic
  in `success_calibration`/`calibration`).

**Takeaway:** we do **not** need new evaluation machinery for the trend side — the repair side's
gate (AUC/MACE/**resolution**/n + OOF isotonic + classifier↔baseline blend) *is* the SOTA-aligned stack.
What's missing is a **forward trend label**, **volatility-normalized + cross-sectional features**, and a
**regime probability** — then the existing gate decides whether any of it earns the right to be surfaced.

---

## 5. What this implies for the V13 trend engine (→ `planner/05_trend_outlook_plan.md`)

| V13 gap (doc 03) | SOTA remedy adopted |
|---|---|
| Scores **saturate** (all ≈1.0) | volatility-normalize + **cross-sectional percentile/z-score** (TSMOM vol-scaling lesson); adaptive multi-lookback strength in [−1,1] |
| `trend_quality ≈ pullback_quality` (**collinear**) | disjoint bases: a **persistence/strength** axis (Hurst⊗ADX, slope+R², efficiency ratio) vs a **pullback/depth** axis |
| **Coverage gap** (GOOGL → `unknown_or_transition`) | route the handoff on a **regime probability** (HMM/changepoint) or the price condition, not one mode-state label |
| **Descriptive only** — no forward outcome / gate | define a **forward trend label** (continuation / forward max-drawdown / time-to-deterioration); validate + **calibrate + gate** with the *existing* RS-style machinery (proper scores, reliability−resolution, isotonic, blend) |
| no probability to size on | surface a **gated `P(trend continues ≤ H)` / `P(deterioration ≤ H)`** only if it clears the gate (AUC≥0.60, MACE≤0.10, **resolution floor**, n≥50) — else abstain |

**Single-name + thin-history caveats** carry over: Hurst needs ≥100–200 bars and is high-variance; HMMs and
deep models are data-hungry; trend-following tests are low-power. So the plan leans on **pooling +
shrinkage + walk-forward + abstention** — the same honesty the repair side carries.

### Key references
Moskowitz, Ooi & Pedersen (2012, *JFE*); Kim, Tse & Wald (2016, *J. Fin. Markets*) and Baltas & Kosowski
(volatility scaling / ATSMOM); Elaut & Erdős (2019); Daniel & Moskowitz and Barroso & Santa-Clara (momentum
crashes / vol-managed momentum); Hamilton (1989, Markov-switching) + adaptive/Gaussian-mixture HMM and
changepoint (BOCPD/PELT/TDA) literature; Wilder (1978, ADX); Hurst (1951)/Lo (1991) and the finite-sample
caveat (random-walk-compatible H≠0.5); Lim, Zohren & Roberts (2019, arXiv:1904.04912, Deep Momentum
Networks), Wood et al. (2021, arXiv:2112.08534, Momentum Transformer), X-Trend (arXiv:2310.10500);
Gneiting & Raftery (2007) and Gneiting, Balabdaoui & Raftery (2007) on proper scoring rules, calibration &
sharpness; Brier (1950)/Murphy decomposition; isotonic/PAV calibration & reliability diagrams.

*Sources gathered via literature search (Exa) on 2026-06-08; methodology described at the design level —
no proprietary V12 material reproduced. Educational research only; not financial advice.*
