"""System health dashboard.

K-001: Aggregated health status combining broker, data, signal, execution,
and risk subsystems into a single operator-facing health report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Overall health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class SubsystemHealth:
    """Health of a single subsystem."""

    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    message: str = ""
    last_check_at: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "last_check_at": self.last_check_at,
            "details": self.details,
        }


@dataclass
class SystemHealthReport:
    """Aggregated system health report."""

    overall_status: HealthStatus
    subsystems: list[SubsystemHealth]
    generated_at: str
    healthy_count: int = 0
    degraded_count: int = 0
    critical_count: int = 0
    unknown_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "generated_at": self.generated_at,
            "healthy_count": self.healthy_count,
            "degraded_count": self.degraded_count,
            "critical_count": self.critical_count,
            "unknown_count": self.unknown_count,
            "subsystems": [s.to_dict() for s in self.subsystems],
        }


class HealthDashboard:
    """Aggregates health checks across all subsystems."""

    def __init__(self) -> None:
        self._checks: dict[str, SubsystemHealth] = {}
        self._check_fns: dict[str, Any] = {}

    def register_check(self, name: str, check_fn: Any) -> None:
        """Register a health check function for a subsystem.

        check_fn should return a SubsystemHealth.
        """
        self._check_fns[name] = check_fn

    def update_status(self, name: str, status: HealthStatus, message: str = "", **details: Any) -> None:
        """Manually update subsystem status."""
        now = datetime.now(timezone.utc).isoformat()
        self._checks[name] = SubsystemHealth(
            name=name,
            status=status,
            message=message,
            last_check_at=now,
            details=dict(details),
        )

    def run_checks(self) -> SystemHealthReport:
        """Run all registered checks and produce aggregated report."""
        now = datetime.now(timezone.utc).isoformat()

        # Run registered check functions
        for name, fn in self._check_fns.items():
            try:
                result = fn()
                if isinstance(result, SubsystemHealth):
                    result.last_check_at = now
                    self._checks[name] = result
                else:
                    self._checks[name] = SubsystemHealth(
                        name=name,
                        status=HealthStatus.HEALTHY,
                        message="check passed",
                        last_check_at=now,
                    )
            except Exception as e:
                self._checks[name] = SubsystemHealth(
                    name=name,
                    status=HealthStatus.CRITICAL,
                    message=f"check failed: {e}",
                    last_check_at=now,
                )

        return self._compile_report(now)

    def get_report(self) -> SystemHealthReport:
        """Get latest report without re-running checks."""
        now = datetime.now(timezone.utc).isoformat()
        return self._compile_report(now)

    def get_subsystem(self, name: str) -> Optional[SubsystemHealth]:
        """Get health for a specific subsystem."""
        return self._checks.get(name)

    @property
    def subsystems(self) -> dict[str, SubsystemHealth]:
        return dict(self._checks)

    def _compile_report(self, generated_at: str) -> SystemHealthReport:
        subsystems = list(self._checks.values())
        healthy = sum(1 for s in subsystems if s.status == HealthStatus.HEALTHY)
        degraded = sum(1 for s in subsystems if s.status == HealthStatus.DEGRADED)
        critical = sum(1 for s in subsystems if s.status == HealthStatus.CRITICAL)
        unknown = sum(1 for s in subsystems if s.status == HealthStatus.UNKNOWN)

        if critical > 0:
            overall = HealthStatus.CRITICAL
        elif degraded > 0:
            overall = HealthStatus.DEGRADED
        elif unknown > 0 and healthy == 0:
            overall = HealthStatus.UNKNOWN
        elif healthy > 0:
            overall = HealthStatus.HEALTHY
        else:
            overall = HealthStatus.UNKNOWN

        return SystemHealthReport(
            overall_status=overall,
            subsystems=subsystems,
            generated_at=generated_at,
            healthy_count=healthy,
            degraded_count=degraded,
            critical_count=critical,
            unknown_count=unknown,
        )
