"""Alert router — wires execution events to notification channels.

I-001: Routes circuit breaker trips, promotion gate blocks, EOD reconciliation
reports, drawdown alerts, and strategy decay warnings to the Telegram
notification pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """A structured alert event."""

    category: str  # circuit_breaker | promotion_gate | eod_report | drawdown | decay | error
    severity: str  # info | warning | critical
    title: str
    message: str
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


# Type alias for notification callback
NotifyFn = Callable[[str, str], bool]


class AlertRouter:
    """Routes structured alerts to notification channels.

    Accepts a notify function with signature (message: str, icon: str) -> bool.
    This decouples routing logic from the actual notification transport.
    """

    def __init__(self, notify_fn: Optional[NotifyFn] = None):
        self._notify_fn = notify_fn
        self._history: list[Alert] = []
        self._max_history = 100
        self._suppressed_categories: set[str] = set()

    @property
    def history(self) -> list[Alert]:
        return list(self._history)

    def suppress_category(self, category: str) -> None:
        """Suppress alerts for a category (useful in tests)."""
        self._suppressed_categories.add(category)

    def unsuppress_category(self, category: str) -> None:
        """Re-enable alerts for a category."""
        self._suppressed_categories.discard(category)

    def route(self, alert: Alert) -> bool:
        """Route an alert to the notification channel."""
        self._record(alert)

        if alert.category in self._suppressed_categories:
            return False

        if self._notify_fn is None:
            return False

        icon = _severity_icon(alert.severity)
        message = f"{alert.title}\n{alert.message}"

        try:
            return self._notify_fn(message, icon)
        except Exception as exc:
            logger.warning("Alert delivery failed: %s", exc)
            return False

    def _record(self, alert: Alert) -> None:
        """Record alert in history ring buffer."""
        self._history.append(alert)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    # ─── Convenience builders ──────────────────────────────────────────

    def circuit_breaker_trip(
        self,
        broker_name: str,
        failure_count: int,
        state: str,
    ) -> bool:
        """Alert: broker circuit breaker tripped."""
        return self.route(Alert(
            category="circuit_breaker",
            severity="critical",
            title=f"CIRCUIT BREAKER — {broker_name}",
            message=(
                f"State: {state}\n"
                f"Failures: {failure_count}\n"
                f"Broker API calls suspended until recovery."
            ),
            metadata={"broker": broker_name, "failures": failure_count, "state": state},
        ))

    def circuit_breaker_recovery(
        self,
        broker_name: str,
    ) -> bool:
        """Alert: broker circuit breaker recovered."""
        return self.route(Alert(
            category="circuit_breaker",
            severity="info",
            title=f"CIRCUIT RECOVERED — {broker_name}",
            message="Broker API calls resumed normally.",
            metadata={"broker": broker_name, "state": "closed"},
        ))

    def promotion_gate_block(
        self,
        strategy_key: str,
        reason_code: str,
        message: str,
    ) -> bool:
        """Alert: promotion gate blocked an entry."""
        return self.route(Alert(
            category="promotion_gate",
            severity="warning",
            title=f"PROMOTION GATE — {strategy_key}",
            message=f"Reason: {reason_code}\n{message}",
            metadata={"strategy": strategy_key, "reason_code": reason_code},
        ))

    def eod_reconciliation_report(
        self,
        report_date: str,
        status: str,
        mismatches: int,
        total_pnl: float,
    ) -> bool:
        """Alert: EOD reconciliation completed."""
        severity = "info" if status == "clean" else "warning"
        pnl_sign = "+" if total_pnl >= 0 else ""
        return self.route(Alert(
            category="eod_report",
            severity=severity,
            title=f"EOD REPORT — {report_date}",
            message=(
                f"Status: {status}\n"
                f"Mismatches: {mismatches}\n"
                f"Daily P&L: {pnl_sign}{total_pnl:.2f}"
            ),
            metadata={"date": report_date, "status": status, "mismatches": mismatches},
        ))

    def drawdown_alert(
        self,
        drawdown_pct: float,
        threshold_pct: float,
        period: str,
        action: str,
    ) -> bool:
        """Alert: drawdown threshold breached."""
        return self.route(Alert(
            category="drawdown",
            severity="critical",
            title=f"DRAWDOWN BREACH — {period}",
            message=(
                f"Current: {drawdown_pct:.2f}%\n"
                f"Threshold: {threshold_pct:.2f}%\n"
                f"Action: {action}"
            ),
            metadata={
                "drawdown_pct": drawdown_pct,
                "threshold_pct": threshold_pct,
                "period": period,
            },
        ))

    def strategy_decay_warning(
        self,
        strategy_key: str,
        metric: str,
        current_value: float,
        threshold: float,
    ) -> bool:
        """Alert: strategy performance decay detected."""
        return self.route(Alert(
            category="decay",
            severity="warning",
            title=f"STRATEGY DECAY — {strategy_key}",
            message=(
                f"Metric: {metric}\n"
                f"Current: {current_value:.4f}\n"
                f"Threshold: {threshold:.4f}\n"
                f"Review recommended."
            ),
            metadata={
                "strategy": strategy_key,
                "metric": metric,
                "value": current_value,
                "threshold": threshold,
            },
        ))

    def generic_error(self, source: str, error_message: str) -> bool:
        """Alert: generic error from any subsystem."""
        return self.route(Alert(
            category="error",
            severity="critical",
            title=f"ERROR — {source}",
            message=error_message,
            metadata={"source": source},
        ))


def _severity_icon(severity: str) -> str:
    """Map severity to notification icon."""
    return {
        "info": "ℹ️",
        "warning": "⚠️",
        "critical": "🚨",
    }.get(severity, "📋")
