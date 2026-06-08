import numpy as np
import pandas as pd
from yearline_universe.context_export import (
    build_statistical_context_envelope, make_json_safe, STATISTICAL_CONTEXT_JSON_SCHEMA,
)


def test_make_json_safe_handles_numpy_and_pandas():
    obj = {"a": np.int64(3), "b": np.float64(1.5), "c": np.bool_(True),
           "d": pd.Timestamp("2024-11-29"), "e": np.nan, "f": [np.int64(1)]}
    out = make_json_safe(obj)
    import json
    json.dumps(out)  # must not raise
    assert out["a"] == 3 and out["b"] == 1.5 and out["c"] is True
    assert out["d"] == "2024-11-29" and out["e"] is None and out["f"] == [1]


def test_envelope_builds_for_repair_engine():
    semantic_card = {"active_engine": "repair_retry_hazard_engine", "as_of_date": "2022-10-03",
                     "interpretation": "repair active"}
    latest = {"mode_state_replay": "below_yearline_repair", "distance_to_ma250_pct": -8.0,
              "required_rebound_to_ma250_pct": 8.7, "hazard_today_gated": 0.02,
              "p_retry_within_40d_gated": 0.55, "hazard_semantics": "active_repair_retry_metric"}
    env = build_statistical_context_envelope("XYZ", "Sector", "peer", semantic_card, latest)
    assert env["ticker"] == "XYZ"
    assert env["repair_retry_context"]["active"] is True
    assert env["retry_hazard_context"]["p_retry_within_40d"] == 0.55
    # research hint reflects repair engine
    assert "BUY_LONG_DATED_PUT" in env["option_overlay_research_hint"]["candidate_action_bias"]
    assert env["option_overlay_research_hint"]["must_not_auto_execute"] is True


def test_envelope_matches_required_schema_keys():
    req = set(STATISTICAL_CONTEXT_JSON_SCHEMA["required"])
    env = build_statistical_context_envelope("XYZ", "S", "p", {"active_engine": None}, {})
    assert req.issubset(set(env.keys()))
