"""
Portfolio manager — coordinates strategies, tracks positions, handles sizing.
This is the brain that turns strategy signals into broker orders.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from strategies.base import Signal, SignalType
from broker.base import BaseBroker, Position
from data.trade_db import log_trade, upsert_position, remove_position, save_daily_snapshot
from portfolio.risk import calc_position_size, SizeResult, RISK_PARAMS
from data.provider import DataProvider
import config

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    """Internal tracking of a position across bars."""
    ticker: str
    strategy: str
    direction: str  # "long" or "short"
    entry_price: float
    entry_time: datetime
    bars_held: int = 0
    size: float = 0.0


class PortfolioManager:
    """
    Manages positions across all strategies and markets.
    Translates signals into broker orders with proper sizing.
    """

    def __init__(self, broker: BaseBroker, data_provider: Optional[DataProvider] = None):
        self.broker = broker
        self.data = data_provider or DataProvider(lookback_days=500)
        self.positions: dict[str, PositionState] = {}  # key = "ticker:strategy"
        self.max_positions = config.PORTFOLIO["max_open_positions"]

    def _pos_key(self, ticker: str, strategy: str) -> str:
        return f"{ticker}:{strategy}"

    def get_position_state(self, ticker: str, strategy: str) -> Optional[PositionState]:
        """Get internal position state."""
        return self.positions.get(self._pos_key(ticker, strategy))

    def get_position_size(self, ticker: str, strategy: str) -> float:
        """Get current position size (>0 long, <0 short, 0 flat)."""
        pos = self.get_position_state(ticker, strategy)
        if pos is None:
            return 0.0
        return pos.size if pos.direction == "long" else -pos.size

    def get_bars_in_trade(self, ticker: str, strategy: str) -> int:
        """Get number of bars the current position has been held."""
        pos = self.get_position_state(ticker, strategy)
        return pos.bars_held if pos else 0

    def increment_bars(self):
        """Called once per daily run to increment bar count for all positions."""
        for pos in self.positions.values():
            pos.bars_held += 1

    def process_signal(self, signal: Signal, current_price: float) -> bool:
        """
        Process a trading signal: execute via broker if appropriate.

        Args:
            signal: The strategy signal
            current_price: Current close price (for logging/P&L)

        Returns:
            True if an order was placed, False otherwise
        """
        if signal.signal_type == SignalType.NONE:
            return False

        key = self._pos_key(signal.ticker, signal.strategy_name)

        # ─── Exits ───────────────────────────────────────────────────────
        if signal.signal_type in (SignalType.LONG_EXIT, SignalType.SHORT_EXIT):
            pos = self.positions.get(key)
            if pos is None:
                logger.warning(f"Exit signal for {key} but no position tracked")
                return False

            result = self.broker.close_position(signal.ticker, signal.strategy_name)
            if result.success:
                # Calculate P&L
                if pos.direction == "long":
                    pnl_points = current_price - pos.entry_price
                else:
                    pnl_points = pos.entry_price - current_price
                pnl_gbp = pnl_points * pos.size

                logger.info(
                    f"CLOSED {pos.direction.upper()} {signal.ticker} "
                    f"[{signal.strategy_name}] "
                    f"entry={pos.entry_price:.2f} exit={current_price:.2f} "
                    f"P&L=£{pnl_gbp:.2f} ({pnl_points:.1f}pts) "
                    f"held={pos.bars_held} bars. "
                    f"Reason: {signal.reason}"
                )

                # Log to database
                log_trade(
                    ticker=signal.ticker, strategy=signal.strategy_name,
                    direction="SELL" if pos.direction == "long" else "BUY",
                    action="CLOSE", size=pos.size, price=current_price,
                    deal_id=result.order_id, pnl=pnl_gbp,
                    notes=f"{signal.reason} | held {pos.bars_held} bars",
                )
                remove_position(result.order_id or key)

                del self.positions[key]
                return True
            else:
                logger.error(f"Failed to close {key}: {result.message}")
                return False

        # ─── Entries ─────────────────────────────────────────────────────
        if signal.signal_type in (SignalType.LONG_ENTRY, SignalType.SHORT_ENTRY):
            # Check if we already have a position
            if key in self.positions:
                logger.warning(f"Entry signal for {key} but already have position")
                return False

            # Check max positions
            if len(self.positions) >= self.max_positions:
                logger.info(
                    f"Skipping {signal.ticker} [{signal.strategy_name}]: "
                    f"max positions reached ({self.max_positions})"
                )
                return False

            # ─── Risk-based position sizing ─────────────────────────────
            # Get current portfolio risk state
            current_risk = sum(
                p.size * p.entry_price * 0.02  # ~2% of notional as risk proxy
                for p in self.positions.values()
            )
            current_margin = sum(
                p.size * p.entry_price * 0.05  # ~5% margin estimate
                for p in self.positions.values()
            )

            equity = config.PORTFOLIO["initial_capital"]  # TODO: use live equity from broker
            try:
                acct = self.broker.get_account_info()
                if acct.equity > 0:
                    equity = acct.equity
            except Exception:
                pass

            # Fetch price data for sizing calculation
            data_ticker = signal.ticker.replace("_trend", "")
            df = self.data.get_daily_bars(data_ticker)
            if df.empty:
                logger.error(f"No price data for sizing {signal.ticker}")
                return False

            size_result = calc_position_size(
                ticker=signal.ticker,
                strategy_name=signal.strategy_name,
                df=df,
                equity=equity,
                current_portfolio_risk=current_risk,
                current_total_margin=current_margin,
                vix_size_multiplier=signal.size_multiplier,
            )

            if size_result.stake_per_point <= 0:
                logger.info(
                    f"Skipping {signal.ticker} [{signal.strategy_name}]: "
                    f"{size_result.notes}"
                )
                return False

            stake = size_result.stake_per_point

            # Place order
            if signal.signal_type == SignalType.LONG_ENTRY:
                result = self.broker.place_long(signal.ticker, stake, signal.strategy_name)
                direction = "long"
            else:
                result = self.broker.place_short(signal.ticker, stake, signal.strategy_name)
                direction = "short"

            if result.success:
                self.positions[key] = PositionState(
                    ticker=signal.ticker,
                    strategy=signal.strategy_name,
                    direction=direction,
                    entry_price=current_price,
                    entry_time=datetime.now(),
                    bars_held=0,
                    size=stake,
                )
                logger.info(
                    f"OPENED {direction.upper()} {signal.ticker} "
                    f"[{signal.strategy_name}] "
                    f"@ {current_price:.2f}, £{stake:.2f}/pt "
                    f"(risk=£{size_result.risk_amount:.0f}, "
                    f"{size_result.risk_pct_of_equity:.1f}% of equity, "
                    f"stop={size_result.stop_distance:.1f}pts {size_result.stop_type}, "
                    f"margin=£{size_result.margin_required:.0f}). "
                    f"Reason: {signal.reason}"
                )

                # Log to database
                log_trade(
                    ticker=signal.ticker, strategy=signal.strategy_name,
                    direction="BUY" if direction == "long" else "SELL",
                    action="OPEN", size=stake, price=current_price,
                    deal_id=result.order_id,
                    notes=(
                        f"{signal.reason} | "
                        f"risk=£{size_result.risk_amount:.0f} ({size_result.risk_pct_of_equity:.1f}%) | "
                        f"stop={size_result.stop_distance:.1f}pts {size_result.stop_type} | "
                        f"margin=£{size_result.margin_required:.0f}"
                    ),
                )
                upsert_position(
                    deal_id=result.order_id or key,
                    ticker=signal.ticker, strategy=signal.strategy_name,
                    direction=direction, size=stake,
                    entry_price=current_price, entry_time=datetime.now().isoformat(),
                )

                return True
            else:
                logger.error(f"Failed to open {key}: {result.message}")
                return False

        return False

    def save_snapshot(self):
        """Save end-of-day snapshot to database."""
        try:
            account = self.broker.get_account_info()
            save_daily_snapshot(
                balance=account.balance,
                equity=account.equity,
                unrealised_pnl=account.unrealised_pnl,
                open_positions=len(self.positions),
            )
            logger.info("Daily snapshot saved to database")
        except Exception as e:
            logger.warning(f"Failed to save daily snapshot: {e}")

    def daily_summary(self) -> str:
        """Generate a daily portfolio summary and save snapshot."""
        # Save snapshot to DB
        self.save_snapshot()

        lines = [
            f"Portfolio Summary — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Open positions: {len(self.positions)}/{self.max_positions}",
            "─" * 50,
        ]

        if not self.positions:
            lines.append("  No open positions")
        else:
            for key, pos in self.positions.items():
                lines.append(
                    f"  {pos.direction.upper():5} {pos.ticker:12} "
                    f"[{pos.strategy:20}] "
                    f"entry={pos.entry_price:>10.2f} "
                    f"held={pos.bars_held} bars "
                    f"£{pos.size}/pt"
                )

        return "\n".join(lines)
