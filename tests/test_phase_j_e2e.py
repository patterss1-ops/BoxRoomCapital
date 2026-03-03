"""Phase J acceptance harness + release checks.

J-007: End-to-end tests covering all Phase J deliverables.
Validates portfolio analytics, strategy comparison, risk attribution,
and cross-module integration.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Module import smoke tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseJModuleImports:
    """Verify all Phase J modules are importable."""

    def test_import_portfolio_analytics(self):
        from analytics.portfolio_analytics import (
            PerformanceMetrics,
            compute_correlation_matrix,
            compute_drawdowns,
            compute_metrics,
            compute_rolling_stats,
        )
        assert callable(compute_metrics)
        assert callable(compute_drawdowns)
        assert callable(compute_rolling_stats)
        assert callable(compute_correlation_matrix)
        assert PerformanceMetrics is not None

    def test_import_strategy_comparison(self):
        from analytics.strategy_comparison import (
            ComparisonResult,
            check_significance,
            compare_strategies,
        )
        assert callable(compare_strategies)
        assert callable(check_significance)
        assert ComparisonResult is not None

    def test_import_risk_attribution(self):
        from analytics.risk_attribution import (
            AttributionResult,
            PortfolioAttribution,
            attribute_portfolio,
            attribute_returns,
        )
        assert callable(attribute_returns)
        assert callable(attribute_portfolio)
        assert AttributionResult is not None
        assert PortfolioAttribution is not None

    def test_import_existing_backtester(self):
        """Existing backtester still importable."""
        from analytics.backtester import BacktestResult, BacktestTrade, Backtester
        assert Backtester is not None
        assert BacktestResult is not None
        assert BacktestTrade is not None

    def test_import_historical_cache(self):
        """Verify J-004 historical cache is importable."""
        from data.historical_cache import CacheEntry, GapInfo, HistoricalCache
        assert HistoricalCache is not None
        assert CacheEntry is not None
        assert GapInfo is not None

    def test_import_report_generator(self):
        """Verify J-006 report generator is importable."""
        from analytics.report_generator import (
            BacktestReport,
            generate_attribution_report,
            generate_comparison_report,
            generate_performance_report,
        )
        assert callable(generate_performance_report)
        assert callable(generate_comparison_report)
        assert callable(generate_attribution_report)
        assert BacktestReport is not None


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Portfolio analytics E2E (J-001)
# ═══════════════════════════════════════════════════════════════════════════


class TestPortfolioAnalyticsE2E:
    """End-to-end portfolio analytics validation."""

    def test_full_metrics_pipeline(self):
        """Compute metrics from realistic return series."""
        from analytics.portfolio_analytics import compute_metrics

        np.random.seed(42)
        returns = list(np.random.normal(0.0005, 0.015, 252))  # 1 year daily
        m = compute_metrics(returns, periods_per_year=252)

        assert m.total_periods == 252
        assert m.annualised_volatility_pct > 0
        assert m.best_period_pct > 0
        assert m.worst_period_pct < 0

    def test_drawdown_analysis(self):
        """Compute drawdowns from equity curve."""
        from analytics.portfolio_analytics import compute_drawdowns

        # Simulate equity curve with drawdown
        equity = [100]
        for _ in range(50):
            equity.append(equity[-1] * 1.005)
        for _ in range(20):
            equity.append(equity[-1] * 0.99)
        for _ in range(30):
            equity.append(equity[-1] * 1.003)

        dds = compute_drawdowns(equity, top_n=3)
        assert len(dds) >= 1
        assert dds[0].depth_pct < 0

    def test_rolling_stats_pipeline(self):
        """Rolling window statistics."""
        from analytics.portfolio_analytics import compute_rolling_stats

        np.random.seed(42)
        returns = list(np.random.normal(0.0005, 0.015, 100))
        rs = compute_rolling_stats(returns, window=21)
        assert len(rs.rolling_return_pct) == 80  # 100 - 21 + 1
        assert len(rs.rolling_sharpe) == 80

    def test_correlation_matrix(self):
        """Cross-strategy correlation analysis."""
        from analytics.portfolio_analytics import compute_correlation_matrix

        np.random.seed(42)
        s1 = list(np.random.normal(0.001, 0.02, 100))
        s2 = list(np.random.normal(0.0005, 0.015, 100))
        s3 = [-x for x in s1]  # Perfectly negatively correlated with s1

        cm = compute_correlation_matrix({"IBS": s1, "GTAA": s2, "Short": s3})
        assert abs(cm.get("IBS", "IBS") - 1.0) < 0.001
        assert cm.get("IBS", "Short") < -0.99


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Strategy comparison E2E (J-003)
# ═══════════════════════════════════════════════════════════════════════════


class TestStrategyComparisonE2E:
    """End-to-end strategy comparison validation."""

    def test_three_strategy_comparison(self):
        """Compare three strategies with different risk/return profiles."""
        from analytics.strategy_comparison import compare_strategies

        np.random.seed(42)
        conservative = list(np.random.normal(0.0003, 0.005, 252))
        moderate = list(np.random.normal(0.0005, 0.012, 252))
        aggressive = list(np.random.normal(0.0008, 0.025, 252))

        r = compare_strategies({
            "Conservative": conservative,
            "Moderate": moderate,
            "Aggressive": aggressive,
        })

        assert len(r.entries) == 3
        assert r.best_strategy != ""
        assert r.worst_strategy != ""
        assert r.best_strategy != r.worst_strategy

        table = r.to_table()
        assert len(table) == 3
        assert all("sharpe_ratio" in row for row in table)

    def test_significance_testing(self):
        """Statistical significance between strategies."""
        from analytics.strategy_comparison import check_significance

        np.random.seed(42)
        a = list(np.random.normal(0.002, 0.01, 100))
        b = list(np.random.normal(-0.001, 0.01, 100))

        result = check_significance(a, b, "Winner", "Loser")
        assert result.mean_diff > 0
        assert result.strategy_a == "Winner"


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Risk attribution E2E (J-005)
# ═══════════════════════════════════════════════════════════════════════════


class TestRiskAttributionE2E:
    """End-to-end risk attribution validation."""

    def test_single_factor_attribution(self):
        """Strategy with market exposure → beta detected."""
        from analytics.risk_attribution import attribute_returns

        np.random.seed(42)
        market = np.random.normal(0.001, 0.02, 200)
        strategy = market * 1.3 + np.random.normal(0, 0.003, 200)

        r = attribute_returns(list(strategy), {"market": list(market)}, "beta_strat")
        assert r.strategy == "beta_strat"
        assert r.r_squared_total > 0.8
        assert len(r.factor_exposures) == 1
        assert abs(r.factor_exposures[0].beta - 1.3) < 0.15

    def test_portfolio_attribution(self):
        """Multi-strategy portfolio attribution."""
        from analytics.risk_attribution import attribute_portfolio

        np.random.seed(42)
        market = np.random.normal(0.001, 0.02, 200)
        sector = np.random.normal(0.0, 0.015, 200)

        portfolio = attribute_portfolio(
            strategy_returns={
                "momentum": list(market * 1.5),
                "mean_rev": list(sector * 0.8),
            },
            factor_returns={"market": list(market), "sector": list(sector)},
        )
        assert len(portfolio.strategy_attributions) == 2
        assert portfolio.dominant_factor in ("market", "sector")


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Cross-module integration
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossModuleIntegration:
    """Validate Phase J modules integrate correctly."""

    def test_metrics_feed_into_comparison(self):
        """compute_metrics output is used by compare_strategies."""
        from analytics.portfolio_analytics import compute_metrics
        from analytics.strategy_comparison import compare_strategies

        np.random.seed(42)
        a = list(np.random.normal(0.001, 0.015, 100))
        b = list(np.random.normal(0.0005, 0.02, 100))

        # compare_strategies internally calls compute_metrics
        r = compare_strategies({"A": a, "B": b})
        assert r.entries[0].metrics.total_periods == 100

    def test_attribution_uses_same_return_format(self):
        """Risk attribution accepts same return format as analytics."""
        from analytics.portfolio_analytics import compute_metrics
        from analytics.risk_attribution import attribute_returns

        np.random.seed(42)
        returns = list(np.random.normal(0.001, 0.02, 100))
        market = list(np.random.normal(0.001, 0.02, 100))

        # Both work with the same return series format
        m = compute_metrics(returns)
        a = attribute_returns(returns, {"market": market})
        assert m.total_periods == 100
        assert a.total_return_pct != 0.0

    def test_backward_compatible_with_backtester(self):
        """Existing BacktestResult/Trade classes coexist."""
        from analytics.backtester import BacktestResult, BacktestTrade
        from analytics.portfolio_analytics import compute_metrics

        # Simulate converting backtest trades to return series
        trades = [
            BacktestTrade(ticker="SPY", strategy="IBS", direction="BUY",
                         entry_date="2026-01-01", entry_price=100.0,
                         exit_date="2026-01-05", exit_price=102.0,
                         pnl_net=2.0),
            BacktestTrade(ticker="SPY", strategy="IBS", direction="BUY",
                         entry_date="2026-01-06", entry_price=102.0,
                         exit_date="2026-01-10", exit_price=101.0,
                         pnl_net=-1.0),
        ]

        # Convert to returns
        equity = 10000.0
        returns = [t.pnl_net / equity for t in trades]
        m = compute_metrics(returns)
        assert m.total_periods == 2
        assert m.positive_periods == 1
        assert m.negative_periods == 1


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: Source file presence
# ═══════════════════════════════════════════════════════════════════════════


class TestPhaseJSourceFiles:
    """Validate all Phase J source files exist."""

    REQUIRED_FILES = [
        "analytics/portfolio_analytics.py",
        "analytics/strategy_comparison.py",
        "analytics/risk_attribution.py",
        "analytics/report_generator.py",
        "analytics/backtester.py",
        "data/historical_cache.py",
    ]

    @pytest.mark.parametrize("rel_path", REQUIRED_FILES)
    def test_source_file_exists(self, rel_path):
        full_path = os.path.join(PROJECT_ROOT, rel_path)
        assert os.path.isfile(full_path), f"Missing: {rel_path}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 7: Historical cache E2E (J-004)
# ═══════════════════════════════════════════════════════════════════════════


class TestHistoricalCacheE2E:
    """End-to-end historical data cache validation."""

    def test_store_retrieve_invalidate_lifecycle(self, tmp_path):
        """Full cache lifecycle: store → retrieve → invalidate."""
        from data.historical_cache import HistoricalCache

        hc = HistoricalCache(cache_dir=str(tmp_path))
        bars = [
            {"date": f"2026-01-{d:02d}", "open": 100 + d, "high": 101 + d,
             "low": 99 + d, "close": 100.5 + d, "volume": 10000}
            for d in range(1, 11)
        ]
        stored = hc.store_bars("AAPL", bars)
        assert stored == 10

        retrieved = hc.get_bars("AAPL")
        assert len(retrieved) == 10

        entry = hc.get_entry("AAPL")
        assert entry.bar_count == 10

        hc.invalidate("AAPL")
        assert hc.get_bars("AAPL") == []

    def test_gap_detection(self, tmp_path):
        """Detect gaps in cached data."""
        from data.historical_cache import HistoricalCache

        hc = HistoricalCache(cache_dir=str(tmp_path))
        bars = [
            {"date": "2026-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"date": "2026-01-20", "open": 105, "high": 106, "low": 104, "close": 105, "volume": 1000},
        ]
        hc.store_bars("MSFT", bars)
        gaps = hc.detect_gaps("MSFT")
        assert len(gaps) == 1
        assert gaps[0].missing_bars > 10


# ═══════════════════════════════════════════════════════════════════════════
# Section 8: Report generator E2E (J-006)
# ═══════════════════════════════════════════════════════════════════════════


class TestReportGeneratorE2E:
    """End-to-end report generation validation."""

    def test_performance_report_pipeline(self):
        """Generate performance report from return series."""
        from analytics.report_generator import generate_performance_report

        np.random.seed(42)
        returns = list(np.random.normal(0.0005, 0.015, 252))
        report = generate_performance_report(returns, "IBS++ v3")

        assert len(report.sections) >= 2
        json_str = report.to_json()
        assert "IBS++ v3" in json_str
        text = report.to_text()
        assert "IBS++ v3" in text

    def test_comparison_report_pipeline(self):
        """Generate comparison report from strategy results."""
        from analytics.report_generator import generate_comparison_report
        from analytics.strategy_comparison import compare_strategies

        np.random.seed(42)
        result = compare_strategies({
            "Conservative": list(np.random.normal(0.0003, 0.005, 100)),
            "Aggressive": list(np.random.normal(0.0008, 0.025, 100)),
        })

        report = generate_comparison_report(result)
        assert "Comparison" in report.title
        assert report.metadata["strategies_compared"] == 2

    def test_attribution_report_pipeline(self):
        """Generate attribution report from factor analysis."""
        from analytics.report_generator import generate_attribution_report
        from analytics.risk_attribution import attribute_portfolio

        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 100))
        portfolio = attribute_portfolio(
            {"momentum": [m * 1.5 for m in market]},
            {"market": market},
        )
        report = generate_attribution_report(portfolio)
        assert "Portfolio" in report.title
        j = report.to_json()
        assert len(j) > 0
