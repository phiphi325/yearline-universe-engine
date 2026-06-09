"""Track D TO-0 — engine handoff coverage: promote above-MA250 names to the trend engine."""
import numpy as np
import pandas as pd

from yearline_universe.semantic import build_semantic_history, assign_active_engine


def test_assign_active_engine_base_cases():
    assert assign_active_engine("below_yearline_repair") == "repair_retry_hazard_engine"
    assert assign_active_engine("accepted_above_watch") == "post_confirmation_trend_engine"
    assert assign_active_engine("transition_watch") == "unknown_or_transition"  # raw map (pre-promotion)


def _replay(states, start="2024-01-01", distance=None):
    idx = pd.bdate_range(start, periods=len(states))
    d = {"as_of_date": idx, "mode_state_replay": states}
    if distance is not None:
        d["distance_to_ma250_pct"] = distance   # the replay layer already carries this (replay.py)
    return pd.DataFrame(d)


def test_handoff_promotes_above_ma250_names_to_trend_engine():
    # transition_watch is unknown_or_transition by the raw map — but where a trend state exists for the
    # bar (i.e. price is above MA250), TO-0 promotes it to the trend engine instead of orphaning it.
    states = ["below_yearline_repair", "transition_watch", "transition_watch", "accepted_above_watch"]
    replay = _replay(states, distance=[-5.0, -1.0, 12.0, 24.0])
    trend = pd.DataFrame({
        "as_of_date": replay["as_of_date"],
        # only the last two bars are above MA250 (have a computed trend state)
        "post_confirmation_trend_state": [np.nan, np.nan, "healthy_trend", "overextended_trend"],
        "trend_quality_score": [np.nan, np.nan, 0.6, 0.7],
    })
    h = build_semantic_history(replay, trend)
    eng = h.sort_values("as_of_date")["active_engine"].tolist()
    assert eng[0] == "repair_retry_hazard_engine"          # repair stays repair
    assert eng[1] == "unknown_or_transition"               # transition_watch w/o trend state stays unknown
    assert eng[2] == "post_confirmation_trend_engine"      # transition_watch + trend state ⇒ PROMOTED
    assert eng[3] == "post_confirmation_trend_engine"      # accepted_above_watch ⇒ trend
    # the replay's distance_to_ma250_pct must SURVIVE the trend merge intact (no _x/_y collision), so
    # context_export can read it for both the repair and trend contexts (TO-0 distance threading).
    assert "distance_to_ma250_pct" in h.columns and "distance_to_ma250_pct_x" not in h.columns
    assert float(h.sort_values("as_of_date")["distance_to_ma250_pct"].iloc[-1]) == 24.0


def test_no_trend_history_leaves_engines_unchanged():
    replay = _replay(["below_yearline_repair", "transition_watch", "accepted_above_watch"])
    h = build_semantic_history(replay, None)
    eng = h.sort_values("as_of_date")["active_engine"].tolist()
    assert eng == ["repair_retry_hazard_engine", "unknown_or_transition", "post_confirmation_trend_engine"]
