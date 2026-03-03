"""Tests for J-003 strategy comparison framework."""

from __future__ import annotations

import numpy as np

from analytics.strategy_comparison import (
    ComparisonResult,
    SignificanceResult,
    compare_strategies,
    check_significance,
)


class TestCompareStrategies:
    def test_empty(self):
        r = compare_strategies({})
        assert len(r.entries) == 0

    def test_single_strategy(self):
        r = compare_strategies({"A": [0.01, 0.02, -0.005, 0.015, 0.01]})
        assert len(r.entries) == 1
        assert r.entries[0].rank == 1
        assert r.best_strategy == "A"

    def test_two_strategies_ranked_by_sharpe(self):
        # Strategy A: consistent positive returns (high Sharpe)
        a_returns = [0.01] * 20
        # Strategy B: high vol, mixed (lower Sharpe)
        np.random.seed(42)
        b_returns = list(np.random.normal(0.005, 0.03, 20))

        r = compare_strategies({"A": a_returns, "B": b_returns}, rank_by="sharpe_ratio")
        assert r.entries[0].strategy == "A"  # A has higher Sharpe
        assert r.entries[0].rank == 1
        assert r.entries[1].rank == 2

    def test_rank_by_return(self):
        a = [0.02] * 10  # 2% per period
        b = [0.01] * 10  # 1% per period
        r = compare_strategies({"A": a, "B": b}, rank_by="total_return_pct")
        assert r.entries[0].strategy == "A"

    def test_per_metric_ranks(self):
        a = [0.01] * 20
        b = [0.005, 0.015] * 10
        r = compare_strategies({"A": a, "B": b})
        for e in r.entries:
            assert "sharpe_ratio" in e.rank_by
            assert "profit_factor" in e.rank_by

    def test_to_table(self):
        a = [0.01, 0.02, -0.005]
        b = [0.005, 0.01, 0.001]
        r = compare_strategies({"X": a, "Y": b})
        table = r.to_table()
        assert len(table) == 2
        assert "strategy" in table[0]
        assert "rank" in table[0]
        assert "sharpe_ratio" in table[0]

    def test_summary_stats(self):
        a = [0.01] * 20
        b = [0.005] * 20
        r = compare_strategies({"A": a, "B": b})
        assert "strategies_compared" in r.summary
        assert r.summary["strategies_compared"] == 2
        assert "sharpe_spread" in r.summary

    def test_three_strategies(self):
        a = [0.01] * 20
        b = [0.005] * 20
        c = [0.0] * 20
        r = compare_strategies({"A": a, "B": b, "C": c})
        assert len(r.entries) == 3
        assert r.best_strategy == "A"
        assert r.worst_strategy == "C"


class TestSignificance:
    def test_insufficient_data(self):
        r = check_significance([0.01], [0.02], "A", "B")
        assert r.p_value_approx == 1.0
        assert r.significant_at_5pct is False

    def test_identical_returns(self):
        rets = [0.01, 0.02, -0.01, 0.015, -0.005, 0.01]
        r = check_significance(rets, rets, "A", "B")
        assert abs(r.mean_diff) < 1e-10
        assert r.significant_at_5pct is False

    def test_clearly_different(self):
        a = [0.05] * 30
        b = [-0.05] * 30
        r = check_significance(a, b, "Winner", "Loser")
        assert r.mean_diff > 0
        assert r.significant_at_5pct is True

    def test_names_preserved(self):
        a = [0.01, 0.02, -0.01, 0.015, -0.005]
        b = [0.005, 0.01, -0.005, 0.01, -0.002]
        r = check_significance(a, b, "Alpha", "Beta")
        assert r.strategy_a == "Alpha"
        assert r.strategy_b == "Beta"

    def test_p_value_range(self):
        np.random.seed(42)
        a = list(np.random.normal(0.001, 0.02, 50))
        b = list(np.random.normal(0.0005, 0.02, 50))
        r = check_significance(a, b)
        assert 0.0 <= r.p_value_approx <= 1.0
