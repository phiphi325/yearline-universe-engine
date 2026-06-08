"""Episode, inter-attempt recovery, mode-transition, and live-diagnostic logic.

Faithful port of V12 Module A/B episode + mode-state code. The only structural
change from V12 is that the global ``CONFIG`` references are now explicit
``config`` parameters, so the functions are reusable for any ticker.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import add_indicators, date_str

__all__ = [
    "classify_retry_pattern",
    "build_episode_table",
    "build_recovery_table",
    "enrich_episodes_with_recovery",
    "build_mode_transition_features",
    "build_live_diagnostic",
]


def classify_retry_pattern(gap_days: list[int]) -> str:
    if not gap_days:
        return "single_attempt"
    if max(gap_days) <= 30:
        return "rapid_retry"
    if max(gap_days) <= 70:
        return "medium_retry"
    if min(gap_days) > 100:
        return "long_dormancy"
    if max(gap_days) > 100 and min(gap_days) <= 30:
        return "mixed_fast_and_dormant"
    if max(gap_days) > 100:
        return "slow_retry"
    return "mixed"


def build_episode_table(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()

    rows = []
    for rnd, g0 in events.groupby("round", sort=True):
        g = g0.sort_values("canonical_touch_date").reset_index(drop=True)
        first = g.iloc[0]
        last = g.iloc[-1]
        touch_dates = pd.to_datetime(g["canonical_touch_date"]).tolist()
        gap_days = [(touch_dates[i] - touch_dates[i - 1]).days for i in range(1, len(touch_dates))]

        outcomes = set(g["canonical_outcome"].astype(str))
        if "success" in outcomes:
            episode_outcome = "success"
        elif "pending" in outcomes:
            episode_outcome = "pending"
        else:
            episode_outcome = "fail"

        touch_over = pd.to_numeric(g["canonical_touch_day_overshoot_pct"], errors="coerce")
        max_idx = int(touch_over.idxmax()) if touch_over.notna().any() else None

        rows.append({
            "ticker": first["ticker"],
            "round": int(rnd),
            "first_touch_date": first["canonical_touch_date"],
            "last_touch_date": last["canonical_touch_date"],
            "episode_span_days": int((pd.to_datetime(last["canonical_touch_date"]) - pd.to_datetime(first["canonical_touch_date"])).days),
            "num_attempts": int(len(g)),
            "episode_outcome": episode_outcome,
            "episode_complete": episode_outcome != "pending",
            "first_event_quality": first["canonical_quality"],
            "final_event_quality": last["canonical_quality"],
            "first_touch_day_overshoot": float(first["canonical_touch_day_overshoot_pct"]),
            "max_touch_day_overshoot": float(touch_over.max()) if touch_over.notna().any() else np.nan,
            "touch_day_overshoot_expansion": float(touch_over.max() - first["canonical_touch_day_overshoot_pct"]) if touch_over.notna().any() else np.nan,
            "peak_touch_day_overshoot_attempt_no": int(g.loc[max_idx, "canonical_attempt_no"]) if max_idx is not None else None,
            "touch_gap_days_seq": "; ".join(map(str, gap_days)),
            "touch_gap_transition_seq": "; ".join([f"{i}→{i+1}:{gap_days[i-1]}d" for i in range(1, len(gap_days) + 1)]),
            "retry_pattern": classify_retry_pattern(gap_days),
        })
    return pd.DataFrame(rows)


def build_recovery_table(df_in: pd.DataFrame, events: pd.DataFrame, config: StudyConfig | None = None) -> pd.DataFrame:
    """Build inter-attempt recovery table with volatility-normalised fields."""
    config = config or StudyConfig()
    if events.empty:
        return pd.DataFrame()
    df = add_indicators(df_in, config)
    rows = []
    for rnd, g0 in events.groupby("round", sort=True):
        g = g0.sort_values("canonical_touch_date").reset_index(drop=True)
        if len(g) < 2:
            continue
        for i in range(len(g) - 1):
            a = g.iloc[i]
            b = g.iloc[i + 1]
            loc_a = int(a["canonical_trading_loc"])
            loc_b = int(b["canonical_trading_loc"])
            if loc_b <= loc_a:
                continue
            sl = df.iloc[loc_a:loc_b + 1]
            entry_close = float(df["Close"].iloc[loc_a])
            low_min = float(sl["Low"].min())
            close_min = float(sl["Close"].min())
            below_low = ((sl["Low"] / sl["MA250"]) - 1.0).min() * 100.0
            below_close = ((sl["Close"] / sl["MA250"]) - 1.0).min() * 100.0
            dd_abs_low = abs((low_min / entry_close - 1.0) * 100.0)

            from_atr14_pct = float(df["ATR14_pct"].iloc[loc_a]) if "ATR14_pct" in df.columns else np.nan
            from_hv30 = float(df["HV30"].iloc[loc_a]) if "HV30" in df.columns else np.nan
            from_hv30_daily_pct = float(from_hv30 * 100.0 / np.sqrt(252)) if not pd.isna(from_hv30) else np.nan

            rows.append({
                "ticker": a["ticker"],
                "round": int(rnd),
                "transition": f"{int(a['canonical_attempt_no'])}_to_{int(b['canonical_attempt_no'])}",
                "from_attempt": int(a["canonical_attempt_no"]),
                "to_attempt": int(b["canonical_attempt_no"]),
                "from_date": a["canonical_touch_date"],
                "to_date": b["canonical_touch_date"],
                "gap_days": int((pd.to_datetime(b["canonical_touch_date"]) - pd.to_datetime(a["canonical_touch_date"])).days),
                "trading_days_between": int(loc_b - loc_a),
                "inter_attempt_max_drawdown_low_pct": (low_min / entry_close - 1.0) * 100.0,
                "inter_attempt_max_drawdown_close_pct": (close_min / entry_close - 1.0) * 100.0,
                "drawdown_abs_low_pct": dd_abs_low,
                "below_ma250_depth_low_pct": below_low,
                "below_ma250_depth_close_pct": below_close,
                "below_ma250_abs_low_pct": abs(below_low),
                "from_atr14_pct": from_atr14_pct,
                "from_hv30_annualized": from_hv30,
                "from_hv30_daily_pct": from_hv30_daily_pct,
                "drawdown_atr_multiple": dd_abs_low / from_atr14_pct if from_atr14_pct and not pd.isna(from_atr14_pct) and from_atr14_pct > 0 else np.nan,
                "drawdown_hv30_daily_multiple": dd_abs_low / from_hv30_daily_pct if from_hv30_daily_pct and not pd.isna(from_hv30_daily_pct) and from_hv30_daily_pct > 0 else np.nan,
                "from_touch_day_overshoot": float(a["canonical_touch_day_overshoot_pct"]),
                "to_touch_day_overshoot": float(b["canonical_touch_day_overshoot_pct"]),
                "touch_day_overshoot_delta": float(b["canonical_touch_day_overshoot_pct"] - a["canonical_touch_day_overshoot_pct"]),
                "from_fixed_5d_overshoot": float(a["canonical_touch_window_5d_overshoot_pct"]),
                "to_fixed_5d_overshoot": float(b["canonical_touch_window_5d_overshoot_pct"]),
                "fixed_5d_overshoot_delta": float(b["canonical_touch_window_5d_overshoot_pct"] - a["canonical_touch_window_5d_overshoot_pct"]),
                "next_attempt_success": str(b["canonical_outcome"]) == "success",
                "next_attempt_pending": str(b["canonical_outcome"]) == "pending",
            })
    return pd.DataFrame(rows)


def enrich_episodes_with_recovery(episodes: pd.DataFrame, recovery: pd.DataFrame) -> pd.DataFrame:
    if episodes.empty:
        return episodes
    out = episodes.copy()
    if recovery.empty:
        out["max_completed_drawdown_abs_low_pct"] = np.nan
        out["max_below_ma250_abs_low_pct"] = np.nan
        return out

    agg = recovery.groupby(["ticker", "round"]).agg(
        max_completed_drawdown_abs_low_pct=("drawdown_abs_low_pct", "max"),
        median_completed_drawdown_abs_low_pct=("drawdown_abs_low_pct", "median"),
        max_below_ma250_abs_low_pct=("below_ma250_abs_low_pct", "max"),
        median_gap_days=("gap_days", "median"),
        max_gap_days=("gap_days", "max"),
        median_drawdown_atr_multiple=("drawdown_atr_multiple", "median"),
        max_drawdown_atr_multiple=("drawdown_atr_multiple", "max"),
    ).reset_index()
    return out.merge(agg, on=["ticker", "round"], how="left")


# ---------------------------------------------------------------------------
# Mode-transition prototype scoring
# ---------------------------------------------------------------------------

def score_retry_speed(max_gap: float | None) -> float:
    if pd.isna(max_gap):
        return 0.30
    if max_gap <= 30:
        return 1.00
    if max_gap <= 70:
        return 0.60
    if max_gap <= 100:
        return 0.35
    return 0.10


def score_retry_compression(seq: str, config: StudyConfig | None = None) -> float:
    """Heuristic retry-compression score aligned with V10 semantics."""
    config = config or StudyConfig()
    if not isinstance(seq, str) or not seq:
        return 0.30
    vals = []
    for part in seq.split(";"):
        if ":" in part and part.strip().endswith("d"):
            try:
                vals.append(float(part.split(":")[1].replace("d", "").strip()))
            except Exception:
                pass
    if len(vals) == 0:
        return 0.30
    if len(vals) == 1:
        only = vals[0]
        if only >= config.long_gap_days:
            return 0.10
        if only <= config.short_gap_days:
            return 0.50
        return 0.30
    if vals[-1] < vals[0]:
        ratio = vals[-1] / max(vals[0], 1.0)
        return max(0.0, min(1.0, 1.0 - ratio))
    return 0.0


def score_drawdown_health(dd_abs: float | None) -> float:
    if pd.isna(dd_abs):
        return 0.50
    if dd_abs <= 3:
        return 1.00
    if dd_abs <= 6:
        return 0.70
    if dd_abs <= 10:
        return 0.45
    if dd_abs <= 15:
        return 0.25
    return 0.10


def score_overshoot_expansion(expansion: float | None) -> float:
    if pd.isna(expansion):
        return 0.30
    if expansion <= 0:
        return 0.10
    return max(0.0, min(1.0, expansion / 5.0))


def build_mode_transition_features(episodes: pd.DataFrame, config: StudyConfig | None = None) -> pd.DataFrame:
    config = config or StudyConfig()
    if episodes.empty:
        return pd.DataFrame()
    out = episodes.copy()
    out["retry_speed_score"] = out["max_gap_days"].map(score_retry_speed)
    out["retry_compression_score"] = out["touch_gap_transition_seq"].map(lambda s: score_retry_compression(s, config))
    out["drawdown_health_score"] = out["max_completed_drawdown_abs_low_pct"].map(score_drawdown_health)
    out["below_ma250_health_score"] = out["max_below_ma250_abs_low_pct"].map(score_drawdown_health)
    out["touch_overshoot_expansion_score"] = out["touch_day_overshoot_expansion"].map(score_overshoot_expansion)

    out["trend_following_readiness_prototype"] = (
        0.20 * out["retry_speed_score"]
        + 0.20 * out["retry_compression_score"]
        + 0.20 * out["drawdown_health_score"]
        + 0.15 * out["below_ma250_health_score"]
        + 0.15 * out["touch_overshoot_expansion_score"]
        + 0.10 * np.where(out["num_attempts"] >= 2, 0.70, 0.30)
    ).clip(0, 1)

    def state(x: float) -> str:
        if x >= 0.60:
            return "trend_following_candidate"
        if x >= 0.35:
            return "transition_repair"
        return "reflexive_reversal_or_failed_repair"

    out["mode_transition_state_prototype"] = out["trend_following_readiness_prototype"].map(state)
    return out


def build_live_diagnostic(
    ticker: str,
    df_in: pd.DataFrame,
    events: pd.DataFrame,
    mode_features: pd.DataFrame,
    config: StudyConfig | None = None,
) -> dict[str, Any]:
    config = config or StudyConfig()
    df = add_indicators(df_in, config)
    if events.empty:
        return {
            "ticker": ticker,
            "state": "no_canonical_touch_detected",
            "as_of": date_str(df.index[-1]) if len(df) else None,
        }

    latest = events.sort_values("canonical_touch_date").iloc[-1]
    loc = int(latest["canonical_trading_loc"])
    last_loc = len(df) - 1
    sl = df.iloc[loc:last_loc + 1]

    current_close = float(df["Close"].iloc[-1])
    current_ma250 = float(df["MA250"].iloc[-1])
    current_distance = (current_close / current_ma250 - 1.0) * 100.0 if current_ma250 else np.nan
    dd_low = (sl["Low"].min() / float(df["Close"].iloc[loc]) - 1.0) * 100.0
    dd_close = (sl["Close"].min() / float(df["Close"].iloc[loc]) - 1.0) * 100.0
    below_low = ((sl["Low"] / sl["MA250"]) - 1.0).min() * 100.0
    below_close = ((sl["Close"] / sl["MA250"]) - 1.0).min() * 100.0

    latest_mode = {}
    if not mode_features.empty:
        mf = mode_features[mode_features["round"] == latest["round"]]
        if not mf.empty:
            latest_mode = mf.iloc[-1].to_dict()

    if current_distance >= 0 and str(latest["canonical_outcome"]) == "success":
        state = "accepted_above_yearline"
    elif current_distance >= 0:
        state = "testing_yearline_unconfirmed"
    else:
        state = "below_yearline_after_latest_touch"

    drawdown_bucket = "deep_drawdown" if abs(dd_low) >= config.deep_drawdown_pct else (
        "shallow_drawdown" if abs(dd_low) <= config.shallow_drawdown_pct else "medium_drawdown"
    )

    return {
        "schema_version": "yearline_universe.live_transition.v13",
        "ticker": ticker,
        "as_of": date_str(df.index[-1]),
        "state": state,
        "latest_round": int(latest["round"]),
        "latest_attempt_no": int(latest["canonical_attempt_no"]),
        "latest_touch_date": date_str(latest["canonical_touch_date"]),
        "latest_outcome": str(latest["canonical_outcome"]),
        "latest_quality": str(latest["canonical_quality"]),
        "source_detectors": str(latest["source_detectors"]),
        "days_since_last_touch": int((df.index[-1] - pd.to_datetime(latest["canonical_touch_date"])).days),
        "trading_days_since_last_touch": int(last_loc - loc),
        "current_close": round(current_close, 4),
        "current_ma250": round(current_ma250, 4),
        "current_distance_to_ma250_pct": round(current_distance, 2),
        "current_drawdown_since_last_touch_low_pct": round(dd_low, 2),
        "current_drawdown_since_last_touch_close_pct": round(dd_close, 2),
        "current_below_ma250_depth_low_pct": round(below_low, 2),
        "current_below_ma250_depth_close_pct": round(below_close, 2),
        "drawdown_bucket": drawdown_bucket,
        "trend_following_readiness_prototype": round(float(latest_mode.get("trend_following_readiness_prototype", np.nan)), 3) if latest_mode else None,
        "mode_transition_state_prototype": latest_mode.get("mode_transition_state_prototype") if latest_mode else None,
        "research_warning": "Live diagnostic does not know the future next-touch gap.",
    }
