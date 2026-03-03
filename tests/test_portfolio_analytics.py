"""Tests for J-001 portfolio analytics calculations."""

from __future__ import annotations

import math

import numpy as np

from analytics.portfolio_analytics import (
    CorrelationMatrix,
    DrawdownInfo,
    PerformanceMetrics,
    RollingStats,
    compute_correlation_matrix,
    compute_drawdowns,
    compute_metrics,
    compute_rolling_stats,
)


class TestComputeMetrics:
    def test_empty_returns(self):
        m = compute_metrics([])
        assert m.total_periods == 0
        assert m.total_return_pct == 0.0

    def test_positive_returns(self):
        # 10 days of 1% returns
        returns = [0.01] * 10
        m = compute_metrics(returns, periods_per_year=252)
        assert m.total_return_pct > 0
        assert m.annualised_return_pct > 0
        assert m.sharpe_ratio > 0
        assert m.win_rate_pct == 100.0
        assert m.positive_periods == 10
        assert m.negative_periods == 0

    def test_negative_returns(self):
        returns = [-0.01] * 10
        m = compute_metrics(returns, periods_per_year=252)
        assert m.total_return_pct < 0
        assert m.sharpe_ratio < 0
        assert m.win_rate_pct == 0.0

    def test_mixed_returns(self):
        returns = [0.02, -0.01, 0.015, -0.005, 0.01, -0.02, 0.03, -0.005]
        m = compute_metrics(returns, periods_per_year=252)
        assert m.total_periods == 8
        assert m.positive_periods == 4
        assert m.negative_periods == 4
        assert m.win_rate_pct == 50.0
        assert m.profit_factor > 0

    def test_max_drawdown(self):
        # Up then down
        returns = [0.10, 0.10, -0.15, -0.10]
        m = compute_metrics(returns, periods_per_year=252)
        assert m.max_drawdown_pct < 0

    def test_sharpe_with_risk_free_rate(self):
        returns = [0.001] * 100
        m0 = compute_metrics(returns, risk_free_rate=0.0)
        m4 = compute_metrics(returns, risk_free_rate=0.04)
        # Higher risk-free rate should reduce Sharpe
        assert m0.sharpe_ratio > m4.sharpe_ratio

    def test_sortino_only_downside(self):
        # Mostly positive with a couple negatives → sortino defined
        returns = [0.01, 0.02, 0.015, -0.005, 0.01, -0.003, 0.02, 0.005]
        m = compute_metrics(returns, periods_per_year=252)
        # With few negatives, sortino should be > sharpe
        assert m.sortino_ratio > m.sharpe_ratio

    def test_calmar_ratio(self):
        returns = [0.01, 0.01, -0.05, 0.01, 0.01]
        m = compute_metrics(returns, periods_per_year=252)
        assert m.calmar_ratio != 0.0

    def test_skewness_and_kurtosis(self):
        np.random.seed(42)
        returns = list(np.random.normal(0.001, 0.02, 100))
        m = compute_metrics(returns)
        # Normal distribution should have skew ≈ 0, kurtosis ≈ 0
        assert abs(m.skewness) < 1.0
        assert abs(m.kurtosis) < 2.0

    def test_to_dict(self):
        m = compute_metrics([0.01, -0.005, 0.02])
        d = m.to_dict()
        assert "sharpe_ratio" in d
        assert "total_return_pct" in d
        assert isinstance(d["total_return_pct"], float)

    def test_best_worst_period(self):
        returns = [0.05, -0.03, 0.02, -0.01]
        m = compute_metrics(returns)
        assert m.best_period_pct == 5.0
        assert m.worst_period_pct == -3.0


class TestComputeDrawdowns:
    def test_no_drawdown(self):
        equity = [100, 101, 102, 103, 104]
        dds = compute_drawdowns(equity)
        assert len(dds) == 0

    def test_single_drawdown(self):
        equity = [100, 105, 95, 90, 100, 110]
        dds = compute_drawdowns(equity)
        assert len(dds) >= 1
        assert dds[0].depth_pct < 0

    def test_ongoing_drawdown(self):
        equity = [100, 90, 85]
        dds = compute_drawdowns(equity)
        assert len(dds) == 1
        assert dds[0].end_idx == -1  # Ongoing

    def test_multiple_drawdowns_sorted_by_depth(self):
        equity = [100, 95, 100, 80, 100, 90, 100]
        dds = compute_drawdowns(equity, top_n=5)
        assert len(dds) >= 2
        # Most negative first
        assert dds[0].depth_pct <= dds[1].depth_pct

    def test_top_n_limit(self):
        equity = [100, 90, 100, 85, 100, 95, 100, 88, 100]
        dds = compute_drawdowns(equity, top_n=2)
        assert len(dds) <= 2


class TestComputeRollingStats:
    def test_insufficient_data(self):
        rs = compute_rolling_stats([0.01, 0.02], window=10)
        assert len(rs.rolling_return_pct) == 0

    def test_basic_rolling(self):
        returns = [0.01] * 20
        rs = compute_rolling_stats(returns, window=5)
        assert len(rs.rolling_return_pct) == 16  # 20 - 5 + 1
        assert all(r > 0 for r in rs.rolling_return_pct)

    def test_with_dates(self):
        returns = [0.01] * 10
        dates = [f"2026-01-{i+1:02d}" for i in range(10)]
        rs = compute_rolling_stats(returns, window=5, dates=dates)
        assert len(rs.dates) == 6
        assert rs.dates[0] == "2026-01-05"


class TestCorrelationMatrix:
    def test_perfect_correlation(self):
        a = [0.01, 0.02, -0.01, 0.03, -0.02]
        cm = compute_correlation_matrix({"A": a, "B": a})
        assert abs(cm.get("A", "B") - 1.0) < 0.001

    def test_negative_correlation(self):
        a = [0.01, 0.02, -0.01, 0.03, -0.02]
        b = [-x for x in a]
        cm = compute_correlation_matrix({"A": a, "B": b})
        assert cm.get("A", "B") < -0.99

    def test_diagonal_is_one(self):
        a = [0.01, -0.02, 0.015]
        b = [0.005, 0.01, -0.005]
        cm = compute_correlation_matrix({"X": a, "Y": b})
        assert abs(cm.get("X", "X") - 1.0) < 0.001
        assert abs(cm.get("Y", "Y") - 1.0) < 0.001

    def test_single_series(self):
        cm = compute_correlation_matrix({"A": [0.01, 0.02]})
        assert cm.labels == ["A"]
        assert cm.matrix == [[1.0]]

    def test_empty(self):
        cm = compute_correlation_matrix({})
        assert len(cm.labels) == 0
