"""V13.8 — YearlineContext adapter (Track B / Phase 9): contract shape, gating, version pins, staleness."""
import pytest

from yearline_universe.adapter import (
    to_yearline_context, export_yearline_context, ADAPTER_VERSION,
    YEARLINE_CONTEXT_JSON_SCHEMA, YEARLINE_CONTEXT_HORIZONS,
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
