"""V13.9 — the NYSE-approx trading-day calendar used by the nightly producer's freshness guard."""
import pytest

from yearline_universe.market_calendar import is_trading_day, last_completed_session


@pytest.mark.parametrize("d,expected", [
    ("2025-06-10", True),    # Tuesday — normal session
    ("2025-12-26", True),    # Friday after Christmas — open
    ("2025-06-07", False),   # Saturday
    ("2025-06-08", False),   # Sunday
    ("2025-01-01", False),   # New Year's Day
    ("2025-01-20", False),   # MLK Jr. Day
    ("2025-04-18", False),   # Good Friday (NYSE-only, not a federal holiday)
    ("2025-06-19", False),   # Juneteenth
    ("2025-07-04", False),   # Independence Day
    ("2025-12-25", False),   # Christmas
])
def test_is_trading_day(d, expected):
    assert is_trading_day(d) is expected


def test_last_completed_session_prior_weekday():
    # A Wednesday run expects Tuesday's (completed) session.
    assert str(last_completed_session("2025-06-11")) == "2025-06-10"


def test_last_completed_session_skips_weekend_and_holiday():
    # Saturday run; the prior day (Fri 2025-07-04) is Independence Day ⇒ expect Thursday 2025-07-03.
    assert str(last_completed_session("2025-07-05")) == "2025-07-03"
    # Monday run ⇒ the prior Friday's session.
    assert str(last_completed_session("2025-06-09")) == "2025-06-06"
