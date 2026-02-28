"""
Tests for fund/nav.py — daily NAV calculation from multi-broker ledger data (B-003).

Tests cover:
- Fund NAV calculation from positions + cash
- Sleeve-level NAV breakdown
- Daily return calculation
- High water mark and drawdown tracking
- NAV persistence (fund_daily_report + sleeve_daily_report)
- run_daily_nav end-to-end job
- Edge cases: no positions, no cash, zero NAV
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from data.trade_db import (
    init_db,
    get_fund_daily_reports,
    get_sleeve_daily_reports,
    save_fund_daily_report,
)
from execution.ledger import (
    register_broker_account,
    sync_positions,
    sync_cash_balance,
)
from fund.nav import (
    NAVSnapshot,
    SleeveNAV,
    calculate_fund_nav,
    calculate_sleeve_navs,
    persist_nav_report,
    run_daily_nav,
)


@pytest.fixture
def db(tmp_path):
    """Create a fresh test database and return its path."""
    path = str(tmp_path / "test_fund_nav.db")
    init_db(path)
    return path


def _seed_broker_data(db: str) -> str:
    """Seed an IG broker account with positions and cash. Returns account id."""
    acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
    sync_positions(acct_id, [
        {"ticker": "FTSE100", "direction": "long", "quantity": 2,
         "avg_cost": 7500, "market_value": 15200, "unrealised_pnl": 200,
         "sleeve": "sleeve_1"},
        {"ticker": "DAX40", "direction": "long", "quantity": 1,
         "avg_cost": 16000, "market_value": 16500, "unrealised_pnl": 500,
         "sleeve": "sleeve_1"},
        {"ticker": "GOLD", "direction": "short", "quantity": 1,
         "avg_cost": 2000, "market_value": 1900, "unrealised_pnl": 100,
         "sleeve": "sleeve_2"},
    ], db_path=db)
    sync_cash_balance(acct_id, balance=50000, buying_power=45000, db_path=db)
    return acct_id


def _seed_multi_broker(db: str) -> tuple[str, str]:
    """Seed IG + IBKR accounts with positions and cash."""
    ig_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
    ibkr_id = register_broker_account("ibkr", "U1234567", "GIA", db_path=db)

    sync_positions(ig_id, [
        {"ticker": "FTSE100", "direction": "long", "quantity": 2,
         "market_value": 15000, "unrealised_pnl": 200, "sleeve": "sleeve_1"},
    ], db_path=db)
    sync_positions(ibkr_id, [
        {"ticker": "SPY", "direction": "long", "quantity": 10,
         "market_value": 5000, "unrealised_pnl": 300, "sleeve": "sleeve_6"},
    ], db_path=db)

    sync_cash_balance(ig_id, balance=30000, db_path=db)
    sync_cash_balance(ibkr_id, balance=20000, db_path=db)
    return ig_id, ibkr_id


# ─── Fund NAV calculation ─────────────────────────────────────────────────


class TestCalculateFundNAV:
    def test_basic_nav_calculation(self, db):
        _seed_broker_data(db)
        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)

        assert isinstance(nav, NAVSnapshot)
        assert nav.report_date == "2026-02-28"
        # cash (50000) + positions (15200 + 16500 + 1900 = 33600)
        assert nav.total_cash == 50000
        assert nav.total_positions_value == 33600
        assert nav.total_nav == 83600
        assert nav.unrealised_pnl == 800  # 200 + 500 + 100

    def test_multi_broker_aggregation(self, db):
        _seed_multi_broker(db)
        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)

        # Positions: 15000 + 5000 = 20000
        assert nav.total_positions_value == 20000
        # Cash: 30000 + 20000 = 50000
        assert nav.total_cash == 50000
        assert nav.total_nav == 70000
        assert nav.unrealised_pnl == 500  # 200 + 300

    def test_no_positions(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_cash_balance(acct_id, balance=10000, db_path=db)

        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)
        assert nav.total_nav == 10000
        assert nav.total_positions_value == 0
        assert nav.total_cash == 10000

    def test_no_cash(self, db):
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_positions(acct_id, [
            {"ticker": "FTSE100", "direction": "long", "quantity": 1,
             "market_value": 5000, "unrealised_pnl": 0},
        ], db_path=db)

        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)
        assert nav.total_nav == 5000
        assert nav.total_cash == 0

    def test_empty_database(self, db):
        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)
        assert nav.total_nav == 0
        assert nav.total_cash == 0
        assert nav.total_positions_value == 0

    def test_default_currency(self, db):
        _seed_broker_data(db)
        nav = calculate_fund_nav(db_path=db)
        assert nav.currency == "GBP"

    def test_daily_return_first_day(self, db):
        """First day should have no daily return (no previous)."""
        _seed_broker_data(db)
        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)
        assert nav.daily_return_pct is None

    def test_daily_return_with_previous(self, db):
        """Daily return should be calculated from previous fund report."""
        _seed_broker_data(db)
        # Persist a previous day's report
        save_fund_daily_report(
            report_date="2026-02-27",
            total_nav=80000,
            total_cash=48000,
            total_positions_value=32000,
            high_water_mark=80000,
            db_path=db,
        )
        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)
        # 83600 / 80000 - 1 = 4.5%
        assert nav.daily_return_pct is not None
        assert abs(nav.daily_return_pct - 4.5) < 0.01

    def test_drawdown_from_hwm(self, db):
        """Drawdown should reflect decline from high water mark."""
        _seed_broker_data(db)
        # Previous HWM was higher
        save_fund_daily_report(
            report_date="2026-02-25",
            total_nav=90000,
            high_water_mark=90000,
            db_path=db,
        )
        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)
        # NAV 83600 vs HWM 90000 → drawdown = (83600-90000)/90000 * 100 ≈ -7.11%
        assert nav.high_water_mark == 90000
        assert nav.drawdown_pct < 0
        assert abs(nav.drawdown_pct - (-7.111)) < 0.01

    def test_new_high_water_mark(self, db):
        """HWM should update when NAV exceeds previous."""
        _seed_broker_data(db)
        save_fund_daily_report(
            report_date="2026-02-25",
            total_nav=70000,
            high_water_mark=70000,
            db_path=db,
        )
        nav = calculate_fund_nav(report_date="2026-02-28", db_path=db)
        # NAV 83600 > previous HWM 70000 → new HWM = 83600
        assert nav.high_water_mark == 83600
        assert nav.drawdown_pct == 0  # At HWM, no drawdown


# ─── Sleeve NAV calculation ───────────────────────────────────────────────


class TestCalculateSleeveNAVs:
    def test_sleeve_breakdown(self, db):
        _seed_broker_data(db)
        sleeves = calculate_sleeve_navs(report_date="2026-02-28", db_path=db)

        assert len(sleeves) == 2
        sleeve_map = {s.sleeve: s for s in sleeves}
        assert "sleeve_1" in sleeve_map
        assert "sleeve_2" in sleeve_map

    def test_sleeve_positions_value(self, db):
        _seed_broker_data(db)
        sleeves = calculate_sleeve_navs(report_date="2026-02-28", db_path=db)
        sleeve_map = {s.sleeve: s for s in sleeves}

        # sleeve_1: FTSE100 (15200) + DAX40 (16500) = 31700
        assert sleeve_map["sleeve_1"].positions_value == 31700
        # sleeve_2: GOLD (1900)
        assert sleeve_map["sleeve_2"].positions_value == 1900

    def test_sleeve_cash_allocation(self, db):
        _seed_broker_data(db)
        sleeves = calculate_sleeve_navs(report_date="2026-02-28", db_path=db)
        sleeve_map = {s.sleeve: s for s in sleeves}

        # Cash allocated proportionally: total positions = 33600
        # sleeve_1: 31700/33600 * 50000 ≈ 47172.62
        s1_cash = sleeve_map["sleeve_1"].cash_allocated
        assert abs(s1_cash - 47172.62) < 1

        # sleeve_2: 1900/33600 * 50000 ≈ 2827.38
        s2_cash = sleeve_map["sleeve_2"].cash_allocated
        assert abs(s2_cash - 2827.38) < 1

    def test_sleeve_weights_sum_to_100(self, db):
        _seed_broker_data(db)
        sleeves = calculate_sleeve_navs(report_date="2026-02-28", db_path=db)
        total_weight = sum(s.weight_pct for s in sleeves)
        assert abs(total_weight - 100.0) < 0.1

    def test_sleeve_unrealised_pnl(self, db):
        _seed_broker_data(db)
        sleeves = calculate_sleeve_navs(report_date="2026-02-28", db_path=db)
        sleeve_map = {s.sleeve: s for s in sleeves}

        # sleeve_1: 200 + 500 = 700
        assert sleeve_map["sleeve_1"].unrealised_pnl == 700
        # sleeve_2: 100
        assert sleeve_map["sleeve_2"].unrealised_pnl == 100

    def test_no_positions_equal_cash_split(self, db):
        """With no positions, cash should be split equally among zero sleeves."""
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_cash_balance(acct_id, balance=10000, db_path=db)

        sleeves = calculate_sleeve_navs(report_date="2026-02-28", db_path=db)
        assert len(sleeves) == 0  # No sleeves without positions

    def test_unassigned_sleeve(self, db):
        """Positions without sleeve attribution go to 'unassigned'."""
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_positions(acct_id, [
            {"ticker": "FTSE100", "direction": "long", "quantity": 1,
             "market_value": 5000, "unrealised_pnl": 0},
        ], db_path=db)

        sleeves = calculate_sleeve_navs(report_date="2026-02-28", db_path=db)
        assert len(sleeves) == 1
        assert sleeves[0].sleeve == "unassigned"


# ─── NAV persistence ──────────────────────────────────────────────────────


class TestPersistNavReport:
    def test_persist_fund_report(self, db):
        nav = NAVSnapshot(
            report_date="2026-02-28",
            total_nav=100000,
            total_cash=60000,
            total_positions_value=40000,
            unrealised_pnl=2000,
            realised_pnl=500,
            daily_return_pct=1.5,
            drawdown_pct=-2.0,
            high_water_mark=102000,
        )
        persist_nav_report(nav, [], db_path=db)

        reports = get_fund_daily_reports(days=5, db_path=db)
        assert len(reports) == 1
        assert reports[0]["report_date"] == "2026-02-28"
        assert reports[0]["total_nav"] == 100000
        assert reports[0]["daily_return_pct"] == 1.5

    def test_persist_sleeve_reports(self, db):
        nav = NAVSnapshot(
            report_date="2026-02-28",
            total_nav=100000,
            total_cash=60000,
            total_positions_value=40000,
            unrealised_pnl=0,
            realised_pnl=0,
            daily_return_pct=None,
            drawdown_pct=0,
            high_water_mark=100000,
        )
        sleeves = [
            SleeveNAV("sleeve_1", nav=70000, positions_value=28000,
                       cash_allocated=42000, unrealised_pnl=1000,
                       realised_pnl=0, weight_pct=70.0, daily_return_pct=2.0),
            SleeveNAV("sleeve_2", nav=30000, positions_value=12000,
                       cash_allocated=18000, unrealised_pnl=500,
                       realised_pnl=0, weight_pct=30.0, daily_return_pct=-0.5),
        ]
        persist_nav_report(nav, sleeves, db_path=db)

        sleeve_reports = get_sleeve_daily_reports(days=5, db_path=db)
        assert len(sleeve_reports) == 2

    def test_upsert_idempotent(self, db):
        nav = NAVSnapshot(
            report_date="2026-02-28",
            total_nav=100000,
            total_cash=60000,
            total_positions_value=40000,
            unrealised_pnl=0,
            realised_pnl=0,
            daily_return_pct=1.0,
            drawdown_pct=0,
            high_water_mark=100000,
        )
        persist_nav_report(nav, [], db_path=db)

        # Update same date with new NAV
        nav2 = NAVSnapshot(
            report_date="2026-02-28",
            total_nav=101000,
            total_cash=60000,
            total_positions_value=41000,
            unrealised_pnl=0,
            realised_pnl=0,
            daily_return_pct=1.5,
            drawdown_pct=0,
            high_water_mark=101000,
        )
        persist_nav_report(nav2, [], db_path=db)

        reports = get_fund_daily_reports(days=5, db_path=db)
        assert len(reports) == 1
        assert reports[0]["total_nav"] == 101000


# ─── End-to-end daily NAV job ─────────────────────────────────────────────


class TestRunDailyNAV:
    def test_full_nav_job(self, db):
        _seed_broker_data(db)
        result = run_daily_nav(report_date="2026-02-28", db_path=db)

        assert result["report_date"] == "2026-02-28"
        assert result["total_nav"] == 83600
        assert result["total_cash"] == 50000
        assert result["total_positions_value"] == 33600
        assert result["unrealised_pnl"] == 800
        assert isinstance(result["sleeves"], list)
        assert len(result["sleeves"]) == 2

    def test_nav_job_persists_to_db(self, db):
        _seed_broker_data(db)
        run_daily_nav(report_date="2026-02-28", db_path=db)

        fund_reports = get_fund_daily_reports(days=5, db_path=db)
        assert len(fund_reports) == 1
        assert fund_reports[0]["total_nav"] == 83600

        sleeve_reports = get_sleeve_daily_reports(days=5, db_path=db)
        assert len(sleeve_reports) == 2

    def test_multi_day_sequence(self, db):
        """Two consecutive daily NAV runs should produce correct daily return."""
        _seed_broker_data(db)

        # Day 1
        run_daily_nav(report_date="2026-02-27", db_path=db)

        # Day 2 (same positions, so same NAV — return should be 0%)
        result = run_daily_nav(report_date="2026-02-28", db_path=db)
        assert result["daily_return_pct"] == 0.0

    def test_empty_fund(self, db):
        """NAV job on empty fund should succeed with zero values."""
        result = run_daily_nav(report_date="2026-02-28", db_path=db)
        assert result["total_nav"] == 0
        assert result["total_cash"] == 0
        assert result["sleeves"] == []


# ─── Regression: sleeve query truncation (C-000b) ───────────────────────


class TestSleeveQueryRegression:
    """Regression tests for get_sleeve_daily_reports truncation fix (C-000b).

    The ``days`` parameter must select the N most recent distinct *dates*,
    returning all sleeve rows for those dates.  Previously a global
    ``LIMIT N`` would silently drop sleeves when more sleeves than N existed.
    """

    def test_multiple_sleeves_not_truncated(self, db):
        """All sleeves should be returned even when days=2 and >2 sleeves exist."""
        from data.trade_db import save_sleeve_daily_report

        # 4 sleeves on a single date
        for sleeve in ["equity", "bonds", "commodities", "cash"]:
            save_sleeve_daily_report(
                report_date="2026-02-28",
                sleeve=sleeve,
                nav=25000,
                positions_value=15000,
                cash_allocated=10000,
                db_path=db,
            )

        reports = get_sleeve_daily_reports(days=2, db_path=db)
        sleeves_returned = {r["sleeve"] for r in reports}
        assert sleeves_returned == {"equity", "bonds", "commodities", "cash"}
        assert len(reports) == 4

    def test_multiple_dates_multiple_sleeves(self, db):
        """days=2 should return all sleeves for the 2 most recent dates."""
        from data.trade_db import save_sleeve_daily_report

        for date in ["2026-02-26", "2026-02-27", "2026-02-28"]:
            for sleeve in ["equity", "bonds", "commodities"]:
                save_sleeve_daily_report(
                    report_date=date,
                    sleeve=sleeve,
                    nav=25000,
                    positions_value=15000,
                    cash_allocated=10000,
                    db_path=db,
                )

        # days=2 should return 2 dates × 3 sleeves = 6 rows
        reports = get_sleeve_daily_reports(days=2, db_path=db)
        assert len(reports) == 6
        dates_returned = {r["report_date"] for r in reports}
        assert dates_returned == {"2026-02-27", "2026-02-28"}

    def test_days_1_returns_all_sleeves_for_latest_date(self, db):
        """days=1 should return all sleeves for just the most recent date."""
        from data.trade_db import save_sleeve_daily_report

        for date in ["2026-02-27", "2026-02-28"]:
            for sleeve in ["equity", "bonds"]:
                save_sleeve_daily_report(
                    report_date=date,
                    sleeve=sleeve,
                    nav=50000,
                    positions_value=30000,
                    cash_allocated=20000,
                    db_path=db,
                )

        reports = get_sleeve_daily_reports(days=1, db_path=db)
        assert len(reports) == 2
        assert all(r["report_date"] == "2026-02-28" for r in reports)

    def test_sleeve_filter_still_works(self, db):
        """Filtering by specific sleeve should still work correctly."""
        from data.trade_db import save_sleeve_daily_report

        for date in ["2026-02-27", "2026-02-28"]:
            for sleeve in ["equity", "bonds"]:
                save_sleeve_daily_report(
                    report_date=date,
                    sleeve=sleeve,
                    nav=50000,
                    positions_value=30000,
                    cash_allocated=20000,
                    db_path=db,
                )

        reports = get_sleeve_daily_reports(sleeve="equity", days=5, db_path=db)
        assert len(reports) == 2
        assert all(r["sleeve"] == "equity" for r in reports)
