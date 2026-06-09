"""V13.4 Phase 8 (RS-3) — calibrate + blend + gate the success classifier."""
import numpy as np
import pandas as pd

from yearline_universe.success_models import SUCCESS_MODEL_FEATURES
from yearline_universe.success_calibration import (
    evaluate_success_calibration_gate, SUCCESS_CALIBRATION_VERSION,
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
                         "empirical_success_pred": float(np.clip(0.5 + 0.2 * y + rng.normal(0, 0.15), 0, 1)),
                         **feat})
    return pd.DataFrame(rows)


def test_calibration_gate_structure_and_isotonic_auc_invariance():
    res = evaluate_success_calibration_gate(_synthetic_success_table(), n_splits=5)
    assert res["available"] is True and res["version"] == SUCCESS_CALIBRATION_VERSION
    surfaces = res["surfaces"]
    for name in ("classifier_raw", "classifier_isotonic", "empirical_baseline", "blend", "blend_isotonic"):
        s = surfaces[name]
        assert {"auc", "mace", "brier", "n", "gate"} <= set(s)
        assert "passed" in s["gate"] and isinstance(s["gate"]["fail_reasons"], list)
        if s["auc"] is not None:
            assert 0.0 <= s["auc"] <= 1.0
    # isotonic is (per-fold) monotone ⇒ discrimination is essentially preserved
    raw, iso = surfaces["classifier_raw"]["auc"], surfaces["classifier_isotonic"]["auc"]
    assert raw is not None and iso is not None and abs(raw - iso) < 0.08
    # recommendation is consistent with gate_passed
    assert (res["recommended_surface"] is None) == (res["gate_passed"] is False)
    if res["recommended_surface"] is not None:
        assert res["recommended_surface"] in surfaces
    assert res["blend_weight_classifier"] in (0.0, 0.25, 0.5, 0.75, 1.0)


def test_empty_and_single_class_graceful():
    assert evaluate_success_calibration_gate(pd.DataFrame())["available"] is False
    one = _synthetic_success_table(n_tickers=3, attempts_per=6, seed=2).copy()
    one["y_success"] = 0
    assert evaluate_success_calibration_gate(one)["available"] is False
