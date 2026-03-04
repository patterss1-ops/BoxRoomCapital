"""Risk limit monitoring daemon.

K-003: Continuous risk limit monitoring with pre-breach warnings.
Tracks exposure, drawdown, and concentration limits against thresholds
and emits alerts before hard limits are hit.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional


class LimitStatus(str, Enum):
    """Status of a risk limit."""
    OK = "ok"
    WARNING = "warning"
    BREACH = "breach"


@dataclass
class LimitConfig:
    """Configuration for a single risk limit."""

    name: str
    warn_threshold: float  # Warning level (e.g. 80% of limit)
    hard_limit: float  # Hard breach level
    metric_fn: Optional[Callable[[], float]] = None  # Function to fetch current value
    description: str = ""


@dataclass
class LimitCheckResult:
    """Result of checking a single limit."""

    name: str
    current_value: float
    warn_threshold: float
    hard_limit: float
    status: LimitStatus
    utilisation_pct: float  # current / hard_limit * 100
    headroom: float  # hard_limit - current
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current_value": round(self.current_value, 4),
            "warn_threshold": self.warn_threshold,
            "hard_limit": self.hard_limit,
            "status": self.status.value,
            "utilisation_pct": round(self.utilisation_pct, 2),
            "headroom": round(self.headroom, 4),
            "checked_at": self.checked_at,
        }


@dataclass
class LimitMonitorReport:
    """Aggregated limit monitoring report."""

    results: list[LimitCheckResult]
    overall_status: LimitStatus
    breaches: int = 0
    warnings: int = 0
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "breaches": self.breaches,
            "warnings": self.warnings,
            "generated_at": self.generated_at,
            "results": [r.to_dict() for r in self.results],
        }


class LimitMonitor:
    """Monitors risk limits and emits pre-breach warnings."""

    def __init__(
        self,
        alert_fn: Optional[Callable[[str, LimitCheckResult], None]] = None,
        max_history: int = 1000,
    ) -> None:
        self._limits: dict[str, LimitConfig] = {}
        self._history: deque[LimitCheckResult] = deque(maxlen=max_history)
        self._alert_fn = alert_fn

    def add_limit(self, config: LimitConfig) -> None:
        """Register a limit to monitor."""
        self._limits[config.name] = config

    def remove_limit(self, name: str) -> bool:
        """Remove a monitored limit."""
        return self._limits.pop(name, None) is not None

    def check_limit(
        self,
        name: str,
        current_value: Optional[float] = None,
    ) -> LimitCheckResult:
        """Check a single limit against its thresholds.

        If current_value is provided, use it. Otherwise call the metric_fn.
        """
        config = self._limits.get(name)
        if config is None:
            raise KeyError(f"Unknown limit: {name}")

        if current_value is None and config.metric_fn is not None:
            current_value = config.metric_fn()
        elif current_value is None:
            current_value = 0.0

        now = datetime.now(timezone.utc).isoformat()

        # Determine status
        if abs(config.hard_limit) < 1e-12:
            status = LimitStatus.OK
            util_pct = 0.0
        elif current_value >= config.hard_limit:
            status = LimitStatus.BREACH
            util_pct = current_value / config.hard_limit * 100.0
        elif current_value >= config.warn_threshold:
            status = LimitStatus.WARNING
            util_pct = current_value / config.hard_limit * 100.0
        else:
            status = LimitStatus.OK
            util_pct = current_value / config.hard_limit * 100.0

        headroom = config.hard_limit - current_value

        result = LimitCheckResult(
            name=name,
            current_value=current_value,
            warn_threshold=config.warn_threshold,
            hard_limit=config.hard_limit,
            status=status,
            utilisation_pct=util_pct,
            headroom=headroom,
            checked_at=now,
        )

        self._history.append(result)

        # Fire alert
        if status != LimitStatus.OK and self._alert_fn:
            self._alert_fn(status.value, result)

        return result

    def check_all(self) -> LimitMonitorReport:
        """Check all registered limits and produce aggregated report."""
        now = datetime.now(timezone.utc).isoformat()
        results = []

        for name in self._limits:
            result = self.check_limit(name)
            results.append(result)

        breaches = sum(1 for r in results if r.status == LimitStatus.BREACH)
        warnings = sum(1 for r in results if r.status == LimitStatus.WARNING)

        if breaches > 0:
            overall = LimitStatus.BREACH
        elif warnings > 0:
            overall = LimitStatus.WARNING
        else:
            overall = LimitStatus.OK

        return LimitMonitorReport(
            results=results,
            overall_status=overall,
            breaches=breaches,
            warnings=warnings,
            generated_at=now,
        )

    @property
    def history(self) -> list[LimitCheckResult]:
        return list(self._history)

    @property
    def limits(self) -> dict[str, LimitConfig]:
        return dict(self._limits)
