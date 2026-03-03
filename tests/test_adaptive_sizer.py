"""Tests for M-003 adaptive volatility-adjusted position sizing."""

from __future__ import annotations

import math

import pytest

from risk.adaptive_sizer import (
    AdaptivePositionSizer,
    SizingConfig,
    SizingResult,
    VolatilityMethod,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _steady_prices(start: float = 100.0, n: int = 30, step: float = 1.0) -> list[float]:
    """Generate a monotonically increasing price series."""
    return [start + i * step for i in range(n)]


def _volatile_prices(start: float = 100.0, n: int = 30, amplitude: float = 5.0) -> list[float]:
    """Generate an oscillating price series with known amplitude."""
    return [start + amplitude * ((-1) ** i) for i in range(n)]


# ---------------------------------------------------------------------------
# 1. ATR volatility computation
# ---------------------------------------------------------------------------

class TestATRVolatility:
    def test_atr_constant_step(self):
        """Constant $1 increments -> ATR = 1.0."""
        prices = _steady_prices(100.0, 30, step=1.0)
        sizer = AdaptivePositionSizer(SizingConfig(method=VolatilityMethod.ATR, lookback_period=20))
        vol = sizer.compute_volatility(prices)
        assert vol == pytest.approx(1.0, abs=1e-9)

    def test_atr_larger_step(self):
        """Constant $3 increments -> ATR = 3.0."""
        prices = _steady_prices(100.0, 30, step=3.0)
        sizer = AdaptivePositionSizer(SizingConfig(method=VolatilityMethod.ATR, lookback_period=20))
        vol = sizer.compute_volatility(prices)
        assert vol == pytest.approx(3.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. Rolling std volatility computation
# ---------------------------------------------------------------------------

class TestRollingStdVolatility:
    def test_rolling_std_constant_return(self):
        """Constant returns have zero std."""
        # Geometric series with constant 1% return
        prices = [100.0 * (1.01 ** i) for i in range(30)]
        sizer = AdaptivePositionSizer(SizingConfig(method=VolatilityMethod.ROLLING_STD, lookback_period=20))
        vol = sizer.compute_volatility(prices)
        assert vol == pytest.approx(0.0, abs=1e-9)

    def test_rolling_std_positive(self):
        """Oscillating prices yield a positive std."""
        prices = _volatile_prices(100.0, 30, amplitude=5.0)
        sizer = AdaptivePositionSizer(SizingConfig(method=VolatilityMethod.ROLLING_STD, lookback_period=20))
        vol = sizer.compute_volatility(prices)
        assert vol > 0


# ---------------------------------------------------------------------------
# 3. EWMA volatility computation
# ---------------------------------------------------------------------------

class TestEWMAVolatility:
    def test_ewma_positive_for_volatile_series(self):
        prices = _volatile_prices(100.0, 30, amplitude=5.0)
        sizer = AdaptivePositionSizer(SizingConfig(method=VolatilityMethod.EWMA, ewma_span=10))
        vol = sizer.compute_volatility(prices)
        assert vol > 0

    def test_ewma_near_zero_for_constant_return(self):
        prices = [100.0 * (1.01 ** i) for i in range(30)]
        sizer = AdaptivePositionSizer(SizingConfig(method=VolatilityMethod.EWMA, ewma_span=20))
        vol = sizer.compute_volatility(prices)
        assert vol == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. Basic position size calculation
# ---------------------------------------------------------------------------

class TestBasicSizing:
    def test_basic_calculation(self):
        """Known vol -> deterministic size."""
        prices = _steady_prices(100.0, 30, step=1.0)  # ATR = 1.0
        sizer = AdaptivePositionSizer(SizingConfig(
            method=VolatilityMethod.ATR,
            risk_per_trade_pct=1.0,
            max_position_pct=100.0,  # effectively uncapped
            min_position_size=1.0,
        ))
        result = sizer.calculate_size("AAPL", prices, portfolio_value=100_000.0, current_price=100.0)
        # risk_amount = 100000 * 1% = 1000;  raw = 1000 / 1.0 = 1000 shares
        # max_shares = (100000 * 100%) / 100 = 1000 -> not capped
        assert result.raw_size == pytest.approx(1000.0, rel=1e-6)
        assert result.adjusted_size == 1000.0
        assert result.volatility == pytest.approx(1.0, abs=1e-9)
        assert result.risk_amount == pytest.approx(1000.0)
        assert result.method == VolatilityMethod.ATR


# ---------------------------------------------------------------------------
# 5. Max position cap applied
# ---------------------------------------------------------------------------

class TestMaxPositionCap:
    def test_cap_limits_shares(self):
        prices = _steady_prices(100.0, 30, step=1.0)  # ATR = 1.0
        sizer = AdaptivePositionSizer(SizingConfig(
            method=VolatilityMethod.ATR,
            risk_per_trade_pct=1.0,
            max_position_pct=1.0,  # tight cap
        ))
        result = sizer.calculate_size("AAPL", prices, portfolio_value=100_000.0, current_price=100.0)
        # raw = 1000 shares; max = 100000*1%/100 = 10 shares
        assert result.adjusted_size == 10.0
        assert result.capped is True


# ---------------------------------------------------------------------------
# 6. Min position floor applied
# ---------------------------------------------------------------------------

class TestMinPositionFloor:
    def test_floor_raises_size(self):
        """When computed size is tiny, the floor kicks in."""
        prices = _volatile_prices(100.0, 30, amplitude=50.0)
        sizer = AdaptivePositionSizer(SizingConfig(
            method=VolatilityMethod.ATR,
            risk_per_trade_pct=0.01,
            max_position_pct=100.0,
            min_position_size=5.0,
        ))
        result = sizer.calculate_size("AAPL", prices, portfolio_value=10_000.0, current_price=100.0)
        assert result.adjusted_size >= 5.0
        assert result.floored is True


# ---------------------------------------------------------------------------
# 7. High volatility reduces position size
# ---------------------------------------------------------------------------

class TestHighVolReduces:
    def test_high_vol_smaller_than_low_vol(self):
        low_vol_prices = _steady_prices(100.0, 30, step=1.0)   # ATR ~ 1
        high_vol_prices = _steady_prices(100.0, 30, step=5.0)  # ATR ~ 5
        sizer = AdaptivePositionSizer(SizingConfig(
            method=VolatilityMethod.ATR,
            max_position_pct=100.0,
        ))
        low = sizer.calculate_size("A", low_vol_prices, 100_000.0, current_price=100.0)
        high = sizer.calculate_size("B", high_vol_prices, 100_000.0, current_price=100.0)
        assert high.raw_size < low.raw_size


# ---------------------------------------------------------------------------
# 8. Low volatility increases position size
# ---------------------------------------------------------------------------

class TestLowVolIncreases:
    def test_low_vol_larger(self):
        tiny_step = _steady_prices(100.0, 30, step=0.1)
        normal_step = _steady_prices(100.0, 30, step=2.0)
        sizer = AdaptivePositionSizer(SizingConfig(
            method=VolatilityMethod.ATR,
            max_position_pct=100.0,
        ))
        tiny = sizer.calculate_size("A", tiny_step, 100_000.0, current_price=100.0)
        normal = sizer.calculate_size("B", normal_step, 100_000.0, current_price=100.0)
        assert tiny.raw_size > normal.raw_size


# ---------------------------------------------------------------------------
# 9. Batch calculation
# ---------------------------------------------------------------------------

class TestBatch:
    def test_batch_returns_all(self):
        prices_map = {
            "AAPL": _steady_prices(150.0, 25, step=1.0),
            "GOOG": _steady_prices(2800.0, 25, step=10.0),
            "TSLA": _volatile_prices(700.0, 25, amplitude=20.0),
        }
        sizer = AdaptivePositionSizer(SizingConfig(max_position_pct=100.0))
        results = sizer.calculate_batch(["AAPL", "GOOG", "TSLA"], prices_map, 500_000.0)
        assert len(results) == 3
        assert [r.ticker for r in results] == ["AAPL", "GOOG", "TSLA"]

    def test_batch_missing_ticker_gives_zero(self):
        sizer = AdaptivePositionSizer()
        results = sizer.calculate_batch(["MISSING"], {}, 100_000.0)
        assert len(results) == 1
        assert results[0].adjusted_size == 0.0


# ---------------------------------------------------------------------------
# 10. Risk amount calculation
# ---------------------------------------------------------------------------

class TestRiskAmount:
    def test_risk_amount_matches_config(self):
        sizer = AdaptivePositionSizer(SizingConfig(risk_per_trade_pct=2.0))
        prices = _steady_prices(100.0, 30, step=1.0)
        result = sizer.calculate_size("X", prices, portfolio_value=200_000.0, current_price=100.0)
        assert result.risk_amount == pytest.approx(4000.0)  # 200k * 2%


# ---------------------------------------------------------------------------
# 11. Scale factor effect
# ---------------------------------------------------------------------------

class TestScaleFactor:
    def test_higher_scale_factor_reduces_size(self):
        prices = _steady_prices(100.0, 30, step=2.0)
        base = AdaptivePositionSizer(SizingConfig(
            volatility_scale_factor=1.0,
            max_position_pct=100.0,
        ))
        scaled = AdaptivePositionSizer(SizingConfig(
            volatility_scale_factor=2.0,
            max_position_pct=100.0,
        ))
        r_base = base.calculate_size("X", prices, 100_000.0, current_price=100.0)
        r_scaled = scaled.calculate_size("X", prices, 100_000.0, current_price=100.0)
        assert r_scaled.raw_size == pytest.approx(r_base.raw_size / 2.0, rel=1e-6)


# ---------------------------------------------------------------------------
# 12. Insufficient price data
# ---------------------------------------------------------------------------

class TestInsufficientData:
    def test_short_series_uses_available(self):
        """Fewer prices than lookback still works (uses what's available)."""
        prices = [100.0, 102.0, 101.0]  # only 3 points, lookback=20
        sizer = AdaptivePositionSizer(SizingConfig(lookback_period=20))
        vol = sizer.compute_volatility(prices)
        assert vol > 0  # computes from what's available

    def test_short_series_sizing(self):
        prices = [100.0, 102.0, 101.0]
        sizer = AdaptivePositionSizer(SizingConfig(max_position_pct=100.0))
        result = sizer.calculate_size("X", prices, 100_000.0)
        assert result.adjusted_size >= 0


# ---------------------------------------------------------------------------
# 13. Single price point
# ---------------------------------------------------------------------------

class TestSinglePrice:
    def test_single_price_zero_volatility(self):
        sizer = AdaptivePositionSizer()
        vol = sizer.compute_volatility([100.0])
        assert vol == 0.0

    def test_single_price_zero_size(self):
        sizer = AdaptivePositionSizer()
        result = sizer.calculate_size("X", [100.0], 100_000.0)
        assert result.adjusted_size == 0.0


# ---------------------------------------------------------------------------
# 14. Zero / negative portfolio value
# ---------------------------------------------------------------------------

class TestZeroNegativePortfolio:
    def test_zero_portfolio(self):
        prices = _steady_prices(100.0, 30, step=1.0)
        sizer = AdaptivePositionSizer()
        result = sizer.calculate_size("X", prices, portfolio_value=0.0, current_price=100.0)
        assert result.adjusted_size == 0.0
        assert result.raw_size == 0.0

    def test_negative_portfolio(self):
        prices = _steady_prices(100.0, 30, step=1.0)
        sizer = AdaptivePositionSizer()
        result = sizer.calculate_size("X", prices, portfolio_value=-50_000.0, current_price=100.0)
        assert result.adjusted_size == 0.0


# ---------------------------------------------------------------------------
# 15. Custom config parameters
# ---------------------------------------------------------------------------

class TestCustomConfig:
    def test_custom_values_propagate(self):
        cfg = SizingConfig(
            method=VolatilityMethod.ROLLING_STD,
            lookback_period=10,
            risk_per_trade_pct=0.5,
            max_position_pct=5.0,
            min_position_size=2.0,
            volatility_scale_factor=1.5,
            ewma_span=15,
        )
        sizer = AdaptivePositionSizer(cfg)
        returned = sizer.get_config()
        assert returned.method == VolatilityMethod.ROLLING_STD
        assert returned.lookback_period == 10
        assert returned.risk_per_trade_pct == 0.5
        assert returned.max_position_pct == 5.0
        assert returned.min_position_size == 2.0
        assert returned.volatility_scale_factor == 1.5
        assert returned.ewma_span == 15


# ---------------------------------------------------------------------------
# 16. Default config values
# ---------------------------------------------------------------------------

class TestDefaultConfig:
    def test_defaults(self):
        cfg = SizingConfig()
        assert cfg.method == VolatilityMethod.ATR
        assert cfg.lookback_period == 20
        assert cfg.risk_per_trade_pct == 1.0
        assert cfg.max_position_pct == 10.0
        assert cfg.min_position_size == 1.0
        assert cfg.volatility_scale_factor == 1.0
        assert cfg.ewma_span == 20


# ---------------------------------------------------------------------------
# 17. Capped flag set correctly
# ---------------------------------------------------------------------------

class TestCappedFlag:
    def test_not_capped_when_within_limit(self):
        prices = _steady_prices(100.0, 30, step=1.0)
        sizer = AdaptivePositionSizer(SizingConfig(max_position_pct=100.0))
        result = sizer.calculate_size("X", prices, 100_000.0, current_price=100.0)
        assert result.capped is False

    def test_capped_when_over_limit(self):
        prices = _steady_prices(100.0, 30, step=0.1)  # ATR ~ 0.1, raw huge
        sizer = AdaptivePositionSizer(SizingConfig(max_position_pct=1.0))
        result = sizer.calculate_size("X", prices, 100_000.0, current_price=100.0)
        assert result.capped is True


# ---------------------------------------------------------------------------
# 18. Floored flag set correctly
# ---------------------------------------------------------------------------

class TestFlooredFlag:
    def test_not_floored_when_large_enough(self):
        prices = _steady_prices(100.0, 30, step=1.0)
        sizer = AdaptivePositionSizer(SizingConfig(
            min_position_size=1.0,
            max_position_pct=100.0,
        ))
        result = sizer.calculate_size("X", prices, 100_000.0, current_price=100.0)
        assert result.floored is False

    def test_floored_when_too_small(self):
        prices = _volatile_prices(100.0, 30, amplitude=50.0)
        sizer = AdaptivePositionSizer(SizingConfig(
            risk_per_trade_pct=0.001,
            min_position_size=5.0,
            max_position_pct=100.0,
        ))
        result = sizer.calculate_size("X", prices, 1_000.0, current_price=100.0)
        assert result.floored is True
        assert result.adjusted_size >= 5.0


# ---------------------------------------------------------------------------
# 19. Position size rounds down to integer
# ---------------------------------------------------------------------------

class TestRoundDown:
    def test_rounds_down(self):
        """adjusted_size is always a whole number (floor)."""
        prices = _steady_prices(100.0, 30, step=1.5)  # ATR = 1.5
        sizer = AdaptivePositionSizer(SizingConfig(
            risk_per_trade_pct=1.0,
            max_position_pct=100.0,
        ))
        result = sizer.calculate_size("X", prices, 100_000.0, current_price=100.0)
        # raw = 1000 / 1.5 = 666.67 -> floor = 666
        assert result.adjusted_size == 666.0
        assert result.adjusted_size == math.floor(result.adjusted_size)


# ---------------------------------------------------------------------------
# 20. Current price defaults to last price
# ---------------------------------------------------------------------------

class TestCurrentPriceDefault:
    def test_defaults_to_last(self):
        prices = _steady_prices(100.0, 30, step=1.0)
        last_price = prices[-1]
        sizer = AdaptivePositionSizer(SizingConfig(max_position_pct=100.0))
        result_default = sizer.calculate_size("X", prices, 100_000.0)
        result_explicit = sizer.calculate_size("X", prices, 100_000.0, current_price=last_price)
        assert result_default.adjusted_size == result_explicit.adjusted_size
        assert result_default.raw_size == pytest.approx(result_explicit.raw_size)


# ---------------------------------------------------------------------------
# 21. Method override in compute_volatility
# ---------------------------------------------------------------------------

class TestMethodOverride:
    def test_override_method(self):
        prices = _volatile_prices(100.0, 30, amplitude=5.0)
        sizer = AdaptivePositionSizer(SizingConfig(method=VolatilityMethod.ATR))
        vol_atr = sizer.compute_volatility(prices, method=VolatilityMethod.ATR)
        vol_std = sizer.compute_volatility(prices, method=VolatilityMethod.ROLLING_STD)
        # Both should be positive but may differ
        assert vol_atr > 0
        assert vol_std > 0


# ---------------------------------------------------------------------------
# 22. Empty price list
# ---------------------------------------------------------------------------

class TestEmptyPrices:
    def test_empty_volatility(self):
        sizer = AdaptivePositionSizer()
        assert sizer.compute_volatility([]) == 0.0

    def test_empty_sizing(self):
        sizer = AdaptivePositionSizer()
        result = sizer.calculate_size("X", [], 100_000.0)
        assert result.adjusted_size == 0.0


# ---------------------------------------------------------------------------
# 23. get_config returns the live config
# ---------------------------------------------------------------------------

class TestGetConfig:
    def test_get_config_identity(self):
        cfg = SizingConfig(lookback_period=42)
        sizer = AdaptivePositionSizer(cfg)
        assert sizer.get_config() is cfg
        assert sizer.get_config().lookback_period == 42
