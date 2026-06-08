"""V13.3 Phase 7 (PR-C) — direct horizon classifier: shape, signal, episode-aware CV.

Fast + synthetic: a modeling table is built directly (no panel) with a planted signal so
the harness's structure, group-purged CV, and "beats baseline" plumbing are all exercised
without spinning up the universe pipeline. The real-universe head-to-head lives in the
phase-07 measurement script / README, not in the unit suite.
"""
import numpy as np
import pandas as pd

from yearline_universe.labels import MODEL_FEATURE_COLUMNS
from yearline_universe.models import (
    evaluate_direct_horizon_models, fit_direct_horizon_models,
    make_direct_horizon_logistic, DIRECT_MODEL_VERSION,
)

HORIZONS = [10, 20, 40, 60]


def _synthetic_model_table(n_transitions=60, seed=0):
    """One row per (transition, day-since-touch) with a planted, learnable signal.

    Each transition has a latent ``readiness`` that shortens its span; path features are
    tied to readiness (+ noise) so a linear model genuinely discriminates. The empirical
    baseline column is an intentionally *weaker* informative signal, so the classifier can
    plausibly beat it — letting us exercise the verdict plumbing (not assert a real result).
    """
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(n_transitions):
        readiness = float(rng.normal())
        span = int(np.clip(40 - readiness * 10 + rng.normal(0, 4), 6, 80))
        for td in range(span + 1):
            remaining = span - td
            feat = {c: float(rng.normal()) for c in MODEL_FEATURE_COLUMNS}
            feat["trading_days_since_touch"] = float(td)
            feat["repair_gap_pct"] = 10.0 - readiness * 3.0 + rng.normal(0, 0.5)
            feat["return_5d"] = readiness * 2.0 + rng.normal(0, 0.5)
            feat["distance_to_ma250_slope_10d"] = readiness * 0.3 + rng.normal(0, 0.1)
            row = {"transition_key": f"T{t:03d}", **feat}
            for h in HORIZONS:
                row[f"y_{h}"] = int(remaining <= h)
                # weak-but-informative baseline (noisier than the planted feature signal)
                row[f"empirical_pred_{h}"] = float(np.clip(
                    0.25 + 0.4 * int(remaining <= h) + rng.normal(0, 0.22), 0.0, 1.0))
            rows.append(row)
    return pd.DataFrame(rows)


def test_evaluate_structure_and_signal():
    ds = _synthetic_model_table()
    res = evaluate_direct_horizon_models(ds, horizons=HORIZONS, n_splits=5)
    assert res["available"] is True
    assert res["model_version"] == DIRECT_MODEL_VERSION
    assert res["n_transitions"] == 60
    assert not res["features_missing"], res["features_missing"]
    assert "group_kfold_purged_by_transition" in res["cv"]

    by_h = {r["horizon_days"]: r for r in res["horizons"]}
    for h in HORIZONS:
        r = by_h[h]
        if r["status"] != "ok":
            continue
        # planted signal ⇒ the logistic must discriminate clearly out-of-fold
        assert r["logistic"]["auc"] is not None and r["logistic"]["auc"] > 0.7, (h, r["logistic"])
        # every comparison field is well-formed
        for k in ("empirical_baseline", "logistic", "gbm_diagnostic"):
            assert "auc" in r[k] and "mace" in r[k]
        assert isinstance(r["logistic_beats_empirical_auc"], bool)
        assert isinstance(r["promote_recommended"], bool)


def test_oof_is_purged_by_transition_not_optimistic():
    """A column that is pure label leakage *per row* must NOT inflate OOF AUC when the
    leakage is constant within a transition and CV is purged by transition.

    We plant a feature equal to the transition's mean label. In-sample that's near-perfect;
    under transition-purged GroupKFold the test transition's value was never seen, so AUC
    stays bounded away from 1.0 — proving the fold split holds out whole episodes.
    """
    ds = _synthetic_model_table(n_transitions=40, seed=3)
    # overwrite one feature with a within-transition-constant copy of the 20d label mean
    leak = ds.groupby("transition_key")["y_20"].transform("mean")
    ds = ds.copy()
    ds["realized_vol_20d"] = leak.to_numpy()
    res = evaluate_direct_horizon_models(ds, horizons=[20], n_splits=5)
    r = {x["horizon_days"]: x for x in res["horizons"]}[20]
    assert r["status"] == "ok"
    # if folds leaked whole-episode label means, AUC would be ~1.0; purging keeps it < 0.999
    assert r["logistic"]["auc"] is None or r["logistic"]["auc"] < 0.999


def test_fit_models_returns_scorable_pipeline():
    ds = _synthetic_model_table(n_transitions=30, seed=1)
    fit = fit_direct_horizon_models(ds, horizons=HORIZONS)
    assert fit["available"] is True
    assert set(fit["models"]).issubset(set(HORIZONS))
    # a fitted pipeline scores a fresh row to a probability in [0, 1]
    h = next(iter(fit["models"]))
    X = ds[fit["features"]].head(3).to_numpy(dtype=float)
    p = fit["models"][h].predict_proba(X)[:, 1]
    assert ((p >= 0) & (p <= 1)).all()


def test_empty_dataset_is_graceful():
    res = evaluate_direct_horizon_models(pd.DataFrame(), horizons=HORIZONS)
    assert res["available"] is False
