from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from research.market_data.ingestion import BarchartAdapter, IBKRAdapter, NorgateAdapter


def test_ibkr_adapter_fetch_daily_bars_returns_raw_bar_models():
    frame = pd.DataFrame(
        [
            {"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5, "Volume": 1000},
            {"Open": 11.0, "High": 12.0, "Low": 10.0, "Close": 11.5, "Volume": 1100},
        ],
        index=pd.to_datetime(["2026-03-06", "2026-03-07"]),
    )
    adapter = IBKRAdapter(history_fetcher=lambda symbol, start, end: frame)

    bars = adapter.fetch_daily_bars("SPY", date(2026, 3, 6), date(2026, 3, 8), instrument_id=7)

    assert len(bars) == 2
    assert bars[0].vendor == "ibkr"
    assert bars[0].instrument_id == 7


def test_ibkr_adapter_fetch_daily_bars_handles_single_ticker_multiindex_frame():
    frame = pd.DataFrame(
        {
            ("Open", "SPY"): [10.0, 11.0],
            ("High", "SPY"): [11.0, 12.0],
            ("Low", "SPY"): [9.0, 10.0],
            ("Close", "SPY"): [10.5, 11.5],
            ("Volume", "SPY"): [1000, 1100],
        },
        index=pd.to_datetime(["2026-03-06", "2026-03-07"]),
    )
    adapter = IBKRAdapter(history_fetcher=lambda symbol, start, end: frame)

    bars = adapter.fetch_daily_bars("SPY", date(2026, 3, 6), date(2026, 3, 8), instrument_id=7)

    assert len(bars) == 2
    assert bars[0].open == 10.0
    assert bars[0].volume == 1000


def test_ibkr_adapter_fetch_instrument_info_uses_qualified_contract():
    broker = SimpleNamespace(
        _ib=object(),
        _qualify_contract=lambda symbol, exchange="SMART": SimpleNamespace(
            conId=12345,
            exchange="SMART",
            currency="USD",
        ),
    )
    adapter = IBKRAdapter(broker_factory=lambda: broker)

    instrument = adapter.fetch_instrument_info("SPY")

    assert instrument.vendor_ids["ibkr"] == "12345"
    assert instrument.metadata["source"] == "ibkr_adapter"


def test_placeholder_adapters_raise_not_implemented():
    with pytest.raises(NotImplementedError):
        NorgateAdapter().fetch_daily_bars("SPY", date(2026, 3, 1), date(2026, 3, 8))
    with pytest.raises(NotImplementedError):
        BarchartAdapter().fetch_instrument_info("SPY")
