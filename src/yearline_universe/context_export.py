"""Repo-ready statistical context export (Engine handoff -> JSON envelope).

Faithful port of V12 Module I (V12.8). Produces:

* ``SingleTickerStatisticalContextEnvelope`` - the per-ticker repo-ready JSON.
* ``UniverseStatisticalContextBundle``       - the universe-level bundle (V13.5;
  a working implementation that simply nests the per-ticker envelopes plus
  placeholder pooled blocks, since pooling lands in V13.3).

The ``option_overlay_research_hint`` block is an *evidence overlay only*. It is
explicitly flagged ``must_not_auto_execute: True`` and carries no broker /
execution semantics, consistent with the V13 "research engine, not trades"
constraint.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

__all__ = [
    "make_json_safe",
    "build_statistical_context_envelope",
    "export_single_ticker_context",
    "export_universe_context_bundle",
    "STATISTICAL_CONTEXT_JSON_SCHEMA",
    "SINGLE_TICKER_SCHEMA_VERSION",
    "UNIVERSE_BUNDLE_SCHEMA_VERSION",
]

SINGLE_TICKER_SCHEMA_VERSION = "v13_single_ticker_statistical_context_envelope"
UNIVERSE_BUNDLE_SCHEMA_VERSION = "v13_universe_statistical_context_bundle"
CONTEXT_VERSION = "v13.1"
MODEL_STACK_VERSION = "yearline_universe_v13.1"


def make_json_safe(obj: Any) -> Any:
    """Recursively convert numpy / pandas / datetime types into JSON-safe values."""
    if isinstance(obj, dict):
        return {str(k): make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [make_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, float):
        return None if math.isnan(obj) else obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj.date())
    try:
        if obj is None:
            return None
        if not isinstance(obj, (dict, list, tuple, str, int, float, bool)) and pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


def _f(x, default=None):
    try:
        if x is None or pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def _i(x, default=None):
    try:
        if x is None or pd.isna(x):
            return default
        return int(x)
    except Exception:
        return default


def _option_overlay_hint(active_engine: str | None, trend_state: str | None) -> dict[str, Any]:
    """Non-executable research overlay hint. Carries NO execution semantics."""
    hint: dict[str, Any] = {
        "active_engine": active_engine,
        "research_hint": None,
        "candidate_action_bias": [],
        "must_not_auto_execute": True,
    }
    if active_engine == "repair_retry_hazard_engine":
        hint["research_hint"] = "defensive_repair_wait_or_light_hedge_context"
        hint["candidate_action_bias"] = ["BUY_LONG_DATED_PUT", "OPEN_COLLAR", "NO_OP"]
    elif active_engine == "post_confirmation_trend_engine":
        if trend_state in {"healthy_trend", "early_confirmation"}:
            hint["research_hint"] = "preserve_upside_convexity_light_income_only"
            hint["candidate_action_bias"] = ["NO_OP", "SELL_COVERED_CALL_PARTIAL"]
        elif trend_state == "overextended_trend":
            hint["research_hint"] = "careful_extension_monetization_without_bearish_flip"
            hint["candidate_action_bias"] = ["SELL_COVERED_CALL_PARTIAL", "ROLL_UP_AND_OUT", "NO_OP"]
        elif trend_state == "trend_deterioration_watch":
            hint["research_hint"] = "increase_protection_prepare_handoff_to_repair_engine"
            hint["candidate_action_bias"] = ["OPEN_COLLAR", "BUY_LONG_DATED_PUT", "REDUCE_COVERAGE"]
        else:
            hint["research_hint"] = "trend_engine_active_but_state_unclear"
            hint["candidate_action_bias"] = ["NO_OP"]
    return hint


def build_statistical_context_envelope(
    ticker: str,
    sector: str,
    peer_group: str,
    semantic_card: Mapping[str, Any],
    latest_semantic_row: Mapping[str, Any],
    calibration_summary: Mapping[str, Any] | None = None,
    source_info: Mapping[str, Any] | None = None,
    retry_timing_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the per-ticker SingleTickerStatisticalContextEnvelope.

    Ticker-agnostic port of V12.8. Inputs are the semantic current-state card,
    the latest semantic-history row, and an optional calibration summary.

    ``retry_timing_context`` (V13.3 Phase 2) is an *additive* block of conditional
    days-to-next-touch estimators (see ``timing.py``); when omitted a dormant stub
    is emitted so the schema key is always present. It does not alter any other
    field.
    """
    semantic_card = dict(semantic_card or {})
    latest = dict(latest_semantic_row or {})
    calibration = dict(calibration_summary or {
        "available": False, "summary": [],
        "warning": "Calibration metrics unavailable; probabilities should be treated as prototype.",
    })

    as_of = semantic_card.get("as_of_date") or latest.get("as_of_date") or (source_info or {}).get("data_as_of")
    if isinstance(as_of, pd.Timestamp):
        as_of = str(as_of.date())
    active_engine = semantic_card.get("active_engine") or latest.get("active_engine")

    repair_context = {
        "active": active_engine == "repair_retry_hazard_engine",
        "mode_state": latest.get("mode_state_replay"),
        "distance_to_ma250_pct": _f(latest.get("distance_to_ma250_pct")),
        "required_rebound_to_ma250_pct": _f(latest.get("required_rebound_to_ma250_pct")),
        "drawdown_so_far_pct": _f(latest.get("drawdown_so_far_pct")),
        "below_ma250_depth_so_far_pct": _f(latest.get("below_ma250_depth_so_far_pct")),
    }
    hazard_active = active_engine == "repair_retry_hazard_engine"
    # V13.3 Phase 3: canonical P(retry<=H) is the EMPIRICAL completed-path estimator
    # (ports V12.4.1). hazard_today stays the logistic one-day hazard; the saturating
    # state-hold-forward model curve is retained only as a labelled diagnostic.
    retry_hazard_context = {
        "active": hazard_active,
        "hazard_today": _f(latest.get("hazard_today_gated")),
        "p_retry_within_10d": _f(latest.get("p_retry_within_10d_gated")),
        "p_retry_within_20d": _f(latest.get("p_retry_within_20d_gated")),
        "p_retry_within_40d": _f(latest.get("p_retry_within_40d_gated")),
        "p_retry_within_60d": _f(latest.get("p_retry_within_60d_gated")),
        "survival_60d": _f(latest.get("survival_60d_gated")),
        "probability_policy": latest.get("horizon_probability_policy") if hazard_active else None,
        "p_retry_within_40d_reference_n": _i(latest.get("p_retry_within_40d_reference_n")) if hazard_active else None,
        "p_retry_within_40d_reference_scope": latest.get("p_retry_within_40d_reference_scope") if hazard_active else None,
        "diagnostic_model_state_hold_forward": {
            "policy": "diagnostic_model_state_hold_forward_not_canonical",
            "p_retry_within_10d": _f(latest.get("p_retry_within_10d_model_state_hold_forward_diagnostic")),
            "p_retry_within_20d": _f(latest.get("p_retry_within_20d_model_state_hold_forward_diagnostic")),
            "p_retry_within_40d": _f(latest.get("p_retry_within_40d_model_state_hold_forward_diagnostic")),
            "p_retry_within_60d": _f(latest.get("p_retry_within_60d_model_state_hold_forward_diagnostic")),
            "note": "Frozen-state forward extrapolation; saturates to 1.0 for deep-below states. Not canonical.",
        } if hazard_active else None,
        "semantics": latest.get("hazard_semantics"),
    }
    # V13.3 Phase 4 (V13.7): if calibration ran, surface the calibrated P + trust gate
    # for the headline 40d horizon, so a consumer knows whether to trust the number.
    if hazard_active and calibration.get("available"):
        live_cal = (calibration.get("live_calibrated_horizon_probabilities") or {}).get("40", {})
        gate40 = (calibration.get("trust_gate") or {}).get("40", {})
        retry_hazard_context["p_retry_within_40d_calibrated"] = _f(live_cal.get("calibrated_probability"))
        retry_hazard_context["calibration_gate_40d"] = {
            "passed": bool(gate40.get("passed")),
            "auc": _f(gate40.get("auc")),
            "mace_raw": _f(gate40.get("mace_raw")),
            "fail_reasons": gate40.get("fail_reasons", []),
        }
        retry_hazard_context["surfaced_probability_is_calibrated"] = bool(gate40.get("passed"))
    trend_context = {
        "active": active_engine == "post_confirmation_trend_engine",
        "trend_state": latest.get("post_confirmation_trend_state"),
        "trend_quality_score": _f(latest.get("trend_quality_score")),
        "pullback_quality_score": _f(latest.get("pullback_quality_score")),
        "overextension_score": _f(latest.get("overextension_score")),
        "deterioration_risk_score": _f(latest.get("deterioration_risk_score")),
        "drawdown_from_post_confirmation_peak_pct": _f(latest.get("drawdown_from_post_confirmation_peak_pct")),
        "days_since_confirmation": _i(latest.get("days_since_confirmation")),
        "semantics": latest.get("trend_semantics"),
    }
    option_hint = _option_overlay_hint(active_engine, trend_context.get("trend_state"))

    timing_block = dict(retry_timing_context) if retry_timing_context else {
        "schema": "v13_retry_timing_conditional_estimators",
        "active": False,
        "reason": "not_computed_for_this_context",
        "active_engine": active_engine,
        "note": "Conditional retry-timing estimators were not attached to this envelope.",
        "must_not_auto_execute": True,
    }

    warnings = []
    if not calibration.get("available"):
        warnings.append("calibration_metrics_unavailable")
    warnings.append("context_is_research_overlay_not_trading_signal")
    warnings.append("do_not_modify_locked_regime_taxonomy_without_repo_migration")

    envelope = {
        "schema_version": SINGLE_TICKER_SCHEMA_VERSION,
        "context_version": CONTEXT_VERSION,
        "model_stack_version": MODEL_STACK_VERSION,
        "as_of": as_of,
        "ticker": ticker,
        "sector": sector,
        "peer_group": peer_group,
        "source": dict(source_info or {}),
        "active_engine_context": {
            "active_engine": active_engine,
            "mode_state": latest.get("mode_state_replay"),
            "handoff_interpretation": semantic_card.get("interpretation"),
        },
        "repair_retry_context": repair_context,
        "retry_hazard_context": retry_hazard_context,
        "post_confirmation_trend_context": trend_context,
        "retry_timing_context": timing_block,
        "calibration_context": calibration,
        "option_overlay_research_hint": option_hint,
        "warnings": warnings,
        "disclaimers": [
            "Educational research only. Not financial advice.",
            "This context is an evidence overlay and must not auto-execute trades.",
        ],
    }
    return make_json_safe(envelope)


def export_single_ticker_context(result) -> dict:
    """Return the repo-ready per-ticker context dict for a TickerPipelineResult.

    The envelope is built during the pipeline and stored on
    ``result.latest_context``; this returns a JSON-safe copy.
    """
    ctx = getattr(result, "latest_context", None) or {}
    return make_json_safe(dict(ctx))


def export_universe_context_bundle(result) -> dict:
    """Build the UniverseStatisticalContextBundle (V13.5).

    V13.1 produces a working bundle that nests each ticker's envelope and leaves
    pooled blocks as placeholders (pooling is V13.3). Schema is stable now so
    downstream consumers can integrate against it immediately.
    """
    ticker_results = getattr(result, "ticker_results", {}) or {}
    ticker_contexts = {
        t: make_json_safe(dict(getattr(r, "latest_context", {}) or {}))
        for t, r in ticker_results.items()
    }
    warnings = []
    failed = [t for t, r in ticker_results.items() if getattr(r, "status", "ok") != "ok"]
    if failed:
        warnings.append(f"tickers_failed: {','.join(sorted(failed))}")

    # V13.3 pooled gap x drawdown evidence (descriptive; not a forecast).
    from .pooling import build_pooled_evidence, _pool_frames, build_gap_drawdown_matrix
    evidence = build_pooled_evidence(ticker_results)
    if evidence.get("n_recovery_transitions", 0) < 30:
        warnings.append("pooled_evidence_low_sample")

    # V13.3 Phase 2 pooled conditional days-to-next-touch estimators. For each
    # repair-active ticker, condition the POOLED cross-ticker recovery on the
    # ticker's live setup (peer-group + ALL scopes). Per-ticker envelopes keep
    # their own self-conditioned block; this is the richer pooled view.
    from .timing import build_retry_timing_context
    from .config import StudyConfig
    pooled_recovery, _ev, _n = _pool_frames(ticker_results)
    pooled_timing: dict[str, Any] = {}
    if not pooled_recovery.empty:
        rec = pooled_recovery.copy()
        rec["group"] = rec["peer_group"]
        timing_matrix = build_gap_drawdown_matrix(rec, StudyConfig())
        for t, r in ticker_results.items():
            if getattr(r, "status", None) != "ok":
                continue
            live = getattr(r, "live_diagnostic", {}) or {}
            ae = (getattr(r, "latest_context", {}) or {}).get("active_engine_context", {}).get("active_engine")
            block = build_retry_timing_context(
                live, rec, timing_matrix, peer_group=getattr(r, "peer_group", "unknown"),
                active_engine=ae, scope="universe_pooled",
            )
            if block.get("active"):
                pooled_timing[t] = block

    bundle = {
        "schema_version": UNIVERSE_BUNDLE_SCHEMA_VERSION,
        "context_version": CONTEXT_VERSION,
        "universe_name": getattr(result, "universe_name", None),
        "as_of": getattr(result, "as_of", None),
        "ticker_contexts": ticker_contexts,
        "pooled_context": {
            "headline_correlation": evidence.get("headline_correlation", {}),
            "n_recovery_transitions": evidence.get("n_recovery_transitions"),
            "n_canonical_events": evidence.get("n_canonical_events"),
            "peer_group": evidence.get("peer_group", {}),
            "sector": evidence.get("sector", {}),
            "universe": evidence.get("universe", {}),
            "retry_timing": pooled_timing,
            "disclaimers": evidence.get("disclaimers", []),
        },
        "warnings": warnings,
        "disclaimers": [
            "Educational research only. Not financial advice.",
            "Universe bundle is an evidence overlay and must not auto-execute trades.",
        ],
    }
    return make_json_safe(bundle)


# JSON schema for the per-ticker envelope (repo integration aid).
STATISTICAL_CONTEXT_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://option-mgmt-2026.local/schemas/statistical-context/v13.schema.json",
    "title": "SingleTickerStatisticalContextEnvelope",
    "type": "object",
    "required": [
        "schema_version", "context_version", "model_stack_version", "as_of", "ticker",
        "sector", "peer_group", "active_engine_context", "repair_retry_context",
        "retry_hazard_context", "post_confirmation_trend_context", "calibration_context",
        "option_overlay_research_hint", "warnings", "disclaimers",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "context_version": {"type": "string"},
        "model_stack_version": {"type": "string"},
        "as_of": {"type": ["string", "null"]},
        "ticker": {"type": "string"},
        "sector": {"type": "string"},
        "peer_group": {"type": "string"},
        "active_engine_context": {"type": "object"},
        "repair_retry_context": {"type": "object"},
        "retry_hazard_context": {"type": "object"},
        "post_confirmation_trend_context": {"type": "object"},
        "retry_timing_context": {"type": "object"},
        "calibration_context": {"type": "object"},
        "option_overlay_research_hint": {"type": "object"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "disclaimers": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}


def write_json(obj: Any, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(make_json_safe(obj), fh, indent=2)
    return path
