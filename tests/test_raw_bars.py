from datetime import datetime, timezone

from research.market_data.raw_bars import RawBar, get_bars, get_latest_bar, ingest_bars
from tests.research_test_utils import FakeConnection, FakeCursor, make_description


def test_ingest_bars_uses_bulk_insert(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.raw_bars.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.raw_bars.release_pg_connection", lambda conn: None)

    count = ingest_bars(
        [
            RawBar(
                instrument_id=1,
                vendor="ibkr",
                bar_timestamp=datetime(2026, 3, 7, 0, 0, tzinfo=timezone.utc),
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.5,
                volume=100,
            )
        ]
    )

    assert count == 1
    assert conn.committed is True
    assert len(cursor.executemany_calls) == 1


def test_get_bars_returns_models(monkeypatch):
    cursor = FakeCursor(
        fetchall_results=[
            [
                (1, 5, "ibkr", datetime(2026, 3, 7, tzinfo=timezone.utc), "daily", 1.0, 2.0, 0.5, 1.5, 10, None, None, 1),
            ]
        ],
        descriptions=[
            make_description(
                "bar_id",
                "instrument_id",
                "vendor",
                "bar_timestamp",
                "session_code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "bid",
                "ask",
                "ingestion_ver",
            )
        ],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.raw_bars.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.raw_bars.release_pg_connection", lambda conn: None)

    bars = get_bars(5, datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 3, 8, tzinfo=timezone.utc))

    assert len(bars) == 1
    assert bars[0].close == 1.5


def test_get_latest_bar_returns_single_latest(monkeypatch):
    cursor = FakeCursor(
        fetchone_results=[
            (9, 5, "ibkr", datetime(2026, 3, 8, tzinfo=timezone.utc), "daily", 1.0, 2.0, 0.5, 1.8, 20, None, None, 1),
        ],
        descriptions=[
            make_description(
                "bar_id",
                "instrument_id",
                "vendor",
                "bar_timestamp",
                "session_code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "bid",
                "ask",
                "ingestion_ver",
            )
        ],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.raw_bars.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.raw_bars.release_pg_connection", lambda conn: None)

    bar = get_latest_bar(5, vendor="ibkr")

    assert bar is not None
    assert bar.bar_id == 9
