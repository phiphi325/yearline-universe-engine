"""V13.8 — yearline repo-integration adapter (Track B / Phase 9, yearline side).

A **thin, deterministic projection** of the full `SingleTickerStatisticalContextEnvelope` down to the
small, flat, JSON-serializable **`YearlineContext`** contract that `option-mgmt-2026` consumes — the
integration boundary. No new modelling; the envelope is the source of truth.

Why a separate contract (see `docs/option-mgmt-integration/`): option-mgmt's pure engine
(`packages/engine`) is no-I/O / lean-deps and must **never** import this (heavy, I/O) package. Instead the
engine consumes a lightweight, **versioned, gated** value object — hydrated in option-mgmt's jobs layer
from a persisted artifact this adapter writes. The consumer must honor the gates: use `p_retry[h]` only
where `gate_passed[h]`, and `p_success` only where `success_gate_passed`.

`ADAPTER_VERSION` is the contract pin — **bump it on any contract-shape change** (the cross-repo contract
test keys off it). Educational research only; not financial advice; `must_not_auto_execute` always True.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any, Mapping

__all__ = [
    "ADAPTER_VERSION",
    "YEARLINE_CONTEXT_HORIZONS",
    "to_yearline_context",
    "export_yearline_context",
    "YEARLINE_CONTEXT_JSON_SCHEMA",
    # V13.8.1 — presentation series (the trend-plot data source; NOT an engine decision input)
    "TREND_SERIES_VERSION",
    "to_yearline_trend_series",
    "export_yearline_trend_series",
    "YEARLINE_TREND_SERIES_JSON_SCHEMA",
]

ADAPTER_VERSION = "v13_8_yearline_context_adapter_v1"
YEARLINE_CONTEXT_HORIZONS = (10, 20, 40, 60)
TREND_SERIES_VERSION = "v13_8_1_yearline_trend_series_v1"


def _f(x):
    try:
        if x is None:
            return None
        xf = float(x)
        return xf if xf == xf else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _r(x, n=4):
    v = _f(x)
    return round(v, n) if v is not None else None


def _as_date(x):
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return datetime.fromisoformat(str(x)[:10]).date()
    except ValueError:
        return None


def _is_stale(as_of, as_of_today, max_age_days) -> bool:
    """Stale if the envelope has no usable as_of, or (when a reference 'today' is given) it is older than
    ``max_age_days`` calendar days. With no reference the consumer (which knows its run date) decides — the
    adapter then reports False but still surfaces ``as_of`` for that check."""
    d = _as_date(as_of)
    if d is None:
        return True
    ref = _as_date(as_of_today)
    if ref is None:
        return False
    return (ref - d).days > int(max_age_days)


def _composite(rs: Mapping[str, Any] | None) -> dict[str, Any]:
    """Per-horizon SURFACED P(successful reclaim ≤ H) from the RS-4 success overlay (only where both the
    occurrence and success gates passed; else None for that horizon)."""
    out: dict[str, Any] = {}
    comp = (rs or {}).get("successful_reclaim_within_horizon") or {}
    for h in YEARLINE_CONTEXT_HORIZONS:
        c = comp.get(str(h)) or comp.get(h) or {}
        out[str(h)] = _r(c.get("surfaced_probability"))
    return out


def to_yearline_context(envelope: Mapping[str, Any], *, as_of_today=None, max_age_days: int = 4) -> dict[str, Any]:
    """Project a statistical-context envelope onto the flat, gated ``YearlineContext`` contract."""
    env = dict(envelope or {})
    rr = env.get("repair_retry_context") or {}
    rh = env.get("retry_hazard_context") or {}
    tc = env.get("post_confirmation_trend_context") or {}
    consensus = (env.get("retry_timing_context") or {}).get("consensus") or {}
    rs = env.get("retry_success_context") if isinstance(env.get("retry_success_context"), Mapping) else None
    as_of = env.get("as_of")
    repair_active = bool(rr.get("active"))

    # --- gated occurrence P(retry≤H): prefer the Phase-7 blend (the gate-passing surface) where surfaced,
    # else the canonical empirical estimator gated by the Phase-4 isotonic trust gate. Dormant when not in
    # repair (above the yearline) — emit no horizons, so the consumer reads the trend state instead. ---
    p_retry: dict[str, float] = {}
    gate_passed: dict[str, bool] = {}
    p_retry_basis = None
    if repair_active:
        blend = rh.get("direct_classifier_blend")
        blend = blend if isinstance(blend, Mapping) and blend.get("available") else None
        if blend:
            ph = blend.get("per_horizon") or {}
            for h in YEARLINE_CONTEXT_HORIZONS:
                d = ph.get(str(h)) or ph.get(h) or {}
                if d.get("blend_probability") is not None:
                    p_retry[str(h)] = _r(d.get("blend_probability"))
                    gate_passed[str(h)] = bool(d.get("gate_passed"))
            if p_retry:
                p_retry_basis = "blend"
        if not p_retry:  # empirical fallback
            cal_tg = (env.get("calibration_context") or {}).get("trust_gate") or {}
            for h in YEARLINE_CONTEXT_HORIZONS:
                v = rh.get(f"p_retry_within_{h}d")
                if v is not None:
                    p_retry[str(h)] = _r(v)
                    g = cal_tg.get(str(h)) or cal_tg.get(h) or {}
                    gate_passed[str(h)] = bool(g.get("passed"))
            # ensure the headline 40d gate reflects the surfaced calibration gate if trust_gate was absent
            if "40" in p_retry and not gate_passed.get("40"):
                gate_passed["40"] = bool((rh.get("calibration_gate_40d") or {}).get("passed"))
            if p_retry:
                p_retry_basis = "empirical"

    distance = rr.get("distance_to_ma250_pct") if repair_active else tc.get("distance_to_ma250_pct")
    drange = consensus.get("remaining_days_range") or [None, None]

    return {
        "as_of": (str(_as_date(as_of)) if _as_date(as_of) else None),
        "ticker": env.get("ticker"),
        "schema_version": env.get("schema_version"),
        "model_stack_version": env.get("model_stack_version"),
        "adapter_version": ADAPTER_VERSION,
        # structural regime
        "repair_active": repair_active,
        "distance_to_ma250_pct": _r(distance),
        "required_rebound_to_ma250_pct": _r(rr.get("required_rebound_to_ma250_pct")),
        "post_confirmation_trend_state": (None if repair_active else tc.get("trend_state")),
        # gated retry occurrence (consume ONLY where gate_passed)
        "p_retry": p_retry,
        "p_retry_basis": p_retry_basis,
        "gate_passed": gate_passed,
        # conditional timing (descriptive range, not a forecast)
        "days_to_touch_central": _r(consensus.get("central_remaining_days"), 2),
        "days_to_touch_low": _r(drange[0], 2),
        "days_to_touch_high": _r(drange[1], 2),
        # gated retry SUCCESS (Track A / RS-4 — populated when surfaced; consume only where success_gate_passed)
        "p_success": (_r((rs or {}).get("p_success_given_retry")) if (rs and rs.get("available")) else None),
        "success_gate_passed": bool(rs.get("gate_passed")) if (rs and rs.get("available")) else False,
        "p_successful_reclaim": _composite(rs),
        # provenance / safety
        "reference_scope": rh.get("p_retry_within_40d_reference_scope"),
        "is_stale": _is_stale(as_of, as_of_today, max_age_days),
        "must_not_auto_execute": True,
    }


def export_yearline_context(envelope: Mapping[str, Any], out_dir: str = "exports", *,
                            as_of_today=None) -> str:
    """Write the YearlineContext artifact (the persisted, versioned hand-off) and return its path."""
    ctx = to_yearline_context(envelope, as_of_today=as_of_today)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"yearline_context_{ctx.get('ticker') or 'UNKNOWN'}_{ctx.get('as_of') or 'NA'}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ctx, fh, indent=2, default=str)
    return path


# JSON schema for the YearlineContext contract (the cross-repo boundary; option-mgmt pins this).
YEARLINE_CONTEXT_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://option-mgmt-2026.local/schemas/yearline-context/v13_8.schema.json",
    "title": "YearlineContext",
    "type": "object",
    "required": [
        "as_of", "ticker", "schema_version", "model_stack_version", "adapter_version",
        "repair_active", "p_retry", "p_retry_basis", "gate_passed",
        "is_stale", "must_not_auto_execute",
    ],
    "properties": {
        "as_of": {"type": ["string", "null"]},
        "ticker": {"type": ["string", "null"]},
        "schema_version": {"type": ["string", "null"]},
        "model_stack_version": {"type": ["string", "null"]},
        "adapter_version": {"type": "string"},
        "repair_active": {"type": "boolean"},
        "distance_to_ma250_pct": {"type": ["number", "null"]},
        "required_rebound_to_ma250_pct": {"type": ["number", "null"]},
        "post_confirmation_trend_state": {"type": ["string", "null"]},
        "p_retry": {"type": "object", "additionalProperties": {"type": "number"}},
        "p_retry_basis": {"type": ["string", "null"], "enum": ["empirical", "blend", None]},
        "gate_passed": {"type": "object", "additionalProperties": {"type": "boolean"}},
        "days_to_touch_central": {"type": ["number", "null"]},
        "days_to_touch_low": {"type": ["number", "null"]},
        "days_to_touch_high": {"type": ["number", "null"]},
        "p_success": {"type": ["number", "null"]},
        "success_gate_passed": {"type": "boolean"},
        "p_successful_reclaim": {"type": "object", "additionalProperties": {"type": ["number", "null"]}},
        "reference_scope": {"type": ["string", "null"]},
        "is_stale": {"type": "boolean"},
        "must_not_auto_execute": {"const": True},
    },
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# V13.8.1 — YearlineTrendSeries (presentation artifact for the trend plot)
# ---------------------------------------------------------------------------
# A thin, deterministic, READ-ONLY time-series projection over the engine's existing per-day history
# (``semantic_history`` + optional ``price_df``). It feeds the option-mgmt **Today-screen trend plot**
# (OM-Y3) — it is NOT the gated decision contract and never enters the replay hash. Kept separate so the
# heavy chart payload never bloats or churns the lean scalar ``YearlineContext``. See
# docs/phased_design/phase_09/ux_trend_plot_support_analysis.md.

# out_key -> (semantic_history column, is_numeric)
_TREND_SERIES_FIELDS = {
    "distance_to_ma250_pct": ("distance_to_ma250_pct", True),   # the headline trend line (0 = yearline)
    "drawdown_so_far_pct": ("drawdown_so_far_pct", True),
    "active_engine": ("active_engine", False),                  # regime band shading
    "post_confirmation_trend_state": ("post_confirmation_trend_state", False),
    "trend_quality": ("trend_quality_score", True),
    "pullback_quality": ("pullback_quality_score", True),
    "overextension": ("overextension_score", True),
    "deterioration": ("deterioration_risk_score", True),
    "hazard_today": ("hazard_today_gated", True),               # gated → null off the repair engine
    "p_retry_40d": ("p_retry_within_40d_gated", True),
}


def _col_list(df, col: str, numeric: bool) -> list | None:
    if col not in df.columns:
        return None
    out = []
    for v in df[col].tolist():
        if v is None or (isinstance(v, float) and v != v):
            out.append(None)
        elif numeric:
            out.append(_r(v))
        else:
            out.append(str(v))
    return out


def to_yearline_trend_series(semantic_history, *, ticker: str | None = None,
                             schema_version: str | None = None, model_stack_version: str | None = None,
                             price_df=None, lookback_days: int | None = None, config=None) -> dict[str, Any]:
    """Project per-day ``semantic_history`` (+ optional ``price_df``) onto the trend-plot series contract."""
    import pandas as pd

    if semantic_history is None or getattr(semantic_history, "empty", True) or "as_of_date" not in getattr(semantic_history, "columns", []):
        return {"available": False, "warning": "no_semantic_history", "ticker": ticker,
                "series_version": TREND_SERIES_VERSION, "must_not_auto_execute": True}

    h = semantic_history.copy()
    h["as_of_date"] = pd.to_datetime(h["as_of_date"])
    h = h.sort_values("as_of_date")
    if lookback_days:
        h = h.tail(int(lookback_days))
    dates = [str(d.date()) for d in h["as_of_date"]]

    out: dict[str, Any] = {
        "available": True, "ticker": ticker, "as_of": (dates[-1] if dates else None),
        "schema_version": schema_version, "model_stack_version": model_stack_version,
        "series_version": TREND_SERIES_VERSION, "n": len(dates), "dates": dates,
    }
    for out_key, (col, numeric) in _TREND_SERIES_FIELDS.items():
        out[out_key] = _col_list(h, col, numeric)

    # Optional price + MA overlays (aligned to the series dates).
    if price_df is not None and not getattr(price_df, "empty", True) and "Close" in price_df.columns:
        p = price_df.copy()
        p.index = pd.to_datetime(p.index).normalize()
        close = p["Close"].astype(float)
        ma_len = int(getattr(config, "ma_len", 250)) if config is not None else 250
        idx = pd.to_datetime(dates).normalize()
        cols = {"close": close, "ma20": close.rolling(20).mean(),
                "ma50": close.rolling(50).mean(), "ma250": close.rolling(ma_len).mean()}
        for k, s in cols.items():
            aligned = s.reindex(idx)
            out[k] = [None if pd.isna(v) else _r(v) for v in aligned.tolist()]

    out["must_not_auto_execute"] = True
    return out


def export_yearline_trend_series(semantic_history, out_dir: str = "exports", **kwargs) -> str:
    """Write the YearlineTrendSeries artifact and return its path."""
    s = to_yearline_trend_series(semantic_history, **kwargs)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"yearline_trend_series_{s.get('ticker') or 'UNKNOWN'}_{s.get('as_of') or 'NA'}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, default=str)
    return path


YEARLINE_TREND_SERIES_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://option-mgmt-2026.local/schemas/yearline-trend-series/v13_8_1.schema.json",
    "title": "YearlineTrendSeries",
    "type": "object",
    "required": ["available", "series_version", "must_not_auto_execute"],
    "properties": {
        "available": {"type": "boolean"},
        "warning": {"type": "string"},
        "ticker": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
        "schema_version": {"type": ["string", "null"]},
        "model_stack_version": {"type": ["string", "null"]},
        "series_version": {"type": "string"},
        "n": {"type": "integer"},
        "dates": {"type": "array", "items": {"type": "string"}},
        "distance_to_ma250_pct": {"type": ["array", "null"]},
        "drawdown_so_far_pct": {"type": ["array", "null"]},
        "active_engine": {"type": ["array", "null"]},
        "post_confirmation_trend_state": {"type": ["array", "null"]},
        "trend_quality": {"type": ["array", "null"]},
        "pullback_quality": {"type": ["array", "null"]},
        "overextension": {"type": ["array", "null"]},
        "deterioration": {"type": ["array", "null"]},
        "hazard_today": {"type": ["array", "null"]},
        "p_retry_40d": {"type": ["array", "null"]},
        "close": {"type": "array"},
        "ma20": {"type": "array"}, "ma50": {"type": "array"}, "ma250": {"type": "array"},
        "must_not_auto_execute": {"const": True},
    },
    "additionalProperties": False,
}
