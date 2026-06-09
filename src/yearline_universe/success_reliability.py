"""V13.4 Phase 8 (RS-3 reliability) — is the blend's low MACE *true calibration* or *base-rate shrinkage*?

RS-3's headline: the classifier↔empirical **blend** clears the trust gate with out-of-fold MACE ≈ 0.036.
But the empirical baseline is nearly **flat** (≈ the base rate), so blending mostly **drags the
classifier's over-confident 80–90% predictions back toward the historical ~45%** — i.e. it buys
calibration by **shrinking variance (sharpness)**, not by correcting the model's feature weights.

This module quantifies exactly that, on the **real** leave-one-ticker-out surfaces:

  * **Brier (Murphy) decomposition** — `Brier = reliability − resolution + uncertainty` — separates
    *calibration* (reliability) from *informative sharpness* (resolution).
  * **Variance-shrinkage index** — `1 − var(blend)/var(raw)` — how far the blend collapsed toward center.
  * **Pure-shrinkage counterfactual** — shrink the raw classifier toward the base rate by the *same*
    variance factor, using **no** empirical information; its MACE tells us how much of the calibration
    gain is *just* shrinkage vs. the empirical anchor's bucket information.
  * **Reliability-diagram + prediction-histogram data** — for the figures (plotting lives in the
    runnable script under `docs/phased_design/phase_08/reliability/`; this module stays plot-free).

Pure functions, no I/O, no plotting (so it is unit-testable). Educational research only.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .config import StudyConfig
from .success_models import build_success_model_table
from .success_calibration import success_oof_surfaces
from .models import _binned_mace, _auc

__all__ = [
    "reliability_curve",
    "brier_decomposition",
    "success_reliability_diagnostic",
    "build_success_reliability_diagnostic",
]


def _finite(y, p):
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    m = np.isfinite(y) & np.isfinite(p)
    return y[m], p[m]


def reliability_curve(y, p, n_bins: int = 5) -> dict[str, list]:
    """Uniform-bin reliability curve: per-bin mean predicted prob, observed success fraction, count."""
    y, p = _finite(y, p)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    bp, bt, bc = [], [], []
    for b in range(n_bins):
        sel = idx == b
        if sel.sum() == 0:
            continue
        bp.append(round(float(p[sel].mean()), 4))
        bt.append(round(float(y[sel].mean()), 4))
        bc.append(int(sel.sum()))
    return {"bin_pred": bp, "bin_true": bt, "bin_count": bc}


def brier_decomposition(y, p, n_bins: int = 10) -> dict[str, float | None]:
    """Murphy decomposition: Brier = reliability − resolution + uncertainty (binned approximation)."""
    y, p = _finite(y, p)
    n = len(y)
    if n == 0:
        return {"brier": None, "reliability": None, "resolution": None, "uncertainty": None}
    ybar = float(y.mean())
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    rel = res = 0.0
    for b in range(n_bins):
        sel = idx == b
        nk = int(sel.sum())
        if nk == 0:
            continue
        pk, yk = float(p[sel].mean()), float(y[sel].mean())
        rel += (nk / n) * (pk - yk) ** 2
        res += (nk / n) * (yk - ybar) ** 2
    unc = ybar * (1.0 - ybar)
    brier = float(np.mean((p - y) ** 2))
    return {"brier": round(brier, 5), "reliability": round(rel, 5), "resolution": round(res, 5),
            "uncertainty": round(unc, 5), "brier_reconstructed": round(rel - res + unc, 5)}


def _surface_metrics(y, p) -> dict[str, Any]:
    yy, pp = _finite(y, p)
    bd = brier_decomposition(y, p)
    return {"auc": _auc(y, p), "mace": _binned_mace(y, p),
            "std": round(float(np.std(pp)), 4) if len(pp) else None,
            "mean": round(float(np.mean(pp)), 4) if len(pp) else None,
            "p_gt_0.7": int(np.sum(pp > 0.7)), "p_lt_0.3": int(np.sum(pp < 0.3)),
            "reliability": bd["reliability"], "resolution": bd["resolution"],
            "uncertainty": bd["uncertainty"], "brier": bd["brier"],
            "reliability_diagram": reliability_curve(y, p)}


def success_reliability_diagnostic(table: pd.DataFrame, feature_columns=None, n_splits: int = 5,
                                   surfaces: Sequence[str] = ("classifier_raw", "empirical_baseline", "blend"),
                                   ) -> dict[str, Any]:
    """Reliability + sharpness decomposition of the RS-3 surfaces, with a base-rate-shrinkage analysis.

    Returns per-surface metrics (incl. reliability-diagram points + Brier decomposition) and a
    ``shrinkage`` block answering: *how much of the blend's low MACE is variance-shrinkage to the base
    rate vs. true calibration from the empirical anchor's information?*
    """
    surf = success_oof_surfaces(table, feature_columns=feature_columns, n_splits=n_splits)
    if not surf.get("available"):
        return {"available": False, "warning": surf.get("warning", "unavailable")}
    y = surf["y"]
    base = surf["base_rate"]
    S = surf["surfaces"]

    per_surface = {name: _surface_metrics(y, S[name]) for name in surfaces if name in S}

    # --- base-rate shrinkage decomposition (raw classifier -> blend) ---
    raw, emp, blend = S["classifier_raw"], S["empirical_baseline"], S["blend"]
    yv, rawv = _finite(y, raw)
    _, blendv = _finite(y, blend)
    var_raw = float(np.var(rawv)) if len(rawv) else 0.0
    var_blend = float(np.var(blendv)) if len(blendv) else 0.0
    shrinkage_index = (1.0 - var_blend / var_raw) if var_raw > 1e-12 else None

    mace_raw = _binned_mace(y, raw)
    mace_blend = _binned_mace(y, blend)

    # pure-shrinkage counterfactual: shrink raw toward the base rate by the SAME variance factor as the
    # blend, using NO empirical bucket information. s s.t. var(base + s*(raw-base)) == var(blend).
    s = float(np.sqrt(var_blend / var_raw)) if var_raw > 1e-12 else 0.0
    p_pure = np.clip(base + s * (raw - base), 0.0, 1.0)
    mace_pure = _binned_mace(y, p_pure)
    # the literal "shrink halfway to the base rate" surface (== the blend if empirical were exactly flat)
    p_half = np.clip(base + 0.5 * (raw - base), 0.0, 1.0)
    mace_half = _binned_mace(y, p_half)

    def _frac(a, b):
        return (round(float(a) / float(b), 3) if (a is not None and b not in (None, 0)) else None)

    total_gain = (mace_raw - mace_blend) if (mace_raw is not None and mace_blend is not None) else None
    shrink_gain = (mace_raw - mace_pure) if (mace_raw is not None and mace_pure is not None) else None
    info_gain = (mace_pure - mace_blend) if (mace_pure is not None and mace_blend is not None) else None

    # sharpness (resolution) traded away
    res_raw = per_surface.get("classifier_raw", {}).get("resolution")
    res_blend = per_surface.get("blend", {}).get("resolution")
    resolution_lost = (round(res_raw - res_blend, 5) if (res_raw is not None and res_blend is not None) else None)

    # how far the raw extremes were pulled toward center in the blend
    extreme = np.isfinite(raw) & np.isfinite(blend) & ((raw > 0.7) | (raw < 0.3))
    extreme_pull = round(float(np.mean(np.abs(blend[extreme] - raw[extreme]))), 4) if extreme.sum() else None

    shrinkage = {
        "base_rate": round(base, 4),
        "var_raw": round(var_raw, 5), "var_blend": round(var_blend, 5),
        "variance_shrinkage_index": (round(shrinkage_index, 3) if shrinkage_index is not None else None),
        "mace_raw": mace_raw, "mace_blend": mace_blend,
        "mace_pure_shrink_to_base": mace_pure, "mace_half_shrink_to_base": mace_half,
        "total_mace_gain": (round(total_gain, 4) if total_gain is not None else None),
        "gain_from_shrinkage": (round(shrink_gain, 4) if shrink_gain is not None else None),
        "gain_from_empirical_information": (round(info_gain, 4) if info_gain is not None else None),
        "fraction_of_gain_from_shrinkage": _frac(shrink_gain, total_gain),
        "resolution_raw": res_raw, "resolution_blend": res_blend, "resolution_lost_to_shrinkage": resolution_lost,
        "mean_abs_pull_of_raw_extremes": extreme_pull,
        "interpretation": (
            "The blend's low MACE is largely base-rate shrinkage: it matches the raw classifier shrunk "
            "toward the base rate by the same variance factor (mace_pure_shrink_to_base ≈ mace_blend), so "
            "fraction_of_gain_from_shrinkage near 1.0 means the empirical anchor added little beyond "
            "pulling predictions to center. It trades sharpness (resolution_lost_to_shrinkage) for "
            "calibration — honest and safe for sizing, but not a sharper model."),
    }

    return {
        "available": True, "n": int(len(y)), "base_rate": round(base, 4),
        "blend_weight_classifier": surf["blend_weight_classifier"],
        "per_surface": per_surface,
        "shrinkage": shrinkage,
        "disclaimers": [
            "All surfaces are leave-one-ticker-out OOF predictions (the deployment-relevant CV).",
            "Brier = reliability − resolution + uncertainty (Murphy); resolution = informative sharpness.",
            "pure_shrink_to_base = base_rate + s·(raw − base_rate) with s matching the blend's variance.",
            "Educational research only; not financial advice.",
        ],
    }


def build_success_reliability_diagnostic(tickers_data: Mapping[str, Mapping[str, Any]],
                                         config: StudyConfig | None = None,
                                         n_splits: int = 5) -> dict[str, Any]:
    """Convenience: build the success table, then run the reliability diagnostic."""
    table = build_success_model_table(tickers_data, config=config)
    out = success_reliability_diagnostic(table, n_splits=n_splits)
    out["table_rows"] = int(len(table))
    return out
