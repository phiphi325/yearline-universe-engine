import numpy as np
import pandas as pd
import pytest
from yearline_universe.indicators import add_indicators, safe_pct, date_str
from yearline_universe import StudyConfig


def _synthetic(n=400):
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    close = pd.Series(100 + np.linspace(0, 50, n), index=idx)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1e6}, index=idx)


def test_add_indicators_columns():
    df = add_indicators(_synthetic(), StudyConfig())
    for col in ["MA250", "MA200", "ATR14", "ATR14_pct", "HV30",
                "distance_to_ma250_pct", "gap_state"]:
        assert col in df.columns
    # MA250 needs 250 obs of warmup
    assert df["MA250"].iloc[:249].isna().all()
    assert df["MA250"].iloc[260:].notna().all()


def test_distance_sign_in_uptrend():
    df = add_indicators(_synthetic(), StudyConfig())
    # rising series => price above its own MA250 => positive distance
    assert df["distance_to_ma250_pct"].dropna().iloc[-1] > 0


def test_safe_pct_and_date_str():
    assert safe_pct(110, 100) == pytest.approx(10.0)
    assert np.isnan(safe_pct(1, 0))
    assert date_str(pd.Timestamp("2024-11-29")) == "2024-11-29"
    assert date_str(pd.NaT) is None
