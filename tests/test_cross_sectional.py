"""V13.3 Phase 7 (PR-D) — cross-sectional (peer/sector/market) features: shape, leakage, sanity."""
import numpy as np
import pandas as pd
from yearline_universe import StudyConfig
from yearline_universe.cross_sectional import (
    build_cross_sectional_features, CROSS_SECTIONAL_FEATURE_COLUMNS,
)


def _mk(n=400, seed=0, drift=0.0004):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.012, n))
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.004, "Low": close * 0.996,
                         "Close": close, "Volume": 1_000_000}, index=idx)


def _tickers_data():
    # 3 equity names (two peer groups) + one ETF that becomes the market-regime proxy.
    return {
        "AAA": {"peer_group": "mega_cap_software_like", "price_df": _mk(seed=1, drift=0.0006)},
        "BBB": {"peer_group": "mega_cap_software_like", "price_df": _mk(seed=2, drift=0.0001)},
        "CCC": {"peer_group": "ai_accelerator", "price_df": _mk(seed=3, drift=0.0009)},
        "QQQ": {"peer_group": "etf_context", "price_df": _mk(seed=4, drift=0.0004)},
    }


def test_shape_and_columns():
    out = build_cross_sectional_features(_tickers_data(), StudyConfig())
    assert list(out.columns) == ["ticker", "as_of_date"] + CROSS_SECTIONAL_FEATURE_COLUMNS
    assert out["ticker"].nunique() == 4
    breadth = out["xs_breadth_frac_above_ma250"].dropna()
    assert ((breadth >= 0) & (breadth <= 1)).all()
    assert set(out["mkt_above_ma250"].dropna().unique()).issubset({0.0, 1.0})
    # QQQ present ⇒ the market-regime block is populated (not all NaN)
    assert out["mkt_distance_to_ma250_pct"].notna().any()


def test_peer_relative_is_centered_on_the_cross_section():
    """rel_X = ticker_X − median(equity X); the median of rel_X across the equity names
    is 0 by construction — a cheap invariant that the benchmark is the equity cross-section."""
    out = build_cross_sectional_features(_tickers_data(), StudyConfig())
    eq = out[out["ticker"].isin(["AAA", "BBB", "CCC"])].dropna(subset=["rel_return_20d_vs_xs_median"])
    last = eq["as_of_date"].max()
    sl = eq[eq["as_of_date"] == last]
    assert len(sl) == 3
    assert abs(float(sl["rel_return_20d_vs_xs_median"].median())) < 1e-6
    assert abs(float(sl["rel_distance_to_ma250_vs_xs_median"].median())) < 1e-6


def test_no_future_leakage_cross_sectional():
    """Every row-t value must use only data ≤ t: truncating the WHOLE panel at t and
    recomputing must reproduce row-t exactly (cross-sectional aggregates at t are
    contemporaneous, never look-ahead)."""
    td = _tickers_data()
    full = build_cross_sectional_features(td, StudyConfig())
    full_dt = pd.to_datetime(full["as_of_date"])
    for t in (300, 340, 360):                       # well past the MA250 warm-up
        date_t = td["AAA"]["price_df"].index[t]
        trunc_td = {tk: {"peer_group": d["peer_group"], "price_df": d["price_df"].iloc[: t + 1]}
                    for tk, d in td.items()}
        trunc = build_cross_sectional_features(trunc_td, StudyConfig())
        trunc_dt = pd.to_datetime(trunc["as_of_date"])
        for tk in td:
            a = full[(full["ticker"] == tk) & (full_dt == date_t)]
            b = trunc[(trunc["ticker"] == tk) & (trunc_dt == date_t)]
            assert len(a) == 1 and len(b) == 1
            for c in CROSS_SECTIONAL_FEATURE_COLUMNS:
                va, vb = a[c].iloc[0], b[c].iloc[0]
                if pd.isna(va) and pd.isna(vb):
                    continue
                assert abs(float(va) - float(vb)) < 1e-9, f"leakage in {c} {tk} t={t}: {va} vs {vb}"
