"""Position sizing & risk limits engine.

I-002: Computes trade sizes from configurable risk parameters and exposure
limits with optional volatility adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from risk.limits_engine import (
    ExposureLimitConfig,
    ExposureLimitContext,
    apply_exposure_limits,
)


@dataclass
class SizingConfig:
    """Position sizing configuration."""

    max_position_pct: float = 5.0
    max_strategy_pct: float = 20.0
    max_portfolio_heat_pct: float = 50.0
    risk_per_trade_pct: float = 1.0
    use_volatility_adjustment: bool = True
    volatility_scalar: float = 1.0
    min_trade_notional: float = 10.0


@dataclass
class SizingContext:
    """Portfolio state for sizing decisions."""

    equity: float
    current_portfolio_heat_pct: float = 0.0
    strategy_exposure: dict[str, float] = field(default_factory=dict)
    ticker_volatility_pct: Optional[float] = None


@dataclass
class SizingResult:
    """Computed position size and reasoning."""

    ticker: str
    strategy: str
    recommended_notional: float
    max_allowed_notional: float
    sizing_method: str
    capped_by: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "strategy": self.strategy,
            "recommended_notional": round(self.recommended_notional, 2),
            "max_allowed_notional": round(self.max_allowed_notional, 2),
            "sizing_method": self.sizing_method,
            "capped_by": self.capped_by,
            "details": self.details,
        }


class PositionSizer:
    """Stateful convenience wrapper around the sizing function."""

    def __init__(self, config: Optional[SizingConfig] = None):
        self.config = config or SizingConfig()

    def size_position(
        self,
        ticker: str,
        strategy: str,
        price: float,
        context: Optional[SizingContext] = None,
        stop_distance_pct: Optional[float] = None,
    ) -> SizingResult:
        return compute_position_size(
            ticker=ticker,
            strategy=strategy,
            price=price,
            config=self.config,
            context=context,
            stop_distance_pct=stop_distance_pct,
        )


def compute_position_size(
    ticker: str,
    strategy: str,
    price: float,
    config: Optional[SizingConfig] = None,
    context: Optional[SizingContext] = None,
    stop_distance_pct: Optional[float] = None,
) -> SizingResult:
    """Compute recommended position size for one order candidate."""
    del price  # Notional sizing currently does not require a quote-derived quantity.

    config = config or SizingConfig()
    context = context or SizingContext(equity=100000.0)

    equity = float(context.equity or 0.0)
    if equity <= 0:
        return SizingResult(
            ticker=ticker,
            strategy=strategy,
            recommended_notional=0.0,
            max_allowed_notional=0.0,
            sizing_method="none",
            capped_by="zero_equity",
        )

    base_notional, method = _compute_base_notional(
        equity=equity,
        config=config,
        context=context,
        stop_distance_pct=stop_distance_pct,
    )

    limit_config = ExposureLimitConfig(
        max_position_pct=config.max_position_pct,
        max_strategy_pct=config.max_strategy_pct,
        max_portfolio_heat_pct=config.max_portfolio_heat_pct,
        min_trade_notional=config.min_trade_notional,
    )
    limit_context = ExposureLimitContext(
        equity=equity,
        current_portfolio_heat_pct=context.current_portfolio_heat_pct,
        strategy_exposure=dict(context.strategy_exposure),
    )
    limit_result = apply_exposure_limits(
        strategy=strategy,
        proposed_notional=base_notional,
        config=limit_config,
        context=limit_context,
    )

    if limit_result.blocked:
        return SizingResult(
            ticker=ticker,
            strategy=strategy,
            recommended_notional=0.0,
            max_allowed_notional=round(limit_result.max_allowed_notional, 2),
            sizing_method=method,
            capped_by=limit_result.capped_by,
            details=dict(limit_result.details),
        )

    return SizingResult(
        ticker=ticker,
        strategy=strategy,
        recommended_notional=round(limit_result.capped_notional, 2),
        max_allowed_notional=round(limit_result.max_allowed_notional, 2),
        sizing_method=method,
        capped_by=limit_result.capped_by,
    )


def _compute_base_notional(
    equity: float,
    config: SizingConfig,
    context: SizingContext,
    stop_distance_pct: Optional[float],
) -> tuple[float, str]:
    if stop_distance_pct and float(stop_distance_pct) > 0:
        risk_amount = equity * (float(config.risk_per_trade_pct) / 100.0)
        base_notional = risk_amount / (float(stop_distance_pct) / 100.0)
        method = "risk_based"
    else:
        base_notional = equity * (float(config.max_position_pct) / 100.0)
        method = "fixed"

    if (
        config.use_volatility_adjustment
        and context.ticker_volatility_pct is not None
        and float(context.ticker_volatility_pct) > 0
    ):
        vol_ratio = 20.0 / float(context.ticker_volatility_pct)
        base_notional = base_notional * vol_ratio * float(config.volatility_scalar)
        method = "volatility_adjusted"

    return max(0.0, float(base_notional)), method
