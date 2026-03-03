"""Market data health monitor + fallback provider management.

I-005: Monitors market data freshness, detects stale/missing data,
and tracks provider health status. Integrates with the alert router
to notify on degradation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ProviderStatus(str, Enum):
    """Health status of a market data provider."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class ProviderHealth:
    """Health state for a single data provider."""

    name: str
    status: ProviderStatus = ProviderStatus.HEALTHY
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    consecutive_failures: int = 0
    failure_threshold: int = 3
    staleness_threshold_secs: float = 300.0  # 5 minutes

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "consecutive_failures": self.consecutive_failures,
        }


@dataclass
class DataFreshnessCheck:
    """Result of checking market data freshness."""

    ticker: str
    provider: str
    is_fresh: bool
    last_update_at: Optional[str] = None
    staleness_secs: float = 0.0
    status: str = "ok"  # ok | stale | missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "provider": self.provider,
            "is_fresh": self.is_fresh,
            "last_update_at": self.last_update_at,
            "staleness_secs": round(self.staleness_secs, 1),
            "status": self.status,
        }


class MarketDataMonitor:
    """Monitors market data providers and tracks data freshness."""

    def __init__(
        self,
        providers: Optional[list[str]] = None,
        failure_threshold: int = 3,
        staleness_threshold_secs: float = 300.0,
        alert_fn: Optional[Callable[[str, str], bool]] = None,
    ):
        self._providers: dict[str, ProviderHealth] = {}
        self._data_timestamps: dict[str, str] = {}  # ticker → last update ISO
        self._alert_fn = alert_fn

        provider_names = providers or ["yfinance"]
        self._active_provider = provider_names[0] if provider_names else None
        for name in provider_names:
            self._providers[name] = ProviderHealth(
                name=name,
                failure_threshold=failure_threshold,
                staleness_threshold_secs=staleness_threshold_secs,
            )

    @property
    def providers(self) -> dict[str, ProviderHealth]:
        return dict(self._providers)

    @property
    def active_provider(self) -> Optional[str]:
        return self._active_provider

    def record_success(self, provider: str, ticker: Optional[str] = None) -> None:
        """Record a successful data fetch."""
        now = datetime.now(timezone.utc).isoformat()
        if provider in self._providers:
            p = self._providers[provider]
            previous = p.status
            p.last_success_at = now
            p.consecutive_failures = 0
            p.status = ProviderStatus.HEALTHY
            if previous != ProviderStatus.HEALTHY:
                self._emit_alert(
                    f"MARKET_DATA_PROVIDER_RECOVERED: {provider} status={p.status.value}",
                    "info",
                )

        if ticker:
            self._data_timestamps[ticker] = now

    def record_failure(self, provider: str) -> None:
        """Record a failed data fetch."""
        now = datetime.now(timezone.utc).isoformat()
        if provider in self._providers:
            p = self._providers[provider]
            previous = p.status
            p.last_failure_at = now
            p.consecutive_failures += 1

            if p.consecutive_failures >= p.failure_threshold:
                p.status = ProviderStatus.DOWN
            elif p.consecutive_failures >= 1:
                p.status = ProviderStatus.DEGRADED
            if p.status != previous:
                self._emit_alert(
                    (
                        "MARKET_DATA_PROVIDER_DEGRADED"
                        if p.status == ProviderStatus.DEGRADED
                        else "MARKET_DATA_PROVIDER_DOWN"
                    )
                    + f": {provider} failures={p.consecutive_failures}",
                    "warning",
                )

    def check_freshness(
        self,
        ticker: str,
        provider: str = "yfinance",
    ) -> DataFreshnessCheck:
        """Check if market data for a ticker is fresh."""
        prov = self._providers.get(provider)
        threshold = prov.staleness_threshold_secs if prov else 300.0

        last_update = self._data_timestamps.get(ticker)
        if last_update is None:
            return DataFreshnessCheck(
                ticker=ticker,
                provider=provider,
                is_fresh=False,
                status="missing",
            )

        now = datetime.now(timezone.utc)
        last_dt = datetime.fromisoformat(last_update)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)

        staleness = (now - last_dt).total_seconds()

        if staleness > threshold:
            return DataFreshnessCheck(
                ticker=ticker,
                provider=provider,
                is_fresh=False,
                last_update_at=last_update,
                staleness_secs=staleness,
                status="stale",
            )

        return DataFreshnessCheck(
            ticker=ticker,
            provider=provider,
            is_fresh=True,
            last_update_at=last_update,
            staleness_secs=staleness,
            status="ok",
        )

    def get_healthy_provider(self) -> Optional[str]:
        """Get the first healthy provider, or None if all are down."""
        for name, health in self._providers.items():
            if health.status == ProviderStatus.HEALTHY:
                return name
        # Fallback: try degraded providers
        for name, health in self._providers.items():
            if health.status == ProviderStatus.DEGRADED:
                return name
        return None

    def choose_provider(self, preferred: Optional[str] = None) -> Optional[str]:
        """Select provider with healthy-first fallback and emit switch alerts."""
        selected: Optional[str] = None
        if preferred and preferred in self._providers:
            preferred_status = self._providers[preferred].status
            if preferred_status == ProviderStatus.HEALTHY:
                selected = preferred

        if selected is None:
            selected = self.get_healthy_provider()

        if selected and self._active_provider and selected != self._active_provider:
            self._emit_alert(
                f"MARKET_DATA_PROVIDER_SWITCH: {self._active_provider} -> {selected}",
                "warning",
            )
        if selected:
            self._active_provider = selected
        return selected

    def get_status_summary(self) -> dict[str, Any]:
        """Get overall market data health summary."""
        providers = {n: p.to_dict() for n, p in self._providers.items()}
        healthy_count = sum(
            1 for p in self._providers.values()
            if p.status == ProviderStatus.HEALTHY
        )
        return {
            "total_providers": len(self._providers),
            "healthy_providers": healthy_count,
            "providers": providers,
            "tracked_tickers": len(self._data_timestamps),
            "active_provider": self._active_provider,
        }

    def _emit_alert(self, message: str, level: str) -> None:
        if self._alert_fn is None:
            return
        try:
            self._alert_fn(message, level)
        except Exception:
            logger.warning("Market data alert callback failed", exc_info=True)
