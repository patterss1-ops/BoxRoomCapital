"""Tests for A-001 broker capability schema and pre-trade validation path."""

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
from broker.ig import IGBroker
from broker.paper import PaperBroker
from execution.policy.capability_policy import (
    CapabilityOrderIntent,
    RouteAccountType,
    StrategyRequirements,
    validate_order_intent_capabilities,
)


class LongOnlyTestBroker(BaseBroker):
    """Minimal broker stub to verify rejection before order call."""

    capabilities = BrokerCapabilities(
        supports_spot_etf=True,
        supports_live=True,
        supports_short=False,
    )

    def __init__(self):
        self.place_short_calls = 0

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
        return OrderResult(success=True, order_id="test-long")

    def place_short(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        self.place_short_calls += 1
        return OrderResult(success=True, order_id="test-short")

    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        return OrderResult(success=True, order_id="test-close", timestamp=datetime.utcnow())


def test_existing_ig_options_flow_capabilities_pass():
    """Regression guard: existing IG options flow should pass capability checks."""
    broker = IGBroker(is_demo=True)
    intent = CapabilityOrderIntent(
        strategy_id="ibs_credit_spreads",
        broker_name="ig",
        account_type=RouteAccountType.SPREADBET,
        requirements=StrategyRequirements(
            requires_short=True,
            requires_options=True,
            requires_spreadbet=True,
            requires_live=True,
        ),
    )
    result = validate_order_intent_capabilities(intent, broker)
    assert result.allowed, result.message


def test_short_requirement_rejected_for_broker_without_short_support():
    """If strategy needs shorting and broker can't short, reject pre-trade."""
    broker = LongOnlyTestBroker()
    intent = CapabilityOrderIntent(
        strategy_id="needs_short",
        broker_name="long_only_test",
        account_type=RouteAccountType.GIA,
        requirements=StrategyRequirements(requires_short=True, requires_live=True),
    )
    result = validate_order_intent_capabilities(intent, broker)
    assert not result.allowed
    assert result.reason_code == "unsupported_capability"
    assert "supports_short" in result.missing_capabilities
    assert broker.place_short_calls == 0


def test_unsupported_account_lane_rejected_before_order_call():
    """
    Unsupported account/broker mapping should reject before order placement.

    Here, spreadbet route is requested on paper broker.
    """
    broker = PaperBroker()
    intent = CapabilityOrderIntent(
        strategy_id="spreadbet_only_strategy",
        broker_name="paper",
        account_type=RouteAccountType.SPREADBET,
        requirements=StrategyRequirements(requires_spreadbet=True),
    )
    result = validate_order_intent_capabilities(intent, broker)
    assert not result.allowed
    assert result.reason_code == "unsupported_account_lane"
    assert "supports_spreadbet" in result.missing_capabilities


def test_paper_account_route_requires_paper_capability():
    """Paper account lane should be allowed only on brokers supporting paper mode."""
    broker = PaperBroker()
    intent = CapabilityOrderIntent(
        strategy_id="paper_test",
        broker_name="paper",
        account_type=RouteAccountType.PAPER,
        requirements=StrategyRequirements(requires_paper=True, requires_short=True),
    )
    result = validate_order_intent_capabilities(intent, broker)
    assert result.allowed, result.message

