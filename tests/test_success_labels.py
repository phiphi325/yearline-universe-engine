"""V13.4 Phase 8 (RS-1) — retry-success labels + empirical success estimator."""
import numpy as np
import pandas as pd

from yearline_universe.success_labels import (
    build_success_dataset, build_empirical_success_reference,
    empirical_success_probability_for_row, SUCCESS_PROB_POLICY,
)


def _recovery_table(specs):
    """specs: list of (from_attempt, drawdown, outcome) where outcome in {'success','fail','pending'}."""
    rows = []
    for i, (att, dd, outcome) in enumerate(specs):
        rows.append({
            "round": 1, "transition": f"{att}_to_{att + 1}", "from_attempt": att, "to_attempt": att + 1,
            "to_date": str((pd.Timestamp("2015-01-01") + pd.Timedelta(days=i * 7)).date()),
            "drawdown_abs_low_pct": float(dd), "below_ma250_abs_low_pct": float(dd) * 1.1,
            "from_touch_day_overshoot": 0.5, "from_fixed_5d_overshoot": 0.4,
            "drawdown_atr_multiple": 2.0, "gap_days": 30, "trading_days_between": 20,
            "next_attempt_success": outcome == "success",
            "next_attempt_pending": outcome == "pending",
        })
    return pd.DataFrame(rows)


def _reference(n_low_succ, n_low_fail, n_high_succ, n_high_fail, low_dd=4.0, high_dd=15.0):
    """A reference where LOW-drawdown attempts succeed more than HIGH-drawdown ones."""
    specs = ([(2, low_dd, "success")] * n_low_succ + [(2, low_dd, "fail")] * n_low_fail
             + [(2, high_dd, "success")] * n_high_succ + [(2, high_dd, "fail")] * n_high_fail)
    td = {"AAA": {"peer_group": "peerX", "recovery_table": _recovery_table(specs)}}
    return build_success_dataset(td)


def test_build_success_dataset_labels_and_censoring():
    td = {"AAA": {"peer_group": "peerX", "recovery_table": _recovery_table([
        (1, 4.0, "success"), (2, 8.0, "fail"), (2, 12.0, "pending"), (3, 6.0, "success"),
    ])}}
    ds = build_success_dataset(td)
    # pending row excluded; 3 completed rows remain
    assert len(ds) == 3
    assert set(ds["y_success"].unique()).issubset({0, 1})
    assert ds["y_success"].sum() == 2                       # two successes
    # required columns + buckets + keys present
    for c in ("ticker", "group", "transition_key", "episode_key", "y_success",
              "drawdown_bucket", "below_ma250_bucket", "attempt_bucket"):
        assert c in ds.columns, c
    assert ds["group"].eq("peerX").all()
    assert ds["transition_key"].is_unique


def test_empirical_success_probability_signal_and_bounds():
    ref = _reference(n_low_succ=18, n_low_fail=2, n_high_succ=3, n_high_fail=17)
    base = ref["y_success"].mean()                          # ~0.525
    p_low = empirical_success_probability_for_row(
        {"group": "peerX", "transition": "2_to_3", "drawdown_abs_low_pct": 4.0,
         "below_ma250_abs_low_pct": 4.4, "from_attempt": 2}, ref)
    p_high = empirical_success_probability_for_row(
        {"group": "peerX", "transition": "2_to_3", "drawdown_abs_low_pct": 15.0,
         "below_ma250_abs_low_pct": 16.5, "from_attempt": 2}, ref)
    # bounds + provenance
    for r in (p_low, p_high):
        assert 0.0 <= r["success_probability"] <= 1.0
        assert r["estimator"] == SUCCESS_PROB_POLICY
        assert r["reference_n"] > 0 and "reference_scope" in r and r["universe_prior_rate"] is not None
    # signal: shallow-drawdown attempts rank above deep-drawdown ones, straddling the base rate
    assert p_low["success_probability"] > base > p_high["success_probability"]


def test_shrinkage_pulls_small_buckets_toward_universe():
    """A bucket with too few rows falls through the ladder and is shrunk toward the universe rate —
    so a 100%-success but tiny bucket does NOT yield p≈1.0."""
    ref = _reference(n_low_succ=18, n_low_fail=2, n_high_succ=3, n_high_fail=17)
    # add a tiny all-success bucket at dd≈25 (020_plus): only 2 rows, far below the floor
    tiny = build_success_dataset({"AAA": {"peer_group": "peerX",
                                          "recovery_table": _recovery_table([(2, 25.0, "success")] * 2)}})
    ref2 = pd.concat([ref, tiny], ignore_index=True)
    r = empirical_success_probability_for_row(
        {"group": "peerX", "transition": "2_to_3", "drawdown_abs_low_pct": 25.0,
         "below_ma250_abs_low_pct": 27.5, "from_attempt": 2}, ref2)
    # not pinned at the raw 1.0 of the 2-row bucket — borrowed strength from the wider scope
    assert r["success_probability"] < 0.8
    assert r["reference_n"] >= 15                            # used a broader scope, not the 2-row bucket


def test_exclude_transition_key_and_empty_graceful():
    ref = _reference(10, 10, 10, 10)
    key = ref["transition_key"].iloc[0]
    full = empirical_success_probability_for_row(
        {"group": "peerX", "transition": "2_to_3", "drawdown_abs_low_pct": 4.0,
         "below_ma250_abs_low_pct": 4.4, "from_attempt": 2}, ref)
    excl = empirical_success_probability_for_row(
        {"group": "peerX", "transition": "2_to_3", "drawdown_abs_low_pct": 4.0,
         "below_ma250_abs_low_pct": 4.4, "from_attempt": 2}, ref, exclude_transition_key=key)
    assert 0.0 <= excl["success_probability"] <= 1.0
    # the key is unique (real attempts have distinct touch dates) and excluding that in-scope
    # row drops the scope's reference count by exactly one
    assert (ref["transition_key"] == key).sum() == 1
    assert full["reference_n"] - excl["reference_n"] == 1
    # empty inputs are graceful
    assert build_success_dataset({}).empty
    none = empirical_success_probability_for_row({"drawdown_abs_low_pct": 5.0}, pd.DataFrame())
    assert np.isnan(none["success_probability"]) and none["reference_scope"] == "no_reference_rows"
    assert build_empirical_success_reference(pd.DataFrame()).empty
