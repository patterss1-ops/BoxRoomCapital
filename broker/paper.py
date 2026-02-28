"""
Paper trading broker — logs all trades locally without touching any real API.
Used for validation and cross-checking against TradingView.
"""
import csv
import logging
import os
from datetime import datetime
from typing import Optional

from broker.base import BaseBroker, BrokerCapabilities, OrderResult, Position, AccountInfo
import config

logger = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """Paper trading broker that tracks positions and P&L in memory + CSV."""

    capabilities = BrokerCapabilities(
        supports_spot_etf=True,
        supports_short=True,
        supports_paper=True,
        supports_live=False,
    )

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or config.PORTFOLIO["initial_capital"]
        self.balance = self.initial_capital
        self.positions: dict[str, Position] = {}  # key = "ticker:strategy"
        self.trade_history: list[dict] = []
        self.trade_counter = 0
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        logger.info(f"Paper broker connected. Capital: £{self.balance:.2f}")
        return True

    def disconnect(self):
        self._connected = False
        logger.info("Paper broker disconnected")

    def get_account_info(self) -> AccountInfo:
        unrealised = sum(p.unrealised_pnl for p in self.positions.values())
        return AccountInfo(
            balance=self.balance,
            equity=self.balance + unrealised,
            unrealised_pnl=unrealised,
            open_positions=len(self.positions),
            currency="GBP",
        )

    def get_positions(self) -> list[Position]:
        return list(self.positions.values())

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        key = f"{ticker}:{strategy}"
        return self.positions.get(key)

    def place_long(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        key = f"{ticker}:{strategy}"
        if key in self.positions:
            return OrderResult(success=False, message=f"Already have position for {key}")

        self.trade_counter += 1
        now = datetime.now()

        pos = Position(
            ticker=ticker,
            direction="long",
            size=stake_per_point,
            entry_price=0.0,  # Will be filled by portfolio manager with actual price
            entry_time=now,
            strategy=strategy,
        )
        self.positions[key] = pos

        trade = {
            "id": self.trade_counter,
            "time": now.isoformat(),
            "ticker": ticker,
            "strategy": strategy,
            "direction": "long",
            "action": "open",
            "stake": stake_per_point,
            "price": 0.0,  # Filled later
            "pnl": 0.0,
        }
        self.trade_history.append(trade)
        self._append_to_csv(trade)

        logger.info(f"PAPER LONG: {ticker} @ £{stake_per_point}/pt [{strategy}]")
        return OrderResult(
            success=True,
            order_id=str(self.trade_counter),
            fill_qty=stake_per_point,
            timestamp=now,
        )

    def place_short(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        key = f"{ticker}:{strategy}"
        if key in self.positions:
            return OrderResult(success=False, message=f"Already have position for {key}")

        self.trade_counter += 1
        now = datetime.now()

        pos = Position(
            ticker=ticker,
            direction="short",
            size=stake_per_point,
            entry_price=0.0,
            entry_time=now,
            strategy=strategy,
        )
        self.positions[key] = pos

        trade = {
            "id": self.trade_counter,
            "time": now.isoformat(),
            "ticker": ticker,
            "strategy": strategy,
            "direction": "short",
            "action": "open",
            "stake": stake_per_point,
            "price": 0.0,
            "pnl": 0.0,
        }
        self.trade_history.append(trade)
        self._append_to_csv(trade)

        logger.info(f"PAPER SHORT: {ticker} @ £{stake_per_point}/pt [{strategy}]")
        return OrderResult(
            success=True,
            order_id=str(self.trade_counter),
            fill_qty=stake_per_point,
            timestamp=now,
        )

    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        key = f"{ticker}:{strategy}"
        pos = self.positions.pop(key, None)
        if pos is None:
            return OrderResult(success=False, message=f"No position found for {key}")

        self.trade_counter += 1
        now = datetime.now()

        # P&L is tracked by portfolio manager with actual prices
        trade = {
            "id": self.trade_counter,
            "time": now.isoformat(),
            "ticker": ticker,
            "strategy": strategy,
            "direction": pos.direction,
            "action": "close",
            "stake": pos.size,
            "price": 0.0,
            "pnl": pos.unrealised_pnl,
        }
        self.trade_history.append(trade)
        self._append_to_csv(trade)

        self.balance += pos.unrealised_pnl

        logger.info(
            f"PAPER CLOSE: {ticker} {pos.direction} P&L=£{pos.unrealised_pnl:.2f} [{strategy}]"
        )
        return OrderResult(
            success=True,
            order_id=str(self.trade_counter),
            fill_price=0.0,
            timestamp=now,
        )

    def _append_to_csv(self, trade: dict):
        """Append a trade record to the CSV log."""
        file_path = config.TRADE_LOG_FILE
        file_exists = os.path.exists(file_path)

        with open(file_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade)

    def summary(self) -> str:
        """Print a summary of paper trading results."""
        info = self.get_account_info()
        total_trades = len(self.trade_history)
        closes = [t for t in self.trade_history if t["action"] == "close"]
        winners = [t for t in closes if t["pnl"] > 0]
        win_rate = len(winners) / len(closes) * 100 if closes else 0

        return (
            f"Paper Trading Summary:\n"
            f"  Balance: £{info.balance:.2f}\n"
            f"  Equity: £{info.equity:.2f}\n"
            f"  Total trades: {total_trades}\n"
            f"  Closed trades: {len(closes)}\n"
            f"  Win rate: {win_rate:.1f}%\n"
            f"  Open positions: {info.open_positions}\n"
            f"  Net P&L: £{info.balance - self.initial_capital:.2f}\n"
        )
