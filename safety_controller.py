"""
Safety controller — hard limits on risk to protect Steve's £5k.

Every order goes through check_order_viability() before execution.
Kill switch triggers if daily loss exceeds threshold — requires manual restart.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


@dataclass
class SafetyLimits:
    """Configurable safety limits."""
    max_risk_per_trade_pct: float = 2.0    # Max 2% of equity per trade (£100 on £5k)
    max_total_heat_pct: float = 4.0        # Max 4% across all open spreads (£200)
    max_daily_loss_pct: float = 10.0       # Kill switch at 10% daily loss (£500)
    max_open_spreads: int = 6              # Max 6 simultaneous spreads
    min_premium_pct: float = 2.0           # Don't trade if premium < 2% of width
    max_contracts_per_trade: int = 20      # Hard cap on contracts


class SafetyController:
    """
    Enforces risk limits on every trade. Tracks daily P&L and triggers
    a kill switch if losses exceed threshold.
    """

    def __init__(self, initial_equity: float, limits: Optional[SafetyLimits] = None):
        self.equity = initial_equity
        self.limits = limits or SafetyLimits()
        self.kill_switch = False
        self.kill_switch_reason = ""
        self._daily_pnl = 0.0
        self._daily_date = date.today()
        self._current_heat = 0.0  # Total £ at risk across open spreads
        self._open_spread_count = 0

    def update_state(self, equity: float, open_spreads: list[dict]):
        """
        Update controller with current portfolio state.
        Called on every tick / signal check.
        """
        self.equity = equity

        # Reset daily P&L counter at midnight
        today = date.today()
        if today != self._daily_date:
            self._daily_pnl = 0.0
            self._daily_date = today
            # Reset kill switch at start of new day (manual restart still required)

        # Recalculate heat from open positions
        self._current_heat = sum(
            float(s.get("max_loss", 0)) * float(s.get("size", 1))
            for s in open_spreads
        )
        self._open_spread_count = len(open_spreads)

    def record_closed_trade(self, pnl: float):
        """Record a closed trade's P&L for daily loss tracking."""
        self._daily_pnl += pnl

        # Check kill switch
        max_daily_loss = self.equity * (self.limits.max_daily_loss_pct / 100)
        if self._daily_pnl < -max_daily_loss:
            self.kill_switch = True
            self.kill_switch_reason = (
                f"Daily loss £{-self._daily_pnl:.0f} exceeds "
                f"£{max_daily_loss:.0f} ({self.limits.max_daily_loss_pct}% of equity)"
            )
            logger.error(f"KILL SWITCH TRIGGERED: {self.kill_switch_reason}")

    def check_order_viability(
        self,
        proposed_risk: float,
        proposed_size: float,
        premium_pct: float = 100.0,
    ) -> Tuple[bool, str]:
        """
        Check if a proposed trade passes all safety gates.

        Args:
            proposed_risk: Max loss in £ for this trade (spread_width - premium) × size
            proposed_size: Number of contracts
            premium_pct: Premium collected as % of spread width

        Returns:
            (viable, reason) — True if trade is allowed
        """
        # Gate 1: Kill switch
        if self.kill_switch:
            return False, f"KILL SWITCH: {self.kill_switch_reason}"

        # Gate 2: Max risk per trade
        # Allow 10% tolerance for minimum-size trades (1 contract) since we can't go below 1
        max_single_risk = self.equity * (self.limits.max_risk_per_trade_pct / 100)
        tolerance = 1.10 if proposed_size <= 1 else 1.0
        if proposed_risk > max_single_risk * tolerance:
            return False, (
                f"Risk £{proposed_risk:.0f} > max single trade "
                f"£{max_single_risk:.0f} ({self.limits.max_risk_per_trade_pct}%)"
            )

        # Gate 3: Total heat limit
        max_heat = self.equity * (self.limits.max_total_heat_pct / 100)
        if (self._current_heat + proposed_risk) > max_heat:
            return False, (
                f"Total heat would be £{self._current_heat + proposed_risk:.0f} "
                f"> max £{max_heat:.0f} ({self.limits.max_total_heat_pct}%)"
            )

        # Gate 4: Max open spreads
        if self._open_spread_count >= self.limits.max_open_spreads:
            return False, (
                f"Already {self._open_spread_count} spreads open "
                f"(max {self.limits.max_open_spreads})"
            )

        # Gate 5: Max contracts
        if proposed_size > self.limits.max_contracts_per_trade:
            return False, (
                f"Size {proposed_size} > max {self.limits.max_contracts_per_trade} contracts"
            )

        # Gate 6: Minimum premium quality
        if premium_pct < self.limits.min_premium_pct:
            return False, (
                f"Premium {premium_pct:.1f}% < min {self.limits.min_premium_pct}% of width"
            )

        # Gate 7: Daily loss approaching limit
        max_daily_loss = self.equity * (self.limits.max_daily_loss_pct / 100)
        if self._daily_pnl < -(max_daily_loss * 0.8):
            return False, (
                f"Daily loss £{-self._daily_pnl:.0f} at 80%+ of kill switch "
                f"(£{max_daily_loss:.0f}), pausing new trades"
            )

        return True, "OK"

    def get_status(self) -> dict:
        """Get current safety status for dashboard display."""
        max_heat = self.equity * (self.limits.max_total_heat_pct / 100)
        max_daily_loss = self.equity * (self.limits.max_daily_loss_pct / 100)

        return {
            "kill_switch": self.kill_switch,
            "kill_switch_reason": self.kill_switch_reason,
            "equity": self.equity,
            "current_heat": round(self._current_heat, 2),
            "max_heat": round(max_heat, 2),
            "heat_pct": round(self._current_heat / max_heat * 100, 1) if max_heat > 0 else 0,
            "open_spreads": self._open_spread_count,
            "max_spreads": self.limits.max_open_spreads,
            "daily_pnl": round(self._daily_pnl, 2),
            "max_daily_loss": round(max_daily_loss, 2),
            "daily_loss_pct": round(-self._daily_pnl / max_daily_loss * 100, 1) if max_daily_loss > 0 else 0,
        }

    def reset_kill_switch(self):
        """Manual reset of kill switch (requires explicit action)."""
        logger.warning("KILL SWITCH MANUALLY RESET")
        self.kill_switch = False
        self.kill_switch_reason = ""
        self._daily_pnl = 0.0
