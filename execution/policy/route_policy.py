"""Route policy primitives for deterministic account/broker routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from execution.policy.capability_policy import RouteAccountType


class RouteRejectCode(str, Enum):
    """Machine-readable reason codes for route rejection."""

    OK = "ok"
    KILL_SWITCH_ACTIVE = "kill_switch_active"
    MARKET_COOLDOWN_ACTIVE = "market_cooldown_active"
    ROUTE_NOT_CONFIGURED = "route_not_configured"
    BROKER_NOT_REGISTERED = "broker_not_registered"
    CAPABILITY_REJECTED = "capability_rejected"


@dataclass(frozen=True)
class RoutePolicyState:
    """Operational policy controls evaluated before broker submission."""

    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    cooldown_tickers: set[str] = field(default_factory=set)

    def has_cooldown(self, ticker: str) -> bool:
        return str(ticker or "").upper() in {t.upper() for t in self.cooldown_tickers}


@dataclass(frozen=True)
class RouteResolution:
    """Resolved account lane + broker target."""

    account_type: RouteAccountType
    broker_name: str
    matched_key: str
    used_override: bool = False


@dataclass(frozen=True)
class RouteDecision:
    """Final route policy decision."""

    allowed: bool
    reason_code: str
    message: str
    resolution: RouteResolution | None = None
    missing_capabilities: list[str] = field(default_factory=list)
