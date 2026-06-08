"""Reporting (forward-compatible skeleton).

Ships a working ``build_universe_markdown_report``. The richer per-ticker and
per-universe PDF report packs from V12 (reportlab) are intentionally deferred;
this module provides the stable entry point and a useful markdown summary now.
"""

from __future__ import annotations

import pandas as pd

from .dashboard import build_cross_sectional_dashboard

__all__ = ["build_universe_markdown_report"]


def build_universe_markdown_report(universe_result) -> str:
    """Return a concise markdown summary of a universe run."""
    m = getattr(universe_result, "run_manifest", {}) or {}
    lines: list[str] = []
    lines.append(f"# Universe Statistical Context — {getattr(universe_result, 'universe_name', 'universe')}")
    lines.append("")
    lines.append(f"- as_of: **{getattr(universe_result, 'as_of', None)}**")
    lines.append(f"- tickers: {m.get('n_ok')}/{m.get('n_tickers')} ok, {m.get('n_failed')} failed")
    lines.append("")
    lines.append("> Educational research only. Not financial advice. Evidence overlay; must not auto-execute trades.")
    lines.append("")
    lines.append("## Cross-sectional state")
    lines.append("")
    dash = build_cross_sectional_dashboard(universe_result)
    if dash.empty:
        lines.append("_No ticker results._")
        return "\n".join(lines)

    show = dash[["ticker", "sector", "active_engine", "distance_to_ma250_pct",
                 "post_confirmation_trend_state", "trend_quality_score", "status"]].copy()
    lines.append("| " + " | ".join(show.columns) + " |")
    lines.append("|" + "|".join(["---"] * len(show.columns)) + "|")
    for _, r in show.iterrows():
        def fmt(v):
            return f"{v:.2f}" if isinstance(v, float) else ("" if v is None else str(v))
        lines.append("| " + " | ".join(fmt(r[c]) for c in show.columns) + " |")
    lines.append("")
    lines.append("_Full PDF report packs (per-ticker + universe) are a planned reporting expansion._")
    return "\n".join(lines)
