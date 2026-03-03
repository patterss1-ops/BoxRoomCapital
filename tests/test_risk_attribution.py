"""Tests for J-005 risk attribution engine."""

from __future__ import annotations

import numpy as np

from analytics.risk_attribution import (
    AttributionResult,
    FactorExposure,
    PortfolioAttribution,
    attribute_portfolio,
    attribute_returns,
)


class TestAttributeReturns:
    def test_empty_factors(self):
        r = attribute_returns([0.01, 0.02, -0.01, 0.015, 0.005], {})
        assert r.idiosyncratic_risk_pct == 100.0
        assert r.r_squared_total == 0.0

    def test_insufficient_data(self):
        r = attribute_returns([0.01, 0.02], {"market": [0.005, 0.01]})
        assert r.idiosyncratic_risk_pct == 100.0

    def test_perfect_factor_exposure(self):
        """Strategy = 2 * market → R² ≈ 1."""
        np.random.seed(42)
        market = np.random.normal(0.001, 0.02, 100)
        strategy = market * 2.0  # Perfect 2x beta

        r = attribute_returns(
            list(strategy),
            {"market": list(market)},
            strategy_name="2x_market",
        )
        assert r.strategy == "2x_market"
        assert r.r_squared_total > 0.99
        assert len(r.factor_exposures) == 1
        assert abs(r.factor_exposures[0].beta - 2.0) < 0.05

    def test_zero_factor_exposure(self):
        """Uncorrelated strategy → low R²."""
        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 50))
        np.random.seed(99)
        strategy = list(np.random.normal(0.001, 0.02, 50))

        r = attribute_returns(strategy, {"market": market})
        assert r.r_squared_total < 0.3  # Likely very low

    def test_multi_factor(self):
        """Two factors explaining returns."""
        np.random.seed(42)
        f1 = np.random.normal(0.001, 0.02, 100)
        f2 = np.random.normal(0.0, 0.015, 100)
        noise = np.random.normal(0, 0.002, 100)
        strategy = 1.5 * f1 + 0.5 * f2 + noise

        r = attribute_returns(
            list(strategy),
            {"market": list(f1), "sector": list(f2)},
        )
        assert len(r.factor_exposures) == 2
        assert r.r_squared_total > 0.8

    def test_to_dict(self):
        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 30))
        strategy = [m * 1.5 for m in market]
        r = attribute_returns(strategy, {"market": market}, strategy_name="test")
        d = r.to_dict()
        assert d["strategy"] == "test"
        assert "factor_exposures" in d
        assert len(d["factor_exposures"]) == 1

    def test_systematic_vs_idiosyncratic_sum(self):
        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 50))
        strategy = [m * 1.2 + 0.001 for m in market]
        r = attribute_returns(strategy, {"market": market})
        # Systematic + idiosyncratic should be close to 100%
        total = r.systematic_risk_pct + r.idiosyncratic_risk_pct
        assert abs(total - 100.0) < 0.01


class TestAttributePortfolio:
    def test_empty(self):
        r = attribute_portfolio({}, {})
        assert len(r.strategy_attributions) == 0
        assert r.dominant_factor == "none"

    def test_single_strategy(self):
        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 50))
        strategy = [m * 1.5 for m in market]
        r = attribute_portfolio(
            {"strat1": strategy},
            {"market": market},
        )
        assert len(r.strategy_attributions) == 1
        assert r.dominant_factor == "market"

    def test_multi_strategy(self):
        np.random.seed(42)
        market = np.random.normal(0.001, 0.02, 50)
        sector = np.random.normal(0.0, 0.015, 50)
        s1 = list(market * 1.5)
        s2 = list(sector * 2.0)

        r = attribute_portfolio(
            {"market_follower": s1, "sector_player": s2},
            {"market": list(market), "sector": list(sector)},
        )
        assert len(r.strategy_attributions) == 2
        assert r.dominant_factor in ("market", "sector")

    def test_to_dict(self):
        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 30))
        r = attribute_portfolio(
            {"s1": [m * 1.2 for m in market]},
            {"market": market},
        )
        d = r.to_dict()
        assert "portfolio_systematic_pct" in d
        assert "strategies" in d
        assert len(d["strategies"]) == 1
