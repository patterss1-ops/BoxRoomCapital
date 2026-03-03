"""Tests for I-002 exposure limit engine."""

from __future__ import annotations

from risk.limits_engine import (
    ExposureLimitConfig,
    ExposureLimitContext,
    apply_exposure_limits,
)


def test_position_limit_caps_notional():
    config = ExposureLimitConfig(max_position_pct=5.0)
    context = ExposureLimitContext(equity=100000.0)
    result = apply_exposure_limits(
        strategy="momentum",
        proposed_notional=7000.0,
        config=config,
        context=context,
    )
    assert result.blocked is False
    assert result.capped_notional == 5000.0
    assert result.capped_by == "position_limit"


def test_strategy_limit_blocks_when_exhausted():
    config = ExposureLimitConfig(max_strategy_pct=20.0)
    context = ExposureLimitContext(
        equity=100000.0,
        strategy_exposure={"momentum": 20000.0},
    )
    result = apply_exposure_limits(
        strategy="momentum",
        proposed_notional=1000.0,
        config=config,
        context=context,
    )
    assert result.blocked is True
    assert result.capped_notional == 0.0
    assert result.capped_by == "strategy_limit"


def test_heat_limit_blocks_when_no_capacity():
    config = ExposureLimitConfig(max_portfolio_heat_pct=50.0)
    context = ExposureLimitContext(
        equity=100000.0,
        current_portfolio_heat_pct=50.0,
    )
    result = apply_exposure_limits(
        strategy="momentum",
        proposed_notional=1000.0,
        config=config,
        context=context,
    )
    assert result.blocked is True
    assert result.capped_notional == 0.0
    assert result.capped_by == "heat_limit"


def test_min_trade_notional_blocks_too_small_result():
    config = ExposureLimitConfig(
        max_position_pct=0.01,
        min_trade_notional=100.0,
    )
    context = ExposureLimitContext(equity=100000.0)
    result = apply_exposure_limits(
        strategy="momentum",
        proposed_notional=10.0,
        config=config,
        context=context,
    )
    assert result.blocked is True
    assert result.capped_by == "min_size"


def test_sequential_caps_precedence_strategy_after_position():
    config = ExposureLimitConfig(max_position_pct=10.0, max_strategy_pct=20.0)
    context = ExposureLimitContext(
        equity=100000.0,
        strategy_exposure={"momentum": 18000.0},
    )
    result = apply_exposure_limits(
        strategy="momentum",
        proposed_notional=20000.0,
        config=config,
        context=context,
    )
    assert result.blocked is False
    assert result.capped_notional == 2000.0
    assert result.capped_by == "strategy_limit"


def test_zero_equity_blocks():
    config = ExposureLimitConfig()
    context = ExposureLimitContext(equity=0.0)
    result = apply_exposure_limits(
        strategy="momentum",
        proposed_notional=1000.0,
        config=config,
        context=context,
    )
    assert result.blocked is True
    assert result.capped_by == "zero_equity"
