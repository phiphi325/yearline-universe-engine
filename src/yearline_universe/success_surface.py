"""V13.4 Phase 8 (RS-4, consumer wiring) — the gate-passing retry-SUCCESS surface, live.

RS-1→RS-3 proved the capability (never surfaced): a regularized-logistic classifier on the attempt's
readiness state + path + cross-sectional features, **blended** with the RS-1 empirical success estimator,
**clears the trust gate** under leave-one-ticker-out (AUC ≈ 0.70, honest OOF MACE ≈ 0.036). RS-4 turns
that into a **live, gated overlay** — the capability→consumer step, mirroring the Phase-7 occurrence blend
(`blend_surface.py`) exactly:

  * ``build_success_surface_model(tickers_data)`` — universe-level, **compute-once**: build the RS-2
    success table, pick the convex blend weight ``w`` and record the **RS-3 trust gate** on the blend
    surface (the validated, recommended surface), and fit the classifier on all completed attempts for
    live scoring. The empirical success reference is kept for the live empirical anchor.
  * ``apply_success_live(model, live_feature_frame, live_empirical_success_prob)`` — cheap: score the live
    readiness state, blend with the live empirical success probability, attach the gate.
  * ``build_retry_success_context(...)`` — orchestrates the live success-state row (current recovery
    state + leakage-safe path/cross-sectional features at as-of) and the **composite**
    ``P(successful reclaim ≤ H) = P(retry ≤ H) × P(success │ retry)`` — surfaced **only where both the
    occurrence gate and the success gate pass**.

Success is a **single** probability (given an attempt, does it reclaim and hold?), not horizon-indexed;
only the *composite* is per-horizon (via the occurrence P(retry≤H)). The empirical/occurrence estimators
stay canonical — this is a clearly-labelled, gated overlay that never overwrites them. Surfacing is opt-in
(``surface_success=True``) and pooled-only (cross-sectional features need the universe). Educational
research only; not a trading signal.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .features import build_price_path_features, PATH_FEATURE_COLUMNS
from .cross_sectional import build_cross_sectional_features
from .success_labels import (
    build_empirical_success_reference, empirical_success_probability_for_row,
    SUCCESS_STATE_FEATURES,
)
from .success_models import build_success_model_table, SUCCESS_MODEL_FEATURES
from .success_calibration import evaluate_success_calibration_gate, success_oof_surfaces
from .models import make_direct_horizon_logistic, _clean_matrix
from .generalization import episode_row_weights

try:
    from sklearn.linear_model import LogisticRegression  # noqa: F401  (capability probe)
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

__all__ = [
    "SUCCESS_SURFACE_VERSION",
    "build_success_surface_model",
    "apply_success_live",
    "build_retry_success_context",
]

SUCCESS_SURFACE_VERSION = "v13_phase8_retry_success_overlay"
_DEFAULT_SUCCESS_W = 0.5
_COMPOSITE_HORIZONS = (10, 20, 40, 60)


# ---------------------------------------------------------------------------
# Compute-once model: fitted success classifier + blend weight + RS-3 gate
# ---------------------------------------------------------------------------

def build_success_surface_model(tickers_data: Mapping[str, Mapping[str, Any]],
                                config: StudyConfig | None = None,
                                n_splits: int = 5) -> dict[str, Any]:
    """Fit the success classifier on pooled completed attempts, pick the blend weight, and record the
    RS-3 trust gate on the recommended (blend) surface. Compute-once; reused per live ticker."""
    config = config or StudyConfig()
    if not _SKLEARN:
        return {"available": False, "warning": "sklearn_unavailable"}
    table = build_success_model_table(tickers_data, config=config)
    if table is None or table.empty or "y_success" not in table.columns:
        return {"available": False, "warning": "empty_success_table"}

    # RS-3 gate + blend weight on the honest leave-one-ticker-out OOF surfaces (the validated path).
    gate_eval = evaluate_success_calibration_gate(table, n_splits=n_splits)
    if not gate_eval.get("available"):
        return {"available": False, "warning": gate_eval.get("warning", "gate_unavailable")}
    surf = success_oof_surfaces(table, n_splits=n_splits)
    best_w = float(gate_eval.get("blend_weight_classifier", _DEFAULT_SUCCESS_W))
    blend_gate = (gate_eval.get("surfaces", {}).get("blend", {}) or {})
    gate = {"passed": bool(blend_gate.get("gate", {}).get("passed")),
            "auc": blend_gate.get("auc"), "mace": blend_gate.get("mace"),
            "n": gate_eval.get("n"),
            "fail_reasons": blend_gate.get("gate", {}).get("fail_reasons", [])}

    feats = [c for c in SUCCESS_MODEL_FEATURES if c in table.columns]
    y_all = pd.to_numeric(table["y_success"], errors="coerce")
    valid = y_all.notna().to_numpy()
    y = y_all[valid].astype(int).to_numpy()
    if len(np.unique(y)) < 2:
        return {"available": False, "warning": "single_class"}
    X = _clean_matrix(table.loc[valid], feats)
    episode = (table.loc[valid, "episode_key"].astype(str).to_numpy()
               if "episode_key" in table.columns else np.arange(len(y)).astype(str))
    w = episode_row_weights(episode)
    try:
        pipe = make_direct_horizon_logistic().fit(X, y, clf__sample_weight=w)
    except Exception:
        try:
            pipe = make_direct_horizon_logistic().fit(X, y)
        except Exception:
            return {"available": False, "warning": "classifier_fit_failed"}

    return {
        "available": True,
        "version": SUCCESS_SURFACE_VERSION,
        "pipeline": pipe,
        "feature_columns": feats,
        "blend_weight_classifier": best_w,
        "gate": gate,
        "recommended_surface": gate_eval.get("recommended_surface"),
        "base_rate": float(gate_eval.get("base_rate", float(y.mean()))),
        "n": int(len(y)),
        "n_tickers": gate_eval.get("n_tickers"),
        "empirical_reference": build_empirical_success_reference(table),
        "gate_thresholds": gate_eval.get("gate_thresholds"),
    }


# ---------------------------------------------------------------------------
# Live success-state row + feature frame (static recovery state + path + cross-sectional ≤ as-of)
# ---------------------------------------------------------------------------

def _norm(dt) -> pd.Timestamp:
    return pd.Timestamp(dt).normalize()


def _live_success_state(live_row: Mapping[str, Any], recovery_table, peer_group: str) -> dict[str, Any]:
    """The live readiness state for an imminent retry: the most recent recovery-table row (carries the
    SUCCESS_STATE_FEATURES + group/transition), overlaid with any fields present on the panel live row."""
    state: dict[str, Any] = {}
    if recovery_table is not None and not getattr(recovery_table, "empty", True):
        rt = recovery_table.copy()
        if "to_date" in rt.columns:
            rt = rt.sort_values("to_date")
        state = rt.iloc[-1].to_dict()
    # overlay panel live-row fields where present (preferring the freshest live state).
    for c in list(SUCCESS_STATE_FEATURES) + ["transition", "to_date", "as_of_date"]:
        v = (live_row or {}).get(c)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            state[c] = v
    state["group"] = peer_group or state.get("group", "unknown")
    if state.get("as_of_date") is None:
        state["as_of_date"] = state.get("to_date")
    return state


def _live_success_feature_frame(tickers_data, live_ticker, state_row, config,
                                feature_columns) -> pd.DataFrame:
    """One row of SUCCESS_MODEL_FEATURES for the live readiness state (leakage-safe at as-of)."""
    row: dict[str, Any] = {c: np.nan for c in feature_columns}
    for c in SUCCESS_STATE_FEATURES:
        if c in feature_columns and c in state_row and pd.notna(state_row.get(c)):
            row[c] = float(state_row[c])

    as_of = _norm(state_row.get("as_of_date") or state_row.get("to_date"))
    pdf = (tickers_data.get(live_ticker) or {}).get("price_df")
    if pdf is not None and not getattr(pdf, "empty", True):
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
# Live apply + context block (+ occurrence × success composite)
# ---------------------------------------------------------------------------

def apply_success_live(model: Mapping[str, Any], live_feature_frame: pd.DataFrame,
                       live_empirical_success_prob: float | None) -> dict[str, Any]:
    """Blended P(success│retry) for the live readiness state + provenance + gate."""
    cols = model["feature_columns"]
    X = _clean_matrix(live_feature_frame, cols)
    p_clf = float(model["pipeline"].predict_proba(X)[0, 1])
    p_emp = (float(live_empirical_success_prob)
             if (live_empirical_success_prob is not None and not pd.isna(live_empirical_success_prob))
             else None)
    w = float(model["blend_weight_classifier"])
    p_blend = (w * p_clf + (1 - w) * p_emp) if p_emp is not None else p_clf
    return {
        "success_probability": float(np.clip(p_blend, 0.0, 1.0)),
        "classifier_probability": p_clf,
        "empirical_probability": p_emp,
        "blend_weight_classifier": w,
        "gate": model["gate"],
        "gate_passed": bool(model["gate"].get("passed")),
    }


def build_retry_success_context(tickers_data: Mapping[str, Mapping[str, Any]], live_ticker: str,
                                live_row: Mapping[str, Any] | None,
                                occurrence_probs: Mapping[int, float] | None = None,
                                occurrence_calibrated: Mapping[int, bool] | bool | None = None,
                                occurrence_surface: Mapping[int, str] | None = None,
                                config: StudyConfig | None = None,
                                model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the live ``retry_success_context`` overlay (opt-in, pooled-only, gated).

    ``occurrence_probs`` maps horizon → P(retry≤H) for the gate-verified occurrence surface (the caller
    prefers the Phase-7 blend where it passes, else the canonical empirical estimator); ``occurrence_surface``
    labels which one backed each horizon. The composite ``P(reclaim≤H) = P(retry≤H) × P(success│retry)`` is
    *surfaced* only where the success gate passes AND the occurrence side is gate-verified
    (``occurrence_calibrated``); otherwise it is labelled diagnostic. Never overwrites the canonical surfaces.
    """
    config = config or StudyConfig()
    if not _SKLEARN:
        return {"available": False, "warning": "sklearn_unavailable"}
    if not live_row or live_row.get("as_of_date") is None:
        return {"available": False, "warning": "no_live_row"}
    if len(tickers_data) < 2:
        return {"available": False, "warning": "success_overlay_requires_pooled_universe_for_cross_sectional_features"}
    model = model if (model and model.get("available")) else build_success_surface_model(tickers_data, config)
    if not model.get("available"):
        return {"available": False, "warning": model.get("warning", "success_model_unavailable")}

    peer_group = (tickers_data.get(live_ticker) or {}).get("peer_group", "unknown")
    recovery_table = (tickers_data.get(live_ticker) or {}).get("recovery_table")
    state_row = _live_success_state(live_row, recovery_table, peer_group)
    emp = empirical_success_probability_for_row(state_row, model["empirical_reference"])
    p_emp_success = emp.get("success_probability")

    live_feat = _live_success_feature_frame(tickers_data, live_ticker, state_row, config, model["feature_columns"])
    applied = apply_success_live(model, live_feat, p_emp_success)
    success_gate_passed = applied["gate_passed"]

    # composite P(reclaim ≤ H) = P(retry ≤ H) × P(success │ retry)
    def _occ_ok(h: int) -> bool:
        if isinstance(occurrence_calibrated, Mapping):
            return bool(occurrence_calibrated.get(h) or occurrence_calibrated.get(str(h)))
        return bool(occurrence_calibrated)

    composite: dict[str, Any] = {}
    p_succ = applied["success_probability"]
    for h in _COMPOSITE_HORIZONS:
        p_retry = (occurrence_probs or {}).get(h, (occurrence_probs or {}).get(str(h)))
        if p_retry is None or pd.isna(p_retry):
            continue
        both = bool(success_gate_passed and _occ_ok(h))
        val = float(p_retry) * float(p_succ)
        composite[str(h)] = {
            "p_retry_within_h": round(float(p_retry), 4),
            "occurrence_surface": ((occurrence_surface or {}).get(h) or (occurrence_surface or {}).get(str(h))),
            "p_success_given_retry": round(float(p_succ), 4),
            "p_successful_reclaim_within_h": round(val, 4),
            "occurrence_gate_passed": bool(_occ_ok(h)),
            "success_gate_passed": bool(success_gate_passed),
            "both_gates_passed": both,
            "surfaced_probability": (round(val, 4) if both else None),
        }

    return {
        "available": True,
        "schema": SUCCESS_SURFACE_VERSION,
        "policy": "gated_success_overlay_empirical_and_occurrence_remain_canonical",
        "p_success_given_retry": round(float(p_succ), 4),
        "classifier_probability": round(float(applied["classifier_probability"]), 4),
        "empirical_probability": (round(float(p_emp_success), 4) if p_emp_success is not None and not pd.isna(p_emp_success) else None),
        "empirical_reference_scope": emp.get("reference_scope"),
        "empirical_reference_n": emp.get("reference_n"),
        "blend_weight_classifier": round(float(applied["blend_weight_classifier"]), 3),
        "base_rate": round(float(model["base_rate"]), 4),
        "gate": applied["gate"],
        "gate_passed": bool(success_gate_passed),
        "surfaced_probability_is_calibrated": bool(success_gate_passed),
        "successful_reclaim_within_horizon": composite,
        "interpretation": (
            "P(success │ retry) is the gated blend of the RS-2 success classifier (readiness + "
            "cross-sectional features) with the RS-1 empirical success estimator — given an attempt at the "
            "yearline, will it reclaim and hold? It is a single probability (not horizon-indexed). The "
            "composite multiplies it by the gate-verified occurrence P(retry≤H) — preferring the Phase-7 "
            "classifier↔empirical blend where it passes (it calibrates long horizons the isotonic-only "
            "surface cannot), else the canonical empirical estimator (see each horizon's occurrence_surface). "
            "A horizon's composite is surfaced only where BOTH the occurrence gate and the success gate pass."),
        "caveats": [
            "Thin sample (low-hundreds attempts); the gate PASS is high-variance — re-validate walk-forward.",
            "The blend's calibration is largely base-rate shrinkage (see reliability/); trust the ranking, "
            "size gently on the level.",
        ],
        "disclaimer": "Educational research only. Not financial advice. Not a calibrated trading signal.",
        "must_not_auto_execute": True,
    }
