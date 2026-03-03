"""Exposure limit calculations for position sizing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExposureLimitConfig:
    """Percent-of-equity thresholds used by the sizing limit engine."""

    max_position_pct: float = 5.0
    max_strategy_pct: float = 20.0
    max_portfolio_heat_pct: float = 50.0
    min_trade_notional: float = 10.0


@dataclass(frozen=True)
class ExposureLimitContext:
    """Current portfolio exposures required for cap evaluation."""

    equity: float
    current_portfolio_heat_pct: float = 0.0
    strategy_exposure: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ExposureLimitResult:
    """Result of applying exposure limits to a proposed notional."""

    capped_notional: float
    max_allowed_notional: float
    capped_by: str
    blocked: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def apply_exposure_limits(
    strategy: str,
    proposed_notional: float,
    config: ExposureLimitConfig,
    context: ExposureLimitContext,
) -> ExposureLimitResult:
    """
    Sequentially cap notional by position, strategy, and portfolio-heat limits.

    Cap precedence intentionally mirrors the existing sizing behavior:
      1) position limit
      2) strategy limit
      3) heat limit
      4) minimum notional
    """
    equity = float(context.equity or 0.0)
    if equity <= 0:
        return ExposureLimitResult(
            capped_notional=0.0,
            max_allowed_notional=0.0,
            capped_by="zero_equity",
            blocked=True,
            details={"equity": equity},
        )

    notional = max(0.0, float(proposed_notional or 0.0))
    capped_by = "none"

    max_position_notional = equity * (float(config.max_position_pct) / 100.0)
    if notional > max_position_notional:
        notional = max_position_notional
        capped_by = "position_limit"

    current_strategy = float(context.strategy_exposure.get(strategy, 0.0) or 0.0)
    max_strategy_notional = equity * (float(config.max_strategy_pct) / 100.0)
    remaining_strategy = max(0.0, max_strategy_notional - current_strategy)
    if remaining_strategy <= 0:
        return ExposureLimitResult(
            capped_notional=0.0,
            max_allowed_notional=0.0,
            capped_by="strategy_limit",
            blocked=True,
            details={
                "current_exposure": current_strategy,
                "max": max_strategy_notional,
            },
        )
    if notional > remaining_strategy:
        notional = remaining_strategy
        capped_by = "strategy_limit"

    current_heat_pct = float(context.current_portfolio_heat_pct or 0.0)
    remaining_heat_pct = float(config.max_portfolio_heat_pct) - current_heat_pct
    remaining_heat_notional = equity * (max(0.0, remaining_heat_pct) / 100.0)
    if remaining_heat_pct <= 0:
        return ExposureLimitResult(
            capped_notional=0.0,
            max_allowed_notional=0.0,
            capped_by="heat_limit",
            blocked=True,
            details={
                "current_heat_pct": current_heat_pct,
                "max": float(config.max_portfolio_heat_pct),
            },
        )
    if notional > remaining_heat_notional:
        notional = remaining_heat_notional
        capped_by = "heat_limit"

    max_allowed = min(max_position_notional, remaining_strategy, remaining_heat_notional)
    max_allowed = max(0.0, max_allowed)

    if notional < float(config.min_trade_notional):
        return ExposureLimitResult(
            capped_notional=0.0,
            max_allowed_notional=notional,
            capped_by="min_size",
            blocked=True,
            details={
                "computed": notional,
                "min_required": float(config.min_trade_notional),
            },
        )

    return ExposureLimitResult(
        capped_notional=notional,
        max_allowed_notional=max_allowed,
        capped_by=capped_by,
        blocked=False,
    )
