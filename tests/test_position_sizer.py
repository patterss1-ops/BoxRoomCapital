"""Tests for I-002 position sizing & risk limits engine."""

from __future__ import annotations

import pytest

from risk.position_sizer import (
    PositionSizer,
    SizingConfig,
    SizingContext,
    SizingResult,
    compute_position_size,
)


class TestSizingResult:
    def test_to_dict(self):
        r = SizingResult(
            ticker="AAPL",
            strategy="momentum",
            recommended_notional=5000.0,
            max_allowed_notional=5000.0,
            sizing_method="fixed",
            capped_by="none",
        )
        d = r.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["recommended_notional"] == 5000.0


class TestPositionSizing:
    def test_position_sizer_wrapper_uses_config(self):
        sizer = PositionSizer(config=SizingConfig(max_position_pct=2.0, use_volatility_adjustment=False))
        result = sizer.size_position(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            context=SizingContext(equity=100000.0),
        )
        assert result.recommended_notional == 2000.0

    def test_fixed_sizing_default(self):
        """Default config uses 5% of equity."""
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            context=SizingContext(equity=100000.0),
        )
        assert result.recommended_notional == 5000.0
        assert result.sizing_method == "fixed"
        assert result.capped_by == "none"

    def test_risk_based_sizing_with_stop(self):
        """Risk-based sizing: 1% risk / 5% stop = 20% position."""
        config = SizingConfig(risk_per_trade_pct=1.0, max_position_pct=25.0, use_volatility_adjustment=False)
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=config,
            context=SizingContext(equity=100000.0),
            stop_distance_pct=5.0,
        )
        # 1% of 100k = 1000 risk. 1000 / 0.05 = 20000 notional
        assert result.recommended_notional == 20000.0
        assert result.sizing_method == "risk_based"

    def test_volatility_adjusted_sizing(self):
        """Higher volatility reduces position size."""
        config = SizingConfig(max_position_pct=10.0, use_volatility_adjustment=True)
        # 40% vol → half the size of 20% vol baseline
        result = compute_position_size(
            ticker="MEME",
            strategy="momentum",
            price=50.0,
            config=config,
            context=SizingContext(equity=100000.0, ticker_volatility_pct=40.0),
        )
        assert result.sizing_method == "volatility_adjusted"
        assert result.recommended_notional == 5000.0  # 10000 * (20/40) = 5000

    def test_low_volatility_increases_size_but_capped(self):
        """Low vol increases size but position cap applies."""
        config = SizingConfig(max_position_pct=5.0, use_volatility_adjustment=True)
        # 10% vol → 2x base, but capped at 5% of equity = 5000
        result = compute_position_size(
            ticker="BOND",
            strategy="momentum",
            price=100.0,
            config=config,
            context=SizingContext(equity=100000.0, ticker_volatility_pct=10.0),
        )
        assert result.recommended_notional == 5000.0
        assert result.capped_by == "position_limit"

    def test_strategy_exposure_cap(self):
        """Strategy already near limit → reduced or blocked."""
        config = SizingConfig(max_position_pct=10.0, max_strategy_pct=20.0, use_volatility_adjustment=False)
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=config,
            context=SizingContext(
                equity=100000.0,
                strategy_exposure={"momentum": 18000.0},
            ),
        )
        assert result.recommended_notional == 2000.0  # 20000 - 18000
        assert result.capped_by == "strategy_limit"

    def test_strategy_limit_blocks(self):
        """Strategy at max → zero size."""
        config = SizingConfig(max_strategy_pct=20.0, use_volatility_adjustment=False)
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=config,
            context=SizingContext(
                equity=100000.0,
                strategy_exposure={"momentum": 20000.0},
            ),
        )
        assert result.recommended_notional == 0.0
        assert result.capped_by == "strategy_limit"

    def test_portfolio_heat_cap(self):
        """High portfolio heat → reduced position."""
        config = SizingConfig(
            max_position_pct=10.0,
            max_portfolio_heat_pct=50.0,
            use_volatility_adjustment=False,
        )
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=config,
            context=SizingContext(equity=100000.0, current_portfolio_heat_pct=48.0),
        )
        assert result.recommended_notional == 2000.0  # 50%-48% = 2% of 100k
        assert result.capped_by == "heat_limit"

    def test_heat_limit_blocks(self):
        """Portfolio at max heat → zero size."""
        config = SizingConfig(max_portfolio_heat_pct=50.0, use_volatility_adjustment=False)
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=config,
            context=SizingContext(equity=100000.0, current_portfolio_heat_pct=50.0),
        )
        assert result.recommended_notional == 0.0
        assert result.capped_by == "heat_limit"

    def test_below_min_trade_size(self):
        """Position too small after caps → zero."""
        config = SizingConfig(
            max_position_pct=0.01,  # Tiny
            min_trade_notional=100.0,
            use_volatility_adjustment=False,
        )
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=config,
            context=SizingContext(equity=100000.0),
        )
        assert result.recommended_notional == 0.0
        assert result.capped_by == "min_size"

    def test_zero_equity_returns_zero(self):
        """Zero equity → zero size."""
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            context=SizingContext(equity=0.0),
        )
        assert result.recommended_notional == 0.0
        assert result.capped_by == "zero_equity"

    def test_default_context_uses_100k(self):
        """No context → default 100k equity."""
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
        )
        assert result.recommended_notional == 5000.0  # 5% of 100k

    def test_no_vol_skips_adjustment(self):
        """No volatility data → falls back to fixed sizing."""
        config = SizingConfig(use_volatility_adjustment=True, max_position_pct=5.0)
        result = compute_position_size(
            ticker="AAPL",
            strategy="momentum",
            price=150.0,
            config=config,
            context=SizingContext(equity=100000.0, ticker_volatility_pct=None),
        )
        assert result.sizing_method == "fixed"
        assert result.recommended_notional == 5000.0
