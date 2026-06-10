"""Minimal NYSE trading-day calendar — dependency-free (pandas built-ins only).

Used by the nightly producer (`scripts/run_nightly.py`) to answer "is there a new daily bar to
publish?" without a heavy dependency.

NYSE holidays ≈ ``USFederalHolidayCalendar`` **minus** {Columbus Day, Veterans Day} **plus**
{Good Friday}. This is the standard good-enough approximation. It does **not** model early closes
(half-days) or one-off closures (e.g. national days of mourning); for exhaustive accuracy use
``pandas_market_calendars``. For a daily-bar freshness guard the approximation is sufficient.

Educational research only; not financial advice.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
)

__all__ = ["is_trading_day", "latest_trading_day", "last_completed_session"]


class _NYSEHolidayCalendar(AbstractHolidayCalendar):
    """NYSE full-day closures (observed dates)."""
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2021-06-19", observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


_CAL = _NYSEHolidayCalendar()


def _ts(d) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


def is_trading_day(d) -> bool:
    """True if ``d`` is a NYSE full trading day (weekday and not an observed holiday)."""
    ts = _ts(d)
    if ts.weekday() >= 5:  # Saturday / Sunday
        return False
    holidays = _CAL.holidays(ts - pd.Timedelta(days=1), ts + pd.Timedelta(days=1))
    return ts not in holidays


def latest_trading_day(asof=None) -> date:
    """The most recent trading day on or before ``asof`` (default: today, UTC)."""
    ts = _ts(asof if asof is not None else pd.Timestamp.utcnow())
    for _ in range(15):  # ample to clear any holiday cluster
        if is_trading_day(ts):
            return ts.date()
        ts -= pd.Timedelta(days=1)
    return ts.date()


def last_completed_session(asof=None) -> date:
    """The latest trading session that has **fully closed** as of ``asof``.

    A post-close nightly (run in the early AM) expects *yesterday's* session, not today's (today
    hasn't traded yet). So this is ``latest_trading_day(asof - 1 day)`` — e.g. a Tuesday run after a
    Monday holiday expects Friday's bar; a Saturday run expects Friday's bar; a Wednesday run expects
    Tuesday's bar.
    """
    ts = _ts(asof if asof is not None else pd.Timestamp.utcnow())
    return latest_trading_day(ts - pd.Timedelta(days=1))
