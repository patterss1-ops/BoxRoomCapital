from datetime import date

from research.market_data.instruments import (
    InstrumentMaster,
    create_instrument,
    get_instrument,
    search_instruments,
    update_instrument,
)
from tests.research_test_utils import FakeConnection, FakeCursor, connection_sequence, make_description


def test_create_instrument_returns_inserted_id(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(101,)])
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.instruments.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.instruments.release_pg_connection", lambda conn: None)

    instrument = create_instrument(
        InstrumentMaster(
            symbol="AAPL",
            asset_class="equity",
            venue="NASDAQ",
            currency="USD",
            vendor_ids={"ibkr": "265598"},
        )
    )

    assert instrument.instrument_id == 101
    assert conn.committed is True
    assert "INSERT INTO research.instruments" in cursor.executed[0][0]


def test_get_instrument_maps_row_to_model(monkeypatch):
    cursor = FakeCursor(
        fetchone_results=[
            (
                7,
                "ES",
                "future",
                "CME",
                "USD",
                "cme_globex",
                50.0,
                0.25,
                {"ibkr": "123"},
                True,
                date(2024, 1, 1),
                None,
                {"sector": "index"},
            )
        ],
        descriptions=[
            make_description(
                "instrument_id",
                "symbol",
                "asset_class",
                "venue",
                "currency",
                "session_template",
                "multiplier",
                "tick_size",
                "vendor_ids",
                "is_active",
                "listing_date",
                "delisting_date",
                "metadata",
            )
        ],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.instruments.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.instruments.release_pg_connection", lambda conn: None)

    instrument = get_instrument(7)

    assert instrument is not None
    assert instrument.symbol == "ES"
    assert instrument.vendor_ids["ibkr"] == "123"


def test_update_instrument_reads_back_updated_row(monkeypatch):
    update_cursor = FakeCursor()
    read_cursor = FakeCursor(
        fetchone_results=[
            (
                3,
                "MSFT",
                "equity",
                "NASDAQ",
                "USD",
                "us_equity",
                None,
                None,
                {"ibkr": "456"},
                False,
                None,
                None,
                {"note": "inactive"},
            )
        ],
        descriptions=[
            make_description(
                "instrument_id",
                "symbol",
                "asset_class",
                "venue",
                "currency",
                "session_template",
                "multiplier",
                "tick_size",
                "vendor_ids",
                "is_active",
                "listing_date",
                "delisting_date",
                "metadata",
            )
        ],
    )
    monkeypatch.setattr(
        "research.market_data.instruments.get_pg_connection",
        connection_sequence(FakeConnection(update_cursor), FakeConnection(read_cursor)),
    )
    monkeypatch.setattr("research.market_data.instruments.release_pg_connection", lambda conn: None)

    instrument = update_instrument(3, is_active=False, metadata={"note": "inactive"})

    assert instrument is not None
    assert instrument.is_active is False
    assert instrument.metadata["note"] == "inactive"


def test_search_instruments_returns_matches(monkeypatch):
    cursor = FakeCursor(
        fetchall_results=[
            [
                (1, "SPY", "equity", "NYSE", "USD", "us_equity", None, None, {}, True, None, None, {}),
                (2, "SPX", "index", "CBOE", "USD", "us_index", None, None, {}, True, None, None, {}),
            ]
        ],
        descriptions=[
            make_description(
                "instrument_id",
                "symbol",
                "asset_class",
                "venue",
                "currency",
                "session_template",
                "multiplier",
                "tick_size",
                "vendor_ids",
                "is_active",
                "listing_date",
                "delisting_date",
                "metadata",
            )
        ],
    )
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.instruments.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.instruments.release_pg_connection", lambda conn: None)

    matches = search_instruments("SP")

    assert [item.symbol for item in matches] == ["SPY", "SPX"]
