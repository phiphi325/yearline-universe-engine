"""Peer-group / sector / universe pooling (V13.3).

Two layers:
  * ``build_pooled_context`` (V13.1) — lightweight per-group counts/success-rate.
  * V13.3 pooled EVIDENCE — faithful, de-globalised port of V12 Module B:
      - gap-by-drawdown matrix (drawdown_bucket x gap_bucket -> counts, median gap,
        next-attempt success rate + Wilson interval, interpretation label),
      - Spearman correlation of inter-attempt drawdown vs gap-days (bootstrap CI),
      - attempt-success classification by attempt bucket (and source-touch quality),
    computed at peer_group / sector / universe levels.

This is DESCRIPTIVE historical evidence (not a forward forecast): every output
carries sample sizes, and correlations with n < 5 are suppressed. Evidence overlay
only; not financial advice.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .indicators import sample_quality

try:
    from scipy.stats import spearmanr, pearsonr
except Exception:  # pragma: no cover
    spearmanr = pearsonr = None

__all__ = [
    "build_pooled_context",
    "build_pooled_evidence",
    "build_gap_drawdown_matrix",
    "build_gap_drawdown_corr_summary",
    "build_pooled_attempt_success",
    "wilson_interval",
]

RANDOM_SEED = 42
GroupBy = Literal["peer_group", "sector", "universe"]


# ---------------------------------------------------------------------------
# V13.1 lightweight summary (unchanged — keeps existing callers/tests stable)
# ---------------------------------------------------------------------------

def _group_key(result, group_by: GroupBy) -> str:
    if group_by == "peer_group":
        return getattr(result, "peer_group", "unknown")
    if group_by == "sector":
        return getattr(result, "sector", "unknown")
    return "ALL"


def build_pooled_context(ticker_results: Mapping[str, object], group_by: GroupBy = "peer_group") -> pd.DataFrame:
    """Basic per-group counts + episode success rate + active-engine mix (V13.1)."""
    rows: dict[str, dict] = {}
    for ticker, res in ticker_results.items():
        if getattr(res, "status", None) != "ok":
            continue
        key = _group_key(res, group_by)
        b = rows.setdefault(key, {"group_by": group_by, "group": key, "n_tickers": 0,
                                  "n_canonical_events": 0, "n_episodes": 0, "n_success_episodes": 0,
                                  "active_repair": 0, "active_trend": 0, "active_other": 0, "_tickers": []})
        b["n_tickers"] += 1
        b["_tickers"].append(ticker)
        events = getattr(res, "canonical_events", None)
        episodes = getattr(res, "episodes", None)
        if events is not None and not events.empty:
            b["n_canonical_events"] += int(len(events))
        if episodes is not None and not episodes.empty:
            b["n_episodes"] += int(len(episodes))
            if "episode_outcome" in episodes.columns:
                b["n_success_episodes"] += int((episodes["episode_outcome"].astype(str) == "success").sum())
        engine = (getattr(res, "latest_context", {}) or {}).get("active_engine_context", {}).get("active_engine")
        if engine == "repair_retry_hazard_engine":
            b["active_repair"] += 1
        elif engine == "post_confirmation_trend_engine":
            b["active_trend"] += 1
        else:
            b["active_other"] += 1
    out_rows = []
    for b in rows.values():
        n_epi = b["n_episodes"]
        out_rows.append({
            "group_by": b["group_by"], "group": b["group"], "n_tickers": b["n_tickers"],
            "tickers": ",".join(sorted(b["_tickers"])), "n_canonical_events": b["n_canonical_events"],
            "n_episodes": n_epi, "episode_success_rate": (b["n_success_episodes"] / n_epi) if n_epi else np.nan,
            "n_active_repair_engine": b["active_repair"], "n_active_trend_engine": b["active_trend"],
            "n_active_other": b["active_other"],
            "pooled_metrics_status": "v13_1_basic_counts__rich_evidence_in_build_pooled_evidence",
        })
    return pd.DataFrame(out_rows).sort_values(["group_by", "group"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# V13.3 statistics (ported from V12 Module B)
# ---------------------------------------------------------------------------

def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (np.nan, np.nan)
    p = successes / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = z * np.sqrt((p * (1 - p) + z**2 / (4 * n)) / n) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def beta_binomial_summary(successes: int, n: int, alpha: float = 1.0, beta: float = 1.0) -> dict[str, float]:
    if n <= 0:
        return {"raw_rate": np.nan, "posterior_mean": np.nan, "wilson_low": np.nan, "wilson_high": np.nan}
    lo, hi = wilson_interval(successes, n)
    return {"raw_rate": successes / n, "posterior_mean": (successes + alpha) / (n + alpha + beta),
            "wilson_low": lo, "wilson_high": hi}


def _attempt_bucket_series(s: pd.Series) -> pd.Series:
    return pd.Series(np.where(s >= 3, "3+", s.astype(int).astype(str)), index=s.index)


def gap_bucket(gap_days: float, config: StudyConfig) -> str:
    if pd.isna(gap_days):
        return "unknown_gap"
    if gap_days <= config.short_gap_days:
        return "short_gap"
    if gap_days >= config.long_gap_days:
        return "long_gap"
    return "medium_gap"


def drawdown_bucket(dd_abs: float, config: StudyConfig) -> str:
    if pd.isna(dd_abs):
        return "unknown_drawdown"
    if dd_abs <= config.shallow_drawdown_pct:
        return "shallow_drawdown"
    if dd_abs >= config.deep_drawdown_pct:
        return "deep_drawdown"
    return "medium_drawdown"


def matrix_interpretation(bucket: str) -> str:
    mapping = {
        "short_gap__shallow_drawdown": "healthy_absorption__trend_readiness_up",
        "short_gap__deep_drawdown": "volatile_squeeze__needs_confirmation",
        "medium_gap__shallow_drawdown": "orderly_repair__watch_for_retry",
        "medium_gap__deep_drawdown": "damaged_repair__defensive_transition",
        "long_gap__deep_drawdown": "structural_damage_or_long_dormancy__trend_readiness_down",
        "long_gap__shallow_drawdown": "slow_absorption__needs_peer_context",
    }
    return mapping.get(bucket, "mixed_or_medium_repair")


def _bucket_recovery(df: pd.DataFrame, config: StudyConfig) -> pd.DataFrame:
    out = df.copy()
    out["gap_bucket"] = out["gap_days"].map(lambda x: gap_bucket(x, config))
    out["drawdown_bucket"] = out["drawdown_abs_low_pct"].map(lambda x: drawdown_bucket(x, config))
    out["matrix_bucket"] = out["gap_bucket"] + "__" + out["drawdown_bucket"]
    return out


def build_gap_drawdown_matrix(recovery: pd.DataFrame, config: StudyConfig | None = None) -> pd.DataFrame:
    """drawdown x gap bucket -> counts, median gap/DD, next-attempt success + Wilson interval."""
    config = config or StudyConfig()
    if recovery.empty:
        return pd.DataFrame()
    df = _bucket_recovery(recovery, config)
    rows = []

    def _emit(group_label, g):
        successes = int(g["next_attempt_success"].sum())
        pending = int(g["next_attempt_pending"].sum()) if "next_attempt_pending" in g.columns else 0
        completed_n = int(len(g) - pending)
        summ = beta_binomial_summary(successes, completed_n)
        rows.append({
            "group": group_label, "matrix_bucket": g["matrix_bucket"].iloc[0],
            "interpretation": matrix_interpretation(g["matrix_bucket"].iloc[0]),
            "n": int(len(g)), "completed_n": completed_n, "successes": successes, "pending": pending,
            "sample_quality": sample_quality(len(g)),
            "median_gap_days": float(g["gap_days"].median()),
            "median_drawdown_abs_low_pct": float(g["drawdown_abs_low_pct"].median()),
            "median_below_ma250_abs_low_pct": float(g["below_ma250_abs_low_pct"].median()) if "below_ma250_abs_low_pct" in g.columns else np.nan,
            "next_attempt_success_rate": summ["raw_rate"], "next_attempt_success_posterior": summ["posterior_mean"],
            "next_attempt_success_wilson_low": summ["wilson_low"], "next_attempt_success_wilson_high": summ["wilson_high"],
        })

    for (group_label, _bucket), g in df.groupby(["group", "matrix_bucket"], dropna=False):
        _emit(group_label, g)
    for _bucket, g in df.groupby("matrix_bucket", dropna=False):  # universe ("ALL")
        gg = g.copy(); gg["group"] = "ALL"
        _emit("ALL", gg)
    return pd.DataFrame(rows).sort_values(["group", "matrix_bucket"]).reset_index(drop=True)


def _bootstrap_spearman_ci(x, y, n_boot=500, seed=RANDOM_SEED):
    if spearmanr is None or len(x) < 5:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    vals = []
    n = len(x)
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        try:
            c = spearmanr(x[idx], y[idx], nan_policy="omit").correlation
            if not pd.isna(c):
                vals.append(c)
        except Exception:
            pass
    if not vals:
        return (np.nan, np.nan)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def _corr_pair(x: pd.Series, y: pd.Series):
    valid = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(valid) < 5:
        return (np.nan, np.nan, np.nan, np.nan)
    sx, sy = valid["x"].to_numpy(float), valid["y"].to_numpy(float)
    sp = spearmanr(sx, sy).correlation if spearmanr is not None else np.nan
    pr = pearsonr(sx, sy)[0] if pearsonr is not None else np.nan
    lo, hi = _bootstrap_spearman_ci(sx, sy)
    return (float(sp), float(lo), float(hi), float(pr))


def build_gap_drawdown_corr_summary(recovery: pd.DataFrame) -> pd.DataFrame:
    """Spearman(drawdown_abs_low_pct, gap_days) by group x transition, with bootstrap CI."""
    if recovery.empty:
        return pd.DataFrame()
    rows = []
    groups = list(recovery["group"].dropna().unique()) + ["ALL"]
    for group in groups:
        df = recovery if group == "ALL" else recovery[recovery["group"] == group]
        transitions = sorted(df["transition"].dropna().unique().tolist()) + ["all_transitions"]
        for trans in transitions:
            g = df if trans == "all_transitions" else df[df["transition"] == trans]
            n = len(g)
            if n < 5:
                rows.append({"group": group, "transition": trans, "n": n, "sample_quality": sample_quality(n),
                             "correlation_status": "suppressed_n_lt_5", "spearman_gap_vs_drawdown": np.nan,
                             "spearman_ci95_low": np.nan, "spearman_ci95_high": np.nan, "pearson_gap_vs_drawdown": np.nan})
            else:
                sp, lo, hi, pr = _corr_pair(g["gap_days"], g["drawdown_abs_low_pct"])
                rows.append({"group": group, "transition": trans, "n": n, "sample_quality": sample_quality(n),
                             "correlation_status": "computed", "spearman_gap_vs_drawdown": sp,
                             "spearman_ci95_low": lo, "spearman_ci95_high": hi, "pearson_gap_vs_drawdown": pr})
    return pd.DataFrame(rows)


def build_pooled_attempt_success(events: pd.DataFrame) -> pd.DataFrame:
    """Attempt success rate by group x attempt bucket (Wilson + Beta(1,1))."""
    if events.empty:
        return pd.DataFrame()
    df = events.copy()
    df["attempt_bucket"] = _attempt_bucket_series(df["canonical_attempt_no"])
    rows = []

    def _emit(group_label, attempt_bucket, g):
        n = len(g)
        successes = int((g["canonical_outcome"] == "success").sum())
        pending = int((g["canonical_outcome"] == "pending").sum())
        summ = beta_binomial_summary(successes, n)
        rows.append({"group": group_label, "attempt_bucket": str(attempt_bucket), "n": n,
                     "successes": successes, "pending": pending, "raw_success_rate": summ["raw_rate"],
                     "posterior_mean_beta_1_1": summ["posterior_mean"], "wilson_low": summ["wilson_low"],
                     "wilson_high": summ["wilson_high"], "sample_quality": sample_quality(n)})

    for (group_label, attempt_bucket), g in df.groupby(["group", "attempt_bucket"], dropna=False):
        _emit(group_label, attempt_bucket, g)
    for attempt_bucket, g in df.groupby("attempt_bucket", dropna=False):
        _emit("ALL", attempt_bucket, g)
    return pd.DataFrame(rows).sort_values(["group", "attempt_bucket"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Orchestration: assemble pooled evidence at all levels for the bundle
# ---------------------------------------------------------------------------

def _pool_frames(ticker_results: Mapping[str, object]):
    rec_parts, ev_parts = [], []
    n_ok = 0
    for ticker, res in ticker_results.items():
        if getattr(res, "status", None) != "ok":
            continue
        n_ok += 1
        sector = getattr(res, "sector", "unknown")
        peer = getattr(res, "peer_group", "unknown")
        rec = getattr(res, "recovery_table", None)
        if rec is not None and not rec.empty:
            r = rec.copy(); r["sector"] = sector; r["peer_group"] = peer
            rec_parts.append(r)
        ev = getattr(res, "canonical_events", None)
        if ev is not None and not ev.empty:
            e = ev.copy(); e["sector"] = sector; e["peer_group"] = peer
            ev_parts.append(e)
    recovery = pd.concat(rec_parts, ignore_index=True) if rec_parts else pd.DataFrame()
    events = pd.concat(ev_parts, ignore_index=True) if ev_parts else pd.DataFrame()
    return recovery, events, n_ok


def _records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.replace({np.nan: None}).to_dict("records")


def build_pooled_evidence(ticker_results: Mapping[str, object], config: StudyConfig | None = None) -> dict[str, Any]:
    """Full V13.3 pooled evidence at peer_group / sector / universe levels."""
    config = config or StudyConfig()
    recovery, events, n_ok = _pool_frames(ticker_results)

    def for_dim(dim: str):
        rec = recovery.copy(); ev = events.copy()
        if not rec.empty:
            rec["group"] = rec[dim]
        if not ev.empty:
            ev["group"] = ev[dim]
        return (build_gap_drawdown_corr_summary(rec), build_gap_drawdown_matrix(rec, config),
                build_pooled_attempt_success(ev))

    peer_corr, peer_mtx, peer_att = for_dim("peer_group")
    sect_corr, sect_mtx, sect_att = for_dim("sector")

    def level(corr, mtx, att, keep_all: bool):
        sel = (lambda d: d[d["group"] == "ALL"]) if keep_all else (lambda d: d[d["group"] != "ALL"])
        return {
            "correlation": _records(sel(corr) if not corr.empty else corr),
            "gap_drawdown_matrix": _records(sel(mtx) if not mtx.empty else mtx),
            "attempt_success": _records(sel(att) if not att.empty else att),
        }

    # headline: universe all-transitions Spearman
    headline = {}
    if not peer_corr.empty:
        u = peer_corr[(peer_corr["group"] == "ALL") & (peer_corr["transition"] == "all_transitions")]
        if not u.empty:
            r = u.iloc[0]
            headline = {"metric": "spearman_drawdown_vs_days_to_next_touch", "scope": "universe/all_transitions",
                        "spearman": r["spearman_gap_vs_drawdown"], "ci95": [r["spearman_ci95_low"], r["spearman_ci95_high"]],
                        "n": int(r["n"]), "status": r["correlation_status"]}

    return {
        "schema": "v13_pooled_gap_drawdown_evidence",
        "is_descriptive_evidence_not_forecast": True,
        "n_tickers_ok": n_ok,
        "n_recovery_transitions": int(len(recovery)),
        "n_canonical_events": int(len(events)),
        "headline_correlation": headline,
        "peer_group": level(peer_corr, peer_mtx, peer_att, keep_all=False),
        "sector": level(sect_corr, sect_mtx, sect_att, keep_all=False),
        "universe": level(peer_corr, peer_mtx, peer_att, keep_all=True),
        "disclaimers": [
            "Descriptive historical evidence, not a forward forecast.",
            "Correlations with n<5 are suppressed; small buckets are statistically fragile.",
            "Not financial advice; evidence overlay only.",
        ],
    }
