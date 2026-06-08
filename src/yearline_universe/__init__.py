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
from .cross_sectional import (
    build_cross_sectional_features,
    CROSS_SECTIONAL_FEATURE_COLUMNS,
)
from .labels import (
    build_direct_horizon_dataset,
    MODEL_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS_WITH_XS,
)
from .models import (
    fit_direct_horizon_models,
    evaluate_direct_horizon_models,
    build_and_evaluate_direct_horizon_models,
    compare_feature_sets,
    build_and_compare_cross_sectional,
    DIRECT_MODEL_VERSION,
)
from .generalization import (
    evaluate_generalization,
    build_and_evaluate_generalization,
    episode_row_weights,
    calibration_metrics,
    GENERALIZATION_VERSION,
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
    "build_cross_sectional_features",
    "CROSS_SECTIONAL_FEATURE_COLUMNS",
    "build_direct_horizon_dataset",
    "MODEL_FEATURE_COLUMNS",
    "MODEL_FEATURE_COLUMNS_WITH_XS",
    "fit_direct_horizon_models",
    "evaluate_direct_horizon_models",
    "build_and_evaluate_direct_horizon_models",
    "compare_feature_sets",
    "build_and_compare_cross_sectional",
    "DIRECT_MODEL_VERSION",
    "evaluate_generalization",
    "build_and_evaluate_generalization",
    "episode_row_weights",
    "calibration_metrics",
    "GENERALIZATION_VERSION",
    "validate_ticker_sanity",
    "validate_reference_parity",
    "ml_feature_leakage_audit",
    "hazard_feature_leakage_audit",
]
