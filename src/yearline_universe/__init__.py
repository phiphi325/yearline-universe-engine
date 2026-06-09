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
from .blend_surface import (
    build_blend_model,
    apply_blend_live,
    build_blend_context,
    BLEND_SURFACE_VERSION,
)
from .success_labels import (
    build_success_dataset,
    build_empirical_success_reference,
    empirical_success_probability_for_row,
    SUCCESS_STATE_FEATURES,
    SUCCESS_PROB_POLICY,
)
from .success_models import (
    build_success_model_table,
    evaluate_success_models,
    build_and_evaluate_success_models,
    SUCCESS_MODEL_FEATURES,
)
from .success_calibration import (
    success_oof_surfaces,
    evaluate_success_calibration_gate,
    build_and_evaluate_success_calibration,
    SUCCESS_CALIBRATION_VERSION,
)
from .success_reliability import (
    reliability_curve,
    brier_decomposition,
    success_reliability_diagnostic,
    build_success_reliability_diagnostic,
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
    "build_blend_model",
    "apply_blend_live",
    "build_blend_context",
    "BLEND_SURFACE_VERSION",
    "build_success_dataset",
    "build_empirical_success_reference",
    "empirical_success_probability_for_row",
    "SUCCESS_STATE_FEATURES",
    "SUCCESS_PROB_POLICY",
    "build_success_model_table",
    "evaluate_success_models",
    "build_and_evaluate_success_models",
    "SUCCESS_MODEL_FEATURES",
    "success_oof_surfaces",
    "evaluate_success_calibration_gate",
    "build_and_evaluate_success_calibration",
    "SUCCESS_CALIBRATION_VERSION",
    "reliability_curve",
    "brier_decomposition",
    "success_reliability_diagnostic",
    "build_success_reliability_diagnostic",
    "validate_ticker_sanity",
    "validate_reference_parity",
    "ml_feature_leakage_audit",
    "hazard_feature_leakage_audit",
]
