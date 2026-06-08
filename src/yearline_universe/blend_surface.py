"""V13.3 Phase 7 (consumer wiring) — the gated classifier↔empirical blend, live.

PR-A→E proved (capability, never surfaced) that a regularized-logistic direct horizon
classifier on path + cross-sectional features, **blended** with the empirical estimator,
beats either alone under the leave-one-*ticker*-out test on both discrimination (AUC) and
calibration (MACE) at every horizon. This module turns that capability into a **live,
gated overlay** for the envelope — the capability→consumer step.

Design (mirrors the compute-once calibration model):

  * ``build_blend_model(tickers_data)`` — universe-level, **compute-once**: per horizon,
    fit a row-weighted logistic on the pooled *completed* rows, choose the convex blend
    weight ``w`` (``w·classifier + (1−w)·empirical``) by out-of-fold (transition-purged)
    Brier, and record the blend's OOF **gate** (AUC ≥ 0.60, MACE ≤ 0.10, n ≥ 50 — the same
    gate basis as the empirical calibrator). The fitted pipelines live in memory and are
    threaded per-ticker exactly like ``calibration_model``.
  * ``apply_blend_live(model, live_feature_frame, live_empirical_probs)`` — cheap: score the
    live state, blend with the live empirical probability, attach the per-horizon gate.
  * ``build_blend_context(...)`` — orchestrates the live feature row (static panel state +
    leakage-safe path features at as-of + cross-sectional regime at as-of) and applies.

**The empirical estimator stays canonical.** This produces a clearly-labelled, gated
*discriminative overlay*; it never overwrites ``p_retry_within_{h}d``. Surfacing it is
opt-in (``surface_blend=True``) and pooled-only (the cross-sectional features need the
universe). Educational research only; not a trading signal.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .features import build_price_path_features
from .cross_sectional import build_cross_sectional_features
from .labels import MODEL_FEATURE_COLUMNS_WITH_XS, build_direct_horizon_dataset
from .calibration import CALIBRATION_HORIZONS, GATE_MIN_N, GATE_MIN_AUC, GATE_MAX_MACE
from .models import make_direct_horizon_logistic, _auc, _binned_mace, _clean_matrix
from .generalization import episode_row_weights, _grouped_oof, _brier, BLEND_GRID

try:
    from sklearn.linear_model import LogisticRegression  # noqa: F401  (capability probe)
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

__all__ = [
    "BLEND_SURFACE_VERSION",
    "build_blend_model",
    "apply_blend_live",
    "build_blend_context",
]

BLEND_SURFACE_VERSION = "v13_phase7_direct_classifier_blend_overlay"
# Default per-horizon prior on the classifier weight when a horizon can't pick its own
# (kept conservative — lean on the calibrated empirical estimator).
_DEFAULT_BLEND_W = 0.5


# ---------------------------------------------------------------------------
# Compute-once model: per-horizon fitted classifier + blend weight + gate
# ---------------------------------------------------------------------------

def build_blend_model(tickers_data: Mapping[str, Mapping[str, Any]],
                      config: StudyConfig | None = None, horizons=None) -> dict[str, Any]:
    """Fit the per-horizon row-weighted classifier on pooled completed rows, pick the blend
    weight by transition-purged OOF Brier, and record the blend's OOF gate. Compute-once.
    """
    config = config or StudyConfig()
    horizons = [int(h) for h in (horizons or CALIBRATION_HORIZONS)]
    if not _SKLEARN:
        return {"available": False, "warning": "sklearn_unavailable"}
    dataset = build_direct_horizon_dataset(tickers_data, config=config, horizons=horizons,
                                           include_cross_sectional=True)
    if dataset is None or dataset.empty or "transition_key" not in dataset.columns:
        return {"available": False, "warning": "empty_dataset"}

    feats = [c for c in MODEL_FEATURE_COLUMNS_WITH_XS if c in dataset.columns]
    groups_all = dataset["transition_key"].astype(str).to_numpy()
    per_horizon: dict[int, Any] = {}
    for h in horizons:
        ycol, ecol = f"y_{h}", f"empirical_pred_{h}"
        if ycol not in dataset.columns:
            continue
        y_all = pd.to_numeric(dataset[ycol], errors="coerce")
        valid = y_all.notna().to_numpy()
        y = y_all[valid].astype(int).to_numpy()
        if len(np.unique(y)) < 2:
            continue
        X = _clean_matrix(dataset.loc[valid], feats)
        g = groups_all[valid]
        w = episode_row_weights(g)
        emp = (pd.to_numeric(dataset.loc[valid, ecol], errors="coerce").to_numpy()
               if ecol in dataset.columns else np.full(len(y), np.nan))

        # transition-purged OOF for blend-weight selection + the honest gate.
        oof = _grouped_oof(make_direct_horizon_logistic, X, y, g, w, leave_one_out=False, n_splits=5)
        best_w, gate = _select_weight_and_gate(y, oof, emp)

        # final classifier fit on ALL completed rows (row-weighted) for live scoring.
        try:
            pipe = make_direct_horizon_logistic().fit(X, y, clf__sample_weight=w)
        except Exception:
            try:
                pipe = make_direct_horizon_logistic().fit(X, y)
            except Exception:
                continue
        per_horizon[int(h)] = {
            "pipeline": pipe, "feature_columns": feats,
            "blend_weight_classifier": float(best_w), "gate": gate,
            "base_rate": float(y.mean()), "n": int(len(y)),
        }

    return {
        "available": bool(per_horizon),
        "version": BLEND_SURFACE_VERSION,
        "horizons": [h for h in horizons if h in per_horizon],
        "feature_columns": feats,
        "per_horizon": per_horizon,
        "n_transitions": int(pd.Series(groups_all).nunique()),
        "gate_thresholds": {"min_auc": GATE_MIN_AUC, "max_mace": GATE_MAX_MACE, "min_n": GATE_MIN_N},
    }


def _select_weight_and_gate(y: np.ndarray, clf_oof: np.ndarray, emp: np.ndarray) -> tuple[float, dict]:
    """Pick w∈BLEND_GRID minimizing OOF Brier of w·clf+(1−w)·emp; gate that blend's OOF AUC/MACE."""
    mask = np.isfinite(clf_oof) & np.isfinite(emp) & np.isfinite(y)
    if mask.sum() < GATE_MIN_N or len(np.unique(y[mask])) < 2:
        # fall back to a conservative default; gate fails for lack of evidence.
        return _DEFAULT_BLEND_W, {"passed": False, "auc": None, "mace": None,
                                  "n": int(mask.sum()), "fail_reasons": ["insufficient_rows"]}
    yv, c, e = y[mask], clf_oof[mask], emp[mask]
    best = min(BLEND_GRID, key=lambda w: (_brier(yv, w * c + (1 - w) * e) if _brier(yv, w * c + (1 - w) * e) is not None else np.inf))
    blend = best * c + (1 - best) * e
    auc, mace, n = _auc(yv, blend), _binned_mace(yv, blend), int(mask.sum())
    reasons = []
    if auc is None or auc < GATE_MIN_AUC:
        reasons.append("auc_below_min")
    if mace is None or mace > GATE_MAX_MACE:
        reasons.append("mace_above_max")
    if n < GATE_MIN_N:
        reasons.append("n_below_min")
    return float(best), {"passed": not reasons, "auc": auc, "mace": mace, "n": n, "fail_reasons": reasons}


# ---------------------------------------------------------------------------
# Live feature row (static + path + cross-sectional, all ≤ as-of)
# ---------------------------------------------------------------------------

def _norm(dt) -> pd.Timestamp:
    return pd.Timestamp(dt).normalize()


def _live_feature_frame(tickers_data: Mapping[str, Mapping[str, Any]], live_ticker: str,
                        live_row: Mapping[str, Any], config: StudyConfig,
                        feature_columns: list[str]) -> pd.DataFrame:
    """One row of MODEL_FEATURE_COLUMNS_WITH_XS for the live state (leakage-safe at as-of)."""
    row: dict[str, Any] = {c: np.nan for c in feature_columns}
    # static repair-state straight off the panel's live row.
    for c in ("trading_days_since_touch", "drawdown_so_far_pct", "below_ma250_depth_so_far_pct",
              "from_touch_day_overshoot_pct", "attempt_no"):
        if c in feature_columns and c in live_row and pd.notna(live_row.get(c)):
            row[c] = float(live_row[c])

    as_of = _norm(live_row.get("as_of_date"))
    pdf = (tickers_data.get(live_ticker) or {}).get("price_df")
    if pdf is not None and not pdf.empty:
        pf = build_price_path_features(pdf, config)
        pf.index = pd.to_datetime(pf.index).normalize()
        elig = pf.index[pf.index <= as_of]
        if len(elig):
            src = pf.loc[elig[-1]]
            for c in feature_columns:
                if c in pf.columns and pd.notna(src.get(c)):
                    row[c] = float(src[c])

    xs = build_cross_sectional_features(tickers_data, config)
    if not xs.empty:
        xs = xs.copy()
        xs["as_of_date"] = pd.to_datetime(xs["as_of_date"]).dt.normalize()
        sub = xs[(xs["ticker"] == live_ticker) & (xs["as_of_date"] <= as_of)]
        if not sub.empty:
            src = sub.sort_values("as_of_date").iloc[-1]
            for c in feature_columns:
                if c in xs.columns and pd.notna(src.get(c)):
                    row[c] = float(src[c])
    return pd.DataFrame([row], columns=feature_columns)


# ---------------------------------------------------------------------------
# Live apply + context block
# ---------------------------------------------------------------------------

def apply_blend_live(model: Mapping[str, Any], live_feature_frame: pd.DataFrame,
                     live_empirical_probs: Mapping[int, float], horizons=None) -> dict[str, Any]:
    """Per-horizon blended P(retry≤H) for the live state + provenance + gate."""
    horizons = [int(h) for h in (horizons or model.get("horizons") or CALIBRATION_HORIZONS)]
    out: dict[str, Any] = {}
    per = model.get("per_horizon", {})
    for h in horizons:
        ph = per.get(int(h)) or per.get(h)
        if ph is None:
            continue
        cols = ph["feature_columns"]
        X = _clean_matrix(live_feature_frame, cols)
        try:
            p_clf = float(ph["pipeline"].predict_proba(X)[0, 1])
        except Exception:
            continue
        p_emp = live_empirical_probs.get(int(h))
        p_emp = float(p_emp) if (p_emp is not None and not pd.isna(p_emp)) else None
        w = float(ph["blend_weight_classifier"])
        p_blend = (w * p_clf + (1 - w) * p_emp) if p_emp is not None else p_clf
        out[int(h)] = {
            "blend_probability": p_blend,
            "classifier_probability": p_clf,
            "empirical_probability": p_emp,
            "blend_weight_classifier": w,
            "gate": ph["gate"],
            "gate_passed": bool(ph["gate"].get("passed")),
        }
    return out


def build_blend_context(tickers_data: Mapping[str, Mapping[str, Any]], live_ticker: str,
                        live_row: Mapping[str, Any] | None,
                        live_empirical_probs: Mapping[int, float] | None,
                        config: StudyConfig | None = None,
                        model: Mapping[str, Any] | None = None, horizons=None) -> dict[str, Any]:
    """Build the live ``direct_classifier_blend`` overlay block (opt-in, pooled-only).

    ``model`` (compute-once) is reused when supplied; otherwise it is built inline. Returns a
    self-describing, gated block — never overwrites the canonical empirical probability.
    """
    config = config or StudyConfig()
    horizons = [int(h) for h in (horizons or CALIBRATION_HORIZONS)]
    if not _SKLEARN:
        return {"available": False, "warning": "sklearn_unavailable"}
    if not live_row or live_row.get("as_of_date") is None:
        return {"available": False, "warning": "no_live_row"}
    if len(tickers_data) < 2:
        return {"available": False, "warning": "blend_requires_pooled_universe_for_cross_sectional_features"}
    model = model if (model and model.get("available")) else build_blend_model(tickers_data, config, horizons)
    if not model.get("available"):
        return {"available": False, "warning": model.get("warning", "blend_model_unavailable")}

    live_feat = _live_feature_frame(tickers_data, live_ticker, live_row, config, model["feature_columns"])
    emp = {int(h): (live_empirical_probs or {}).get(int(h)) for h in horizons}
    per_h = apply_blend_live(model, live_feat, emp, horizons)
    if not per_h:
        return {"available": False, "warning": "no_horizons_scored"}
    return {
        "available": True,
        "schema": BLEND_SURFACE_VERSION,
        "policy": "gated_discriminative_overlay_empirical_remains_canonical",
        "per_horizon": {str(h): per_h[h] for h in sorted(per_h)},
        "any_gate_passed": bool(any(v["gate_passed"] for v in per_h.values())),
        "interpretation": (
            "Per-horizon blend of the direct horizon classifier (path + cross-sectional "
            "features) with the empirical completed-path estimator. The empirical estimate "
            "remains the canonical P(retry<=H); this is a gated discriminative overlay."),
        "disclaimer": "Educational research only. Not financial advice. Not a calibrated trading signal.",
        "must_not_auto_execute": True,
    }
