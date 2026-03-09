from datetime import date

from research.market_data.canonical_bars import CanonicalBar
from research.market_data.futures import (
    FuturesContract,
    MultiplePrices,
    build_continuous_series,
    build_multiple_prices,
    get_carry_series,
    register_contract,
)
from tests.research_test_utils import FakeConnection, FakeCursor


def test_register_contract_returns_id(monkeypatch):
    cursor = FakeCursor(fetchone_results=[(22,)])
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.market_data.futures.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.market_data.futures.release_pg_connection", lambda conn: None)

    contract = register_contract(
        FuturesContract(
            instrument_id=8,
            root_symbol="ES",
            expiry_date=date(2026, 6, 1),
            contract_code="ESM26",
            is_front=True,
        )
    )

    assert contract.contract_id == 22
    assert conn.committed is True


def test_build_multiple_prices_uses_front_and_next_contract(monkeypatch):
    first = FuturesContract(instrument_id=1, root_symbol="ES", expiry_date=date(2026, 6, 1), contract_code="ESM26", is_front=True)
    second = FuturesContract(instrument_id=2, root_symbol="ES", expiry_date=date(2026, 9, 1), contract_code="ESU26")
    monkeypatch.setattr("research.market_data.futures.get_contracts", lambda root_symbol: [first, second])
    monkeypatch.setattr("research.market_data.futures.get_front_contract", lambda root_symbol, as_of: first)
    monkeypatch.setattr(
        "research.market_data.futures.get_canonical_bars",
        lambda instrument_id, start, end: [CanonicalBar(instrument_id=instrument_id, bar_date=end, close=6000.0 if instrument_id == 1 else 6015.0, adj_close=0, session_template="cme")]
    )
    monkeypatch.setattr("research.market_data.futures.get_latest_bar", lambda instrument_id: None)

    snapshot = build_multiple_prices("ES", date(2026, 3, 8))

    assert snapshot == MultiplePrices(
        root_symbol="ES",
        price_date=date(2026, 3, 8),
        current_contract="ESM26",
        current_price=6000.0,
        next_contract="ESU26",
        next_price=6015.0,
        carry_contract="ESU26",
        carry_price=6015.0,
    )


def test_build_continuous_series_back_adjusts_roll(monkeypatch):
    first = FuturesContract(instrument_id=1, root_symbol="CL", expiry_date=date(2026, 2, 1), contract_code="CLG26")
    second = FuturesContract(instrument_id=2, root_symbol="CL", expiry_date=date(2026, 3, 1), contract_code="CLH26")
    monkeypatch.setattr("research.market_data.futures.get_contracts", lambda root_symbol: [first, second])
    monkeypatch.setattr("research.market_data.futures.get_roll_calendar", lambda root_symbol: [])

    def _bars(instrument_id, start, end):
        if instrument_id == 1:
            return [
                CanonicalBar(instrument_id=1, bar_date=date(2026, 1, 1), close=70.0, adj_close=70.0, session_template="cme"),
                CanonicalBar(instrument_id=1, bar_date=date(2026, 1, 2), close=72.0, adj_close=72.0, session_template="cme"),
            ]
        return [
            CanonicalBar(instrument_id=2, bar_date=date(2026, 1, 3), close=75.0, adj_close=75.0, session_template="cme"),
            CanonicalBar(instrument_id=2, bar_date=date(2026, 1, 4), close=76.0, adj_close=76.0, session_template="cme"),
        ]

    monkeypatch.setattr("research.market_data.futures.get_canonical_bars", _bars)

    series = build_continuous_series("CL")

    assert [item.price for item in series] == [70.0, 72.0, 72.0, 73.0]


def test_get_carry_series_returns_term_structure_diffs(monkeypatch):
    monkeypatch.setattr(
        "research.market_data.futures.get_contracts",
        lambda root_symbol: [
            FuturesContract(instrument_id=1, root_symbol=root_symbol, expiry_date=date(2026, 6, 1), contract_code="ESM26"),
            FuturesContract(instrument_id=2, root_symbol=root_symbol, expiry_date=date(2026, 9, 1), contract_code="ESU26"),
        ],
    )
    monkeypatch.setattr(
        "research.market_data.futures.get_canonical_bars",
        lambda instrument_id, start, end: [CanonicalBar(instrument_id=instrument_id, bar_date=date(2026, 3, 8), close=100.0, adj_close=100.0, session_template="cme")],
    )
    monkeypatch.setattr(
        "research.market_data.futures.build_multiple_prices",
        lambda root_symbol, as_of: MultiplePrices(
            root_symbol=root_symbol,
            price_date=as_of,
            current_contract="ESM26",
            current_price=6000.0,
            next_contract="ESU26",
            next_price=6010.0,
            carry_contract="ESU26",
            carry_price=6010.0,
        ),
    )

    carry = get_carry_series("ES", date(2026, 3, 1), date(2026, 3, 8))

    assert carry == [
        {
            "root_symbol": "ES",
            "price_date": date(2026, 3, 8),
            "current_contract": "ESM26",
            "next_contract": "ESU26",
            "carry": 10.0,
        }
    ]
