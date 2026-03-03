"""Tests for J-006 backtest report generator."""

from __future__ import annotations

import json

import numpy as np

from analytics.report_generator import (
    BacktestReport,
    ReportSection,
    generate_attribution_report,
    generate_comparison_report,
    generate_performance_report,
)


class TestBacktestReport:
    def test_to_dict(self):
        r = BacktestReport(
            title="Test",
            generated_at="2026-03-03T16:00:00Z",
            sections=[ReportSection(title="S1", content={"key": "val"})],
        )
        d = r.to_dict()
        assert d["title"] == "Test"
        assert len(d["sections"]) == 1

    def test_to_json(self):
        r = BacktestReport(title="Test", generated_at="now")
        j = r.to_json()
        parsed = json.loads(j)
        assert parsed["title"] == "Test"

    def test_to_text(self):
        r = BacktestReport(
            title="Test Report",
            generated_at="2026-03-03",
            sections=[
                ReportSection(title="Metrics", content={"sharpe": 1.5, "return": 10.0}),
            ],
        )
        text = r.to_text()
        assert "Test Report" in text
        assert "sharpe" in text


class TestPerformanceReport:
    def test_basic_report(self):
        np.random.seed(42)
        returns = list(np.random.normal(0.001, 0.015, 100))
        report = generate_performance_report(returns, "TestStrat")

        assert "TestStrat" in report.title
        assert len(report.sections) >= 2  # Performance + Equity + maybe Drawdowns
        assert report.metadata["strategy"] == "TestStrat"
        assert report.metadata["periods"] == 100

    def test_equity_curve_section(self):
        returns = [0.01] * 10
        report = generate_performance_report(returns, "Up", equity_start=10000)

        eq_section = next(s for s in report.sections if s.title == "Equity Curve")
        assert eq_section.content["start_equity"] == 10000
        assert eq_section.content["final_equity"] > 10000

    def test_drawdown_section(self):
        returns = [0.05, -0.10, -0.05, 0.03, 0.02]
        report = generate_performance_report(returns, "VolatileStrat")

        dd_section = next(s for s in report.sections if s.title == "Top Drawdowns")
        assert dd_section.content["count"] >= 0

    def test_json_serialisable(self):
        returns = [0.01, -0.005, 0.02]
        report = generate_performance_report(returns, "S")
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["title"].startswith("Performance Report")


class TestComparisonReport:
    def test_basic_comparison_report(self):
        from analytics.strategy_comparison import compare_strategies

        np.random.seed(42)
        result = compare_strategies({
            "A": list(np.random.normal(0.001, 0.015, 50)),
            "B": list(np.random.normal(0.0005, 0.02, 50)),
        })

        report = generate_comparison_report(result)
        assert "Comparison" in report.title
        assert report.metadata["strategies_compared"] == 2

        rank_section = next(s for s in report.sections if s.title == "Strategy Rankings")
        assert rank_section.content["best"] != ""
        assert len(rank_section.content["rankings"]) == 2

    def test_json_serialisable(self):
        from analytics.strategy_comparison import compare_strategies

        result = compare_strategies({
            "X": [0.01, 0.02, -0.01, 0.015, 0.005],
            "Y": [0.005, 0.01, 0.002, 0.008, -0.003],
        })
        report = generate_comparison_report(result)
        j = report.to_json()
        parsed = json.loads(j)
        assert "sections" in parsed


class TestAttributionReport:
    def test_single_attribution_report(self):
        from analytics.risk_attribution import attribute_returns

        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 50))
        strategy = [m * 1.5 for m in market]

        attr = attribute_returns(strategy, {"market": market}, "beta_strat")
        report = generate_attribution_report(attr)
        assert "beta_strat" in report.title

    def test_portfolio_attribution_report(self):
        from analytics.risk_attribution import attribute_portfolio

        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 50))

        portfolio = attribute_portfolio(
            {"s1": [m * 1.2 for m in market]},
            {"market": market},
        )
        report = generate_attribution_report(portfolio)
        assert "Portfolio" in report.title
        assert report.metadata["dominant_factor"] == "market"

    def test_json_serialisable(self):
        from analytics.risk_attribution import attribute_returns

        np.random.seed(42)
        market = list(np.random.normal(0.001, 0.02, 30))
        attr = attribute_returns([m * 1.3 for m in market], {"market": market})
        report = generate_attribution_report(attr)
        j = report.to_json()
        json.loads(j)  # Should not raise
