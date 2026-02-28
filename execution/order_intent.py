"""Unified order intent model for broker-agnostic execution flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from execution.policy.capability_policy import RouteAccountType


class OrderSide(str, Enum):
    """Supported order directions."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Supported order type envelope."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderIntentStatus(str, Enum):
    """Canonical order lifecycle states."""

    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


ALLOWED_ACTORS = {"system", "operator"}


def normalize_actor(actor: str) -> str:
    """Normalize and validate actor for audit transitions."""
    value = str(actor or "").strip().lower()
    if value not in ALLOWED_ACTORS:
        raise ValueError(f"Unsupported actor '{actor}'. Expected one of: {sorted(ALLOWED_ACTORS)}")
    return value


def normalize_status(status: str | OrderIntentStatus) -> OrderIntentStatus:
    """Convert string/enum status to canonical enum value."""
    if isinstance(status, OrderIntentStatus):
        return status
    return OrderIntentStatus(str(status).strip().lower())


def _normalize_risk_tags(risk_tags: list[str] | None) -> list[str]:
    if not risk_tags:
        return []
    values = []
    for tag in risk_tags:
        value = str(tag or "").strip()
        if value:
            values.append(value)
    return sorted(set(values))


@dataclass
class OrderIntent:
    """
    Canonical execution intent.

    This is the broker-agnostic contract that execution routing/policy can consume.
    """

    strategy_id: str
    strategy_version: str
    sleeve: str
    account_type: RouteAccountType | str
    broker_target: str
    instrument: str
    side: OrderSide | str
    qty: float
    order_type: OrderType | str
    risk_tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.account_type = RouteAccountType(self.account_type)
        self.side = OrderSide(str(self.side).upper())
        self.order_type = OrderType(str(self.order_type).upper())
        self.qty = float(self.qty)
        self.risk_tags = _normalize_risk_tags(self.risk_tags)
        if self.metadata is None:
            self.metadata = {}
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")
        self.validate()

    def validate(self) -> None:
        """Validate required fields and numeric bounds."""
        required_fields = {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "sleeve": self.sleeve,
            "broker_target": self.broker_target,
            "instrument": self.instrument,
        }
        for field_name, value in required_fields.items():
            if not str(value or "").strip():
                raise ValueError(f"{field_name} is required")
        if self.qty <= 0:
            raise ValueError("qty must be > 0")

    def to_payload(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary for persistence/audit."""
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "sleeve": self.sleeve,
            "account_type": self.account_type.value,
            "broker_target": self.broker_target,
            "instrument": self.instrument,
            "side": self.side.value,
            "qty": self.qty,
            "order_type": self.order_type.value,
            "risk_tags": list(self.risk_tags),
            "metadata": dict(self.metadata),
        }
