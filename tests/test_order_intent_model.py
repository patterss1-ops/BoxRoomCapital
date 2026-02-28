"""Unit tests for OrderIntent model validation and normalization."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.order_intent import OrderIntent, OrderSide, OrderType


def test_order_intent_normalizes_enum_and_risk_tags():
    intent = OrderIntent(
        strategy_id="ibs_credit_spreads",
        strategy_version="v3",
        sleeve="options_income",
        account_type="SPREADBET",
        broker_target="ig",
        instrument="SPY",
        side="sell",
        qty=2,
        order_type="market",
        risk_tags=["  vol:high", "vol:high", "  ", "sleeve:income"],
        metadata={"epic_short": "OP.D.SPXWEEKLY.5200P.IP"},
    )

    assert intent.side == OrderSide.SELL
    assert intent.order_type == OrderType.MARKET
    assert intent.risk_tags == ["sleeve:income", "vol:high"]
    assert intent.to_payload()["account_type"] == "SPREADBET"


def test_order_intent_rejects_invalid_qty():
    with pytest.raises(ValueError, match="qty must be > 0"):
        OrderIntent(
            strategy_id="ibs_credit_spreads",
            strategy_version="v3",
            sleeve="options_income",
            account_type="SPREADBET",
            broker_target="ig",
            instrument="SPY",
            side="BUY",
            qty=0,
            order_type="LIMIT",
        )


def test_order_intent_requires_non_empty_identity_fields():
    with pytest.raises(ValueError, match="strategy_id is required"):
        OrderIntent(
            strategy_id="",
            strategy_version="v3",
            sleeve="options_income",
            account_type="SPREADBET",
            broker_target="ig",
            instrument="SPY",
            side="BUY",
            qty=1,
            order_type="LIMIT",
        )
