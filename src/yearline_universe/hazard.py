"""Retry-timing / quality ML models + discrete-time survival hazard.

Faithful port of V12 Module C (statistical-learning MVP) and Module D
(survival / hazard MVP). De-globalised and de-MSFT'd:

* ``live_ticker="MSFT"`` defaults become a required ``live_ticker`` argument.
* The pooled ``all_results`` / ``TICKER_METADATA`` globals become an explicit
  ``tickers_data`` mapping. For a single-ticker V13.1 run this mapping holds
  one entry, so the hazard / ML models fit on that ticker's own history and the
  context is honestly flagged as a low-sample prototype. The V13.2 universe
  runner can pass the whole universe to pool the fit.

No execution semantics — research / evidence only.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import add_indicators

try:  # sklearn is required for the models; degrade gracefully if missing.
    from sklearn.linear_model import HuberRegressor, LinearRegression, LogisticRegression
    from sklearn.model_selection import LeaveOneGroupOut
    from sklearn.metrics import mean_absolute_error, brier_score_loss, log_loss
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

__all__ = [
    "RANDOM_SEED",
    "HAZARD_HORIZONS",
    "build_retry_transition_dataset",
    "fit_retry_timing_model",
    "fit_retry_quality_classifier",
    "build_hazard_daily_panel",
    "fit_retry_hazard_model",
    "score_future_hazard_curve",
    "build_empirical_horizon_reference",
    "empirical_horizon_probabilities_for_row",
    "run_hazard_layer",
    "HORIZON_PROB_POLICY",
]

RANDOM_SEED = 42
HAZARD_MAX_FUTURE_DAYS = 90
HAZARD_HORIZONS = [10, 20, 40, 60, 90]

# --- V13.3 Phase 3: empirical completed-path horizon policy (ports V12.4.1) ---
# The canonical P(retry <= H) is no longer the logistic state-hold-forward curve
# (a saturating step; see docs/.../V13_data_and_report_analysis.md §2.2). It is an
# EMPIRICAL estimate: among completed historical at-risk days in *similar* states,
# how often did the next retry occur within H trading days. The logistic model is
# retained only for ``hazard_today``; its forward curve is kept as a diagnostic.
HORIZON_PROB_POLICY = "v13_empirical_horizon_calibrated"          # == V12.4.1 policy
HORIZON_MIN_REFERENCE_N = 25
HORIZON_PRIOR_STRENGTH = 8.0

TIMING_FEATURES_NUM = [
    "drawdown_so_far_pct", "below_ma250_depth_so_far_pct", "attempt_no",
    "from_touch_day_overshoot_pct", "from_fixed_5d_overshoot_pct",
]
TIMING_FEATURES_CAT = ["transition", "group", "from_canonical_quality"]
QUALITY_FEATURES_NUM = list(TIMING_FEATURES_NUM)
QUALITY_FEATURES_CAT = list(TIMING_FEATURES_CAT)

HAZARD_NUM_FEATURES = [
    "trading_days_since_touch", "calendar_days_since_touch", "drawdown_so_far_pct",
    "below_ma250_depth_so_far_pct", "distance_to_ma250_pct", "required_rebound_to_ma250_pct",
    "from_touch_day_overshoot_pct", "from_fixed_5d_overshoot_pct", "attempt_no",
]
HAZARD_CAT_FEATURES = ["transition", "group", "from_canonical_quality"]


# ---------------------------------------------------------------------------
# Small helpers (ported)
# ---------------------------------------------------------------------------

def _required_rebound_from_distance(distance_pct: Any) -> float:
    try:
        d = float(distance_pct) / 100.0
        if d >= 0:
            return 0.0
        return (1.0 / (1.0 + d) - 1.0) * 100.0
    except Exception:
        return np.nan


def _attempt_bucket(attempt_no: Any) -> str | None:
    try:
        attempt_no = int(attempt_no)
        return str(attempt_no) if attempt_no <= 2 else "3+"
    except Exception:
        return None


def _normalize_dt(x):
    return pd.Timestamp(x).normalize()


def _loc_for_date(index: pd.Index, dt) -> int | None:
    if dt is None or pd.isna(dt):
        return None
    target = _normalize_dt(dt)
    idx_norm = pd.to_datetime(index).normalize()
    matches = np.flatnonzero(idx_norm == target)
    if len(matches):
        return int(matches[0])
    pos = idx_norm.searchsorted(target)
    if 0 <= pos < len(idx_norm):
        return int(pos)
    return None


def _safe_get(row, names, default=np.nan):
    for n in names:
        try:
            if n in row.index and not pd.isna(row[n]):
                return row[n]
        except Exception:
            pass
    return default


# ---------------------------------------------------------------------------
# Module C — ML-ready transition dataset + timing / quality models
# ---------------------------------------------------------------------------

def build_retry_transition_dataset(
    ticker: str,
    peer_group: str,
    recovery_table: pd.DataFrame,
    live_diagnostic: Mapping[str, Any] | None,
) -> pd.DataFrame:
    """Build the ML-ready transition dataset for one ticker.

    Historical completed rows come from the inter-attempt recovery table; one
    live censored row is derived from the live diagnostic.
    """
    rows: list[dict[str, Any]] = []
    if recovery_table is not None and not recovery_table.empty:
        for _, r in recovery_table.iterrows():
            attempt_no = r.get("from_attempt")
            rows.append({
                "ticker": ticker,
                "group": peer_group,
                "round": r.get("round"),
                "transition": r.get("transition"),
                "from_attempt": attempt_no,
                "to_attempt": r.get("to_attempt"),
                "attempt_no": attempt_no,
                "attempt_bucket": _attempt_bucket(attempt_no),
                "from_date": r.get("from_date"),
                "to_date": r.get("to_date"),
                "as_of_date": r.get("to_date"),
                "is_completed": True,
                "is_censored": False,
                "gap_days": pd.to_numeric(r.get("gap_days"), errors="coerce"),
                "days_since_last_touch": pd.to_numeric(r.get("gap_days"), errors="coerce"),
                "drawdown_so_far_pct": pd.to_numeric(r.get("drawdown_abs_low_pct"), errors="coerce"),
                "below_ma250_depth_so_far_pct": pd.to_numeric(r.get("below_ma250_abs_low_pct"), errors="coerce"),
                "distance_to_ma250_pct": np.nan,
                "required_rebound_to_ma250_pct": np.nan,
                "from_canonical_quality": "unknown",
                "from_touch_day_overshoot_pct": pd.to_numeric(r.get("from_touch_day_overshoot"), errors="coerce"),
                "from_fixed_5d_overshoot_pct": pd.to_numeric(r.get("from_fixed_5d_overshoot"), errors="coerce"),
                "next_attempt_success": (np.nan if pd.isna(r.get("next_attempt_success")) else bool(r.get("next_attempt_success"))),
                "scenario_name": "historical_completed_transition",
            })

    live = dict(live_diagnostic or {})
    latest_attempt = live.get("latest_attempt_no")
    if latest_attempt is not None and not pd.isna(latest_attempt):
        dist = live.get("current_distance_to_ma250_pct")
        rows.append({
            "ticker": ticker,
            "group": peer_group,
            "round": live.get("latest_round"),
            "transition": f"{int(latest_attempt)}_to_{int(latest_attempt) + 1}",
            "from_attempt": latest_attempt,
            "to_attempt": int(latest_attempt) + 1,
            "attempt_no": latest_attempt,
            "attempt_bucket": _attempt_bucket(latest_attempt),
            "from_date": live.get("latest_touch_date"),
            "to_date": None,
            "as_of_date": live.get("as_of"),
            "is_completed": False,
            "is_censored": True,
            "gap_days": np.nan,
            "days_since_last_touch": live.get("days_since_last_touch"),
            "drawdown_so_far_pct": abs(float(live.get("current_drawdown_since_last_touch_low_pct"))) if live.get("current_drawdown_since_last_touch_low_pct") is not None else np.nan,
            "below_ma250_depth_so_far_pct": abs(float(live.get("current_below_ma250_depth_low_pct"))) if live.get("current_below_ma250_depth_low_pct") is not None else np.nan,
            "distance_to_ma250_pct": dist,
            "required_rebound_to_ma250_pct": _required_rebound_from_distance(dist),
            "from_canonical_quality": live.get("latest_quality", "unknown"),
            "from_touch_day_overshoot_pct": np.nan,
            "from_fixed_5d_overshoot_pct": np.nan,
            "next_attempt_success": np.nan,
            "scenario_name": "live_current_state_is_max",
        })

    df = pd.DataFrame(rows)
    for c in ["gap_days", "days_since_last_touch", "drawdown_so_far_pct",
              "below_ma250_depth_so_far_pct", "distance_to_ma250_pct",
              "required_rebound_to_ma250_pct", "attempt_no"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _prepare_design_matrix(train_df, live_df, num_cols, cat_cols):
    train = train_df.copy()
    live = live_df.copy()
    train["__is_live"] = False
    live["__is_live"] = True
    combo = pd.concat([train, live], ignore_index=True)
    for c in num_cols:
        if c not in combo.columns:
            combo[c] = np.nan
        combo[c] = pd.to_numeric(combo[c], errors="coerce")
        med = combo.loc[~combo["__is_live"], c].median()
        combo[c] = combo[c].fillna(0 if pd.isna(med) else med)
    for c in cat_cols:
        if c not in combo.columns:
            combo[c] = "unknown"
        combo[c] = combo[c].fillna("unknown").astype(str)
    X_all = pd.get_dummies(combo[num_cols + cat_cols], columns=cat_cols, dummy_na=False)
    X_train = X_all.loc[~combo["__is_live"]].copy()
    X_live = X_all.loc[combo["__is_live"]].copy()
    return X_train, X_live, list(X_all.columns)


def fit_retry_timing_model(dataset: pd.DataFrame, live_ticker: str):
    """Robust regression for the conditional retry gap. Ported from V12."""
    if not _SKLEARN:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"status": "sklearn_unavailable"}
    completed = dataset[(dataset["is_completed"] == True) & dataset["gap_days"].notna()].copy()
    live = dataset[(dataset["is_censored"] == True) & (dataset["ticker"].astype(str) == live_ticker)].copy()
    if completed.empty or live.empty or len(completed) < 20:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"status": "insufficient_data", "n_completed": int(len(completed))}
    X_train, X_live, feature_names = _prepare_design_matrix(completed, live, TIMING_FEATURES_NUM, TIMING_FEATURES_CAT)
    y = completed["gap_days"].astype(float).to_numpy()
    try:
        model = HuberRegressor().fit(X_train, y)
        model_name = "HuberRegressor"
    except Exception as e:
        model = LinearRegression().fit(X_train, y)
        model_name = f"LinearRegression_fallback_after_{type(e).__name__}"
    coef = getattr(model, "coef_", np.zeros(X_train.shape[1]))
    pred_live = float(model.predict(X_live)[0])

    rng = np.random.default_rng(RANDOM_SEED)
    preds = []
    for _ in range(300):
        idx = rng.integers(0, len(completed), len(completed))
        sample = completed.iloc[idx].copy()
        if sample["gap_days"].nunique() < 2:
            continue
        try:
            Xb, Xl, _ = _prepare_design_matrix(sample, live, TIMING_FEATURES_NUM, TIMING_FEATURES_CAT)
            yb = sample["gap_days"].astype(float).to_numpy()
            mb = HuberRegressor().fit(Xb, yb)
            preds.append(float(mb.predict(Xl)[0]))
        except Exception:
            continue
    if preds:
        p10, p25, p50, p75, p90 = np.percentile(preds, [10, 25, 50, 75, 90])
    else:
        p10 = p25 = p50 = p75 = p90 = np.nan

    live_row = live.iloc[0].to_dict()
    elapsed = float(live_row.get("days_since_last_touch") or 0)
    as_of = pd.to_datetime(live_row.get("as_of_date"), errors="coerce")
    rough_p50_date = None
    if pd.notna(as_of) and pd.notna(p50):
        rough_p50_date = str((as_of + pd.Timedelta(days=int(max(0, math.ceil(p50 - elapsed))))).date())

    pred_df = pd.DataFrame([{
        "ticker": live_row.get("ticker"), "target_transition": live_row.get("transition"),
        "as_of_date": live_row.get("as_of_date"), "model_name": model_name, "n_train": len(completed),
        "predicted_gap_point": pred_live, "predicted_gap_p10": p10, "predicted_gap_p25": p25,
        "predicted_gap_p50": p50, "predicted_gap_p75": p75, "predicted_gap_p90": p90,
        "elapsed_days": elapsed,
        "predicted_remaining_days_p50": max(0, p50 - elapsed) if pd.notna(p50) else np.nan,
        "rough_retry_date_p50": rough_p50_date, "model_quality": "prototype_interpretable",
        "model_warning": "Small event sample; use as context, not as forecast.",
    }])
    coef_df = pd.DataFrame({"feature": feature_names, "coefficient": coef}).sort_values("coefficient", key=lambda s: s.abs(), ascending=False)
    return pred_df, coef_df, completed, {"status": "ok", "model_name": model_name, "n_train": len(completed)}


def fit_retry_quality_classifier(dataset: pd.DataFrame, live_ticker: str):
    """Logistic next-attempt-success classifier. Ported from V12."""
    if not _SKLEARN:
        return pd.DataFrame([{"ticker": live_ticker, "status": "sklearn_unavailable"}]), pd.DataFrame(), pd.DataFrame()
    train = dataset[(dataset["is_completed"] == True) & dataset["next_attempt_success"].notna()].copy()
    live = dataset[(dataset["is_censored"] == True) & (dataset["ticker"].astype(str) == live_ticker)].copy()
    if not train.empty:
        train["y"] = train["next_attempt_success"].astype(bool).astype(int)
    if train.empty or live.empty or len(train) < 30 or train["y"].nunique() < 2:
        out = pd.DataFrame([{
            "ticker": live_ticker, "model_name": "LogisticRegression",
            "status": "suppressed_insufficient_labels", "p_next_retry_success": np.nan,
            "quality_bucket": "unknown", "n_train": int(len(train)),
            "model_warning": "Not enough labeled events or only one class available.",
        }])
        return out, pd.DataFrame(), train
    X_train, X_live, feature_names = _prepare_design_matrix(train, live, QUALITY_FEATURES_NUM, QUALITY_FEATURES_CAT)
    y = train["y"].to_numpy()
    clf = LogisticRegression(max_iter=2000, C=0.75)
    clf.fit(X_train, y)
    p_live = float(clf.predict_proba(X_live)[0, 1])
    if p_live < 0.25:
        bucket = "low"
    elif p_live < 0.45:
        bucket = "low_to_medium"
    elif p_live < 0.60:
        bucket = "medium"
    else:
        bucket = "high_relative_to_sample"
    live_row = live.iloc[0].to_dict()
    out = pd.DataFrame([{
        "ticker": live_row.get("ticker"), "target_transition": live_row.get("transition"),
        "as_of_date": live_row.get("as_of_date"), "model_name": "LogisticRegression_L2",
        "n_train": len(train), "base_rate_train": float(train["y"].mean()),
        "p_next_retry_success": p_live, "quality_bucket": bucket,
        "model_quality": "prototype_uncalibrated",
        "model_warning": "Probability is uncalibrated; validate before production use.",
    }])
    coef_df = pd.DataFrame({"feature": feature_names, "coefficient": clf.coef_[0]}).sort_values("coefficient", key=lambda s: s.abs(), ascending=False)
    return out, coef_df, train


# ---------------------------------------------------------------------------
# Module D — discrete-time survival hazard
# ---------------------------------------------------------------------------

def _make_daily_rows_for_transition(
    *, ticker, group, price_df, transition_row, completed, live=False, as_of_date=None, config=None, ind_df=None,
) -> list[dict]:
    config = config or StudyConfig()
    if price_df is None or price_df.empty:
        return []
    # Reuse a precomputed indicator frame when provided (avoids recomputing
    # rolling MAs/ATR per transition — a meaningful saving for many transitions).
    df = (ind_df if ind_df is not None else add_indicators(price_df, config)).copy()
    from_date = _safe_get(transition_row, ["from_date", "latest_touch_date"])
    to_date = _safe_get(transition_row, ["to_date"], default=np.nan)
    from_loc = _loc_for_date(df.index, from_date)
    if from_loc is None:
        return []
    if completed:
        to_loc = _loc_for_date(df.index, to_date)
        if to_loc is None or to_loc <= from_loc:
            return []
    else:
        to_loc = _loc_for_date(df.index, as_of_date if as_of_date is not None else df.index[-1])
        if to_loc is None or to_loc <= from_loc:
            return []

    entry_close = float(df["Close"].iloc[from_loc])
    raw_attempt = _safe_get(transition_row, ["from_attempt", "attempt_no", "latest_attempt_no"], default=np.nan)
    attempt_no = int(raw_attempt) if not pd.isna(raw_attempt) else np.nan
    raw_to = _safe_get(transition_row, ["to_attempt"], default=(attempt_no + 1 if not pd.isna(attempt_no) else np.nan))
    to_attempt = int(raw_to) if not pd.isna(raw_to) else np.nan
    transition = str(_safe_get(transition_row, ["transition"], default=f"{attempt_no}_to_{to_attempt}"))
    source_quality = str(_safe_get(transition_row, ["from_canonical_quality", "from_quality", "latest_quality"], default="unknown"))
    touch_overshoot = _safe_get(transition_row, ["from_touch_day_overshoot_pct", "from_touch_day_overshoot", "from_overshoot"], default=np.nan)
    fixed5 = _safe_get(transition_row, ["from_fixed_5d_overshoot_pct", "from_fixed_5d_overshoot"], default=np.nan)

    rows = []
    for loc in range(from_loc + 1, to_loc + 1):
        sub = df.iloc[from_loc:loc + 1]
        cur = df.iloc[loc]
        cur_date = pd.Timestamp(df.index[loc])
        low_min = float(sub["Low"].min())
        drawdown_so_far = abs((low_min / entry_close - 1.0) * 100.0)
        below_series = ((sub["Low"] / sub["MA250"]) - 1.0) * 100.0
        below_min = float(below_series.min()) if below_series.notna().any() else np.nan
        below_depth_abs = abs(below_min) if not pd.isna(below_min) and below_min < 0 else 0.0
        distance = float(cur.get("distance_to_ma250_pct", np.nan))
        event_today = int(completed and loc == to_loc)
        rows.append({
            "ticker": ticker, "group": group, "round": _safe_get(transition_row, ["round"], default=np.nan),
            "transition": transition, "from_attempt": attempt_no, "to_attempt": to_attempt,
            "from_date": pd.Timestamp(from_date).date() if not pd.isna(from_date) else None,
            "as_of_date": cur_date.date(),
            "to_date": pd.Timestamp(to_date).date() if completed and not pd.isna(to_date) else None,
            "trading_days_since_touch": int(loc - from_loc),
            "calendar_days_since_touch": int((_normalize_dt(cur_date) - _normalize_dt(from_date)).days),
            "event_retry_today": event_today,
            "is_completed_transition": bool(completed), "is_censored_transition": bool(not completed),
            "is_live_transition": bool(live),
            "current_close": float(cur["Close"]),
            "current_ma250": float(cur["MA250"]) if not pd.isna(cur.get("MA250", np.nan)) else np.nan,
            "distance_to_ma250_pct": distance,
            "required_rebound_to_ma250_pct": _required_rebound_from_distance(distance),
            "drawdown_so_far_pct": float(drawdown_so_far), "below_ma250_depth_so_far_pct": float(below_depth_abs),
            "from_canonical_quality": source_quality,
            "from_touch_day_overshoot_pct": float(touch_overshoot) if not pd.isna(touch_overshoot) else np.nan,
            "from_fixed_5d_overshoot_pct": float(fixed5) if not pd.isna(fixed5) else np.nan,
            "attempt_no": attempt_no,
            "attempt_bucket": str(int(attempt_no)) if not pd.isna(attempt_no) and int(attempt_no) <= 2 else "3+",
        })
    return rows


def build_hazard_daily_panel(tickers_data: Mapping[str, Mapping[str, Any]], live_ticker: str, config: StudyConfig | None = None) -> pd.DataFrame:
    """Build daily at-risk rows from one or many tickers.

    ``tickers_data[ticker]`` = {peer_group, price_df, recovery_table, live_diagnostic}.
    Single-ticker (V13.1) passes one entry; the universe runner can pool many.
    """
    config = config or StudyConfig()
    rows: list[dict] = []
    ind_cache: dict[str, pd.DataFrame] = {}
    for ticker, data in tickers_data.items():
        rec = data.get("recovery_table")
        price_df = data.get("price_df")
        group = data.get("peer_group", "unknown")
        if rec is None or rec.empty or price_df is None:
            continue
        ind = ind_cache.get(ticker)
        if ind is None:
            ind = add_indicators(price_df, config)
            ind_cache[ticker] = ind
        for _, tr in rec.iterrows():
            rows.extend(_make_daily_rows_for_transition(
                ticker=ticker, group=group, price_df=price_df, transition_row=tr,
                completed=True, live=False, config=config, ind_df=ind,
            ))

    data = tickers_data.get(live_ticker)
    if data is not None:
        live = dict(data.get("live_diagnostic") or {})
        if live.get("latest_touch_date") is not None and live.get("latest_attempt_no") is not None:
            from_attempt = live.get("latest_attempt_no")
            to_attempt = int(from_attempt) + 1 if from_attempt is not None and not pd.isna(from_attempt) else np.nan
            live_row = pd.Series({
                "round": live.get("latest_round"), "transition": f"{from_attempt}_to_{to_attempt}",
                "from_attempt": from_attempt, "to_attempt": to_attempt,
                "from_date": live.get("latest_touch_date"), "latest_touch_date": live.get("latest_touch_date"),
                "latest_attempt_no": live.get("latest_attempt_no"),
                "from_canonical_quality": live.get("latest_quality"),
            })
            live_price = data.get("price_df")
            live_ind = ind_cache.get(live_ticker)
            if live_ind is None and live_price is not None:
                live_ind = add_indicators(live_price, config)
            rows.extend(_make_daily_rows_for_transition(
                ticker=live_ticker, group=data.get("peer_group", "unknown"),
                price_df=live_price, transition_row=live_row,
                completed=False, live=True, as_of_date=live.get("as_of"), config=config, ind_df=live_ind,
            ))
    panel = pd.DataFrame(rows)
    for c in HAZARD_NUM_FEATURES:
        if c in panel.columns:
            panel[c] = pd.to_numeric(panel[c], errors="coerce")
    return panel


def prepare_hazard_design(train_df, score_df):
    train = train_df.copy(); score = score_df.copy()
    train["__score"] = False; score["__score"] = True
    combo = pd.concat([train, score], ignore_index=True)
    for c in HAZARD_NUM_FEATURES:
        if c not in combo.columns:
            combo[c] = np.nan
        combo[c] = pd.to_numeric(combo[c], errors="coerce")
        med = combo.loc[combo["__score"] == False, c].median()
        combo[c] = combo[c].fillna(0.0 if pd.isna(med) else med)
    for c in HAZARD_CAT_FEATURES:
        if c not in combo.columns:
            combo[c] = "unknown"
        combo[c] = combo[c].fillna("unknown").astype(str)
    X_all = pd.get_dummies(combo[HAZARD_NUM_FEATURES + HAZARD_CAT_FEATURES], columns=HAZARD_CAT_FEATURES, dummy_na=False)
    X_train = X_all.loc[combo["__score"] == False].reset_index(drop=True)
    X_score = X_all.loc[combo["__score"] == True].reset_index(drop=True)
    return X_train, X_score, list(X_all.columns)


def fit_retry_hazard_model(panel: pd.DataFrame):
    """Fit a discrete-time logistic hazard. Returns (model, card) or (None, card)."""
    if not _SKLEARN:
        return None, {"status": "sklearn_unavailable"}
    train = panel[panel["is_live_transition"] == False].copy()
    train = train[train["event_retry_today"].notna()].copy()
    if train.empty:
        return None, {"status": "no_training_rows"}
    train["y"] = train["event_retry_today"].astype(int)
    if len(train) < 100 or train["y"].nunique() < 2:
        return None, {"status": "insufficient_hazard_training_data", "n_train": int(len(train)), "n_events": int(train["y"].sum())}
    model = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=RANDOM_SEED)
    # Fit on the design built against itself (no score rows needed for fit).
    X_train, _, feature_names = prepare_hazard_design(train, train.head(1))
    model.fit(X_train, train["y"].to_numpy())
    card = {
        "status": "ok", "model_name": "DiscreteTimeLogisticHazard",
        "model_version": "v13_discrete_time_logistic_hazard",
        "n_train_daily_rows": int(len(train)), "n_event_rows": int(train["y"].sum()),
        "event_row_rate": float(train["y"].mean()),
        "feature_names": feature_names,
        "model_quality": "prototype_uncalibrated",
        "model_warning": "Discrete-time hazard MVP; probabilities are not production-calibrated.",
    }
    return (model, feature_names, train), card


def score_future_hazard_curve(fit_obj, panel: pd.DataFrame, horizons=None, max_future_days: int = HAZARD_MAX_FUTURE_DAYS):
    """Score the live ticker's cumulative retry probability over future horizons."""
    horizons = horizons or HAZARD_HORIZONS
    if fit_obj is None:
        return pd.DataFrame(), pd.DataFrame(), {"available": False, "warning": "Hazard model not fitted (low sample)."}
    model, feature_names, train = fit_obj
    live_rows = panel[panel["is_live_transition"] == True].copy()
    if live_rows.empty:
        return pd.DataFrame(), pd.DataFrame(), {"available": False, "warning": "No live transition rows to score."}
    base = live_rows.sort_values("as_of_date").tail(1).iloc[0].copy()
    as_of = pd.Timestamp(base["as_of_date"])
    cur_td = int(base.get("trading_days_since_touch", 0))
    cur_cd = int(base.get("calendar_days_since_touch", cur_td))

    future_rows = []
    for h in range(1, max_future_days + 1):
        r = base.copy()
        r["future_horizon_day"] = h
        r["as_of_date"] = (as_of + pd.Timedelta(days=h)).date()
        r["trading_days_since_touch"] = cur_td + h
        r["calendar_days_since_touch"] = cur_cd + h
        future_rows.append(r.to_dict())
    fut = pd.DataFrame(future_rows)

    _, X_future, _ = prepare_hazard_design(train, fut)
    # Align columns to training feature space.
    X_future = X_future.reindex(columns=feature_names, fill_value=0)
    hazard = model.predict_proba(X_future)[:, 1]

    curve = fut[["future_horizon_day", "as_of_date", "ticker", "transition",
                 "trading_days_since_touch", "drawdown_so_far_pct",
                 "distance_to_ma250_pct", "required_rebound_to_ma250_pct"]].copy()
    curve["hazard_probability"] = hazard
    curve["survival_probability_from_as_of"] = np.cumprod(1.0 - curve["hazard_probability"].clip(0, 1))
    curve["cumulative_retry_probability_from_as_of"] = 1.0 - curve["survival_probability_from_as_of"]

    horizon_rows = []
    for h in horizons:
        row = curve[curve["future_horizon_day"] <= h].tail(1)
        if row.empty:
            continue
        rec = row.iloc[0]
        horizon_rows.append({
            "horizon_days": h,
            "cumulative_retry_probability": float(rec["cumulative_retry_probability_from_as_of"]),
            "survival_probability": float(rec["survival_probability_from_as_of"]),
            "hazard_on_horizon_day": float(rec["hazard_probability"]),
        })
    horizon_summary = pd.DataFrame(horizon_rows)

    median_row = curve[curve["cumulative_retry_probability_from_as_of"] >= 0.5].head(1)
    context = {
        "available": True,
        "schema_version": "yearline_universe.survival_hazard.v13",
        "ticker": str(base.get("ticker")),
        "target_transition": str(base.get("transition")),
        "latest_live_as_of": str(base.get("as_of_date")),
        "hazard_today": float(hazard[0]) if len(hazard) else None,
        "horizon_probabilities": {str(r["horizon_days"]): r for r in horizon_rows},
        "median_retry_horizon_day_if_prob_crosses_50pct": int(median_row["future_horizon_day"].iloc[0]) if not median_row.empty else None,
        "scenario": "state_hold_forward",
        "interpretation": "Prototype hazard curve conditioned on today's live repair state; not a price-path forecast.",
        "disclaimer": "Educational research only. Not financial advice. Not a calibrated trading signal.",
    }
    return curve, horizon_summary, context


# ---------------------------------------------------------------------------
# V13.3 Phase 3 — empirical completed-path horizon estimator (ports V12.4.1)
# ---------------------------------------------------------------------------

def _horizon_transition_key(df: pd.DataFrame) -> pd.Series:
    parts = []
    for col in ["ticker", "round", "transition", "from_date", "to_date"]:
        if col in df.columns:
            parts.append(df[col].astype(str).fillna("NA"))
        else:
            parts.append(pd.Series(["NA"] * len(df), index=df.index))
    out = parts[0]
    for p in parts[1:]:
        out = out + "|" + p
    return out


def _horizon_add_state_buckets(df: pd.DataFrame) -> pd.DataFrame:
    """Bucketize the live-observable state (days-since-touch x distance x drawdown)."""
    out = df.copy()
    if out.empty:
        return out
    if "trading_days_since_touch" in out.columns:
        out["days_since_touch_bucket"] = pd.cut(
            pd.to_numeric(out["trading_days_since_touch"], errors="coerce"),
            bins=[-0.1, 5, 10, 20, 40, 60, 90, 120, 10_000],
            labels=["000_005", "006_010", "011_020", "021_040", "041_060", "061_090", "091_120", "121_plus"],
            include_lowest=True,
        ).astype(str)
    else:
        out["days_since_touch_bucket"] = "unknown"
    if "distance_to_ma250_pct" in out.columns:
        out["distance_to_ma250_bucket"] = pd.cut(
            pd.to_numeric(out["distance_to_ma250_pct"], errors="coerce"),
            bins=[-1_000, -20, -15, -10, -7.5, -5, -2.5, 0, 2.5, 1_000],
            labels=["lt_m20", "m20_m15", "m15_m10", "m10_m7p5", "m7p5_m5", "m5_m2p5", "m2p5_0", "0_2p5", "gt_2p5"],
            include_lowest=True,
        ).astype(str)
    else:
        out["distance_to_ma250_bucket"] = "unknown"
    if "drawdown_so_far_pct" in out.columns:
        out["drawdown_so_far_bucket"] = pd.cut(
            pd.to_numeric(out["drawdown_so_far_pct"], errors="coerce"),
            bins=[-0.1, 3, 5, 8, 12, 20, 1_000],
            labels=["000_003", "003_005", "005_008", "008_012", "012_020", "020_plus"],
            include_lowest=True,
        ).astype(str)
    else:
        out["drawdown_so_far_bucket"] = "unknown"
    for col in ["ticker", "group", "transition", "from_canonical_quality"]:
        if col not in out.columns:
            out[col] = "unknown"
        out[col] = out[col].fillna("unknown").astype(str)
    return out


def build_empirical_horizon_reference(panel: pd.DataFrame) -> pd.DataFrame:
    """Completed historical at-risk rows with ``remaining_trading_days_to_retry``.

    Each row is an as-of day inside a *completed* transition; the target is how many
    trading days remained until the realised retry. This is the reference pool the
    empirical horizon estimator borrows strength from — it replaces the flawed
    frozen-state forward extrapolation with "how often did similar historical states
    retouch within H trading days".
    """
    if panel is None or panel.empty:
        return pd.DataFrame()
    d = panel[panel["is_live_transition"] == False].copy()
    d = d[d["event_retry_today"].notna()].copy()
    if d.empty:
        return d
    d["transition_key"] = _horizon_transition_key(d)
    d["event_retry_today"] = d["event_retry_today"].astype(int)
    d["trading_days_since_touch"] = pd.to_numeric(d["trading_days_since_touch"], errors="coerce")
    event_days = (
        d[d["event_retry_today"] == 1]
        .groupby("transition_key")["trading_days_since_touch"]
        .max().rename("event_trading_day").reset_index()
    )
    d = d.merge(event_days, on="transition_key", how="left")
    d["remaining_trading_days_to_retry"] = d["event_trading_day"] - d["trading_days_since_touch"]
    d = d[d["event_trading_day"].notna()].copy()
    d = d[d["remaining_trading_days_to_retry"] >= 0].copy()
    return _horizon_add_state_buckets(d).reset_index(drop=True)


_HORIZON_SCOPE_LADDER = [
    ("ticker_transition_quality_state", ["ticker", "transition", "from_canonical_quality", "days_since_touch_bucket", "distance_to_ma250_bucket", "drawdown_so_far_bucket"]),
    ("ticker_transition_state", ["ticker", "transition", "days_since_touch_bucket", "distance_to_ma250_bucket"]),
    ("group_transition_state", ["group", "transition", "days_since_touch_bucket", "distance_to_ma250_bucket"]),
    ("universe_transition_state", ["transition", "days_since_touch_bucket", "distance_to_ma250_bucket"]),
    ("group_state", ["group", "days_since_touch_bucket", "distance_to_ma250_bucket"]),
    ("universe_state", ["days_since_touch_bucket", "distance_to_ma250_bucket"]),
    ("group_transition", ["group", "transition"]),
    ("universe_transition", ["transition"]),
    ("universe_all_completed_transitions", []),
]


def _horizon_row_with_buckets(row_like) -> pd.Series:
    return _horizon_add_state_buckets(pd.DataFrame([dict(row_like)])).iloc[0]


def _horizon_filter_reference(reference_rows: pd.DataFrame, row_like, exclude_transition_key=None):
    ref = reference_rows.copy()
    if exclude_transition_key is not None and "transition_key" in ref.columns:
        ref = ref[ref["transition_key"].astype(str) != str(exclude_transition_key)].copy()
    if ref.empty:
        return ref, "empty_reference"
    row = _horizon_row_with_buckets(row_like)
    for scope, cols in _HORIZON_SCOPE_LADDER:
        mask = pd.Series(True, index=ref.index)
        for col in cols:
            if col not in ref.columns:
                mask &= False
            else:
                mask &= ref[col].astype(str).fillna("unknown").eq(str(row.get(col, "unknown")))
        sample = ref[mask].copy()
        if len(sample) >= HORIZON_MIN_REFERENCE_N or scope == "universe_all_completed_transitions":
            return sample, scope
    return ref, "universe_all_completed_transitions"


def empirical_horizon_probabilities_for_row(
    row_like, reference_rows: pd.DataFrame, horizons=None, exclude_transition_key=None,
) -> dict[int, dict[str, Any]]:
    """Empirical P(retry <= H) for a live/at-risk state from similar completed paths.

    Borrows strength via the scope ladder (first scope with >= N rows wins) and
    shrinks the in-scope rate toward the universe rate (Beta-style prior, strength
    HORIZON_PRIOR_STRENGTH). Every horizon carries its reference n + scope so the
    sample is transparent.
    """
    horizons = horizons or HAZARD_HORIZONS
    if reference_rows is None or reference_rows.empty:
        return {int(h): {"cumulative_retry_probability": np.nan, "survival_probability": np.nan,
                         "reference_n": 0, "reference_success_n": 0, "reference_scope": "no_reference_rows",
                         "universe_prior_rate": np.nan, "estimator": HORIZON_PROB_POLICY} for h in horizons}
    # The reference is bucketized once in build_empirical_horizon_reference; only
    # re-bucket if a caller passed raw rows (keeps the per-replay-row loop cheap).
    ref = reference_rows if "days_since_touch_bucket" in reference_rows.columns else _horizon_add_state_buckets(reference_rows.copy())
    sample, scope = _horizon_filter_reference(ref, row_like, exclude_transition_key=exclude_transition_key)
    out: dict[int, dict[str, Any]] = {}
    for h in horizons:
        h_int = int(h)
        universe_actual = (ref["remaining_trading_days_to_retry"] <= h_int).astype(int)
        prior_rate = float(universe_actual.mean()) if len(universe_actual) else np.nan
        if sample.empty:
            p, n, k = prior_rate, 0, np.nan
        else:
            y = (sample["remaining_trading_days_to_retry"] <= h_int).astype(int)
            n, k = int(len(y)), int(y.sum())
            if pd.isna(prior_rate):
                p = float(y.mean()) if n else np.nan
            else:
                p = float((k + HORIZON_PRIOR_STRENGTH * prior_rate) / (n + HORIZON_PRIOR_STRENGTH))
        out[h_int] = {
            "cumulative_retry_probability": p,
            "survival_probability": (1.0 - p) if not pd.isna(p) else np.nan,
            "reference_n": n, "reference_success_n": (None if pd.isna(k) else int(k)),
            "reference_scope": scope, "universe_prior_rate": prior_rate,
            "estimator": HORIZON_PROB_POLICY, "smoothing_prior_strength": HORIZON_PRIOR_STRENGTH,
        }
    return out


# ---------------------------------------------------------------------------
# Orchestration: full hazard layer for one ticker
# ---------------------------------------------------------------------------

def run_hazard_layer(
    ticker: str,
    peer_group: str,
    price_df: pd.DataFrame,
    recovery_table: pd.DataFrame,
    live_diagnostic: Mapping[str, Any] | None,
    config: StudyConfig | None = None,
    pooled_data: Mapping[str, Mapping[str, Any]] | None = None,
    fit_ml_models: bool = False,
    calibrate: bool = False,
    calibration_model: Mapping[str, Any] | None = None,
    surface_blend: bool = False,
    blend_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the survival hazard (and, optionally, the ML timing/quality models).

    ``pooled_data`` (optional) lets the V13.2 universe runner inject the whole
    universe so the hazard / ML models are trained on pooled history. When it is
    None, the fit uses only this ticker's own history (V13.1 single-ticker mode),
    and the resulting context is flagged as a low-sample prototype.

    ``fit_ml_models`` (default False): the V13.1 statistical-context envelope does
    NOT consume the retry-timing / quality ML predictions, so they are skipped by
    default — this avoids the timing model's 300-fit bootstrap (the dominant cost
    on high-event tickers) with no change to any pipeline output. Pass True if a
    caller specifically wants those prototype predictions.
    """
    config = config or StudyConfig()
    pooled = bool(pooled_data)
    tickers_data = dict(pooled_data) if pooled_data else {
        ticker: {
            "peer_group": peer_group, "price_df": price_df,
            "recovery_table": recovery_table, "live_diagnostic": live_diagnostic,
        }
    }

    # ML retry timing / quality — OPTIONAL (not consumed by the V13.1 envelope).
    timing_pred, quality_pred = pd.DataFrame(), pd.DataFrame()
    timing_status = {"status": "skipped_not_consumed_by_envelope"}
    ml_dataset = pd.DataFrame()
    if fit_ml_models:
        if pooled_data:
            ml_frames = [
                build_retry_transition_dataset(t, d.get("peer_group", "unknown"), d.get("recovery_table"), d.get("live_diagnostic") if t == ticker else None)
                for t, d in pooled_data.items()
            ]
            ml_dataset = pd.concat([f for f in ml_frames if not f.empty], ignore_index=True) if ml_frames else pd.DataFrame()
        else:
            ml_dataset = build_retry_transition_dataset(ticker, peer_group, recovery_table, live_diagnostic)
        if not ml_dataset.empty:
            timing_pred, _, _, timing_status = fit_retry_timing_model(ml_dataset, ticker)
            quality_pred, _, _ = fit_retry_quality_classifier(ml_dataset, ticker)
        else:
            timing_status = {"status": "no_data"}

    # Discrete-time hazard.
    panel = build_hazard_daily_panel(tickers_data, ticker, config)
    fit_obj, hazard_card = (None, {"status": "empty_panel"})
    if not panel.empty:
        fit_obj, hazard_card = fit_retry_hazard_model(panel)
    curve, horizon_summary, hazard_context = score_future_hazard_curve(fit_obj, panel)

    # V13.3 Phase 3: replace the canonical horizon P with the EMPIRICAL completed-path
    # estimator (ports V12.4.1). The logistic model still supplies hazard_today; its
    # state-hold-forward curve is retained only as a labelled diagnostic.
    horizon_reference = build_empirical_horizon_reference(panel)
    live_rows = panel[panel["is_live_transition"] == True]
    if hazard_context.get("available") and not live_rows.empty and not horizon_reference.empty:
        live_row = live_rows.sort_values("as_of_date").tail(1).iloc[0].to_dict()
        emp = empirical_horizon_probabilities_for_row(live_row, horizon_reference, HAZARD_HORIZONS)
        hazard_context["diagnostic_model_state_hold_forward"] = {
            "scenario": "state_hold_forward",
            "policy": "diagnostic_model_state_hold_forward_not_canonical",
            "horizon_probabilities": hazard_context.get("horizon_probabilities", {}),
            "warning": "State-hold-forward horizon probabilities are diagnostic only; not canonical P(retry<=H).",
        }
        hazard_context["horizon_probabilities"] = {str(h): emp[h] for h in HAZARD_HORIZONS}
        hazard_context["probability_policy_version"] = HORIZON_PROB_POLICY
        hazard_context["scenario"] = "empirical_completed_path_horizon_probability"
        hazard_context["horizon_reference_rows_n"] = int(len(horizon_reference))
        hazard_context["interpretation"] = (
            "hazard_today is the logistic one-day conditional hazard. Horizon probabilities are empirical "
            "completed-path estimates from similar historical at-risk states, not frozen-state extrapolations."
        )
        crossed = [h for h in HAZARD_HORIZONS if (emp[h].get("cumulative_retry_probability") or 0) >= 0.5]
        hazard_context["median_retry_horizon_day_if_prob_crosses_50pct"] = (min(crossed) if crossed else None)

    hazard_context["training_scope"] = "pooled_universe" if pooled else "single_ticker_self_fit"
    hazard_context["model_card"] = hazard_card
    if not pooled:
        hazard_context["low_sample_warning"] = (
            "Hazard/ML models fitted on this ticker's own history only (V13.1 single-ticker mode). "
            "Treat probabilities as a low-sample prototype; universe-pooled training arrives in V13.2."
        )

    # V13.3 Phase 4 (V13.7): OPT-IN calibration of the empirical horizon estimator.
    # Expensive (rescans the panel with leave-one-transition-out), so default off.
    calibration_context = {"available": False, "warning": "calibration_not_requested_pass_calibrate_true"}
    if calibrate and not panel.empty:
        from .calibration import build_calibration_context
        live_rows_c = panel[panel["is_live_transition"] == True]
        live_row_c = (live_rows_c.sort_values("as_of_date").tail(1).iloc[0].to_dict()
                      if not live_rows_c.empty else None)
        # ``calibration_model`` (V13.3 Phase 6 follow-up): when the universe runner has
        # already built the pooled calibration model once, reuse it — the per-ticker
        # cost collapses to the cheap live apply instead of rebuilding the LOTO dataset.
        calibration_context = build_calibration_context(panel, live_row=live_row_c, model=calibration_model)
        calibration_context["training_scope"] = "pooled_universe" if pooled else "single_ticker_self_fit"

    # V13.3 Phase 7 (consumer wiring): OPT-IN gated classifier↔empirical blend overlay.
    # Output-changing, so default off; pooled-only (cross-sectional features need the
    # universe) and only when there is a live hazard state. ``blend_model`` is the
    # compute-once model reused per ticker (built once by the universe runner).
    blend_context = {"available": False, "warning": "blend_not_requested_pass_surface_blend_true"}
    if surface_blend and pooled and hazard_context.get("available") and not horizon_reference.empty:
        from .blend_surface import build_blend_context
        live_rows_b = panel[panel["is_live_transition"] == True]
        if not live_rows_b.empty:
            live_row_b = live_rows_b.sort_values("as_of_date").tail(1).iloc[0].to_dict()
            emp_b = empirical_horizon_probabilities_for_row(live_row_b, horizon_reference, HAZARD_HORIZONS)
            emp_p = {h: emp_b[h].get("cumulative_retry_probability") for h in (10, 20, 40, 60)}
            blend_context = build_blend_context(tickers_data, ticker, live_row_b, emp_p, config, model=blend_model)

    return {
        "hazard_history": curve,
        "hazard_horizon_summary": horizon_summary,
        "hazard_context": hazard_context,
        "hazard_fit": fit_obj,          # (model, feature_names, train) — reused by replay
        "hazard_panel": panel,
        "calibration_context": calibration_context,
        "blend_context": blend_context,
        "timing_prediction": timing_pred,
        "timing_status": timing_status,
        "quality_prediction": quality_pred,
        "ml_dataset_rows": int(len(ml_dataset)),
    }
