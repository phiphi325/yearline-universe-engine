"""V13.3 Phase 7 (PR-A) — path-dynamic features: shape, leakage-safety, sanity."""
import numpy as np
import pandas as pd
from conftest import CACHE_DIR
from yearline_universe import StudyConfig
from yearline_universe.data_loader import load_price_data
from yearline_universe.features import (
    build_price_path_features, repair_path_features_at,
    PATH_FEATURE_COLUMNS, REPAIR_PATH_FEATURE_KEYS,
)


def _synthetic_prices(n=400, seed=0):
    rng = np.random.default_rng(seed)
    # gentle uptrend with noise so MAs/returns are well-defined
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.012, n))
    idx = pd.bdate_range("2020-01-01", periods=n)
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1e6, 5e6, n)}, index=idx)


def test_path_features_shape_and_columns():
    feat = build_price_path_features(_synthetic_prices(), StudyConfig())
    assert list(feat.columns) == PATH_FEATURE_COLUMNS
    assert len(feat) == 400
    # warm-up: early rows NaN until trailing windows fill; later rows finite
    assert feat["return_20d"].iloc[:20].isna().all()
    assert feat["return_20d"].iloc[60:].notna().all()
    # flags are 0/1; compression positive
    assert set(feat["price_above_ma50"].dropna().unique()).issubset({0.0, 1.0})
    rc = feat["range_compression_10d"].dropna()
    assert (rc > 0).all()


def test_no_future_leakage():
    """Each feature at row t must depend only on data up to t: computing on the full
    series vs a series truncated at t+1 must give identical row-t values."""
    px = _synthetic_prices(n=320, seed=7)
    full = build_price_path_features(px, StudyConfig())
    for t in (260, 290, 300):                      # a few well-warmed rows
        trunc = build_price_path_features(px.iloc[: t + 1], StudyConfig())
        a = full.iloc[t]
        b = trunc.iloc[-1]
        for c in PATH_FEATURE_COLUMNS:
            va, vb = a[c], b[c]
            if pd.isna(va) and pd.isna(vb):
                continue
            assert abs(float(va) - float(vb)) < 1e-9, f"leakage in {c} at t={t}: {va} vs {vb}"


def test_repair_path_features_v_shape():
    # construct an explicit V: fall from 100 to 80, then recover to 92
    n = 120
    close = np.concatenate([np.linspace(100, 80, 40), np.linspace(80.5, 92, 80)])
    idx = pd.bdate_range("2021-01-01", periods=n)
    px = pd.DataFrame({"Open": close, "High": close * 1.002, "Low": close * 0.998,
                       "Close": close, "Volume": 1_000_000}, index=idx)
    feats = repair_path_features_at(px, touch_loc=0, asof_loc=n - 1, config=StudyConfig())
    assert set(REPAIR_PATH_FEATURE_KEYS) == set(feats)
    assert feats["drawdown_so_far_pct"] > 15            # dropped ~20% off entry
    assert feats["bounce_from_low_pct"] > 10            # reclaimed ~15% off the low
    assert 0.0 <= feats["close_position_in_repair_range"] <= 1.0
    assert feats["close_position_in_repair_range"] > 0.5  # near the recovered high
    assert feats["reclaim_from_low_speed_pct_per_day"] > 0


def test_real_ticker_runs_and_is_finite_at_latest():
    px = load_price_data("MSFT", config=StudyConfig(), cache_dir=str(CACHE_DIR), provider="cache")
    feat = build_price_path_features(px, StudyConfig())
    last = feat.iloc[-1]
    # the core dynamics are finite on a long real series at the latest bar
    for c in ["return_20d", "distance_to_ma250_change_10d", "repair_gap_pct",
              "realized_vol_20d", "range_compression_10d"]:
        assert np.isfinite(float(last[c])), c
