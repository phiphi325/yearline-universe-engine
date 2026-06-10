"""Parity check — a new live provider (default: Tiingo) vs the committed Yahoo cache.

Before trusting a provider swap you must confirm it doesn't silently shift the model's inputs. This
compares, per universe ticker, the **adjusted close** and the engine-relevant **distance-to-MA250**
between the committed cache (the Yahoo-adjusted baseline) and a live provider, and fails (exit 1) if
the latest-bar distance diverges beyond a tolerance.

Needs ``TIINGO_API_KEY`` for the Tiingo side. Educational research only; ``must_not_auto_execute``.

Usage:
    TIINGO_API_KEY=... python scripts/parity_check.py [config.yaml] [--provider tiingo] [--tolerance-pp 0.25]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from yearline_universe import StudyConfig, load_universe_config  # noqa: E402
from yearline_universe.data_loader import load_price_data  # noqa: E402


def _distance_to_ma250(close: pd.Series) -> float | None:
    if close is None or len(close) < 250:
        return None
    ma = close.rolling(250).mean().iloc[-1]
    last = close.iloc[-1]
    if pd.isna(ma) or ma == 0 or pd.isna(last):
        return None
    return float((last / ma - 1.0) * 100.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default=str(REPO / "config" / "universe_mega_cap_ai_infra.yaml"))
    ap.add_argument("--provider", default="tiingo", choices=["tiingo", "yfinance", "yahoo_chart"])
    ap.add_argument("--cache-dir", default=str(REPO / "data" / "price_cache"))
    ap.add_argument("--tolerance-pp", type=float, default=0.25,
                    help="max allowed |distance-to-MA250| divergence at the latest common bar, in pp")
    args = ap.parse_args()

    uni = load_universe_config(args.config)
    print(f"parity: {args.provider} vs cache | universe={uni.universe_name} | tolerance={args.tolerance_pp}pp\n")
    print(f"  {'ticker':6} {'n_common':>8} {'adjClose Δ% (max/last)':>24} {'dist_cache':>11} {'dist_'+args.provider:>13} {'Δpp':>8}  flag")

    rows, worst, failures = [], 0.0, []
    for t in uni.symbols:
        try:
            base = load_price_data(t, config=StudyConfig(), cache_dir=args.cache_dir, provider="cache")
            new = load_price_data(t, config=StudyConfig(), provider=args.provider, force_download=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  {t:6} ERROR {exc}")
            failures.append(t)
            continue
        common = base.index.intersection(new.index)
        if len(common) == 0:
            print(f"  {t:6} no overlapping dates")
            failures.append(t)
            continue
        latest = common.max()
        bclose = base.loc[:latest, "Close"]
        nclose = new.loc[:latest, "Close"]
        rel = ((new.loc[common, "Close"] - base.loc[common, "Close"]) / base.loc[common, "Close"] * 100.0)
        max_pct = float(rel.abs().max())
        last_pct = float(rel.loc[latest])
        d_base = _distance_to_ma250(bclose)
        d_new = _distance_to_ma250(nclose)
        dpp = (abs(d_new - d_base) if (d_base is not None and d_new is not None) else float("nan"))
        flag = ""
        if dpp == dpp:  # not NaN
            worst = max(worst, dpp)
            if dpp > args.tolerance_pp:
                flag = "  ⚠ EXCEEDS"
                failures.append(t)
        print(f"  {t:6} {len(common):>8} {max_pct:>10.3f} / {last_pct:>8.3f} "
              f"{(d_base if d_base is not None else float('nan')):>11.3f} "
              f"{(d_new if d_new is not None else float('nan')):>13.3f} {dpp:>8.3f}{flag}")
        rows.append((t, dpp))

    print(f"\n  worst distance divergence: {worst:.3f}pp (tolerance {args.tolerance_pp}pp)")
    if failures:
        print(f"  PARITY FAILED for: {sorted(set(failures))} — investigate (usually a dividend-adjustment "
              f"difference) before switching the nightly to {args.provider}.")
        return 1
    print(f"  PARITY OK — {args.provider} matches the cache within tolerance; safe to adopt (re-validate periodically).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
