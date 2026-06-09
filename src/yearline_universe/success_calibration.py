"""V13.4 Phase 8 (RS-3) — calibrate the success classifier, blend with the empirical baseline, gate.

RS-2 showed the success classifier **ranks** well (leave-one-ticker-out AUC ≈ 0.71, beating the RS-1
empirical baseline and the base rate) but is **mis-calibrated** (MACE ≈ 0.13 > the 0.10 gate;
over-confident). RS-3 closes that loop the Phase-4/6/7 way:

  * **honest out-of-fold isotonic** recalibration of the (already ticker-LOO) classifier predictions —
    a *second* GroupKFold-by-episode purge, so the calibrated MACE is not in-sample-optimistic;
  * a **classifier↔empirical blend** (convex, ``w`` by OOF Brier — the Phase-7 lever);
  * the **trust gate** (AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50) applied per surface, recommending the
    best gate-passing surface or **abstaining** if none clears it.

``success_oof_surfaces`` exposes the per-attempt leave-one-ticker-out predictions for every candidate
surface so the RS-3 *reliability* diagnostic (``success_reliability``) can interrogate *how* the blend
achieves its calibration (true calibration vs. variance-shrinkage to the base rate).

Capability-before-consumer. Educational research only; not financial advice.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .success_models import build_success_model_table, SUCCESS_MODEL_FEATURES
from .models import make_direct_horizon_logistic, _clean_matrix
from .generalization import _grouped_oof, episode_row_weights, calibration_metrics, _brier, BLEND_GRID
from .calibration import GATE_MIN_AUC, GATE_MAX_MACE, GATE_MIN_N

try:
    from sklearn.isotonic import IsotonicRegression
    from sklearn.model_selection import GroupKFold
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

__all__ = [
    "SUCCESS_CALIBRATION_VERSION",
    "success_oof_surfaces",
    "evaluate_success_calibration_gate",
    "build_and_evaluate_success_calibration",
]

SUCCESS_CALIBRATION_VERSION = "v13_phase8_success_calibration_gate"


def _oof_isotonic(raw: np.ndarray, y: np.ndarray, groups: np.ndarray, n_splits: int = 5) -> np.ndarray:
    """Out-of-fold isotonic calibration (GroupKFold-purged by ``groups``) of already-OOF predictions."""
    raw = np.asarray(raw, dtype=float)
    y = np.asarray(y, dtype=float)
    out = np.full(len(y), np.nan, dtype=float)
    m = np.isfinite(raw) & np.isfinite(y)
    idx = np.where(m)[0]
    if len(idx) < GATE_MIN_N:
        return out
    rr, yy, gg = raw[idx], y[idx], np.asarray(groups)[idx]
    k = min(int(n_splits), len(np.unique(gg)))
    if k < 2:
        return out
    cal = np.full(len(idx), np.nan, dtype=float)
    for tr, te in GroupKFold(n_splits=k).split(rr.reshape(-1, 1), yy, gg):
        if len(np.unique(yy[tr])) < 2:
            cal[te] = float(np.mean(yy[tr]))
            continue
        iso = IsotonicRegression(out_of_bounds="clip").fit(rr[tr], yy[tr])
        cal[te] = iso.predict(rr[te])
    out[idx] = cal
    return out


def success_oof_surfaces(table: pd.DataFrame, feature_columns=None, n_splits: int = 5) -> dict[str, Any]:
    """Per-attempt leave-one-ticker-out predictions for every candidate surface (arrays, not metrics).

    Returns ``{available, y, episode, ticker, base_rate, blend_weight_classifier, surfaces}`` where
    ``surfaces`` maps name → np.ndarray of P(success): classifier_raw, classifier_isotonic,
    empirical_baseline, blend, blend_isotonic. Shared by the gate evaluator and the reliability diagnostic.
    """
    if not _SKLEARN:
        return {"available": False, "warning": "sklearn_unavailable"}
    if table is None or table.empty or "y_success" not in table.columns:
        return {"available": False, "warning": "empty_table"}
    cols = list(feature_columns) if feature_columns is not None else SUCCESS_MODEL_FEATURES
    feats = [c for c in cols if c in table.columns]
    y_all = pd.to_numeric(table["y_success"], errors="coerce")
    valid = y_all.notna().to_numpy()
    y = y_all[valid].astype(int).to_numpy()
    if len(np.unique(y)) < 2:
        return {"available": False, "warning": "single_class"}
    X = _clean_matrix(table.loc[valid], feats)
    episode = (table.loc[valid, "episode_key"].astype(str).to_numpy()
               if "episode_key" in table.columns else np.arange(len(y)).astype(str))
    ticker = (table.loc[valid, "ticker"].astype(str).to_numpy()
              if "ticker" in table.columns else episode)
    w = episode_row_weights(episode)
    emp = (pd.to_numeric(table.loc[valid, "empirical_success_pred"], errors="coerce").to_numpy()
           if "empirical_success_pred" in table.columns else np.full(len(y), np.nan))

    clf = _grouped_oof(make_direct_horizon_logistic, X, y, ticker, w, leave_one_out=True, n_splits=n_splits)
    clf_iso = _oof_isotonic(clf, y, episode, n_splits)
    if np.isnan(clf_iso).all():
        clf_iso = clf

    mask = np.isfinite(clf) & np.isfinite(emp) & np.isfinite(y)
    best_w = 0.5
    if mask.sum() >= GATE_MIN_N and len(np.unique(y[mask])) > 1:
        best_w = min(BLEND_GRID, key=lambda ww: (_brier(y[mask], ww * clf[mask] + (1 - ww) * emp[mask])
                     if _brier(y[mask], ww * clf[mask] + (1 - ww) * emp[mask]) is not None else np.inf))
    emp_filled = np.where(np.isfinite(emp), emp, np.nanmean(emp) if np.isfinite(emp).any() else float(y.mean()))
    blend = best_w * clf + (1 - best_w) * emp_filled
    blend_iso = _oof_isotonic(blend, y, episode, n_splits)
    if np.isnan(blend_iso).all():
        blend_iso = blend

    return {
        "available": True, "y": y, "episode": episode, "ticker": ticker,
        "base_rate": float(y.mean()), "blend_weight_classifier": float(best_w),
        "features_used": feats,
        "surfaces": {
            "classifier_raw": clf, "classifier_isotonic": clf_iso,
            "empirical_baseline": emp, "blend": blend, "blend_isotonic": blend_iso,
        },
    }


def _gate(metrics: dict) -> dict:
    auc, mace, n = metrics.get("auc"), metrics.get("mace"), metrics.get("n")
    reasons = []
    if auc is None or auc < GATE_MIN_AUC:
        reasons.append("auc_below_min")
    if mace is None or mace > GATE_MAX_MACE:
        reasons.append("mace_above_max")
    if n is None or n < GATE_MIN_N:
        reasons.append("n_below_min")
    return {"passed": not reasons, "fail_reasons": reasons}


def evaluate_success_calibration_gate(table: pd.DataFrame, feature_columns=None,
                                      n_splits: int = 5) -> dict[str, Any]:
    """Calibrate + blend + gate the success classifier (leave-one-ticker-out), per surface."""
    surf = success_oof_surfaces(table, feature_columns=feature_columns, n_splits=n_splits)
    if not surf.get("available"):
        return {"available": False, "warning": surf.get("warning", "unavailable")}
    y = surf["y"]
    out_surfaces: dict[str, Any] = {}
    for name, p in surf["surfaces"].items():
        mtr = calibration_metrics(y, p)
        out_surfaces[name] = {**{k: mtr[k] for k in ("auc", "mace", "ece", "reliability_slope", "brier", "n")},
                              "gate": _gate(mtr)}
    passing = [(n_, s["auc"]) for n_, s in out_surfaces.items() if s["gate"]["passed"] and s["auc"] is not None]
    recommended = max(passing, key=lambda t: t[1])[0] if passing else None
    return {
        "available": True, "version": SUCCESS_CALIBRATION_VERSION,
        "n": int(len(y)), "n_tickers": int(len(np.unique(surf["ticker"]))),
        "n_episodes": int(len(np.unique(surf["episode"]))), "base_rate": surf["base_rate"],
        "cv": f"leave_one_ticker_out + episode-purged OOF isotonic(k<={n_splits})",
        "blend_weight_classifier": surf["blend_weight_classifier"],
        "gate_thresholds": {"min_auc": GATE_MIN_AUC, "max_mace": GATE_MAX_MACE, "min_n": GATE_MIN_N},
        "surfaces": out_surfaces,
        "recommended_surface": recommended,
        "gate_passed": recommended is not None,
        "disclaimers": [
            "MACE is honest out-of-fold isotonic (episode-purged), not in-sample-optimistic.",
            "recommended_surface = the gate-passing surface with the highest AUC; None ⇒ abstain (not yet).",
            "Isotonic is monotone ⇒ it changes calibration (MACE), not discrimination (AUC).",
            "A low MACE can reflect variance-shrinkage to the base rate — see the RS-3 reliability diagnostic.",
            "Capability-before-consumer; RS-4 surfaces only a gate-passing surface. Educational research only.",
        ],
    }


def build_and_evaluate_success_calibration(tickers_data: Mapping[str, Mapping[str, Any]],
                                           config: StudyConfig | None = None,
                                           n_splits: int = 5) -> dict[str, Any]:
    """Convenience: build the success table, then run calibrate + blend + gate."""
    table = build_success_model_table(tickers_data, config=config)
    result = evaluate_success_calibration_gate(table, n_splits=n_splits)
    result["table_rows"] = int(len(table))
    return result
