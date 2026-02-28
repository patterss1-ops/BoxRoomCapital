"""
Tests for execution/ledger.py — Multi-broker ledger.

Covers: broker account registration, position sync, cash balance sync,
NAV snapshots, reconciliation, and unified views.
"""
import json
import os
import tempfile
import pytest

from data.trade_db import init_db, get_conn
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
def db_path():
    """Create a fresh temp DB for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    yield path
    os.unlink(path)


# ─── Broker account registration ────────────────────────────────────────────


class TestBrokerAccountRegistration:
    """Test registering and querying broker accounts."""

    def test_register_new_account(self, db_path):
        internal_id = register_broker_account(
            broker="ibkr",
            account_id="DU12345",
            account_type="PAPER",
            currency="USD",
            label="IBKR Paper",
            db_path=db_path,
        )
        assert internal_id
        assert len(internal_id) == 36  # UUID length

    def test_register_returns_consistent_id_on_conflict(self, db_path):
        """ON CONFLICT should update fields but return same internal ID."""
        id1 = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        id2 = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="GIA",
            label="Updated label", db_path=db_path,
        )
        assert id1 == id2

    def test_register_different_accounts_get_different_ids(self, db_path):
        id1 = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        id2 = register_broker_account(
            broker="ig", account_id="IG-001", account_type="SPREADBET",
            db_path=db_path,
        )
        assert id1 != id2

    def test_get_broker_accounts_all(self, db_path):
        register_broker_account(broker="ibkr", account_id="DU12345",
                                account_type="PAPER", db_path=db_path)
        register_broker_account(broker="ig", account_id="IG-001",
                                account_type="SPREADBET", db_path=db_path)
        accounts = get_broker_accounts(db_path=db_path)
        assert len(accounts) == 2

    def test_get_broker_accounts_filter_by_broker(self, db_path):
        register_broker_account(broker="ibkr", account_id="DU12345",
                                account_type="PAPER", db_path=db_path)
        register_broker_account(broker="ig", account_id="IG-001",
                                account_type="SPREADBET", db_path=db_path)
        ibkr_accounts = get_broker_accounts(broker="ibkr", db_path=db_path)
        assert len(ibkr_accounts) == 1
        assert ibkr_accounts[0]["broker"] == "ibkr"

    def test_inactive_accounts_excluded_by_default(self, db_path):
        acct_id = register_broker_account(
            broker="ibkr", account_id="OLD-ACCT", account_type="GIA",
            db_path=db_path,
        )
        # Deactivate
        conn = get_conn(db_path)
        conn.execute("UPDATE broker_accounts SET is_active=0 WHERE id=?", (acct_id,))
        conn.commit()
        conn.close()

        active = get_broker_accounts(db_path=db_path)
        assert len(active) == 0

        all_accts = get_broker_accounts(active_only=False, db_path=db_path)
        assert len(all_accts) == 1

    def test_account_fields_persisted(self, db_path):
        register_broker_account(
            broker="ibkr", account_id="DU99999", account_type="ISA",
            currency="GBP", label="My ISA", db_path=db_path,
        )
        accts = get_broker_accounts(db_path=db_path)
        assert len(accts) == 1
        acct = accts[0]
        assert acct["broker"] == "ibkr"
        assert acct["account_id"] == "DU99999"
        assert acct["account_type"] == "ISA"
        assert acct["currency"] == "GBP"
        assert acct["label"] == "My ISA"
        assert acct["is_active"] == 1


# ─── Position sync ──────────────────────────────────────────────────────────


class TestPositionSync:
    """Test syncing positions from broker into ledger."""

    def _setup_account(self, db_path):
        return register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )

    def test_sync_inserts_new_positions(self, db_path):
        acct_id = self._setup_account(db_path)
        positions = [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
        ]
        result = sync_positions(acct_id, positions, db_path=db_path)
        assert result["synced"] == 2
        assert result["inserted"] == 2
        assert result["updated"] == 0
        assert result["removed"] == 0

    def test_sync_updates_existing_positions(self, db_path):
        acct_id = self._setup_account(db_path)
        # First sync
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
        ], db_path=db_path)

        # Second sync with updated quantity
        result = sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 200, "avg_cost": 455.0},
        ], db_path=db_path)
        assert result["updated"] == 1
        assert result["inserted"] == 0

    def test_sync_removes_stale_positions(self, db_path):
        acct_id = self._setup_account(db_path)
        # Sync 2 positions
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
        ], db_path=db_path)

        # Sync with only SPY — QQQ should be removed
        result = sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
        ], db_path=db_path)
        assert result["removed"] == 1
        assert result["updated"] == 1

    def test_sync_empty_positions_removes_all(self, db_path):
        acct_id = self._setup_account(db_path)
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
        ], db_path=db_path)

        result = sync_positions(acct_id, [], db_path=db_path)
        assert result["synced"] == 0
        assert result["removed"] == 1

    def test_sync_preserves_optional_fields(self, db_path):
        acct_id = self._setup_account(db_path)
        sync_positions(acct_id, [
            {
                "ticker": "VUSA",
                "direction": "long",
                "quantity": 500,
                "avg_cost": 55.0,
                "market_value": 28000.0,
                "unrealised_pnl": 500.0,
                "currency": "GBP",
                "strategy": "dual_momentum",
                "sleeve": "sleeve_6",
                "con_id": "12345",
            },
        ], db_path=db_path)

        positions = get_unified_positions(db_path=db_path)
        assert len(positions) == 1
        p = positions[0]
        assert p["market_value"] == 28000.0
        assert p["unrealised_pnl"] == 500.0
        assert p["currency"] == "GBP"
        assert p["strategy"] == "dual_momentum"
        assert p["sleeve"] == "sleeve_6"
        assert p["con_id"] == "12345"

    def test_sync_same_ticker_different_directions(self, db_path):
        """Long and short positions in same ticker should coexist."""
        acct_id = self._setup_account(db_path)
        positions = [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "SPY", "direction": "short", "quantity": 50, "avg_cost": 460.0},
        ]
        result = sync_positions(acct_id, positions, db_path=db_path)
        assert result["synced"] == 2
        assert result["inserted"] == 2

        unified = get_unified_positions(db_path=db_path)
        assert len(unified) == 2


# ─── Unified positions ──────────────────────────────────────────────────────


class TestUnifiedPositions:
    """Test cross-broker unified position views."""

    def _setup_multi_broker(self, db_path):
        ibkr_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        ig_id = register_broker_account(
            broker="ig", account_id="IG-001", account_type="SPREADBET",
            db_path=db_path,
        )
        sync_positions(ibkr_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0,
             "sleeve": "sleeve_2"},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0,
             "sleeve": "sleeve_2"},
        ], db_path=db_path)
        sync_positions(ig_id, [
            {"ticker": "FTSE100", "direction": "long", "quantity": 2, "avg_cost": 7500.0,
             "sleeve": "sleeve_1"},
            {"ticker": "DAX40", "direction": "short", "quantity": 1, "avg_cost": 18000.0,
             "sleeve": "sleeve_1"},
        ], db_path=db_path)
        return ibkr_id, ig_id

    def test_unified_all_brokers(self, db_path):
        self._setup_multi_broker(db_path)
        positions = get_unified_positions(db_path=db_path)
        assert len(positions) == 4

    def test_unified_filter_by_broker(self, db_path):
        self._setup_multi_broker(db_path)
        ibkr_pos = get_unified_positions(broker="ibkr", db_path=db_path)
        assert len(ibkr_pos) == 2
        assert all(p["broker"] == "ibkr" for p in ibkr_pos)

        ig_pos = get_unified_positions(broker="ig", db_path=db_path)
        assert len(ig_pos) == 2
        assert all(p["broker"] == "ig" for p in ig_pos)

    def test_unified_filter_by_sleeve(self, db_path):
        self._setup_multi_broker(db_path)
        s1 = get_unified_positions(sleeve="sleeve_1", db_path=db_path)
        assert len(s1) == 2
        s2 = get_unified_positions(sleeve="sleeve_2", db_path=db_path)
        assert len(s2) == 2

    def test_unified_includes_account_metadata(self, db_path):
        self._setup_multi_broker(db_path)
        positions = get_unified_positions(broker="ibkr", db_path=db_path)
        p = positions[0]
        assert "account_id" in p
        assert "account_type" in p
        assert p["account_id"] == "DU12345"
        assert p["account_type"] == "PAPER"


# ─── Cash balance sync ───────────────────────────────────────────────────────


class TestCashBalance:
    """Test cash balance snapshot sync and retrieval."""

    def _setup_account(self, db_path):
        return register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )

    def test_sync_cash_balance(self, db_path):
        acct_id = self._setup_account(db_path)
        sync_cash_balance(acct_id, balance=50000.0, buying_power=45000.0,
                          currency="USD", db_path=db_path)

        balances = get_latest_cash_balances(db_path=db_path)
        assert len(balances) == 1
        assert balances[0]["balance"] == 50000.0
        assert balances[0]["buying_power"] == 45000.0

    def test_multiple_snapshots_returns_latest(self, db_path):
        acct_id = self._setup_account(db_path)
        sync_cash_balance(acct_id, balance=50000.0, db_path=db_path)
        sync_cash_balance(acct_id, balance=51000.0, db_path=db_path)
        sync_cash_balance(acct_id, balance=52000.0, db_path=db_path)

        balances = get_latest_cash_balances(db_path=db_path)
        assert len(balances) == 1
        assert balances[0]["balance"] == 52000.0

    def test_multi_broker_cash_balances(self, db_path):
        ibkr_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        ig_id = register_broker_account(
            broker="ig", account_id="IG-001", account_type="SPREADBET",
            db_path=db_path,
        )
        sync_cash_balance(ibkr_id, balance=50000.0, currency="USD", db_path=db_path)
        sync_cash_balance(ig_id, balance=10000.0, currency="GBP", db_path=db_path)

        balances = get_latest_cash_balances(db_path=db_path)
        assert len(balances) == 2
        brokers = {b["broker"] for b in balances}
        assert brokers == {"ibkr", "ig"}

    def test_cash_balance_includes_account_metadata(self, db_path):
        acct_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            label="Test Paper", db_path=db_path,
        )
        sync_cash_balance(acct_id, balance=50000.0, db_path=db_path)

        balances = get_latest_cash_balances(db_path=db_path)
        b = balances[0]
        assert b["account_label"] == "Test Paper"
        assert b["account_type"] == "PAPER"


# ─── NAV snapshots ──────────────────────────────────────────────────────────


class TestNAVSnapshots:
    """Test NAV snapshot persistence and retrieval."""

    def test_save_and_retrieve_fund_nav(self, db_path):
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=100000.0, cash=20000.0,
            positions_value=80000.0, unrealised_pnl=5000.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )
        history = get_nav_history(level="fund", level_id="fund", db_path=db_path)
        assert len(history) == 1
        nav = history[0]
        assert nav["net_liquidation"] == 100000.0
        assert nav["cash"] == 20000.0
        assert nav["positions_value"] == 80000.0
        assert nav["unrealised_pnl"] == 5000.0

    def test_save_sleeve_level_nav(self, db_path):
        save_nav_snapshot(
            level="sleeve", level_id="sleeve_1",
            net_liquidation=15000.0, cash=2000.0,
            positions_value=13000.0,
            broker="ig", account_type="SPREADBET",
            snapshot_date="2026-02-28",
            db_path=db_path,
        )
        history = get_nav_history(level="sleeve", level_id="sleeve_1", db_path=db_path)
        assert len(history) == 1
        assert history[0]["broker"] == "ig"

    def test_save_account_level_nav(self, db_path):
        acct_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        save_nav_snapshot(
            level="account", level_id=acct_id,
            net_liquidation=50000.0, cash=10000.0,
            positions_value=40000.0,
            broker="ibkr", account_type="PAPER",
            snapshot_date="2026-02-28",
            db_path=db_path,
        )
        history = get_nav_history(level="account", level_id=acct_id, db_path=db_path)
        assert len(history) == 1

    def test_nav_upsert_same_date(self, db_path):
        """Same date + level + level_id should upsert."""
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=100000.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=101000.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )
        history = get_nav_history(level="fund", level_id="fund", db_path=db_path)
        assert len(history) == 1
        assert history[0]["net_liquidation"] == 101000.0

    def test_nav_history_multiple_dates(self, db_path):
        for i in range(5):
            save_nav_snapshot(
                level="fund", level_id="fund",
                net_liquidation=100000.0 + i * 1000,
                snapshot_date=f"2026-02-{23 + i:02d}",
                db_path=db_path,
            )
        history = get_nav_history(level="fund", level_id="fund", days=3, db_path=db_path)
        assert len(history) == 3  # Limited by days param

    def test_nav_history_returns_most_recent_first(self, db_path):
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=100000.0,
            snapshot_date="2026-02-25",
            db_path=db_path,
        )
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=101000.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )
        history = get_nav_history(level="fund", level_id="fund", db_path=db_path)
        assert history[0]["snapshot_date"] == "2026-02-28"
        assert history[1]["snapshot_date"] == "2026-02-25"

    def test_nav_with_realised_pnl(self, db_path):
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=100000.0,
            realised_pnl=2500.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )
        history = get_nav_history(level="fund", level_id="fund", db_path=db_path)
        assert history[0]["realised_pnl"] == 2500.0


# ─── Reconciliation ─────────────────────────────────────────────────────────


class TestReconciliation:
    """Test position reconciliation between broker and ledger."""

    def _setup_with_positions(self, db_path):
        acct_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
            {"ticker": "IWM", "direction": "short", "quantity": 25, "avg_cost": 200.0},
        ], db_path=db_path)
        return acct_id

    def test_reconcile_clean(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        broker_positions = [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
            {"ticker": "IWM", "direction": "short", "quantity": 25, "avg_cost": 200.0},
        ]
        result = reconcile_positions(acct_id, broker_positions, db_path=db_path)
        assert result["status"] == "clean"
        assert result["mismatches"] == 0
        assert result["positions_checked"] == 3

    def test_reconcile_quantity_mismatch(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        broker_positions = [
            {"ticker": "SPY", "direction": "long", "quantity": 150, "avg_cost": 450.0},  # Qty differs
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
            {"ticker": "IWM", "direction": "short", "quantity": 25, "avg_cost": 200.0},
        ]
        result = reconcile_positions(acct_id, broker_positions, db_path=db_path)
        assert result["status"] == "mismatch"
        assert result["mismatches"] == 1
        assert result["details"][0]["type"] == "quantity_mismatch"
        assert result["details"][0]["ticker"] == "SPY"
        assert result["details"][0]["broker_qty"] == 150
        assert result["details"][0]["ledger_qty"] == 100

    def test_reconcile_missing_in_ledger(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        broker_positions = [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
            {"ticker": "IWM", "direction": "short", "quantity": 25, "avg_cost": 200.0},
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 180.0},  # New at broker
        ]
        result = reconcile_positions(acct_id, broker_positions, db_path=db_path)
        assert result["status"] == "mismatch"
        assert result["mismatches"] == 1
        missing = [d for d in result["details"] if d["type"] == "missing_in_ledger"]
        assert len(missing) == 1
        assert missing[0]["ticker"] == "AAPL"

    def test_reconcile_phantom_in_ledger(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        # Broker only has 2 of 3 positions — IWM is phantom
        broker_positions = [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
        ]
        result = reconcile_positions(acct_id, broker_positions, db_path=db_path)
        assert result["status"] == "mismatch"
        phantoms = [d for d in result["details"] if d["type"] == "phantom_in_ledger"]
        assert len(phantoms) == 1
        assert phantoms[0]["ticker"] == "IWM"

    def test_reconcile_multiple_mismatches(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        broker_positions = [
            {"ticker": "SPY", "direction": "long", "quantity": 200, "avg_cost": 450.0},  # Qty diff
            {"ticker": "AAPL", "direction": "long", "quantity": 10, "avg_cost": 180.0},  # Missing in ledger
            # QQQ and IWM missing from broker → 2 phantoms
        ]
        result = reconcile_positions(acct_id, broker_positions, db_path=db_path)
        assert result["status"] == "mismatch"
        assert result["mismatches"] == 4  # 1 qty + 1 missing + 2 phantom

    def test_reconcile_report_persisted(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
            {"ticker": "IWM", "direction": "short", "quantity": 25, "avg_cost": 200.0},
        ], db_path=db_path)

        reports = get_reconciliation_reports(broker_account_id=acct_id, db_path=db_path)
        assert len(reports) == 1
        assert reports[0]["id"] == result["report_id"]
        assert reports[0]["status"] == "clean"

    def test_reconcile_reports_contain_details_json(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 999, "avg_cost": 450.0},
        ], db_path=db_path)

        reports = get_reconciliation_reports(broker_account_id=acct_id, db_path=db_path)
        assert len(reports) == 1
        details = reports[0]["details"]
        assert isinstance(details, list)
        assert len(details) > 0

    def test_reconcile_empty_broker_all_phantom(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        result = reconcile_positions(acct_id, [], db_path=db_path)
        assert result["status"] == "mismatch"
        assert result["mismatches"] == 3
        assert all(d["type"] == "phantom_in_ledger" for d in result["details"])

    def test_get_reports_limit(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        # Run 5 reconciliations
        for _ in range(5):
            reconcile_positions(acct_id, [
                {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
                {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
                {"ticker": "IWM", "direction": "short", "quantity": 25, "avg_cost": 200.0},
            ], db_path=db_path)

        reports = get_reconciliation_reports(limit=3, db_path=db_path)
        assert len(reports) == 3

    def test_get_reports_all_accounts(self, db_path):
        acct1 = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        acct2 = register_broker_account(
            broker="ig", account_id="IG-001", account_type="SPREADBET",
            db_path=db_path,
        )
        sync_positions(acct1, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
        ], db_path=db_path)
        sync_positions(acct2, [
            {"ticker": "FTSE", "direction": "long", "quantity": 2, "avg_cost": 7500.0},
        ], db_path=db_path)

        reconcile_positions(acct1, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
        ], db_path=db_path)
        reconcile_positions(acct2, [
            {"ticker": "FTSE", "direction": "long", "quantity": 2, "avg_cost": 7500.0},
        ], db_path=db_path)

        all_reports = get_reconciliation_reports(db_path=db_path)
        assert len(all_reports) == 2

    def test_reconcile_suggestion_text(self, db_path):
        acct_id = self._setup_with_positions(db_path)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 200, "avg_cost": 450.0},
        ], db_path=db_path)
        qty_mismatch = [d for d in result["details"] if d["type"] == "quantity_mismatch"]
        assert len(qty_mismatch) == 1
        assert "Update" in qty_mismatch[0]["suggestion"]
        assert "100" in qty_mismatch[0]["suggestion"]
        assert "200" in qty_mismatch[0]["suggestion"]


# ─── Integration: full lifecycle ─────────────────────────────────────────────


class TestLedgerIntegration:
    """End-to-end integration tests combining multiple ledger operations."""

    def test_full_multi_broker_lifecycle(self, db_path):
        """Register accounts → sync positions → sync cash → NAV → reconcile."""
        # 1. Register accounts
        ibkr_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            currency="USD", label="IBKR Paper", db_path=db_path,
        )
        ig_id = register_broker_account(
            broker="ig", account_id="IG-001", account_type="SPREADBET",
            currency="GBP", label="IG Spreadbet", db_path=db_path,
        )

        # 2. Sync positions
        ibkr_sync = sync_positions(ibkr_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0,
             "market_value": 46000.0, "sleeve": "sleeve_2"},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0,
             "market_value": 19500.0, "sleeve": "sleeve_2"},
        ], db_path=db_path)
        assert ibkr_sync["inserted"] == 2

        ig_sync = sync_positions(ig_id, [
            {"ticker": "FTSE100", "direction": "long", "quantity": 2, "avg_cost": 7500.0,
             "market_value": 15200.0, "sleeve": "sleeve_1"},
        ], db_path=db_path)
        assert ig_sync["inserted"] == 1

        # 3. Unified positions
        all_positions = get_unified_positions(db_path=db_path)
        assert len(all_positions) == 3

        # 4. Cash balances
        sync_cash_balance(ibkr_id, balance=30000.0, buying_power=28000.0,
                          currency="USD", db_path=db_path)
        sync_cash_balance(ig_id, balance=5000.0, buying_power=4000.0,
                          currency="GBP", db_path=db_path)

        balances = get_latest_cash_balances(db_path=db_path)
        assert len(balances) == 2

        # 5. NAV snapshots
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=115700.0, cash=35000.0,
            positions_value=80700.0, unrealised_pnl=1200.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )
        save_nav_snapshot(
            level="account", level_id=ibkr_id,
            net_liquidation=95500.0, cash=30000.0,
            positions_value=65500.0,
            broker="ibkr", account_type="PAPER",
            snapshot_date="2026-02-28",
            db_path=db_path,
        )

        fund_nav = get_nav_history(level="fund", level_id="fund", db_path=db_path)
        assert len(fund_nav) == 1
        assert fund_nav[0]["net_liquidation"] == 115700.0

        # 6. Reconciliation
        ibkr_recon = reconcile_positions(ibkr_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
            {"ticker": "QQQ", "direction": "long", "quantity": 50, "avg_cost": 380.0},
        ], db_path=db_path)
        assert ibkr_recon["status"] == "clean"

        ig_recon = reconcile_positions(ig_id, [
            {"ticker": "FTSE100", "direction": "long", "quantity": 2, "avg_cost": 7500.0},
        ], db_path=db_path)
        assert ig_recon["status"] == "clean"

        # All reports
        reports = get_reconciliation_reports(db_path=db_path)
        assert len(reports) == 2
        assert all(r["status"] == "clean" for r in reports)

    def test_position_update_then_reconcile(self, db_path):
        """Position changes should be caught by reconciliation."""
        acct_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        # Initial sync
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100, "avg_cost": 450.0},
        ], db_path=db_path)

        # Broker reports different quantity (trade happened outside bot)
        result = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 150, "avg_cost": 452.0},
        ], db_path=db_path)
        assert result["status"] == "mismatch"
        assert result["mismatches"] == 1

        # Re-sync to fix
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 150, "avg_cost": 452.0},
        ], db_path=db_path)

        # Reconcile again — should be clean
        result2 = reconcile_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 150, "avg_cost": 452.0},
        ], db_path=db_path)
        assert result2["status"] == "clean"
