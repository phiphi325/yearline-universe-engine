"""Historical daily replay / monitoring backfill.

Faithful port of V12 Module E (V12.9). Builds a daily state-history time series
for one ticker from ``replay_start`` to the latest bar, scoring the fixed
discrete-time hazard model at each as-of date and assigning a coarse
``mode_state_replay``.

Performance (V13.1): the forward hazard curve is scored in a single vectorized
pass instead of rebuilding a one-hot design matrix per as-of day. This is
*output-preserving*: the hazard model is a linear (logistic) classifier and the
state-hold-forward scenario only varies two features across the horizon
(``trading_days_since_touch`` and ``calendar_days_since_touch``, each +1 per day),
so for each as-of day the future-day logit is exactly
``base_logit + h * (coef_trading + coef_calendar)``. We therefore build the design
matrix once for all as-of base rows and broadcast over horizons. See
docs/V13_performance_optimization_report.md.
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import add_indicators
from .hazard import (
    prepare_hazard_design, HAZARD_HORIZONS, HAZARD_MAX_FUTURE_DAYS,
    build_empirical_horizon_reference, empirical_horizon_probabilities_for_row,
    HORIZON_PROB_POLICY,
)

__all__ = ["build_replay_history", "build_replay_history_incremental"]

REPLAY_MODEL_POLICY = "fixed_hazard_model"
# v2: V13.3 Phase 3 changed the per-row schema (empirical canonical P + model
# state-hold-forward diagnostic columns) — invalidate any v1 caches.
REPLAY_CACHE_SCHEMA = "v13_replay_cache_2"


def _as_float(x, default=np.nan):
    try:
        if x is None or pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def _as_int(x, default=None):
    try:
        if x is None or pd.isna(x):
            return default
        return int(x)
    except Exception:
        return default


def _required_rebound(distance_pct):
    try:
        d = float(distance_pct) / 100.0
        if d >= 0:
            return 0.0
        return (1.0 / (1.0 + d) - 1.0) * 100.0
    except Exception:
        return np.nan


def _mode_proxy(distance_pct, drawdown_pct, p_retry_40d, p_retry_60d) -> str:
    distance_pct = _as_float(distance_pct)
    drawdown_pct = _as_float(drawdown_pct)
    p60 = _as_float(p_retry_60d)
    if distance_pct >= 0 and drawdown_pct <= 5:
        return "accepted_above_watch"
    if distance_pct < -8 and drawdown_pct >= 8:
        return "failed_repair_deep_below"
    if distance_pct < 0 and p60 >= 0.50:
        return "repair_retry_probability_building"
    if distance_pct < 0:
        return "below_yearline_repair"
    return "transition_watch"


def _batch_score_curves(hazard_fit, base_df: pd.DataFrame, horizons, max_future_days):
    """Vectorized forward hazard curves for ALL as-of base rows at once.

    Exact closed form for the linear hazard model under the state-hold-forward
    scenario. Returns one summary dict per row (same shape the old per-day path
    produced).
    """
    model, feature_names, train = hazard_fit
    _, X_base, _ = prepare_hazard_design(train, base_df)
    X_base = X_base.reindex(columns=feature_names, fill_value=0)

    coef = np.asarray(model.coef_, dtype=float).ravel()
    intercept = float(np.asarray(model.intercept_, dtype=float).ravel()[0])
    base_logit = X_base.to_numpy(dtype=float) @ coef + intercept          # (n_rows,)

    fn = list(feature_names)
    slope = coef[fn.index("trading_days_since_touch")] + coef[fn.index("calendar_days_since_touch")]

    H = np.arange(1, max_future_days + 1)                                 # (Hmax,)
    logits = base_logit[:, None] + H[None, :] * slope                     # (n_rows, Hmax)
    hazard = 1.0 / (1.0 + np.exp(-logits))                                # (n_rows, Hmax)
    surv_cum = np.cumprod(1.0 - np.clip(hazard, 0.0, 1.0), axis=1)        # survival
    cum = 1.0 - surv_cum                                                  # cumulative retry prob

    summaries = []
    for i in range(hazard.shape[0]):
        s: dict[str, Any] = {"hazard_today": float(hazard[i, 0])}
        for horizon in horizons:
            j = min(int(horizon), max_future_days) - 1
            s[horizon] = {"cumulative_retry_probability": float(cum[i, j]),
                          "survival_probability": float(surv_cum[i, j])}
        cross = np.flatnonzero(cum[i] >= 0.5)
        s["median_retry_horizon_day"] = int(H[cross[0]]) if len(cross) else None
        summaries.append(s)
    return summaries


def build_replay_history(
    ticker: str,
    price_df: pd.DataFrame,
    canonical_events: pd.DataFrame,
    peer_group: str,
    hazard_fit: Any,
    config: StudyConfig | None = None,
    replay_start: str = "2020-01-01",
    replay_end: str | None = None,
    horizons: list[int] | None = None,
    max_future_days: int = HAZARD_MAX_FUTURE_DAYS,
) -> pd.DataFrame:
    """Daily replay state-history for one ticker. Returns the history DataFrame."""
    config = config or StudyConfig()
    horizons = horizons or HAZARD_HORIZONS
    if price_df is None or price_df.empty or canonical_events is None or canonical_events.empty:
        return pd.DataFrame()

    price = add_indicators(price_df, config).copy()
    price.index = pd.to_datetime(price.index)
    events = canonical_events.copy()
    events["canonical_touch_date"] = pd.to_datetime(events["canonical_touch_date"]).dt.normalize()

    start = pd.Timestamp(replay_start).normalize()
    end = pd.Timestamp(replay_end).normalize() if replay_end is not None else pd.Timestamp(price.index.max()).normalize()
    index_norm = pd.to_datetime(price.index).normalize()
    replay_dates = [d for d in index_norm if start <= d <= end]

    # --- Pass 1: build per-as-of state rows (no hazard scoring yet) ---------
    rows = []
    for as_of in replay_dates:
        eligible = events[events["canonical_touch_date"] <= as_of]
        if eligible.empty:
            continue
        latest_event = eligible.sort_values(["canonical_touch_date", "canonical_event_id"]).iloc[-1]
        touch_date = pd.Timestamp(latest_event["canonical_touch_date"]).normalize()
        if touch_date >= as_of:
            continue
        touch_match = np.flatnonzero(index_norm == touch_date)
        asof_match = np.flatnonzero(index_norm == as_of)
        if len(touch_match) == 0 or len(asof_match) == 0:
            continue
        touch_loc, asof_loc = int(touch_match[0]), int(asof_match[0])
        if asof_loc <= touch_loc:
            continue

        sub = price.iloc[touch_loc:asof_loc + 1]
        cur = price.iloc[asof_loc]
        entry_close = float(price["Close"].iloc[touch_loc])
        drawdown_so_far_pct = abs((float(sub["Low"].min()) / entry_close - 1.0) * 100.0)
        below_series = ((sub["Low"] / sub["MA250"]) - 1.0) * 100.0
        below_min = float(below_series.min()) if below_series.notna().any() else np.nan
        below_depth_abs = abs(below_min) if not pd.isna(below_min) and below_min < 0 else 0.0
        distance_pct = ((float(cur["Close"]) / float(cur["MA250"])) - 1.0) * 100.0 if not pd.isna(cur["MA250"]) else np.nan

        attempt_no = _as_int(latest_event.get("canonical_attempt_no"), None)
        to_attempt = attempt_no + 1 if attempt_no is not None else None
        transition = f"{attempt_no}_to_{to_attempt}" if attempt_no is not None else "unknown"

        rows.append({
            "as_of_date": str(as_of.date()), "ticker": ticker, "group": peer_group,
            "latest_round": _as_int(latest_event.get("round"), None), "latest_attempt_no": attempt_no,
            "target_transition": transition, "transition": transition,
            "latest_touch_date": str(touch_date.date()),
            "canonical_event_id": _as_int(latest_event.get("canonical_event_id"), None),
            "canonical_quality": str(latest_event.get("canonical_quality", "unknown")),
            "from_canonical_quality": str(latest_event.get("canonical_quality", "unknown")),
            "latest_event_outcome": latest_event.get("canonical_outcome", None),
            "trading_days_since_touch": int(asof_loc - touch_loc),
            "calendar_days_since_touch": int((as_of - touch_date).days),
            "days_since_last_touch": int(asof_loc - touch_loc),
            "current_close": float(cur["Close"]),
            "current_ma250": float(cur["MA250"]) if not pd.isna(cur["MA250"]) else np.nan,
            "distance_to_ma250_pct": distance_pct,
            "required_rebound_to_ma250_pct": _required_rebound(distance_pct),
            "drawdown_so_far_pct": drawdown_so_far_pct, "below_ma250_depth_so_far_pct": below_depth_abs,
            "from_touch_day_overshoot_pct": _as_float(latest_event.get("canonical_touch_day_overshoot_pct", np.nan)),
            "from_fixed_5d_overshoot_pct": _as_float(latest_event.get("canonical_touch_window_5d_overshoot_pct", np.nan)),
            "attempt_no": attempt_no, "event_retry_today": 0,
            "is_live_transition": True, "is_censored_transition": True, "is_replay_row": True,
        })

    if not rows:
        return pd.DataFrame()

    # --- Pass 2: ONE batched, vectorized hazard scoring for all as-of rows ------
    # The logistic state-hold-forward curve is now a DIAGNOSTIC (it saturates to 1.0
    # for deep-below states). hazard_today is kept from it; the canonical horizon
    # P(retry<=H) is the empirical completed-path estimator below (V13.3 Phase 3).
    summaries = None
    if hazard_fit is not None:
        try:
            summaries = _batch_score_curves(hazard_fit, pd.DataFrame(rows), horizons, max_future_days)
        except Exception as exc:  # pragma: no cover - defensive
            summaries = None
            for rr in rows:
                rr["hazard_error"] = str(exc)

    # Empirical completed-path reference (built once from the fitted train panel).
    horizon_reference = (
        build_empirical_horizon_reference(hazard_fit[2])
        if (hazard_fit is not None and len(hazard_fit) >= 3 and hazard_fit[2] is not None) else pd.DataFrame()
    )
    have_ref = horizon_reference is not None and not horizon_reference.empty

    for i, rr in enumerate(rows):
        # hazard_today + the diagnostic state-hold-forward curve.
        if summaries is not None:
            s = summaries[i]
            rr["hazard_today"] = s["hazard_today"]
            for h in horizons:
                rr[f"p_retry_within_{h}d_model_state_hold_forward_diagnostic"] = s[h]["cumulative_retry_probability"]
            rr["median_retry_horizon_day_model_diagnostic"] = s["median_retry_horizon_day"]
        else:
            rr["hazard_today"] = np.nan
            for h in horizons:
                rr[f"p_retry_within_{h}d_model_state_hold_forward_diagnostic"] = None
            rr["median_retry_horizon_day_model_diagnostic"] = None

        # Canonical horizon probabilities: empirical completed-path estimator.
        if have_ref:
            emp = empirical_horizon_probabilities_for_row(rr, horizon_reference, horizons)
            for h in horizons:
                e = emp[int(h)]
                rr[f"p_retry_within_{h}d"] = e["cumulative_retry_probability"]
                rr[f"survival_{h}d"] = e["survival_probability"]
                rr[f"p_retry_within_{h}d_reference_n"] = e["reference_n"]
                rr[f"p_retry_within_{h}d_reference_scope"] = e["reference_scope"]
            crossed = [h for h in horizons if (emp[int(h)].get("cumulative_retry_probability") or 0) >= 0.5]
            rr["median_retry_horizon_day"] = min(crossed) if crossed else None
            rr["horizon_probability_policy"] = HORIZON_PROB_POLICY
        else:
            for h in horizons:
                rr[f"p_retry_within_{h}d"] = None
                rr[f"survival_{h}d"] = None
            rr["median_retry_horizon_day"] = None
            rr["horizon_probability_policy"] = "unavailable_no_reference"

        rr["mode_state_replay"] = _mode_proxy(
            rr.get("distance_to_ma250_pct"), rr.get("drawdown_so_far_pct"),
            rr.get("p_retry_within_40d"), rr.get("p_retry_within_60d"),
        )
        rr["replay_model_policy"] = REPLAY_MODEL_POLICY

    history = pd.DataFrame(rows).sort_values("as_of_date").reset_index(drop=True)
    return history


# ---------------------------------------------------------------------------
# Incremental daily mode
# ---------------------------------------------------------------------------
#
# A daily run only needs to score the newest bar(s); the prior history doesn't
# change UNLESS a corporate action (split/dividend) retroactively re-bases the
# adjusted price series, or a newly completed transition changes the fitted
# hazard model. Each replay row is computed *independently* from
# (price, canonical_events, model), so appending new dates is exact AS LONG AS
# those inputs are unchanged over the cached window. We therefore fingerprint
# exactly those inputs; any mismatch (e.g. a split or dividend re-adjustment)
# forces a full recompute. Result: incremental output is always identical to a
# full replay.


def _overlap_fingerprint(price_df, canonical_events, hazard_fit, start, upto, ndigits: int = 6) -> str:
    """Hash the inputs the cached history depends on, over [start, upto].

    Adjusted OHLCV (rounded) catches splits/dividends (which re-base the whole
    series); the canonical-events signature catches new/changed events; the model
    coefficients catch a refit. Any change ⇒ different hash ⇒ cache invalidated.
    """
    h = hashlib.sha1()
    idx = pd.to_datetime(price_df.index).normalize()
    mask = (idx >= start) & (idx <= upto)
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in price_df.columns]
    sub = price_df.loc[mask, cols].round(ndigits)
    h.update(pd.util.hash_pandas_object(sub, index=True).values.tobytes())

    if canonical_events is not None and not canonical_events.empty:
        ev = canonical_events.copy()
        ev["_d"] = pd.to_datetime(ev["canonical_touch_date"]).dt.normalize()
        ev = ev[ev["_d"] <= upto]
        last = str(ev["_d"].max().date()) if len(ev) else "none"
        h.update(f"{len(ev)}|{last}|{''.join(ev['canonical_outcome'].astype(str))}".encode())
    else:
        h.update(b"no_events")

    if hazard_fit is not None:
        model = hazard_fit[0]
        h.update(np.asarray(model.coef_, dtype=float).round(8).tobytes())
        h.update(np.asarray(model.intercept_, dtype=float).round(8).tobytes())
        h.update("|".join(map(str, hazard_fit[1])).encode())
    else:
        h.update(b"no_model")

    h.update(str(pd.Timestamp(start).date()).encode())
    return h.hexdigest()


def _load_cache(path: Path):
    try:
        with open(path, "rb") as fh:
            obj = pickle.load(fh)
        if isinstance(obj, dict) and "meta" in obj and "history" in obj:
            return obj
    except Exception:
        return None
    return None


def _save_cache(path: Path, history: pd.DataFrame, fingerprint: str, start, last) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "schema_version": REPLAY_CACHE_SCHEMA,
        "replay_start": str(pd.Timestamp(start).date()),
        "last_date": str(pd.Timestamp(last).date()),
        "fingerprint": fingerprint,
        "n_rows": int(len(history)),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump({"meta": meta, "history": history}, fh)
    tmp.replace(path)  # atomic-ish write


def build_replay_history_incremental(
    ticker: str,
    price_df: pd.DataFrame,
    canonical_events: pd.DataFrame,
    peer_group: str,
    hazard_fit: Any,
    config: StudyConfig | None = None,
    replay_start: str = "2020-01-01",
    horizons: list[int] | None = None,
    max_future_days: int = HAZARD_MAX_FUTURE_DAYS,
    state_dir: str | Path | None = None,
) -> tuple[pd.DataFrame, str]:
    """Replay history with a persistent per-ticker cache (daily mode).

    Returns ``(history, mode)`` where mode is one of:
    ``full_recompute:<reason>``, ``incremental_appended:<n>``, ``cache_hit_no_new``.
    The history is always identical to ``build_replay_history`` on the same data.
    Falls back to a full recompute on any cache miss/invalidation (no cache,
    schema/replay_start change, shrunk window, or changed inputs — e.g. a split or
    dividend re-adjustment, or a refit hazard model).
    """
    config = config or StudyConfig()
    horizons = horizons or HAZARD_HORIZONS
    if state_dir is None:
        # No cache location ⇒ behave like the plain (full) builder.
        return build_replay_history(ticker, price_df, canonical_events, peer_group, hazard_fit,
                                    config, replay_start, None, horizons, max_future_days), "full_recompute:no_state_dir"
    if price_df is None or price_df.empty or canonical_events is None or canonical_events.empty:
        return pd.DataFrame(), "full_recompute:empty_inputs"

    cache_path = Path(state_dir) / f"{ticker}_replay.pkl"
    start = pd.Timestamp(replay_start).normalize()
    idx = pd.to_datetime(price_df.index).normalize()
    current_last = idx[idx <= idx.max()].max()

    def _full(reason: str) -> tuple[pd.DataFrame, str]:
        hist = build_replay_history(ticker, price_df, canonical_events, peer_group, hazard_fit,
                                    config, replay_start, None, horizons, max_future_days)
        if not hist.empty:
            fp = _overlap_fingerprint(price_df, canonical_events, hazard_fit, start, current_last)
            _save_cache(cache_path, hist, fp, start, current_last)
        return hist, f"full_recompute:{reason}"

    cached = _load_cache(cache_path)
    if cached is None:
        return _full("no_cache")
    meta = cached["meta"]
    if meta.get("schema_version") != REPLAY_CACHE_SCHEMA:
        return _full("cache_schema_mismatch")
    if meta.get("replay_start") != str(start.date()):
        return _full("replay_start_changed")

    cached_last = pd.Timestamp(meta["last_date"]).normalize()
    if current_last < cached_last:
        return _full("data_window_shrank")

    # Validate the cached window against fresh inputs (splits/dividends/model).
    fresh_fp = _overlap_fingerprint(price_df, canonical_events, hazard_fit, start, cached_last)
    if fresh_fp != meta.get("fingerprint"):
        return _full("inputs_changed")  # e.g. split/dividend re-adjustment or model refit

    if current_last == cached_last:
        return cached["history"], "cache_hit_no_new"

    # Fast path: score only the new dates (cached_last, current_last].
    next_day = cached_last + pd.Timedelta(days=1)
    new_rows = build_replay_history(ticker, price_df, canonical_events, peer_group, hazard_fit,
                                    config, str(next_day.date()), None, horizons, max_future_days)
    if new_rows.empty:
        return cached["history"], "cache_hit_no_new"
    history = pd.concat([cached["history"], new_rows], ignore_index=True)
    history = history.sort_values("as_of_date").reset_index(drop=True)
    fp2 = _overlap_fingerprint(price_df, canonical_events, hazard_fit, start, current_last)
    _save_cache(cache_path, history, fp2, start, current_last)
    return history, f"incremental_appended:{len(new_rows)}"
