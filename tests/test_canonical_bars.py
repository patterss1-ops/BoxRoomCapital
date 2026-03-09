from datetime import date, datetime, timezone

from research.market_data.canonical_bars import (
    CanonicalBar,
    get_canonical_bars,
    normalize_raw_to_canonical,
    reprocess_bars,
    store_canonical_bars,
)
from research.market_data.corporate_actions import CorporateAction
from research.market_data.raw_bars import RawBar
from tests.research_test_utils import FakeConnection, FakeCursor, make_description


def test_normalize_raw_to_canonical_applies_adjustments_and_flags():
    raw_bars = [
        RawBar(
            instrument_id=1,
            vendor="ibkr",
            bar_timestamp=datetime(2026, 1, 10, tzinfo=timezone.utc),
            open=100.0,
            high=105.0,
            low=95.0,
            close=100.0,
            volume=1000,
        )
    ]
    actions = [
        CorporateAction(instrument_id=1, action_type="split", ex_date=date(2026, 2, 1), ratio=2.0),
        CorporateAction(instrument_id=1, action_type="dividend", ex_date=date(2026, 2, 15), ratio=1.0),
    ]

    bars = normalize_raw_to_canonical(raw_bars, actions, "us_equity")

    assert len(bars) == 1
    assert round(bars[0].adj_close, 2) == 49.0
    assert "spike_checked" in bars[0].quality_flags
    assert bars[0].dollar_volume == 100000.0


def test_store_canonical_bars_bulk_inserts(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.canonical_bars.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.canonical_bars.release_pg_connection", lambda conn: None)

    count = store_canonical_bars(
        [
            CanonicalBar(
                instrument_id=1,
                bar_date=date(2026, 3, 1),
                close=10.0,
                adj_close=10.0,
                session_template="us_equity",
            )
        ]
    )

    assert count == 1
    assert len(cursor.executemany_calls) == 1
    assert conn.committed is True


def test_get_canonical_bars_returns_latest_version_rows(monkeypatch):
    cursor = FakeCursor(
        fetchall_results=[
            [
                (1, 1, date(2026, 3, 1), 10.0, 11.0, 9.0, 10.5, 10.5, 100, 1050.0, "us_equity", 2, ["session_aligned"]),
            ]
        ],
        descriptions=[
            make_description(
                "bar_id",
                "instrument_id",
                "bar_date",
                "open",
                "high",
                "low",
                "close",
                "adj_close",
                "volume",
                "dollar_volume",
                "session_template",
                "data_version",
                "quality_flags",
            )
        ],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.canonical_bars.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.canonical_bars.release_pg_connection", lambda conn: None)

    bars = get_canonical_bars(1, date(2026, 3, 1), date(2026, 3, 5))

    assert len(bars) == 1
    assert bars[0].data_version == 2


def test_reprocess_bars_increments_data_version(monkeypatch):
    version_cursor = FakeCursor(fetchone_results=[(2,)])
    version_conn = FakeConnection(version_cursor)
    monkeypatch.setattr("research.market_data.canonical_bars.get_pg_connection", lambda: version_conn)
    monkeypatch.setattr("research.market_data.canonical_bars.release_pg_connection", lambda conn: None)
    monkeypatch.setattr(
        "research.market_data.canonical_bars.get_bars",
        lambda instrument_id, start, end: [
            RawBar(
                instrument_id=instrument_id,
                vendor="ibkr",
                bar_timestamp=datetime(2026, 3, 1, tzinfo=timezone.utc),
                close=10.0,
            )
        ],
    )
    monkeypatch.setattr("research.market_data.canonical_bars.get_actions", lambda instrument_id: [])
    stored = {}
    monkeypatch.setattr(
        "research.market_data.canonical_bars.store_canonical_bars",
        lambda bars: stored.setdefault("bars", bars) or len(bars),
    )

    bars = reprocess_bars(1, date(2026, 3, 1), date(2026, 3, 1), session_template="us_equity")

    assert bars[0].data_version == 3
    assert stored["bars"][0].data_version == 3
