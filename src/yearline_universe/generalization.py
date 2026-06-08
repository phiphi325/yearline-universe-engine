"""V13.3 Phase 7 (PR-E) — generalization rigor for the direct horizon classifier.

PR-C/D proved *capability* (the classifier + cross-sectional features beat the empirical
estimator under transition-purged CV). PR-E asks the harder, decision-relevant question
before anything is surfaced:

  **Does it generalize to an UNSEEN ticker — and is it more robust blended with the
  empirical estimator than either alone?**

Four pieces, all conservative for ~10² episodes across a handful of names:

  1. **Leave-one-*ticker*-out CV** (LeaveOneGroupOut by ``ticker``). Transition-purged CV
     still lets the model learn a name's idiosyncrasies from its *other* episodes; ticker-LOO
     holds out the whole name. The drop (``generalization_gap``) is the honest cost of
     deploying on a name with no history.
  2. **Episode row weighting** (≈ 1/√rows-per-transition, mean-normalized) via
     ``sample_weight`` so a 200-day dormant episode doesn't outvote a 10-day one in the fit.
  3. **A matched ticker-LOO empirical baseline**: recompute the empirical estimate for each
     held-out ticker from a reference with that ticker removed — so classifier and baseline
     face the same unseen-name handicap.
  4. **Hierarchical-shrinkage blend**: a per-horizon convex mix ``w·classifier + (1−w)·empirical``,
     ``w`` chosen out-of-fold by Brier — lean on the classifier where it discriminates, on
     the (lower-variance, calibrated) estimator elsewhere.

Plus richer calibration metrics — ECE, quantile-MACE (equal-count bins), reliability slope,
Brier — alongside the binned MACE the trust gate uses. Still capability-before-consumer:
nothing is wired into the envelope here; PR-E produces the evidence for *which* surface to
gate in. Educational research only; not a trading signal.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .labels import MODEL_FEATURE_COLUMNS_WITH_XS, build_direct_horizon_dataset
from .calibration import CALIBRATION_HORIZONS, GATE_MIN_N, GATE_MIN_AUC, GATE_MAX_MACE
from .hazard import (
    build_hazard_daily_panel, build_empirical_horizon_reference,
    empirical_horizon_probabilities_for_row,
)
from .models import (
    make_direct_horizon_logistic, _auc, _binned_mace, _clean_matrix,
)

try:
    from sklearn.model_selection import GroupKFold
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

__all__ = [
    "GENERALIZATION_VERSION", "BLEND_GRID",
    "episode_row_weights", "calibration_metrics",
    "evaluate_generalization", "build_and_evaluate_generalization",
]

GENERALIZATION_VERSION = "v13_phase7_generalization"
BLEND_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]


# ---------------------------------------------------------------------------
# Row weighting + richer calibration metrics
# ---------------------------------------------------------------------------

def episode_row_weights(transition_keys) -> np.ndarray:
    """≈ 1/√(rows-in-this-transition), mean-normalized to 1 (down-weights long episodes)."""
    s = pd.Series([str(t) for t in transition_keys])
    counts = s.map(s.value_counts()).to_numpy(dtype=float)
    w = 1.0 / np.sqrt(np.where(counts > 0, counts, 1.0))
    return w * (len(w) / w.sum()) if w.sum() > 0 else w


def _ece(y, p) -> float | None:
    """Expected calibration error: sample-weighted |observed − predicted| over 10 bins."""
    d = pd.DataFrame({"p": np.clip(np.asarray(p, float), 0, 1), "y": np.asarray(y, float)}).dropna()
    if d.empty:
        return None
    d["b"] = pd.cut(d["p"], bins=np.linspace(0, 1, 11), include_lowest=True)
    g = d.groupby("b", observed=False).agg(n=("y", "size"), pm=("p", "mean"), obs=("y", "mean")).dropna()
    tot = g["n"].sum()
    return float(((g["n"] / tot) * (g["obs"] - g["pm"]).abs()).sum()) if tot else None


def _quantile_mace(y, p, q: int = 10) -> float | None:
    """MACE over equal-COUNT bins (robust when predictions cluster)."""
    d = pd.DataFrame({"p": np.clip(np.asarray(p, float), 0, 1), "y": np.asarray(y, float)}).dropna()
    if len(d) < q:
        return None
    d["b"] = pd.qcut(d["p"].rank(method="first"), q, labels=False)
    g = d.groupby("b").agg(pm=("p", "mean"), obs=("y", "mean"))
    return float((g["obs"] - g["pm"]).abs().mean())


def _reliability_slope(y, p) -> float | None:
    """OLS slope of observed ~ predicted; 1.0 = perfectly responsive, <1 = over-confident."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    m = np.isfinite(p) & np.isfinite(y)
    if m.sum() < 10 or np.var(p[m]) < 1e-12:
        return None
    return float(np.polyfit(p[m], y[m], 1)[0])


def _brier(y, p) -> float | None:
    p, y = np.asarray(p, float), np.asarray(y, float)
    m = np.isfinite(p) & np.isfinite(y)
    return float(np.mean((p[m] - y[m]) ** 2)) if m.sum() else None


def calibration_metrics(y, p) -> dict[str, Any]:
    """AUC (discrimination) + binned MACE / ECE / quantile-MACE / slope / Brier (calibration)."""
    return {
        "auc": _auc(y, p), "mace": _binned_mace(y, p), "ece": _ece(y, p),
        "quantile_mace": _quantile_mace(y, p), "reliability_slope": _reliability_slope(y, p),
        "brier": _brier(y, p), "n": int(np.isfinite(np.asarray(p, float)).sum()),
    }


# ---------------------------------------------------------------------------
# Grouped OOF (transition-purged k-fold OR leave-one-group-out), weighted
# ---------------------------------------------------------------------------

def _grouped_oof(make_estimator: Callable[[], Any], X: np.ndarray, y: np.ndarray,
                 groups: np.ndarray, weights: np.ndarray | None,
                 leave_one_out: bool, n_splits: int) -> np.ndarray:
    oof = np.full(len(y), np.nan, dtype=float)
    uniq = np.unique(groups)
    if len(uniq) < 2:
        return oof
    if leave_one_out:
        splits = [(np.where(groups != g)[0], np.where(groups == g)[0]) for g in uniq]
    else:
        k = min(int(n_splits), len(uniq))
        if k < 2:
            return oof
        splits = list(GroupKFold(n_splits=k).split(X, y, groups))
    for tr, te in splits:
        if len(np.unique(y[tr])) < 2:
            oof[te] = (float(np.average(y[tr], weights=weights[tr]))
                       if weights is not None else float(np.mean(y[tr])))
            continue
        est = make_estimator()
        try:
            if weights is not None:
                est.fit(X[tr], y[tr], clf__sample_weight=weights[tr])
            else:
                est.fit(X[tr], y[tr])
        except TypeError:           # estimator without a "clf" step / no sample_weight support
            est.fit(X[tr], y[tr])
        oof[te] = est.predict_proba(X[te])[:, 1]
    return oof


def _best_blend(y: np.ndarray, p_clf: np.ndarray, p_emp: np.ndarray) -> dict[str, Any]:
    """Pick w in BLEND_GRID minimizing Brier for w·clf + (1−w)·emp; report that blend's metrics."""
    mask = np.isfinite(p_clf) & np.isfinite(p_emp) & np.isfinite(y)
    if mask.sum() < GATE_MIN_N or len(np.unique(y[mask])) < 2:
        return {"available": False, "n": int(mask.sum())}
    yv, c, e = y[mask], p_clf[mask], p_emp[mask]
    best = None
    for w in BLEND_GRID:
        blend = w * c + (1.0 - w) * e
        b = _brier(yv, blend)
        if b is not None and (best is None or b < best[1]):
            best = (w, b, blend)
    if best is None:
        return {"available": False, "n": int(mask.sum())}
    w, _, blend = best
    m = calibration_metrics(yv, blend)
    return {"available": True, "best_w_classifier_weight": float(w), "n": int(mask.sum()),
            **{k: m[k] for k in ("auc", "mace", "ece", "quantile_mace", "reliability_slope", "brier")},
            "classifier_alone": {k: v for k, v in calibration_metrics(yv, c).items()
                                 if k in ("auc", "mace", "brier")},
            "empirical_alone": {k: v for k, v in calibration_metrics(yv, e).items()
                                if k in ("auc", "mace", "brier")}}


# ---------------------------------------------------------------------------
# Matched ticker-LOO empirical baseline (exclude the held-out name from the reference)
# ---------------------------------------------------------------------------

def _ticker_loo_empirical(tickers_data: Mapping[str, Mapping[str, Any]],
                          config: StudyConfig, horizons: list[int]) -> pd.DataFrame:
    """Empirical P(retry≤H) for each row computed from a reference with that row's TICKER
    removed — the matched unseen-name baseline. Keyed by (transition_key, trading_days_since_touch)."""
    panel = build_hazard_daily_panel(tickers_data, next(iter(tickers_data)), config)
    ref = build_empirical_horizon_reference(panel)
    if ref is None or ref.empty or "ticker" not in ref.columns:
        return pd.DataFrame()
    rows = []
    for tk in ref["ticker"].unique():
        other = ref[ref["ticker"] != tk]
        if other.empty:
            continue
        for _, r in ref[ref["ticker"] == tk].iterrows():
            probs = empirical_horizon_probabilities_for_row(r, other, horizons)
            rec = {"transition_key": r["transition_key"],
                   "trading_days_since_touch": r["trading_days_since_touch"]}
            for h in horizons:
                rec[f"empirical_tickerloo_{h}"] = probs[int(h)]["cumulative_retry_probability"]
            rows.append(rec)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# The PR-E evaluation
# ---------------------------------------------------------------------------

def evaluate_generalization(dataset: pd.DataFrame, feature_columns=None, horizons=None,
                            n_splits: int = 5) -> dict[str, Any]:
    """Transition-purged vs leave-one-ticker-out generalization, weighting effect, and the
    classifier↔empirical blend, per horizon. Uses ``empirical_tickerloo_H`` columns when
    present (matched baseline), else falls back to ``empirical_pred_H`` (transition-LOO).
    """
    horizons = [int(h) for h in (horizons or CALIBRATION_HORIZONS)]
    if not _SKLEARN:
        return {"available": False, "warning": "sklearn_unavailable"}
    if dataset is None or dataset.empty or "ticker" not in dataset.columns:
        return {"available": False, "warning": "empty_dataset_or_no_ticker"}

    cols = list(feature_columns) if feature_columns is not None else MODEL_FEATURE_COLUMNS_WITH_XS
    feats = [c for c in cols if c in dataset.columns]
    tx_groups_all = dataset["transition_key"].astype(str).to_numpy()
    tk_groups_all = dataset["ticker"].astype(str).to_numpy()

    per_h = []
    for h in horizons:
        ycol = f"y_{h}"
        if ycol not in dataset.columns:
            continue
        y_all = pd.to_numeric(dataset[ycol], errors="coerce")
        valid = y_all.notna().to_numpy()
        y = y_all[valid].astype(int).to_numpy()
        tx, tk = tx_groups_all[valid], tk_groups_all[valid]
        if len(np.unique(y)) < 2 or len(np.unique(tk)) < 2:
            per_h.append({"horizon_days": h, "status": "degenerate_labels_or_one_ticker",
                          "n": int(len(y)), "n_tickers": int(len(np.unique(tk)))})
            continue
        X = _clean_matrix(dataset.loc[valid], feats)
        w = episode_row_weights(tx)

        clf_tx = _grouped_oof(make_direct_horizon_logistic, X, y, tx, w, False, n_splits)
        clf_tk_w = _grouped_oof(make_direct_horizon_logistic, X, y, tk, w, True, n_splits)
        clf_tk_u = _grouped_oof(make_direct_horizon_logistic, X, y, tk, None, True, n_splits)

        emp_tx = (pd.to_numeric(dataset.loc[valid, f"empirical_pred_{h}"], errors="coerce").to_numpy()
                  if f"empirical_pred_{h}" in dataset.columns else np.full(len(y), np.nan))
        emp_tk = (pd.to_numeric(dataset.loc[valid, f"empirical_tickerloo_{h}"], errors="coerce").to_numpy()
                  if f"empirical_tickerloo_{h}" in dataset.columns else emp_tx)

        gap = (None if (_auc(y, clf_tx) is None or _auc(y, clf_tk_w) is None)
               else float(_auc(y, clf_tx) - _auc(y, clf_tk_w)))
        weff = (None if (_auc(y, clf_tk_w) is None or _auc(y, clf_tk_u) is None)
                else float(_auc(y, clf_tk_w) - _auc(y, clf_tk_u)))

        entry = {
            "horizon_days": h, "status": "ok", "n": int(len(y)),
            "n_tickers": int(len(np.unique(tk))), "base_rate": float(y.mean()),
            "transition_purged": {
                "classifier_weighted": calibration_metrics(y, clf_tx),
                "empirical": calibration_metrics(y, emp_tx),
            },
            "ticker_loo": {
                "classifier_weighted": calibration_metrics(y, clf_tk_w),
                "classifier_unweighted": calibration_metrics(y, clf_tk_u),
                "empirical": calibration_metrics(y, emp_tk),
            },
            "generalization_gap_auc": gap,
            "weighting_effect_auc": weff,
            "blend_ticker_loo": _best_blend(y, clf_tk_w, emp_tk),
        }
        per_h.append(entry)

    return {
        "available": True, "model_version": GENERALIZATION_VERSION,
        "n_rows": int(len(dataset)),
        "n_transitions": int(dataset["transition_key"].nunique()),
        "n_tickers": int(dataset["ticker"].nunique()),
        "features_used": feats,
        "cv": "transition_purged_groupkfold vs leave_one_ticker_out",
        "blend_grid_classifier_weight": BLEND_GRID,
        "gate_thresholds": {"min_auc": GATE_MIN_AUC, "max_mace": GATE_MAX_MACE, "min_n": GATE_MIN_N},
        "horizons": per_h,
        "disclaimers": [
            "Leave-one-ticker-out holds out a whole name; the gap is the unseen-name deployment cost.",
            "Row weights ≈ 1/sqrt(episode rows) affect the FIT only; metrics are unweighted (each at-risk day counts once).",
            "Blend weight w chosen out-of-fold by Brier; empirical baseline is the matched ticker-LOO estimate when available.",
            "Capability-before-consumer; promotion gated downstream. Educational research only.",
        ],
    }


def build_and_evaluate_generalization(tickers_data: Mapping[str, Mapping[str, Any]],
                                      config: StudyConfig | None = None, horizons=None,
                                      feature_columns=None, n_splits: int = 5,
                                      ticker_loo_empirical: bool = True) -> dict[str, Any]:
    """Build the (cross-sectional) modeling table, optionally attach the matched ticker-LOO
    empirical baseline, then run the PR-E generalization evaluation. ``tickers_data`` =
    the universe runner's pooled_data."""
    config = config or StudyConfig()
    horizons = [int(h) for h in (horizons or CALIBRATION_HORIZONS)]
    dataset = build_direct_horizon_dataset(tickers_data, config=config, horizons=horizons,
                                           include_cross_sectional=True)
    if not dataset.empty and ticker_loo_empirical:
        tk_emp = _ticker_loo_empirical(tickers_data, config, horizons)
        if not tk_emp.empty:
            dataset = dataset.merge(tk_emp, on=["transition_key", "trading_days_since_touch"], how="left")
    result = evaluate_generalization(dataset, feature_columns=feature_columns,
                                     horizons=horizons, n_splits=n_splits)
    result["dataset_rows"] = int(len(dataset))
    result["matched_ticker_loo_empirical"] = bool(ticker_loo_empirical
                                                   and any(c.startswith("empirical_tickerloo_") for c in dataset.columns))
    return result
