"""V13.4 Phase 8 (RS-4 consumer wiring) — live retry-success overlay + occurrence×success composite.

Fast/synthetic (no real-universe load): exercises the live blend math, the composite + dual-gating,
graceful unavailability, and the envelope's byte-identical-when-off guarantee.
"""
import numpy as np
import pandas as pd

from yearline_universe.success_labels import SUCCESS_STATE_FEATURES
from yearline_universe.success_surface import (
    build_success_surface_model, apply_success_live, build_retry_success_context,
)
from yearline_universe.context_export import build_statistical_context_envelope
from yearline_universe.models import make_direct_horizon_logistic


def _fitted_model(w=0.5, gate_passed=True, feature_columns=None):
    cols = feature_columns or list(SUCCESS_STATE_FEATURES)
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(60, len(cols))), columns=cols)
    y = (X[cols[0]] + rng.normal(0, 0.5, 60) > 0).astype(int).to_numpy()
    pipe = make_direct_horizon_logistic().fit(X, y)
    ref = pd.DataFrame({
        "group": ["g"] * 24, "transition": ["t"] * 24,
        "drawdown_abs_low_pct": rng.uniform(2, 15, 24),
        "below_ma250_abs_low_pct": rng.uniform(3, 18, 24),
        "from_attempt": rng.integers(1, 4, 24),
        "y_success": rng.integers(0, 2, 24),
        "transition_key": [f"k{i}" for i in range(24)],
    })
    return {"available": True, "pipeline": pipe, "feature_columns": cols,
            "blend_weight_classifier": w, "base_rate": 0.4,
            "gate": {"passed": gate_passed, "auc": 0.70, "mace": 0.04, "n": 160, "fail_reasons": []},
            "recommended_surface": "blend", "empirical_reference": ref}


def _tickers_data(n_bars=300):
    rng = np.random.default_rng(1)
    out = {}
    for tk in ("AAA", "BBB"):
        idx = pd.bdate_range("2018-01-01", periods=n_bars)
        px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_bars)))
        price_df = pd.DataFrame({"Open": px, "High": px * 1.01, "Low": px * 0.99,
                                 "Close": px, "Volume": 1e6}, index=idx)
        rec = pd.DataFrame({
            "round": [1, 1], "transition": ["t", "t"],
            "to_date": [str(idx[-40].date()), str(idx[-10].date())],
            "from_attempt": [1, 2], "to_attempt": [2, 3],
            "drawdown_abs_low_pct": [8.0, 6.0], "below_ma250_abs_low_pct": [10.0, 7.0],
            "from_touch_day_overshoot": [1.0, 0.5], "from_fixed_5d_overshoot": [1.2, 0.6],
            "drawdown_atr_multiple": [2.0, 1.5], "gap_days": [20, 15], "trading_days_between": [14, 10],
            "next_attempt_success": [1, 0], "next_attempt_pending": [False, False],
        })
        out[tk] = {"peer_group": "g", "price_df": price_df, "recovery_table": rec,
                   "live_diagnostic": {"ticker": tk}}
    return out


def test_apply_success_live_blend_math():
    m = _fitted_model(w=0.6)
    cols = m["feature_columns"]
    frame = pd.DataFrame([{c: 0.1 for c in cols}])
    out = apply_success_live(m, frame, live_empirical_success_prob=0.30)
    assert 0.0 <= out["success_probability"] <= 1.0
    expected = 0.6 * out["classifier_probability"] + 0.4 * 0.30
    assert abs(out["success_probability"] - expected) < 1e-9
    # no empirical → pure classifier
    out2 = apply_success_live(m, frame, live_empirical_success_prob=None)
    assert abs(out2["success_probability"] - out2["classifier_probability"]) < 1e-9


def test_build_model_unavailable_on_empty():
    res = build_success_surface_model({}, None)
    assert res["available"] is False


def test_retry_success_context_composite_and_dual_gate():
    td = _tickers_data()
    model = _fitted_model(w=0.5, gate_passed=True)
    live_row = {"as_of_date": "2019-02-01", "transition": "t"}
    occ = {10: 0.2, 20: 0.4, 40: 0.6, 60: 0.7}
    occ_gate = {10: True, 20: True, 40: True, 60: False}
    # occurrence_surface provenance: blend at 10/20/40 (gate-passing), empirical fallback at 60.
    occ_surface = {10: "phase7_blend", 20: "phase7_blend", 40: "phase7_blend", 60: "empirical_isotonic"}
    ctx = build_retry_success_context(td, "AAA", live_row, occurrence_probs=occ,
                                      occurrence_calibrated=occ_gate, occurrence_surface=occ_surface,
                                      model=model)
    assert ctx["available"] is True
    assert ctx["gate_passed"] is True
    ps = ctx["p_success_given_retry"]
    comp = ctx["successful_reclaim_within_horizon"]
    # composite == P(retry) * P(success), per horizon; occurrence_surface is echoed through
    for h in (10, 20, 40):
        c = comp[str(h)]
        assert abs(c["p_successful_reclaim_within_h"] - round(occ[h] * ps, 4)) < 1e-6
        assert c["both_gates_passed"] is True
        assert c["surfaced_probability"] == round(occ[h] * ps, 4)
        assert c["occurrence_surface"] == "phase7_blend"
    # H=60: occurrence gate fails ⇒ not surfaced even though success gate passed
    assert comp["60"]["both_gates_passed"] is False
    assert comp["60"]["surfaced_probability"] is None
    assert comp["60"]["occurrence_surface"] == "empirical_isotonic"


def test_retry_success_context_success_gate_fail_blocks_surfacing():
    td = _tickers_data()
    model = _fitted_model(gate_passed=False)
    ctx = build_retry_success_context(td, "AAA", {"as_of_date": "2019-02-01", "transition": "t"},
                                      occurrence_probs={40: 0.6}, occurrence_calibrated={40: True},
                                      model=model)
    assert ctx["available"] is True and ctx["gate_passed"] is False
    assert ctx["successful_reclaim_within_horizon"]["40"]["surfaced_probability"] is None


def _envelope(success_context=None):
    return build_statistical_context_envelope(
        "AAA", "Technology", "software_like",
        semantic_card={"as_of_date": "2020-01-01", "active_engine": "repair_retry_hazard_engine"},
        latest_semantic_row={"as_of_date": "2020-01-01", "active_engine": "repair_retry_hazard_engine"},
        success_context=success_context,
    )


def test_envelope_byte_identical_when_off():
    # default: no success surfacing ⇒ key absent (byte-identical)
    assert "retry_success_context" not in _envelope(None)
    assert "retry_success_context" not in _envelope({"available": False, "warning": "x"})
    # surfaced ⇒ attached as a top-level block
    env = _envelope({"available": True, "p_success_given_retry": 0.42, "gate_passed": True})
    assert env["retry_success_context"]["p_success_given_retry"] == 0.42
