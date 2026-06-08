"""Generic ticker pipeline + universe batch runner.

``run_ticker_pipeline(ticker_config, universe_config)`` runs ANY configured
ticker through the identical code path:

    load -> detect -> canonical events -> episodes/recovery -> mode features
    -> live diagnostic -> hazard (ML + survival) -> daily replay
    -> post-confirmation trend -> semantic engine handoff
    -> repo-ready statistical context envelope

There is no ticker-specific branching anywhere. ``run_universe_pipeline``
(V13.2 stretch) batches all configured tickers, isolating per-ticker failures
and emitting a run manifest + universe bundle.
"""

from __future__ import annotations

import traceback
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import (
    StudyConfig, TickerConfig, UniverseConfig,
    TickerPipelineResult, UniversePipelineResult,
)
from .data_loader import load_price_data
from .event_detection import detect_source_attempts, build_canonical_events
from .episodes import (
    build_episode_table, build_recovery_table, enrich_episodes_with_recovery,
    build_mode_transition_features, build_live_diagnostic,
)
from .hazard import run_hazard_layer
from .replay import build_replay_history, build_replay_history_incremental
from .trend import build_post_confirmation_trend_state_history, build_post_confirmation_latest_context
from .semantic import build_semantic_history, build_current_state_card
from .context_export import build_statistical_context_envelope, export_universe_context_bundle, make_json_safe
from .pooling import build_gap_drawdown_matrix
from .timing import build_retry_timing_context
from .indicators import date_str

__all__ = ["run_ticker_pipeline", "run_universe_pipeline"]


def _build_foundation(ticker_config: TickerConfig, universe_config: UniverseConfig, *,
                      cache_dir: str | None, provider: str) -> dict[str, Any]:
    """Load price + detect events + build recovery/mode/live (the deterministic base).

    Extracted so the universe runner can build every ticker's foundation once, pool
    them, and feed them back in (``prebuilt_foundation``) without recomputing.
    """
    ticker = ticker_config.ticker
    study = universe_config.study_for(ticker_config)
    price_df = load_price_data(ticker, config=study, cache_dir=cache_dir, provider=provider)
    strict = detect_source_attempts(ticker, price_df, "strict", study)
    loose = detect_source_attempts(ticker, price_df, "loose", study)
    if strict.empty and loose.empty:
        source = pd.DataFrame()
    else:
        source = pd.concat([strict, loose], ignore_index=True)
        source = source.sort_values(["trading_loc", "detector"]).reset_index(drop=True)
        source["source_event_id"] = np.arange(1, len(source) + 1)
    events = build_canonical_events(ticker, price_df, source, study)
    episodes = build_episode_table(events)
    recovery = build_recovery_table(price_df, events, study)
    episodes = enrich_episodes_with_recovery(episodes, recovery)
    mode_features = build_mode_transition_features(episodes, study)
    live = build_live_diagnostic(ticker, price_df, events, mode_features, study)
    return {"study": study, "price_df": price_df, "source": source, "events": events,
            "episodes": episodes, "recovery": recovery, "mode_features": mode_features, "live": live}


def run_ticker_pipeline(
    ticker_config: TickerConfig,
    universe_config: UniverseConfig,
    *,
    cache_dir: str | None = None,
    provider: str = "auto",
    pooled_data: Mapping[str, Mapping[str, Any]] | None = None,
    incremental: bool = False,
    state_dir: str | None = None,
    fit_ml_models: bool = False,
    calibrate: bool = False,
    calibration_model: Mapping[str, Any] | None = None,
    prebuilt_foundation: Mapping[str, Any] | None = None,
) -> TickerPipelineResult:
    """Run the full per-ticker statistical-context pipeline. Ticker-agnostic.

    Set ``incremental=True`` with a ``state_dir`` to use the persistent daily
    replay cache (only the newest bars are scored; falls back to a full replay
    when a split/dividend/model change invalidates the cache). Output is
    identical to a full run either way.

    ``fit_ml_models`` (default False): the statistical-context envelope does NOT
    consume the prototype retry-timing / quality ML predictions, so they are
    skipped by default — this avoids the timing model's 300-fit Huber bootstrap
    (the dominant cost on high-event tickers, ~6-7s for MSFT). Enabling it adds
    that cost and attaches the predictions to ``result.manifest["ml_models"]``;
    the envelope is unchanged either way. Enable only when you actually need those
    predictions (research, or future pooled-hazard / downstream consumers).
    """
    ticker = ticker_config.ticker
    sector = ticker_config.sector
    peer_group = ticker_config.peer_group

    try:
        # --- Foundation (built here, or reused from the universe pooling pass) ---
        f = prebuilt_foundation if prebuilt_foundation is not None else _build_foundation(
            ticker_config, universe_config, cache_dir=cache_dir, provider=provider)
        study = f["study"]
        price_df = f["price_df"]; source = f["source"]; events = f["events"]
        episodes = f["episodes"]; recovery = f["recovery"]
        mode_features = f["mode_features"]; live = f["live"]

        # --- Hazard (ML timing/quality + discrete-time survival) ------------
        hz = run_hazard_layer(ticker, peer_group, price_df, recovery, live, study,
                              pooled_data=pooled_data, fit_ml_models=fit_ml_models,
                              calibrate=calibrate, calibration_model=calibration_model)
        hazard_fit = hz["hazard_fit"]
        hazard_history = hz["hazard_history"]
        hazard_context = hz["hazard_context"]

        # --- Daily replay (reuses the fitted hazard model) ------------------
        if incremental and state_dir:
            replay_history, replay_mode = build_replay_history_incremental(
                ticker, price_df, events, peer_group, hazard_fit, study,
                replay_start=universe_config.replay_start, state_dir=state_dir,
            )
        else:
            replay_history = build_replay_history(
                ticker, price_df, events, peer_group, hazard_fit, study,
                replay_start=universe_config.replay_start,
            )
            replay_mode = "full"

        # --- Post-confirmation trend engine ---------------------------------
        trend_history = build_post_confirmation_trend_state_history(
            ticker, price_df, study, start_date=universe_config.replay_start,
        )
        trend_context = build_post_confirmation_latest_context(trend_history, ticker, price_df, study)

        # --- Semantic active-engine handoff ---------------------------------
        semantic_history = build_semantic_history(replay_history, trend_history)
        semantic_card = build_current_state_card(semantic_history)

        # --- Repo-ready statistical context envelope ------------------------
        latest_row = (
            semantic_history.sort_values("as_of_date").iloc[-1].to_dict()
            if semantic_history is not None and not semantic_history.empty else {}
        )
        cal_ctx = hz.get("calibration_context") or {}
        if cal_ctx.get("available"):
            # V13.3 Phase 4 (V13.7): real horizon calibration of the empirical estimator.
            calibration_summary = dict(cal_ctx)
            calibration_summary["hazard_model_card"] = hazard_context.get("model_card")
            calibration_summary.setdefault("training_scope", hazard_context.get("training_scope"))
        else:
            calibration_summary = {
                "available": False,
                "summary": [],
                "warning": "Calibration not run (pass calibrate=True). Hazard probabilities are an "
                           "uncalibrated prototype; horizon calibration + gating is V13.7 / Phase 4.",
                "hazard_model_card": hazard_context.get("model_card"),
                "training_scope": hazard_context.get("training_scope"),
            }
        data_as_of = date_str(price_df.index[-1]) if len(price_df) else None
        source_info = {
            "engine": "yearline_universe_v13",
            "universe_name": universe_config.universe_name,
            "upstream_data_start": study.start,
            "replay_start": universe_config.replay_start,
            "data_provider": price_df.attrs.get("provider"),
            "data_as_of": data_as_of,
        }
        # --- Conditional days-to-next-touch estimators (V13.3 Phase 2) ------
        # Additive, repair-regime-gated. Self-conditioned on THIS ticker's own
        # recovery history (the universe bundle re-derives a pooled version).
        active_engine = semantic_card.get("active_engine") or latest_row.get("active_engine")
        rec_t = recovery.copy()
        matrix_t = None
        if not rec_t.empty:
            rec_t["group"] = peer_group
            matrix_t = build_gap_drawdown_matrix(rec_t, study)
        retry_timing = build_retry_timing_context(
            live, rec_t, matrix_t, peer_group=peer_group, config=study,
            active_engine=active_engine, scope="single_ticker_self_conditioned",
        )

        envelope = build_statistical_context_envelope(
            ticker, sector, peer_group, semantic_card, latest_row,
            calibration_summary=calibration_summary, source_info=source_info,
            retry_timing_context=retry_timing,
        )

        manifest = {
            "ticker": ticker, "sector": sector, "peer_group": peer_group, "status": "ok",
            "data_provider": price_df.attrs.get("provider"),
            "first_price_bar": date_str(price_df.index[0]) if len(price_df) else None,
            "latest_price_bar": data_as_of,
            "n_price_bars": int(len(price_df)),
            "n_source_attempts": int(len(source)),
            "n_canonical_events": int(len(events)),
            "n_canonical_episodes": int(len(episodes)),
            "n_recovery_transitions": int(len(recovery)),
            "n_replay_rows": int(len(replay_history)) if replay_history is not None else 0,
            "replay_mode": replay_mode,
            "active_engine": envelope.get("active_engine_context", {}).get("active_engine"),
        }
        if fit_ml_models:
            tp, qp = hz.get("timing_prediction"), hz.get("quality_prediction")
            manifest["ml_models"] = make_json_safe({
                "timing_status": hz.get("timing_status"),
                "timing_prediction": (tp.iloc[0].to_dict() if tp is not None and not tp.empty else None),
                "quality_prediction": (qp.iloc[0].to_dict() if qp is not None and not qp.empty else None),
                "ml_dataset_rows": hz.get("ml_dataset_rows"),
            })

        return TickerPipelineResult(
            ticker=ticker, sector=sector, peer_group=peer_group, status="ok", error=None,
            price_df=price_df, source_attempts=source, canonical_events=events,
            episodes=episodes, recovery_table=recovery, mode_features=mode_features,
            replay_history=replay_history, hazard_history=hazard_history,
            trend_history=trend_history, semantic_history=semantic_history,
            live_diagnostic=live, latest_context=envelope, manifest=manifest,
        )

    except Exception as exc:  # per-ticker failure isolation
        return TickerPipelineResult(
            ticker=ticker, sector=sector, peer_group=peer_group, status="error",
            error=f"{type(exc).__name__}: {exc}",
            live_diagnostic={"ticker": ticker, "state": "error", "error": str(exc)},
            latest_context={},
            manifest={"ticker": ticker, "status": "error", "error": str(exc),
                      "traceback": traceback.format_exc()[-2000:]},
        )


def _error_result(tc: TickerConfig, exc: Exception) -> TickerPipelineResult:
    return TickerPipelineResult(
        ticker=tc.ticker, sector=tc.sector, peer_group=tc.peer_group,
        status="error", error=f"worker_process_failed: {type(exc).__name__}: {exc}",
        live_diagnostic={"ticker": tc.ticker, "state": "error", "error": str(exc)},
        latest_context={},
        manifest={"ticker": tc.ticker, "status": "error", "error": str(exc)},
    )


def run_universe_pipeline(
    universe_config: UniverseConfig,
    *,
    cache_dir: str | None = None,
    provider: str = "auto",
    n_jobs: int = 1,
    incremental: bool = False,
    state_dir: str | None = None,
    fit_ml_models: bool = False,
    calibrate: bool = False,
    pool_hazard: bool = False,
) -> UniversePipelineResult:
    """V13.2 batch runner: run every configured ticker, isolate failures, emit a manifest + bundle.

    Parallelism: ``n_jobs=1`` (default) runs serially. ``n_jobs>1`` runs tickers
    across processes via ``ProcessPoolExecutor``; ``n_jobs<=0`` uses all CPU cores.
    Tickers are independent and each run is deterministic (fixed RANDOM_SEED), so
    parallel output is identical to serial. Failures stay isolated even if a whole
    worker process raises. Results are always returned in universe-config order.

    ``pool_hazard`` (V13.3 Phase 5, default False): build every ticker's foundation
    once, then run each ticker's hazard / empirical-horizon reference / calibration on
    the **pooled** universe panel (so state-conditioned scopes have enough samples to
    discriminate — see phase_05). This is output-changing for the hazard/calibration
    blocks; the descriptive/timing/trend blocks are unaffected.
    """
    tcs = list(universe_config.tickers)

    # V13.3 Phase 5: optional pooled hazard/reference/calibration. Build foundations
    # once (a cheap deterministic base) and assemble the cross-ticker pooled panel.
    foundations: dict[str, Any] = {}
    pooled_data: dict[str, Any] | None = None
    if pool_hazard:
        for tc in tcs:
            try:
                foundations[tc.ticker] = _build_foundation(tc, universe_config, cache_dir=cache_dir, provider=provider)
            except Exception:
                foundations[tc.ticker] = None
        pooled_data = {
            tc.ticker: {"peer_group": tc.peer_group, "price_df": foundations[tc.ticker]["price_df"],
                        "recovery_table": foundations[tc.ticker]["recovery"],
                        "live_diagnostic": foundations[tc.ticker]["live"]}
            for tc in tcs if foundations.get(tc.ticker) is not None
        }

    # V13.3 Phase 6 follow-up: build the (live-ticker-independent) pooled calibration
    # model ONCE here, then reuse it across tickers — instead of recomputing the
    # identical LOTO calibration N times. The per-ticker cost becomes the cheap live apply.
    calibration_model = None
    if pool_hazard and calibrate and pooled_data:
        from .hazard import build_hazard_daily_panel
        from .calibration import build_calibration_model
        study0 = universe_config.study_for(tcs[0])
        any_ticker = next(iter(pooled_data))
        pooled_panel = build_hazard_daily_panel(pooled_data, any_ticker, study0)
        if not pooled_panel.empty:
            calibration_model = build_calibration_model(pooled_panel)

    base = dict(cache_dir=cache_dir, provider=provider, incremental=incremental,
                state_dir=state_dir, fit_ml_models=fit_ml_models, calibrate=calibrate)

    def _kw(tc: TickerConfig) -> dict:
        k = dict(base)
        if pool_hazard:
            k["pooled_data"] = pooled_data
            k["prebuilt_foundation"] = foundations.get(tc.ticker)
            if calibration_model is not None:
                k["calibration_model"] = calibration_model
        return k

    if n_jobs == 1 or len(tcs) <= 1:
        results = [run_ticker_pipeline(tc, universe_config, **_kw(tc)) for tc in tcs]
    else:
        import os
        from concurrent.futures import ProcessPoolExecutor, as_completed
        max_workers = (os.cpu_count() or 1) if n_jobs <= 0 else n_jobs
        by_ticker: dict[str, TickerPipelineResult] = {}
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            fut_to_tc = {
                ex.submit(run_ticker_pipeline, tc, universe_config, **_kw(tc)): tc
                for tc in tcs
            }
            for fut in as_completed(fut_to_tc):
                tc = fut_to_tc[fut]
                try:
                    by_ticker[tc.ticker] = fut.result()
                except Exception as exc:  # worker-process-level failure isolation
                    by_ticker[tc.ticker] = _error_result(tc, exc)
        results = [by_ticker[tc.ticker] for tc in tcs]  # preserve config order

    ticker_results: dict[str, TickerPipelineResult] = {}
    manifest_rows = []
    for tc, res in zip(tcs, results):
        ticker_results[tc.ticker] = res
        manifest_rows.append({
            "ticker": tc.ticker, "sector": tc.sector, "peer_group": tc.peer_group,
            "status": res.status, "error": res.error,
            "n_canonical_events": (res.manifest or {}).get("n_canonical_events"),
            "active_engine": (res.manifest or {}).get("active_engine"),
            "data_as_of": (res.manifest or {}).get("latest_price_bar"),
        })

    as_ofs = [r.manifest.get("latest_price_bar") for r in ticker_results.values() if r.status == "ok" and r.manifest.get("latest_price_bar")]
    as_of = max(as_ofs) if as_ofs else None
    n_ok = sum(1 for r in ticker_results.values() if r.status == "ok")

    run_manifest = {
        "schema_version": "v13_universe_run_manifest",
        "universe_name": universe_config.universe_name,
        "as_of": as_of,
        "n_tickers": len(ticker_results),
        "n_ok": n_ok,
        "n_failed": len(ticker_results) - n_ok,
        "tickers": manifest_rows,
    }

    result = UniversePipelineResult(
        universe_name=universe_config.universe_name, as_of=as_of,
        ticker_results=ticker_results, run_manifest=run_manifest,
    )
    bundle = export_universe_context_bundle(result)
    return UniversePipelineResult(
        universe_name=universe_config.universe_name, as_of=as_of,
        ticker_results=ticker_results, universe_context_bundle=bundle, run_manifest=run_manifest,
    )
