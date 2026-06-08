"""V13.3 Phase 7 (PR-E) — generalization rigor: weights, ticker-LOO, blend, richer metrics."""
import numpy as np
import pandas as pd

from yearline_universe.labels import MODEL_FEATURE_COLUMNS_WITH_XS
from yearline_universe.generalization import (
    episode_row_weights, calibration_metrics, evaluate_generalization,
    GENERALIZATION_VERSION, BLEND_GRID,
)

HORIZONS = [20, 40]


def _synthetic_multi_ticker_table(n_tickers=6, per_ticker=10, seed=0):
    """Modeling table spanning several tickers, each with several transitions and a planted
    signal — so leave-one-ticker-out and the blend have something to chew on."""
    rng = np.random.default_rng(seed)
    rows = []
    for ti in range(n_tickers):
        tk = f"TK{ti:02d}"
        for k in range(per_ticker):
            readiness = float(rng.normal())
            span = int(np.clip(40 - readiness * 10 + rng.normal(0, 4), 6, 70))
            tkey = f"{tk}|{k}"
            for td in range(span + 1):
                remaining = span - td
                feat = {c: float(rng.normal()) for c in MODEL_FEATURE_COLUMNS_WITH_XS}
                feat["trading_days_since_touch"] = float(td)
                feat["repair_gap_pct"] = 10.0 - readiness * 3.0 + rng.normal(0, 0.5)
                feat["return_5d"] = readiness * 2.0 + rng.normal(0, 0.5)
                row = {"ticker": tk, "transition_key": tkey, **feat}
                for h in HORIZONS:
                    row[f"y_{h}"] = int(remaining <= h)
                    row[f"empirical_pred_{h}"] = float(np.clip(
                        0.25 + 0.4 * int(remaining <= h) + rng.normal(0, 0.2), 0, 1))
                rows.append(row)
    return pd.DataFrame(rows)


def test_episode_row_weights_downweight_long_episodes():
    keys = ["A"] * 100 + ["B"] * 4            # one long episode, one short
    w = episode_row_weights(keys)
    assert abs(w.mean() - 1.0) < 1e-9          # mean-normalized
    assert w[0] < w[-1]                         # a long-episode row weighs less than a short-episode row
    # ratio tracks 1/sqrt(n): sqrt(100/4) = 5
    assert abs((w[-1] / w[0]) - 5.0) < 1e-6


def test_calibration_metrics_keys_and_bounds():
    y = np.array([0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0])
    p = np.clip(y * 0.6 + 0.2 + np.linspace(-0.05, 0.05, len(y)), 0, 1)
    m = calibration_metrics(y, p)
    assert set(m) >= {"auc", "mace", "ece", "quantile_mace", "reliability_slope", "brier", "n"}
    assert 0.0 <= m["auc"] <= 1.0 and 0.0 <= m["brier"] <= 1.0


def test_evaluate_generalization_structure_and_gap():
    ds = _synthetic_multi_ticker_table()
    res = evaluate_generalization(ds, horizons=HORIZONS, n_splits=4)
    assert res["available"] is True
    assert res["model_version"] == GENERALIZATION_VERSION
    assert res["n_tickers"] == 6
    assert res["blend_grid_classifier_weight"] == BLEND_GRID
    for r in res["horizons"]:
        if r["status"] != "ok":
            continue
        # both CV schemes report the full metric set
        for scheme in ("transition_purged", "ticker_loo"):
            cw = r[scheme]["classifier_weighted"]
            assert {"auc", "mace", "ece", "quantile_mace", "reliability_slope", "brier"} <= set(cw)
        # generalization gap + weighting effect are floats (or None) and present
        assert "generalization_gap_auc" in r and "weighting_effect_auc" in r
        # blend reports a convex weight in the grid (when enough rows)
        b = r["blend_ticker_loo"]
        if b.get("available"):
            assert b["best_w_classifier_weight"] in BLEND_GRID
            assert 0.0 <= b["auc"] <= 1.0


def test_empty_and_single_ticker_are_graceful():
    assert evaluate_generalization(pd.DataFrame(), horizons=HORIZONS)["available"] is False
    one = _synthetic_multi_ticker_table(n_tickers=1, per_ticker=6)
    res = evaluate_generalization(one, horizons=HORIZONS, n_splits=3)
    # one ticker ⇒ leave-one-ticker-out is undefined ⇒ each horizon flagged, not crashed
    assert res["available"] is True
    assert all(h["status"] != "ok" for h in res["horizons"])
