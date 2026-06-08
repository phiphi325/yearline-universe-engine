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

    if pd.notna(det) and det >= 0.70:
        return "trend_deterioration_watch"
    if pd.notna(over) and over >= 0.70 and pd.notna(trend_q) and trend_q >= 0.50:
        return "overextended_trend"
    if pd.notna(days) and days <= 20 and pd.notna(trend_q) and trend_q >= 0.45:
        return "early_confirmation"
    if pd.notna(dd) and dd >= 4 and pd.notna(pull_q) and pull_q >= 0.45:
        return "pullback_but_intact"
    if pd.notna(trend_q) and trend_q >= 0.65 and (pd.isna(dist50) or dist50 >= -3):
        return "healthy_trend"
    return "early_confirmation"


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

            trend_quality = np.nanmean([
                _clip01((dist250 + 2.0) / 10.0),
                _clip01((ma_spread + 1.0) / 6.0),
                _clip01((ma250_slope + 1.0) / 4.0),
                _clip01(1.0 - dd_peak / 15.0),
            ])
            pullback_quality = np.nanmean([
                _clip01(1.0 - dd_peak / 15.0),
                _clip01((dist250 + 1.0) / 8.0),
                _clip01((ma250_slope + 0.5) / 3.0),
                _clip01(1.0 if dist50 >= -5 else 0.2),
            ])
            extension_atr_multiple = dist50 / atr_pct if pd.notna(dist50) and pd.notna(atr_pct) and atr_pct != 0 else np.nan
            overextension = np.nanmean([
                _clip01(dist50 / 12.0),
                _clip01(dist250 / 25.0),
                _clip01((row.get("RSI14", np.nan) - 60.0) / 20.0),
                _clip01(extension_atr_multiple / 4.0 if pd.notna(extension_atr_multiple) else np.nan),
            ])
            deterioration = np.nanmean([
                _clip01(dd_peak / 15.0),
                _clip01((-dist50) / 8.0),
                _clip01((-ma50_slope) / 3.0),
                _clip01(1.0 if dist250 < 1 else 0.0),
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
    }
    context["option_overlay_research_hint"] = hints.get(context["trend_state"], "handoff_to_repair_retry_hazard_engine")
    return context
