from datetime import date

from research.market_data.liquidity import (
    LiquidityCostEntry,
    get_cost_series,
    get_latest_cost,
    record_cost,
)
from tests.research_test_utils import FakeConnection, FakeCursor, make_description


def test_record_cost_upserts(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.liquidity.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.liquidity.release_pg_connection", lambda conn: None)

    entry = record_cost(LiquidityCostEntry(instrument_id=1, as_of=date(2026, 3, 8), spread_cost_bps=5.0))

    assert entry.spread_cost_bps == 5.0
    assert conn.committed is True


def test_get_cost_series_returns_entries(monkeypatch):
    cursor = FakeCursor(
        fetchall_results=[[(1, date(2026, 3, 8), 0.1, 5.0, 1.0, None, None)]],
        descriptions=[make_description("instrument_id", "as_of", "inside_spread", "spread_cost_bps", "commission_per_unit", "funding_rate", "borrow_cost")],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.liquidity.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.liquidity.release_pg_connection", lambda conn: None)

    series = get_cost_series(1, date(2026, 3, 1), date(2026, 3, 8))

    assert len(series) == 1
    assert series[0].inside_spread == 0.1


def test_get_latest_cost_returns_latest_row(monkeypatch):
    cursor = FakeCursor(
        fetchone_results=[(1, date(2026, 3, 8), 0.1, 5.0, 1.0, None, None)],
        descriptions=[make_description("instrument_id", "as_of", "inside_spread", "spread_cost_bps", "commission_per_unit", "funding_rate", "borrow_cost")],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.liquidity.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.liquidity.release_pg_connection", lambda conn: None)

    latest = get_latest_cost(1)

    assert latest is not None
    assert latest.as_of == date(2026, 3, 8)
