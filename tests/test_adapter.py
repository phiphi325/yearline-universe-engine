"""V13.8 — YearlineContext adapter (Track B / Phase 9): contract shape, gating, version pins, staleness.
Plus V13.8.1 — the YearlineTrendSeries presentation projection (trend-plot data source)."""
import numpy as np
import pandas as pd
import pytest

from yearline_universe.adapter import (
    to_yearline_context, export_yearline_context, ADAPTER_VERSION,
    YEARLINE_CONTEXT_JSON_SCHEMA, YEARLINE_CONTEXT_HORIZONS,
    to_yearline_trend_series, export_yearline_trend_series, TREND_SERIES_VERSION,
    YEARLINE_TREND_SERIES_JSON_SCHEMA,
)


def _repair_env(blend=True, success=True):
    env = {
        "as_of": "2026-05-29", "ticker": "MSFT", "schema_version": "v13", "model_stack_version": "ms1",
        "repair_retry_context": {"active": True, "distance_to_ma250_pct": -3.0,
                                 "required_rebound_to_ma250_pct": 3.1},
        "retry_hazard_context": {"active": True, "p_retry_within_10d": 0.40, "p_retry_within_20d": 0.70,
                                 "p_retry_within_40d": 0.94, "p_retry_within_60d": 0.96,
                                 "p_retry_within_40d_reference_scope": "group_transition",
                                 "calibration_gate_40d": {"passed": False}},
        "post_confirmation_trend_context": {},
        "retry_timing_context": {"consensus": {"central_remaining_days": 5.0,
                                               "remaining_days_range": [0.0, 30.0]}},
        "calibration_context": {"trust_gate": {"10": {"passed": True}, "20": {"passed": True},
                                               "40": {"passed": False}, "60": {"passed": False}}},
    }
    if blend:
        env["retry_hazard_context"]["direct_classifier_blend"] = {"available": True, "per_horizon": {
            "10": {"blend_probability": 0.50, "gate_passed": True},
            "20": {"blend_probability": 0.65, "gate_passed": True},
            "40": {"blend_probability": 0.89, "gate_passed": True},
            "60": {"blend_probability": 0.93, "gate_passed": True}}}
    if success:
        env["retry_success_context"] = {"available": True, "p_success_given_retry": 0.14, "gate_passed": True,
            "successful_reclaim_within_horizon": {
                "10": {"surfaced_probability": 0.07}, "20": {"surfaced_probability": 0.09},
                "40": {"surfaced_probability": 0.12}, "60": {"surfaced_probability": 0.13}}}
    return env


def _trend_env():
    return {"as_of": "2026-05-29", "ticker": "AAPL", "schema_version": "v13", "model_stack_version": "ms1",
            "repair_retry_context": {"active": False},
            "retry_hazard_context": {"active": False},
            "post_confirmation_trend_context": {"active": True, "trend_state": "overextended_trend",
                                                "distance_to_ma250_pct": 24.0},
            "retry_timing_context": {}}


def _assert_schema_conformant(ctx):
    props = YEARLINE_CONTEXT_JSON_SCHEMA["properties"]
    for k in YEARLINE_CONTEXT_JSON_SCHEMA["required"]:
        assert k in ctx, f"missing required key {k}"
    for k in ctx:                                   # additionalProperties: False
        assert k in props, f"unexpected key {k}"
    assert ctx["must_not_auto_execute"] is True
    try:
        import jsonschema
        jsonschema.validate(ctx, YEARLINE_CONTEXT_JSON_SCHEMA)
    except ImportError:
        pass


def test_contract_shape_versions_and_safety():
    ctx = to_yearline_context(_repair_env())
    _assert_schema_conformant(ctx)
    assert ctx["adapter_version"] == ADAPTER_VERSION
    assert ctx["schema_version"] == "v13" and ctx["model_stack_version"] == "ms1"
    assert ctx["must_not_auto_execute"] is True


def test_prefers_blend_surface_and_per_horizon_gates():
    ctx = to_yearline_context(_repair_env(blend=True))
    assert ctx["p_retry_basis"] == "blend"
    assert ctx["p_retry"] == {"10": 0.5, "20": 0.65, "40": 0.89, "60": 0.93}
    assert all(ctx["gate_passed"][str(h)] is True for h in YEARLINE_CONTEXT_HORIZONS)
    # timing range threaded from the consensus block
    assert ctx["days_to_touch_central"] == 5.0 and ctx["days_to_touch_high"] == 30.0


def test_empirical_fallback_uses_calibration_trust_gate():
    ctx = to_yearline_context(_repair_env(blend=False))
    assert ctx["p_retry_basis"] == "empirical"
    assert ctx["p_retry"]["40"] == 0.94
    assert ctx["gate_passed"]["10"] is True and ctx["gate_passed"]["40"] is False  # from trust_gate


def test_dormant_trend_emits_no_p_retry_and_keeps_trend_state():
    ctx = to_yearline_context(_trend_env())
    assert ctx["repair_active"] is False
    assert ctx["p_retry"] == {} and ctx["p_retry_basis"] is None and ctx["gate_passed"] == {}
    assert ctx["post_confirmation_trend_state"] == "overextended_trend"
    assert ctx["distance_to_ma250_pct"] == 24.0          # taken from the trend context when above MA250


def test_success_fields_populated_and_gated():
    on = to_yearline_context(_repair_env(success=True))
    assert on["p_success"] == 0.14 and on["success_gate_passed"] is True
    assert on["p_successful_reclaim"] == {"10": 0.07, "20": 0.09, "40": 0.12, "60": 0.13}
    off = to_yearline_context(_repair_env(success=False))
    assert off["p_success"] is None and off["success_gate_passed"] is False
    assert all(v is None for v in off["p_successful_reclaim"].values())


def test_is_stale_uses_as_of_reference():
    assert to_yearline_context(_repair_env(), as_of_today="2026-05-30")["is_stale"] is False
    assert to_yearline_context(_repair_env(), as_of_today="2026-06-30")["is_stale"] is True
    bad = dict(_repair_env()); bad["as_of"] = None
    assert to_yearline_context(bad)["is_stale"] is True


def test_export_writes_artifact(tmp_path):
    p = export_yearline_context(_repair_env(), out_dir=str(tmp_path))
    assert p.endswith("yearline_context_MSFT_2026-05-29.json")
    import json
    ctx = json.load(open(p))
    _assert_schema_conformant(ctx)


# ---------------------------------------------------------------------------
# V13.8.1 — YearlineTrendSeries (presentation projection)
# ---------------------------------------------------------------------------

def _semantic_history(n=40):
    idx = pd.bdate_range("2026-03-02", periods=n)
    half = n // 2
    return pd.DataFrame({
        "as_of_date": idx,
        "distance_to_ma250_pct": np.linspace(-6.0, 3.0, n),
        "drawdown_so_far_pct": np.linspace(12.0, 1.0, n),
        "active_engine": ["repair_retry_hazard_engine"] * half + ["post_confirmation_trend_engine"] * (n - half),
        "post_confirmation_trend_state": [None] * half + ["healthy_trend"] * (n - half),
        "trend_quality_score": [np.nan] * half + list(np.linspace(0.55, 0.72, n - half)),
        "pullback_quality_score": [np.nan] * half + list(np.linspace(0.60, 0.80, n - half)),
        "overextension_score": [np.nan] * half + list(np.linspace(0.30, 0.60, n - half)),
        "deterioration_risk_score": [np.nan] * half + list(np.linspace(0.10, 0.05, n - half)),
        "hazard_today_gated": list(np.linspace(0.02, 0.05, half)) + [np.nan] * (n - half),
        "p_retry_within_40d_gated": list(np.linspace(0.6, 0.9, half)) + [np.nan] * (n - half),
    })


def _price_df(n=400):
    idx = pd.bdate_range("2025-01-01", periods=n)              # spans the semantic dates (for MA reindex)
    px = 100 * np.exp(np.cumsum(np.full(n, 0.0006)))
    return pd.DataFrame({"Open": px, "High": px * 1.005, "Low": px * 0.995, "Close": px, "Volume": 1e6}, index=idx)


def _assert_series_schema(s):
    props = YEARLINE_TREND_SERIES_JSON_SCHEMA["properties"]
    for k in YEARLINE_TREND_SERIES_JSON_SCHEMA["required"]:
        assert k in s, f"missing {k}"
    for k in s:
        assert k in props, f"unexpected key {k}"
    assert s["must_not_auto_execute"] is True


def test_trend_series_shape_alignment_and_types():
    s = to_yearline_trend_series(_semantic_history(40), ticker="MSFT", schema_version="v13")
    _assert_series_schema(s)
    assert s["available"] is True and s["n"] == 40 and len(s["dates"]) == 40
    assert s["series_version"] == TREND_SERIES_VERSION
    for key in ("distance_to_ma250_pct", "active_engine", "trend_quality", "hazard_today", "p_retry_40d"):
        assert len(s[key]) == 40                              # every series aligned to dates
    assert all(isinstance(v, float) for v in s["distance_to_ma250_pct"])
    assert s["active_engine"][0] == "repair_retry_hazard_engine"


def test_trend_series_nan_becomes_none():
    s = to_yearline_trend_series(_semantic_history(40))
    # trend_quality is NaN over the repair (first) half, finite over the trend half
    assert s["trend_quality"][0] is None and isinstance(s["trend_quality"][-1], float)
    # hazard is gated to the repair half ⇒ None over the trend half
    assert isinstance(s["hazard_today"][0], float) and s["hazard_today"][-1] is None


def test_trend_series_price_overlay_aligned():
    s = to_yearline_trend_series(_semantic_history(40), price_df=_price_df())
    assert "close" in s and len(s["close"]) == 40
    assert len(s["ma250"]) == 40 and any(v is not None for v in s["ma250"])  # 250+ prior bars exist


def test_trend_series_lookback_and_empty():
    s = to_yearline_trend_series(_semantic_history(40), lookback_days=10)
    assert s["n"] == 10
    empty = to_yearline_trend_series(pd.DataFrame())
    assert empty["available"] is False and empty["must_not_auto_execute"] is True


def test_trend_series_export(tmp_path):
    p = export_yearline_trend_series(_semantic_history(12), ticker="MSFT", out_dir=str(tmp_path))
    import json
    s = json.load(open(p))
    assert s["available"] is True and s["n"] == 12


def test_trend_series_unavailable_shape_is_schema_conformant():
    """V13.8.2 — the available:false empty state is itself schema-conformant (the empty-panel golden shape
    OM-Y3 vendors as fixture_unavailable_trend_series.json). Docs/fixture hardening only, no new modelling,
    so the series_version pin is unchanged."""
    s = to_yearline_trend_series(pd.DataFrame(), ticker="MSFT")
    _assert_series_schema(s)                          # required keys + additionalProperties:false hold
    assert s["available"] is False and s["warning"] == "no_semantic_history"
    assert s["ticker"] == "MSFT" and s["series_version"] == TREND_SERIES_VERSION
    assert s["must_not_auto_execute"] is True
