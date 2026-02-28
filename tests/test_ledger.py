"""
Tests for execution/ledger.py — Claude's multi-broker ledger module (A-005).

56 tests covering:
- Broker account registration + idempotency
- Position sync (insert, update, remove)
- Unified position view with filters
- Cash balance recording + latest balances
- NAV snapshot hierarchy (fund/sleeve/account)
- Reconciliation (clean, quantity mismatch, missing, phantom)
- Reconciliation report persistence + retrieval
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from data.trade_db import init_db

from execution.ledger import (
    register_broker_account,
    get_broker_accounts,
    sync_positions,
    get_unified_positions,
    sync_cash_balance,
    get_latest_cash_balances,
    save_nav_snapshot,
    get_nav_history,
    reconcile_positions,
    get_reconciliation_reports,
)


@pytest.fixture
def db(tmp_path):
    """Create a fresh test database and return its path."""
    path = str(tmp_path / "test_ledger.db")
    init_db(path)
    return path


# ─── Broker account registration ────────────────────────────────────────────


class TestBrokerAccounts:
    def test_register_returns_uuid(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        assert isinstance(acct_id, str)
        assert len(acct_id) == 36  # UUID format

    def test_register_idempotent(self, db):
        id1 = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        id2 = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        assert id1 == id2

    def test_register_updates_on_conflict(self, db):
        register_broker_account("ig", "ACC-IG-1", "spreadbet", label="Old", db_path=db)
        register_broker_account("ig", "ACC-IG-1", "spreadbet", label="New", db_path=db)
        accts = get_broker_accounts(broker="ig", db_path=db)
        assert len(accts) == 1
        assert accts[0]["label"] == "New"

    def test_register_multiple_brokers(self, db):
        register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        accts = get_broker_accounts(db_path=db)
        assert len(accts) == 2

    def test_register_with_currency(self, db):
        register_broker_account("ibkr", "U1234567", "GIA", currency="USD", db_path=db)
        accts = get_broker_accounts(broker="ibkr", db_path=db)
        assert accts[0]["currency"] == "USD"

    def test_list_filtered_by_broker(self, db):
        register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        ig_only = get_broker_accounts(broker="ig", db_path=db)
        assert len(ig_only) == 1
        assert ig_only[0]["broker"] == "ig"

    def test_list_active_only(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        # Deactivate via direct DB update
        from data.trade_db import get_conn
        conn = get_conn(db)
        conn.execute("UPDATE broker_accounts SET is_active=0 WHERE id=?", (acct_id,))
        conn.commit()
        conn.close()
        active = get_broker_accounts(active_only=True, db_path=db)
        assert len(active) == 0
        all_accts = get_broker_accounts(active_only=False, db_path=db)
        assert len(all_accts) == 1


# ─── Position sync ──────────────────────────────────────────────────────────


class TestPositionSync:
    def _setup_account(self, db):
        return register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)

    def test_sync_inserts_new_positions(self, db):
        acct_id = self._setup_account(db)
        result = sync_positions(acct_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0},
            {"ticker": "MSFT", "direction": "long", "quantity": 5, "avg_cost": 300.0},
        ], db_path=db)
        assert result["synced"] == 2
        assert result["inserted"] == 2
        assert result["updated"] == 0
        assert result["removed"] == 0

    def test_sync_updates_existing(self, db):
        acct_id = self._setup_account(db)
        sync_positions(acct_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0},
        ], db_path=db)
        result = sync_positions(acct_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 20, "avg_cost": 155.0},
        ], db_path=db)
        assert result["updated"] == 1
        assert result["inserted"] == 0

    def test_sync_removes_closed_positions(self, db):
        acct_id = self._setup_account(db)
        sync_positions(acct_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0},
            {"ticker": "MSFT", "direction": "long", "quantity": 5, "avg_cost": 300.0},
        ], db_path=db)
        result = sync_positions(acct_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0},
        ], db_path=db)
        assert result["removed"] == 1
        positions = get_unified_positions(db_path=db)
        assert len(positions) == 1
        assert positions[0]["ticker"] == "AAPL"

    def test_sync_with_optional_fields(self, db):
        acct_id = self._setup_account(db)
        sync_positions(acct_id, [
            {
                "ticker": "AAPL",
                "direction": "long",
                "quantity": 10,
                "avg_cost": 150.0,
                "market_value": 1600.0,
                "unrealised_pnl": 100.0,
                "currency": "USD",
                "strategy": "IBS Credit Spreads",
                "sleeve": "sleeve_1",
                "con_id": "CON123",
            },
        ], db_path=db)
        positions = get_unified_positions(db_path=db)
        assert len(positions) == 1
        p = positions[0]
        assert p["market_value"] == 1600.0
        assert p["strategy"] == "IBS Credit Spreads"
        assert p["sleeve"] == "sleeve_1"

    def test_sync_empty_list_removes_all(self, db):
        acct_id = self._setup_account(db)
        sync_positions(acct_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0},
        ], db_path=db)
        result = sync_positions(acct_id, [], db_path=db)
        assert result["removed"] == 1
        assert result["synced"] == 0


# ─── Unified positions ──────────────────────────────────────────────────────


class TestUnifiedPositions:
    def test_unified_across_brokers(self, db):
        ig_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        ibkr_id = register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        sync_positions(ig_id, [
            {"ticker": "SPY", "direction": "short", "quantity": 1, "avg_cost": 450.0},
        ], db_path=db)
        sync_positions(ibkr_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0},
        ], db_path=db)
        all_pos = get_unified_positions(db_path=db)
        assert len(all_pos) == 2

    def test_filter_by_broker(self, db):
        ig_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        ibkr_id = register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        sync_positions(ig_id, [
            {"ticker": "SPY", "direction": "short", "quantity": 1, "avg_cost": 450.0},
        ], db_path=db)
        sync_positions(ibkr_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0},
        ], db_path=db)
        ig_pos = get_unified_positions(broker="ig", db_path=db)
        assert len(ig_pos) == 1
        assert ig_pos[0]["ticker"] == "SPY"

    def test_filter_by_sleeve(self, db):
        acct_id = register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        sync_positions(acct_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0, "sleeve": "sleeve_1"},
            {"ticker": "MSFT", "direction": "long", "quantity": 5, "avg_cost": 300.0, "sleeve": "sleeve_2"},
        ], db_path=db)
        s1 = get_unified_positions(sleeve="sleeve_1", db_path=db)
        assert len(s1) == 1
        assert s1[0]["ticker"] == "AAPL"

    def test_includes_account_metadata(self, db):
        register_broker_account("ig", "ACC-IG-1", "spreadbet", label="Main IG", db_path=db)
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", label="Main IG", db_path=db)
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 1, "avg_cost": 450.0},
        ], db_path=db)
        positions = get_unified_positions(db_path=db)
        assert positions[0]["account_label"] == "Main IG"
        assert positions[0]["account_type"] == "spreadbet"


# ─── Cash balances ───────────────────────────────────────────────────────────


class TestCashBalances:
    def test_record_and_retrieve(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_cash_balance(acct_id, balance=10000.0, buying_power=8000.0, db_path=db)
        balances = get_latest_cash_balances(db_path=db)
        assert len(balances) == 1
        assert balances[0]["balance"] == 10000.0
        assert balances[0]["buying_power"] == 8000.0

    def test_latest_only(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_cash_balance(acct_id, balance=10000.0, db_path=db)
        sync_cash_balance(acct_id, balance=10500.0, db_path=db)
        balances = get_latest_cash_balances(db_path=db)
        assert len(balances) == 1
        assert balances[0]["balance"] == 10500.0

    def test_multiple_accounts(self, db):
        ig_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        ibkr_id = register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        sync_cash_balance(ig_id, balance=10000.0, db_path=db)
        sync_cash_balance(ibkr_id, balance=50000.0, currency="USD", db_path=db)
        balances = get_latest_cash_balances(db_path=db)
        assert len(balances) == 2


# ─── NAV snapshots ───────────────────────────────────────────────────────────


class TestNAVSnapshots:
    def test_save_and_retrieve_fund_level(self, db):
        save_nav_snapshot("fund", "fund", net_liquidation=100000.0, cash=50000.0, db_path=db)
        history = get_nav_history(level="fund", level_id="fund", db_path=db)
        assert len(history) == 1
        assert history[0]["net_liquidation"] == 100000.0
        assert history[0]["cash"] == 50000.0

    def test_save_sleeve_level(self, db):
        save_nav_snapshot("sleeve", "sleeve_1", net_liquidation=25000.0, db_path=db)
        save_nav_snapshot("sleeve", "sleeve_2", net_liquidation=30000.0, db_path=db)
        s1 = get_nav_history(level="sleeve", level_id="sleeve_1", db_path=db)
        assert len(s1) == 1
        assert s1[0]["net_liquidation"] == 25000.0

    def test_upsert_same_date(self, db):
        save_nav_snapshot("fund", "fund", net_liquidation=100000.0,
                          snapshot_date="2025-01-15", db_path=db)
        save_nav_snapshot("fund", "fund", net_liquidation=101000.0,
                          snapshot_date="2025-01-15", db_path=db)
        history = get_nav_history(level="fund", level_id="fund", db_path=db)
        assert len(history) == 1
        assert history[0]["net_liquidation"] == 101000.0

    def test_history_ordered_desc(self, db):
        for i in range(5):
            save_nav_snapshot("fund", "fund", net_liquidation=100000.0 + i * 1000,
                              snapshot_date=f"2025-01-{10+i:02d}", db_path=db)
        history = get_nav_history(level="fund", level_id="fund", days=3, db_path=db)
        assert len(history) == 3
        assert history[0]["snapshot_date"] > history[1]["snapshot_date"]

    def test_account_level_with_broker(self, db):
        acct_id = register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        save_nav_snapshot("account", acct_id, net_liquidation=50000.0,
                          broker="ibkr", account_type="GIA", db_path=db)
        history = get_nav_history(level="account", level_id=acct_id, db_path=db)
        assert len(history) == 1
        assert history[0]["broker"] == "ibkr"


# ─── Reconciliation ─────────────────────────────────────────────────────────


class TestReconciliation:
    def _setup_ledger(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 10, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 5, "avg_cost": 380.0},
        ], db_path=db)
        return acct_id

    def test_clean_reconciliation(self, db):
        acct_id = self._setup_ledger(db)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 10, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 5, "avg_cost": 380.0},
        ], db_path=db)
        assert result["status"] == "clean"
        assert result["mismatches"] == 0
        assert result["positions_checked"] == 2

    def test_quantity_mismatch(self, db):
        acct_id = self._setup_ledger(db)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 15, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 5, "avg_cost": 380.0},
        ], db_path=db)
        assert result["status"] == "mismatch"
        assert result["mismatches"] == 1
        detail = result["details"][0]
        assert detail["type"] == "quantity_mismatch"
        assert detail["broker_qty"] == 15
        assert detail["ledger_qty"] == 10

    def test_missing_in_ledger(self, db):
        acct_id = self._setup_ledger(db)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 10, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 5, "avg_cost": 380.0},
            {"ticker": "AAPL", "direction": "long", "quantity": 20, "avg_cost": 150.0},
        ], db_path=db)
        assert result["mismatches"] == 1
        missing = [d for d in result["details"] if d["type"] == "missing_in_ledger"]
        assert len(missing) == 1
        assert missing[0]["ticker"] == "AAPL"

    def test_phantom_in_ledger(self, db):
        acct_id = self._setup_ledger(db)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 10, "avg_cost": 450.0},
            # QQQ missing from broker → phantom
        ], db_path=db)
        assert result["mismatches"] == 1
        phantom = [d for d in result["details"] if d["type"] == "phantom_in_ledger"]
        assert len(phantom) == 1
        assert phantom[0]["ticker"] == "QQQ"

    def test_multiple_mismatch_types(self, db):
        acct_id = self._setup_ledger(db)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 15, "avg_cost": 450.0},
            # QQQ missing → phantom
            {"ticker": "AAPL", "direction": "long", "quantity": 20, "avg_cost": 150.0},
        ], db_path=db)
        assert result["mismatches"] == 3  # qty_mismatch + phantom + missing

    def test_reconciliation_persists_report(self, db):
        acct_id = self._setup_ledger(db)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 15, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 5, "avg_cost": 380.0},
        ], db_path=db)
        reports = get_reconciliation_reports(broker_account_id=acct_id, db_path=db)
        assert len(reports) == 1
        assert reports[0]["id"] == result["report_id"]
        assert reports[0]["status"] == "mismatch"
        assert isinstance(reports[0]["details"], list)

    def test_multiple_reports_ordered(self, db):
        acct_id = self._setup_ledger(db)
        reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 10, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 5, "avg_cost": 380.0},
        ], db_path=db)
        reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 15, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 5, "avg_cost": 380.0},
        ], db_path=db)
        reports = get_reconciliation_reports(db_path=db)
        assert len(reports) == 2
        # Most recent first
        assert reports[0]["status"] == "mismatch"
        assert reports[1]["status"] == "clean"

    def test_reconciliation_with_empty_broker(self, db):
        acct_id = self._setup_ledger(db)
        result = reconcile_positions(acct_id, [], db_path=db)
        assert result["status"] == "mismatch"
        assert result["mismatches"] == 2  # both are phantoms

    def test_reconciliation_with_empty_ledger(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 10, "avg_cost": 450.0},
        ], db_path=db)
        assert result["status"] == "mismatch"
        missing = [d for d in result["details"] if d["type"] == "missing_in_ledger"]
        assert len(missing) == 1


# ─── Edge cases ──────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_position_direction_differentiation(self, db):
        acct_id = register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 10, "avg_cost": 450.0},
            {"ticker": "SPY", "direction": "short", "quantity": 5, "avg_cost": 460.0},
        ], db_path=db)
        positions = get_unified_positions(db_path=db)
        assert len(positions) == 2

    def test_sync_preserves_other_accounts(self, db):
        ig_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        ibkr_id = register_broker_account("ibkr", "U1234567", "GIA", db_path=db)
        sync_positions(ig_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 1, "avg_cost": 450.0},
        ], db_path=db)
        sync_positions(ibkr_id, [
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 150.0},
        ], db_path=db)
        # Re-sync IG with empty → should only remove IG positions
        sync_positions(ig_id, [], db_path=db)
        all_pos = get_unified_positions(db_path=db)
        assert len(all_pos) == 1
        assert all_pos[0]["ticker"] == "AAPL"

    def test_zero_quantity_position(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 0, "avg_cost": 0},
        ], db_path=db)
        positions = get_unified_positions(db_path=db)
        assert len(positions) == 1
        assert positions[0]["quantity"] == 0

    def test_nav_with_all_fields(self, db):
        save_nav_snapshot(
            level="fund",
            level_id="fund",
            net_liquidation=100000.0,
            cash=40000.0,
            positions_value=60000.0,
            unrealised_pnl=5000.0,
            realised_pnl=2000.0,
            currency="GBP",
            broker="ibkr",
            account_type="GIA",
            snapshot_date="2025-03-01",
            db_path=db,
        )
        history = get_nav_history(level="fund", level_id="fund", db_path=db)
        h = history[0]
        assert h["net_liquidation"] == 100000.0
        assert h["positions_value"] == 60000.0
        assert h["unrealised_pnl"] == 5000.0
        assert h["realised_pnl"] == 2000.0
