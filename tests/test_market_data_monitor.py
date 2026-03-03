"""Tests for I-005 market data health monitor."""

from __future__ import annotations

import time

from data.market_data_monitor import (
    DataFreshnessCheck,
    MarketDataMonitor,
    ProviderHealth,
    ProviderStatus,
)


class TestProviderHealth:
    def test_to_dict(self):
        p = ProviderHealth(name="yfinance", status=ProviderStatus.HEALTHY)
        d = p.to_dict()
        assert d["name"] == "yfinance"
        assert d["status"] == "healthy"


class TestMarketDataMonitor:
    def test_default_provider(self):
        mon = MarketDataMonitor()
        assert "yfinance" in mon.providers

    def test_custom_providers(self):
        mon = MarketDataMonitor(providers=["yfinance", "iqfeed"])
        assert len(mon.providers) == 2

    def test_record_success_updates_status(self):
        mon = MarketDataMonitor()
        mon.record_failure("yfinance")
        assert mon.providers["yfinance"].status == ProviderStatus.DEGRADED
        mon.record_success("yfinance", ticker="AAPL")
        assert mon.providers["yfinance"].status == ProviderStatus.HEALTHY
        assert mon.providers["yfinance"].consecutive_failures == 0

    def test_consecutive_failures_marks_down(self):
        mon = MarketDataMonitor(failure_threshold=3)
        mon.record_failure("yfinance")
        assert mon.providers["yfinance"].status == ProviderStatus.DEGRADED
        mon.record_failure("yfinance")
        assert mon.providers["yfinance"].status == ProviderStatus.DEGRADED
        mon.record_failure("yfinance")
        assert mon.providers["yfinance"].status == ProviderStatus.DOWN

    def test_freshness_check_missing(self):
        mon = MarketDataMonitor()
        check = mon.check_freshness("AAPL")
        assert check.status == "missing"
        assert check.is_fresh is False

    def test_freshness_check_fresh(self):
        mon = MarketDataMonitor()
        mon.record_success("yfinance", ticker="AAPL")
        check = mon.check_freshness("AAPL")
        assert check.status == "ok"
        assert check.is_fresh is True
        assert check.staleness_secs < 5.0

    def test_freshness_check_stale(self):
        mon = MarketDataMonitor(staleness_threshold_secs=0.01)
        mon.record_success("yfinance", ticker="AAPL")
        time.sleep(0.02)
        check = mon.check_freshness("AAPL")
        assert check.status == "stale"
        assert check.is_fresh is False

    def test_get_healthy_provider(self):
        mon = MarketDataMonitor(providers=["primary", "backup"])
        assert mon.get_healthy_provider() == "primary"

    def test_get_healthy_provider_fallback_to_degraded(self):
        mon = MarketDataMonitor(providers=["primary", "backup"], failure_threshold=3)
        # Take primary down
        for _ in range(3):
            mon.record_failure("primary")
        assert mon.providers["primary"].status == ProviderStatus.DOWN
        # Degrade backup
        mon.record_failure("backup")
        assert mon.get_healthy_provider() == "backup"  # degraded is still usable

    def test_get_healthy_provider_all_down(self):
        mon = MarketDataMonitor(providers=["primary"], failure_threshold=1)
        mon.record_failure("primary")
        assert mon.get_healthy_provider() is None

    def test_choose_provider_switches_from_down_primary(self):
        mon = MarketDataMonitor(providers=["primary", "backup"], failure_threshold=1)
        mon.record_failure("primary")
        selected = mon.choose_provider(preferred="primary")
        assert selected == "backup"
        assert mon.active_provider == "backup"

    def test_alerts_on_status_changes_and_switch(self):
        alerts: list[tuple[str, str]] = []
        mon = MarketDataMonitor(
            providers=["primary", "backup"],
            failure_threshold=1,
            alert_fn=lambda msg, level: alerts.append((msg, level)) or True,
        )
        mon.record_failure("primary")
        mon.choose_provider(preferred="primary")
        mon.record_success("primary", ticker="AAPL")
        assert any("MARKET_DATA_PROVIDER_DOWN" in msg for msg, _ in alerts)
        assert any("MARKET_DATA_PROVIDER_SWITCH" in msg for msg, _ in alerts)
        assert any("MARKET_DATA_PROVIDER_RECOVERED" in msg for msg, _ in alerts)

    def test_status_summary(self):
        mon = MarketDataMonitor(providers=["yfinance", "iqfeed"])
        mon.record_success("yfinance", ticker="AAPL")
        mon.record_success("yfinance", ticker="MSFT")
        summary = mon.get_status_summary()
        assert summary["total_providers"] == 2
        assert summary["healthy_providers"] == 2
        assert summary["tracked_tickers"] == 2
        assert summary["active_provider"] == "yfinance"

    def test_freshness_to_dict(self):
        check = DataFreshnessCheck(
            ticker="AAPL",
            provider="yfinance",
            is_fresh=True,
            staleness_secs=1.23,
            status="ok",
        )
        d = check.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["staleness_secs"] == 1.2
