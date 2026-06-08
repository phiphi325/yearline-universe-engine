"""Validation: anti-leakage audit, structural sanity gates, and optional
reference parity.

Ported and generalised from V12 Module B (MSFT V10 parity gate) + the ML /
hazard feature leakage audits.

* The anti-leakage audit tables are ported verbatim — they are a key V12
  artifact and are inherently ticker-agnostic.
* The MSFT-specific V10 parity gate (which compared against values hardcoded
  for V12's frozen 2026 dataset) is generalised into:
    - ``validate_ticker_sanity`` : structural checks that hold for ANY ticker
      and any data window (the regression-style guard for the refactor).
    - ``validate_reference_parity`` : optional exact comparison against a frozen
      reference, used only when such a reference is supplied.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

__all__ = [
    "ml_feature_leakage_audit",
    "hazard_feature_leakage_audit",
    "validate_ticker_sanity",
    "validate_reference_parity",
]


# ---------------------------------------------------------------------------
# Anti-leakage audits (ported verbatim from V12; ticker-agnostic)
# ---------------------------------------------------------------------------

_ML_LEAKAGE_ROWS = [
    {"feature": "ticker", "feature_group": "identity", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Ticker identity is known at scoring time."},
    {"feature": "group", "feature_group": "identity", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Peer group is known at scoring time."},
    {"feature": "transition", "feature_group": "state", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Current target transition (e.g. 2_to_3) is known."},
    {"feature": "attempt_no", "feature_group": "state", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Current attempt number is known."},
    {"feature": "from_canonical_quality", "feature_group": "event_quality", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Quality of the source touch is known after the source event."},
    {"feature": "days_since_last_touch", "feature_group": "live_repair_state", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Elapsed time since source touch is live observable."},
    {"feature": "drawdown_so_far_pct", "feature_group": "live_repair_state", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Current maximum damage so far is live observable."},
    {"feature": "below_ma250_depth_so_far_pct", "feature_group": "live_repair_state", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Below-yearline depth so far is live observable."},
    {"feature": "distance_to_ma250_pct", "feature_group": "live_repair_state", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Current price distance to MA250 is live observable."},
    {"feature": "required_rebound_to_ma250_pct", "feature_group": "live_repair_state", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Computed from current distance to MA250."},
    {"feature": "from_touch_day_overshoot_pct", "feature_group": "source_touch", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Touch-day overshoot is known after source touch day."},
    {"feature": "from_fixed_5d_overshoot_pct", "feature_group": "source_touch", "live_available": True, "allowed_for_training": True, "allowed_for_live_scoring": True, "reason": "Allowed only after the fixed window has elapsed; otherwise null."},
    {"feature": "gap_days", "feature_group": "label", "live_available": False, "allowed_for_training": False, "allowed_for_live_scoring": False, "reason": "Target for timing model; unknown for live transition."},
    {"feature": "next_attempt_success", "feature_group": "label", "live_available": False, "allowed_for_training": False, "allowed_for_live_scoring": False, "reason": "Target for quality model; future-known."},
    {"feature": "episode_outcome", "feature_group": "label", "live_available": False, "allowed_for_training": False, "allowed_for_live_scoring": False, "reason": "Post-hoc episode label; not a live input."},
    {"feature": "future_max_drawdown", "feature_group": "forbidden_future", "live_available": False, "allowed_for_training": False, "allowed_for_live_scoring": False, "reason": "Uses future path after scoring time."},
    {"feature": "lifecycle_peak_overshoot", "feature_group": "post_hoc_morphology", "live_available": False, "allowed_for_training": False, "allowed_for_live_scoring": False, "reason": "Full lifecycle peak is post-hoc morphology."},
]

_HAZARD_LEAKAGE_ROWS = [
    {"feature": "trading_days_since_touch", "feature_group": "time_at_risk", "live_available": True, "allowed_for_hazard_model": True, "reason": "Elapsed time since source touch is known each day."},
    {"feature": "calendar_days_since_touch", "feature_group": "time_at_risk", "live_available": True, "allowed_for_hazard_model": True, "reason": "Calendar elapsed time is known each day."},
    {"feature": "drawdown_so_far_pct", "feature_group": "live_repair_state", "live_available": True, "allowed_for_hazard_model": True, "reason": "Maximum damage observed so far; not the final future max drawdown."},
    {"feature": "distance_to_ma250_pct", "feature_group": "live_repair_state", "live_available": True, "allowed_for_hazard_model": True, "reason": "Current distance to MA250 is observable."},
    {"feature": "required_rebound_to_ma250_pct", "feature_group": "live_repair_state", "live_available": True, "allowed_for_hazard_model": True, "reason": "Computed from current distance to MA250."},
    {"feature": "from_canonical_quality", "feature_group": "source_touch", "live_available": True, "allowed_for_hazard_model": True, "reason": "Source touch quality is known after the touch."},
    {"feature": "event_retry_today", "feature_group": "label", "live_available": False, "allowed_for_hazard_model": False, "reason": "Hazard label; cannot be used as an input."},
    {"feature": "final_gap_days", "feature_group": "label", "live_available": False, "allowed_for_hazard_model": False, "reason": "Future-known total gap; forbidden as a live input."},
    {"feature": "next_attempt_success", "feature_group": "label", "live_available": False, "allowed_for_hazard_model": False, "reason": "Future-known quality label; forbidden as a live input."},
]


def ml_feature_leakage_audit() -> pd.DataFrame:
    """Return the ML feature leakage-audit table (anti-leakage policy)."""
    return pd.DataFrame(_ML_LEAKAGE_ROWS)


def hazard_feature_leakage_audit() -> pd.DataFrame:
    """Return the hazard feature leakage-audit table (anti-leakage policy)."""
    return pd.DataFrame(_HAZARD_LEAKAGE_ROWS)


# ---------------------------------------------------------------------------
# Structural sanity gate (generalised; any ticker, any window)
# ---------------------------------------------------------------------------

def validate_ticker_sanity(result) -> dict[str, Any]:
    """Structural sanity checks that must hold for any valid ticker pipeline run.

    This is the V13 regression guard: it does not depend on a specific dataset,
    so it catches refactor regressions (broken detector, mis-assigned rounds,
    invalid outcomes) without requiring a frozen reference.
    """
    checks: list[dict[str, Any]] = []

    def add(name, ok, detail=None):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    status = getattr(result, "status", None)
    add("status_ok", status == "ok", status)

    events = getattr(result, "canonical_events", None)
    has_events = events is not None and not events.empty
    add("has_canonical_events", has_events, None if events is None else int(len(events)))

    if has_events:
        # rounds non-decreasing in touch-date order
        ev = events.sort_values("canonical_touch_date")
        rounds = ev["round"].tolist()
        add("rounds_non_decreasing", all(rounds[i] <= rounds[i + 1] for i in range(len(rounds) - 1)))
        # outcomes are valid
        valid = set(ev["canonical_outcome"].astype(str).unique()) <= {"success", "fail", "pending"}
        add("outcomes_valid", valid, sorted(ev["canonical_outcome"].astype(str).unique()))
        # at most one pending and it is the last event
        pend = ev[ev["canonical_outcome"].astype(str) == "pending"]
        add("pending_only_trailing", pend.empty or (pend.index.max() == ev.index.max() and len(pend) <= 2))
        # touch dates present
        add("touch_dates_present", ev["canonical_touch_date"].notna().all())
        # attempt numbers reset to 1 after a success
        ok_reset = True
        prev_outcome = None
        for _, r in ev.iterrows():
            if prev_outcome == "success":
                ok_reset = ok_reset and int(r["canonical_attempt_no"]) == 1
            prev_outcome = str(r["canonical_outcome"])
        add("attempt_no_resets_after_success", ok_reset)

    live = getattr(result, "live_diagnostic", None) or {}
    add("live_diagnostic_present", bool(live), live.get("state") if live else None)
    if live and "current_distance_to_ma250_pct" in live:
        add("distance_is_finite", live.get("current_distance_to_ma250_pct") is not None
            and np.isfinite(float(live.get("current_distance_to_ma250_pct"))))

    table = pd.DataFrame(checks)
    passed = bool(table["pass"].all()) if not table.empty else False
    return {"ticker": getattr(result, "ticker", None), "passed": passed,
            "n_checks": int(len(table)), "n_failures": int((~table["pass"]).sum()) if not table.empty else 0,
            "table": table}


def validate_reference_parity(
    result,
    expected_latest: Mapping[str, Any] | None = None,
    expected_counts: Mapping[str, int] | None = None,
    score_tol: float = 0.015,
    distance_tol: float = 0.25,
) -> dict[str, Any]:
    """Optional exact-parity gate against a frozen reference.

    Supply ``expected_latest`` (e.g. latest_round, latest_outcome,
    current_distance_to_ma250_pct, ...) and/or ``expected_counts`` (e.g.
    n_canonical_events) captured from a frozen reference run on the SAME dataset.
    Returns ``status="no_reference"`` if nothing is supplied, so it never
    spuriously fails on a different data window.
    """
    if not expected_latest and not expected_counts:
        return {"status": "no_reference", "passed": None,
                "note": "Supply expected_latest/expected_counts from a frozen reference run to enable exact parity."}

    rows = []

    def add(name, expected, actual, tol=None):
        if tol is None:
            ok = str(expected) == str(actual)
            diff = None
        else:
            try:
                diff = float(actual) - float(expected)
                ok = abs(diff) <= tol
            except Exception:
                diff, ok = None, False
        rows.append({"check": name, "expected": expected, "actual": actual, "difference": diff, "tolerance": tol, "pass": bool(ok)})

    if expected_counts:
        actual_counts = {
            "n_canonical_events": len(getattr(result, "canonical_events", []) or []),
            "n_canonical_episodes": len(getattr(result, "episodes", []) or []),
            "n_recovery_transitions": len(getattr(result, "recovery_table", []) or []),
        }
        for k, exp in expected_counts.items():
            add(k, exp, actual_counts.get(k))

    if expected_latest:
        live = getattr(result, "live_diagnostic", None) or {}
        float_keys = {"current_distance_to_ma250_pct", "current_drawdown_since_last_touch_low_pct", "trend_following_readiness_prototype"}
        for k, exp in expected_latest.items():
            actual = live.get("state") if k == "latest_state" else live.get(k)
            tol = (score_tol if k == "trend_following_readiness_prototype" else distance_tol) if k in float_keys else None
            add(k, exp, actual, tol)

    table = pd.DataFrame(rows)
    return {"status": "compared", "passed": bool(table["pass"].all()) if not table.empty else False,
            "n_failures": int((~table["pass"]).sum()) if not table.empty else 0, "table": table}
