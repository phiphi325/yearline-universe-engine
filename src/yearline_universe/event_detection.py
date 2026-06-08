"""Yearline (MA250) attempt detection and canonical-event construction.

Faithful port of V12 Module A detector logic + Module B canonical timeline
builder. Preserves the V10-parity state-machine semantics exactly. Every
function takes ``ticker`` and ``config`` explicitly — no MSFT specialisation.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import add_indicators, date_str

__all__ = [
    "detect_source_attempts",
    "build_canonical_events",
    "assign_canonical_rounds",
]


def _prepare_detector_frame(df_in: pd.DataFrame, config: StudyConfig) -> pd.DataFrame:
    """Return a MA-ready working frame for detector state-machine logic."""
    work = add_indicators(df_in, config).copy()
    work = work.dropna(subset=["MA250"]).copy()
    work["above_close"] = work["Close"] > work["MA250"]
    sustained_below = (~work["above_close"]).rolling(config.lookback_below_days).mean() >= config.below_frac
    work["sustained_below"] = sustained_below.fillna(False)

    strict_trigger = (
        (work["High"] >= work["MA250"])
        & (~work["above_close"].shift(1, fill_value=True))
        & work["sustained_below"]
    )
    loose_trigger = (
        (work["Low"] <= work["MA250"] * (1.0 + config.band))
        & (work["High"] >= work["MA250"] * (1.0 - config.band))
        & work["sustained_below"]
    )
    work["strict_trigger"] = strict_trigger
    work["loose_trigger"] = loose_trigger
    return work


def classify_attempt_outcome_v10_parity(
    work: pd.DataFrame,
    loc: int,
    config: StudyConfig,
) -> tuple[str, int | None, int, int, float]:
    """Classify one detector attempt using V10 state-machine semantics.

    Returns: outcome, days_to_confirm, end_loc, peak_loc, peak_high
    """
    high = work["High"]
    close = work["Close"]
    ma = work["MA250"]
    n = len(work)

    consec_above = 0
    peak_high = float(high.iloc[loc])
    peak_loc = loc
    outcome = "pending"
    days_to_confirm: int | None = None
    end_loc = loc
    scan_stop = min(loc + config.max_scan_days, n)

    for k in range(loc, scan_stop):
        if float(high.iloc[k]) > peak_high:
            peak_high = float(high.iloc[k])
            peak_loc = k

        consec_above = consec_above + 1 if close.iloc[k] > ma.iloc[k] else 0
        if consec_above >= config.confirm_days:
            hold_end = k + config.success_hold_days
            if hold_end >= n:
                outcome = "pending"
                days_to_confirm = k - loc
                end_loc = k
                break
            hold_slice = work.iloc[k:hold_end + 1]
            if (hold_slice["Close"] > hold_slice["MA250"]).mean() >= 0.70:
                outcome = "success"
                days_to_confirm = k - loc
                end_loc = k
                break

        lo = max(loc, k - config.new_attempt_gap)
        if k > peak_loc and (close.iloc[lo:k + 1] < ma.iloc[lo:k + 1]).sum() >= config.new_attempt_gap:
            outcome = "fail"
            end_loc = k
            break

        end_loc = k

    if outcome == "pending" and scan_stop >= loc + config.max_scan_days:
        outcome = "fail"
    return outcome, days_to_confirm, end_loc, peak_loc, peak_high


def fixed_window_metrics(
    work: pd.DataFrame,
    loc: int,
    config: StudyConfig,
) -> dict[str, float | None]:
    """Fixed-window MFE / MAE / net metrics with V10 full-window hygiene."""
    out: dict[str, float | None] = {}
    entry_close = float(work["Close"].iloc[loc])
    avail_after = len(work) - loc
    for label, wlen in config.windows.items():
        full = avail_after >= wlen
        out[f"{label}_full"] = bool(full)
        if not full:
            out[f"{label}_MFE"] = None
            out[f"{label}_MAE"] = None
            out[f"{label}_net"] = None
            out[f"{label}_overshoot"] = None
            out[f"{label}_days_left"] = int(wlen - avail_after)
            continue
        out[f"{label}_days_left"] = 0
        sl = work.iloc[loc:loc + wlen]
        out[f"{label}_MFE"] = (sl["High"].max() / entry_close - 1.0) * 100.0
        out[f"{label}_MAE"] = (sl["Low"].min() / entry_close - 1.0) * 100.0
        out[f"{label}_net"] = (sl["Close"].iloc[-1] / entry_close - 1.0) * 100.0
        out[f"{label}_overshoot"] = ((sl["High"] / sl["MA250"]) - 1.0).max() * 100.0
    return out


def detect_source_attempts(
    ticker: str,
    df_in: pd.DataFrame,
    detector: str,
    config: StudyConfig | None = None,
) -> pd.DataFrame:
    """Detect strict or loose source attempts with V10 parity semantics."""
    config = config or StudyConfig()
    if detector not in {"strict", "loose"}:
        raise ValueError("detector must be 'strict' or 'loose'")

    work = _prepare_detector_frame(df_in, config)
    if work.empty:
        return pd.DataFrame()
    trigger_col = "strict_trigger" if detector == "strict" else "loose_trigger"

    rows: list[dict[str, Any]] = []
    n = len(work)
    i = 1
    source_round = 0
    source_attempt_no = 0
    round_open = False

    while i < n:
        if bool(work[trigger_col].iloc[i]):
            if not round_open:
                source_round += 1
                source_attempt_no = 1
                round_open = True
            else:
                source_attempt_no += 1

            outcome, days_to_confirm, end_work_loc, peak_work_loc, peak_high = classify_attempt_outcome_v10_parity(work, i, config)
            touch_date = pd.to_datetime(work.index[i])
            end_date = pd.to_datetime(work.index[end_work_loc])
            raw_loc_arr = df_in.index.get_indexer([touch_date])
            raw_end_arr = df_in.index.get_indexer([end_date])
            raw_loc = int(raw_loc_arr[0]) if len(raw_loc_arr) and raw_loc_arr[0] >= 0 else int(i)
            raw_end_loc = int(raw_end_arr[0]) if len(raw_end_arr) and raw_end_arr[0] >= 0 else raw_loc

            row = work.iloc[i]
            touch_ma = float(row["MA250"])
            win = fixed_window_metrics(work, i, config)
            touch_window_3d = win.get("3d_overshoot")
            touch_window_5d = win.get("5d_overshoot")
            lifecycle_peak = (peak_high / touch_ma - 1.0) * 100.0 if touch_ma else np.nan

            rec: dict[str, Any] = {
                "ticker": ticker,
                "detector": detector,
                "source_round": int(source_round),
                "source_attempt_no": int(source_attempt_no),
                "touch_date": touch_date,
                "trading_loc": raw_loc,
                "work_loc": int(i),
                "outcome": outcome,
                "days_to_confirm": days_to_confirm,
                "end_loc": raw_end_loc,
                "end_work_loc": int(end_work_loc),
                "end_date": end_date,
                "gap_state": row["gap_state"],
                "touch_day_overshoot_pct": (float(row["High"]) / touch_ma - 1.0) * 100.0,
                "touch_day_close_distance_pct": (float(row["Close"]) / touch_ma - 1.0) * 100.0,
                "touch_window_3d_overshoot_pct": touch_window_3d,
                "touch_window_5d_overshoot_pct": touch_window_5d,
                "lifecycle_peak_overshoot_pct_post_hoc": lifecycle_peak,
                "ma250": touch_ma,
                "ma200": float(row["MA200"]) if pd.notna(row["MA200"]) else np.nan,
                "ma200_ma250_gap_pct": float(row["ma200_ma250_gap_pct"]) if pd.notna(row["ma200_ma250_gap_pct"]) else np.nan,
                "atr14_pct": float(row["ATR14_pct"]) if pd.notna(row["ATR14_pct"]) else np.nan,
                "hv30": float(row["HV30"]) if pd.notna(row["HV30"]) else np.nan,
            }
            rec.update(win)
            rows.append(rec)

            if outcome == "success":
                round_open = False
            i = end_work_loc + 1
        else:
            i += 1

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("touch_date").reset_index(drop=True)
    out["source_detector_event_id"] = np.arange(1, len(out) + 1)
    return out


def _json_safe_source_events(rows: pd.DataFrame) -> str:
    """Compact JSON audit trail for one canonical cluster."""
    cols = [
        "source_event_id", "detector", "touch_date", "trading_loc", "outcome",
        "touch_day_overshoot_pct", "touch_window_5d_overshoot_pct",
        "lifecycle_peak_overshoot_pct_post_hoc",
    ]
    use = [c for c in cols if c in rows.columns]
    audit = rows[use].copy()
    for c in ["touch_date"]:
        if c in audit.columns:
            audit[c] = audit[c].map(date_str)
    return json.dumps(audit.to_dict(orient="records"), default=str)


def build_canonical_events(
    ticker: str,
    df_in: pd.DataFrame,
    source_attempts: pd.DataFrame,
    config: StudyConfig | None = None,
) -> pd.DataFrame:
    config = config or StudyConfig()
    if source_attempts is None or source_attempts.empty:
        return pd.DataFrame()

    src = source_attempts.copy().sort_values(["trading_loc", "detector"]).reset_index(drop=True)
    merge = config.canonical_touch_merge_trading_days

    strict = src[src["detector"] == "strict"].copy()
    loose = src[src["detector"] == "loose"].copy()

    clusters: list[dict[str, Any]] = []

    # 1) Strict anchors.
    for _, r in strict.iterrows():
        clusters.append({
            "anchor_loc": int(r["trading_loc"]),
            "anchor_date": pd.to_datetime(r["touch_date"]),
            "rows": [r.to_dict()],
            "has_strict": True,
        })

    # 2) Attach loose hits to nearest strict anchor if close enough.
    loose_unassigned: list[dict[str, Any]] = []
    for _, r in loose.iterrows():
        loc = int(r["trading_loc"])
        if clusters:
            distances = [abs(loc - int(c["anchor_loc"])) for c in clusters]
            j = int(np.argmin(distances))
            if distances[j] <= merge:
                clusters[j]["rows"].append(r.to_dict())
                continue
        loose_unassigned.append(r.to_dict())

    # 3) Build loose-only clusters with non-chain anchor policy.
    loose_unassigned = sorted(loose_unassigned, key=lambda x: int(x["trading_loc"]))
    current: dict[str, Any] | None = None
    for r in loose_unassigned:
        loc = int(r["trading_loc"])
        if current is None or loc - int(current["anchor_loc"]) > merge:
            if current is not None:
                clusters.append(current)
            current = {
                "anchor_loc": loc,
                "anchor_date": pd.to_datetime(r["touch_date"]),
                "rows": [r],
                "has_strict": False,
            }
        else:
            current["rows"].append(r)
    if current is not None:
        clusters.append(current)

    events: list[dict[str, Any]] = []
    for c in clusters:
        rows = pd.DataFrame(c["rows"]).sort_values(["trading_loc", "detector"])
        has_strict = (rows["detector"] == "strict").any()
        strict_rows = rows[rows["detector"] == "strict"].sort_values("trading_loc")
        if has_strict:
            rep = strict_rows.iloc[0]
            canonical_quality = "strict"
            strict_touch_date = pd.to_datetime(rep["touch_date"])
            strict_trading_loc = int(rep["trading_loc"])
        else:
            rep = rows.sort_values("trading_loc").iloc[0]
            canonical_quality = "loose_only"
            strict_touch_date = pd.NaT
            strict_trading_loc = np.nan

        source_detectors = "+".join(sorted(rows["detector"].unique()))
        outcomes = set(rows["outcome"].astype(str))
        if "success" in outcomes:
            canonical_outcome = "success"
        elif "pending" in outcomes:
            canonical_outcome = "pending"
        else:
            canonical_outcome = "fail"

        locs = rows["trading_loc"].astype(int).tolist()
        earliest_idx = int(rows["trading_loc"].idxmin())
        earliest_row = rows.loc[earliest_idx]

        lifecycle_peak_cluster_max = pd.to_numeric(
            rows.get("lifecycle_peak_overshoot_pct_post_hoc"), errors="coerce"
        ).max()

        ev = {
            "ticker": ticker,
            "canonical_touch_date": pd.to_datetime(rep["touch_date"]),
            "canonical_trading_loc": int(rep["trading_loc"]),
            "canonical_quality": canonical_quality,
            "source_detectors": source_detectors,
            "source_event_count": int(len(rows)),
            "source_event_ids": ",".join(map(str, rows["source_event_id"].astype(int).tolist())),
            "source_events_json": _json_safe_source_events(rows),
            "earliest_detected_date": pd.to_datetime(earliest_row["touch_date"]),
            "earliest_detected_trading_loc": int(earliest_row["trading_loc"]),
            "strict_touch_date": strict_touch_date,
            "strict_trading_loc": strict_trading_loc,
            "canonical_date_policy": config.canonical_date_policy,
            "canonical_cluster_policy": config.canonical_cluster_policy,
            "cluster_span_trading_days": int(max(locs) - min(locs)),
            "canonical_outcome": canonical_outcome,
            "canonical_gap_state": rep.get("gap_state"),
            "canonical_touch_day_overshoot_pct": float(rep.get("touch_day_overshoot_pct", np.nan)),
            "canonical_touch_day_close_distance_pct": float(rep.get("touch_day_close_distance_pct", np.nan)),
            "canonical_touch_window_3d_overshoot_pct": float(rep.get("touch_window_3d_overshoot_pct", np.nan)),
            "canonical_touch_window_5d_overshoot_pct": float(rep.get("touch_window_5d_overshoot_pct", np.nan)),
            "canonical_lifecycle_peak_overshoot_pct_post_hoc": float(lifecycle_peak_cluster_max) if not pd.isna(lifecycle_peak_cluster_max) else np.nan,
            "representative_lifecycle_peak_overshoot_pct_post_hoc": float(rep.get("lifecycle_peak_overshoot_pct_post_hoc", np.nan)),
            "canonical_atr14_pct": float(rep.get("atr14_pct", np.nan)),
            "canonical_hv30": float(rep.get("hv30", np.nan)),
            "canonical_ma250": float(rep.get("ma250", np.nan)),
            "canonical_ma200": float(rep.get("ma200", np.nan)),
            "canonical_ma200_ma250_gap_pct": float(rep.get("ma200_ma250_gap_pct", np.nan)),
        }
        if ev["cluster_span_trading_days"] > merge:
            ev["canonical_warning"] = "cluster_span_exceeds_merge_window"
        else:
            ev["canonical_warning"] = ""
        events.append(ev)

    out = pd.DataFrame(events).sort_values("canonical_touch_date").reset_index(drop=True)
    out["canonical_event_id"] = np.arange(1, len(out) + 1)
    return assign_canonical_rounds(out)


def assign_canonical_rounds(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events

    out = events.sort_values("canonical_touch_date").reset_index(drop=True).copy()
    rounds = []
    attempts = []
    current_round = 1
    current_attempt = 1

    for _, row in out.iterrows():
        rounds.append(current_round)
        attempts.append(current_attempt)
        if str(row["canonical_outcome"]) == "success":
            current_round += 1
            current_attempt = 1
        else:
            current_attempt += 1

    out["round"] = rounds
    out["canonical_attempt_no"] = attempts
    cols = ["ticker", "canonical_event_id", "round", "canonical_attempt_no"] + [
        c for c in out.columns if c not in {"ticker", "canonical_event_id", "round", "canonical_attempt_no"}
    ]
    return out[cols]
