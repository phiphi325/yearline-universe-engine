"""V13.4 Phase 8 (RS-3 reliability) — Brier decomposition, shrinkage index, reliability curve."""
import numpy as np
import pandas as pd

from yearline_universe.success_models import SUCCESS_MODEL_FEATURES
from yearline_universe.success_reliability import (
    reliability_curve, brier_decomposition, success_reliability_diagnostic,
)


def _synthetic_success_table(n_tickers=9, attempts_per=16, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for ti in range(n_tickers):
        tk = f"TK{ti:02d}"
        for k in range(attempts_per):
            latent = float(rng.normal())
            y = int((latent + rng.normal(0, 0.5)) > 0)
            feat = {c: float(rng.normal()) for c in SUCCESS_MODEL_FEATURES}
            feat["repair_gap_pct"] = -2.0 * latent + rng.normal(0, 0.4)
            feat["return_20d"] = 2.0 * latent + rng.normal(0, 0.4)
            rows.append({"ticker": tk, "episode_key": f"{tk}|{k // 3}", "transition_key": f"{tk}|{k}",
                         "y_success": y,
                         "empirical_success_pred": float(np.clip(0.5 + rng.normal(0, 0.05), 0, 1)),  # near-flat
                         **feat})
    return pd.DataFrame(rows)


def test_brier_decomposition_identity_and_bounds():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 400)
    p = np.clip(0.5 + (y - 0.5) * 0.5 + rng.normal(0, 0.12, 400), 0, 1)
    d = brier_decomposition(y, p, n_bins=10)
    assert d["reliability"] >= 0 and d["resolution"] >= 0
    assert 0.0 <= d["uncertainty"] <= 0.25
    # Murphy identity holds up to the within-bin variance residual
    assert abs(d["brier"] - d["brier_reconstructed"]) < 0.05


def test_reliability_curve_shape():
    y = np.array([0, 1] * 20)
    p = np.linspace(0.05, 0.95, 40)
    c = reliability_curve(y, p, n_bins=5)
    assert set(c) == {"bin_pred", "bin_true", "bin_count"}
    assert len(c["bin_pred"]) == len(c["bin_true"]) == len(c["bin_count"]) >= 1
    assert sum(c["bin_count"]) == 40


def test_diagnostic_structure_and_shrinkage():
    diag = success_reliability_diagnostic(_synthetic_success_table(), n_splits=5)
    assert diag["available"] is True
    for name in ("classifier_raw", "empirical_baseline", "blend"):
        s = diag["per_surface"][name]
        assert {"auc", "mace", "std", "resolution", "reliability", "uncertainty", "reliability_diagram"} <= set(s)
    sh = diag["shrinkage"]
    assert {"variance_shrinkage_index", "mace_raw", "mace_blend", "mace_pure_shrink_to_base",
            "fraction_of_gain_from_shrinkage", "resolution_lost_to_shrinkage"} <= set(sh)
    # the blend collapses variance vs the raw classifier ⇒ shrinkage index in (0, 1]
    if sh["variance_shrinkage_index"] is not None:
        assert 0.0 < sh["variance_shrinkage_index"] <= 1.0
    # the blend is no sharper than the raw classifier (resolution not increased)
    assert diag["per_surface"]["blend"]["std"] <= diag["per_surface"]["classifier_raw"]["std"] + 1e-9
