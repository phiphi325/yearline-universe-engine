"""Cross-sectional universe dashboard (V13.4 — forward-compatible skeleton).

Ships a working ``build_cross_sectional_dashboard`` that produces the spec's
core cross-sectional table from each ticker's context envelope. Interactive
plot packs / HTML rendering are the V13.4 expansion.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

__all__ = ["build_cross_sectional_dashboard", "DASHBOARD_COLUMNS"]

DASHBOARD_COLUMNS = [
    "ticker", "sector", "peer_group", "active_engine", "mode_state",
    "distance_to_ma250_pct", "required_rebound_to_ma250_pct",
    "p_retry_within_40d_gated", "post_confirmation_trend_state",
    "trend_quality_score", "overextension_score", "deterioration_risk_score",
    "as_of", "status",
]


def build_cross_sectional_dashboard(universe_result) -> pd.DataFrame:
    """Build the V13.4 cross-sectional state table for all tickers in a universe run."""
    rows: list[dict[str, Any]] = []
    for ticker, res in getattr(universe_result, "ticker_results", {}).items():
        if getattr(res, "status", None) != "ok":
            rows.append({"ticker": ticker, "sector": getattr(res, "sector", None),
                         "peer_group": getattr(res, "peer_group", None), "status": "error"})
            continue
        env = getattr(res, "latest_context", {}) or {}
        aec = env.get("active_engine_context", {})
        repair = env.get("repair_retry_context", {})
        hazard = env.get("retry_hazard_context", {})
        trend = env.get("post_confirmation_trend_context", {})
        rows.append({
            "ticker": env.get("ticker", ticker),
            "sector": env.get("sector"), "peer_group": env.get("peer_group"),
            "active_engine": aec.get("active_engine"), "mode_state": aec.get("mode_state"),
            "distance_to_ma250_pct": repair.get("distance_to_ma250_pct"),
            "required_rebound_to_ma250_pct": repair.get("required_rebound_to_ma250_pct"),
            "p_retry_within_40d_gated": hazard.get("p_retry_within_40d"),
            "post_confirmation_trend_state": trend.get("trend_state"),
            "trend_quality_score": trend.get("trend_quality_score"),
            "overextension_score": trend.get("overextension_score"),
            "deterioration_risk_score": trend.get("deterioration_risk_score"),
            "as_of": env.get("as_of"), "status": "ok",
        })
    df = pd.DataFrame(rows)
    for c in DASHBOARD_COLUMNS:
        if c not in df.columns:
            df[c] = None
    return df[DASHBOARD_COLUMNS].sort_values(["sector", "peer_group", "ticker"]).reset_index(drop=True)
