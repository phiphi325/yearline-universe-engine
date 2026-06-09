"""Post-confirmation trend-management engine (Engine 2).

Faithful port of V12 Module G (V12.11). Active only while price is above MA250
(after yearline acceptance). Scores trend quality, pullback quality,
overextension and deterioration risk, and assigns a post-confirmation trend
state. De-globalised: takes price_df / config / start_date explicitly.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import add_indicators

__all__ = [
    "build_post_confirmation_trend_state_history",
    "build_post_confirmation_latest_context",
    "TREND_SCHEMA_VERSION",
]

TREND_SCHEMA_VERSION = "yearline_universe.post_confirmation_trend.v13"
TREND_MODEL_VERSION = "v13_rule_based_trend_state"


def _rsi(close, window=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _slope_pct(series, window):
    return (series / series.shift(window) - 1.0) * 100.0


def _clip01(x):
    try:
        return float(np.clip(x, 0.0, 1.0))
    except Exception:
        return np.nan


def _safe_nanmean(vals) -> float:
    """Mean of the finite entries; NaN (no warning) if none are finite (early-window rows)."""
    a = np.asarray([v for v in vals], dtype=float)
    a = a[np.isfinite(a)]
    return float(a.mean()) if a.size else np.nan


# --- TO-1 (Track D): SOTA trend-strength indicators that *spread* (don't peg at 1.0 like the
# fixed-denominator clips). All vectorized (no per-row Python apply). See docs/research/04. ---

def _efficiency_ratio(close: pd.Series, window: int = 20) -> pd.Series:
    """Kaufman fractal efficiency: |net move| / summed path over ``window`` ∈ [0,1]. High = clean trend."""
    net = (close - close.shift(window)).abs()
    path = close.diff().abs().rolling(window).sum()
    return (net / path.replace(0, np.nan)).clip(0.0, 1.0)


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's ADX (directional-movement trend strength), Wilder-smoothed via EWM."""
    up, dn = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=close.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=close.index)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    a = 1.0 / window
    atr = tr.ewm(alpha=a, adjust=False).mean().replace(0, np.nan)
    plus_di = 100.0 * plus_dm.ewm(alpha=a, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=a, adjust=False).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean()


def _trend_r2(close: pd.Series, window: int = 60) -> pd.Series:
    """Rolling R² of log-price vs a time trend ∈ [0,1] — trend "tightness"/linearity (vectorized)."""
    t = pd.Series(np.arange(len(close), dtype=float), index=close.index)
    r = np.log(close).rolling(window).corr(t)
    return (r ** 2).clip(0.0, 1.0)


def _variance_ratio(close: pd.Series, k: int = 10, window: int = 120) -> pd.Series:
    """Lo-MacKinlay-style persistence proxy: var(k-step log ret) / (k·var(1-step)); >1 trending, <1 mean-
    reverting (a cheap, vectorized Hurst-adjacent measure; not a significance test — treat as a soft feature)."""
    r1 = np.log(close).diff()
    rk = np.log(close) - np.log(close).shift(k)
    v1 = r1.rolling(window).var()
    vk = rk.rolling(window).var()
    return vk / (k * v1.replace(0, np.nan))


def _assign_state(row) -> str:
    above_ma250 = bool(row.get("price_above_ma250", False))
    if not above_ma250:
        return "trend_breakdown_to_repair"
    days = row.get("days_since_confirmation", np.nan)
    trend_q = row.get("trend_quality_score", np.nan)
    pull_q = row.get("pullback_quality_score", np.nan)
    over = row.get("overextension_score", np.nan)
    det = row.get("deterioration_risk_score", np.nan)
    dd = row.get("drawdown_from_post_confirmation_peak_pct", np.nan)
    dist50 = row.get("distance_to_ma50_pct", np.nan)

    # Thresholds operate on the TO-1 de-saturated scores (bounded indicators that spread, so a clean strong
    # trend lands ≈0.55–0.75 rather than pegging at 1.0). The state machine is descriptive; TO-3 will
    # validate/calibrate it against a forward outcome.
    if pd.notna(det) and det >= 0.65:
        return "trend_deterioration_watch"
    if pd.notna(over) and over >= 0.65 and pd.notna(trend_q) and trend_q >= 0.45:
        return "overextended_trend"
    if pd.notna(days) and days <= 20 and pd.notna(trend_q) and trend_q >= 0.40:
        return "early_confirmation"
    if pd.notna(dd) and dd >= 4 and pd.notna(pull_q) and pull_q >= 0.45:
        return "pullback_but_intact"
    if pd.notna(trend_q) and trend_q >= 0.55 and (pd.isna(dist50) or dist50 >= -3):
        return "healthy_trend"
    return "indeterminate_trend"


def build_post_confirmation_trend_state_history(
    ticker: str,
    price_df: pd.DataFrame,
    config: StudyConfig | None = None,
    start_date: str = "2020-01-01",
) -> pd.DataFrame:
    config = config or StudyConfig()
    if price_df is None or price_df.empty:
        return pd.DataFrame()
    price = add_indicators(price_df, config).copy()
    price.index = pd.to_datetime(price.index)
    p = price[price.index >= pd.Timestamp(start_date)].copy()
    if p.empty:
        return pd.DataFrame()

    p["MA20"] = p["Close"].rolling(20).mean()
    p["MA50"] = p["Close"].rolling(50).mean()
    if "MA250" not in p.columns:
        p["MA250"] = p["Close"].rolling(config.ma_len).mean()

    p["RSI14"] = _rsi(p["Close"], 14)
    if "ATR14_pct" not in p.columns:
        p["ATR14_pct"] = np.nan
    p["distance_to_ma250_pct"] = (p["Close"] / p["MA250"] - 1.0) * 100.0
    p["distance_to_ma50_pct"] = (p["Close"] / p["MA50"] - 1.0) * 100.0
    p["distance_to_ma20_pct"] = (p["Close"] / p["MA20"] - 1.0) * 100.0
    p["ma50_minus_ma250_pct"] = (p["MA50"] / p["MA250"] - 1.0) * 100.0
    p["ma20_minus_ma50_pct"] = (p["MA20"] / p["MA50"] - 1.0) * 100.0
    p["ma250_slope_20d_pct"] = _slope_pct(p["MA250"], 20)
    p["ma250_slope_60d_pct"] = _slope_pct(p["MA250"], 60)
    p["ma50_slope_20d_pct"] = _slope_pct(p["MA50"], 20)
    p["rolling_20d_return_pct"] = (p["Close"] / p["Close"].shift(20) - 1.0) * 100.0
    p["rolling_60d_return_pct"] = (p["Close"] / p["Close"].shift(60) - 1.0) * 100.0
    p["price_above_ma250"] = p["Close"] > p["MA250"]

    # TO-1: SOTA trend-strength indicators (bounded, de-saturated) computed once on the full series.
    p["efficiency_ratio_20d"] = _efficiency_ratio(p["Close"], 20)
    p["trend_r2_60d"] = _trend_r2(p["Close"], 60)
    _high = p["High"] if "High" in p.columns else p["Close"]
    _low = p["Low"] if "Low" in p.columns else p["Close"]
    p["adx_14"] = _adx(_high, _low, p["Close"], 14)
    p["variance_ratio_10_120"] = _variance_ratio(p["Close"], k=10, window=120)

    p["above_run_id"] = (p["price_above_ma250"] != p["price_above_ma250"].shift(1)).cumsum()
    above = p[p["price_above_ma250"]].copy()
    if above.empty:
        return pd.DataFrame()

    rows = []
    for run_id, g in above.groupby("above_run_id"):
        g = g.copy()
        confirmation_date = g.index.min()
        peak_close = g["Close"].cummax()
        drawdown_abs = (g["Close"] / peak_close - 1.0).abs() * 100.0

        for i, (dt, row) in enumerate(g.iterrows()):
            atr_pct = row.get("ATR14_pct", np.nan)
            dist250 = row.get("distance_to_ma250_pct", np.nan)
            dist50 = row.get("distance_to_ma50_pct", np.nan)
            ma_spread = row.get("ma50_minus_ma250_pct", np.nan)
            ma250_slope = row.get("ma250_slope_20d_pct", np.nan)
            ma50_slope = row.get("ma50_slope_20d_pct", np.nan)
            dd_peak = float(drawdown_abs.loc[dt])
            er = row.get("efficiency_ratio_20d", np.nan)
            r2 = row.get("trend_r2_60d", np.nan)
            adx = row.get("adx_14", np.nan)
            vr = row.get("variance_ratio_10_120", np.nan)

            # TO-1 — STRENGTH / PERSISTENCE axis: bounded SOTA indicators that *spread* (path efficiency,
            # trend linearity R², directional strength ADX, Lo-MacKinlay persistence). No drawdown/MA50
            # terms ⇒ disjoint from pullback_quality (de-collinearized).
            trend_quality = _safe_nanmean([
                _clip01(er),
                _clip01(r2),
                _clip01(adx / 50.0) if pd.notna(adx) else np.nan,
                _clip01(0.5 + (vr - 1.0)) if pd.notna(vr) else np.nan,
            ])
            # PULLBACK / DEPTH axis: how the current dip from the post-confirmation peak looks. Disjoint
            # feature base (drawdown depth + MA50 position) from the strength axis above.
            pullback_quality = _safe_nanmean([
                _clip01(1.0 - dd_peak / 12.0),
                _clip01((dist50 + 8.0) / 12.0),
            ])
            extension_atr_multiple = dist50 / atr_pct if pd.notna(dist50) and pd.notna(atr_pct) and atr_pct != 0 else np.nan
            overextension = _safe_nanmean([
                _clip01(dist50 / 12.0),
                _clip01(dist250 / 25.0),
                _clip01((row.get("RSI14", np.nan) - 60.0) / 20.0),
                _clip01(extension_atr_multiple / 4.0 if pd.notna(extension_atr_multiple) else np.nan),
            ])
            deterioration = _safe_nanmean([
                _clip01(dd_peak / 12.0),
                _clip01((-dist50) / 8.0),
                _clip01((-ma50_slope) / 3.0),
                _clip01(1.0 - _clip01(adx / 25.0)) if pd.notna(adx) else np.nan,
            ])

            rec = {
                "as_of_date": dt.date().isoformat(), "ticker": ticker,
                "above_run_id": int(run_id), "confirmation_date": confirmation_date.date().isoformat(),
                "days_since_confirmation": int(i), "close": float(row["Close"]),
                "ma20": float(row["MA20"]) if pd.notna(row["MA20"]) else np.nan,
                "ma50": float(row["MA50"]) if pd.notna(row["MA50"]) else np.nan,
                "ma250": float(row["MA250"]) if pd.notna(row["MA250"]) else np.nan,
                "price_above_ma250": bool(row["price_above_ma250"]),
                "distance_to_ma250_pct": float(dist250) if pd.notna(dist250) else np.nan,
                "distance_to_ma50_pct": float(dist50) if pd.notna(dist50) else np.nan,
                "distance_to_ma20_pct": float(row["distance_to_ma20_pct"]) if pd.notna(row["distance_to_ma20_pct"]) else np.nan,
                "ma50_minus_ma250_pct": float(ma_spread) if pd.notna(ma_spread) else np.nan,
                "ma250_slope_20d_pct": float(ma250_slope) if pd.notna(ma250_slope) else np.nan,
                "ma50_slope_20d_pct": float(ma50_slope) if pd.notna(ma50_slope) else np.nan,
                "atr14_pct": float(atr_pct) if pd.notna(atr_pct) else np.nan,
                "rsi14": float(row["RSI14"]) if pd.notna(row["RSI14"]) else np.nan,
                "post_confirmation_peak_close": float(peak_close.loc[dt]),
                "drawdown_from_post_confirmation_peak_pct": float(dd_peak),
                "extension_atr_multiple": float(extension_atr_multiple) if pd.notna(extension_atr_multiple) else np.nan,
                "efficiency_ratio_20d": float(er) if pd.notna(er) else np.nan,
                "trend_r2_60d": float(r2) if pd.notna(r2) else np.nan,
                "adx_14": float(adx) if pd.notna(adx) else np.nan,
                "variance_ratio_10_120": float(vr) if pd.notna(vr) else np.nan,
                "trend_quality_score": float(trend_quality),
                "pullback_quality_score": float(pullback_quality),
                "overextension_score": float(overextension),
                "deterioration_risk_score": float(deterioration),
            }
            rec["post_confirmation_trend_state"] = _assign_state(rec)
            rows.append(rec)

    return pd.DataFrame(rows).sort_values("as_of_date").reset_index(drop=True)


def build_post_confirmation_latest_context(
    hist: pd.DataFrame,
    ticker: str,
    price_df: pd.DataFrame,
    config: StudyConfig | None = None,
) -> Mapping[str, Any]:
    config = config or StudyConfig()
    if hist is None or hist.empty:
        return {
            "schema_version": TREND_SCHEMA_VERSION, "ticker": ticker, "active": False,
            "reason": "no_above_ma250_post_confirmation_rows",
            "handoff": "repair_retry_hazard_engine",
            "disclaimer": "Educational research only. Not financial advice.",
        }
    h = hist.copy()
    h["as_of_date"] = pd.to_datetime(h["as_of_date"])
    latest = h.sort_values("as_of_date").iloc[-1].to_dict()

    price = add_indicators(price_df, config)
    latest_price_date = pd.Timestamp(price.index.max()).normalize()
    latest_hist_date = pd.Timestamp(latest["as_of_date"]).normalize()
    active = latest_hist_date == latest_price_date and bool(latest.get("price_above_ma250", False))

    context = {
        "schema_version": TREND_SCHEMA_VERSION, "model_version": TREND_MODEL_VERSION,
        "ticker": ticker, "active": bool(active), "as_of_date": str(latest_hist_date.date()),
        "latest_price_date": str(latest_price_date.date()),
        "trend_state": latest.get("post_confirmation_trend_state"),
        "confirmation_date": latest.get("confirmation_date"),
        "days_since_confirmation": int(latest.get("days_since_confirmation", 0)),
        "distance_to_ma250_pct": latest.get("distance_to_ma250_pct"),
        "distance_to_ma50_pct": latest.get("distance_to_ma50_pct"),
        "drawdown_from_post_confirmation_peak_pct": latest.get("drawdown_from_post_confirmation_peak_pct"),
        "trend_quality_score": latest.get("trend_quality_score"),
        "pullback_quality_score": latest.get("pullback_quality_score"),
        "overextension_score": latest.get("overextension_score"),
        "deterioration_risk_score": latest.get("deterioration_risk_score"),
        "handoff": "post_confirmation_trend_engine" if active else "repair_retry_hazard_engine",
        "option_overlay_research_hint": None,
        "disclaimer": "Educational research only. Not financial advice.",
    }
    hints = {
        "early_confirmation": "preserve_upside_convexity_reduce_heavy_hedges_gradually",
        "healthy_trend": "hold_core_exposure_light_premium_harvesting_only",
        "overextended_trend": "consider_careful_extension_monetization_without_becoming_bearish",
        "pullback_but_intact": "avoid_panic_hedging_monitor_ma50_ma250_response",
        "trend_deterioration_watch": "increase_protection_prepare_handoff_to_repair_engine",
        "indeterminate_trend": "insufficient_trend_signal_no_directional_overlay",
    }
    context["option_overlay_research_hint"] = hints.get(context["trend_state"], "insufficient_trend_signal_no_directional_overlay")
    return context
