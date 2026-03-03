"""Tests for L-002 market calendar and trading-hours helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone

from data.market_calendar import MarketCalendar


def test_weekend_is_not_trading_day():
    cal = MarketCalendar()
    assert cal.is_trading_day(date(2026, 3, 7)) is False  # Saturday
    assert cal.is_trading_day(date(2026, 3, 8)) is False  # Sunday


def test_known_holiday_is_not_trading_day():
    cal = MarketCalendar()
    assert cal.get_holiday_name(date(2026, 1, 1)) == "New Year's Day"
    assert cal.is_trading_day(date(2026, 1, 1)) is False


def test_regular_session_window_utc_before_dst():
    cal = MarketCalendar()
    window = cal.get_session_window(date(2026, 3, 3))
    assert window is not None
    assert window.open_utc.hour == 14 and window.open_utc.minute == 30
    assert window.close_utc.hour == 21 and window.close_utc.minute == 0
    assert window.is_early_close is False


def test_regular_session_window_utc_after_dst():
    cal = MarketCalendar()
    window = cal.get_session_window(date(2026, 3, 10))
    assert window is not None
    assert window.open_utc.hour == 13 and window.open_utc.minute == 30
    assert window.close_utc.hour == 20 and window.close_utc.minute == 0


def test_black_friday_is_early_close():
    cal = MarketCalendar()
    window = cal.get_session_window(date(2026, 11, 27))
    assert window is not None
    assert window.is_early_close is True
    assert window.close_utc.hour == 18 and window.close_utc.minute == 0


def test_next_trading_day_skips_holiday_and_weekend():
    cal = MarketCalendar()
    # 2026-07-03 is observed Independence Day closure (7/4 is Saturday).
    assert cal.is_trading_day(date(2026, 7, 3)) is False
    assert cal.next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)


def test_previous_trading_day_skips_weekend_and_holiday():
    cal = MarketCalendar()
    assert cal.previous_trading_day(date(2026, 7, 6)) == date(2026, 7, 2)


def test_market_phase_transitions_regular_day():
    cal = MarketCalendar()
    # 2026-03-10 is post-DST; NY 09:30 = 13:30 UTC
    assert cal.market_phase(datetime(2026, 3, 10, 12, 30, tzinfo=timezone.utc)) == "pre_market"
    assert cal.market_phase(datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)) == "regular"
    assert cal.market_phase(datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc)) == "after_hours"
    assert cal.market_phase(datetime(2026, 3, 10, 2, 0, tzinfo=timezone.utc)) == "closed"
