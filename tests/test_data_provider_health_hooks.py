"""Tests for DataProvider market-monitor health hooks."""

from __future__ import annotations

import pandas as pd

from data.provider import DataProvider


class _StubMonitor:
    def __init__(self):
        self.success_calls: list[tuple[str, str]] = []
        self.failure_calls: list[str] = []

    def record_success(self, provider: str, ticker: str | None = None):
        self.success_calls.append((provider, ticker or ""))

    def record_failure(self, provider: str):
        self.failure_calls.append(provider)


def _sample_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000, 1100],
        },
        index=pd.to_datetime(["2026-03-01", "2026-03-02"]),
    )


def test_provider_records_success(monkeypatch):
    monitor = _StubMonitor()
    provider = DataProvider(market_monitor=monitor, provider_name="yfinance")
    monkeypatch.setattr("data.provider.yf.download", lambda *args, **kwargs: _sample_bars())

    bars = provider.get_daily_bars("AAPL", force_refresh=True)
    assert not bars.empty
    assert monitor.success_calls == [("yfinance", "AAPL")]
    assert monitor.failure_calls == []


def test_provider_records_failure_on_empty_response(monkeypatch):
    monitor = _StubMonitor()
    provider = DataProvider(market_monitor=monitor, provider_name="yfinance")
    monkeypatch.setattr("data.provider.yf.download", lambda *args, **kwargs: pd.DataFrame())

    bars = provider.get_daily_bars("AAPL", force_refresh=True)
    assert bars.empty
    assert monitor.failure_calls == ["yfinance"]
