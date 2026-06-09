"""V13.4 Phase 8 (RS-2) — direct success classifier head-to-head: structure, signal, CV plumbing."""
import numpy as np
import pandas as pd

from yearline_universe.success_models import (
    SUCCESS_MODEL_FEATURES, evaluate_success_models, build_success_model_table,
)


def _synthetic_success_table(n_tickers=8, attempts_per=15, seed=0):
    """Attempt-level table with a planted, learnable success signal + an episode grouping.
    `y` is a (noisy) threshold on a latent that two features reveal; the other ~31 features are noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for ti in range(n_tickers):
        tk = f"TK{ti:02d}"
        for k in range(attempts_per):
            latent = float(rng.normal())
            y = int((latent + rng.normal(0, 0.5)) > 0)
            feat = {c: float(rng.normal()) for c in SUCCESS_MODEL_FEATURES}
            feat["repair_gap_pct"] = -2.0 * latent + rng.normal(0, 0.4)   # planted signal
            feat["return_20d"] = 2.0 * latent + rng.normal(0, 0.4)
            rows.append({"ticker": tk, "episode_key": f"{tk}|{k // 3}", "transition_key": f"{tk}|{k}",
                         "y_success": y,
                         "empirical_success_pred": float(np.clip(0.5 + rng.normal(0, 0.1), 0, 1)),
                         **feat})
    return pd.DataFrame(rows)


def test_full_feature_run_is_well_formed():
    """Full ~33-feature run on a tiny sample: assert STRUCTURE (not an AUC threshold) — signal may be
    drowned by noise features at this n, which is exactly RS-2's real risk."""
    res = evaluate_success_models(_synthetic_success_table(), n_splits=4)
    assert res["available"] is True and res["model"] == "l2_logistic_success"
    assert res["n_tickers"] == 8 and 0.0 <= res["base_rate"] <= 1.0
    for k in ("classifier_episode_purged", "classifier_ticker_loo", "empirical_baseline"):
        assert {"auc", "mace", "brier", "lift_over_base_brier"} <= set(res[k])
    assert "generalization_gap_auc" in res
    assert isinstance(res["classifier_beats_empirical_auc"], bool)
    assert isinstance(res["classifier_beats_base"], bool)
    assert "leave_one_ticker_out" in res["cv"]


def test_signal_detected_on_focused_features():
    """When the signal features aren't drowned, the episode-purged OOF classifier discriminates."""
    res = evaluate_success_models(_synthetic_success_table(),
                                  feature_columns=["repair_gap_pct", "return_20d"], n_splits=4)
    assert res["available"] is True and res["n_features"] == 2
    auc = res["classifier_episode_purged"]["auc"]
    assert auc is not None and auc > 0.6, res["classifier_episode_purged"]
    # the Brier-lift-over-base field is computed
    assert "lift_over_base_brier" in res["classifier_ticker_loo"]


def test_empty_and_single_class_graceful():
    assert evaluate_success_models(pd.DataFrame())["available"] is False
    one = _synthetic_success_table(n_tickers=3, attempts_per=4, seed=1).copy()
    one["y_success"] = 1                                   # single class
    assert evaluate_success_models(one)["available"] is False
    assert build_success_model_table({}).empty             # empty pooled_data is graceful
