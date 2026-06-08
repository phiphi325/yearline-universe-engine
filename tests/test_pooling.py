import pandas as pd
from conftest import CACHE_DIR, CONFIG_DIR
from yearline_universe.pooling import build_pooled_context


class _FakeResult:
    def __init__(self, ticker, sector, peer, engine, n_events, n_epi, n_success):
        self.ticker = ticker
        self.sector = sector
        self.peer_group = peer
        self.status = "ok"
        self.canonical_events = pd.DataFrame({"x": range(n_events)})
        self.episodes = pd.DataFrame({"episode_outcome": (["success"] * n_success + ["fail"] * (n_epi - n_success))})
        self.latest_context = {"active_engine_context": {"active_engine": engine}}


def _results():
    return {
        "MSFT": _FakeResult("MSFT", "Information Technology", "mega_cap_software", "post_confirmation_trend_engine", 35, 12, 12),
        "NVDA": _FakeResult("NVDA", "Information Technology", "ai_accelerator", "unknown_or_transition", 21, 7, 7),
        "JPM": _FakeResult("JPM", "Financials", "banks", "repair_retry_hazard_engine", 18, 6, 4),
    }


def test_pool_by_sector():
    df = build_pooled_context(_results(), group_by="sector")
    assert set(df["group"]) == {"Information Technology", "Financials"}
    it = df[df["group"] == "Information Technology"].iloc[0]
    assert it["n_tickers"] == 2
    assert it["n_canonical_events"] == 56


def test_pool_by_peer_group():
    df = build_pooled_context(_results(), group_by="peer_group")
    assert len(df) == 3
    assert (df["pooled_metrics_status"].str.contains("basic_counts")).all()


# --- V13.3 pooled evidence -------------------------------------------------
from yearline_universe.pooling import (
    build_gap_drawdown_matrix, build_gap_drawdown_corr_summary,
    build_pooled_attempt_success, build_pooled_evidence, wilson_interval,
)


def test_wilson_interval_bounds():
    lo, hi = wilson_interval(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0


def _synthetic_recovery():
    # deeper drawdown paired with longer gap => strong positive Spearman
    import numpy as np
    n = 30
    dd = np.linspace(1.0, 25.0, n)
    gap = dd * 6 + np.linspace(0, 5, n)  # monotone increasing with dd
    return pd.DataFrame({
        "group": ["g"] * n, "transition": (["1_to_2", "2_to_3"] * n)[:n],
        "gap_days": gap, "drawdown_abs_low_pct": dd, "below_ma250_abs_low_pct": dd * 0.5,
        "next_attempt_success": ([True, False] * n)[:n], "next_attempt_pending": [False] * n,
    })


def test_gap_drawdown_matrix_and_correlation():
    rec = _synthetic_recovery()
    mtx = build_gap_drawdown_matrix(rec)
    assert not mtx.empty and {"matrix_bucket", "next_attempt_success_rate", "n"}.issubset(mtx.columns)
    corr = build_gap_drawdown_corr_summary(rec)
    allt = corr[(corr["group"] == "ALL") & (corr["transition"] == "all_transitions")].iloc[0]
    assert allt["correlation_status"] == "computed"
    assert allt["spearman_gap_vs_drawdown"] > 0.9   # monotone by construction


def test_attempt_success_table():
    ev = pd.DataFrame({
        "group": ["g"] * 6, "canonical_attempt_no": [1, 1, 2, 2, 3, 4],
        "canonical_outcome": ["success", "fail", "success", "pending", "fail", "success"],
        "canonical_quality": ["strict"] * 6,
    })
    att = build_pooled_attempt_success(ev)
    assert set(att["attempt_bucket"]) >= {"1", "2", "3+"}
    assert (att["wilson_low"] <= att["raw_success_rate"]).all()


def test_build_pooled_evidence_real():
    import dataclasses
    from yearline_universe import load_universe_config, run_ticker_pipeline
    uni = load_universe_config(CONFIG_DIR / "universe_mega_cap_ai_infra.yaml")
    uni = dataclasses.replace(uni, replay_start="2024-06-01")
    results = {}
    for sym in ["MSFT", "AAPL"]:
        results[sym] = run_ticker_pipeline(uni.get_ticker(sym), uni, cache_dir=str(CACHE_DIR), provider="cache")
    ev = build_pooled_evidence(results)
    assert ev["n_recovery_transitions"] > 0
    h = ev["headline_correlation"]
    assert h and h["status"] == "computed" and h["spearman"] > 0.5   # drawdown<->time thesis holds
    assert ev["universe"]["gap_drawdown_matrix"] and ev["peer_group"]["attempt_success"]

