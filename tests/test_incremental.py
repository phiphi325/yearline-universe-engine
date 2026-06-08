"""Incremental daily replay: equivalence to full replay + edge cases (splits)."""
import numpy as np
import pandas as pd
import pytest
from conftest import CACHE_DIR
from yearline_universe import StudyConfig
from yearline_universe.data_loader import load_price_data
from yearline_universe.event_detection import detect_source_attempts, build_canonical_events
from yearline_universe.replay import build_replay_history, build_replay_history_incremental

CFG = StudyConfig(start="2009-01-01")
REPLAY_START = "2024-06-01"   # short window => fast test
PEER = "mega_cap_software"


@pytest.fixture(scope="module")
def price_and_events():
    df = load_price_data("MSFT", config=CFG, cache_dir=str(CACHE_DIR), provider="cache")
    s = detect_source_attempts("MSFT", df, "strict", CFG)
    l = detect_source_attempts("MSFT", df, "loose", CFG)
    src = pd.concat([s, l], ignore_index=True).sort_values(["trading_loc", "detector"]).reset_index(drop=True)
    src["source_event_id"] = np.arange(1, len(src) + 1)
    events = build_canonical_events("MSFT", df, src, CFG)
    return df, events


def _equal(a, b):
    if a.shape != b.shape:
        return False
    for c in [c for c in a.columns if c in b.columns]:
        x, y = a[c], b[c]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            if not np.allclose(x.to_numpy(float), y.to_numpy(float), rtol=1e-7, atol=1e-9, equal_nan=True):
                return False
        elif not x.astype(str).reset_index(drop=True).equals(y.astype(str).reset_index(drop=True)):
            return False
    return True


def _incr(df, events, state_dir, replay_start=REPLAY_START):
    return build_replay_history_incremental("MSFT", df, events, PEER, None, CFG,
                                            replay_start=replay_start, state_dir=str(state_dir))


def test_incremental_append_equals_full(tmp_path, price_and_events):
    df, events = price_and_events
    yday, today = df.iloc[:-1], df  # one new bar

    h1, m1 = _incr(yday, events, tmp_path)
    assert m1.startswith("full_recompute")             # first run builds + caches
    h2, m2 = _incr(today, events, tmp_path)
    assert m2.startswith("incremental_appended")        # second run appends only

    full = build_replay_history("MSFT", today, events, PEER, None, CFG, replay_start=REPLAY_START)
    assert _equal(h2.reset_index(drop=True), full.reset_index(drop=True))


def test_cache_hit_when_no_new_data(tmp_path, price_and_events):
    df, events = price_and_events
    _incr(df, events, tmp_path)
    _, mode = _incr(df, events, tmp_path)
    assert mode == "cache_hit_no_new"


def test_split_invalidates_cache(tmp_path, price_and_events):
    df, events = price_and_events
    _incr(df, events, tmp_path)                          # cache on the normal series
    # Simulate a 2:1 split re-adjustment: the whole adjusted series is re-based.
    split = df.copy()
    for c in ["Open", "High", "Low", "Close"]:
        split[c] = split[c] * 0.5
    h, mode = _incr(split, events, tmp_path)
    assert mode == "full_recompute:inputs_changed"       # split detected -> recompute
    full_split = build_replay_history("MSFT", split, events, PEER, None, CFG, replay_start=REPLAY_START)
    assert _equal(h.reset_index(drop=True), full_split.reset_index(drop=True))


def test_replay_start_change_invalidates(tmp_path, price_and_events):
    df, events = price_and_events
    _incr(df, events, tmp_path, replay_start="2024-06-01")
    _, mode = _incr(df, events, tmp_path, replay_start="2024-07-01")
    assert mode == "full_recompute:replay_start_changed"
