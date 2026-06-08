"""V13.3 Phase 7 (PR-C) — direct horizon classifier + episode-aware head-to-head.

The empirical completed-path estimator (Phase 3) is a *calibrated count* — "of similar
historical at-risk states, what fraction retried within H days." It conditions only on
**static buckets**. The direct horizon classifier instead learns a function of the
**path-dynamic features** (`features.py`) → ``P(retry within H)`` directly, so it can
represent *whether a repair is improving* (bouncing off the low, gap closing, vol
falling) — the thing the buckets are blind to.

This module trains that classifier and answers the only question that matters for
promotion:

  **Under episode-aware (leave-one-transition-out) cross-validation, does the direct
  classifier beat the empirical estimator's AUC at each horizon — without making the
  calibration (MACE) worse?**

Design choices that respect the small effective sample (~the number of independent
*transitions*, not daily rows):

  * **Primary = L2-regularized logistic** (impute → scale → logistic). Linear, low
    variance, probabilities stay meaningful for a calibration (MACE) read.
  * **Diagnostic-only = gradient boosting** (shallow, sub-sampled). Reported for an
    upper-bound on non-linear signal, **never** promoted — it overfits ~10² episodes.
  * **Episode-aware CV = GroupKFold purged by ``transition_key``** — an entire
    transition is in train or test, never split, so autocorrelated within-episode rows
    can't leak. The empirical baseline column (``empirical_pred_H``) is itself a
    leave-one-transition-out estimate, so the comparison is held-out vs held-out.

"Capability before consumer": this is **not** wired into the envelope yet. PR-D/E add
cross-sectional features + richer validation; only then does a winning model get
surfaced (behind the same trust gate as the empirical estimator). Educational research only.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from .labels import MODEL_FEATURE_COLUMNS, build_direct_horizon_dataset
from .calibration import (
    CALIBRATION_HORIZONS, CALIBRATION_BINS, MIN_CALIBRATION_BIN_N,
    GATE_MIN_N, GATE_MIN_AUC, GATE_MAX_MACE,
)

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    from sklearn.model_selection import GroupKFold
    from sklearn.metrics import roc_auc_score
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

__all__ = [
    "DIRECT_MODEL_VERSION",
    "MODEL_RANDOM_SEED",
    "MACE_TOLERANCE",
    "make_direct_horizon_logistic",
    "make_direct_horizon_gbm",
    "fit_direct_horizon_models",
    "evaluate_direct_horizon_models",
    "build_and_evaluate_direct_horizon_models",
]

DIRECT_MODEL_VERSION = "v13_phase7_direct_horizon_classifier"
MODEL_RANDOM_SEED = 42
# A horizon is only "promotable" if the classifier's calibration is no worse than the
# empirical baseline by more than this slack (MACE is noisy on ~10² episodes).
MACE_TOLERANCE = 0.02
DEFAULT_CV_SPLITS = 5


# ---------------------------------------------------------------------------
# Estimators (primary = logistic; diagnostic = GBM)
# ---------------------------------------------------------------------------

def make_direct_horizon_logistic() -> "Pipeline":
    """Primary model: median-impute → standardize → L2 logistic (low-variance, calibrated-ish)."""
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(C=1.0, max_iter=5000, random_state=MODEL_RANDOM_SEED)),
    ])


def make_direct_horizon_gbm() -> "Pipeline":
    """Diagnostic-only model: shallow, sub-sampled GBM (upper-bound on non-linear signal)."""
    return Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", GradientBoostingClassifier(
            n_estimators=200, max_depth=2, learning_rate=0.05, subsample=0.8,
            random_state=MODEL_RANDOM_SEED)),
    ])


# ---------------------------------------------------------------------------
# Metric helpers (MACE matches calibration.py's reliability-bin definition)
# ---------------------------------------------------------------------------

def _auc(y, p) -> float | None:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    m = np.isfinite(p)
    if m.sum() == 0 or len(np.unique(y[m])) < 2:
        return None
    try:
        return float(roc_auc_score(y[m], p[m]))
    except Exception:
        return None


def _binned_mace(y, p) -> float | None:
    """Mean abs calibration error over the same 10 bins / usable-bin floor as the gate."""
    d = pd.DataFrame({"p": np.clip(np.asarray(p, dtype=float), 0, 1),
                      "y": np.asarray(y, dtype=float)}).dropna()
    if d.empty:
        return None
    d["prob_bin"] = pd.cut(d["p"], bins=CALIBRATION_BINS, include_lowest=True)
    g = (d.groupby("prob_bin", observed=False)
           .agg(n=("y", "size"), pm=("p", "mean"), obs=("y", "mean")).reset_index())
    usable = g[g["n"] >= MIN_CALIBRATION_BIN_N]
    if usable.empty:
        return None
    return float((usable["obs"] - usable["pm"]).abs().mean())


def _clean_matrix(frame: pd.DataFrame, cols) -> np.ndarray:
    X = frame[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    return np.where(np.isfinite(X), X, np.nan)


def _oof_proba(make_estimator: Callable[[], Any], X: np.ndarray, y: np.ndarray,
               groups: np.ndarray, n_splits: int) -> np.ndarray:
    """Out-of-fold P(y=1) via GroupKFold purged by ``groups`` (transition_key).

    Degenerate folds (a single class in the training split) fall back to the train base
    rate for their test rows, so the OOF vector is always fully populated.
    """
    oof = np.full(len(y), np.nan, dtype=float)
    n_groups = int(len(np.unique(groups)))
    splits = min(int(n_splits), n_groups)
    if splits < 2:
        return oof
    for tr, te in GroupKFold(n_splits=splits).split(X, y, groups):
        if len(np.unique(y[tr])) < 2:
            oof[te] = float(np.mean(y[tr]))
            continue
        try:
            est = make_estimator().fit(X[tr], y[tr])
            oof[te] = est.predict_proba(X[te])[:, 1]
        except Exception:
            oof[te] = float(np.mean(y[tr]))
    return oof


# ---------------------------------------------------------------------------
# Final fit (for future scoring / consumption by PR-D/E)
# ---------------------------------------------------------------------------

def fit_direct_horizon_models(dataset: pd.DataFrame, horizons=None) -> dict[str, Any]:
    """Fit the primary (logistic) model on ALL valid rows, per horizon.

    Returns ``{"models": {h: fitted_pipeline}, "features": [...], ...}``. Not consumed by
    the envelope yet — provided so PR-D/E can score the live state once a horizon is promoted.
    """
    horizons = [int(h) for h in (horizons or CALIBRATION_HORIZONS)]
    if not _SKLEARN or dataset is None or dataset.empty:
        return {"available": False, "warning": "sklearn_unavailable" if not _SKLEARN else "empty_dataset"}
    feats = [c for c in MODEL_FEATURE_COLUMNS if c in dataset.columns]
    models: dict[int, Any] = {}
    for h in horizons:
        ycol = f"y_{h}"
        if ycol not in dataset.columns:
            continue
        y = pd.to_numeric(dataset[ycol], errors="coerce")
        valid = y.notna()
        yv = y[valid].astype(int).to_numpy()
        if len(np.unique(yv)) < 2:
            continue
        X = _clean_matrix(dataset.loc[valid], feats)
        try:
            models[h] = make_direct_horizon_logistic().fit(X, yv)
        except Exception:
            continue
    return {"available": bool(models), "model_version": DIRECT_MODEL_VERSION,
            "primary_model": "l2_logistic", "features": feats, "models": models}


# ---------------------------------------------------------------------------
# The head-to-head: classifier vs empirical baseline, episode-aware CV
# ---------------------------------------------------------------------------

def evaluate_direct_horizon_models(dataset: pd.DataFrame, horizons=None,
                                   n_splits: int = DEFAULT_CV_SPLITS,
                                   include_gbm: bool = True) -> dict[str, Any]:
    """Episode-aware OOF AUC/MACE for the direct classifier vs the empirical baseline.

    For each horizon, on the **identical** set of rows where the empirical baseline
    prediction exists, compute out-of-fold (GroupKFold purged by transition):
      * empirical baseline AUC / MACE  (``empirical_pred_H`` — itself LOTO),
      * logistic (primary) AUC / MACE,
      * GBM (diagnostic) AUC / MACE,
    and a per-horizon verdict (``promote_recommended`` = beats baseline AUC AND MACE not
    worse by > tolerance AND enough rows). The whole thing is JSON-serializable.
    """
    horizons = [int(h) for h in (horizons or CALIBRATION_HORIZONS)]
    if not _SKLEARN:
        return {"available": False, "warning": "sklearn_unavailable"}
    if dataset is None or dataset.empty:
        return {"available": False, "warning": "empty_dataset"}

    feats = [c for c in MODEL_FEATURE_COLUMNS if c in dataset.columns]
    missing = [c for c in MODEL_FEATURE_COLUMNS if c not in dataset.columns]
    if "transition_key" in dataset.columns:
        groups_all = dataset["transition_key"].astype(str).to_numpy()
    else:
        groups_all = np.arange(len(dataset)).astype(str)

    per_horizon: list[dict[str, Any]] = []
    for h in horizons:
        ycol, ecol = f"y_{h}", f"empirical_pred_{h}"
        if ycol not in dataset.columns:
            continue
        y_all = pd.to_numeric(dataset[ycol], errors="coerce")
        valid = y_all.notna().to_numpy()
        y = y_all[valid].astype(int).to_numpy()
        g = groups_all[valid]
        if len(np.unique(y)) < 2 or len(np.unique(g)) < 2:
            per_horizon.append({"horizon_days": h, "status": "degenerate_labels_or_groups",
                                "n": int(len(y))})
            continue

        X = _clean_matrix(dataset.loc[valid], feats)
        log_oof = _oof_proba(make_direct_horizon_logistic, X, y, g, n_splits)
        gbm_oof = (_oof_proba(make_direct_horizon_gbm, X, y, g, n_splits)
                   if include_gbm else np.full(len(y), np.nan))
        emp = (pd.to_numeric(dataset.loc[valid, ecol], errors="coerce").to_numpy()
               if ecol in dataset.columns else np.full(len(y), np.nan))

        # Fair head-to-head: score everything on rows where BOTH the baseline and the
        # classifier OOF predictions exist.
        shared = np.isfinite(log_oof) & np.isfinite(emp)
        n_shared = int(shared.sum())

        def _m(p):
            return {"auc": _auc(y[shared], p[shared]) if n_shared else None,
                    "mace": _binned_mace(y[shared], p[shared]) if n_shared else None}

        log_m, emp_m, gbm_m = _m(log_oof), _m(emp), _m(gbm_oof)
        # Classifier metrics on ALL its OOF rows too (baseline can be sparser).
        log_full = {"auc": _auc(y, log_oof), "mace": _binned_mace(y, log_oof),
                    "n": int(np.isfinite(log_oof).sum())}

        def _delta(a, b):
            return (float(a) - float(b)) if (a is not None and b is not None) else None

        auc_delta = _delta(log_m["auc"], emp_m["auc"])
        mace_delta = _delta(log_m["mace"], emp_m["mace"])
        beats_auc = auc_delta is not None and auc_delta > 0
        mace_ok = (log_m["mace"] is not None and emp_m["mace"] is not None
                   and log_m["mace"] <= emp_m["mace"] + MACE_TOLERANCE)

        per_horizon.append({
            "horizon_days": h, "status": "ok", "n_shared_rows": n_shared,
            "base_rate": float(y[shared].mean()) if n_shared else None,
            "empirical_baseline": {**emp_m, "n": int(np.isfinite(emp).sum())},
            "logistic": {**log_m, "n_shared": n_shared, "n_all_oof": log_full["n"],
                         "auc_all_oof": log_full["auc"], "mace_all_oof": log_full["mace"]},
            "gbm_diagnostic": {**gbm_m, "note": "diagnostic_only_never_promoted"},
            "auc_delta_logistic_minus_empirical": auc_delta,
            "mace_delta_logistic_minus_empirical": mace_delta,
            "logistic_beats_empirical_auc": bool(beats_auc),
            "logistic_mace_not_worse": bool(mace_ok),
            "promote_recommended": bool(beats_auc and mace_ok and n_shared >= GATE_MIN_N),
        })

    return {
        "available": True,
        "model_version": DIRECT_MODEL_VERSION,
        "primary_model": "l2_logistic",
        "diagnostic_model": "gbm_shallow_subsampled",
        "n_rows": int(len(dataset)),
        "n_transitions": int(pd.Series(groups_all).nunique()),
        "features_used": feats,
        "features_missing": missing,
        "cv": f"group_kfold_purged_by_transition(k<={int(n_splits)})",
        "mace_tolerance": MACE_TOLERANCE,
        "gate_thresholds": {"min_auc": GATE_MIN_AUC, "max_mace": GATE_MAX_MACE, "min_n": GATE_MIN_N},
        "horizons": per_horizon,
        "disclaimers": [
            "Episode-aware CV: GroupKFold purged by transition_key (held-out vs held-out).",
            "Logistic is primary; GBM is diagnostic-only and is never promoted (overfits ~10^2 episodes).",
            "promote_recommended ⇒ beats empirical AUC AND MACE not worse by > tolerance AND n>=min_n.",
            "Capability-before-consumer: not wired into the envelope; promotion is gated downstream.",
            "Educational research only; not a trading signal.",
        ],
    }


def build_and_evaluate_direct_horizon_models(tickers_data: Mapping[str, Mapping[str, Any]],
                                             config=None, horizons=None,
                                             n_splits: int = DEFAULT_CV_SPLITS,
                                             include_gbm: bool = True) -> dict[str, Any]:
    """Convenience: build the modeling table from ``tickers_data`` then run the head-to-head.

    ``tickers_data`` is the universe runner's pooled_data
    (``{ticker: {peer_group, price_df, recovery_table, live_diagnostic}}``).
    """
    horizons = [int(h) for h in (horizons or CALIBRATION_HORIZONS)]
    dataset = build_direct_horizon_dataset(tickers_data, config=config, horizons=horizons)
    result = evaluate_direct_horizon_models(dataset, horizons=horizons,
                                            n_splits=n_splits, include_gbm=include_gbm)
    result["dataset_rows"] = int(len(dataset))
    return result
