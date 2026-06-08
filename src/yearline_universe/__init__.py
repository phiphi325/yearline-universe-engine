"""yearline_universe — V13 universe statistical context engine.

Ticker-agnostic, universe-first, sector-aware MA250 / yearline repair-trend
statistical context engine. The notebook orchestrates; all logic lives here.
"""

from __future__ import annotations

__version__ = "13.1.0"

from .config import (
    StudyConfig,
    TickerConfig,
    UniverseConfig,
    TickerPipelineResult,
    UniversePipelineResult,
    load_universe_config,
)
from .ticker_pipeline import run_ticker_pipeline, run_universe_pipeline
from .context_export import (
    build_statistical_context_envelope,
    export_single_ticker_context,
    export_universe_context_bundle,
    STATISTICAL_CONTEXT_JSON_SCHEMA,
    make_json_safe,
)
from .timing import (
    build_retry_timing_context,
    build_estimator_comparison,
    build_live_retry_setup,
)
from .calibration import (
    build_calibration_context,
    build_horizon_calibration_dataset,
    horizon_calibration_metrics,
)
from .features import (
    build_price_path_features,
    repair_path_features_at,
    PATH_FEATURE_COLUMNS,
)
from .validation import (
    validate_ticker_sanity,
    validate_reference_parity,
    ml_feature_leakage_audit,
    hazard_feature_leakage_audit,
)

__all__ = [
    "__version__",
    "StudyConfig",
    "TickerConfig",
    "UniverseConfig",
    "TickerPipelineResult",
    "UniversePipelineResult",
    "load_universe_config",
    "run_ticker_pipeline",
    "run_universe_pipeline",
    "build_statistical_context_envelope",
    "export_single_ticker_context",
    "export_universe_context_bundle",
    "STATISTICAL_CONTEXT_JSON_SCHEMA",
    "make_json_safe",
    "build_retry_timing_context",
    "build_estimator_comparison",
    "build_live_retry_setup",
    "build_calibration_context",
    "build_horizon_calibration_dataset",
    "horizon_calibration_metrics",
    "build_price_path_features",
    "repair_path_features_at",
    "PATH_FEATURE_COLUMNS",
    "validate_ticker_sanity",
    "validate_reference_parity",
    "ml_feature_leakage_audit",
    "hazard_feature_leakage_audit",
]
