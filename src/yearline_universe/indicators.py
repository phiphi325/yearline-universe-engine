"""Feature engineering / indicators for the V13 engine.

Faithful port of V12's ``add_indicators`` and small helpers. Ticker-agnostic;
takes an explicit :class:`StudyConfig` (no module-level CONFIG global).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import StudyConfig

__all__ = ["add_indicators", "sample_quality", "safe_pct", "date_str"]


def add_indicators(df: pd.DataFrame, config: StudyConfig | None = None) -> pd.DataFrame:
    """Attach MA250/MA200, ATR, HV30, distance-to-yearline and MA gap state."""
    config = config or StudyConfig()
    out = df.copy()
    out["MA250"] = out["Close"].rolling(config.ma_len, min_periods=config.ma_len).mean()
    out["MA200"] = out["Close"].rolling(config.ma_fast_len, min_periods=config.ma_fast_len).mean()

    prev_close = out["Close"].shift(1)
    tr = pd.concat(
        [
            out["High"] - out["Low"],
            (out["High"] - prev_close).abs(),
            (out["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["TR"] = tr
    out["ATR14"] = tr.rolling(config.atr_len, min_periods=config.atr_len).mean()
    out["ATR14_pct"] = out["ATR14"] / out["Close"] * 100.0

    ret = out["Close"].pct_change()
    out["HV30"] = ret.rolling(30, min_periods=30).std() * np.sqrt(252)
    out["distance_to_ma250_pct"] = (out["Close"] / out["MA250"] - 1.0) * 100.0
    out["high_vs_ma250_pct"] = (out["High"] / out["MA250"] - 1.0) * 100.0
    out["low_vs_ma250_pct"] = (out["Low"] / out["MA250"] - 1.0) * 100.0

    gap = (out["MA200"] / out["MA250"] - 1.0) * 100.0
    out["ma200_ma250_gap_pct"] = gap

    def gap_state(x: float) -> str:
        # V10 parity: treat |MA200/MA250 - 1| < 0.5% as flat / glued.
        if pd.isna(x):
            return "unknown"
        if abs(x) < 0.5:
            return "flat"
        if x >= 0.5:
            return "bull_open"
        return "bear_open"

    out["gap_state"] = gap.map(gap_state)
    return out


def sample_quality(n: int) -> str:
    if n < 3:
        return "very_low"
    if n < 10:
        return "low"
    if n < 30:
        return "medium"
    return "high"


def safe_pct(num: float, den: float) -> float:
    if den == 0 or pd.isna(num) or pd.isna(den):
        return np.nan
    return (num / den - 1.0) * 100.0


def date_str(x: Any) -> str | None:
    if pd.isna(x):
        return None
    return pd.to_datetime(x).strftime("%Y-%m-%d")
