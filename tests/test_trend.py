"""Track D TO-1 — post-confirmation trend scoring: de-saturation + de-collinearization + indicators."""
import numpy as np
import pandas as pd

from yearline_universe.trend import (
    build_post_confirmation_trend_state_history as build_trend,
    _efficiency_ratio, _trend_r2, _adx, _variance_ratio, _assign_state,
)

_VALID_STATES = {
    "trend_breakdown_to_repair", "trend_deterioration_watch", "overextended_trend",
    "early_confirmation", "pullback_but_intact", "healthy_trend", "indeterminate_trend",
}


def _ohlcv(close, start="2019-01-01"):
    idx = pd.bdate_range(start, periods=len(close))
    close = pd.Series(close, index=idx)
    return pd.DataFrame({"Open": close.shift(1).fillna(close), "High": close * 1.006,
                         "Low": close * 0.994, "Close": close, "Volume": 1e6}, index=idx)


def _clean_uptrend(n=420):
    return 100.0 * (1.0008 ** np.arange(n))          # smooth, high-efficiency rise


def _choppy(n=420, seed=0):
    rng = np.random.default_rng(seed)
    drift = 100.0 * (1.0008 ** np.arange(n))          # same drift ⇒ reliably above MA250...
    osc = 1.0 + 0.08 * np.sin(np.arange(n) / 6.0)     # ...but big short-period swings ⇒ low efficiency
    return drift * osc * (1.0 + rng.normal(0, 0.01, n))


def test_indicator_helpers_bounded():
    c = pd.Series(_clean_uptrend(300), index=pd.bdate_range("2019-01-01", periods=300))
    er, r2 = _efficiency_ratio(c, 20).dropna(), _trend_r2(c, 60).dropna()
    assert ((er >= 0) & (er <= 1)).all() and ((r2 >= 0) & (r2 <= 1)).all()
    adx = _adx(c * 1.006, c * 0.994, c, 14).dropna()
    assert (adx >= 0).all() and (adx <= 100).all()
    assert _variance_ratio(c, 10, 120).dropna().gt(0).all()


def test_scores_in_unit_interval_and_new_indicators_present():
    h = build_trend("UP", _ohlcv(_clean_uptrend()))
    assert not h.empty
    for col in ("trend_quality_score", "pullback_quality_score", "overextension_score",
                "deterioration_risk_score"):
        v = h[col].dropna()
        assert ((v >= 0) & (v <= 1)).all()
    for col in ("efficiency_ratio_20d", "trend_r2_60d", "adx_14", "variance_ratio_10_120"):
        assert col in h.columns
    assert set(h["post_confirmation_trend_state"].unique()) <= _VALID_STATES


def test_de_saturation_clean_beats_choppy():
    """A clean trend must score materially higher trend_quality than a choppy one (resolution restored)."""
    clean = build_trend("CLEAN", _ohlcv(_clean_uptrend()))
    choppy = build_trend("CHOP", _ohlcv(_choppy()))
    cq = clean["trend_quality_score"].dropna().mean()
    pq = choppy["trend_quality_score"].dropna().mean()
    # The old saturated scorer pegged BOTH at ~1.0 (no resolution); the de-saturated scorer separates them.
    assert cq > pq + 0.10
    assert choppy["trend_quality_score"].dropna().mean() < 0.95   # choppy is not pinned at the ceiling


def test_de_collinearization_trend_vs_pullback():
    """trend_quality and pullback_quality use disjoint feature bases ⇒ not near-perfectly collinear."""
    h = build_trend("CLEAN", _ohlcv(_clean_uptrend()))
    j = h[["trend_quality_score", "pullback_quality_score"]].dropna()
    if len(j) > 30 and j["trend_quality_score"].std() > 1e-6 and j["pullback_quality_score"].std() > 1e-6:
        assert j["trend_quality_score"].corr(j["pullback_quality_score"]) < 0.9


def test_assign_state_indeterminate_fallback():
    # all scores low / nothing triggers ⇒ indeterminate_trend (no longer mislabeled early_confirmation)
    row = {"price_above_ma250": True, "days_since_confirmation": 200, "trend_quality_score": 0.2,
           "pullback_quality_score": 0.2, "overextension_score": 0.1, "deterioration_risk_score": 0.1,
           "drawdown_from_post_confirmation_peak_pct": 0.5, "distance_to_ma50_pct": 1.0}
    assert _assign_state(row) == "indeterminate_trend"
    assert _assign_state({"price_above_ma250": False}) == "trend_breakdown_to_repair"
