"""V13.3 Phase 3 — empirical completed-path horizon estimator (ports V12.4.1)."""
import dataclasses

import numpy as np
import pandas as pd
from conftest import CACHE_DIR, CONFIG_DIR

from yearline_universe.hazard import (
    build_empirical_horizon_reference, empirical_horizon_probabilities_for_row,
    HORIZON_PROB_POLICY,
)
from yearline_universe import load_universe_config, run_universe_pipeline, export_single_ticker_context


def _synthetic_panel(n_transitions=6, span=20):
    """Completed at-risk rows: each transition counts down to an event at `span`."""
    rows = []
    for r in range(n_transitions):
        for td in range(0, span + 1):
            rows.append({
                "ticker": "ZZZ", "group": "peerX", "round": r, "transition": "2_to_3",
                "from_date": f"201{r}-01-01", "to_date": f"201{r}-03-01",
                "from_canonical_quality": "strict",
                "is_live_transition": False, "event_retry_today": int(td == span),
                "trading_days_since_touch": td,
                "distance_to_ma250_pct": -10.0 + td * 0.3,
                "drawdown_so_far_pct": 9.0,
            })
    return pd.DataFrame(rows)


def test_reference_builder_computes_remaining_days():
    ref = build_empirical_horizon_reference(_synthetic_panel())
    assert not ref.empty
    assert "remaining_trading_days_to_retry" in ref.columns
    assert (ref["remaining_trading_days_to_retry"] >= 0).all()
    # bucket columns are precomputed once
    assert {"days_since_touch_bucket", "distance_to_ma250_bucket", "drawdown_so_far_bucket"}.issubset(ref.columns)


def test_empirical_probabilities_monotone_and_transparent():
    ref = build_empirical_horizon_reference(_synthetic_panel(span=20))
    # a state ~10 trading days from a typical event
    row = {"ticker": "ZZZ", "group": "peerX", "transition": "2_to_3",
           "from_canonical_quality": "strict", "trading_days_since_touch": 10,
           "distance_to_ma250_pct": -7.0, "drawdown_so_far_pct": 9.0}
    emp = empirical_horizon_probabilities_for_row(row, ref, [10, 20, 40, 60, 90])
    ps = [emp[h]["cumulative_retry_probability"] for h in (10, 20, 40, 60, 90)]
    # cumulative probability is non-decreasing in horizon and bounded in [0,1]
    assert all(0.0 <= p <= 1.0 for p in ps)
    assert all(ps[i] <= ps[i + 1] + 1e-9 for i in range(len(ps) - 1))
    # provenance is exposed
    assert emp[40]["reference_n"] > 0 and isinstance(emp[40]["reference_scope"], str)
    assert emp[40]["estimator"] == HORIZON_PROB_POLICY


def test_real_msft_empirical_canonical_not_saturated_and_diagnostic_preserved():
    uni = load_universe_config(CONFIG_DIR / "universe_mega_cap_ai_infra.yaml")
    uni = dataclasses.replace(uni, replay_start="2024-06-01")
    res = run_universe_pipeline(uni, cache_dir=str(CACHE_DIR), provider="cache", n_jobs=1)

    rhc = export_single_ticker_context(res.ticker_results["MSFT"])["retry_hazard_context"]
    assert rhc["active"] is True
    assert rhc["probability_policy"] == HORIZON_PROB_POLICY
    # CANONICAL (empirical) P60 is a data-driven frequency, NOT pinned at 1.0.
    assert rhc["p_retry_within_60d"] is not None and rhc["p_retry_within_60d"] < 0.999
    # canonical is non-decreasing across horizons
    assert rhc["p_retry_within_10d"] <= rhc["p_retry_within_40d"] + 1e-9
    # provenance present
    assert rhc["p_retry_within_40d_reference_n"] and rhc["p_retry_within_40d_reference_scope"]
    # the demoted state-hold-forward curve is preserved as a diagnostic and DOES saturate.
    diag = rhc["diagnostic_model_state_hold_forward"]
    assert diag is not None and diag["p_retry_within_60d"] >= 0.99   # the old step
    assert diag["p_retry_within_60d"] > rhc["p_retry_within_60d"]      # diagnostic > canonical

    # AAPL (trend engine active) → hazard gated off, no diagnostic block.
    aapl = export_single_ticker_context(res.ticker_results["AAPL"])["retry_hazard_context"]
    assert aapl["active"] is False
    assert aapl["p_retry_within_40d"] is None and aapl["diagnostic_model_state_hold_forward"] is None
