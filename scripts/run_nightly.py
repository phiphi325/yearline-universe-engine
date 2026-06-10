"""Nightly producer — refresh data, run the pooled pipeline, and publish the option-mgmt artifacts.

For each universe ticker it writes (keyed ``{ticker}_{as_of}`` ⇒ idempotent):
  * ``yearline_context_{TICKER}_{as_of}.json``        (the gated decision contract; adapter.export_yearline_context)
  * ``yearline_trend_series_{TICKER}_{as_of}.json``   (the trend-plot series;       adapter.export_yearline_trend_series)
plus a universe-level ``yearline_run_status_{run_date}.json`` (available true/false + provenance).

Market-calendar guard: a **pre-flight** checks whether the freshest bar has advanced to the last
*completed* trading session (NYSE-approx). On a **no-new-bar day** (weekend/holiday/data lag) it writes
an ``available:false`` status and exits 0 **without** the heavy run — unless ``--force``.

Retry/backoff wraps every live fetch (cloud-runner throttling / transient network).

Data source: ``--provider`` (default ``tiingo`` — needs ``TIINGO_API_KEY``; see
``docs/reference/data_providers.md``). Educational research only; ``must_not_auto_execute``.

Usage:
    TIINGO_API_KEY=... python scripts/run_nightly.py [config.yaml] [--provider tiingo] [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from yearline_universe import (  # noqa: E402
    StudyConfig, load_universe_config, run_universe_pipeline, export_single_ticker_context,
    export_yearline_context, export_yearline_trend_series,
)
from yearline_universe.data_loader import load_price_data  # noqa: E402
from yearline_universe.market_calendar import last_completed_session  # noqa: E402


def _with_retry(fn, *, retries: int, base_delay: float, label: str):
    """Call ``fn`` with exponential backoff; re-raise after the last attempt."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - transient network/throttle
            last = exc
            if attempt >= retries:
                break
            delay = base_delay * (2 ** (attempt - 1))
            print(f"  [{label}] attempt {attempt}/{retries} failed: {exc} — retrying in {delay:.0f}s")
            time.sleep(delay)
    raise RuntimeError(f"{label}: all {retries} attempts failed (last: {last})")


def _write(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _status(out_dir: Path, run_date, **kw) -> Path:
    s = {"schema": "yearline_run_status_v1", "run_date": str(run_date),
         "generated_at": datetime.now(timezone.utc).isoformat(), "must_not_auto_execute": True, **kw}
    p = out_dir / f"yearline_run_status_{run_date}.json"
    _write(s, p)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default=str(REPO / "config" / "universe_mega_cap_ai_infra.yaml"))
    ap.add_argument("--provider", default="tiingo",
                    choices=["auto", "cache", "tiingo", "yfinance", "yahoo_chart"])
    ap.add_argument("--out", default=str(REPO / "exports" / "yearline_context"))
    ap.add_argument("--cache-dir", default=str(REPO / "data" / "price_cache"))
    ap.add_argument("--as-of", default=None, help="override the run date (YYYY-MM-DD); default = today (UTC)")
    ap.add_argument("--lookback-days", type=int, default=None, help="trend-series window (default: full history)")
    ap.add_argument("--force", action="store_true", help="run even if the market-calendar guard says no new bar")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--retry-base-delay", type=float, default=5.0)
    ap.add_argument("--fast", action="store_true",
                    help="skip the slow pooled hazard/calibration (NOT the gated production contract)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    run_date = (args.as_of or datetime.now(timezone.utc).date().isoformat())[:10]
    expected = last_completed_session(run_date)
    uni = load_universe_config(args.config)
    symbols = list(uni.symbols)
    ref = symbols[0] if symbols else "MSFT"
    print(f"nightly | universe={uni.universe_name} tickers={symbols} provider={args.provider} "
          f"run_date={run_date} expected_session={expected}")

    # --- market-calendar guard: pre-flight freshness check on a reference ticker -------------------
    if not args.force:
        try:
            df = _with_retry(
                lambda: load_price_data(ref, config=StudyConfig(), cache_dir=args.cache_dir,
                                        provider=args.provider, force_download=(args.provider == "auto")),
                retries=args.retries, base_delay=args.retry_base_delay, label=f"preflight:{ref}")
            latest_bar = df.index[-1].date() if len(df) else None
        except Exception as exc:  # noqa: BLE001
            p = _status(out_dir, run_date, available=False, reason="preflight_fetch_failed",
                        error=str(exc), provider=args.provider, expected_session=str(expected))
            print(f"  PRE-FLIGHT FAILED: {exc}\n  wrote {p}")
            return 1
        if latest_bar is None or latest_bar < expected:
            p = _status(out_dir, run_date, available=False, reason="no_new_bar",
                        latest_bar=str(latest_bar), expected_session=str(expected), provider=args.provider)
            print(f"  NO NEW BAR (latest={latest_bar} < expected={expected}); wrote {p}. "
                  f"Use --force to run anyway.")
            return 0
        print(f"  pre-flight OK: latest_bar={latest_bar} >= expected_session={expected} (provider={df.attrs.get('provider')})")

    # --- full pooled run (the gated production contract) -------------------------------------------
    gated = not args.fast
    result = _with_retry(
        lambda: run_universe_pipeline(
            uni, cache_dir=args.cache_dir, provider=args.provider,
            pool_hazard=gated, calibrate=gated, surface_blend=gated, surface_success=gated),
        retries=args.retries, base_delay=args.retry_base_delay, label="universe_pipeline")

    as_of = result.run_manifest.get("as_of")
    written, n_ok, n_failed = [], 0, 0
    for ticker, res in result.ticker_results.items():
        if getattr(res, "status", None) != "ok":
            n_failed += 1
            print(f"  {ticker:6} FAIL {getattr(res, 'error', '')}")
            continue
        env = export_single_ticker_context(res)
        cpath = export_yearline_context(env, out_dir=str(out_dir), as_of_today=run_date)
        spath = export_yearline_trend_series(
            res.semantic_history, out_dir=str(out_dir), ticker=env.get("ticker"),
            schema_version=env.get("schema_version"), model_stack_version=env.get("model_stack_version"),
            price_df=res.price_df, lookback_days=args.lookback_days)
        written += [cpath, spath]
        n_ok += 1
        print(f"  {ticker:6} OK   as_of={env.get('as_of')} -> {Path(cpath).name}, {Path(spath).name}")

    p = _status(out_dir, run_date, available=(n_ok > 0), reason=("ok" if n_ok else "no_ok_tickers"),
                as_of=as_of, expected_session=str(expected), provider=args.provider,
                n_ok=n_ok, n_failed=n_failed, files=[Path(f).name for f in written])
    print(f"\n{n_ok} ok / {n_failed} failed | as_of={as_of} | wrote {len(written)} artifacts + {p.name} under {out_dir}")
    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
