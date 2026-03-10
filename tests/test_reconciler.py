"""Tests for execution/reconciler.py (D-003)."""

from __future__ import annotations

from typing import Optional

from broker.base import AccountInfo, BaseBroker, Position
from data import trade_db
from execution import ledger
from execution.reconciler import (
    _position_to_ledger_row,
    account_info_equity,
    compute_live_equity,
    sync_broker_snapshot,
)
from utils.datetime_utils import utc_now_naive


class StubBroker(BaseBroker):
    def __init__(self, account: AccountInfo, positions: list[Position], connect_ok: bool = True):
        self._account = account
        self._positions = positions
        self._connect_ok = connect_ok
        self.connected = False

    def connect(self) -> bool:
        self.connected = self._connect_ok
        return self.connected

    def disconnect(self):
        self.connected = False

    def get_account_info(self) -> AccountInfo:
        return self._account

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        for p in self._positions:
            if p.ticker == ticker and p.strategy == strategy:
                return p
        return None

    def place_long(self, ticker: str, stake_per_point: float, strategy: str):
        raise NotImplementedError

    def place_short(self, ticker: str, stake_per_point: float, strategy: str):
        raise NotImplementedError

    def close_position(self, ticker: str, strategy: str):
        raise NotImplementedError


class TestReconciler:
    def _init_db(self, tmp_path) -> str:
        db = tmp_path / "reconciler.db"
        trade_db.init_db(str(db))
        return str(db)

    def test_sync_broker_snapshot_updates_ledger_and_equity(self, tmp_path):
        db = self._init_db(tmp_path)

        broker = StubBroker(
            account=AccountInfo(
                balance=10_000.0,
                equity=10_500.0,
                unrealised_pnl=500.0,
                open_positions=1,
                currency="GBP",
            ),
            positions=[
                Position(
                    ticker="SPY",
                    direction="long",
                    size=2.0,
                    entry_price=500.0,
                    entry_time=utc_now_naive(),
                    strategy="gtaa",
                    unrealised_pnl=100.0,
                    deal_id="DL-1",
                )
            ],
        )

        summary = sync_broker_snapshot(
            broker=broker,
            broker_name="paper",
            account_id="PAPER-1",
            account_type="PAPER",
            sleeve="core",
            db_path=db,
        )

        assert summary.positions_synced == 1
        assert summary.cash_balance == 10_000.0
        assert summary.net_liquidation == 10_500.0

        accounts = ledger.get_broker_accounts(db_path=db)
        assert len(accounts) == 1
        assert accounts[0]["broker"] == "paper"
        assert accounts[0]["account_id"] == "PAPER-1"

        positions = ledger.get_unified_positions(db_path=db)
        assert len(positions) == 1
        assert positions[0]["ticker"] == "SPY"
        assert positions[0]["sleeve"] == "core"

        cash = ledger.get_latest_cash_balances(db_path=db)
        assert len(cash) == 1
        assert float(cash[0]["balance"]) == 10_000.0

        # market_value includes unrealised PnL estimate: (2 * 500) + 100 = 1100
        assert compute_live_equity(default_equity=0.0, db_path=db) == 11_100.0

        fund_nav = ledger.get_nav_history(level="fund", level_id="fund", days=1, db_path=db)
        assert len(fund_nav) == 1
        assert float(fund_nav[0]["cash"]) == 10_000.0
        assert float(fund_nav[0]["positions_value"]) == 1_100.0
        assert float(fund_nav[0]["net_liquidation"]) == 11_100.0

    def test_compute_live_equity_falls_back_when_no_ledger_data(self, tmp_path):
        db = self._init_db(tmp_path)
        assert compute_live_equity(default_equity=12_345.0, db_path=db) == 12_345.0

    def test_compute_live_equity_closes_connection_on_query_error(self, monkeypatch):
        closed = {"value": False}

        class BrokenConn:
            def execute(self, *_args, **_kwargs):
                raise RuntimeError("boom")

            def close(self):
                closed["value"] = True

        monkeypatch.setattr("execution.reconciler.get_conn", lambda _db_path: BrokenConn())
        result = compute_live_equity(default_equity=7_654.0, db_path="ignored.db")
        assert result == 7_654.0
        assert closed["value"] is True

    def test_sync_broker_snapshot_raises_on_connect_failure(self, tmp_path):
        db = self._init_db(tmp_path)
        broker = StubBroker(
            account=AccountInfo(0.0, 0.0, 0.0, 0),
            positions=[],
            connect_ok=False,
        )

        try:
            sync_broker_snapshot(
                broker=broker,
                broker_name="paper",
                account_id="X",
                account_type="PAPER",
                db_path=db,
            )
            raised = False
        except RuntimeError:
            raised = True

        assert raised is True

    def test_sync_broker_snapshot_reuses_connected_broker(self, tmp_path):
        db = self._init_db(tmp_path)

        class ConnectedStubBroker(StubBroker):
            def __init__(self, account, positions):
                super().__init__(account=account, positions=positions, connect_ok=True)
                self.connected = True
                self.connect_calls = 0

            def connect(self) -> bool:
                self.connect_calls += 1
                self.connected = True
                return True

            def is_connected(self) -> bool:
                return self.connected

        broker = ConnectedStubBroker(
            account=AccountInfo(
                balance=10_000.0,
                equity=10_000.0,
                unrealised_pnl=0.0,
                open_positions=0,
                currency="GBP",
            ),
            positions=[],
        )

        summary = sync_broker_snapshot(
            broker=broker,
            broker_name="paper",
            account_id="PAPER-1",
            account_type="PAPER",
            sleeve="core",
            db_path=db,
        )

        assert summary.positions_synced == 0
        assert broker.connect_calls == 0

    def test_account_info_equity_helper(self):
        assert account_info_equity(None, fallback=1000.0) == 1000.0
        assert account_info_equity(AccountInfo(0.0, 0.0, 0.0, 0), fallback=1000.0) == 1000.0
        assert account_info_equity(AccountInfo(0.0, 5000.0, 0.0, 0), fallback=1000.0) == 5000.0

    def test_position_market_value_estimate_uses_unrealised_pnl(self):
        long_pos = Position(
            ticker="SPY",
            direction="long",
            size=2.0,
            entry_price=500.0,
            entry_time=utc_now_naive(),
            strategy="gtaa",
            unrealised_pnl=100.0,
            deal_id="L-1",
        )
        short_pos = Position(
            ticker="QQQ",
            direction="short",
            size=2.0,
            entry_price=500.0,
            entry_time=utc_now_naive(),
            strategy="dm",
            unrealised_pnl=100.0,
            deal_id="S-1",
        )

        long_row = _position_to_ledger_row(long_pos, sleeve="core", currency="GBP")
        short_row = _position_to_ledger_row(short_pos, sleeve="core", currency="GBP")

        assert long_row["market_value"] == 1_100.0
        assert short_row["market_value"] == 900.0
