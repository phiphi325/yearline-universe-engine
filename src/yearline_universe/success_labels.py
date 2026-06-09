"""V13.4 Phase 8 (RS-1) — retry-SUCCESS labels + empirical base-rate-by-bucket estimator.

Distinct from retry **occurrence** (`P(retry ≤ H)`, the mature calibrated/gated estimator in
`hazard.py`): this is retry **success** — *given an attempt at the yearline, will it reclaim and
**hold** (vs get rejected)?* The canonical label is the attempt's realised outcome
(`event_detection.classify_attempt_outcome_v10_parity` → "success" iff it confirms above MA250 for
``confirm_days`` then holds ≥70% over ``success_hold_days``). On the recovery table this surfaces as
``next_attempt_success`` with ``next_attempt_pending`` flagging unresolved (censored) attempts.

RS-1 delivers, **capability-before-consumer** (nothing surfaced in the envelope):
  * ``build_success_dataset`` — a leakage-safe, attempt-level success dataset (one row per
    *completed* recovery transition; pending attempts excluded), pooled across the universe.
  * ``build_empirical_success_reference`` / ``empirical_success_probability_for_row`` — the empirical
    "of similar historical attempts, what fraction succeeded?" estimator, mirroring the Phase-3 horizon
    estimator: a bucket scope-ladder (group/transition/drawdown → universe) with Bayesian shrinkage to
    the universe success prior. This is the **calibrated baseline** the RS-2 classifier must beat.

The binding constraint is sample: success is labelled per *attempt* (tens single-ticker, low hundreds
pooled), far scarcer than the per-day occurrence rows. So the floor is lower and shrinkage stronger.
Educational research only; not financial advice.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig

__all__ = [
    "SUCCESS_PROB_POLICY",
    "SUCCESS_MIN_REFERENCE_N",
    "SUCCESS_PRIOR_STRENGTH",
    "SUCCESS_STATE_FEATURES",
    "build_success_dataset",
    "build_empirical_success_reference",
    "empirical_success_probability_for_row",
]

SUCCESS_PROB_POLICY = "v13_phase8_empirical_attempt_success"
# Attempts are scarce (vs per-day rows), so the in-scope floor is lower than the horizon
# estimator's 25 and the shrinkage prior is comparatively strong.
SUCCESS_MIN_REFERENCE_N = 15
SUCCESS_PRIOR_STRENGTH = 6.0

# Static state describing the recovery into the attempt (used for buckets now; the RS-2
# classifier consumes these + the Phase-7 path/cross-sectional features later).
SUCCESS_STATE_FEATURES = [
    "drawdown_abs_low_pct", "below_ma250_abs_low_pct",
    "from_touch_day_overshoot", "from_fixed_5d_overshoot",
    "drawdown_atr_multiple", "gap_days", "trading_days_between", "from_attempt",
]


def _attempt_bucket(n: Any) -> str:
    try:
        n = int(n)
        return str(n) if n <= 2 else "3+"
    except Exception:
        return "unknown"


def _success_add_state_buckets(df: pd.DataFrame) -> pd.DataFrame:
    """Bucketize the recovery state (drawdown depth, below-MA250 depth, attempt #)."""
    out = df.copy()
    if out.empty:
        return out
    out["drawdown_bucket"] = pd.cut(
        pd.to_numeric(out.get("drawdown_abs_low_pct"), errors="coerce"),
        bins=[-0.1, 3, 5, 8, 12, 20, 10_000],
        labels=["000_003", "003_005", "005_008", "008_012", "012_020", "020_plus"],
        include_lowest=True).astype(str)
    out["below_ma250_bucket"] = pd.cut(
        pd.to_numeric(out.get("below_ma250_abs_low_pct"), errors="coerce"),
        bins=[-0.1, 5, 10, 15, 20, 10_000],
        labels=["000_005", "005_010", "010_015", "015_020", "020_plus"],
        include_lowest=True).astype(str)
    if "from_attempt" in out.columns:
        out["attempt_bucket"] = out["from_attempt"].map(_attempt_bucket)
    elif "attempt_bucket" not in out.columns:
        out["attempt_bucket"] = "unknown"
    for c in ("group", "transition"):
        if c not in out.columns:
            out[c] = "unknown"
        out[c] = out[c].fillna("unknown").astype(str)
    return out


def build_success_dataset(tickers_data: Mapping[str, Mapping[str, Any]],
                          config: StudyConfig | None = None) -> pd.DataFrame:
    """Leakage-safe, attempt-level success dataset pooled across the universe.

    ``tickers_data[ticker]`` = {peer_group, price_df, recovery_table, live_diagnostic} (the universe
    runner's pooled_data). One row per **completed** recovery transition (``next_attempt_pending`` is
    excluded — leakage-safe censoring); ``y_success`` = 1 iff the next attempt reclaimed and held.
    """
    config = config or StudyConfig()
    parts = []
    for tk, d in (tickers_data or {}).items():
        rec = d.get("recovery_table")
        if rec is None or getattr(rec, "empty", True):
            continue
        if "next_attempt_success" not in rec.columns:
            continue
        r = rec.copy()
        if "next_attempt_pending" in r.columns:
            r = r[~r["next_attempt_pending"].astype(bool)]      # drop censored/unresolved
        r = r[r["next_attempt_success"].notna()]
        if r.empty:
            continue
        r["ticker"] = tk
        r["group"] = d.get("peer_group", "unknown")
        r["y_success"] = r["next_attempt_success"].astype(bool).astype(int)
        rnd = r["round"].astype(str) if "round" in r.columns else "NA"
        trans = r["transition"].astype(str) if "transition" in r.columns else "NA"
        to_d = pd.to_datetime(r["to_date"], errors="coerce").astype(str) if "to_date" in r.columns else "NA"
        r["episode_key"] = r["ticker"].astype(str) + "|" + rnd
        r["transition_key"] = r["episode_key"] + "|" + trans + "|" + to_d
        parts.append(r)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    keep = (["ticker", "group", "round", "transition", "from_attempt", "to_attempt", "to_date",
             "episode_key", "transition_key", "y_success"]
            + [c for c in SUCCESS_STATE_FEATURES if c in df.columns])
    keep = [c for c in dict.fromkeys(keep) if c in df.columns]
    return _success_add_state_buckets(df[keep]).reset_index(drop=True)


def build_empirical_success_reference(dataset: pd.DataFrame) -> pd.DataFrame:
    """The completed-attempt rows *are* the reference; ensure state buckets are present."""
    if dataset is None or dataset.empty:
        return pd.DataFrame()
    return dataset if "drawdown_bucket" in dataset.columns else _success_add_state_buckets(dataset.copy())


_SUCCESS_SCOPE_LADDER = [
    ("group_transition_drawdown", ["group", "transition", "drawdown_bucket"]),
    ("group_transition", ["group", "transition"]),
    ("transition_drawdown", ["transition", "drawdown_bucket"]),
    ("transition", ["transition"]),
    ("group_drawdown", ["group", "drawdown_bucket"]),
    ("group", ["group"]),
    ("drawdown", ["drawdown_bucket"]),
    ("universe_all", []),
]


def empirical_success_probability_for_row(
    row_like, reference: pd.DataFrame, exclude_transition_key=None,
    prior_strength: float = SUCCESS_PRIOR_STRENGTH,
    min_reference_n: int = SUCCESS_MIN_REFERENCE_N,
) -> dict[str, Any]:
    """Empirical P(success) for an attempt from similar completed attempts.

    Borrows strength via the scope ladder (first scope with ≥ ``min_reference_n`` rows wins) and
    shrinks the in-scope success rate toward the universe rate (Beta-style prior, strength
    ``prior_strength``). Pass ``exclude_transition_key`` for a leave-one-attempt-out estimate.
    """
    if reference is None or reference.empty:
        return {"success_probability": np.nan, "reference_n": 0, "reference_success_n": 0,
                "reference_scope": "no_reference_rows", "universe_prior_rate": np.nan,
                "estimator": SUCCESS_PROB_POLICY}
    ref = reference if "drawdown_bucket" in reference.columns else _success_add_state_buckets(reference.copy())
    if exclude_transition_key is not None and "transition_key" in ref.columns:
        ref = ref[ref["transition_key"].astype(str) != str(exclude_transition_key)]
    row = _success_add_state_buckets(pd.DataFrame([dict(row_like)])).iloc[0]

    universe = ref["y_success"].astype(int)
    prior_rate = float(universe.mean()) if len(universe) else np.nan

    sample, scope_used = ref, "universe_all"
    for scope, cols in _SUCCESS_SCOPE_LADDER:
        mask = pd.Series(True, index=ref.index)
        for c in cols:
            if c not in ref.columns:
                mask &= False
            else:
                mask &= ref[c].astype(str).fillna("unknown").eq(str(row.get(c, "unknown")))
        s = ref[mask]
        if len(s) >= min_reference_n or scope == "universe_all":
            sample, scope_used = s, scope
            break

    if sample.empty:
        p, n, k = prior_rate, 0, np.nan
    else:
        y = sample["y_success"].astype(int)
        n, k = int(len(y)), int(y.sum())
        if pd.isna(prior_rate):
            p = float(y.mean()) if n else np.nan
        else:
            p = float((k + prior_strength * prior_rate) / (n + prior_strength))
    return {
        "success_probability": p,
        "reference_n": n,
        "reference_success_n": (None if pd.isna(k) else int(k)),
        "reference_scope": scope_used,
        "universe_prior_rate": prior_rate,
        "estimator": SUCCESS_PROB_POLICY,
        "smoothing_prior_strength": prior_strength,
    }
