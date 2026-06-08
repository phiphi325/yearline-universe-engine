"""V13.3 Phase 4 (V13.7) — calibration & gating of the empirical horizon estimator.

Ports the V12.6 calibration harness, which scores the **Phase 3 empirical
completed-path** horizon estimator (NOT the demoted state-hold-forward model curve).
On top of the port it adds the two items V12.6 §5 flagged as future work:

  * an **isotonic** calibration transform (fit on out-of-fold predictions), and
  * **purged, transition-aware** evaluation — every prediction excludes its own
    transition (leave-one-transition-out), so a row's realised outcome never leaks
    into its own reference pool.

Output: per-horizon reliability metrics (observed vs predicted, Brier, log-loss, AUC,
mean-abs-calibration-error) + a calibrated variant + a per-horizon **trust gate**.
A surfaced probability is only marked trustworthy where the gate passes.

This is an OPT-IN diagnostic (it rescans the historical panel) — see ``calibrate`` in
``run_ticker_pipeline``. Educational research only; not a trading signal.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .hazard import (
    build_empirical_horizon_reference,
    empirical_horizon_probabilities_for_row,
    HORIZON_PROB_POLICY,
)

try:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    from sklearn.isotonic import IsotonicRegression
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

__all__ = [
    "CALIBRATION_HORIZONS",
    "build_horizon_calibration_dataset",
    "horizon_calibration_metrics",
    "fit_isotonic_per_horizon",
    "build_calibration_context",
    "apply_isotonic_knots",
]

CALIBRATION_HORIZONS = [10, 20, 40, 60]
CALIBRATION_BINS = np.linspace(0.0, 1.0, 11)
MIN_CALIBRATION_BIN_N = 10

# Trust-gate thresholds (a surfaced horizon probability must clear all three).
GATE_MAX_MACE = 0.10      # mean abs calibration error (post-isotonic) must be small
GATE_MIN_AUC = 0.60       # must discriminate better than a coin
GATE_MIN_N = 50           # enough labelled rows to mean anything
CALIBRATION_SCHEMA_VERSION = "yearline_universe.horizon_calibration.v13_7"


# ---------------------------------------------------------------------------
# Safe metric helpers (ported from V12.6)
# ---------------------------------------------------------------------------

def _safe_log_loss(y, p) -> float:
    y = np.asarray(y).astype(int)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    if len(np.unique(y)) < 2:
        return float("nan")
    return float(log_loss(y, p, labels=[0, 1]))


def _safe_auc(y, p) -> float:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    if len(np.unique(y)) < 2:
        return float("nan")
    try:
        return float(roc_auc_score(y, p))
    except Exception:
        return float("nan")


def _safe_brier(y, p) -> float:
    y = np.asarray(y).astype(int)
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    if len(y) == 0:
        return float("nan")
    return float(brier_score_loss(y, p))


# ---------------------------------------------------------------------------
# 1. Calibration dataset — purged, transition-aware (leave-one-transition-out)
# ---------------------------------------------------------------------------

def build_horizon_calibration_dataset(panel: pd.DataFrame, horizons=None) -> pd.DataFrame:
    """One row per historical at-risk day: empirical horizon prediction vs realised.

    The prediction for each row **excludes its own transition** from the reference
    pool (``exclude_transition_key``) — a purged, leakage-free estimate of how the
    empirical horizon estimator generalises.
    """
    horizons = horizons or CALIBRATION_HORIZONS
    ref = build_empirical_horizon_reference(panel)
    if ref is None or ref.empty:
        return pd.DataFrame()
    keep = ["ticker", "group", "transition", "transition_key", "as_of_date",
            "trading_days_since_touch", "remaining_trading_days_to_retry"]
    records = []
    for _, row in ref.iterrows():
        rec = {k: row.get(k) for k in keep if k in ref.columns}
        emp = empirical_horizon_probabilities_for_row(
            row.to_dict(), ref, horizons, exclude_transition_key=row.get("transition_key"),
        )
        rem = float(row["remaining_trading_days_to_retry"])
        for h in horizons:
            h = int(h)
            e = emp.get(h, {})
            rec[f"pred_retry_within_{h}d"] = e.get("cumulative_retry_probability")
            rec[f"pred_retry_within_{h}d_reference_n"] = e.get("reference_n")
            rec[f"pred_retry_within_{h}d_reference_scope"] = e.get("reference_scope")
            rec[f"actual_retry_within_{h}d"] = int(rem <= h)
        records.append(rec)
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# 2. Reliability + metrics by horizon (ported from V12.6)
# ---------------------------------------------------------------------------

def _reliability_table(dataset: pd.DataFrame, pred_col: str, actual_col: str, horizon: int) -> pd.DataFrame:
    if dataset is None or dataset.empty or pred_col not in dataset.columns:
        return pd.DataFrame()
    d = dataset[[pred_col, actual_col]].copy()
    d[pred_col] = pd.to_numeric(d[pred_col], errors="coerce").clip(0, 1)
    d[actual_col] = pd.to_numeric(d[actual_col], errors="coerce")
    d = d.dropna()
    if d.empty:
        return pd.DataFrame()
    d["prob_bin"] = pd.cut(d[pred_col], bins=CALIBRATION_BINS, include_lowest=True)
    out = (d.groupby("prob_bin", observed=False)
             .agg(n=(actual_col, "size"), predicted_mean=(pred_col, "mean"), observed_rate=(actual_col, "mean"))
             .reset_index())
    out["horizon_days"] = horizon
    out["abs_calibration_error"] = (out["observed_rate"] - out["predicted_mean"]).abs()
    out["sample_quality"] = np.where(out["n"] >= MIN_CALIBRATION_BIN_N, "usable", "thin_bin")
    out["prob_bin"] = out["prob_bin"].astype(str)
    return out


def _mace(reliability: pd.DataFrame) -> float:
    if reliability is None or reliability.empty:
        return float("nan")
    usable = reliability[reliability["sample_quality"] == "usable"]
    return float(usable["abs_calibration_error"].mean()) if not usable.empty else float("nan")


def horizon_calibration_metrics(dataset: pd.DataFrame, horizons=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-horizon (observed, predicted, Brier, log-loss, AUC, MACE) + reliability rows."""
    horizons = horizons or CALIBRATION_HORIZONS
    metric_rows, rel_tables = [], []
    for h in horizons:
        h = int(h)
        pred_col, actual_col = f"pred_retry_within_{h}d", f"actual_retry_within_{h}d"
        if dataset is None or dataset.empty or pred_col not in dataset.columns:
            continue
        d = dataset[[pred_col, actual_col]].dropna()
        if d.empty:
            continue
        y = d[actual_col].astype(int).to_numpy()
        p = d[pred_col].astype(float).clip(0, 1).to_numpy()
        rel = _reliability_table(dataset, pred_col, actual_col, h)
        if not rel.empty:
            rel_tables.append(rel)
        metric_rows.append({
            "horizon_days": h, "n": int(len(d)),
            "observed_rate": float(y.mean()), "predicted_mean": float(p.mean()),
            "brier_score": _safe_brier(y, p), "log_loss": _safe_log_loss(y, p),
            "auc": _safe_auc(y, p), "mean_abs_calibration_error_by_bin": _mace(rel),
        })
    metrics = pd.DataFrame(metric_rows)
    reliability = pd.concat(rel_tables, ignore_index=True) if rel_tables else pd.DataFrame()
    return metrics, reliability


# ---------------------------------------------------------------------------
# 3. Isotonic calibration transform (the V12.6 §5 to-do)
# ---------------------------------------------------------------------------

def apply_isotonic_knots(x_thresholds, y_thresholds, p) -> float:
    """Serializable isotonic application: piecewise-linear interpolation over knots."""
    if x_thresholds is None or y_thresholds is None or len(x_thresholds) < 2 or pd.isna(p):
        return float(p) if not pd.isna(p) else float("nan")
    return float(np.interp(float(np.clip(p, 0, 1)), x_thresholds, y_thresholds))


def fit_isotonic_per_horizon(dataset: pd.DataFrame, horizons=None) -> dict[int, dict[str, Any]]:
    """Fit isotonic(pred→observed) per horizon on the purged OOF predictions.

    Returns, per horizon, serializable knots (x/y thresholds) + the post-calibration
    Brier/log-loss/MACE so the transform's value is auditable.
    """
    horizons = horizons or CALIBRATION_HORIZONS
    out: dict[int, dict[str, Any]] = {}
    if not _SKLEARN:
        return out
    for h in horizons:
        h = int(h)
        pred_col, actual_col = f"pred_retry_within_{h}d", f"actual_retry_within_{h}d"
        if dataset is None or dataset.empty or pred_col not in dataset.columns:
            continue
        d = dataset[[pred_col, actual_col]].dropna()
        if len(d) < GATE_MIN_N or d[actual_col].nunique() < 2:
            continue
        x = d[pred_col].astype(float).clip(0, 1).to_numpy()
        y = d[actual_col].astype(int).to_numpy()
        try:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(x, y)
            p_cal = iso.predict(x)
        except Exception:
            continue
        # serializable knots
        xs = np.asarray(iso.X_thresholds_, dtype=float)
        ys = np.asarray(iso.y_thresholds_, dtype=float)
        rel_cal = _reliability_table(
            pd.DataFrame({pred_col: p_cal, actual_col: y}), pred_col, actual_col, h)
        out[h] = {
            "x_thresholds": [float(v) for v in xs],
            "y_thresholds": [float(v) for v in ys],
            "post_calibration_brier": _safe_brier(y, p_cal),
            "post_calibration_log_loss": _safe_log_loss(y, p_cal),
            "post_calibration_mace": _mace(rel_cal),
        }
    return out


# ---------------------------------------------------------------------------
# 4. Orchestrator: calibration_context + per-horizon trust gate
# ---------------------------------------------------------------------------

def _records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.replace({np.nan: None}).to_dict("records")


def _gate_for_horizon(metric: Mapping[str, Any], iso: Mapping[str, Any] | None) -> dict[str, Any]:
    """Gate on AUC (discrimination, transform-invariant), RAW reliability MACE, and n.

    We deliberately do NOT gate on the isotonic post-calibration MACE: it is fit and
    scored on the same rows here, so it is in-sample-optimistic (≈0) and would make the
    MACE check vacuous. AUC is invariant to the monotonic isotonic map, so it is the
    honest discrimination signal.
    """
    n = int(metric.get("n") or 0)
    auc = metric.get("auc")
    mace_raw = metric.get("mean_abs_calibration_error_by_bin")
    reasons = []
    if n < GATE_MIN_N:
        reasons.append(f"n<{GATE_MIN_N}")
    if auc is None or pd.isna(auc) or auc < GATE_MIN_AUC:
        reasons.append(f"auc<{GATE_MIN_AUC}")
    if mace_raw is None or pd.isna(mace_raw) or mace_raw > GATE_MAX_MACE:
        reasons.append(f"mace_raw>{GATE_MAX_MACE}")
    return {"passed": len(reasons) == 0, "n": n,
            "auc": (None if auc is None or pd.isna(auc) else float(auc)),
            "mace_raw": (None if mace_raw is None or pd.isna(mace_raw) else float(mace_raw)),
            "fail_reasons": reasons}


def build_calibration_context(panel: pd.DataFrame, horizons=None,
                              live_row: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Full calibration_context: per-horizon metrics + isotonic + trust gate.

    If ``live_row`` (the latest at-risk state) is given, also returns its calibrated
    surfaced probabilities so the envelope can present a calibrated, gated number.
    """
    horizons = horizons or CALIBRATION_HORIZONS
    if not _SKLEARN:
        return {"available": False, "warning": "sklearn_unavailable"}
    dataset = build_horizon_calibration_dataset(panel, horizons)
    if dataset.empty:
        return {"available": False, "warning": "no_calibration_rows",
                "schema_version": CALIBRATION_SCHEMA_VERSION}

    metrics, reliability = horizon_calibration_metrics(dataset, horizons)
    isotonic = fit_isotonic_per_horizon(dataset, horizons)
    metrics_by_h = {int(m["horizon_days"]): m for m in _records(metrics)}

    gate = {}
    summary = []
    for h in horizons:
        h = int(h)
        m = metrics_by_h.get(h)
        if m is None:
            continue
        iso = isotonic.get(h)
        g = _gate_for_horizon(m, iso)
        gate[str(h)] = g
        summary.append({
            "horizon_days": h, "n": m["n"], "observed_rate": m["observed_rate"],
            "predicted_mean": m["predicted_mean"], "brier_score": m["brier_score"],
            "log_loss": m["log_loss"], "auc": m["auc"],
            "mace_raw": m["mean_abs_calibration_error_by_bin"],
            # in-sample (optimistic) — NOT used by the gate; see disclaimers.
            "mace_calibrated_in_sample": (iso or {}).get("post_calibration_mace"),
            "brier_calibrated_in_sample": (iso or {}).get("post_calibration_brier"),
            "trust_gate_passed": g["passed"],
        })

    ctx: dict[str, Any] = {
        "available": True,
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "probability_policy": HORIZON_PROB_POLICY,
        "method": "purged_leave_one_transition_out_horizon_calibration_plus_isotonic",
        "n_calibration_rows": int(len(dataset)),
        "n_transitions": int(dataset["transition_key"].nunique()) if "transition_key" in dataset.columns else None,
        "horizons": [int(h) for h in horizons],
        "summary": summary,
        "reliability_table": _records(reliability),
        "isotonic_transforms": {str(h): isotonic[h] for h in isotonic},
        "trust_gate": gate,
        "gate_thresholds": {"max_mace": GATE_MAX_MACE, "min_auc": GATE_MIN_AUC, "min_n": GATE_MIN_N},
        "disclaimers": [
            "Calibrates the empirical completed-path estimator (not the diagnostic model curve).",
            "Purged, transition-aware (leave-one-transition-out) — no own-outcome leakage.",
            "Trust gate uses AUC (discrimination) + RAW reliability MACE + n; it does NOT use the "
            "isotonic post-calibration MACE, which is in-sample-optimistic (fit and scored on the same rows).",
            "A horizon's probability is trustworthy only where trust_gate.passed is true.",
            "Not financial advice; evidence overlay only.",
        ],
    }

    if live_row is not None:
        ref = build_empirical_horizon_reference(panel)
        live_emp = empirical_horizon_probabilities_for_row(dict(live_row), ref, horizons)
        live = {}
        for h in horizons:
            h = int(h)
            raw = live_emp.get(h, {}).get("cumulative_retry_probability")
            iso = isotonic.get(h)
            cal = apply_isotonic_knots(iso.get("x_thresholds"), iso.get("y_thresholds"), raw) if iso else raw
            g = gate.get(str(h), {"passed": False})
            live[str(h)] = {
                "raw_probability": (None if raw is None or pd.isna(raw) else float(raw)),
                "calibrated_probability": (None if cal is None or pd.isna(cal) else float(cal)),
                "trust_gate_passed": bool(g.get("passed")),
            }
        ctx["live_calibrated_horizon_probabilities"] = live
    return ctx
