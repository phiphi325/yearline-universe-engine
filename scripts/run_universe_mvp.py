"""V13.0 + V13.1 (+ V13.2 stretch) MVP runner.

Loads a universe config, runs every ticker through the identical
``run_ticker_pipeline`` code path via ``run_universe_pipeline``, and writes:

* exports/ticker_contexts/{TICKER}_statistical_context.json  (per-ticker envelope)
* exports/universe_contexts/{universe}_bundle.json           (universe bundle)
* exports/universe_contexts/{universe}_run_manifest.json     (run manifest)
* exports/reports/statistical_context_schema.json            (JSON schema)
* exports/reports/{ml,hazard}_feature_leakage_audit.csv      (anti-leakage policy)

Usage:
    python scripts/run_universe_mvp.py [config.yaml] [--provider auto|cache]
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from yearline_universe import (  # noqa: E402
    load_universe_config, run_universe_pipeline, export_single_ticker_context,
    validate_ticker_sanity, STATISTICAL_CONTEXT_JSON_SCHEMA,
    ml_feature_leakage_audit, hazard_feature_leakage_audit, make_json_safe,
)
from yearline_universe.context_export import write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default=str(REPO / "config" / "universe_mega_cap_ai_infra.yaml"))
    ap.add_argument("--provider", default="cache", choices=["auto", "cache", "yfinance", "yahoo_chart"])
    ap.add_argument("--cache-dir", default=str(REPO / "data" / "price_cache"))
    ap.add_argument("--n-jobs", type=int, default=1,
                    help="parallel workers across tickers (1=serial, <=0=all cores)")
    ap.add_argument("--incremental", action="store_true",
                    help="use the persistent daily replay cache (only score new bars)")
    ap.add_argument("--state-dir", default=str(REPO / "data" / "replay_state"),
                    help="directory for the incremental replay cache")
    ap.add_argument("--fit-ml-models", action="store_true",
                    help="also fit the prototype retry-timing/quality ML models "
                         "(slower on high-event tickers; not used by the envelope)")
    ap.add_argument("--calibrate", action="store_true",
                    help="run V13.7 horizon calibration of the empirical estimator "
                         "(purged LOTO + isotonic + trust gate; rescans the panel, slower)")
    ap.add_argument("--pool-hazard", action="store_true",
                    help="V13.3 Phase 5: pool the universe for hazard/empirical-reference/calibration "
                         "(state-conditioned scopes discriminate; lifts AUC toward the trust gate)")
    args = ap.parse_args()

    exports = REPO / "exports"
    tdir = exports / "ticker_contexts"
    udir = exports / "universe_contexts"
    rdir = exports / "reports"
    for d in (tdir, udir, rdir):
        d.mkdir(parents=True, exist_ok=True)

    uni = load_universe_config(args.config)
    print(f"Universe: {uni.universe_name} | tickers={list(uni.symbols)} | provider={args.provider} "
          f"| n_jobs={args.n_jobs} | incremental={args.incremental}")

    result = run_universe_pipeline(
        uni, cache_dir=args.cache_dir, provider=args.provider, n_jobs=args.n_jobs,
        incremental=args.incremental, state_dir=(args.state_dir if args.incremental else None),
        fit_ml_models=args.fit_ml_models, calibrate=args.calibrate, pool_hazard=args.pool_hazard,
    )
    if args.incremental:
        for t, r in result.ticker_results.items():
            if r.status == "ok":
                print(f"    {t} replay_mode={r.manifest.get('replay_mode')}")

    # Per-ticker envelopes + sanity.
    print("\nPer-ticker results:")
    for ticker, res in result.ticker_results.items():
        if res.status == "ok":
            env = export_single_ticker_context(res)
            write_json(env, tdir / f"{ticker}_statistical_context.json")
            san = validate_ticker_sanity(res)
            aec = env.get("active_engine_context", {})
            print(f"  {ticker:6} OK   events={res.manifest.get('n_canonical_events'):>3} "
                  f"engine={aec.get('active_engine'):28} as_of={env.get('as_of')} "
                  f"sanity={'PASS' if san['passed'] else 'FAIL'}")
        else:
            print(f"  {ticker:6} FAIL {res.error}")

    # Universe bundle + manifest.
    write_json(result.universe_context_bundle, udir / f"{uni.universe_name}_bundle.json")
    write_json(result.run_manifest, udir / f"{uni.universe_name}_run_manifest.json")

    # Repo artifacts: schema + leakage audits.
    write_json(STATISTICAL_CONTEXT_JSON_SCHEMA, rdir / "statistical_context_schema.json")
    ml_feature_leakage_audit().to_csv(rdir / "ml_feature_leakage_audit.csv", index=False)
    hazard_feature_leakage_audit().to_csv(rdir / "hazard_feature_leakage_audit.csv", index=False)

    m = result.run_manifest
    print(f"\nRun manifest: {m['n_ok']}/{m['n_tickers']} ok, {m['n_failed']} failed | as_of={m['as_of']}")
    print(f"Exports written under: {exports}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
