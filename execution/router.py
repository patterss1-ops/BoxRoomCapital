"""Deterministic account routing and route-policy enforcement."""

from __future__ import annotations

from dataclasses import dataclass

from broker.base import BaseBroker
from execution.policy.capability_policy import (
    CapabilityOrderIntent,
    RouteAccountType,
    StrategyRequirements,
    validate_order_intent_capabilities,
)
from execution.policy.route_policy import (
    RouteDecision,
    RoutePolicyState,
    RouteRejectCode,
    RouteResolution,
)


@dataclass(frozen=True)
class RouteConfigEntry:
    """Configured default route for strategy/sleeve keys."""

    broker_name: str
    account_type: RouteAccountType


@dataclass(frozen=True)
class RouteIntent:
    """Input payload for routing decisions."""

    strategy_id: str
    sleeve: str
    ticker: str
    requirements: StrategyRequirements
    actor: str = "system"


@dataclass(frozen=True)
class RouteOverride:
    """Optional operator override of broker/account route."""

    broker_name: str | None = None
    account_type: RouteAccountType | None = None


class AccountRouter:
    """Route execution intents to account lanes with explicit reject reasons."""

    def __init__(
        self,
        route_map: dict[str, RouteConfigEntry],
        brokers: dict[str, BaseBroker],
    ):
        self.route_map = dict(route_map)
        self.brokers = dict(brokers)

    def resolve(
        self,
        intent: RouteIntent,
        policy_state: RoutePolicyState | None = None,
        override: RouteOverride | None = None,
    ) -> RouteDecision:
        """
        Resolve one route decision.

        Resolution order is deterministic:
        1) `strategy:<strategy_id>`
        2) `sleeve:<sleeve>`
        3) `default`
        """
        state = policy_state or RoutePolicyState()
        if state.kill_switch_active:
            reason = state.kill_switch_reason.strip() or "Kill switch active"
            return RouteDecision(
                allowed=False,
                reason_code=RouteRejectCode.KILL_SWITCH_ACTIVE.value,
                message=reason,
            )

        if state.has_cooldown(intent.ticker):
            return RouteDecision(
                allowed=False,
                reason_code=RouteRejectCode.MARKET_COOLDOWN_ACTIVE.value,
                message=f"Market cooldown active for {intent.ticker.upper()}",
            )

        matched_key, route_entry = self._resolve_route_entry(intent)
        if route_entry is None:
            return RouteDecision(
                allowed=False,
                reason_code=RouteRejectCode.ROUTE_NOT_CONFIGURED.value,
                message=(
                    f"No route configured for strategy '{intent.strategy_id}' "
                    f"or sleeve '{intent.sleeve}'"
                ),
            )

        resolved_broker = route_entry.broker_name
        resolved_account = route_entry.account_type
        used_override = False

        if override:
            if override.broker_name:
                resolved_broker = override.broker_name
                used_override = True
            if override.account_type:
                resolved_account = override.account_type
                used_override = True

        broker = self.brokers.get(resolved_broker)
        if broker is None:
            return RouteDecision(
                allowed=False,
                reason_code=RouteRejectCode.BROKER_NOT_REGISTERED.value,
                message=f"Broker '{resolved_broker}' is not registered",
            )

        capability_intent = CapabilityOrderIntent(
            strategy_id=intent.strategy_id,
            broker_name=resolved_broker,
            account_type=resolved_account,
            requirements=intent.requirements,
        )
        capability_result = validate_order_intent_capabilities(capability_intent, broker)
        if not capability_result.allowed:
            return RouteDecision(
                allowed=False,
                reason_code=capability_result.reason_code or RouteRejectCode.CAPABILITY_REJECTED.value,
                message=capability_result.message,
                resolution=RouteResolution(
                    account_type=resolved_account,
                    broker_name=resolved_broker,
                    matched_key=matched_key,
                    used_override=used_override,
                ),
                missing_capabilities=list(capability_result.missing_capabilities),
            )

        return RouteDecision(
            allowed=True,
            reason_code=RouteRejectCode.OK.value,
            message="Route accepted",
            resolution=RouteResolution(
                account_type=resolved_account,
                broker_name=resolved_broker,
                matched_key=matched_key,
                used_override=used_override,
            ),
        )

    def _resolve_route_entry(self, intent: RouteIntent) -> tuple[str, RouteConfigEntry | None]:
        key_candidates = (
            f"strategy:{intent.strategy_id}",
            f"sleeve:{intent.sleeve}",
            "default",
        )
        for key in key_candidates:
            route_entry = self.route_map.get(key)
            if route_entry:
                return key, route_entry
        return "none", None
