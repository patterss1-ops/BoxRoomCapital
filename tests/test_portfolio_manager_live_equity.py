"""Tests for portfolio.manager live-equity resolution (D-003)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import config
from broker.base import AccountInfo, BaseBroker, Position
from data import trade_db
from execution import ledger
from portfolio.manager import resolve_portfolio_equity


class EquityBroker(BaseBroker):
    def __init__(self, equity: float, raise_on_account: bool = False):
        self._equity = equity
        self._raise_on_account = raise_on_account

    def connect(self) -> bool:
        return True

    def disconnect(self):
        return None

    def get_account_info(self) -> AccountInfo:
        if self._raise_on_account:
            raise RuntimeError("account unavailable")
        return AccountInfo(
            balance=self._equity,
            equity=self._equity,
            unrealised_pnl=0.0,
            open_positions=0,
            currency="GBP",
        )

    def get_positions(self) -> list[Position]:
        return []

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        return None

    def place_long(self, ticker: str, stake_per_point: float, strategy: str):
        raise NotImplementedError

    def place_short(self, ticker: str, stake_per_point: float, strategy: str):
        raise NotImplementedError

    def close_position(self, ticker: str, strategy: str):
        raise NotImplementedError


class TestPortfolioManagerLiveEquity:
    def _init_db(self, tmp_path) -> str:
        db = tmp_path / "portfolio_equity.db"
        trade_db.init_db(str(db))
        return str(db)

    def test_uses_broker_equity_when_available(self, tmp_path):
        db = self._init_db(tmp_path)
        broker = EquityBroker(equity=55_000.0)
        assert resolve_portfolio_equity(broker, db_path=db) == 55_000.0

    def test_falls_back_to_ledger_live_equity_when_broker_unavailable(self, tmp_path):
        db = self._init_db(tmp_path)

        acct_id = ledger.register_broker_account(
            broker="paper",
            account_id="PAPER-2",
            account_type="PAPER",
            db_path=db,
        )
        ledger.sync_positions(
            broker_account_id=acct_id,
            positions=[
                {
                    "ticker": "QQQ",
                    "direction": "long",
                    "quantity": 1.0,
                    "avg_cost": 400.0,
                    "market_value": 400.0,
                    "unrealised_pnl": 0.0,
                    "strategy": "dm",
                    "sleeve": "core",
                }
            ],
            db_path=db,
        )
        ledger.sync_cash_balance(
            broker_account_id=acct_id,
            balance=9_000.0,
            buying_power=9_400.0,
            currency="GBP",
            db_path=db,
        )

        broker = EquityBroker(equity=0.0, raise_on_account=True)
        assert resolve_portfolio_equity(broker, db_path=db) == 9_400.0

    def test_falls_back_to_initial_capital_when_no_live_sources(self, tmp_path):
        db = self._init_db(tmp_path)
        broker = EquityBroker(equity=0.0, raise_on_account=True)
        assert resolve_portfolio_equity(broker, db_path=db) == float(config.PORTFOLIO["initial_capital"])
