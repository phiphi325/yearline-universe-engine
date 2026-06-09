"""V13.4 Phase 8 (RS-2) — direct retry-SUCCESS classifier + episode-aware head-to-head.

RS-1 showed the empirical base-rate-by-bucket estimator does **not** beat the flat success base rate
(AUC ≈ 0.49) — the *static* recovery-state buckets carry no out-of-sample signal for success. RS-2 asks
the next question: do the **readiness** (Phase-7 path-dynamic) + **cross-sectional** features — the same
levers that lifted the *occurrence* problem — add any predictable signal for *success*?

Mirrors the Phase-7 discipline:
  * **primary = L2 logistic** (impute → scale → logistic), reusing ``models.make_direct_horizon_logistic``;
  * **episode-aware CV**: GroupKFold purged by ``episode_key`` (attempts in the same round together)
    **and** leave-one-*ticker*-out (the unseen-name test) — reusing ``generalization``;
  * **head-to-head** vs the RS-1 empirical baseline (``empirical_success_pred``, leave-one-attempt-out)
    and the flat base rate, reporting AUC / Brier / MACE and **lift over the base rate**.

Features are leakage-safe: the static recovery state is known by the attempt's touch date, and the
path/cross-sectional features are computed *at* that touch date (backward-looking ≤ date). Still
capability-before-consumer — nothing surfaced. Educational research only; not financial advice.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .features import build_price_path_features, PATH_FEATURE_COLUMNS
from .cross_sectional import build_cross_sectional_features, CROSS_SECTIONAL_FEATURE_COLUMNS
from .success_labels import (
    build_success_dataset, build_empirical_success_reference,
    empirical_success_probability_for_row, SUCCESS_STATE_FEATURES,
)
from .models import make_direct_horizon_logistic, _clean_matrix
from .generalization import _grouped_oof, episode_row_weights, calibration_metrics, _brier

try:
    from sklearn.linear_model import LogisticRegression  # noqa: F401 (capability probe)
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

__all__ = [
    "SUCCESS_MODEL_FEATURES",
    "build_success_model_table",
    "evaluate_success_models",
    "build_and_evaluate_success_models",
]

# Path readiness features (the de-correlated subset; excludes raw MA levels + required_rebound).
_PATH_SUCCESS_FEATURES = [
    "return_5d", "return_10d", "return_20d",
    "distance_to_ma20_pct", "distance_to_ma50_pct", "price_above_ma20", "price_above_ma50",
    "ma20_change_10d_pct", "ma50_change_20d_pct", "repair_gap_pct",
    "distance_to_ma250_change_5d", "distance_to_ma250_change_10d",
    "distance_to_ma250_change_20d", "distance_to_ma250_slope_10d",
    "realized_vol_20d", "realized_vol_20d_pctile_252d", "range_compression_10d",
]
SUCCESS_MODEL_FEATURES = list(SUCCESS_STATE_FEATURES) + _PATH_SUCCESS_FEATURES + list(CROSS_SECTIONAL_FEATURE_COLUMNS)


def build_success_model_table(tickers_data: Mapping[str, Mapping[str, Any]],
                              config: StudyConfig | None = None) -> pd.DataFrame:
    """Attempt-level success table: RS-1 labels/state + path + cross-sectional features (at the
    attempt's touch date) + the RS-1 empirical baseline (leave-one-attempt-out)."""
    config = config or StudyConfig()
    ds = build_success_dataset(tickers_data, config=config)
    if ds is None or ds.empty or "to_date" not in ds.columns:
        return pd.DataFrame()
    ds = ds.copy()
    ds["as_of_date"] = pd.to_datetime(ds["to_date"], errors="coerce").dt.normalize()

    # Path-dynamic features at the attempt's touch date (leakage-safe ≤ date).
    path_cols = [c for c in _PATH_SUCCESS_FEATURES if c in PATH_FEATURE_COLUMNS]
    parts = []
    for tk, d in tickers_data.items():
        pdf = d.get("price_df")
        if pdf is None or getattr(pdf, "empty", True):
            continue
        pf = build_price_path_features(pdf, config).copy()
        pf["as_of_date"] = pd.to_datetime(pf.index).normalize()
        pf["ticker"] = tk
        parts.append(pf[["ticker", "as_of_date"] + path_cols])
    if parts:
        ds = ds.merge(pd.concat(parts, ignore_index=True), on=["ticker", "as_of_date"], how="left")

    # Cross-sectional regime at the attempt's touch date.
    xs = build_cross_sectional_features(tickers_data, config)
    if not xs.empty:
        xs = xs.copy()
        xs["as_of_date"] = pd.to_datetime(xs["as_of_date"]).dt.normalize()
        ds = ds.merge(xs, on=["ticker", "as_of_date"], how="left")

    # RS-1 empirical baseline prediction, leave-one-attempt-out (the surface to beat).
    ref = build_empirical_success_reference(ds)
    ds["empirical_success_pred"] = [
        empirical_success_probability_for_row(row, ref, exclude_transition_key=row.get("transition_key"))
        ["success_probability"]
        for row in ds.to_dict("records")
    ]
    return ds


def evaluate_success_models(table: pd.DataFrame, feature_columns=None,
                            n_splits: int = 5) -> dict[str, Any]:
    """Episode-purged + leave-one-ticker-out OOF for the success classifier vs the empirical
    baseline and the flat base rate. JSON-serializable."""
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
    base = float(y.mean())

    clf_episode = _grouped_oof(make_direct_horizon_logistic, X, y, episode, w, leave_one_out=False, n_splits=n_splits)
    clf_ticker = _grouped_oof(make_direct_horizon_logistic, X, y, ticker, w, leave_one_out=True, n_splits=n_splits)

    brier_base = _brier(y, np.full(len(y), base))

    def block(p):
        m = calibration_metrics(y, p)
        m["lift_over_base_brier"] = (None if (m["brier"] is None or brier_base is None)
                                     else round(float(brier_base - m["brier"]), 4))
        return m

    clf_tx, clf_tk, emp_m = block(clf_episode), block(clf_ticker), block(emp)
    gap = (None if (clf_tx["auc"] is None or clf_tk["auc"] is None)
           else round(float(clf_tx["auc"] - clf_tk["auc"]), 4))
    beats_emp = (clf_tk["auc"] is not None and emp_m["auc"] is not None and clf_tk["auc"] > emp_m["auc"])
    beats_base = (clf_tk["lift_over_base_brier"] is not None and clf_tk["lift_over_base_brier"] > 0
                  and clf_tk["auc"] is not None and clf_tk["auc"] > 0.5)

    return {
        "available": True, "model": "l2_logistic_success",
        "n": int(len(y)), "n_tickers": int(len(np.unique(ticker))), "n_episodes": int(len(np.unique(episode))),
        "base_rate": base, "brier_flat_base": (None if brier_base is None else round(brier_base, 4)),
        "features_used": feats, "n_features": len(feats),
        "cv": f"episode_purged_groupkfold(k<={n_splits}) + leave_one_ticker_out",
        "classifier_episode_purged": clf_tx,
        "classifier_ticker_loo": clf_tk,
        "empirical_baseline": emp_m,
        "generalization_gap_auc": gap,
        "classifier_beats_empirical_auc": bool(beats_emp),
        "classifier_beats_base": bool(beats_base),
        "disclaimers": [
            "Episode-aware CV: GroupKFold purged by episode_key + leave-one-ticker-out (unseen name).",
            "Beating the bar = ticker-LOO AUC > 0.5 AND Brier lift over the flat base rate > 0.",
            "Capability-before-consumer; success is surfaced only if RS-3 clears the trust gate.",
            "Educational research only; not a trading signal.",
        ],
    }


def build_and_evaluate_success_models(tickers_data: Mapping[str, Mapping[str, Any]],
                                      config: StudyConfig | None = None,
                                      n_splits: int = 5) -> dict[str, Any]:
    """Convenience: build the success modeling table from pooled_data, then run the head-to-head."""
    table = build_success_model_table(tickers_data, config=config)
    result = evaluate_success_models(table, n_splits=n_splits)
    result["table_rows"] = int(len(table))
    return result
