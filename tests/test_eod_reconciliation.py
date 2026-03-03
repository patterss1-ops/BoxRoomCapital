"""Tests for H-005 EOD reconciliation + P&L attribution."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from data.trade_db import DB_PATH, get_conn, init_db
from fund.eod_reconciliation import (
    EODReconciliationReport,
    PositionMismatch,
    SleevePnL,
    StrategyPnL,
    dispatch_eod_reconciliation,
    run_eod_reconciliation,
)


@pytest.fixture
def db(tmp_path):
    """Initialize a fresh test database."""
    db_path = str(tmp_path / "eod_test.db")
    init_db(db_path)
    return db_path


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Data class serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestDataClasses:
    def test_position_mismatch_to_dict(self):
        m = PositionMismatch(
            ticker="AAPL",
            direction="BUY",
            mismatch_type="quantity_mismatch",
            broker_qty=100.0,
            ledger_qty=95.0,
            delta=5.0,
        )
        d = m.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["mismatch_type"] == "quantity_mismatch"
        assert d["delta"] == 5.0

    def test_strategy_pnl_to_dict(self):
        s = StrategyPnL(
            strategy="mean_reversion",
            realised_pnl=150.50,
            trade_count=10,
            win_count=7,
            loss_count=3,
        )
        d = s.to_dict()
        assert d["strategy"] == "mean_reversion"
        assert d["realised_pnl"] == 150.50
        assert d["trade_count"] == 10

    def test_sleeve_pnl_to_dict(self):
        s = SleevePnL(
            sleeve="core",
            unrealised_pnl=200.0,
            positions_value=10000.0,
            position_count=5,
        )
        d = s.to_dict()
        assert d["sleeve"] == "core"
        assert d["position_count"] == 5

    def test_eod_report_to_dict(self):
        report = EODReconciliationReport(
            report_date="2026-03-03",
            status="clean",
            positions_checked=10,
            total_realised_pnl=500.0,
            total_unrealised_pnl=200.0,
        )
        d = report.to_dict()
        assert d["report_date"] == "2026-03-03"
        assert d["status"] == "clean"
        assert d["mismatches_found"] == 0
        assert d["total_realised_pnl"] == 500.0


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Core reconciliation logic
# ═══════════════════════════════════════════════════════════════════════════


class TestEODReconciliation:
    def test_clean_reconciliation_with_empty_db(self, db):
        """Empty DB should produce a clean report with no mismatches."""
        report = run_eod_reconciliation(report_date="2026-03-03", db_path=db)
        assert report.status == "clean"
        assert report.positions_checked == 0
        assert len(report.mismatches) == 0
        assert report.total_realised_pnl == 0.0

    def test_strategy_pnl_attribution(self, db):
        """Closed trades should be attributed to their strategies."""
        conn = get_conn(db)
        today = date.today().isoformat()
        # Insert closed trades for two strategies
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, pnl)
               VALUES (?, 'AAPL', 'momentum', 'BUY', 'CLOSE', 10, 150.0, 50.0)""",
            (today,),
        )
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, pnl)
               VALUES (?, 'MSFT', 'momentum', 'BUY', 'CLOSE', 5, 300.0, -20.0)""",
            (today,),
        )
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, pnl)
               VALUES (?, 'GOOG', 'mean_reversion', 'SELL', 'CLOSE', 8, 200.0, 75.0)""",
            (today,),
        )
        conn.commit()
        conn.close()

        report = run_eod_reconciliation(report_date=today, db_path=db)

        # Check strategy attribution
        strategy_map = {s.strategy: s for s in report.pnl_by_strategy}
        assert "momentum" in strategy_map
        assert "mean_reversion" in strategy_map
        assert strategy_map["momentum"].realised_pnl == 30.0  # 50 - 20
        assert strategy_map["momentum"].trade_count == 2
        assert strategy_map["momentum"].win_count == 1
        assert strategy_map["momentum"].loss_count == 1
        assert strategy_map["mean_reversion"].realised_pnl == 75.0
        assert report.total_realised_pnl == 105.0

    def test_sleeve_pnl_attribution(self, db):
        """Broker positions should be attributed by sleeve."""
        conn = get_conn(db)
        # Register a broker account
        conn.execute(
            """INSERT INTO broker_accounts (id, broker, account_id, account_type, currency, is_active, created_at, updated_at)
               VALUES ('acct1', 'ig', 'ABC123', 'SPREADBET', 'GBP', 1, '2026-01-01', '2026-01-01')"""
        )
        # Insert positions in different sleeves
        conn.execute(
            """INSERT INTO broker_positions
               (broker_account_id, ticker, direction, quantity, avg_cost, market_value, unrealised_pnl, currency, sleeve, last_synced_at)
               VALUES ('acct1', 'AAPL', 'BUY', 10, 150.0, 1600.0, 100.0, 'GBP', 'core', '2026-03-03')"""
        )
        conn.execute(
            """INSERT INTO broker_positions
               (broker_account_id, ticker, direction, quantity, avg_cost, market_value, unrealised_pnl, currency, sleeve, last_synced_at)
               VALUES ('acct1', 'MSFT', 'BUY', 5, 300.0, 1400.0, -100.0, 'GBP', 'growth', '2026-03-03')"""
        )
        conn.commit()
        conn.close()

        report = run_eod_reconciliation(report_date="2026-03-03", db_path=db)

        sleeve_map = {s.sleeve: s for s in report.pnl_by_sleeve}
        assert "core" in sleeve_map
        assert "growth" in sleeve_map
        assert sleeve_map["core"].unrealised_pnl == 100.0
        assert sleeve_map["core"].positions_value == 1600.0
        assert sleeve_map["growth"].unrealised_pnl == -100.0
        assert report.total_unrealised_pnl == 0.0  # 100 + (-100)

    def test_phantom_position_detected(self, db):
        """Zero-quantity positions should be flagged as mismatches."""
        conn = get_conn(db)
        conn.execute(
            """INSERT INTO broker_accounts (id, broker, account_id, account_type, currency, is_active, created_at, updated_at)
               VALUES ('acct1', 'ig', 'ABC123', 'SPREADBET', 'GBP', 1, '2026-01-01', '2026-01-01')"""
        )
        conn.execute(
            """INSERT INTO broker_positions
               (broker_account_id, ticker, direction, quantity, avg_cost, market_value, unrealised_pnl, currency, sleeve, last_synced_at)
               VALUES ('acct1', 'DEAD', 'BUY', 0, 0, 0, 0, 'GBP', 'core', '2026-03-03')"""
        )
        conn.commit()
        conn.close()

        report = run_eod_reconciliation(report_date="2026-03-03", db_path=db)

        assert report.status == "warning"
        assert len(report.mismatches) == 1
        assert report.mismatches[0].ticker == "DEAD"
        assert report.mismatches[0].mismatch_type == "phantom_position"

    def test_report_persisted_to_db(self, db):
        """Reconciliation report should be saved to reconciliation_reports table."""
        run_eod_reconciliation(report_date="2026-03-03", db_path=db)

        conn = get_conn(db)
        rows = conn.execute("SELECT * FROM reconciliation_reports").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["status"] == "clean"
        details = json.loads(rows[0]["details"])
        assert details["report_date"] == "2026-03-03"

    def test_multiple_accounts_reconciled(self, db):
        """All active broker accounts should be checked."""
        conn = get_conn(db)
        conn.execute(
            """INSERT INTO broker_accounts (id, broker, account_id, account_type, currency, is_active, created_at, updated_at)
               VALUES ('acct1', 'ig', 'IG1', 'ISA', 'GBP', 1, '2026-01-01', '2026-01-01')"""
        )
        conn.execute(
            """INSERT INTO broker_accounts (id, broker, account_id, account_type, currency, is_active, created_at, updated_at)
               VALUES ('acct2', 'ibkr', 'IB1', 'GIA', 'GBP', 1, '2026-01-01', '2026-01-01')"""
        )
        conn.execute(
            """INSERT INTO broker_accounts (id, broker, account_id, account_type, currency, is_active, created_at, updated_at)
               VALUES ('acct3', 'ig', 'IG2', 'SIPP', 'GBP', 0, '2026-01-01', '2026-01-01')"""
        )
        conn.commit()
        conn.close()

        report = run_eod_reconciliation(report_date="2026-03-03", db_path=db)
        assert report.broker_accounts_checked == 2  # only active accounts

    def test_default_report_date_is_today(self, db):
        """No report_date should default to today."""
        report = run_eod_reconciliation(db_path=db)
        assert report.report_date == date.today().isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Scheduler dispatch integration
# ═══════════════════════════════════════════════════════════════════════════


class TestDispatchEODReconciliation:
    def test_dispatch_returns_dict_with_window_name(self, db):
        """Dispatch callback returns a dict with window_name set."""
        payload = dispatch_eod_reconciliation(
            window_name="us_close_eod",
            db_path=db,
            report_date="2026-03-03",
        )
        assert isinstance(payload, dict)
        assert payload["window_name"] == "us_close_eod"
        assert payload["status"] == "clean"

    def test_dispatch_handles_errors_gracefully(self, tmp_path):
        """Dispatch should not raise even if DB is broken."""
        payload = dispatch_eod_reconciliation(
            window_name="broken",
            db_path=str(tmp_path / "nonexistent.db"),
        )
        assert isinstance(payload, dict)
        assert payload["window_name"] == "broken"
        # Should still return a dict (error handling)

    def test_dispatch_includes_pnl_data(self, db):
        """Dispatch result should include P&L attribution data."""
        conn = get_conn(db)
        today = date.today().isoformat()
        conn.execute(
            """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, pnl)
               VALUES (?, 'AAPL', 'test_strat', 'BUY', 'CLOSE', 10, 150.0, 42.0)""",
            (today,),
        )
        conn.commit()
        conn.close()

        payload = dispatch_eod_reconciliation(
            window_name="eod",
            db_path=db,
            report_date=today,
        )
        assert payload["total_realised_pnl"] == 42.0
        assert len(payload["pnl_by_strategy"]) == 1
        assert payload["pnl_by_strategy"][0]["strategy"] == "test_strat"
