"""Profile + time a single-ticker pipeline run.

Teaching aid for docs/tutorials/performance_optimization_tutorial.md.
Run it to SEE the profile for yourself (the #1 habit: measure, don't guess).

Examples:
    python scripts/profile_pipeline.py                 # profile MSFT
    python scripts/profile_pipeline.py --ticker AAPL --top 15
    python scripts/profile_pipeline.py --no-profile     # just wall-clock timing
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from yearline_universe import load_universe_config, run_ticker_pipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="MSFT")
    ap.add_argument("--config", default=str(REPO / "config" / "universe_mega_cap_ai_infra.yaml"))
    ap.add_argument("--cache-dir", default=str(REPO / "data" / "price_cache"))
    ap.add_argument("--top", type=int, default=20, help="rows of cProfile output (by cumulative time)")
    ap.add_argument("--no-profile", action="store_true", help="wall-clock only, no cProfile")
    args = ap.parse_args()

    uni = load_universe_config(args.config)
    tc = uni.get_ticker(args.ticker)

    # 1) Wall-clock timing — the number that actually matters to a user.
    t0 = time.perf_counter()
    run_ticker_pipeline(tc, uni, cache_dir=args.cache_dir, provider="cache")
    print(f"{args.ticker}: {time.perf_counter() - t0:.2f}s wall-clock")

    if args.no_profile:
        return 0

    # 2) cProfile — WHERE the time goes. Read 'cumtime' (incl. callees) and
    #    'ncalls' (how often). A huge ncalls on a cheap function is a red flag.
    pr = cProfile.Profile()
    pr.enable()
    run_ticker_pipeline(tc, uni, cache_dir=args.cache_dir, provider="cache")
    pr.disable()
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(args.top)
    print(s.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
