"""Conditional days-to-next-touch ESTIMATORS (V13.3, Phase 2).

The credible "days left until the next MA250 touch" value: a multi-method,
uncertainty-bearing *range* derived from descriptive history — NOT the ill-posed
forward hazard probability (which is a guaranteed step; see
``docs/V13_data_and_report_analysis.md`` §2.2 and the Phase 3 hardening plan).

Faithful, de-globalised port of the V11.5 §7 conditional retry-timing cell
(V12 ``build_live_retry_setup`` / ``build_estimator_comparison`` and friends).
Given a live repair setup (current drawdown-so-far + days elapsed since the last
canonical touch) it conditions historical inter-attempt recovery on the same
``transition`` (e.g. ``2_to_3``) and estimates the total attempt-to-attempt gap
four ways:

  (a) historical **median** gap by transition, at ALL / peer-group scopes;
  (b) gap×drawdown **matrix interpolation** between adjacent drawdown anchors;
  (c) **nearest-neighbor** median within a ±band of the current drawdown;
  (d) **Theil-Sen** robust fit of gap ~ drawdown, with a bootstrap p10–p90.

Each yields an estimated *total* gap, the elapsed days subtracted, an
``estimated_remaining_days``, and a rough retry date *if the repair path
continues and drawdown does not worsen*. A ``consensus`` block reduces the
methods to a single central remaining-day count plus a min/max window.

This is DESCRIPTIVE evidence conditioned on an explicit assumption (current
drawdown is the maximum damage), not a forecast or a trading signal. Every
output carries sample sizes and quality flags; nothing auto-executes.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import sample_quality
from .pooling import beta_binomial_summary, build_gap_drawdown_matrix

try:
    from scipy.stats import theilslopes
except Exception:  # pragma: no cover
    theilslopes = None

__all__ = [
    "required_rebound_to_ma250_pct",
    "build_live_retry_setup",
    "build_transition_gap_summary",
    "build_nearest_neighbor_summary",
    "interpolate_gap_from_matrix",
    "theilsen_gap_estimate",
    "build_estimator_comparison",
    "build_retry_timing_context",
]

RANDOM_SEED = 42
NEAREST_NEIGHBOR_BANDS = [2.5, 5.0]
NEAREST_NEIGHBOR_TOP_K = 12
BOOTSTRAP_N = 500

# Live diagnostic states for which the repair/retry engine is the relevant one
# (price is below MA250 or testing it un-confirmed -> a "next touch" is pending).
_REPAIR_ACTIVE_STATES = {"below_yearline_after_latest_touch", "testing_yearline_unconfirmed"}


# ---------------------------------------------------------------------------
# Helpers (faithful to V12)
# ---------------------------------------------------------------------------

def _to_timestamp(x: Any) -> pd.Timestamp | None:
    try:
        if x is None or pd.isna(x):
            return None
        return pd.to_datetime(x)
    except Exception:
        return None


def _safe_float(x: Any, default: float = np.nan) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def required_rebound_to_ma250_pct(distance_to_ma250_pct: float) -> float:
    """Percent rebound needed to reach MA250 from the current distance.

    distance = -10.1 means price/MA250 = 0.899, so the required rebound to MA250
    is 1/0.899 - 1 ~ 11.2%.
    """
    if pd.isna(distance_to_ma250_pct):
        return np.nan
    ratio = 1.0 + distance_to_ma250_pct / 100.0
    if ratio <= 0:
        return np.nan
    if distance_to_ma250_pct >= 0:
        return 0.0
    return (1.0 / ratio - 1.0) * 100.0


def build_live_retry_setup(
    live_diagnostic: Mapping[str, Any],
    peer_group: str,
    *,
    drawdown_assumption_pct: float | None = None,
) -> dict[str, Any]:
    """Build the conditional-retry live setup from a V13 live diagnostic dict.

    ``drawdown_assumption_pct`` defaults to the live current drawdown-since-touch
    (absolute %), i.e. "assume the damage so far is the maximum before the next
    retry". Pass an explicit value to stress a deeper/shallower assumption.
    """
    live = dict(live_diagnostic or {})
    latest_touch_date = _to_timestamp(live.get("latest_touch_date"))
    as_of_date = _to_timestamp(live.get("as_of"))

    latest_attempt_no = live.get("latest_attempt_no")
    latest_attempt_no = int(latest_attempt_no) if latest_attempt_no is not None and not pd.isna(latest_attempt_no) else None
    target_transition = f"{latest_attempt_no}_to_{latest_attempt_no + 1}" if latest_attempt_no is not None else None

    days_elapsed = (as_of_date - latest_touch_date).days if as_of_date is not None and latest_touch_date is not None else np.nan
    trading_days_elapsed = live.get("trading_days_since_last_touch")
    distance_to_ma250 = _safe_float(live.get("current_distance_to_ma250_pct"))
    live_dd_low = abs(_safe_float(live.get("current_drawdown_since_last_touch_low_pct")))

    assumption = float(drawdown_assumption_pct) if drawdown_assumption_pct is not None else live_dd_low
    return {
        "ticker": live.get("ticker"),
        "group": peer_group,
        "as_of_date": str(as_of_date.date()) if as_of_date is not None else None,
        "latest_touch_date": str(latest_touch_date.date()) if latest_touch_date is not None else None,
        "latest_attempt_no": latest_attempt_no,
        "target_transition": target_transition,
        "latest_outcome": live.get("latest_outcome"),
        "latest_state": live.get("state"),
        "latest_mode_state": live.get("mode_transition_state_prototype"),
        "days_elapsed_since_latest_touch": days_elapsed,
        "trading_days_elapsed_since_latest_touch": int(trading_days_elapsed) if trading_days_elapsed is not None and not pd.isna(trading_days_elapsed) else None,
        "current_distance_to_ma250_pct": distance_to_ma250,
        "required_rebound_to_ma250_pct": required_rebound_to_ma250_pct(distance_to_ma250),
        "live_drawdown_since_touch_low_abs_pct": live_dd_low,
        "drawdown_assumption_abs_pct": assumption,
        "drawdown_assumption_source": "explicit_override" if drawdown_assumption_pct is not None else "live_current_drawdown_so_far",
        "assumption_note": "Assumes current post-touch drawdown is the maximum damage before the next retry.",
    }


def _distribution_summary(df: pd.DataFrame, label: str) -> dict[str, Any]:
    if df is None or df.empty:
        return {"scope": label, "n": 0, "completed_n": 0, "mean_gap_days": np.nan,
                "median_gap_days": np.nan, "p25_gap_days": np.nan, "p75_gap_days": np.nan,
                "p90_gap_days": np.nan, "median_drawdown_abs_low_pct": np.nan,
                "success_rate": np.nan, "posterior_success_rate": np.nan,
                "wilson_low": np.nan, "wilson_high": np.nan, "sample_quality": "none"}
    g = df.copy()
    if "next_attempt_pending" in g.columns:
        completed = g[~g["next_attempt_pending"].astype(bool)]
    else:
        completed = g
    successes = int(completed["next_attempt_success"].sum()) if "next_attempt_success" in completed.columns else 0
    n_completed = len(completed)
    summ = beta_binomial_summary(successes, n_completed)
    return {
        "scope": label, "n": len(g), "completed_n": n_completed,
        "mean_gap_days": float(g["gap_days"].mean()),
        "median_gap_days": float(g["gap_days"].median()),
        "p25_gap_days": float(g["gap_days"].quantile(0.25)),
        "p75_gap_days": float(g["gap_days"].quantile(0.75)),
        "p90_gap_days": float(g["gap_days"].quantile(0.90)),
        "median_drawdown_abs_low_pct": float(g["drawdown_abs_low_pct"].median()),
        "success_rate": summ["raw_rate"], "posterior_success_rate": summ["posterior_mean"],
        "wilson_low": summ["wilson_low"], "wilson_high": summ["wilson_high"],
        "sample_quality": sample_quality(len(g)),
    }


def build_transition_gap_summary(recovery: pd.DataFrame, setup: Mapping[str, Any]) -> pd.DataFrame:
    """Unconditional gap distribution for the target transition: ALL / peer / ticker."""
    transition = setup.get("target_transition")
    ticker = setup.get("ticker")
    group = setup.get("group")
    df = recovery[recovery["transition"] == transition].copy() if recovery is not None and not recovery.empty else pd.DataFrame()
    rows = [_distribution_summary(df, "ALL / target transition")]
    if group and "group" in df.columns:
        rows.append(_distribution_summary(df[df["group"] == group], f"peer group: {group}"))
    if ticker and "ticker" in df.columns:
        rows.append(_distribution_summary(df[df["ticker"] == ticker], f"ticker history: {ticker}"))
    return pd.DataFrame(rows)


def build_nearest_neighbor_summary(
    recovery: pd.DataFrame,
    setup: Mapping[str, Any],
    bands: list[float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Gap distribution conditioned on drawdown within ±band of the assumption."""
    bands = bands if bands is not None else NEAREST_NEIGHBOR_BANDS
    transition = setup.get("target_transition")
    ticker = setup.get("ticker")
    group = setup.get("group")
    current_dd = float(setup.get("drawdown_assumption_abs_pct"))
    df = recovery[recovery["transition"] == transition].copy() if recovery is not None and not recovery.empty else pd.DataFrame()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()
    df["drawdown_distance_to_assumption"] = (df["drawdown_abs_low_pct"] - current_dd).abs()

    scopes = [("ALL", df)]
    if group and "group" in df.columns:
        scopes.append((f"peer group: {group}", df[df["group"] == group]))
    if ticker and "ticker" in df.columns:
        scopes.append((f"ticker history: {ticker}", df[df["ticker"] == ticker]))

    rows = []
    for band in bands:
        for label, x in scopes:
            near = x[x["drawdown_distance_to_assumption"] <= band].copy()
            row = _distribution_summary(near, f"{label}; +/-{band:.1f}% drawdown band")
            row["drawdown_band_pct"] = band
            row["median_drawdown_distance_pct"] = float(near["drawdown_distance_to_assumption"].median()) if not near.empty else np.nan
            rows.append(row)

    nearest_obs = df.sort_values("drawdown_distance_to_assumption").head(NEAREST_NEIGHBOR_TOP_K).copy()
    return pd.DataFrame(rows), nearest_obs


def interpolate_gap_from_matrix(matrix: pd.DataFrame, setup: Mapping[str, Any], group: str = "ALL") -> dict[str, Any]:
    """Piecewise-linear interpolate the median gap across drawdown anchors of the matrix."""
    current_dd = float(setup.get("drawdown_assumption_abs_pct"))
    if matrix is None or matrix.empty:
        return {"method": f"matrix interpolation: {group}", "model_scope": group,
                "estimated_total_gap_days": np.nan, "estimate_quality": "no_matrix"}
    m = matrix[matrix["group"] == group].copy()
    m = m.dropna(subset=["median_drawdown_abs_low_pct", "median_gap_days"])
    if len(m) < 2:
        return {"method": f"matrix interpolation: {group}", "model_scope": group,
                "estimated_total_gap_days": np.nan, "estimate_quality": "insufficient_matrix_rows"}
    m = m.sort_values("median_drawdown_abs_low_pct").reset_index(drop=True)

    above_idx = m.index[m["median_drawdown_abs_low_pct"] >= current_dd].tolist()
    if not above_idx:
        i0, i1 = len(m) - 2, len(m) - 1
        extrapolation = "above_matrix_range"
    elif above_idx[0] == 0:
        i0, i1 = 0, 1
        extrapolation = "below_matrix_range" if current_dd < m.loc[0, "median_drawdown_abs_low_pct"] else "interpolation"
    else:
        i1 = above_idx[0]
        i0 = i1 - 1
        extrapolation = "interpolation"

    r0, r1 = m.loc[i0], m.loc[i1]
    x0, y0 = float(r0["median_drawdown_abs_low_pct"]), float(r0["median_gap_days"])
    x1, y1 = float(r1["median_drawdown_abs_low_pct"]), float(r1["median_gap_days"])
    if abs(x1 - x0) < 1e-9:
        est = float(np.nanmean([y0, y1]))
        weight = np.nan
    else:
        weight = (current_dd - x0) / (x1 - x0)
        est = y0 + weight * (y1 - y0)
    return {
        "method": f"matrix interpolation: {group}", "model_scope": group,
        "estimated_total_gap_days": est, "drawdown_assumption_abs_pct": current_dd,
        "lower_anchor_bucket": r0.get("matrix_bucket"), "lower_anchor_drawdown_pct": x0, "lower_anchor_gap_days": y0,
        "upper_anchor_bucket": r1.get("matrix_bucket"), "upper_anchor_drawdown_pct": x1, "upper_anchor_gap_days": y1,
        "interpolation_weight": (float(weight) if not pd.isna(weight) else np.nan), "estimate_quality": extrapolation,
    }


def theilsen_gap_estimate(df: pd.DataFrame, setup: Mapping[str, Any], label: str) -> dict[str, Any]:
    """Theil-Sen robust fit of gap ~ drawdown, predicted at the assumption, bootstrap p10–p90."""
    current_dd = float(setup.get("drawdown_assumption_abs_pct"))
    if df is None or df.empty or len(df.dropna(subset=["drawdown_abs_low_pct", "gap_days"])) < 5:
        return {"method": f"Theil-Sen robust fit: {label}", "model_scope": label,
                "estimated_total_gap_days": np.nan, "estimate_quality": "n_lt_5"}
    d = df.dropna(subset=["drawdown_abs_low_pct", "gap_days"]).copy()
    x = d["drawdown_abs_low_pct"].astype(float).to_numpy()
    y = d["gap_days"].astype(float).to_numpy()
    if len(np.unique(x)) < 2:
        return {"method": f"Theil-Sen robust fit: {label}", "model_scope": label,
                "estimated_total_gap_days": np.nan, "estimate_quality": "insufficient_x_variation"}
    if theilslopes is not None:
        slope, intercept, _lo_slope, _hi_slope = theilslopes(y, x, 0.95)
    else:
        slope, intercept = np.polyfit(x, y, 1)
    pred = float(intercept + slope * current_dd)

    rng = np.random.default_rng(RANDOM_SEED)
    preds = []
    for _ in range(BOOTSTRAP_N):
        idx = rng.integers(0, len(d), len(d))
        xb, yb = x[idx], y[idx]
        if len(np.unique(xb)) < 2:
            continue
        try:
            if theilslopes is not None:
                b_slope, b_intercept, *_ = theilslopes(yb, xb, 0.95)
            else:
                b_slope, b_intercept = np.polyfit(xb, yb, 1)
            val = float(b_intercept + b_slope * current_dd)
            if np.isfinite(val):
                preds.append(val)
        except Exception:
            continue
    if preds:
        p10, p25, p50, p75, p90 = (float(v) for v in np.percentile(preds, [10, 25, 50, 75, 90]))
    else:
        p10 = p25 = p50 = p75 = p90 = np.nan
    return {
        "method": f"Theil-Sen robust fit: {label}", "model_scope": label, "n": len(d),
        "estimated_total_gap_days": pred, "bootstrap_p10_gap_days": p10, "bootstrap_p25_gap_days": p25,
        "bootstrap_p50_gap_days": p50, "bootstrap_p75_gap_days": p75, "bootstrap_p90_gap_days": p90,
        "drawdown_assumption_abs_pct": current_dd, "slope_days_per_1pct_drawdown": float(slope),
        "intercept_days": float(intercept), "estimate_quality": sample_quality(len(d)),
    }


def build_estimator_comparison(recovery: pd.DataFrame, matrix: pd.DataFrame, setup: Mapping[str, Any]) -> pd.DataFrame:
    """Combine the four estimator families and translate total gap -> remaining days + rough date."""
    transition = setup.get("target_transition")
    group = setup.get("group")
    days_elapsed = _safe_float(setup.get("days_elapsed_since_latest_touch"), default=0.0)
    as_of = _to_timestamp(setup.get("as_of_date"))
    df = recovery[recovery["transition"] == transition].copy() if recovery is not None and not recovery.empty else pd.DataFrame()
    rows: list[dict[str, Any]] = []

    # (a) Historical base median (unconditional), ALL + peer group.
    if not df.empty:
        group_df = df[df["group"] == group] if (group and "group" in df.columns) else pd.DataFrame()
        for label, x in [("ALL", df), (f"peer group: {group}", group_df)]:
            if not x.empty:
                rows.append({
                    "method": f"historical median {transition} gap: {label}", "model_scope": label, "n": len(x),
                    "estimated_total_gap_days": float(x["gap_days"].median()),
                    "drawdown_assumption_abs_pct": setup.get("drawdown_assumption_abs_pct"),
                    "estimate_quality": f"unconditional_{sample_quality(len(x))}",
                })

    # (b) Matrix interpolation, ALL + group.
    rows.append(interpolate_gap_from_matrix(matrix, setup, "ALL"))
    if group:
        rows.append(interpolate_gap_from_matrix(matrix, setup, group))

    # (c) Nearest-neighbor median, ±2.5% band, ALL + group.
    nn_summary, _ = build_nearest_neighbor_summary(recovery, setup, bands=[2.5])
    for _, r in nn_summary.iterrows():
        if r.get("n", 0) > 0 and ("ALL" in str(r.get("scope")) or str(group) in str(r.get("scope"))):
            rows.append({
                "method": f"nearest neighbors +/-2.5%: {r['scope']}", "model_scope": r["scope"], "n": int(r.get("n")),
                "estimated_total_gap_days": r.get("median_gap_days"),
                "drawdown_assumption_abs_pct": setup.get("drawdown_assumption_abs_pct"),
                "estimate_quality": f"conditional_{r.get('sample_quality')}",
            })

    # (d) Theil-Sen robust regression, ALL + group.
    if not df.empty:
        rows.append(theilsen_gap_estimate(df, setup, "ALL"))
        if group:
            rows.append(theilsen_gap_estimate(df[df["group"] == group], setup, f"peer group: {group}"))

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["days_elapsed_since_latest_touch"] = days_elapsed
    out["estimated_remaining_days_from_as_of"] = (out["estimated_total_gap_days"] - days_elapsed).clip(lower=0)
    if as_of is not None:
        out["rough_retry_date_if_repair_continues"] = out["estimated_remaining_days_from_as_of"].map(
            lambda x: str((as_of + pd.Timedelta(days=int(math.ceil(x)))).date()) if pd.notna(x) else None
        )
    else:
        out["rough_retry_date_if_repair_continues"] = None
    return out.sort_values(["estimated_total_gap_days", "method"], na_position="last").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Orchestration: the additive per-ticker retry_timing_context block
# ---------------------------------------------------------------------------

def _records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.replace({np.nan: None}).to_dict("records")


# Quality flags that mark an estimate as too fragile to anchor the headline window.
_FRAGILE_QUALITY = ("very_low", "n_lt_5", "insufficient", "no_matrix", "none")


def _is_fragile(quality: Any) -> bool:
    q = str(quality).lower()
    return any(tok in q for tok in _FRAGILE_QUALITY)


def _consensus(estimators: pd.DataFrame, as_of: pd.Timestamp | None) -> dict[str, Any]:
    """Reduce the estimator methods to a single central remaining-day window.

    Fragile estimates (very small conditioning samples, degenerate matrices) are
    excluded from the central/range so a single n=1 neighbor cannot swing the
    headline; they remain listed in ``estimators``. Falls back to all methods if
    every estimate is fragile.
    """
    if estimators is None or estimators.empty or "estimated_remaining_days_from_as_of" not in estimators.columns:
        return {"available": False, "reason": "no_estimates"}
    df = estimators.copy()
    df["_rem"] = pd.to_numeric(df["estimated_remaining_days_from_as_of"], errors="coerce")
    df["_tot"] = pd.to_numeric(df["estimated_total_gap_days"], errors="coerce")
    df = df[df["_rem"].notna()]
    if df.empty:
        return {"available": False, "reason": "no_estimates"}

    reliable = df[~df["estimate_quality"].map(_is_fragile)] if "estimate_quality" in df.columns else df
    used, used_basis = (reliable, "reliable_methods_only") if not reliable.empty else (df, "all_methods_fallback")

    def _date(days: float) -> str | None:
        if as_of is None or pd.isna(days):
            return None
        return str((as_of + pd.Timedelta(days=int(math.ceil(days)))).date())

    rem, tot = used["_rem"], used["_tot"].dropna()
    central, lo, hi = float(rem.median()), float(rem.min()), float(rem.max())
    return {
        "available": True,
        "n_methods_total": int(len(df)),
        "n_methods_used": int(len(used)),
        "consensus_basis": used_basis,
        "central_total_gap_days": float(tot.median()) if not tot.empty else None,
        "central_remaining_days": central,
        "remaining_days_range": [lo, hi],
        "rough_central_retry_date_if_repair_continues": _date(central),
        "rough_earliest_retry_date_if_repair_continues": _date(lo),
        "rough_latest_retry_date_if_repair_continues": _date(hi),
        "framing": "Evidence-based conditional window across methods; central = median of (non-fragile) method "
                   "remaining-days. The spread reflects method and scope (universe-pooled vs peer/self). "
                   "Not a forecast or a predicted date.",
    }


def _dormant_block(active_engine: str | None, live_state: str | None, reason: str) -> dict[str, Any]:
    return {
        "schema": "v13_retry_timing_conditional_estimators",
        "active": False,
        "reason": reason,
        "active_engine": active_engine,
        "live_state": live_state,
        "note": "Conditional retry-timing is only computed while the repair/retry engine is active "
                "(price below or testing MA250 with a pending next touch).",
        "must_not_auto_execute": True,
    }


def build_retry_timing_context(
    live_diagnostic: Mapping[str, Any],
    recovery: pd.DataFrame,
    matrix: pd.DataFrame | None = None,
    *,
    peer_group: str,
    config: StudyConfig | None = None,
    drawdown_assumption_pct: float | None = None,
    active_engine: str | None = None,
    scope: str = "single_ticker_self_conditioned",
) -> dict[str, Any]:
    """Build the additive, repair-regime-gated ``retry_timing_context`` block.

    ``recovery`` must carry a ``group`` column (peer-group label) alongside the
    standard recovery columns (``transition``, ``gap_days``,
    ``drawdown_abs_low_pct``, ``next_attempt_success``/``_pending``, ``ticker``).
    ``matrix`` is the gap×drawdown matrix (built here if omitted). ``scope``
    documents whether the conditioning history is the ticker's own
    (``single_ticker_self_conditioned``) or the pooled universe
    (``universe_pooled``).
    """
    config = config or StudyConfig()
    live = dict(live_diagnostic or {})
    live_state = live.get("state")

    # --- Gate: only when the repair/retry engine is the active one ----------
    if active_engine is not None:
        repair_active = active_engine == "repair_retry_hazard_engine"
    else:
        repair_active = live_state in _REPAIR_ACTIVE_STATES
    if not repair_active:
        return _dormant_block(active_engine, live_state, "repair_engine_dormant_or_accepted_above_yearline")

    setup = build_live_retry_setup(live, peer_group, drawdown_assumption_pct=drawdown_assumption_pct)
    if not setup.get("target_transition"):
        return _dormant_block(active_engine, live_state, "no_target_transition_no_canonical_attempt")

    if recovery is None or recovery.empty:
        block = _dormant_block(active_engine, live_state, "no_historical_recovery_to_condition_on")
        block["active"] = True
        block["setup"] = setup
        block["estimators"] = []
        block["consensus"] = {"available": False, "reason": "no_historical_recovery"}
        return block

    rec = recovery.copy()
    if "group" not in rec.columns:
        rec["group"] = peer_group
    if matrix is None:
        matrix = build_gap_drawdown_matrix(rec, config)

    estimators = build_estimator_comparison(rec, matrix, setup)
    base_dist = build_transition_gap_summary(rec, setup)
    nn_summary, nearest_obs = build_nearest_neighbor_summary(rec, setup)
    as_of = _to_timestamp(setup.get("as_of_date"))

    nn_cols = ["ticker", "group", "round", "transition", "from_date", "to_date", "gap_days",
               "drawdown_abs_low_pct", "drawdown_distance_to_assumption",
               "next_attempt_success", "next_attempt_pending"]
    nearest_obs = nearest_obs[[c for c in nn_cols if c in nearest_obs.columns]] if not nearest_obs.empty else nearest_obs

    n_transition = int((rec["transition"] == setup.get("target_transition")).sum())
    return {
        "schema": "v13_retry_timing_conditional_estimators",
        "active": True,
        "is_descriptive_evidence_not_forecast": True,
        "must_not_auto_execute": True,
        "conditioning_scope": scope,
        "active_engine": active_engine or "repair_retry_hazard_engine",
        "n_conditioning_transitions": n_transition,
        "sample_quality": sample_quality(n_transition),
        "setup": setup,
        "consensus": _consensus(estimators, as_of),
        "estimators": _records(estimators),
        "base_distribution": _records(base_dist),
        "nearest_neighbor_summary": _records(nn_summary),
        "nearest_observations": _records(nearest_obs),
        "disclaimers": [
            "Conditional on the assumption that current drawdown is the maximum damage before the next retry.",
            "Descriptive historical evidence, not a forward forecast or a predicted date.",
            "Estimates are a range across methods; small conditioning samples are statistically fragile.",
            "Recompute as days-since-touch, distance-to-MA250, and drawdown update.",
            "Not financial advice; evidence overlay only; must not auto-execute.",
        ],
    }
