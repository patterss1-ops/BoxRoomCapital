"""Broker capability policy checks used in pre-trade validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from broker.base import BaseBroker


class RouteAccountType(str, Enum):
    """Supported account routing lanes for capability checks."""

    ISA = "ISA"
    SIPP = "SIPP"
    GIA = "GIA"
    SPREADBET = "SPREADBET"
    PAPER = "PAPER"


@dataclass(frozen=True)
class StrategyRequirements:
    """Capability requirements attached to a strategy execution intent."""

    requires_short: bool = False
    requires_options: bool = False
    requires_futures: bool = False
    requires_spreadbet: bool = False
    requires_cfd: bool = False
    requires_live: bool = False
    requires_paper: bool = False
    requires_spot_etf: bool = False


@dataclass(frozen=True)
class CapabilityOrderIntent:
    """Minimal order intent payload used for A-001 pre-trade validation."""

    strategy_id: str
    broker_name: str
    account_type: RouteAccountType
    requirements: StrategyRequirements


@dataclass
class CapabilityValidationResult:
    """Result of broker capability validation."""

    allowed: bool
    reason_code: str = ""
    missing_capabilities: list[str] = field(default_factory=list)
    message: str = ""


class CapabilityValidationError(ValueError):
    """Raised when a broker cannot satisfy route or strategy requirements."""


_ACCOUNT_CAPABILITY_REQUIREMENTS = {
    RouteAccountType.ISA: ["supports_spot_etf"],
    RouteAccountType.SIPP: ["supports_spot_etf"],
    RouteAccountType.GIA: [],
    RouteAccountType.SPREADBET: ["supports_spreadbet"],
    RouteAccountType.PAPER: ["supports_paper"],
}

_STRATEGY_CAPABILITY_REQUIREMENTS = {
    "requires_short": "supports_short",
    "requires_options": "supports_options",
    "requires_futures": "supports_futures",
    "requires_spreadbet": "supports_spreadbet",
    "requires_cfd": "supports_cfd",
    "requires_live": "supports_live",
    "requires_paper": "supports_paper",
    "requires_spot_etf": "supports_spot_etf",
}


def _collect_missing_capabilities(
    broker: BaseBroker,
    account_type: RouteAccountType,
    requirements: StrategyRequirements,
) -> tuple[str, list[str]]:
    missing: list[str] = []

    # Validate account lane compatibility first.
    for capability in _ACCOUNT_CAPABILITY_REQUIREMENTS[account_type]:
        if not broker.supports_capability(capability):
            missing.append(capability)

    if missing:
        return "unsupported_account_lane", sorted(set(missing))

    # Validate strategy capability requirements.
    for requirement_field, capability in _STRATEGY_CAPABILITY_REQUIREMENTS.items():
        if getattr(requirements, requirement_field) and not broker.supports_capability(capability):
            missing.append(capability)

    if missing:
        return "unsupported_capability", sorted(set(missing))

    return "", []


def validate_route_capabilities(
    broker: BaseBroker,
    account_type: RouteAccountType,
    requirements: StrategyRequirements,
) -> CapabilityValidationResult:
    """
    Validate route + strategy requirements against broker capability matrix.

    This is the required pre-trade capability check path for order intents.
    """
    reason_code, missing = _collect_missing_capabilities(broker, account_type, requirements)
    if not missing:
        return CapabilityValidationResult(allowed=True, reason_code="ok")

    message = (
        f"Broker '{broker.__class__.__name__}' cannot satisfy route "
        f"({account_type.value}) requirements: {', '.join(missing)}"
    )
    return CapabilityValidationResult(
        allowed=False,
        reason_code=reason_code,
        missing_capabilities=missing,
        message=message,
    )


def validate_order_intent_capabilities(
    intent: CapabilityOrderIntent,
    broker: BaseBroker,
) -> CapabilityValidationResult:
    """Validate a concrete order intent before any broker order call is attempted."""
    return validate_route_capabilities(
        broker=broker,
        account_type=intent.account_type,
        requirements=intent.requirements,
    )


def enforce_order_intent_capabilities(intent: CapabilityOrderIntent, broker: BaseBroker) -> None:
    """Raise an explicit error if capability policy would reject the intent."""
    result = validate_order_intent_capabilities(intent, broker)
    if not result.allowed:
        raise CapabilityValidationError(result.message)

