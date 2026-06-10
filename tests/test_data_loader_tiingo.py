"""V13.9 — the Tiingo provider: adjusted-close mapping, no-token fall-through, throttle-JSON guard,
and that it's registered without disturbing the keyless cache path."""
import pytest

from yearline_universe import data_loader
from yearline_universe.config import StudyConfig
from yearline_universe.data_loader import _tiingo_frame_from_csv, _load_from_tiingo, load_price_data

# adjClose (50/51) deliberately != close (100/102) so we can prove the ADJUSTED series is used.
_CSV = (
    "date,close,high,low,open,volume,adjClose,adjHigh,adjLow,adjOpen,adjVolume,divCash,splitFactor\n"
    "2025-06-02,100.0,101.0,99.0,99.5,1000000,50.0,50.5,49.5,49.75,2000000,0.0,1.0\n"
    "2025-06-03,102.0,103.0,101.0,101.5,1100000,51.0,51.5,50.5,50.75,2200000,0.0,1.0\n"
)


class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text


def test_tiingo_frame_maps_adjusted_close():
    df = _tiingo_frame_from_csv(_CSV, "MSFT", StudyConfig())
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.name == "Date" and df.index.tz is None          # tz-naive Date index
    # auto_adjust=True (default) ⇒ Close is the adjusted series, not raw.
    assert df["Close"].tolist() == [50.0, 51.0]
    assert df["Open"].tolist() == [49.75, 50.75]


def test_tiingo_no_token_returns_none(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    assert _load_from_tiingo("MSFT", StudyConfig()) is None


def test_tiingo_throttle_json_returns_none(monkeypatch):
    requests = pytest.importorskip("requests")
    monkeypatch.setenv("TIINGO_API_KEY", "dummy")
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, '{"detail":"hit the rate limit"}'))
    assert _load_from_tiingo("MSFT", StudyConfig()) is None          # JSON body ⇒ not data ⇒ fall through


def test_tiingo_valid_fetch_parses(monkeypatch):
    requests = pytest.importorskip("requests")
    monkeypatch.setenv("TIINGO_API_KEY", "dummy")
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, _CSV))
    df = _load_from_tiingo("MSFT", StudyConfig())
    assert df is not None and df["Close"].tolist() == [50.0, 51.0]


def test_tiingo_registered_and_cache_path_intact():
    assert "tiingo" in data_loader._PROVIDERS
    from conftest import CACHE_DIR
    df = load_price_data("MSFT", config=StudyConfig(), cache_dir=str(CACHE_DIR), provider="cache")
    assert df is not None and len(df) > 250 and "Close" in df.columns
