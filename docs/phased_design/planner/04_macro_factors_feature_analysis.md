# Would macro factors (10yr rates, VIX, market breadth) improve accuracy?

*Analysis note (planner). Prompted by the question: `option-mgmt-2026` could consider macro/market
factors — the **Fed 10-year rate**, **VIX**, and **market breadth** (not yet implemented there) — would
adding such features improve accuracy, e.g. for the Phase-8 retry-**success** classifier (RS-2)?
Educational research only; not financial advice.*

**Short answer.** *Plausibly the right lever in principle, but **not** an accuracy win on the current
sample, and **not** the fix for RS-2's actual gap.* Two things must be separated:
1. **RS-2's current shortfall is calibration, not discrimination** (AUC 0.71 ✓, MACE 0.13 ✗). More
   features improve *discrimination*; they do **not** fix calibration — **RS-3** (isotonic + blend + gate)
   does. So macro features do not address the thing currently blocking RS-2.
2. **Macro features are sample-starved here.** They are *market-level* (one value per date), shared
   across the cross-section and strongly autocorrelated in time, so their **effective sample size is the
   number of distinct macro regimes over the history — a handful — not the 162 attempts.** Adding a
   macro basket to a ~162-attempt / 59-episode / 9-ticker model mostly buys **overfitting**, not signal.

---

## 1. What the engine already has vs. what "macro" would add

| Dimension | Already in yearline | option-mgmt-2026 | Net new from "macro" |
|---|---|---|---|
| Per-name volatility | `realized_vol_20d` + 252d percentile | HV (close-to-close + Parkinson) | — (covered) |
| Market trend/level proxy | **QQQ** `mkt_distance_to_ma250_pct` / `_return_20d` / `_change_20d` / above-flag | — | a *broader* index proxy (SPY/total market) |
| Cross-sectional breadth | `xs_breadth_frac_above_ma250` (over the 9-name universe) | **none (not implemented)** | **true market breadth** (e.g. % of S&P > 200dma, adv/decline) |
| Implied vol / risk regime | — (only *realized* vol) | IV rank/percentile (per-name) | **VIX** level + Δ + term structure (market-wide forward risk) |
| Rates / discount regime | — | — | **10yr level + Δ** (and 2s10s) |

So the genuinely *new* macro signals are **rates (10yr)**, **VIX (implied/market vol regime)**, and **true
market breadth**. The engine already has a *tiny-universe* breadth and a *single-index* (QQQ) proxy, and
*realized* vol — which partially overlap VIX/breadth.

## 2. Is there a plausible signal? (yes)

A yearline repair/retry is genuinely **macro-sensitive**: a stock reclaiming its 1-year trend in a
**falling-rate, low-VIX, broad-rally** regime is more likely to *hold* than one poking at it in a
**rising-rate, high-VIX, narrow-tape** regime. So rates/VIX/breadth have a credible causal link to both
retry **occurrence** and **success**. This is exactly the **temporal regime** signal that Phase 7 found a
*contemporaneous cross-sectional snapshot could not capture* (it's why cross-sectional features didn't
rescue the 60d horizon). **Macro features are the most principled candidate for that regime-dependent
part** — which is the argument *for* them.

## 3. The catch: effective sample size, not feature count, is the ceiling

This is the decisive point. Macro features differ from path/cross-sectional features in a way that
matters for a small sample:

- **Path features** vary *per attempt* (each attempt has its own bounce/gap/vol) → ~162 quasi-independent
  observations.
- **Macro features** are *market-level and slow-moving* → on any given date every attempt sees nearly the
  same 10yr/VIX/breadth, and consecutive months are highly autocorrelated. Over ~15 years there are only a
  **handful of distinct rate/VIX/breadth regimes**. So a macro coefficient is effectively estimated from
  *a few regime-observations*, not 162 — its variance is huge and it will **fit history, not generalize**.

Concretely: leave-one-**ticker**-out CV (what RS-2 uses) will *not even detect* this overfit, because all
tickers share the same macro history — a macro feature that merely memorizes "2020 was a good regime"
looks fine when you hold out a ticker (the regime is still in the training tickers). You would need
**leave-one-*period*-out / walk-forward** validation to expose it. **Adding macro features without
period-based validation would likely *inflate* apparent AUC while *degrading* true out-of-time accuracy.**

## 4. Other practical concerns

- **Redundancy / collinearity.** VIX overlaps the engine's `realized_vol_20d` + the QQQ proxy (risk-on/off
  is partly already encoded); the marginal lift may be small. A true breadth measure is richer than the
  9-name `xs_breadth_frac_above_ma250`, but is still one market-level series.
- **Leakage / point-in-time.** 10yr (`^TNX` / FRED `DGS10`) and VIX (`^VIX`) must be aligned **at the
  attempt's touch date, backward-looking** (the same truncation-test discipline as `cross_sectional.py`).
  Rates aren't revised; breadth built from index constituents needs **point-in-time membership** (else
  survivorship/look-ahead).
- **Sourcing / offline cache.** 10yr + VIX are easy single series (cache like the price CSVs); **true
  breadth needs index constituents** (heavy) — a pragmatic proxy is the universe `% above MA250` (already
  present, tiny) or a breadth ETF/Hi-Lo index.
- **Don't double-count with option-mgmt.** `option-mgmt-2026`'s Market State already models a per-name
  IV/vol regime; if *both* engines bolt on overlapping macro, the integration double-counts. Cleaner:
  **macro/regime is a shared concern** — decide once where it lives (yearline as a *context* feature vs
  option-mgmt's Market State) and avoid duplication (see `02_option_mgmt_integration_plan.md`).

## 5. If pursued — how to test it *honestly*

A disciplined feature experiment (not a basket dump):
1. Add **≤ 1–3** strongly-justified macro features only — e.g. **VIX level + 20–60d Δ**, **10yr 60d Δ**,
   and (proxy) **universe breadth trend** — at the attempt's touch date (leakage-safe).
2. Re-run the RS-2 head-to-head **with and without** macro, reporting the **lift** (ΔAUC, ΔBrier, ΔMACE).
3. **Validate under leave-one-*period*-out / walk-forward** (e.g. expanding-window by year), *in addition*
   to leave-one-ticker-out — this is the only way to catch regime-memorization.
4. **Strong regularization** (lower `C`) given the macro features' tiny effective sample.
5. **Gate honestly** (AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50) and **abstain** if walk-forward shows no robust
   lift. *Expected outcome on the current 9-ticker / 162-attempt data: little-to-no robust lift, and a
   real risk of worse out-of-time accuracy.*

## 6. Recommendation

- **Not now, not on this sample.** Adding a macro basket to the current small model is more likely to
  overfit than to improve true accuracy, and it does **not** fix RS-2's actual gap (calibration → RS-3).
- **Finish the discipline first:** RS-3 (calibrate + blend + gate) is the next, higher-value step; it
  targets the measured shortfall.
- **Treat macro as a *data-unlocked* lever**, tied to the same dependency as Track C: macro features only
  earn their keep once the universe + history span **many independent regimes** (a wider/multi-sector
  universe + deeper history). Until then their effective sample is too small.
- **When data supports it,** add **1–3** macro features (VIX, 10yr Δ, breadth trend), validate
  **walk-forward**, gate, and coordinate with `option-mgmt-2026` so the macro/regime signal lives in one
  place, not two.

## 7. Bottom line

More features ≠ more accuracy here. The yearline engine *already* carries a market-proxy + breadth +
realized-vol; the new macro signals (rates, VIX, true breadth) are **causally plausible and the right tool
for the regime-dependent / longer-horizon part Phase 7 couldn't reach** — but they are **market-level and
sample-starved**, so on the current 162-attempt data they buy overfit, not accuracy, and they don't fix
RS-2's calibration gap. **Do RS-3 next; queue macro features behind the data unlock and validate them
walk-forward, not just by ticker.**
