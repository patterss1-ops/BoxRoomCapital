"""M-006 Compliance rule engine with auditable pre/post-trade checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class ComplianceRuleConfig:
    """Static compliance thresholds and policy lists."""

    allowed_symbols: set[str] = field(default_factory=set)
    blocked_symbols: set[str] = field(default_factory=set)
    max_order_notional: float = 100_000.0
    max_daily_trades: int = 500
    max_position_notional: float = 500_000.0
    wash_trade_cooldown_seconds: int = 300


@dataclass(frozen=True)
class ComplianceViolation:
    """Single policy violation."""

    code: str
    message: str
    severity: str = "error"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComplianceDecision:
    """Result of compliance evaluation."""

    allowed: bool
    phase: str
    audit_id: str
    violations: list[ComplianceViolation] = field(default_factory=list)


@dataclass
class ComplianceEvent:
    """Auditable compliance event stored in-memory."""

    audit_id: str
    phase: str
    allowed: bool
    timestamp: str
    payload: dict[str, Any]
    violations: list[ComplianceViolation]


class ComplianceEngine:
    """Pre/post-trade compliance engine with rule evaluation and audit trail."""

    def __init__(self, config: ComplianceRuleConfig | None = None):
        self.config = config or ComplianceRuleConfig()
        self._events: list[ComplianceEvent] = []

    def evaluate_pre_trade(self, order: dict[str, Any], context: dict[str, Any] | None = None) -> ComplianceDecision:
        context = dict(context or {})
        violations: list[ComplianceViolation] = []
        symbol = str(order.get("symbol", "")).upper()
        qty = float(order.get("qty", 0.0) or 0.0)
        price = float(order.get("price", 0.0) or 0.0)
        notional = abs(qty * price)

        if self.config.allowed_symbols and symbol not in self.config.allowed_symbols:
            violations.append(
                ComplianceViolation(
                    code="SYMBOL_NOT_ALLOWED",
                    message=f"Symbol {symbol} is not in allowed universe",
                    details={"symbol": symbol},
                )
            )
        if symbol in self.config.blocked_symbols:
            violations.append(
                ComplianceViolation(
                    code="SYMBOL_BLOCKED",
                    message=f"Symbol {symbol} is blocked by compliance policy",
                    details={"symbol": symbol},
                )
            )
        if notional > self.config.max_order_notional:
            violations.append(
                ComplianceViolation(
                    code="MAX_ORDER_NOTIONAL_EXCEEDED",
                    message="Order notional exceeds configured limit",
                    details={"notional": notional, "limit": self.config.max_order_notional},
                )
            )

        trades_today = int(context.get("daily_trade_count", 0) or 0)
        if trades_today >= self.config.max_daily_trades:
            violations.append(
                ComplianceViolation(
                    code="MAX_DAILY_TRADES_EXCEEDED",
                    message="Daily trade count limit reached",
                    details={"daily_trade_count": trades_today, "limit": self.config.max_daily_trades},
                )
            )

        projected_position = float(context.get("projected_position_notional", 0.0) or 0.0)
        if projected_position > self.config.max_position_notional:
            violations.append(
                ComplianceViolation(
                    code="MAX_POSITION_NOTIONAL_EXCEEDED",
                    message="Projected position notional exceeds limit",
                    details={"projected": projected_position, "limit": self.config.max_position_notional},
                )
            )

        return self._finalize("pre_trade", order, violations)

    def evaluate_post_trade(self, fill: dict[str, Any], context: dict[str, Any] | None = None) -> ComplianceDecision:
        context = dict(context or {})
        violations: list[ComplianceViolation] = []
        symbol = str(fill.get("symbol", "")).upper()
        side = str(fill.get("side", "")).lower()
        fill_ts = _parse_ts(fill.get("fill_ts"))

        recent = list(context.get("recent_fills", []))
        cooldown = timedelta(seconds=int(self.config.wash_trade_cooldown_seconds))
        for prev in recent:
            if str(prev.get("symbol", "")).upper() != symbol:
                continue
            prev_side = str(prev.get("side", "")).lower()
            if {side, prev_side} != {"buy", "sell"}:
                continue
            prev_ts = _parse_ts(prev.get("fill_ts"))
            if abs(fill_ts - prev_ts) <= cooldown:
                violations.append(
                    ComplianceViolation(
                        code="WASH_TRADE_RISK",
                        message="Opposite-side fill detected inside cooldown window",
                        severity="warning",
                        details={"symbol": symbol, "cooldown_seconds": int(cooldown.total_seconds())},
                    )
                )
                break

        return self._finalize("post_trade", fill, violations)

    def breach_report(self, since_ts: str | None = None) -> list[dict[str, Any]]:
        cutoff = _parse_ts(since_ts) if since_ts else None
        out: list[dict[str, Any]] = []
        for event in self._events:
            if event.allowed:
                continue
            event_ts = _parse_ts(event.timestamp)
            if cutoff and event_ts < cutoff:
                continue
            out.append(
                {
                    "audit_id": event.audit_id,
                    "phase": event.phase,
                    "timestamp": event.timestamp,
                    "violation_codes": [v.code for v in event.violations],
                    "payload": event.payload,
                }
            )
        return out

    @property
    def audit_events(self) -> list[ComplianceEvent]:
        return list(self._events)

    def _finalize(
        self,
        phase: str,
        payload: dict[str, Any],
        violations: list[ComplianceViolation],
    ) -> ComplianceDecision:
        audit_id = uuid.uuid4().hex
        allowed = not any(v.severity == "error" for v in violations)
        now = datetime.now(timezone.utc).isoformat()
        self._events.append(
            ComplianceEvent(
                audit_id=audit_id,
                phase=phase,
                allowed=allowed,
                timestamp=now,
                payload=dict(payload),
                violations=list(violations),
            )
        )
        return ComplianceDecision(
            allowed=allowed,
            phase=phase,
            audit_id=audit_id,
            violations=list(violations),
        )


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        dt = datetime.fromisoformat(value)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
