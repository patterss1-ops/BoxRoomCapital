"""Strategy comparison framework.

J-003: Side-by-side strategy performance comparison with normalized metrics,
relative ranking, and statistical significance testing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from analytics.portfolio_analytics import PerformanceMetrics, compute_metrics


@dataclass
class ComparisonEntry:
    """One strategy's metrics in the comparison."""

    strategy: str
    metrics: PerformanceMetrics
    rank: int = 0  # Overall rank (1=best)
    rank_by: dict[str, int] = field(default_factory=dict)  # Per-metric rank


@dataclass
class ComparisonResult:
    """Side-by-side comparison of multiple strategies."""

    entries: list[ComparisonEntry]
    ranking_metric: str = "sharpe_ratio"
    best_strategy: str = ""
    worst_strategy: str = ""
    summary: dict[str, Any] = field(default_factory=dict)

    def to_table(self) -> list[dict[str, Any]]:
        """Export as a list of dicts for tabular display."""
        rows = []
        for e in self.entries:
            row = {"strategy": e.strategy, "rank": e.rank}
            row.update(e.metrics.to_dict())
            rows.append(row)
        return rows


@dataclass
class SignificanceResult:
    """Statistical significance test between two return series."""

    strategy_a: str
    strategy_b: str
    mean_diff: float  # a - b
    t_statistic: float
    p_value_approx: float
    significant_at_5pct: bool


def compare_strategies(
    strategies: dict[str, Sequence[float]],
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.0,
    rank_by: str = "sharpe_ratio",
) -> ComparisonResult:
    """Compare multiple strategies by their return series.

    Args:
        strategies: Dict of {strategy_name: return_series}.
        periods_per_year: For annualisation.
        risk_free_rate: Annual risk-free rate.
        rank_by: Metric to rank by (default: sharpe_ratio).
    """
    entries = []
    for name, returns in strategies.items():
        m = compute_metrics(returns, periods_per_year, risk_free_rate)
        entries.append(ComparisonEntry(strategy=name, metrics=m))

    if not entries:
        return ComparisonResult(entries=[])

    # Rank by primary metric (higher is better for most metrics)
    higher_is_better = {
        "sharpe_ratio", "sortino_ratio", "calmar_ratio",
        "annualised_return_pct", "total_return_pct", "win_rate_pct",
        "profit_factor", "positive_periods",
    }
    reverse = rank_by in higher_is_better

    def _key(e: ComparisonEntry) -> float:
        return getattr(e.metrics, rank_by, 0.0)

    entries.sort(key=_key, reverse=reverse)
    for i, e in enumerate(entries):
        e.rank = i + 1

    # Also rank per-metric
    rank_metrics = [
        "sharpe_ratio", "sortino_ratio", "annualised_return_pct",
        "max_drawdown_pct", "profit_factor", "win_rate_pct",
    ]
    for metric in rank_metrics:
        rev = metric in higher_is_better
        if metric == "max_drawdown_pct":
            rev = False  # Less negative is better
        sorted_entries = sorted(entries, key=lambda e: getattr(e.metrics, metric, 0.0), reverse=rev)
        for i, e in enumerate(sorted_entries):
            e.rank_by[metric] = i + 1

    result = ComparisonResult(
        entries=entries,
        ranking_metric=rank_by,
        best_strategy=entries[0].strategy if entries else "",
        worst_strategy=entries[-1].strategy if entries else "",
    )

    # Summary stats across strategies
    if len(entries) > 1:
        sharpes = [e.metrics.sharpe_ratio for e in entries]
        result.summary = {
            "strategies_compared": len(entries),
            "sharpe_spread": round(max(sharpes) - min(sharpes), 4),
            "mean_sharpe": round(float(np.mean(sharpes)), 4),
        }

    return result


def check_significance(
    returns_a: Sequence[float],
    returns_b: Sequence[float],
    name_a: str = "A",
    name_b: str = "B",
) -> SignificanceResult:
    """Paired t-test on two return series to check if difference is significant.

    Uses Welch's t-test approximation (unequal variances allowed).
    Both series must have equal length.
    """
    a = np.array(returns_a, dtype=float)
    b = np.array(returns_b, dtype=float)
    n = min(len(a), len(b))
    a = a[:n]
    b = b[:n]

    if n < 5:
        return SignificanceResult(
            strategy_a=name_a, strategy_b=name_b,
            mean_diff=0.0, t_statistic=0.0,
            p_value_approx=1.0, significant_at_5pct=False,
        )

    diff = a - b
    mean_diff = float(np.mean(diff))
    se = float(np.std(diff, ddof=1)) / np.sqrt(n)

    if se < 1e-12:
        # If mean_diff is also ~0, no significance; if nonzero, highly significant
        if abs(mean_diff) < 1e-12:
            t_stat = 0.0
            p_val = 1.0
        else:
            t_stat = float("inf") if mean_diff > 0 else float("-inf")
            p_val = 0.0
    else:
        t_stat = mean_diff / se
        # Approximate two-tailed p-value using normal approximation for large n
        p_val = 2.0 * _normal_cdf(-abs(t_stat))

    return SignificanceResult(
        strategy_a=name_a,
        strategy_b=name_b,
        mean_diff=round(mean_diff, 6),
        t_statistic=round(t_stat, 4),
        p_value_approx=round(p_val, 4),
        significant_at_5pct=p_val < 0.05,
    )


def _normal_cdf(x: float) -> float:
    """Approximate standard normal CDF using Abramowitz & Stegun."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
