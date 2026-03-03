"""Fund-level drawdown circuit breaker.

I-003: Auto-halt trading when portfolio drawdown exceeds configurable
thresholds. Checks daily and rolling period drawdowns against limits
and returns halt/allow decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from data.trade_db import DB_PATH, get_conn

logger = logging.getLogger(__name__)


class DrawdownAction(str, Enum):
    """Action to take when drawdown is assessed."""
    ALLOW = "allow"
    HALT = "halt"
    WARN = "warn"


@dataclass
class DrawdownConfig:
    """Drawdown thresholds for circuit breaker."""

    daily_halt_pct: float = 5.0      # Halt if daily drawdown exceeds this %
    weekly_halt_pct: float = 10.0    # Halt if 7-day drawdown exceeds this %
    daily_warn_pct: float = 3.0      # Warn if daily drawdown exceeds this %
    weekly_warn_pct: float = 7.0     # Warn if 7-day drawdown exceeds this %
    enabled: bool = True


@dataclass
class DrawdownDecision:
    """Result of drawdown assessment."""

    action: DrawdownAction
    reason: str
    daily_drawdown_pct: float
    weekly_drawdown_pct: float
    current_nav: float
    high_water_mark: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "daily_drawdown_pct": round(self.daily_drawdown_pct, 4),
            "weekly_drawdown_pct": round(self.weekly_drawdown_pct, 4),
            "current_nav": round(self.current_nav, 2),
            "high_water_mark": round(self.high_water_mark, 2),
        }


def check_drawdown(
    config: Optional[DrawdownConfig] = None,
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> DrawdownDecision:
    """Check fund-level drawdown against thresholds.

    Reads from fund_daily_report to compute daily and rolling drawdown.
    Returns a decision: ALLOW, WARN, or HALT.
    """
    if config is None:
        config = DrawdownConfig()

    if not config.enabled:
        return DrawdownDecision(
            action=DrawdownAction.ALLOW,
            reason="DRAWDOWN_CHECK_DISABLED",
            daily_drawdown_pct=0.0,
            weekly_drawdown_pct=0.0,
            current_nav=0.0,
            high_water_mark=0.0,
        )

    if report_date is None:
        report_date = date.today().isoformat()

    # Fetch NAV history for drawdown calculation
    daily_dd, weekly_dd, current_nav, hwm = _compute_drawdowns(
        report_date, db_path
    )

    # Check halt thresholds (most severe first)
    if daily_dd >= config.daily_halt_pct:
        return DrawdownDecision(
            action=DrawdownAction.HALT,
            reason=f"DAILY_HALT: {daily_dd:.2f}% >= {config.daily_halt_pct:.2f}%",
            daily_drawdown_pct=daily_dd,
            weekly_drawdown_pct=weekly_dd,
            current_nav=current_nav,
            high_water_mark=hwm,
        )

    if weekly_dd >= config.weekly_halt_pct:
        return DrawdownDecision(
            action=DrawdownAction.HALT,
            reason=f"WEEKLY_HALT: {weekly_dd:.2f}% >= {config.weekly_halt_pct:.2f}%",
            daily_drawdown_pct=daily_dd,
            weekly_drawdown_pct=weekly_dd,
            current_nav=current_nav,
            high_water_mark=hwm,
        )

    # Check warn thresholds
    if daily_dd >= config.daily_warn_pct:
        return DrawdownDecision(
            action=DrawdownAction.WARN,
            reason=f"DAILY_WARN: {daily_dd:.2f}% >= {config.daily_warn_pct:.2f}%",
            daily_drawdown_pct=daily_dd,
            weekly_drawdown_pct=weekly_dd,
            current_nav=current_nav,
            high_water_mark=hwm,
        )

    if weekly_dd >= config.weekly_warn_pct:
        return DrawdownDecision(
            action=DrawdownAction.WARN,
            reason=f"WEEKLY_WARN: {weekly_dd:.2f}% >= {config.weekly_warn_pct:.2f}%",
            daily_drawdown_pct=daily_dd,
            weekly_drawdown_pct=weekly_dd,
            current_nav=current_nav,
            high_water_mark=hwm,
        )

    # All clear
    return DrawdownDecision(
        action=DrawdownAction.ALLOW,
        reason="DRAWDOWN_OK",
        daily_drawdown_pct=daily_dd,
        weekly_drawdown_pct=weekly_dd,
        current_nav=current_nav,
        high_water_mark=hwm,
    )


def _compute_drawdowns(
    report_date: str,
    db_path: str,
) -> tuple[float, float, float, float]:
    """Compute daily and weekly drawdown from fund_daily_report.

    Returns: (daily_drawdown_pct, weekly_drawdown_pct, current_nav, high_water_mark)
    """
    conn = get_conn(db_path)
    try:
        # Get today's report
        today_row = conn.execute(
            "SELECT total_nav, high_water_mark, drawdown_pct FROM fund_daily_report WHERE report_date = ?",
            (report_date,),
        ).fetchone()

        if today_row is None:
            return 0.0, 0.0, 0.0, 0.0

        current_nav = float(today_row["total_nav"])
        hwm = float(today_row["high_water_mark"])
        daily_dd = abs(float(today_row["drawdown_pct"])) if today_row["drawdown_pct"] else 0.0

        # Compute weekly drawdown: max NAV in last 7 days vs current
        week_start = (
            datetime.strptime(report_date, "%Y-%m-%d") - timedelta(days=7)
        ).strftime("%Y-%m-%d")

        week_rows = conn.execute(
            """SELECT MAX(total_nav) AS peak_nav
               FROM fund_daily_report
               WHERE report_date >= ? AND report_date <= ?""",
            (week_start, report_date),
        ).fetchone()

        weekly_dd = 0.0
        if week_rows and week_rows["peak_nav"] and float(week_rows["peak_nav"]) > 0:
            peak = float(week_rows["peak_nav"])
            weekly_dd = ((peak - current_nav) / peak) * 100.0
            weekly_dd = max(0.0, weekly_dd)

        return daily_dd, weekly_dd, current_nav, hwm

    except Exception as exc:
        logger.warning("Drawdown computation failed: %s", exc)
        return 0.0, 0.0, 0.0, 0.0
    finally:
        conn.close()
