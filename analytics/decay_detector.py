"""Strategy performance decay detector.

I-006: Monitors strategy health metrics over time and flags strategies
that show declining performance. Generates decay alerts for the alert
router and recommends review/suspension.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from data.trade_db import DB_PATH, get_conn

logger = logging.getLogger(__name__)


@dataclass
class DecayConfig:
    """Configuration for decay detection thresholds."""

    min_trades: int = 10               # Minimum trades before assessment
    lookback_days: int = 30            # Recent window for comparison
    baseline_days: int = 90            # Baseline window for comparison
    win_rate_floor_pct: float = 35.0   # Flag if win rate drops below this
    profit_factor_floor: float = 0.8   # Flag if profit factor drops below this
    max_consecutive_losses: int = 8    # Flag if N+ losses in a row
    enabled: bool = True


@dataclass
class StrategyHealth:
    """Health assessment for a single strategy."""

    strategy: str
    status: str  # healthy | warning | decay | insufficient_data
    flags: list[str] = field(default_factory=list)
    recent_trades: int = 0
    recent_win_rate_pct: float = 0.0
    recent_profit_factor: float = 0.0
    recent_pnl: float = 0.0
    baseline_win_rate_pct: float = 0.0
    baseline_profit_factor: float = 0.0
    consecutive_losses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "status": self.status,
            "flags": self.flags,
            "recent_trades": self.recent_trades,
            "recent_win_rate_pct": round(self.recent_win_rate_pct, 2),
            "recent_profit_factor": round(self.recent_profit_factor, 2),
            "recent_pnl": round(self.recent_pnl, 2),
            "baseline_win_rate_pct": round(self.baseline_win_rate_pct, 2),
            "baseline_profit_factor": round(self.baseline_profit_factor, 2),
            "consecutive_losses": self.consecutive_losses,
        }


def detect_decay(
    config: Optional[DecayConfig] = None,
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[StrategyHealth]:
    """Run decay detection across all strategies with closed trades.

    Returns a list of StrategyHealth assessments, one per strategy.
    """
    if config is None:
        config = DecayConfig()

    if not config.enabled:
        return []

    if report_date is None:
        report_date = date.today().isoformat()

    strategies = _get_active_strategies(db_path)
    results = []

    for strategy in strategies:
        health = _assess_strategy(strategy, config, report_date, db_path)
        results.append(health)

    return results


def get_decaying_strategies(
    config: Optional[DecayConfig] = None,
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[StrategyHealth]:
    """Convenience: return only strategies with decay or warning status."""
    all_health = detect_decay(config=config, report_date=report_date, db_path=db_path)
    return [h for h in all_health if h.status in ("decay", "warning")]


def _get_active_strategies(db_path: str) -> list[str]:
    """Get distinct strategies that have closed trades."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT DISTINCT strategy FROM trades
               WHERE action = 'CLOSE' AND strategy IS NOT NULL AND strategy != ''
               ORDER BY strategy"""
        ).fetchall()
        return [row["strategy"] for row in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _assess_strategy(
    strategy: str,
    config: DecayConfig,
    report_date: str,
    db_path: str,
) -> StrategyHealth:
    """Assess a single strategy for performance decay."""
    health = StrategyHealth(strategy=strategy, status="healthy")

    conn = get_conn(db_path)
    try:
        # Get all closed trades for this strategy
        all_trades = conn.execute(
            """SELECT timestamp, pnl FROM trades
               WHERE strategy = ? AND action = 'CLOSE' AND pnl IS NOT NULL
               ORDER BY timestamp ASC""",
            (strategy,),
        ).fetchall()
    except Exception:
        all_trades = []
    finally:
        conn.close()

    if len(all_trades) < config.min_trades:
        health.status = "insufficient_data"
        health.recent_trades = len(all_trades)
        return health

    # Split into baseline and recent windows
    report_dt = datetime.strptime(report_date, "%Y-%m-%d")
    recent_cutoff = (report_dt - timedelta(days=config.lookback_days)).isoformat()
    baseline_cutoff = (report_dt - timedelta(days=config.baseline_days)).isoformat()

    recent_pnls = [float(t["pnl"]) for t in all_trades if t["timestamp"] >= recent_cutoff]
    baseline_pnls = [float(t["pnl"]) for t in all_trades if t["timestamp"] < recent_cutoff and t["timestamp"] >= baseline_cutoff]

    # If not enough data in windows, use all trades as baseline
    if len(baseline_pnls) < 5:
        baseline_pnls = [float(t["pnl"]) for t in all_trades]

    health.recent_trades = len(recent_pnls)

    # Compute recent metrics
    if recent_pnls:
        health.recent_pnl = sum(recent_pnls)
        recent_wins = [p for p in recent_pnls if p > 0]
        recent_losses = [p for p in recent_pnls if p <= 0]
        health.recent_win_rate_pct = (len(recent_wins) / len(recent_pnls)) * 100.0
        gross_profit = sum(recent_wins) if recent_wins else 0.0
        gross_loss = abs(sum(recent_losses)) if recent_losses else 0.0
        health.recent_profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else float("inf")
        )

    # Compute baseline metrics
    if baseline_pnls:
        baseline_wins = [p for p in baseline_pnls if p > 0]
        health.baseline_win_rate_pct = (len(baseline_wins) / len(baseline_pnls)) * 100.0
        b_profit = sum(p for p in baseline_pnls if p > 0)
        b_loss = abs(sum(p for p in baseline_pnls if p <= 0))
        health.baseline_profit_factor = b_profit / b_loss if b_loss > 0 else float("inf")

    # Compute consecutive losses (from most recent trades)
    all_pnls_desc = [float(t["pnl"]) for t in reversed(all_trades)]
    consec = 0
    for p in all_pnls_desc:
        if p <= 0:
            consec += 1
        else:
            break
    health.consecutive_losses = consec

    # Apply decay detection rules
    flags = []

    if health.recent_win_rate_pct < config.win_rate_floor_pct and health.recent_trades >= 5:
        flags.append(f"win_rate_below_floor ({health.recent_win_rate_pct:.1f}% < {config.win_rate_floor_pct:.1f}%)")

    if health.recent_profit_factor < config.profit_factor_floor and health.recent_trades >= 5:
        flags.append(f"profit_factor_below_floor ({health.recent_profit_factor:.2f} < {config.profit_factor_floor:.2f})")

    if health.consecutive_losses >= config.max_consecutive_losses:
        flags.append(f"consecutive_losses ({health.consecutive_losses} >= {config.max_consecutive_losses})")

    health.flags = flags

    if len(flags) >= 2:
        health.status = "decay"
    elif len(flags) == 1:
        health.status = "warning"
    else:
        health.status = "healthy"

    return health
