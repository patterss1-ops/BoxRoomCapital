from datetime import date, datetime

from research.market_data.bootstrap import ingest_seeded_market_data, market_data_readiness
from research.market_data.instruments import InstrumentMaster
from research.market_data.raw_bars import RawBar


class FakeAdapter:
    def vendor_name(self) -> str:
        return "ibkr"

    def fetch_daily_bars(self, symbol, start, end, instrument_id=0):
        assert symbol == "SPY"
        return [
            RawBar(
                instrument_id=instrument_id,
                vendor="ibkr",
                bar_timestamp=datetime(2026, 3, 7, 0, 0),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000,
            ),
            RawBar(
                instrument_id=instrument_id,
                vendor="ibkr",
                bar_timestamp=datetime(2026, 3, 8, 0, 0),
                open=101.0,
                high=102.0,
                low=100.0,
                close=101.5,
                volume=1100,
            ),
        ]


def test_ingest_seeded_market_data_only_pulls_new_bars(monkeypatch):
    instrument = InstrumentMaster(
        instrument_id=7,
        symbol="SPY",
        asset_class="etf",
        venue="SMART",
        currency="USD",
        session_template="us_equity",
        vendor_ids={"yfinance": "SPY"},
    )
    ingested = []
    reprocessed = []
    latest = RawBar(
        instrument_id=7,
        vendor="ibkr",
        bar_timestamp=datetime(2026, 3, 7, 0, 0),
        close=100.5,
    )

    monkeypatch.setattr("research.market_data.bootstrap._seeded_instruments", lambda as_of, universes: [instrument])
    monkeypatch.setattr("research.market_data.bootstrap.get_latest_bar", lambda instrument_id, vendor=None: latest)
    monkeypatch.setattr("research.market_data.bootstrap.ingest_bars", lambda bars: ingested.append(list(bars)) or len(bars))
    monkeypatch.setattr(
        "research.market_data.bootstrap.reprocess_bars",
        lambda instrument_id, start, end, session_template="default": reprocessed.append((instrument_id, start, end, session_template)) or [object()],
    )

    summary = ingest_seeded_market_data(
        start=date(2026, 3, 1),
        end=date(2026, 3, 8),
        adapter=FakeAdapter(),
    )

    assert summary["bars_ingested"] == 1
    assert len(ingested[0]) == 1
    assert ingested[0][0].bar_timestamp.date() == date(2026, 3, 8)
    assert reprocessed == [(7, date(2026, 3, 8), date(2026, 3, 8), "us_equity")]


def test_market_data_readiness_reports_ready_rows(monkeypatch):
    instrument = InstrumentMaster(
        instrument_id=9,
        symbol="QQQ",
        asset_class="etf",
        venue="SMART",
        currency="USD",
        session_template="us_equity",
        vendor_ids={"yfinance": "QQQ"},
    )
    latest = RawBar(
        instrument_id=9,
        vendor="ibkr",
        bar_timestamp=datetime(2026, 3, 8, 0, 0),
        close=510.0,
    )

    monkeypatch.setattr("research.market_data.bootstrap._seeded_instruments", lambda as_of, universes: [instrument])
    monkeypatch.setattr("research.market_data.bootstrap.get_latest_bar", lambda instrument_id: latest)
    monkeypatch.setattr("research.market_data.bootstrap.get_canonical_bars", lambda instrument_id, start, end: [object(), object()])

    summary = market_data_readiness(as_of=date(2026, 3, 8))

    assert summary["ready_count"] == 1
    assert summary["rows"][0]["symbol"] == "QQQ"
    assert summary["rows"][0]["status"] == "ready"
