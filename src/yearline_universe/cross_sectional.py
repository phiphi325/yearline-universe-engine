"""V13.3 Phase 7 (PR-D) — cross-sectional (peer / sector / market) features.

The path-dynamic features (PR-A) describe a repair *in isolation*. But whether a name
retouches its yearline also depends on the **regime around it**: is the broad market /
sector itself below its yearline? Is this name strong or weak *relative to its peers*?
How dispersed is the cross-section? Per the research report this is the next lever after
path dynamics — and the one most likely to help the **60d** horizon, which Phase 6 showed
is *regime-limited*, not discrimination-limited.

Every feature at date *t* is **leakage-safe**: it uses only data ≤ *t*. The cross-sectional
aggregates at *t* combine each ticker's *contemporaneous* (≤ *t*) value — that is observable
at *t* and contains no look-ahead. (A truncation test in `tests/test_cross_sectional.py`
asserts row-*t* is identical whether computed on the full panel or one truncated at *t*.)
Episode-aware CV (purge by transition) is unaffected — these are extra columns, not new rows.

Built from the universe runner's pooled_data
(``{ticker: {peer_group, price_df, recovery_table, live_diagnostic}}``). ETFs
(``peer_group`` containing "etf") are treated as **regime proxies**, not repair candidates,
so they're excluded from breadth / peer-relative benchmarks but power the market-regime
block. Ticker-agnostic. Educational research only.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import add_indicators

__all__ = ["CROSS_SECTIONAL_FEATURE_COLUMNS", "build_cross_sectional_features"]

CROSS_SECTIONAL_FEATURE_COLUMNS = [
    # market-regime proxy (broad ETF, default QQQ): where is the market vs its own yearline?
    "mkt_distance_to_ma250_pct", "mkt_above_ma250", "mkt_return_20d",
    "mkt_distance_to_ma250_change_20d",
    # cross-sectional breadth / dispersion over the equity names.
    "xs_breadth_frac_above_ma250", "xs_return_20d_dispersion",
    # this name relative to the equity cross-section (is it leading or lagging peers?).
    "rel_return_20d_vs_xs_median", "rel_distance_to_ma250_vs_xs_median",
]

_MKT_COLS = ["mkt_distance_to_ma250_pct", "mkt_above_ma250", "mkt_return_20d",
             "mkt_distance_to_ma250_change_20d"]


def _per_ticker_series(price_df: pd.DataFrame, config: StudyConfig) -> pd.DataFrame:
    """Per-date, leakage-safe distance / 20d-return / above-yearline for one ticker."""
    df = add_indicators(price_df, config)
    close = df["Close"].astype(float)
    dist = df["distance_to_ma250_pct"].astype(float)
    ma250 = df["MA250"].astype(float)
    out = pd.DataFrame(index=pd.to_datetime(df.index).normalize())
    out["distance_to_ma250_pct"] = dist.to_numpy()
    out["return_20d"] = ((close / close.shift(20) - 1.0) * 100.0).to_numpy()
    out["above_ma250"] = np.where(ma250.notna().to_numpy(),
                                  (close >= ma250).astype(float).to_numpy(), np.nan)
    return out


def build_cross_sectional_features(tickers_data: Mapping[str, Mapping[str, Any]],
                                   config: StudyConfig | None = None,
                                   market_proxy: str | None = None) -> pd.DataFrame:
    """Long frame keyed by (ticker, as_of_date) with CROSS_SECTIONAL_FEATURE_COLUMNS.

    ``market_proxy`` defaults to "QQQ" when present, else the first ETF-context ticker,
    else None (market block becomes NaN — gracefully imputed downstream).
    """
    config = config or StudyConfig()
    empty = pd.DataFrame(columns=["ticker", "as_of_date"] + CROSS_SECTIONAL_FEATURE_COLUMNS)
    per: dict[str, pd.DataFrame] = {}
    is_etf: dict[str, bool] = {}
    for tk, d in tickers_data.items():
        pdf = d.get("price_df")
        if pdf is None or pdf.empty:
            continue
        per[tk] = _per_ticker_series(pdf, config)
        is_etf[tk] = "etf" in str(d.get("peer_group", "")).lower()
    if not per:
        return empty

    # Equity cross-section = repair candidates (ETFs are regime context, not benchmarks).
    equities = [tk for tk in per if not is_etf[tk]] or list(per)
    ret_wide = pd.concat({tk: per[tk]["return_20d"] for tk in equities}, axis=1)
    dist_wide = pd.concat({tk: per[tk]["distance_to_ma250_pct"] for tk in equities}, axis=1)
    above_wide = pd.concat({tk: per[tk]["above_ma250"] for tk in equities}, axis=1)
    xs = pd.DataFrame(index=ret_wide.index)
    xs["xs_ret_median"] = ret_wide.median(axis=1, skipna=True)
    xs["xs_dist_median"] = dist_wide.median(axis=1, skipna=True)
    xs["xs_return_20d_dispersion"] = ret_wide.std(axis=1, skipna=True)
    xs["xs_breadth_frac_above_ma250"] = above_wide.mean(axis=1, skipna=True)

    # Market-regime proxy (broad ETF).
    proxy = market_proxy or ("QQQ" if "QQQ" in per else
                             next((tk for tk in per if is_etf[tk]), None))
    mkt = None
    if proxy and proxy in per:
        mp = per[proxy]
        mkt = pd.DataFrame(index=mp.index)
        mkt["mkt_distance_to_ma250_pct"] = mp["distance_to_ma250_pct"]
        mkt["mkt_above_ma250"] = mp["above_ma250"]
        mkt["mkt_return_20d"] = mp["return_20d"]
        mkt["mkt_distance_to_ma250_change_20d"] = (
            mp["distance_to_ma250_pct"] - mp["distance_to_ma250_pct"].shift(20))

    parts = []
    for tk, s in per.items():
        f = pd.DataFrame(index=s.index)
        f["ticker"] = tk
        f["as_of_date"] = pd.to_datetime(s.index)
        xsr = xs.reindex(s.index)
        f["xs_breadth_frac_above_ma250"] = xsr["xs_breadth_frac_above_ma250"].to_numpy()
        f["xs_return_20d_dispersion"] = xsr["xs_return_20d_dispersion"].to_numpy()
        f["rel_return_20d_vs_xs_median"] = s["return_20d"].to_numpy() - xsr["xs_ret_median"].to_numpy()
        f["rel_distance_to_ma250_vs_xs_median"] = (
            s["distance_to_ma250_pct"].to_numpy() - xsr["xs_dist_median"].to_numpy())
        if mkt is not None:
            mr = mkt.reindex(s.index)
            for c in _MKT_COLS:
                f[c] = mr[c].to_numpy()
        else:
            for c in _MKT_COLS:
                f[c] = np.nan
        parts.append(f)

    out = pd.concat(parts, ignore_index=True)
    return out[["ticker", "as_of_date"] + CROSS_SECTIONAL_FEATURE_COLUMNS]
