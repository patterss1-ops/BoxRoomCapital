"""Vendor adapter interfaces for research market data ingestion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

import pandas as pd
import yfinance as yf

from broker.ibkr import IBKRBroker
from research.market_data.instruments import InstrumentMaster
from research.market_data.raw_bars import RawBar


class VendorAdapter(ABC):
    """Base interface for market data vendors."""

    @abstractmethod
    def vendor_name(self) -> str:
        """Return the canonical vendor key."""

    @abstractmethod
    def fetch_daily_bars(self, symbol: str, start: date, end: date, instrument_id: int = 0) -> list[RawBar]:
        """Fetch vendor-native daily bars."""

    @abstractmethod
    def fetch_instrument_info(
        self,
        symbol: str,
        asset_class: str = "equity",
        venue: str = "SMART",
        currency: str = "USD",
    ) -> InstrumentMaster:
        """Fetch basic instrument metadata."""


class IBKRAdapter(VendorAdapter):
    """IBKR-backed adapter with a yfinance fallback for historical bars."""

    def __init__(self, broker_factory=IBKRBroker, history_fetcher=None):
        self._broker_factory = broker_factory
        self._history_fetcher = history_fetcher or self._fetch_yfinance_history

    def vendor_name(self) -> str:
        return "ibkr"

    def fetch_daily_bars(self, symbol: str, start: date, end: date, instrument_id: int = 0) -> list[RawBar]:
        frame = self._normalize_history_frame(self._history_fetcher(symbol, start, end), symbol)
        bars: list[RawBar] = []
        if frame is None or getattr(frame, "empty", True):
            return bars

        for index, row in frame.iterrows():
            timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else datetime.combine(index, datetime.min.time())
            bars.append(
                RawBar(
                    instrument_id=instrument_id,
                    vendor=self.vendor_name(),
                    bar_timestamp=timestamp,
                    session_code="ibkr_daily",
                    open=_coerce_float(row.get("Open")),
                    high=_coerce_float(row.get("High")),
                    low=_coerce_float(row.get("Low")),
                    close=_coerce_float(row.get("Close")),
                    volume=_coerce_int(row.get("Volume")),
                )
            )
        return bars

    def fetch_instrument_info(
        self,
        symbol: str,
        asset_class: str = "equity",
        venue: str = "SMART",
        currency: str = "USD",
    ) -> InstrumentMaster:
        vendor_ids = {"ibkr": symbol}
        broker = self._broker_factory()
        if getattr(broker, "_ib", None) is not None:
            try:
                contract = broker._qualify_contract(symbol, exchange=venue)
                vendor_ids["ibkr"] = str(getattr(contract, "conId", symbol))
                venue = getattr(contract, "exchange", venue) or venue
                currency = getattr(contract, "currency", currency) or currency
            except Exception:
                pass

        return InstrumentMaster(
            symbol=symbol,
            asset_class=asset_class,
            venue=venue,
            currency=currency,
            vendor_ids=vendor_ids,
            metadata={"source": "ibkr_adapter"},
        )

    @staticmethod
    def _fetch_yfinance_history(symbol: str, start: date, end: date):
        return yf.download(
            symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
            progress=False,
        )

    @staticmethod
    def _normalize_history_frame(frame, symbol: str):
        if frame is None or getattr(frame, "empty", True):
            return frame
        columns = getattr(frame, "columns", None)
        if getattr(columns, "nlevels", 1) <= 1:
            return frame

        ticker_level = columns.get_level_values(-1)
        selected_ticker = symbol if symbol in ticker_level else ticker_level[0]
        normalized = frame.xs(selected_ticker, axis=1, level=-1, drop_level=True)
        if getattr(normalized.columns, "nlevels", 1) > 1:
            normalized.columns = normalized.columns.get_level_values(0)
        return normalized


def _coerce_float(value) -> float | None:
    scalar = _coerce_scalar(value)
    if scalar is None or pd.isna(scalar):
        return None
    return float(scalar)


def _coerce_int(value) -> int | None:
    scalar = _coerce_scalar(value)
    if scalar is None or pd.isna(scalar):
        return None
    return int(scalar)


def _coerce_scalar(value):
    if isinstance(value, pd.Series):
        non_null = value.dropna()
        if non_null.empty:
            return None
        return non_null.iloc[0]
    return value


class NorgateAdapter(VendorAdapter):
    """Interface placeholder for Norgate integration."""

    def vendor_name(self) -> str:
        return "norgate"

    def fetch_daily_bars(self, symbol: str, start: date, end: date, instrument_id: int = 0) -> list[RawBar]:
        raise NotImplementedError("Norgate adapter is a placeholder in this tranche")

    def fetch_instrument_info(
        self,
        symbol: str,
        asset_class: str = "equity",
        venue: str = "SMART",
        currency: str = "USD",
    ) -> InstrumentMaster:
        raise NotImplementedError("Norgate adapter is a placeholder in this tranche")


class BarchartAdapter(VendorAdapter):
    """Interface placeholder for Barchart integration."""

    def vendor_name(self) -> str:
        return "barchart"

    def fetch_daily_bars(self, symbol: str, start: date, end: date, instrument_id: int = 0) -> list[RawBar]:
        raise NotImplementedError("Barchart adapter is a placeholder in this tranche")

    def fetch_instrument_info(
        self,
        symbol: str,
        asset_class: str = "equity",
        venue: str = "SMART",
        currency: str = "USD",
    ) -> InstrumentMaster:
        raise NotImplementedError("Barchart adapter is a placeholder in this tranche")
