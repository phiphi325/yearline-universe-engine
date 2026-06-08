import gc
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CACHE_DIR = REPO / "data" / "price_cache"
CONFIG_DIR = REPO / "config"


@pytest.fixture(autouse=True)
def _reclaim_memory_after_test():
    """Reclaim DataFrame/model memory between tests.

    The real-data tests each spin up the universe pipeline (large pandas frames);
    in a single ``pytest`` process on a memory-constrained machine the cumulative
    footprint can OOM. A gc sweep after each test keeps peak memory bounded. For a
    fully process-isolated run, use ``bash scripts/run_tests.sh`` (one file per
    process) — see the README "Tests" section.
    """
    yield
    gc.collect()
