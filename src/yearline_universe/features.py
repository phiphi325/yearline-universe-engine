"""V13.3 Phase 7 (PR-A) — path-dynamic repair features.

The empirical horizon estimator (Phase 3) conditions only on *static* state buckets
(distance, drawdown, days-since-touch); it cannot tell "10% below MA250 and **bouncing
hard**" from "10% below MA250 and **still making lower lows**." That distinction is the
single biggest cheap lever for retry-horizon *discrimination* (AUC) per
`docs/uploaded/V12_V13_AUC_MACE_improvement_research_report.md` §2/§8.

This module computes **leakage-safe, backward-looking** path-dynamic features:

  * trailing returns + short MA (20/50) trend/slope state,
  * distance-to-yearline *dynamics* (change + slope) and the de-correlated repair gap,
  * volatility level / percentile / range-compression,
  * repair-relative "is it reclaiming off the low?" features (given the latest touch).

Every column at date *t* uses only data up to and including *t* (a leakage test in
`tests/test_features.py` asserts this). Ticker-agnostic — operates on a price frame.

These features are **not yet consumed** by the envelope (capability before consumer);
the direct horizon classifier that uses them is PR-B/C. No existing output changes.
Educational research only.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import add_indicators

__all__ = [
    "PATH_FEATURE_COLUMNS",
    "REPAIR_PATH_FEATURE_KEYS",
    "build_price_path_features",
    "repair_path_features_at",
]

# Time-series (price-only) path features produced by build_price_path_features.
PATH_FEATURE_COLUMNS = [
    "return_5d", "return_10d", "return_20d",
    "ma20", "ma50", "distance_to_ma20_pct", "distance_to_ma50_pct",
    "price_above_ma20", "price_above_ma50", "ma20_change_10d_pct", "ma50_change_20d_pct",
    "repair_gap_pct", "required_rebound_to_ma250_pct",
    "distance_to_ma250_change_5d", "distance_to_ma250_change_10d",
    "distance_to_ma250_change_20d", "distance_to_ma250_slope_10d",
    "realized_vol_20d", "realized_vol_20d_pctile_252d", "range_compression_10d",
]

# Repair-relative features produced by repair_path_features_at (need the touch loc).
REPAIR_PATH_FEATURE_KEYS = [
    "drawdown_so_far_pct", "bounce_from_low_pct", "close_position_in_repair_range",
    "reclaim_from_low_speed_pct_per_day", "days_below_ma250_current_run",
]


def _pct_change(s: pd.Series, n: int) -> pd.Series:
    return (s / s.shift(n) - 1.0) * 100.0


def build_price_path_features(price_df: pd.DataFrame, config: StudyConfig | None = None) -> pd.DataFrame:
    """Per-date, leakage-safe path-dynamic features aligned to ``price_df.index``.

    Reuses ``add_indicators`` (MA250/ATR/HV30/distance) and adds the dynamics above.
    Warm-up rows are NaN until each trailing window is filled.
    """
    config = config or StudyConfig()
    df = add_indicators(price_df, config)
    close = df["Close"].astype(float)
    high, low = df["High"].astype(float), df["Low"].astype(float)
    dist = df["distance_to_ma250_pct"].astype(float)

    out = pd.DataFrame(index=df.index)

    # Trailing returns.
    out["return_5d"] = _pct_change(close, 5)
    out["return_10d"] = _pct_change(close, 10)
    out["return_20d"] = _pct_change(close, 20)

    # Short MA trend/slope state.
    ma20 = close.rolling(20, min_periods=20).mean()
    ma50 = close.rolling(50, min_periods=50).mean()
    out["ma20"] = ma20
    out["ma50"] = ma50
    out["distance_to_ma20_pct"] = (close / ma20 - 1.0) * 100.0
    out["distance_to_ma50_pct"] = (close / ma50 - 1.0) * 100.0
    out["price_above_ma20"] = (close > ma20).astype("float")
    out["price_above_ma50"] = (close > ma50).astype("float")
    out["ma20_change_10d_pct"] = _pct_change(ma20, 10)     # is the short MA turning up?
    out["ma50_change_20d_pct"] = _pct_change(ma50, 20)

    # Repair gap (de-correlated canonical) + required rebound (reporting only).
    out["repair_gap_pct"] = (-dist).clip(lower=0.0)
    ratio = 1.0 + dist / 100.0
    out["required_rebound_to_ma250_pct"] = np.where(ratio > 0, (1.0 / ratio - 1.0) * 100.0, np.nan)
    out["required_rebound_to_ma250_pct"] = out["required_rebound_to_ma250_pct"].clip(lower=0.0)

    # Distance-to-yearline dynamics (is the gap closing?).
    out["distance_to_ma250_change_5d"] = dist - dist.shift(5)
    out["distance_to_ma250_change_10d"] = dist - dist.shift(10)
    out["distance_to_ma250_change_20d"] = dist - dist.shift(20)
    out["distance_to_ma250_slope_10d"] = (dist - dist.shift(10)) / 10.0   # pct-points / day

    # Volatility level / percentile / compression.
    logret = np.log(close / close.shift(1))
    rv20 = logret.rolling(20, min_periods=20).std() * np.sqrt(252.0) * 100.0
    out["realized_vol_20d"] = rv20
    out["realized_vol_20d_pctile_252d"] = rv20.rolling(252, min_periods=60).apply(
        lambda w: float(np.mean(w[:-1] <= w[-1])) if len(w) > 1 else np.nan, raw=True)
    range10 = high.rolling(10, min_periods=10).max() - low.rolling(10, min_periods=10).min()
    range50 = high.rolling(50, min_periods=50).max() - low.rolling(50, min_periods=50).min()
    out["range_compression_10d"] = np.where(range50 > 0, range10 / range50, np.nan)

    return out


def repair_path_features_at(price_df: pd.DataFrame, touch_loc: int, asof_loc: int,
                            config: StudyConfig | None = None) -> dict[str, Any]:
    """Repair-relative path features over the window [touch_loc, asof_loc].

    Captures "how far has it reclaimed off the repair low, and how fast?" — the
    bounce signal the static buckets miss. Uses only rows within the window (≤ as-of).
    """
    config = config or StudyConfig()
    df = add_indicators(price_df, config)
    n = len(df)
    if touch_loc is None or asof_loc is None or touch_loc < 0 or asof_loc >= n or asof_loc <= touch_loc:
        return {k: np.nan for k in REPAIR_PATH_FEATURE_KEYS}
    sub = df.iloc[touch_loc:asof_loc + 1]
    entry_close = float(df["Close"].iloc[touch_loc])
    cur_close = float(df["Close"].iloc[asof_loc])
    low_min = float(sub["Low"].min())
    high_max = float(sub["High"].max())
    low_pos = int(sub["Low"].values.argmin())                 # index within the window
    days_since_low = int((asof_loc - touch_loc) - low_pos)

    drawdown_so_far = abs((low_min / entry_close - 1.0) * 100.0) if entry_close else np.nan
    bounce_from_low = (cur_close / low_min - 1.0) * 100.0 if low_min else np.nan
    rng = high_max - low_min
    close_pos = ((cur_close - low_min) / rng) if rng > 0 else np.nan
    reclaim_speed = (bounce_from_low / days_since_low) if (days_since_low and not pd.isna(bounce_from_low)) else np.nan

    # consecutive days (ending at as-of) with close < MA250.
    ma250 = df["MA250"]
    run = 0
    for i in range(asof_loc, -1, -1):
        c, m = df["Close"].iloc[i], ma250.iloc[i]
        if pd.isna(m) or c >= m:
            break
        run += 1

    return {
        "drawdown_so_far_pct": float(drawdown_so_far) if not pd.isna(drawdown_so_far) else np.nan,
        "bounce_from_low_pct": float(bounce_from_low) if not pd.isna(bounce_from_low) else np.nan,
        "close_position_in_repair_range": float(close_pos) if not pd.isna(close_pos) else np.nan,
        "reclaim_from_low_speed_pct_per_day": float(reclaim_speed) if not pd.isna(reclaim_speed) else np.nan,
        "days_below_ma250_current_run": int(run),
    }
