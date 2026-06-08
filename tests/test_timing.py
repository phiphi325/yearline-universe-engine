import math

import numpy as np
import pandas as pd
from conftest import CACHE_DIR, CONFIG_DIR

from yearline_universe.timing import (
    required_rebound_to_ma250_pct, build_live_retry_setup,
    build_estimator_comparison, build_retry_timing_context,
)
from yearline_universe.pooling import build_gap_drawdown_matrix
from yearline_universe.context_export import build_statistical_context_envelope


# --- helpers ---------------------------------------------------------------

def test_required_rebound_to_ma250():
    # -10.10% distance => price/MA250 = 0.899 => need ~11.23% rebound to touch MA250.
    assert round(required_rebound_to_ma250_pct(-10.10), 2) == 11.23
    assert required_rebound_to_ma250_pct(5.0) == 0.0          # already above
    assert math.isnan(required_rebound_to_ma250_pct(float("nan")))


def _live(state="below_yearline_after_latest_touch", attempt=2, dd=10.0):
    return {
        "ticker": "ZZZ", "state": state, "latest_attempt_no": attempt,
        "latest_touch_date": "2026-06-01", "as_of": "2026-06-05",
        "latest_outcome": "pending", "mode_transition_state_prototype": "transition_repair",
        "current_distance_to_ma250_pct": -10.10,
        "current_drawdown_since_last_touch_low_pct": -dd,
        "trading_days_since_last_touch": 4,
    }


def test_live_setup_defaults_to_live_drawdown_and_transition():
    s = build_live_retry_setup(_live(attempt=2, dd=10.0), "peerX")
    assert s["target_transition"] == "2_to_3"
    assert s["group"] == "peerX"
    assert s["drawdown_assumption_abs_pct"] == 10.0           # abs of live dd
    assert s["drawdown_assumption_source"] == "live_current_drawdown_so_far"
    assert s["days_elapsed_since_latest_touch"] == 4
    # explicit override wins
    s2 = build_live_retry_setup(_live(), "peerX", drawdown_assumption_pct=12.5)
    assert s2["drawdown_assumption_abs_pct"] == 12.5 and s2["drawdown_assumption_source"] == "explicit_override"


def _synthetic_recovery(transition="2_to_3", n=20):
    rng = np.random.default_rng(0)
    dd = np.linspace(3.0, 20.0, n)
    gap = dd * 4.0 + rng.normal(0, 2, n)          # gap grows with drawdown
    return pd.DataFrame({
        "ticker": ["ZZZ"] * n, "group": ["peerX"] * n, "round": range(n),
        "transition": [transition] * n,
        "from_date": pd.date_range("2015-01-01", periods=n, freq="90D").astype(str),
        "to_date": pd.date_range("2015-04-01", periods=n, freq="90D").astype(str),
        "gap_days": np.abs(gap).round(), "drawdown_abs_low_pct": dd,
        "below_ma250_abs_low_pct": dd * 0.5,
        "next_attempt_success": [True, False] * (n // 2),
        "next_attempt_pending": [False] * n,
    })


def test_estimator_comparison_structure_and_remaining_math():
    rec = _synthetic_recovery()
    mtx = build_gap_drawdown_matrix(rec)
    setup = build_live_retry_setup(_live(attempt=2, dd=10.0), "peerX")
    cmp = build_estimator_comparison(rec, mtx, setup)
    assert not cmp.empty
    # all four method families represented
    methods = " ".join(cmp["method"].tolist()).lower()
    for fam in ["historical median", "matrix interpolation", "nearest neighbors", "theil-sen"]:
        assert fam in methods
    # remaining = max(total - elapsed, 0); rough date is elapsed-consistent
    el = setup["days_elapsed_since_latest_touch"]
    for _, r in cmp.dropna(subset=["estimated_total_gap_days"]).iterrows():
        assert r["estimated_remaining_days_from_as_of"] == max(r["estimated_total_gap_days"] - el, 0)
        assert r["rough_retry_date_if_repair_continues"] is None or str(r["rough_retry_date_if_repair_continues"]) >= "2026-06-05"


def test_repair_active_block_is_populated():
    rec = _synthetic_recovery()
    blk = build_retry_timing_context(_live(), rec, peer_group="peerX",
                                     active_engine="repair_retry_hazard_engine")
    assert blk["active"] is True
    assert blk["is_descriptive_evidence_not_forecast"] is True
    assert blk["setup"]["target_transition"] == "2_to_3"
    assert blk["consensus"]["available"] is True
    assert blk["consensus"]["central_remaining_days"] >= 0
    assert len(blk["estimators"]) > 0
    assert blk["must_not_auto_execute"] is True or "must_not_auto_execute" in str(blk["disclaimers"]).lower()


def test_gating_trend_engine_is_dormant():
    rec = _synthetic_recovery()
    # accepted-above-yearline => repair engine dormant
    blk = build_retry_timing_context(_live(state="accepted_above_yearline"), rec, peer_group="peerX")
    assert blk["active"] is False and "dormant" in blk["reason"]
    # active_engine arg overrides the inferred state
    blk2 = build_retry_timing_context(_live(), rec, peer_group="peerX",
                                      active_engine="post_confirmation_trend_engine")
    assert blk2["active"] is False


def test_envelope_additive_existing_fields_byte_identical():
    import json
    semantic_card = {"active_engine": "repair_retry_hazard_engine", "as_of_date": "2026-06-05",
                     "interpretation": "repair active"}
    latest = {"mode_state_replay": "below_yearline_repair", "distance_to_ma250_pct": -10.1,
              "required_rebound_to_ma250_pct": 11.23, "p_retry_within_40d_gated": 0.3}
    timing = build_retry_timing_context(_live(), _synthetic_recovery(), peer_group="peerX",
                                        active_engine="repair_retry_hazard_engine")
    env_with = build_statistical_context_envelope("ZZZ", "Sec", "peerX", semantic_card, latest,
                                                  retry_timing_context=timing)
    env_without = build_statistical_context_envelope("ZZZ", "Sec", "peerX", semantic_card, latest)
    assert "retry_timing_context" in env_with and env_with["retry_timing_context"]["active"] is True
    # every OTHER field must be byte-identical regardless of the timing input
    def strip(e): return json.dumps({k: v for k, v in e.items() if k != "retry_timing_context"}, sort_keys=True)
    assert strip(env_with) == strip(env_without)


def test_real_msft_timing_and_pooled_bundle():
    import dataclasses
    from yearline_universe import load_universe_config, run_universe_pipeline
    uni = load_universe_config(CONFIG_DIR / "universe_mega_cap_ai_infra.yaml")
    uni = dataclasses.replace(uni, replay_start="2024-06-01")
    res = run_universe_pipeline(uni, cache_dir=str(CACHE_DIR), provider="cache", n_jobs=1)

    # Per-ticker self-conditioned block: MSFT is below MA250 as_of 2026-06-05 -> repair active.
    rtc = res.ticker_results["MSFT"].latest_context["retry_timing_context"]
    assert rtc["active"] is True
    assert rtc["setup"]["target_transition"] == "2_to_3"
    assert rtc["conditioning_scope"] == "single_ticker_self_conditioned"
    assert rtc["consensus"]["available"] is True

    # AAPL is accepted above MA250 -> dormant stub (gated off).
    assert res.ticker_results["AAPL"].latest_context["retry_timing_context"]["active"] is False

    # Pooled bundle carries the richer universe-pooled estimate for repair-active tickers.
    pooled = res.universe_context_bundle["pooled_context"]["retry_timing"]
    assert "MSFT" in pooled and pooled["MSFT"]["conditioning_scope"] == "universe_pooled"
    # the pooled 2->3 conditioning sample is larger than MSFT's own
    assert pooled["MSFT"]["n_conditioning_transitions"] >= rtc["n_conditioning_transitions"]
