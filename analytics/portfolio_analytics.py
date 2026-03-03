"""Portfolio analytics calculations.

J-001: Standalone analytics functions for portfolio-level metrics,
rolling statistics, correlation analysis, and drawdown decomposition.
Operates on equity curves and return series independent of backtest engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np


@dataclass
class PerformanceMetrics:
    """Computed performance metrics for a return series."""

    total_return_pct: float = 0.0
    annualised_return_pct: float = 0.0
    annualised_volatility_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_bars: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 0.0
    best_period_pct: float = 0.0
    worst_period_pct: float = 0.0
    positive_periods: int = 0
    negative_periods: int = 0
    total_periods: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in self.__dict__.items()}


@dataclass
class DrawdownInfo:
    """Single drawdown event detail."""

    start_idx: int
    trough_idx: int
    end_idx: int  # Recovery index, -1 if ongoing
    depth_pct: float
    duration_bars: int
    recovery_bars: int  # 0 if ongoing


@dataclass
class RollingStats:
    """Rolling window statistics."""

    window: int
    dates: list[str] = field(default_factory=list)
    rolling_return_pct: list[float] = field(default_factory=list)
    rolling_volatility_pct: list[float] = field(default_factory=list)
    rolling_sharpe: list[float] = field(default_factory=list)


@dataclass
class CorrelationMatrix:
    """Correlation matrix between multiple return series."""

    labels: list[str]
    matrix: list[list[float]]  # len(labels) x len(labels)

    def get(self, a: str, b: str) -> float:
        """Get correlation between two series."""
        ia = self.labels.index(a)
        ib = self.labels.index(b)
        return self.matrix[ia][ib]


# ═══════════════════════════════════════════════════════════════════════════
# Core metric calculations
# ═══════════════════════════════════════════════════════════════════════════


def compute_metrics(
    returns: Sequence[float],
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.0,
) -> PerformanceMetrics:
    """Compute comprehensive performance metrics from a return series.

    Args:
        returns: Sequence of period returns (e.g. daily returns as decimals, not percentages).
        periods_per_year: Number of periods per year for annualisation (252 for daily).
        risk_free_rate: Annual risk-free rate as decimal (e.g. 0.04 for 4%).
    """
    arr = np.array(returns, dtype=float)
    n = len(arr)
    if n == 0:
        return PerformanceMetrics()

    metrics = PerformanceMetrics(total_periods=n)

    # Cumulative return
    cum = np.cumprod(1.0 + arr)
    metrics.total_return_pct = (cum[-1] - 1.0) * 100.0

    # Annualised return
    if n > 0:
        total_factor = cum[-1]
        years = n / periods_per_year
        if years > 0 and total_factor > 0:
            metrics.annualised_return_pct = (total_factor ** (1.0 / years) - 1.0) * 100.0

    # Volatility
    std = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    metrics.annualised_volatility_pct = std * math.sqrt(periods_per_year) * 100.0

    # Sharpe
    rf_per_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = arr - rf_per_period
    mean_excess = float(np.mean(excess))
    std_excess = float(np.std(excess, ddof=1)) if n > 1 else 0.0
    if std_excess > 0:
        metrics.sharpe_ratio = mean_excess / std_excess * math.sqrt(periods_per_year)

    # Sortino
    downside = excess[excess < 0]
    if len(downside) > 1:
        ds_std = float(np.std(downside, ddof=1))
        if ds_std > 0:
            metrics.sortino_ratio = mean_excess / ds_std * math.sqrt(periods_per_year)

    # Drawdown
    dd_pct, dd_dur = _max_drawdown(cum)
    metrics.max_drawdown_pct = dd_pct
    metrics.max_drawdown_duration_bars = dd_dur

    # Calmar
    if abs(metrics.max_drawdown_pct) > 0.001:
        metrics.calmar_ratio = metrics.annualised_return_pct / abs(metrics.max_drawdown_pct)

    # Win rate
    pos = arr[arr > 0]
    neg = arr[arr <= 0]
    metrics.positive_periods = len(pos)
    metrics.negative_periods = len(neg)
    if n > 0:
        metrics.win_rate_pct = len(pos) / n * 100.0

    # Profit factor
    gross_gain = float(np.sum(pos)) if len(pos) > 0 else 0.0
    gross_loss = abs(float(np.sum(neg))) if len(neg) > 0 else 0.001
    metrics.profit_factor = gross_gain / gross_loss if gross_loss > 0 else (float("inf") if gross_gain > 0 else 0.0)

    # Higher moments
    if n > 2:
        metrics.skewness = float(_skewness(arr))
    if n > 3:
        metrics.kurtosis = float(_kurtosis(arr))

    metrics.best_period_pct = float(np.max(arr)) * 100.0
    metrics.worst_period_pct = float(np.min(arr)) * 100.0

    return metrics


def compute_drawdowns(
    equity_curve: Sequence[float],
    top_n: int = 5,
) -> list[DrawdownInfo]:
    """Find the top N drawdown events from an equity curve."""
    arr = np.array(equity_curve, dtype=float)
    if len(arr) < 2:
        return []

    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / np.where(peak > 0, peak, 1.0)

    # Identify drawdown periods
    drawdowns: list[DrawdownInfo] = []
    in_dd = False
    start = 0
    trough = 0
    trough_val = 0.0

    for i in range(len(dd)):
        if dd[i] < 0:
            if not in_dd:
                start = i
                trough = i
                trough_val = dd[i]
                in_dd = True
            elif dd[i] < trough_val:
                trough = i
                trough_val = dd[i]
        elif in_dd:
            # Recovery
            drawdowns.append(DrawdownInfo(
                start_idx=start,
                trough_idx=trough,
                end_idx=i,
                depth_pct=round(trough_val * 100.0, 4),
                duration_bars=i - start,
                recovery_bars=i - trough,
            ))
            in_dd = False

    # Handle ongoing drawdown
    if in_dd:
        drawdowns.append(DrawdownInfo(
            start_idx=start,
            trough_idx=trough,
            end_idx=-1,
            depth_pct=round(trough_val * 100.0, 4),
            duration_bars=len(dd) - start,
            recovery_bars=0,
        ))

    # Sort by depth (most negative first)
    drawdowns.sort(key=lambda d: d.depth_pct)
    return drawdowns[:top_n]


def compute_rolling_stats(
    returns: Sequence[float],
    window: int = 63,
    periods_per_year: float = 252.0,
    dates: Optional[Sequence[str]] = None,
) -> RollingStats:
    """Compute rolling return, volatility, and Sharpe ratio."""
    arr = np.array(returns, dtype=float)
    n = len(arr)
    result = RollingStats(window=window)

    if n < window:
        return result

    for i in range(window, n + 1):
        w = arr[i - window:i]
        cum = float(np.prod(1.0 + w) - 1.0)
        vol = float(np.std(w, ddof=1)) * math.sqrt(periods_per_year) if len(w) > 1 else 0.0
        mean_r = float(np.mean(w))
        std_r = float(np.std(w, ddof=1)) if len(w) > 1 else 0.0
        sharpe = mean_r / std_r * math.sqrt(periods_per_year) if std_r > 0 else 0.0

        result.rolling_return_pct.append(round(cum * 100.0, 4))
        result.rolling_volatility_pct.append(round(vol * 100.0, 4))
        result.rolling_sharpe.append(round(sharpe, 4))

        if dates is not None and i - 1 < len(dates):
            result.dates.append(dates[i - 1])

    return result


def compute_correlation_matrix(
    series_map: dict[str, Sequence[float]],
) -> CorrelationMatrix:
    """Compute pairwise correlation matrix for multiple return series.

    Args:
        series_map: Dict of {label: returns_array}. All series must have equal length.
    """
    labels = list(series_map.keys())
    if len(labels) < 2:
        return CorrelationMatrix(labels=labels, matrix=[[1.0]] if labels else [])

    arrays = [np.array(series_map[lbl], dtype=float) for lbl in labels]
    min_len = min(len(a) for a in arrays)
    trimmed = [a[:min_len] for a in arrays]
    stacked = np.vstack(trimmed)

    corr = np.corrcoef(stacked)
    matrix = [[round(float(corr[i][j]), 4) for j in range(len(labels))] for i in range(len(labels))]
    return CorrelationMatrix(labels=labels, matrix=matrix)


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════


def _max_drawdown(cum_returns: np.ndarray) -> tuple[float, int]:
    """Compute max drawdown % and duration from cumulative return series."""
    peak = np.maximum.accumulate(cum_returns)
    dd = (cum_returns - peak) / np.where(peak > 0, peak, 1.0)
    max_dd = float(np.min(dd)) * 100.0

    # Duration of deepest drawdown
    in_dd = dd < 0
    max_dur = 0
    cur_dur = 0
    for v in in_dd:
        if v:
            cur_dur += 1
            max_dur = max(max_dur, cur_dur)
        else:
            cur_dur = 0
    return max_dd, max_dur


def _skewness(arr: np.ndarray) -> float:
    """Sample skewness."""
    n = len(arr)
    m = np.mean(arr)
    s = np.std(arr, ddof=1)
    if s == 0 or n < 3:
        return 0.0
    return float(n / ((n - 1) * (n - 2)) * np.sum(((arr - m) / s) ** 3))


def _kurtosis(arr: np.ndarray) -> float:
    """Excess kurtosis."""
    n = len(arr)
    m = np.mean(arr)
    s = np.std(arr, ddof=1)
    if s == 0 or n < 4:
        return 0.0
    k4 = float(np.mean(((arr - m) / s) ** 4))
    return k4 - 3.0
