"""Market calendar with exchange sessions, holidays, and trading-hours helpers.

L-002: Provides holiday-aware trading-day checks, session boundaries, and
next/previous trading-day navigation for US equities exchanges.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionWindow:
    """Trading session window in UTC."""

    session_date: date
    open_utc: datetime
    close_utc: datetime
    is_early_close: bool = False
    holiday_name: str = ""


class MarketCalendar:
    """US-equity trading calendar with holiday and early-close awareness."""

    def __init__(self, exchange: str = "XNYS") -> None:
        self.exchange = exchange

    def is_trading_day(self, day: date) -> bool:
        if day.weekday() >= 5:
            return False
        return self.get_holiday_name(day) is None

    def get_holiday_name(self, day: date) -> str | None:
        holidays = _us_market_holidays(day.year)
        return holidays.get(day)

    def get_session_window(self, day: date) -> SessionWindow | None:
        if not self.is_trading_day(day):
            return None

        local_open = datetime.combine(day, time(9, 30), tzinfo=ET)
        local_close = datetime.combine(day, time(16, 0), tzinfo=ET)
        early_closes = _us_market_early_closes(day.year)
        is_early = day in early_closes
        if is_early:
            local_close = datetime.combine(day, time(13, 0), tzinfo=ET)

        return SessionWindow(
            session_date=day,
            open_utc=local_open.astimezone(timezone.utc),
            close_utc=local_close.astimezone(timezone.utc),
            is_early_close=is_early,
            holiday_name="",
        )

    def market_phase(self, at_utc: datetime) -> str:
        """Return `closed`, `pre_market`, `regular`, or `after_hours`."""
        ts = at_utc.astimezone(ET)
        day = ts.date()
        if not self.is_trading_day(day):
            return "closed"

        pre_open = datetime.combine(day, time(4, 0), tzinfo=ET)
        regular_open = datetime.combine(day, time(9, 30), tzinfo=ET)
        regular_close = datetime.combine(day, time(16, 0), tzinfo=ET)
        if day in _us_market_early_closes(day.year):
            regular_close = datetime.combine(day, time(13, 0), tzinfo=ET)
        after_close = datetime.combine(day, time(20, 0), tzinfo=ET)

        if ts < pre_open or ts >= after_close:
            return "closed"
        if ts < regular_open:
            return "pre_market"
        if ts < regular_close:
            return "regular"
        return "after_hours"

    def next_trading_day(self, day: date) -> date:
        current = day + timedelta(days=1)
        while not self.is_trading_day(current):
            current += timedelta(days=1)
        return current

    def previous_trading_day(self, day: date) -> date:
        current = day - timedelta(days=1)
        while not self.is_trading_day(current):
            current -= timedelta(days=1)
        return current


def _us_market_holidays(year: int) -> dict[date, str]:
    """US market holidays for a given year with weekend-observed dates."""
    holidays: dict[date, str] = {}
    holidays[_observed(date(year, 1, 1))] = "New Year's Day"
    holidays[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    holidays[_nth_weekday(year, 2, 0, 3)] = "Presidents' Day"
    holidays[_good_friday(year)] = "Good Friday"
    holidays[_last_weekday(year, 5, 0)] = "Memorial Day"
    holidays[_observed(date(year, 6, 19))] = "Juneteenth"
    holidays[_observed(date(year, 7, 4))] = "Independence Day"
    holidays[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    holidays[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
    holidays[_observed(date(year, 12, 25))] = "Christmas Day"
    return holidays


def _us_market_early_closes(year: int) -> set[date]:
    """Common US market early-close days."""
    early: set[date] = set()
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    black_friday = thanksgiving + timedelta(days=1)
    if black_friday.weekday() < 5 and black_friday not in _us_market_holidays(year):
        early.add(black_friday)

    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 5 and christmas_eve not in _us_market_holidays(year):
        early.add(christmas_eve)

    july3 = date(year, 7, 3)
    july4 = date(year, 7, 4)
    if july4.weekday() < 5 and july3.weekday() < 5 and july3 not in _us_market_holidays(year):
        early.add(july3)

    return early


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return nth weekday (0=Mon) of month."""
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    current += timedelta(days=7 * (n - 1))
    return current


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _good_friday(year: int) -> date:
    return _easter_sunday(year) - timedelta(days=2)


def _easter_sunday(year: int) -> date:
    """Compute Easter Sunday using Meeus/Jones/Butcher Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)
