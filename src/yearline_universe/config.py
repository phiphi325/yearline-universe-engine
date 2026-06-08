"""Configuration layer for the V13 universe statistical context engine.

This module defines the ticker-agnostic, universe-first configuration objects
that drive the whole engine, plus the YAML loader.

Design notes
------------
* ``StudyConfig`` is a faithful port of V12's ``YearlineStudyConfig``. Its
  defaults are kept identical to V12 so the MSFT V10 parity regression gate in
  :mod:`yearline_universe.validation` continues to pass. The spec's example
  ``rolling_windows`` (``ma_fast: 50``) is treated as *illustrative*; the
  shipped universe configs preserve V12 values to protect parity. A universe
  config may still override the windows explicitly.
* ``UniverseConfig`` is the top-level unit (universe-first). A single ticker is
  just one ``TickerConfig`` inside it.
* No ticker is special. There is no MSFT default anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

__all__ = [
    "StudyConfig",
    "YearlineStudyConfig",
    "TickerConfig",
    "UniverseConfig",
    "TickerPipelineResult",
    "UniversePipelineResult",
    "load_universe_config",
]

# Default fixed-horizon windows (trading days). Ported verbatim from V12.
DEFAULT_WINDOWS: dict[str, int] = {
    "3d": 3,
    "5d": 5,
    "2w": 10,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "12m": 252,
}


@dataclass(frozen=True)
class StudyConfig:
    """Per-ticker study parameters (yearline detection, episodes, horizons).

    Faithful port of V12 ``YearlineStudyConfig``. Defaults MUST match V12 to
    preserve the parity gate. Override via the universe config when needed.
    """

    # Moving averages
    ma_len: int = 250                  # the "yearline" (MA250)
    ma_fast_len: int = 200             # secondary trend MA (V12 default; not 50)
    atr_len: int = 14                  # forward-compat (spec rolling_windows.atr)

    # Detector parameters
    band: float = 0.01                 # 1% MA250 band for loose detection / prior-below
    lookback_below_days: int = 10
    below_frac: float = 0.60
    new_attempt_gap: int = 5

    # Confirmation / scan logic
    confirm_days: int = 5
    success_hold_days: int = 20
    max_scan_days: int = 90

    # Pending / explicit failure hygiene
    early_fail_scan_days: int = 10
    fail_below_ma_pct: float = 0.02
    fail_consec_days: int = 2

    # Fixed horizons (trading days)
    windows: dict[str, int] | None = None

    # Canonical merge policy
    canonical_touch_merge_trading_days: int = 2
    canonical_date_policy: str = "strict_preferred"
    canonical_cluster_policy: str = "strict_anchor_first"

    # Recovery-matrix buckets
    short_gap_days: int = 30
    long_gap_days: int = 100
    shallow_drawdown_pct: float = 5.0
    deep_drawdown_pct: float = 8.0

    # Data window
    start: str = "2009-01-01"
    # ``end`` exclusive in yfinance. None => rolling "as of latest bar".
    # The parity gate explicitly passes end="2026-06-06" to reproduce V12.
    end: str | None = None
    auto_adjust: bool = True
    use_cache: bool = True

    def __post_init__(self) -> None:
        if self.windows is None:
            object.__setattr__(self, "windows", dict(DEFAULT_WINDOWS))


# Back-compat alias: ported V12 function bodies refer to YearlineStudyConfig.
YearlineStudyConfig = StudyConfig


@dataclass(frozen=True)
class TickerConfig:
    """A single ticker's universe metadata. Mirrors spec section 6."""

    ticker: str
    sector: str
    peer_group: str
    industry: str | None = None
    role: str | None = None
    weight: float | None = None
    asset_type: str = "equity"
    is_etf: bool = False

    def __post_init__(self) -> None:
        if not self.ticker or not str(self.ticker).strip():
            raise ValueError("TickerConfig.ticker is required and must be non-empty")
        if not self.sector or not str(self.sector).strip():
            raise ValueError(f"TickerConfig({self.ticker!r}).sector is required")
        if not self.peer_group or not str(self.peer_group).strip():
            raise ValueError(f"TickerConfig({self.ticker!r}).peer_group is required")
        # normalise ticker symbol
        object.__setattr__(self, "ticker", str(self.ticker).strip().upper())


@dataclass(frozen=True)
class UniverseConfig:
    """Top-level universe configuration. The engine's primary unit."""

    universe_name: str
    benchmark: str | None
    start: str
    replay_start: str
    tickers: tuple[TickerConfig, ...]
    as_of: str | None = None
    study: StudyConfig = field(default_factory=StudyConfig)

    def __post_init__(self) -> None:
        if not self.universe_name:
            raise ValueError("UniverseConfig.universe_name is required")
        if not self.tickers:
            raise ValueError("UniverseConfig.tickers must contain at least one ticker")
        seen: set[str] = set()
        for tc in self.tickers:
            if tc.ticker in seen:
                raise ValueError(f"Duplicate ticker in universe: {tc.ticker}")
            seen.add(tc.ticker)

    # -- convenience accessors -------------------------------------------------
    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(tc.ticker for tc in self.tickers)

    def get_ticker(self, ticker: str) -> TickerConfig:
        key = str(ticker).strip().upper()
        for tc in self.tickers:
            if tc.ticker == key:
                return tc
        raise KeyError(f"{ticker!r} is not configured in universe {self.universe_name!r}")

    def sectors(self) -> tuple[str, ...]:
        return tuple(sorted({tc.sector for tc in self.tickers}))

    def peer_groups(self) -> tuple[str, ...]:
        return tuple(sorted({tc.peer_group for tc in self.tickers}))

    def study_for(self, ticker_config: TickerConfig) -> StudyConfig:
        """Return the StudyConfig to use for a given ticker.

        Currently universe-global; the hook exists so per-ticker / per-sector
        study overrides can be added later without changing call sites.
        """
        return self.study


@dataclass(frozen=True)
class TickerPipelineResult:
    """Output of :func:`run_ticker_pipeline` for one ticker.

    Superset of the spec section 6 contract: adds ``status``/``error`` for
    per-ticker failure isolation (needed by the universe batch runner) and a
    few intermediate frames consumed by pooling/dashboard later.
    """

    ticker: str
    sector: str
    peer_group: str
    status: str = "ok"
    error: str | None = None
    price_df: Any = None                  # pd.DataFrame
    source_attempts: Any = None           # pd.DataFrame
    canonical_events: Any = None          # pd.DataFrame
    episodes: Any = None                  # pd.DataFrame (canonical episodes + recovery)
    recovery_table: Any = None            # pd.DataFrame
    mode_features: Any = None             # pd.DataFrame
    replay_history: Any = None            # pd.DataFrame
    hazard_history: Any = None            # pd.DataFrame
    trend_history: Any = None             # pd.DataFrame
    semantic_history: Any = None          # pd.DataFrame
    live_diagnostic: Mapping[str, Any] = field(default_factory=dict)
    latest_context: Mapping[str, Any] = field(default_factory=dict)
    manifest: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UniversePipelineResult:
    """Output of the universe batch runner (V13.2+). Defined now for stability."""

    universe_name: str
    as_of: str
    ticker_results: Mapping[str, TickerPipelineResult]
    pooled_by_peer_group: Any = None      # pd.DataFrame
    pooled_by_sector: Any = None          # pd.DataFrame
    pooled_by_universe: Any = None        # pd.DataFrame
    universe_context_bundle: Mapping[str, Any] = field(default_factory=dict)
    run_manifest: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

_ROLLING_WINDOW_MAP = {
    "ma_yearline": "ma_len",
    "ma_fast": "ma_fast_len",
    "atr": "atr_len",
}


def _build_study_config(raw: Mapping[str, Any]) -> StudyConfig:
    """Build a StudyConfig from a universe-config mapping.

    Honours ``rolling_windows`` overrides and ``start``/``as_of`` but otherwise
    keeps V12-faithful defaults.
    """
    overrides: dict[str, Any] = {}

    start = raw.get("start")
    if start is not None:
        overrides["start"] = str(start)

    # as_of maps onto the (exclusive) data end. null => rolling latest.
    as_of = raw.get("as_of")
    if as_of:
        overrides["end"] = str(as_of)

    rolling = raw.get("rolling_windows") or {}
    for yaml_key, field_name in _ROLLING_WINDOW_MAP.items():
        if yaml_key in rolling and rolling[yaml_key] is not None:
            overrides[field_name] = int(rolling[yaml_key])

    # Optional explicit study overrides block (advanced use).
    study_overrides = raw.get("study") or {}
    for k, v in study_overrides.items():
        overrides[k] = v

    return StudyConfig(**overrides)


def load_universe_config(path: str | Path) -> UniverseConfig:
    """Load a :class:`UniverseConfig` from a YAML (or JSON) file.

    Raises a clear error if required fields are missing. No ticker is treated
    specially.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Universe config not found: {path}")

    with open(path, "r") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    if "universe_name" not in raw:
        raise ValueError(f"{path}: missing required key 'universe_name'")
    if "tickers" not in raw or not raw["tickers"]:
        raise ValueError(f"{path}: missing required non-empty key 'tickers'")

    study = _build_study_config(raw)

    ticker_cfgs: list[TickerConfig] = []
    for i, entry in enumerate(raw["tickers"]):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}: tickers[{i}] must be a mapping, got {type(entry)}")
        try:
            ticker_cfgs.append(
                TickerConfig(
                    ticker=entry["ticker"],
                    sector=entry["sector"],
                    peer_group=entry["peer_group"],
                    industry=entry.get("industry"),
                    role=entry.get("role"),
                    weight=entry.get("weight"),
                    asset_type=entry.get("asset_type", "equity"),
                    is_etf=bool(entry.get("is_etf", False)),
                )
            )
        except KeyError as exc:
            raise ValueError(
                f"{path}: tickers[{i}] missing required field {exc}"
            ) from exc

    return UniverseConfig(
        universe_name=str(raw["universe_name"]),
        benchmark=raw.get("benchmark"),
        start=str(raw.get("start", study.start)),
        replay_start=str(raw.get("replay_start", "2020-01-01")),
        as_of=(str(raw["as_of"]) if raw.get("as_of") else None),
        tickers=tuple(ticker_cfgs),
        study=study,
    )
