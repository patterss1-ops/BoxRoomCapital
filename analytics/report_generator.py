"""Backtest report generator.

J-006: Generates structured JSON and summary text reports from backtest
results and portfolio analytics. Designed for operator review and archival.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import numpy as np

from analytics.portfolio_analytics import (
    PerformanceMetrics,
    compute_drawdowns,
    compute_metrics,
)


@dataclass
class ReportSection:
    """One section of a generated report."""

    title: str
    content: dict[str, Any]


@dataclass
class BacktestReport:
    """Complete generated report from backtest/analytics data."""

    title: str
    generated_at: str
    sections: list[ReportSection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        """Serialise report to JSON."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "generated_at": self.generated_at,
            "metadata": self.metadata,
            "sections": [
                {"title": s.title, "content": s.content}
                for s in self.sections
            ],
        }

    def to_text(self) -> str:
        """Render report as human-readable text."""
        lines = [
            f"{'=' * 60}",
            f"  {self.title}",
            f"  Generated: {self.generated_at}",
            f"{'=' * 60}",
            "",
        ]
        for section in self.sections:
            lines.append(f"--- {section.title} ---")
            for k, v in section.content.items():
                if isinstance(v, list):
                    lines.append(f"  {k}:")
                    for item in v[:10]:  # Cap list display
                        lines.append(f"    - {item}")
                elif isinstance(v, dict):
                    lines.append(f"  {k}:")
                    for sk, sv in v.items():
                        lines.append(f"    {sk}: {sv}")
                else:
                    lines.append(f"  {k}: {v}")
            lines.append("")
        return "\n".join(lines)


def generate_performance_report(
    returns: Sequence[float],
    strategy_name: str = "Strategy",
    periods_per_year: float = 252.0,
    equity_start: float = 10000.0,
) -> BacktestReport:
    """Generate a performance report from a return series.

    Args:
        returns: Period returns as decimals (e.g. 0.01 for 1%).
        strategy_name: Name of the strategy.
        periods_per_year: For annualisation.
        equity_start: Starting equity for equity curve.
    """
    now = datetime.now(timezone.utc).isoformat()
    report = BacktestReport(
        title=f"Performance Report: {strategy_name}",
        generated_at=now,
        metadata={"strategy": strategy_name, "periods": len(returns)},
    )

    # Section 1: Performance metrics
    metrics = compute_metrics(returns, periods_per_year)
    report.sections.append(ReportSection(
        title="Performance Summary",
        content=metrics.to_dict(),
    ))

    # Section 2: Equity curve highlights
    arr = np.array(returns, dtype=float)
    cum = np.cumprod(1.0 + arr)
    equity = [round(equity_start * float(c), 2) for c in cum]
    final_equity = equity[-1] if equity else equity_start

    report.sections.append(ReportSection(
        title="Equity Curve",
        content={
            "start_equity": equity_start,
            "final_equity": final_equity,
            "peak_equity": max(equity) if equity else equity_start,
            "trough_equity": min(equity) if equity else equity_start,
        },
    ))

    # Section 3: Drawdown analysis
    if equity:
        drawdowns = compute_drawdowns(equity, top_n=3)
        dd_list = [
            {
                "depth_pct": dd.depth_pct,
                "duration_bars": dd.duration_bars,
                "recovery_bars": dd.recovery_bars,
            }
            for dd in drawdowns
        ]
        report.sections.append(ReportSection(
            title="Top Drawdowns",
            content={"drawdowns": dd_list, "count": len(dd_list)},
        ))

    return report


def generate_comparison_report(
    comparison_result: Any,
) -> BacktestReport:
    """Generate a report from a ComparisonResult.

    Args:
        comparison_result: A ComparisonResult from strategy_comparison.compare_strategies().
    """
    now = datetime.now(timezone.utc).isoformat()
    report = BacktestReport(
        title="Strategy Comparison Report",
        generated_at=now,
        metadata={
            "strategies_compared": len(comparison_result.entries),
            "ranking_metric": comparison_result.ranking_metric,
        },
    )

    # Ranking table
    rankings = []
    for e in comparison_result.entries:
        rankings.append({
            "rank": e.rank,
            "strategy": e.strategy,
            "sharpe": round(e.metrics.sharpe_ratio, 4),
            "return_pct": round(e.metrics.total_return_pct, 2),
            "max_dd_pct": round(e.metrics.max_drawdown_pct, 2),
            "win_rate": round(e.metrics.win_rate_pct, 1),
        })

    report.sections.append(ReportSection(
        title="Strategy Rankings",
        content={
            "best": comparison_result.best_strategy,
            "worst": comparison_result.worst_strategy,
            "rankings": rankings,
        },
    ))

    if comparison_result.summary:
        report.sections.append(ReportSection(
            title="Summary Statistics",
            content=comparison_result.summary,
        ))

    return report


def generate_attribution_report(
    attribution_result: Any,
) -> BacktestReport:
    """Generate a report from an AttributionResult or PortfolioAttribution.

    Args:
        attribution_result: From risk_attribution.attribute_returns() or attribute_portfolio().
    """
    now = datetime.now(timezone.utc).isoformat()

    # Check if this is a PortfolioAttribution or single AttributionResult
    if hasattr(attribution_result, "strategy_attributions"):
        # PortfolioAttribution
        report = BacktestReport(
            title="Portfolio Risk Attribution Report",
            generated_at=now,
            metadata={
                "strategies": len(attribution_result.strategy_attributions),
                "dominant_factor": attribution_result.dominant_factor,
            },
        )
        report.sections.append(ReportSection(
            title="Portfolio Summary",
            content={
                "systematic_risk_pct": round(attribution_result.portfolio_systematic_pct, 2),
                "idiosyncratic_risk_pct": round(attribution_result.portfolio_idiosyncratic_pct, 2),
                "dominant_factor": attribution_result.dominant_factor,
            },
        ))
        for attr in attribution_result.strategy_attributions:
            report.sections.append(ReportSection(
                title=f"Strategy: {attr.strategy}",
                content=attr.to_dict(),
            ))
    else:
        # Single AttributionResult
        report = BacktestReport(
            title=f"Risk Attribution: {attribution_result.strategy}",
            generated_at=now,
            metadata={"strategy": attribution_result.strategy},
        )
        report.sections.append(ReportSection(
            title="Attribution Summary",
            content=attribution_result.to_dict(),
        ))

    return report
