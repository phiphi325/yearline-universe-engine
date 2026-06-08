"""V13.3 Phase 4 (V13.7) — calibration & gating of the empirical horizon estimator."""
import dataclasses

import numpy as np
import pandas as pd
from conftest import CACHE_DIR, CONFIG_DIR

from yearline_universe.calibration import (
    build_horizon_calibration_dataset, horizon_calibration_metrics,
    fit_isotonic_per_horizon, build_calibration_context, build_calibration_model,
    apply_isotonic_knots, CALIBRATION_HORIZONS,
)
from yearline_universe import load_universe_config, run_ticker_pipeline, export_single_ticker_context


def _synthetic_panel(n_transitions=18):
    """Completed at-risk rows; span (and so remaining-days) correlates with the
    distance/drawdown buckets, giving the empirical estimator real signal."""
    rows = []
    for r in range(n_transitions):
        span = 10 + (r % 5) * 10          # spans 10..50 trading days
        dist = -(2.0 + span / 5.0)        # deeper distance ⇒ longer span
        dd = 3.0 + span / 6.0
        for td in range(0, span + 1):
            rows.append({
                "ticker": "ZZZ", "group": "peerX", "round": r, "transition": "2_to_3",
                "from_date": f"20{10+r:02d}-01-01", "to_date": f"20{10+r:02d}-06-01",
                "from_canonical_quality": "strict",
                "is_live_transition": False, "event_retry_today": int(td == span),
                "trading_days_since_touch": td,
                "distance_to_ma250_pct": dist + td * 0.2,
                "drawdown_so_far_pct": dd,
            })
    return pd.DataFrame(rows)


def test_calibration_dataset_has_predictions_and_labels():
    ds = build_horizon_calibration_dataset(_synthetic_panel())
    assert not ds.empty
    for h in CALIBRATION_HORIZONS:
        assert f"pred_retry_within_{h}d" in ds.columns
        assert f"actual_retry_within_{h}d" in ds.columns
        assert set(ds[f"actual_retry_within_{h}d"].dropna().unique()).issubset({0, 1})
    # cumulative actuals are monotone non-decreasing across horizons (per row)
    a = ds[[f"actual_retry_within_{h}d" for h in CALIBRATION_HORIZONS]].to_numpy()
    assert (np.diff(a, axis=1) >= 0).all()


def test_metrics_and_isotonic_shape():
    ds = build_horizon_calibration_dataset(_synthetic_panel())
    metrics, reliability = horizon_calibration_metrics(ds)
    assert not metrics.empty
    assert {"horizon_days", "n", "observed_rate", "predicted_mean", "brier_score", "auc",
            "mean_abs_calibration_error_by_bin"}.issubset(metrics.columns)
    iso = fit_isotonic_per_horizon(ds)
    assert iso, "expected isotonic transforms for the synthetic panel"
    for h, t in iso.items():
        ys = t["y_thresholds"]
        assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))   # monotone by construction
        assert 0.0 <= apply_isotonic_knots(t["x_thresholds"], ys, 0.5) <= 1.0
        # honest out-of-fold metric is present (purged by transition; 18 groups → k-fold runs)
        assert t["oof_method"].startswith("group_kfold")
        assert 0.0 <= t["oof_calibrated_mace"] <= 1.0


def test_build_calibration_context_and_gate():
    panel = _synthetic_panel()
    live = {"ticker": "ZZZ", "group": "peerX", "transition": "2_to_3",
            "from_canonical_quality": "strict", "trading_days_since_touch": 8,
            "distance_to_ma250_pct": -8.0, "drawdown_so_far_pct": 9.0}
    ctx = build_calibration_context(panel, live_row=live)
    assert ctx["available"] is True
    assert "purged" in ctx["method"] and "isotonic" in ctx["method"]
    assert ctx["summary"] and ctx["trust_gate"]
    # every horizon's gate is a well-formed pass/fail; MACE basis is exposed
    for h, g in ctx["trust_gate"].items():
        assert "passed" in g and isinstance(g["fail_reasons"], list)
        assert g["mace_gate_basis"] in ("oof_isotonic_calibrated", "raw_reliability")
    # live calibrated probabilities are present + bounded
    live_p = ctx["live_calibrated_horizon_probabilities"]
    for h in ("10", "20", "40", "60"):
        cp = live_p[h]["calibrated_probability"]
        assert cp is None or (0.0 <= cp <= 1.0)


def test_compute_once_model_matches_inline_build():
    # V13.3 Phase 6 follow-up: a precomputed model reused per ticker must yield results
    # identical to building it inline (compute-once is purely a performance refactor).
    panel = _synthetic_panel()
    live = {"ticker": "ZZZ", "group": "peerX", "transition": "2_to_3",
            "from_canonical_quality": "strict", "trading_days_since_touch": 8,
            "distance_to_ma250_pct": -8.0, "drawdown_so_far_pct": 9.0}
    inline = build_calibration_context(panel, live_row=live)                  # model=None → inline build
    model = build_calibration_model(panel)                                    # built once
    reused = build_calibration_context(panel, live_row=live, model=model)     # reuse → cheap apply
    assert reused["summary"] == inline["summary"]
    assert reused["trust_gate"] == inline["trust_gate"]
    assert reused["live_calibrated_horizon_probabilities"] == inline["live_calibrated_horizon_probabilities"]
    assert "live_calibrated_horizon_probabilities" not in model              # model has no per-ticker block


def test_calibrate_is_opt_in_and_default_off():
    """Default (calibrate=False) leaves calibration_context unavailable — opt-in, like fit_ml_models."""
    uni = load_universe_config(CONFIG_DIR / "universe_mega_cap_ai_infra.yaml")
    uni = dataclasses.replace(uni, replay_start="2024-06-01")
    res = run_ticker_pipeline(uni.get_ticker("MSFT"), uni, cache_dir=str(CACHE_DIR), provider="cache")
    env = export_single_ticker_context(res)
    assert env["calibration_context"]["available"] is False
    # the gate fields are only attached when calibration ran
    assert "surfaced_probability_is_calibrated" not in env["retry_hazard_context"]
