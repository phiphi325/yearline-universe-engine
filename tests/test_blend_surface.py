"""V13.3 Phase 7 (consumer wiring) — blend surface: weight/gate, live apply, live feature row."""
import numpy as np
import pandas as pd
from yearline_universe import StudyConfig
from yearline_universe.labels import MODEL_FEATURE_COLUMNS_WITH_XS
from yearline_universe.models import make_direct_horizon_logistic
from yearline_universe.blend_surface import (
    apply_blend_live, build_blend_context, _select_weight_and_gate, _live_feature_frame,
    BLEND_SURFACE_VERSION,
)


def _prices(n=320, seed=0, drift=0.0004):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.012, n))
    idx = pd.bdate_range("2021-01-01", periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.004, "Low": close * 0.996,
                         "Close": close, "Volume": 1_000_000}, index=idx)


def test_select_weight_and_gate_picks_grid_weight():
    rng = np.random.default_rng(0)
    n = 400
    y = rng.integers(0, 2, n)
    clf = np.clip(0.5 + (y - 0.5) * 0.6 + rng.normal(0, 0.1, n), 0, 1)   # strong ranker
    emp = np.clip(0.4 + (y - 0.5) * 0.3 + rng.normal(0, 0.1, n), 0, 1)   # weaker, calibrated-ish
    w, gate = _select_weight_and_gate(y, clf, emp)
    assert w in (0.0, 0.25, 0.5, 0.75, 1.0)
    assert {"passed", "auc", "mace", "n", "fail_reasons"} <= set(gate)
    assert isinstance(gate["passed"], bool)


def test_apply_blend_live_math_and_bounds():
    feats = MODEL_FEATURE_COLUMNS_WITH_XS
    rng = np.random.default_rng(1)
    X = rng.normal(size=(200, len(feats)))
    y = (X[:, 0] + rng.normal(0, 0.5, 200) > 0).astype(int)
    pipe = make_direct_horizon_logistic().fit(X, y)
    model = {"available": True, "horizons": [20], "feature_columns": feats,
             "per_horizon": {20: {"pipeline": pipe, "feature_columns": feats,
                                  "blend_weight_classifier": 0.5,
                                  "gate": {"passed": True, "auc": 0.8, "mace": 0.05, "n": 200,
                                           "fail_reasons": []}}}}
    live = pd.DataFrame([{c: 0.1 for c in feats}], columns=feats)
    res = apply_blend_live(model, live, {20: 0.4}, [20])
    r = res[20]
    assert 0.0 <= r["blend_probability"] <= 1.0
    # blend == w*clf + (1-w)*emp
    assert abs(r["blend_probability"] - (0.5 * r["classifier_probability"] + 0.5 * 0.4)) < 1e-9
    assert r["empirical_probability"] == 0.4 and r["gate_passed"] is True
    # missing empirical ⇒ falls back to the classifier probability
    res2 = apply_blend_live(model, live, {20: None}, [20])
    assert abs(res2[20]["blend_probability"] - res2[20]["classifier_probability"]) < 1e-12


def test_live_feature_frame_is_one_row_with_all_columns():
    td = {
        "AAA": {"peer_group": "mega_cap_software_like", "price_df": _prices(seed=1, drift=0.0006)},
        "BBB": {"peer_group": "mega_cap_software_like", "price_df": _prices(seed=2, drift=0.0002)},
        "QQQ": {"peer_group": "etf_context", "price_df": _prices(seed=3, drift=0.0004)},
    }
    as_of = td["AAA"]["price_df"].index[-1]
    live_row = {"as_of_date": as_of.date(), "trading_days_since_touch": 7.0,
                "drawdown_so_far_pct": 9.0, "below_ma250_depth_so_far_pct": 6.0,
                "from_touch_day_overshoot_pct": 1.0, "attempt_no": 2.0}
    frame = _live_feature_frame(td, "AAA", live_row, StudyConfig(), MODEL_FEATURE_COLUMNS_WITH_XS)
    assert list(frame.columns) == MODEL_FEATURE_COLUMNS_WITH_XS and len(frame) == 1
    # static came straight from the live row; path/xs are finite at a well-warmed as-of
    assert abs(float(frame["trading_days_since_touch"].iloc[0]) - 7.0) < 1e-9
    for c in ("return_20d", "repair_gap_pct", "rel_return_20d_vs_xs_median", "mkt_return_20d"):
        assert np.isfinite(float(frame[c].iloc[0])), c


def test_build_blend_context_requires_pooled_universe():
    one = {"AAA": {"peer_group": "x", "price_df": _prices()}}
    out = build_blend_context(one, "AAA", {"as_of_date": _prices().index[-1].date()}, {10: 0.3},
                              StudyConfig(), model=None, horizons=[10])
    assert out["available"] is False and "pooled" in out["warning"]


def test_envelope_attaches_blend_only_when_available_and_repair_active():
    """The overlay is additive + gated: the key appears only when a blend is available AND the
    repair engine is active. Default (no blend_context) ⇒ envelope is byte-identical (key absent)."""
    from yearline_universe.context_export import build_statistical_context_envelope
    blend = {"available": True, "schema": BLEND_SURFACE_VERSION,
             "per_horizon": {"40": {"blend_probability": 0.5, "classifier_probability": 0.49,
                                    "empirical_probability": 0.6, "blend_weight_classifier": 0.5,
                                    "gate": {"passed": True}, "gate_passed": True}},
             "any_gate_passed": True}
    repair = {"active_engine": "repair_retry_hazard_engine", "as_of_date": "2026-06-05"}

    # repair-active + available ⇒ attached
    env = build_statistical_context_envelope("ZZZ", "tech", "peerX", repair, repair, blend_context=blend)
    assert env["retry_hazard_context"]["direct_classifier_blend"]["available"] is True

    # no blend_context ⇒ key absent (default byte-identity)
    env_off = build_statistical_context_envelope("ZZZ", "tech", "peerX", repair, repair)
    assert "direct_classifier_blend" not in env_off["retry_hazard_context"]

    # trend-active ⇒ not attached even if a blend was passed
    trend = {"active_engine": "post_confirmation_trend_engine", "as_of_date": "2026-06-05"}
    env_trend = build_statistical_context_envelope("ZZZ", "tech", "peerX", trend, trend, blend_context=blend)
    assert "direct_classifier_blend" not in env_trend["retry_hazard_context"]

    # unavailable blend ⇒ not attached
    env_unavail = build_statistical_context_envelope(
        "ZZZ", "tech", "peerX", repair, repair, blend_context={"available": False})
    assert "direct_classifier_blend" not in env_unavail["retry_hazard_context"]
