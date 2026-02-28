"""
Institutional-grade performance analytics.

Generates weekly/monthly performance reports with metrics used by prop trading desks:
- Sharpe & Sortino ratios (rolling and annualised)
- P&L attribution by strategy, market, and time period
- Drawdown analysis with time-to-recovery
- Profit factor, expectancy, R-multiples
- Strategy decay detection (rolling Sharpe vs baseline)
- Execution quality (slippage, fill rates)

Usage:
    from analytics.performance import PerformanceAnalyser
    pa = PerformanceAnalyser()
    report = pa.weekly_report()
    decay = pa.strategy_decay()
"""
import math
import logging
from datetime import datetime, timedelta, date
from typing import Optional
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from data.trade_db import (
    get_all_trades, get_closed_trades, get_daily_snapshots,
    get_strategy_stats, get_summary, get_bot_events, DB_PATH,
)

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.045  # UK base rate approx (annualised)
TRADING_DAYS_PER_YEAR = 252


# ─── Data classes ────────────────────────────────────────────────────────────

@dataclass
class StrategyMetrics:
    """Full institutional metrics for a single strategy."""
    name: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0          # Average R-multiple
    avg_hold_bars: float = 0.0
    sharpe: float = 0.0                # Annualised
    sortino: float = 0.0               # Annualised
    max_drawdown_pct: float = 0.0
    max_consecutive_losses: int = 0
    recovery_factor: float = 0.0       # Total PnL / Max DD
    payoff_ratio: float = 0.0          # Avg win / Avg loss


@dataclass
class WeeklyReport:
    """Structured weekly performance report."""
    period_start: str
    period_end: str
    # Portfolio level
    starting_equity: float = 0.0
    ending_equity: float = 0.0
    period_return_pct: float = 0.0
    period_pnl: float = 0.0
    # Trade stats for the period
    trades_opened: int = 0
    trades_closed: int = 0
    signals_generated: int = 0
    signals_rejected: int = 0
    # Per-strategy breakdown
    strategy_metrics: dict = field(default_factory=dict)
    # Portfolio metrics (all time, rolling)
    sharpe_30d: float = 0.0
    sharpe_90d: float = 0.0
    sortino_30d: float = 0.0
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    # Market attribution
    pnl_by_market: dict = field(default_factory=dict)
    # Alerts
    alerts: list = field(default_factory=list)


@dataclass
class DecayReport:
    """Strategy decay analysis."""
    strategy: str
    baseline_sharpe: float             # From initial backtest period
    current_rolling_sharpe: float      # Rolling 90-day
    decay_pct: float                   # How much Sharpe has dropped
    status: str                        # "healthy", "warning", "decaying"
    rolling_sharpe_history: list = field(default_factory=list)  # [(date, sharpe), ...]


# ─── Core analyser ───────────────────────────────────────────────────────────

class PerformanceAnalyser:
    """Institutional-grade trading performance analytics."""

    def __init__(self, risk_per_trade_pct: float = 1.0):
        self.risk_per_trade = risk_per_trade_pct

    # ─── Strategy-level metrics ──────────────────────────────────────────

    def calc_strategy_metrics(self, strategy_name: str,
                               trades: Optional[list] = None) -> StrategyMetrics:
        """Calculate full institutional metrics for one strategy."""
        if trades is None:
            all_trades = get_closed_trades()
            trades = [t for t in all_trades if t.get("strategy") == strategy_name]

        m = StrategyMetrics(name=strategy_name)
        if not trades:
            return m

        pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
        if not pnls:
            return m

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        m.trades = len(pnls)
        m.wins = len(wins)
        m.losses = len(losses)
        m.win_rate = len(wins) / len(pnls) * 100 if pnls else 0
        m.total_pnl = sum(pnls)
        m.avg_pnl = np.mean(pnls)
        m.avg_win = np.mean(wins) if wins else 0
        m.avg_loss = np.mean(losses) if losses else 0
        m.best_trade = max(pnls)
        m.worst_trade = min(pnls)

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.001
        m.profit_factor = gross_profit / gross_loss

        # Payoff ratio
        m.payoff_ratio = abs(m.avg_win / m.avg_loss) if m.avg_loss != 0 else float('inf')

        # Expectancy in R-multiples (normalised to risk per trade)
        # R = PnL / risk_amount. Since we risk 1% equity, approximate R as PnL / avg_risk
        avg_risk = abs(m.avg_loss) if m.avg_loss != 0 else 1.0
        m.expectancy_r = m.avg_pnl / avg_risk

        # Sharpe & Sortino (annualised from trade-level returns)
        if len(pnls) >= 5:
            returns = np.array(pnls)
            mean_ret = np.mean(returns)
            std_ret = np.std(returns, ddof=1) if len(returns) > 1 else 1.0

            # Estimate trades per year for annualisation
            trades_per_year = min(len(pnls) * (252 / max(self._trading_days_span(trades), 1)), 500)
            ann_factor = math.sqrt(trades_per_year) if trades_per_year > 0 else 1.0

            m.sharpe = (mean_ret / std_ret) * ann_factor if std_ret > 0 else 0
            downside = returns[returns < 0]
            downside_std = np.std(downside, ddof=1) if len(downside) > 1 else std_ret
            m.sortino = (mean_ret / downside_std) * ann_factor if downside_std > 0 else 0

        # Max consecutive losses
        m.max_consecutive_losses = self._max_consecutive_losses(pnls)

        # Max drawdown from trade equity curve
        cum_pnl = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum_pnl)
        dd = cum_pnl - peak
        m.max_drawdown_pct = float(min(dd)) if len(dd) > 0 else 0

        # Recovery factor
        if m.max_drawdown_pct < 0:
            m.recovery_factor = m.total_pnl / abs(m.max_drawdown_pct)
        else:
            m.recovery_factor = float('inf') if m.total_pnl > 0 else 0

        return m

    # ─── Weekly report ───────────────────────────────────────────────────

    def weekly_report(self, weeks_back: int = 1) -> WeeklyReport:
        """Generate a weekly performance report."""
        now = datetime.now()
        period_end = now
        period_start = now - timedelta(days=7 * weeks_back)

        report = WeeklyReport(
            period_start=period_start.strftime("%Y-%m-%d"),
            period_end=period_end.strftime("%Y-%m-%d"),
        )

        # ─── Equity from snapshots ────────────────────────────────────────
        snapshots = get_daily_snapshots()
        if snapshots:
            snap_df = pd.DataFrame(snapshots)
            snap_df["date"] = pd.to_datetime(snap_df["date"])

            period_snaps = snap_df[snap_df["date"] >= pd.Timestamp(period_start)]
            if not period_snaps.empty:
                report.starting_equity = float(period_snaps.iloc[0]["equity"])
                report.ending_equity = float(period_snaps.iloc[-1]["equity"])
            else:
                report.starting_equity = float(snap_df.iloc[-1]["equity"])
                report.ending_equity = report.starting_equity

            if report.starting_equity > 0:
                report.period_return_pct = (
                    (report.ending_equity - report.starting_equity) / report.starting_equity * 100
                )
            report.period_pnl = report.ending_equity - report.starting_equity

            # Rolling Sharpe from daily equity changes
            if len(snap_df) >= 5:
                snap_df["daily_return"] = snap_df["equity"].pct_change()

                # 30-day rolling Sharpe
                last_30 = snap_df.tail(30)["daily_return"].dropna()
                if len(last_30) >= 5:
                    report.sharpe_30d = self._annualised_sharpe(last_30)

                # 90-day rolling Sharpe
                last_90 = snap_df.tail(90)["daily_return"].dropna()
                if len(last_90) >= 5:
                    report.sharpe_90d = self._annualised_sharpe(last_90)

                # 30-day rolling Sortino
                if len(last_30) >= 5:
                    report.sortino_30d = self._annualised_sortino(last_30)

            # Drawdown
            if len(snap_df) >= 2:
                equities = snap_df["equity"].values
                peak = np.maximum.accumulate(equities)
                dd_pct = (equities - peak) / peak * 100
                report.max_drawdown_pct = float(min(dd_pct))
                report.current_drawdown_pct = float(dd_pct[-1])

        # ─── Trade stats for period ───────────────────────────────────────
        all_trades = get_all_trades()
        period_trades = [
            t for t in all_trades
            if t.get("timestamp", "") >= period_start.isoformat()
        ]

        report.trades_opened = sum(1 for t in period_trades if t.get("action") == "OPEN")
        report.trades_closed = sum(1 for t in period_trades if t.get("action") == "CLOSE")

        # Bot events for signal/rejection counts
        events = get_bot_events(limit=5000)
        period_events = [
            e for e in events
            if e.get("timestamp", "") >= period_start.isoformat()
        ]
        report.signals_generated = sum(1 for e in period_events if e.get("category") == "SIGNAL")
        report.signals_rejected = sum(1 for e in period_events if e.get("category") == "REJECTION")

        # ─── Per-strategy metrics ─────────────────────────────────────────
        strategies = set(t.get("strategy", "") for t in all_trades if t.get("strategy"))
        for strat in strategies:
            report.strategy_metrics[strat] = self.calc_strategy_metrics(strat)

        # ─── P&L by market (from closed trades in period) ─────────────────
        closed_period = [
            t for t in period_trades
            if t.get("action") == "CLOSE" and t.get("pnl") is not None
        ]
        pnl_by_mkt = {}
        for t in closed_period:
            ticker = t.get("ticker", "?")
            pnl_by_mkt[ticker] = pnl_by_mkt.get(ticker, 0) + t["pnl"]
        report.pnl_by_market = {k: round(v, 2) for k, v in sorted(pnl_by_mkt.items(), key=lambda x: x[1], reverse=True)}

        # ─── Alerts ───────────────────────────────────────────────────────
        if report.current_drawdown_pct < -5:
            report.alerts.append(f"WARNING: Drawdown at {report.current_drawdown_pct:.1f}%")
        if report.sharpe_30d < 0:
            report.alerts.append(f"30-day Sharpe is negative ({report.sharpe_30d:.2f})")

        error_count = sum(1 for e in period_events if e.get("category") == "ERROR")
        if error_count > 5:
            report.alerts.append(f"{error_count} errors in the last week")

        rejection_rate = (
            report.signals_rejected / report.signals_generated * 100
            if report.signals_generated > 0 else 0
        )
        if rejection_rate > 30:
            report.alerts.append(f"High rejection rate: {rejection_rate:.0f}%")

        return report

    # ─── Strategy decay ──────────────────────────────────────────────────

    def strategy_decay(self, baseline_window_days: int = 90,
                        rolling_window_days: int = 90) -> list[DecayReport]:
        """
        Detect strategy decay by comparing rolling Sharpe to a baseline.

        The baseline is the Sharpe from the first N days of trading.
        We then compute rolling N-day Sharpe and track the trend.
        """
        closed = get_closed_trades()
        if not closed:
            return []

        # Reverse so oldest first
        closed = list(reversed(closed))

        strategies = set(t.get("strategy", "") for t in closed if t.get("strategy"))
        reports = []

        for strat in strategies:
            strat_trades = [t for t in closed if t.get("strategy") == strat]
            pnls = [t["pnl"] for t in strat_trades if t.get("pnl") is not None]
            timestamps = [t.get("timestamp", "") for t in strat_trades if t.get("pnl") is not None]

            if len(pnls) < 10:
                reports.append(DecayReport(
                    strategy=strat, baseline_sharpe=0, current_rolling_sharpe=0,
                    decay_pct=0, status="insufficient_data",
                ))
                continue

            # Build trade-level equity curve with dates
            trade_df = pd.DataFrame({"pnl": pnls, "date": pd.to_datetime(timestamps)})
            trade_df = trade_df.sort_values("date").reset_index(drop=True)

            # Baseline Sharpe: first N trades or first N days
            baseline_cutoff = trade_df["date"].iloc[0] + timedelta(days=baseline_window_days)
            baseline_trades = trade_df[trade_df["date"] <= baseline_cutoff]

            if len(baseline_trades) < 5:
                baseline_trades = trade_df.head(max(10, len(trade_df) // 3))

            baseline_pnls = baseline_trades["pnl"].values
            baseline_sharpe = self._trade_sharpe(baseline_pnls)

            # Rolling Sharpe history
            rolling_history = []
            window_size = max(10, len(pnls) // 5)  # At least 10 trades per window

            for i in range(window_size, len(trade_df) + 1):
                window_pnls = trade_df["pnl"].iloc[max(0, i - window_size):i].values
                window_date = trade_df["date"].iloc[i - 1]
                rs = self._trade_sharpe(window_pnls)
                rolling_history.append((window_date.isoformat(), round(rs, 3)))

            current_sharpe = rolling_history[-1][1] if rolling_history else 0

            # Decay assessment
            if baseline_sharpe > 0:
                decay_pct = ((baseline_sharpe - current_sharpe) / baseline_sharpe * 100)
            else:
                decay_pct = 0

            if decay_pct > 50:
                status = "decaying"
            elif decay_pct > 25:
                status = "warning"
            else:
                status = "healthy"

            reports.append(DecayReport(
                strategy=strat,
                baseline_sharpe=round(baseline_sharpe, 3),
                current_rolling_sharpe=round(current_sharpe, 3),
                decay_pct=round(decay_pct, 1),
                status=status,
                rolling_sharpe_history=rolling_history,
            ))

        return reports

    # ─── P&L attribution ─────────────────────────────────────────────────

    def pnl_attribution(self, days: int = 7) -> dict:
        """Break down P&L by strategy, market, and day."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        closed = get_closed_trades()
        period_trades = [t for t in closed if t.get("timestamp", "") >= cutoff]

        by_strategy = {}
        by_market = {}
        by_day = {}

        for t in period_trades:
            pnl = t.get("pnl", 0) or 0
            strat = t.get("strategy", "unknown")
            ticker = t.get("ticker", "unknown")
            day = t.get("timestamp", "")[:10]

            by_strategy[strat] = by_strategy.get(strat, 0) + pnl
            by_market[ticker] = by_market.get(ticker, 0) + pnl
            by_day[day] = by_day.get(day, 0) + pnl

        return {
            "by_strategy": {k: round(v, 2) for k, v in by_strategy.items()},
            "by_market": {k: round(v, 2) for k, v in by_market.items()},
            "by_day": {k: round(v, 2) for k, v in sorted(by_day.items())},
            "total": round(sum(t.get("pnl", 0) or 0 for t in period_trades), 2),
            "trade_count": len(period_trades),
        }

    # ─── Helper methods ──────────────────────────────────────────────────

    def _annualised_sharpe(self, daily_returns: pd.Series) -> float:
        """Sharpe ratio from daily returns series."""
        mean = daily_returns.mean()
        std = daily_returns.std()
        if std == 0 or np.isnan(std):
            return 0.0
        daily_rf = RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
        return float((mean - daily_rf) / std * math.sqrt(TRADING_DAYS_PER_YEAR))

    def _annualised_sortino(self, daily_returns: pd.Series) -> float:
        """Sortino ratio from daily returns series."""
        mean = daily_returns.mean()
        daily_rf = RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
        downside = daily_returns[daily_returns < daily_rf]
        downside_std = downside.std() if len(downside) > 1 else daily_returns.std()
        if downside_std == 0 or np.isnan(downside_std):
            return 0.0
        return float((mean - daily_rf) / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR))

    def _trade_sharpe(self, pnls: np.ndarray) -> float:
        """Quick Sharpe from an array of trade P&Ls (not annualised)."""
        if len(pnls) < 2:
            return 0.0
        mean = np.mean(pnls)
        std = np.std(pnls, ddof=1)
        return float(mean / std) if std > 0 else 0.0

    def _max_consecutive_losses(self, pnls: list) -> int:
        """Count longest losing streak."""
        max_streak = 0
        current = 0
        for p in pnls:
            if p <= 0:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak

    def _trading_days_span(self, trades: list) -> int:
        """How many calendar days the trades span."""
        dates = [t.get("timestamp", "")[:10] for t in trades if t.get("timestamp")]
        if len(dates) < 2:
            return 1
        try:
            first = datetime.fromisoformat(min(dates))
            last = datetime.fromisoformat(max(dates))
            return max((last - first).days, 1)
        except (ValueError, TypeError):
            return 1
