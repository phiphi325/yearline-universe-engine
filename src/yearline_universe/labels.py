"""V13.3 Phase 7 (PR-B) — direct horizon labels + the modeling table.

For each **completed** at-risk repair day, label ``y_H = 1`` if the realised next retry
occurred within ``H`` trading days, else 0 (a *true* negative — the row eventually
retried, just not within H). Live/censored transitions have no observed event, so they
are excluded from training — censoring is leakage-safe by construction (only rows whose
event day is known are labelled).

The table joins, per row:
  * the Phase-7 **path-dynamic features** (`features.py`, leakage-safe),
  * the static repair-state already on the hazard panel (de-correlated: `repair_gap`
    is used, not raw distance + required-rebound),
  * the **empirical completed-path** estimator's leave-one-transition-out prediction
    (the Phase-3/5 baseline) — so the direct classifier (PR-C) can be compared
    head-to-head on identical rows,
  * `transition_key` (the episode id, for episode-aware CV) and `y_10/20/40/60`.

Educational research only.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import StudyConfig
from .hazard import build_hazard_daily_panel, build_empirical_horizon_reference, HAZARD_HORIZONS
from .calibration import build_horizon_calibration_dataset
from .features import build_price_path_features, PATH_FEATURE_COLUMNS
from .cross_sectional import build_cross_sectional_features, CROSS_SECTIONAL_FEATURE_COLUMNS

__all__ = [
    "MODEL_FEATURE_COLUMNS", "MODEL_FEATURE_COLUMNS_WITH_XS",
    "CROSS_SECTIONAL_FEATURE_COLUMNS", "build_direct_horizon_dataset",
]

# Static repair-state features already on the panel (kept de-correlated).
_PANEL_STATE_FEATURES = [
    "trading_days_since_touch", "drawdown_so_far_pct", "below_ma250_depth_so_far_pct",
    "from_touch_day_overshoot_pct", "attempt_no",
]
# Path-dynamic features used for modeling (exclude raw MA levels + required_rebound,
# which is collinear with repair_gap_pct).
_PATH_MODEL_FEATURES = [
    "return_5d", "return_10d", "return_20d",
    "distance_to_ma20_pct", "distance_to_ma50_pct", "price_above_ma20", "price_above_ma50",
    "ma20_change_10d_pct", "ma50_change_20d_pct", "repair_gap_pct",
    "distance_to_ma250_change_5d", "distance_to_ma250_change_10d",
    "distance_to_ma250_change_20d", "distance_to_ma250_slope_10d",
    "realized_vol_20d", "realized_vol_20d_pctile_252d", "range_compression_10d",
]
MODEL_FEATURE_COLUMNS = _PANEL_STATE_FEATURES + _PATH_MODEL_FEATURES
# PR-D: path/static features + cross-sectional (peer/sector/market) regime features.
MODEL_FEATURE_COLUMNS_WITH_XS = MODEL_FEATURE_COLUMNS + CROSS_SECTIONAL_FEATURE_COLUMNS


def build_direct_horizon_dataset(tickers_data: Mapping[str, Mapping[str, Any]],
                                 config: StudyConfig | None = None, horizons=None,
                                 include_cross_sectional: bool = True) -> pd.DataFrame:
    """Build the per-row modeling table (features + y_H + empirical-baseline pred).

    ``tickers_data[ticker]`` = {peer_group, price_df, recovery_table, live_diagnostic}
    (the universe runner's pooled_data). Returns an empty frame if there is no panel.

    ``include_cross_sectional`` (PR-D, default True): also merge the peer/sector/market
    cross-sectional regime features (``CROSS_SECTIONAL_FEATURE_COLUMNS``) by
    (ticker, as_of_date), so a path-only vs path+cross-sectional head-to-head is possible.
    """
    config = config or StudyConfig()
    horizons = [int(h) for h in (horizons or HAZARD_HORIZONS)]
    if not tickers_data:
        return pd.DataFrame()
    panel = build_hazard_daily_panel(tickers_data, next(iter(tickers_data)), config)
    ref = build_empirical_horizon_reference(panel)
    if ref is None or ref.empty:
        return pd.DataFrame()
    ref = ref.copy()
    ref["as_of_date"] = pd.to_datetime(ref["as_of_date"])

    # Path features per ticker, merged by (ticker, as_of_date). Drop any path column
    # already present on the panel-derived reference (only ``required_rebound_to_ma250_pct``
    # collides today) so the merge never produces ``_x``/``_y`` suffixes — none of the
    # collided columns are in MODEL_FEATURE_COLUMNS, so this loses no modeling feature.
    path_cols = [c for c in PATH_FEATURE_COLUMNS if c not in ref.columns]
    feat_parts = []
    for tk, d in tickers_data.items():
        pf = build_price_path_features(d["price_df"], config).copy()
        pf["as_of_date"] = pd.to_datetime(pf.index)
        pf["ticker"] = tk
        feat_parts.append(pf[["as_of_date", "ticker"] + path_cols])
    feats = pd.concat(feat_parts, ignore_index=True)
    df = ref.merge(feats, on=["ticker", "as_of_date"], how="left")

    # Direct horizon labels (completed rows ⇒ remaining is observed ⇒ leakage-safe).
    for h in horizons:
        df[f"y_{h}"] = (df["remaining_trading_days_to_retry"] <= h).astype(int)

    # Empirical completed-path baseline prediction (LOTO), aligned on the same rows.
    emp = build_horizon_calibration_dataset(panel, horizons)
    if not emp.empty:
        emp_cols = ["transition_key", "trading_days_since_touch"] + \
                   [f"pred_retry_within_{h}d" for h in horizons]
        emp_cols = [c for c in emp_cols if c in emp.columns]
        df = df.merge(emp[emp_cols].rename(columns={f"pred_retry_within_{h}d": f"empirical_pred_{h}" for h in horizons}),
                      on=["transition_key", "trading_days_since_touch"], how="left")

    # PR-D: cross-sectional (peer/sector/market) regime features, merged on (ticker, as_of_date).
    if include_cross_sectional:
        xs = build_cross_sectional_features(tickers_data, config)
        if not xs.empty:
            xs = xs.copy()
            xs["as_of_date"] = pd.to_datetime(xs["as_of_date"])
            df = df.merge(xs, on=["ticker", "as_of_date"], how="left")
    return df
