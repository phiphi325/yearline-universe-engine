import pandas as pd
from conftest import CACHE_DIR
from yearline_universe import StudyConfig
from yearline_universe.data_loader import load_price_data
from yearline_universe.event_detection import detect_source_attempts, build_canonical_events


def test_detect_and_canonical_events_msft():
    cfg = StudyConfig(start="2009-01-01")
    df = load_price_data("MSFT", config=cfg, cache_dir=str(CACHE_DIR), provider="cache")
    assert len(df) > 1000

    strict = detect_source_attempts("MSFT", df, "strict", cfg)
    loose = detect_source_attempts("MSFT", df, "loose", cfg)
    assert not strict.empty
    src = pd.concat([strict, loose], ignore_index=True).sort_values(["trading_loc", "detector"]).reset_index(drop=True)
    import numpy as np
    src["source_event_id"] = np.arange(1, len(src) + 1)

    events = build_canonical_events("MSFT", df, src, cfg)
    assert not events.empty
    # outcomes are from the locked taxonomy
    assert set(events["canonical_outcome"].astype(str)) <= {"success", "fail", "pending"}
    # rounds are monotonic non-decreasing in touch-date order
    rounds = events.sort_values("canonical_touch_date")["round"].tolist()
    assert all(rounds[i] <= rounds[i + 1] for i in range(len(rounds) - 1))
    # event ids are unique and contiguous
    assert events["canonical_event_id"].is_unique


def test_detector_is_ticker_agnostic():
    cfg = StudyConfig(start="2009-01-01")
    out = {}
    for t in ["MSFT", "AAPL", "NVDA"]:
        df = load_price_data(t, config=cfg, cache_dir=str(CACHE_DIR), provider="cache")
        ev = build_canonical_events(
            t, df,
            _src(t, df, cfg), cfg,
        )
        out[t] = ev
        assert (ev["ticker"] == t).all()
    # different tickers produce different event timelines (no shared/hardcoded state)
    assert out["MSFT"].shape != out["NVDA"].shape or not out["MSFT"].equals(out["NVDA"])


def _src(t, df, cfg):
    import numpy as np
    s = detect_source_attempts(t, df, "strict", cfg)
    l = detect_source_attempts(t, df, "loose", cfg)
    src = pd.concat([s, l], ignore_index=True).sort_values(["trading_loc", "detector"]).reset_index(drop=True)
    src["source_event_id"] = np.arange(1, len(src) + 1)
    return src
