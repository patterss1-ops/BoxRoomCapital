"""Tests for A-004 deterministic account router and policy rejects."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from broker.base import (
    AccountInfo,
    BaseBroker,
    BrokerCapabilities,
    OrderResult,
    Position,
)
from broker.paper import PaperBroker
from execution.policy.capability_policy import RouteAccountType, StrategyRequirements
from execution.policy.route_policy import RoutePolicyState
from execution.router import AccountRouter, RouteConfigEntry, RouteIntent, RouteOverride


class SpreadbetOptionsBroker(BaseBroker):
    """Minimal live spreadbet/options broker stub for routing tests."""

    capabilities = BrokerCapabilities(
        supports_spreadbet=True,
        supports_options=True,
        supports_short=True,
        supports_live=True,
    )

    def connect(self) -> bool:
        return True

    def disconnect(self):
        return None

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(balance=1000.0, equity=1000.0, unrealised_pnl=0.0, open_positions=0)

    def get_positions(self) -> list[Position]:
        return []

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        return None

    def place_long(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        return OrderResult(success=True, order_id="long")

    def place_short(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        return OrderResult(success=True, order_id="short")

    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        return OrderResult(success=True, order_id="close", timestamp=datetime.utcnow())


def _intent(actor: str = "system") -> RouteIntent:
    return RouteIntent(
        strategy_id="ibs_credit_spreads",
        sleeve="options_income",
        ticker="SPY",
        actor=actor,
        requirements=StrategyRequirements(
            requires_options=True,
            requires_spreadbet=True,
            requires_short=True,
            requires_live=True,
        ),
    )


def test_router_is_deterministic_for_same_input_and_config():
    router = AccountRouter(
        route_map={
            "strategy:ibs_credit_spreads": RouteConfigEntry(
                broker_name="ig",
                account_type=RouteAccountType.SPREADBET,
            ),
            "default": RouteConfigEntry(
                broker_name="paper",
                account_type=RouteAccountType.PAPER,
            ),
        },
        brokers={
            "ig": SpreadbetOptionsBroker(),
            "paper": PaperBroker(),
        },
    )
    intent = _intent()

    first = router.resolve(intent)
    second = router.resolve(intent)

    assert first.allowed and second.allowed
    assert first == second
    assert first.resolution is not None
    assert first.resolution.broker_name == "ig"
    assert first.resolution.account_type == RouteAccountType.SPREADBET
    assert first.resolution.matched_key == "strategy:ibs_credit_spreads"


def test_router_returns_explicit_reason_codes_for_invalid_routes():
    intent = _intent()

    no_route_router = AccountRouter(route_map={}, brokers={})
    no_route = no_route_router.resolve(intent)
    assert not no_route.allowed
    assert no_route.reason_code == "route_not_configured"

    missing_broker_router = AccountRouter(
        route_map={"default": RouteConfigEntry(broker_name="ibkr", account_type=RouteAccountType.PAPER)},
        brokers={},
    )
    missing_broker = missing_broker_router.resolve(intent)
    assert not missing_broker.allowed
    assert missing_broker.reason_code == "broker_not_registered"

    capability_router = AccountRouter(
        route_map={"default": RouteConfigEntry(broker_name="paper", account_type=RouteAccountType.SPREADBET)},
        brokers={"paper": PaperBroker()},
    )
    capability = capability_router.resolve(intent)
    assert not capability.allowed
    assert capability.reason_code == "unsupported_account_lane"
    assert "supports_spreadbet" in capability.missing_capabilities


def test_manual_and_automated_actions_share_same_policy_gate():
    router = AccountRouter(
        route_map={
            "strategy:ibs_credit_spreads": RouteConfigEntry(
                broker_name="ig",
                account_type=RouteAccountType.SPREADBET,
            ),
        },
        brokers={"ig": SpreadbetOptionsBroker()},
    )
    automated_intent = _intent(actor="system")
    manual_intent = _intent(actor="operator")

    kill_state = RoutePolicyState(kill_switch_active=True, kill_switch_reason="manual stop")
    automated_kill = router.resolve(automated_intent, policy_state=kill_state)
    manual_kill = router.resolve(manual_intent, policy_state=kill_state)
    assert automated_kill.reason_code == "kill_switch_active"
    assert manual_kill.reason_code == "kill_switch_active"

    cooldown_state = RoutePolicyState(cooldown_tickers={"spy"})
    automated_cd = router.resolve(automated_intent, policy_state=cooldown_state)
    manual_cd = router.resolve(manual_intent, policy_state=cooldown_state)
    assert automated_cd.reason_code == "market_cooldown_active"
    assert manual_cd.reason_code == "market_cooldown_active"


def test_override_uses_same_capability_validation_path():
    router = AccountRouter(
        route_map={
            "strategy:ibs_credit_spreads": RouteConfigEntry(
                broker_name="ig",
                account_type=RouteAccountType.SPREADBET,
            ),
        },
        brokers={
            "ig": SpreadbetOptionsBroker(),
            "paper": PaperBroker(),
        },
    )
    decision = router.resolve(
        _intent(),
        override=RouteOverride(
            broker_name="paper",
            account_type=RouteAccountType.SPREADBET,
        ),
    )
    assert not decision.allowed
    assert decision.reason_code == "unsupported_account_lane"
    assert decision.resolution is not None
    assert decision.resolution.used_override is True
