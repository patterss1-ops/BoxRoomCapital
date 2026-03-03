"""Operational runbook generator.

K-005: Auto-generate operational checklists from system state.
Produces pre-market, intraday, and post-market runbooks based on
current system configuration and health status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class ChecklistItem:
    """A single item in an operational checklist."""

    step: int
    action: str
    subsystem: str
    severity: str = "info"  # info | warning | critical
    automated: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "action": self.action,
            "subsystem": self.subsystem,
            "severity": self.severity,
            "automated": self.automated,
            "notes": self.notes,
        }


@dataclass
class Runbook:
    """Generated operational runbook."""

    title: str
    phase: str  # pre_market | intraday | post_market | on_demand
    generated_at: str
    items: list[ChecklistItem] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "phase": self.phase,
            "generated_at": self.generated_at,
            "total_items": len(self.items),
            "items": [i.to_dict() for i in self.items],
            "context": self.context,
        }

    def to_text(self) -> str:
        lines = [
            f"{'=' * 50}",
            f"  RUNBOOK: {self.title}",
            f"  Phase: {self.phase}",
            f"  Generated: {self.generated_at}",
            f"{'=' * 50}",
            "",
        ]
        for item in self.items:
            marker = "[AUTO]" if item.automated else "[MANUAL]"
            sev = f"[{item.severity.upper()}]" if item.severity != "info" else ""
            lines.append(f"  {item.step}. {marker}{sev} [{item.subsystem}] {item.action}")
            if item.notes:
                lines.append(f"     Note: {item.notes}")
        return "\n".join(lines)


class RunbookGenerator:
    """Generates operational runbooks based on system state."""

    def __init__(
        self,
        strategies: Optional[list[str]] = None,
        brokers: Optional[list[str]] = None,
        data_providers: Optional[list[str]] = None,
    ) -> None:
        self._strategies = strategies or ["ibs_mean_reversion", "trend_following"]
        self._brokers = brokers or ["ig"]
        self._data_providers = data_providers or ["yfinance"]

    def generate_pre_market(self) -> Runbook:
        """Generate pre-market opening checklist."""
        now = datetime.now(timezone.utc).isoformat()
        items = []
        step = 1

        # Data checks
        for provider in self._data_providers:
            items.append(ChecklistItem(
                step=step, action=f"Verify {provider} data feed is active",
                subsystem="data", automated=True,
            ))
            step += 1

        # Broker checks
        for broker in self._brokers:
            items.append(ChecklistItem(
                step=step, action=f"Confirm {broker} API connection",
                subsystem="broker", automated=True,
            ))
            step += 1
            items.append(ChecklistItem(
                step=step, action=f"Check {broker} account balance and margin",
                subsystem="broker", automated=True,
            ))
            step += 1

        # Risk checks
        items.append(ChecklistItem(
            step=step, action="Verify risk limits are within bounds",
            subsystem="risk", automated=True,
        ))
        step += 1
        items.append(ChecklistItem(
            step=step, action="Check overnight position changes",
            subsystem="risk", severity="warning",
        ))
        step += 1

        # Strategy checks
        for strat in self._strategies:
            items.append(ChecklistItem(
                step=step, action=f"Verify {strat} signal engine ready",
                subsystem="signal", automated=True,
            ))
            step += 1

        return Runbook(
            title="Pre-Market Opening Checklist",
            phase="pre_market",
            generated_at=now,
            items=items,
            context={
                "strategies": self._strategies,
                "brokers": self._brokers,
                "providers": self._data_providers,
            },
        )

    def generate_post_market(self) -> Runbook:
        """Generate post-market closing checklist."""
        now = datetime.now(timezone.utc).isoformat()
        items = []
        step = 1

        items.append(ChecklistItem(
            step=step, action="Run EOD reconciliation",
            subsystem="reconciliation", automated=True,
        ))
        step += 1

        items.append(ChecklistItem(
            step=step, action="Generate daily NAV report",
            subsystem="fund", automated=True,
        ))
        step += 1

        items.append(ChecklistItem(
            step=step, action="Check for unreconciled positions",
            subsystem="reconciliation", severity="warning",
        ))
        step += 1

        items.append(ChecklistItem(
            step=step, action="Review execution quality metrics",
            subsystem="execution",
        ))
        step += 1

        items.append(ChecklistItem(
            step=step, action="Check strategy decay indicators",
            subsystem="analytics", automated=True,
        ))
        step += 1

        items.append(ChecklistItem(
            step=step, action="Archive daily trade journal",
            subsystem="audit", automated=True,
        ))
        step += 1

        return Runbook(
            title="Post-Market Closing Checklist",
            phase="post_market",
            generated_at=now,
            items=items,
        )

    def generate_incident(self, incident_type: str = "generic") -> Runbook:
        """Generate incident response runbook."""
        now = datetime.now(timezone.utc).isoformat()
        items = []
        step = 1

        items.append(ChecklistItem(
            step=step, action="Acknowledge incident and capture timestamp",
            subsystem="ops", severity="critical",
        ))
        step += 1

        if incident_type == "broker_disconnect":
            items.append(ChecklistItem(
                step=step, action="Check broker circuit breaker state",
                subsystem="broker", automated=True, severity="critical",
            ))
            step += 1
            items.append(ChecklistItem(
                step=step, action="Verify pending orders are safe",
                subsystem="execution", severity="critical",
            ))
            step += 1
            items.append(ChecklistItem(
                step=step, action="Attempt reconnection (max 3 retries)",
                subsystem="broker", automated=True,
            ))
            step += 1
        elif incident_type == "data_stale":
            items.append(ChecklistItem(
                step=step, action="Check data provider health",
                subsystem="data", automated=True, severity="warning",
            ))
            step += 1
            items.append(ChecklistItem(
                step=step, action="Switch to backup data provider",
                subsystem="data", automated=True,
            ))
            step += 1
        else:
            items.append(ChecklistItem(
                step=step, action="Run full system health check",
                subsystem="health", automated=True,
            ))
            step += 1

        items.append(ChecklistItem(
            step=step, action="Document incident and resolution",
            subsystem="ops",
        ))
        step += 1

        return Runbook(
            title=f"Incident Response: {incident_type}",
            phase="on_demand",
            generated_at=now,
            items=items,
            context={"incident_type": incident_type},
        )
