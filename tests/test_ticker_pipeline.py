import dataclasses
import pytest
from conftest import CACHE_DIR, CONFIG_DIR
from yearline_universe import (
    load_universe_config, run_ticker_pipeline, export_single_ticker_context,
    validate_ticker_sanity,
)

ENVELOPE_TOP_KEYS = {
    "schema_version", "context_version", "model_stack_version", "as_of", "ticker",
    "sector", "peer_group", "source", "active_engine_context", "repair_retry_context",
    "retry_hazard_context", "post_confirmation_trend_context", "retry_timing_context",
    "calibration_context", "option_overlay_research_hint", "warnings", "disclaimers",
}


@pytest.fixture(scope="module")
def fast_universe():
    # Late replay_start keeps the daily-replay loop short so tests run fast.
    uni = load_universe_config(CONFIG_DIR / "universe_mega_cap_ai_infra.yaml")
    return dataclasses.replace(uni, replay_start="2024-06-01")


@pytest.mark.parametrize("sym", ["MSFT", "AAPL", "NVDA"])
def test_pipeline_runs_for_each_ticker(fast_universe, sym):
    res = run_ticker_pipeline(fast_universe.get_ticker(sym), fast_universe,
                              cache_dir=str(CACHE_DIR), provider="cache")
    assert res.status == "ok", res.error
    assert res.ticker == sym
    assert not res.canonical_events.empty
    assert validate_ticker_sanity(res)["passed"]


def test_envelope_schema_is_stable(fast_universe):
    envs = {}
    for sym in ["MSFT", "AAPL", "NVDA"]:
        res = run_ticker_pipeline(fast_universe.get_ticker(sym), fast_universe,
                                  cache_dir=str(CACHE_DIR), provider="cache")
        envs[sym] = export_single_ticker_context(res)
    # identical top-level schema for every ticker
    for sym, e in envs.items():
        assert set(e.keys()) == ENVELOPE_TOP_KEYS, sym
        assert e["schema_version"] == "v13_single_ticker_statistical_context_envelope"
        assert e["ticker"] == sym
        assert e["option_overlay_research_hint"]["must_not_auto_execute"] is True


def test_pool_hazard_enriches_the_empirical_reference(fast_universe):
    # V13.3 Phase 5: pooling adds cross-ticker rows to the empirical horizon reference
    # (more samples ⇒ state-conditioned scopes can discriminate ⇒ the trust gate can pass).
    from yearline_universe.ticker_pipeline import _build_foundation
    from yearline_universe.hazard import run_hazard_layer, build_empirical_horizon_reference

    fnd = {t: _build_foundation(fast_universe.get_ticker(t), fast_universe,
                                cache_dir=str(CACHE_DIR), provider="cache")
           for t in ["MSFT", "AAPL", "NVDA"]}
    pooled = {t: {"peer_group": fast_universe.get_ticker(t).peer_group,
                  "price_df": f["price_df"], "recovery_table": f["recovery"], "live_diagnostic": f["live"]}
              for t, f in fnd.items()}
    study = fast_universe.study_for(fast_universe.get_ticker("MSFT"))

    single = run_hazard_layer("MSFT", "mega_cap_software", fnd["MSFT"]["price_df"],
                              fnd["MSFT"]["recovery"], fnd["MSFT"]["live"], study)
    pooled_run = run_hazard_layer("MSFT", "mega_cap_software", fnd["MSFT"]["price_df"],
                                  fnd["MSFT"]["recovery"], fnd["MSFT"]["live"], study, pooled_data=pooled)

    n_single = len(build_empirical_horizon_reference(single["hazard_panel"]))
    n_pooled = len(build_empirical_horizon_reference(pooled_run["hazard_panel"]))
    assert n_pooled > n_single                      # cross-ticker rows were added
    assert pooled_run["hazard_context"]["available"]
    assert pooled_run["hazard_context"]["training_scope"] == "pooled_universe"


def test_failure_isolation_for_missing_ticker(fast_universe):
    # A ticker with no cached data fails in isolation (status="error"), not a crash.
    # (The whole mega-cap universe is now cached, so fabricate an uncached symbol.)
    missing = dataclasses.replace(fast_universe.get_ticker("MSFT"), ticker="ZZZZ_NO_DATA")
    res = run_ticker_pipeline(missing, fast_universe, cache_dir=str(CACHE_DIR), provider="cache")
    assert res.status == "error"
    assert res.error


def test_parallel_universe_matches_serial(fast_universe):
    # run_universe_pipeline(n_jobs=2) must produce byte-identical envelopes to serial.
    import json
    from yearline_universe import run_universe_pipeline, export_single_ticker_context

    serial = run_universe_pipeline(fast_universe, cache_dir=str(CACHE_DIR), provider="cache", n_jobs=1)
    parallel = run_universe_pipeline(fast_universe, cache_dir=str(CACHE_DIR), provider="cache", n_jobs=2)

    assert serial.run_manifest["n_ok"] == parallel.run_manifest["n_ok"]
    assert list(serial.ticker_results.keys()) == list(parallel.ticker_results.keys())  # config order preserved
    for t, r in serial.ticker_results.items():
        if r.status == "ok":
            assert (json.dumps(export_single_ticker_context(r), sort_keys=True)
                    == json.dumps(export_single_ticker_context(parallel.ticker_results[t]), sort_keys=True)), t
