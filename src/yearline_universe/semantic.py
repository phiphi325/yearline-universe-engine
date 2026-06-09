"""Semantic active-engine handoff (dashboard semantics / engine arbitration).

Faithful port of V12 Module H (V12.12). Decides which engine is active
(repair/retry/hazard vs post-confirmation trend) on each replay date, gates the
hazard fields accordingly, merges the trend-state fields, and produces the
current active-engine state card that the context envelope consumes.

NOTE: this module has no home in the spec's module list; it is the documented
V13 addition (the spec's module set omits the V12 semantic engine).

De-globalised: takes the replay history and trend history DataFrames directly
rather than reading notebook globals / CSVs.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

__all__ = [
    "REPAIR_ENGINE_STATES",
    "POST_CONFIRMATION_PROXY_STATES",
    "assign_active_engine",
    "build_semantic_history",
    "build_current_state_card",
    "SEMANTIC_SCHEMA_VERSION",
]

SEMANTIC_SCHEMA_VERSION = "yearline_universe.engine_handoff_semantics.v13"
SEMANTIC_MODEL_VERSION = "v13_engine_handoff_dashboard_semantics"

REPAIR_ENGINE_STATES = {
    "below_yearline_repair",
    "failed_repair_deep_below",
    "repair_retry_probability_building",
}
POST_CONFIRMATION_PROXY_STATES = {
    "accepted_above_watch",
}

_HAZARD_COLS = [
    "hazard_today", "p_retry_within_10d", "p_retry_within_20d", "p_retry_within_40d",
    "p_retry_within_60d", "p_retry_within_90d", "survival_20d", "survival_40d",
    "survival_60d", "survival_90d",
]
_TREND_COLS = [
    "as_of_date", "post_confirmation_trend_state", "trend_quality_score",
    "pullback_quality_score", "overextension_score", "deterioration_risk_score",
    "drawdown_from_post_confirmation_peak_pct", "days_since_confirmation",
    # NB: distance_to_ma250_pct is intentionally NOT merged here — the replay history already carries it
    # (replay.py), and re-merging would collide (_x/_y suffix) and null out BOTH the repair and trend
    # context distances. context_export reads the existing column for trend_context (TO-0).
]


def assign_active_engine(mode_state) -> str:
    if mode_state in REPAIR_ENGINE_STATES:
        return "repair_retry_hazard_engine"
    if mode_state in POST_CONFIRMATION_PROXY_STATES:
        return "post_confirmation_trend_engine"
    return "unknown_or_transition"


def build_semantic_history(replay_history: pd.DataFrame, trend_history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Annotate the replay history with active_engine, gated hazard fields, and trend fields."""
    if replay_history is None or replay_history.empty:
        return pd.DataFrame()
    h = replay_history.copy().sort_values("as_of_date").reset_index(drop=True)
    h["as_of_date"] = pd.to_datetime(h["as_of_date"])

    h["active_engine"] = h["mode_state_replay"].apply(assign_active_engine)

    # Merge the post-confirmation trend fields FIRST, so the trend signal can refine the engine handoff.
    if trend_history is not None and not trend_history.empty:
        t = trend_history.copy()
        t["as_of_date"] = pd.to_datetime(t["as_of_date"])
        cols = [c for c in _TREND_COLS if c in t.columns]
        h = h.merge(t[cols].drop_duplicates("as_of_date"), on="as_of_date", how="left")
    else:
        for c in _TREND_COLS:
            if c != "as_of_date" and c not in h.columns:
                h[c] = np.nan

    # TO-0 (Track D): route clearly-post-confirmation names to the trend engine. The mode-state machine
    # only maps the single `accepted_above_watch` proxy to the trend engine, orphaning above-MA250 names in
    # other transitional states (e.g. `transition_watch`) as `unknown_or_transition` even though `trend.py`
    # has computed a full trend state for them. Promote those rows: where the engine is unknown_or_transition
    # AND a post-confirmation trend state exists for that bar, the trend engine is the active one.
    if "post_confirmation_trend_state" in h.columns:
        promote = (h["active_engine"] == "unknown_or_transition") & h["post_confirmation_trend_state"].notna()
        h.loc[promote, "active_engine"] = "post_confirmation_trend_engine"

    # Engine-keyed semantics + gating are computed AFTER the handoff is finalized.
    h["hazard_semantics"] = np.where(
        h["active_engine"] == "repair_retry_hazard_engine",
        "active_repair_retry_metric", "not_applicable_post_confirmation_handoff",
    )
    for col in _HAZARD_COLS:
        if col in h.columns:
            h[f"{col}_gated"] = np.where(h["active_engine"] == "repair_retry_hazard_engine", h[col], np.nan)

    h["trend_semantics"] = np.where(
        h["active_engine"] == "post_confirmation_trend_engine",
        "active_post_confirmation_metric", "inactive_until_yearline_acceptance",
    )
    return h


def build_current_state_card(semantic_history: pd.DataFrame) -> Mapping[str, Any]:
    """Highest-level interpretation: which engine is active right now."""
    if semantic_history is None or semantic_history.empty:
        return {"schema_version": SEMANTIC_SCHEMA_VERSION, "status": "no_semantic_history"}

    h = semantic_history.copy().sort_values("as_of_date")
    latest = h.iloc[-1]
    active_engine = latest.get("active_engine")
    base: dict[str, Any] = {
        "schema_version": SEMANTIC_SCHEMA_VERSION, "model_version": SEMANTIC_MODEL_VERSION,
        "as_of_date": str(pd.Timestamp(latest["as_of_date"]).date()), "ticker": latest.get("ticker"),
        "mode_state_replay": latest.get("mode_state_replay"), "active_engine": active_engine,
        "distance_to_ma250_pct": latest.get("distance_to_ma250_pct"),
        "required_rebound_to_ma250_pct": latest.get("required_rebound_to_ma250_pct"),
        "drawdown_so_far_pct": latest.get("drawdown_so_far_pct"),
        "interpretation": None, "primary_metrics": {}, "inactive_metrics_note": None,
        "disclaimer": "Educational research only. Not financial advice.",
    }
    if active_engine == "repair_retry_hazard_engine":
        base["interpretation"] = (
            "Repair / retry engine is active. Focus on distance to MA250, required rebound, "
            "repair drawdown, and gated retry probabilities."
        )
        base["primary_metrics"] = {
            "hazard_today_gated": latest.get("hazard_today_gated"),
            "p_retry_within_20d_gated": latest.get("p_retry_within_20d_gated"),
            "p_retry_within_40d_gated": latest.get("p_retry_within_40d_gated"),
            "p_retry_within_60d_gated": latest.get("p_retry_within_60d_gated"),
            "survival_60d_gated": latest.get("survival_60d_gated"),
        }
        base["inactive_metrics_note"] = "Post-confirmation trend metrics are secondary until yearline acceptance is restored."
    elif active_engine == "post_confirmation_trend_engine":
        base["interpretation"] = (
            "Post-confirmation trend engine is active. Retry-hazard metrics are not applicable; "
            "focus on trend quality, pullback quality, overextension, and deterioration risk."
        )
        base["primary_metrics"] = {
            "post_confirmation_trend_state": latest.get("post_confirmation_trend_state"),
            "trend_quality_score": latest.get("trend_quality_score"),
            "pullback_quality_score": latest.get("pullback_quality_score"),
            "overextension_score": latest.get("overextension_score"),
            "deterioration_risk_score": latest.get("deterioration_risk_score"),
            "drawdown_from_post_confirmation_peak_pct": latest.get("drawdown_from_post_confirmation_peak_pct"),
        }
        base["inactive_metrics_note"] = "Repair retry-hazard metrics are gated off during accepted-above / post-confirmation regimes."
    else:
        base["interpretation"] = "Unknown or transition state. Review both repair and post-confirmation modules."
        base["inactive_metrics_note"] = "No single engine is clearly active."
    return base
