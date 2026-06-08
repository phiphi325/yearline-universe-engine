import pytest
from conftest import CONFIG_DIR
from yearline_universe import load_universe_config, TickerConfig, UniverseConfig, StudyConfig


def test_load_mega_cap_universe():
    uni = load_universe_config(CONFIG_DIR / "universe_mega_cap_ai_infra.yaml")
    assert isinstance(uni, UniverseConfig)
    assert uni.universe_name == "mega_cap_ai_infra"
    assert "MSFT" in uni.symbols and "NVDA" in uni.symbols
    assert "Information Technology" in uni.sectors()
    # V12-faithful rolling window (not the spec's illustrative 50)
    assert uni.study.ma_fast_len == 200
    assert uni.study.ma_len == 250


def test_load_sectors_sample_universe():
    uni = load_universe_config(CONFIG_DIR / "universe_sp500_sectors_sample.yaml")
    assert len(uni.sectors()) >= 5  # genuinely cross-sector


def test_ticker_config_requires_fields():
    with pytest.raises(ValueError):
        TickerConfig(ticker="", sector="X", peer_group="y")
    with pytest.raises(ValueError):
        TickerConfig(ticker="ABC", sector="", peer_group="y")


def test_ticker_symbol_normalised():
    tc = TickerConfig(ticker=" msft ", sector="IT", peer_group="sw")
    assert tc.ticker == "MSFT"


def test_universe_rejects_duplicates():
    a = TickerConfig(ticker="AAA", sector="S", peer_group="P")
    with pytest.raises(ValueError):
        UniverseConfig(universe_name="u", benchmark=None, start="2009-01-01",
                       replay_start="2020-01-01", tickers=(a, a))


def test_get_ticker_and_study_for():
    uni = load_universe_config(CONFIG_DIR / "universe_mega_cap_ai_infra.yaml")
    tc = uni.get_ticker("aapl")          # case-insensitive
    assert tc.ticker == "AAPL"
    assert isinstance(uni.study_for(tc), StudyConfig)
    with pytest.raises(KeyError):
        uni.get_ticker("ZZZZ")
