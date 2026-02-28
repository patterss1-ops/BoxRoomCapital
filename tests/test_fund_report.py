"""
Tests for fund/report.py — fund performance report generation (B-003).

Tests cover:
- Daily report generation
- Period report generation (7d, 30d)
- Sleeve performance breakdown
- Performance statistics (return, drawdown, vol)
- Report text formatting
- Edge cases: no data, single day, missing sleeves
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from data.trade_db import (
    init_db,
    save_fund_daily_report,
    save_sleeve_daily_report,
    save_risk_daily_snapshot,
)
from fund.report import (
    FundReport,
    PerformanceSummary,
    SleevePerformance,
    generate_daily_report,
    generate_period_report,
    format_report_text,
)


@pytest.fixture
def db(tmp_path):
    """Create a fresh test database and return its path."""
    path = str(tmp_path / "test_fund_report.db")
    init_db(path)
    return path


def _seed_daily_reports(db: str, days: int = 10):
    """Seed fund_daily_report with a series of daily entries."""
    base_nav = 100000
    for i in range(days):
        day_num = 20 + i  # 2026-02-20 through 2026-02-29
        if day_num > 28:
            continue  # Feb only has 28 days in non-leap
        date_str = f"2026-02-{day_num:02d}"
        # Simulate gradual growth with some fluctuation
        nav = base_nav + (i * 500) + ((-1) ** i * 200)
        daily_ret = ((nav - base_nav) / base_nav * 100) if i > 0 else None
        hwm = max(base_nav, nav)

        save_fund_daily_report(
            report_date=date_str,
            total_nav=nav,
            total_cash=nav * 0.6,
            total_positions_value=nav * 0.4,
            unrealised_pnl=nav * 0.02,
            realised_pnl=100 * i,
            daily_return_pct=daily_ret,
            drawdown_pct=min(0, (nav - hwm) / hwm * 100) if hwm > 0 else 0,
            high_water_mark=hwm,
            db_path=db,
        )


def _seed_sleeve_reports(db: str):
    """Seed sleeve_daily_report for two sleeves over multiple days."""
    for day in [27, 28]:
        date_str = f"2026-02-{day}"
        save_sleeve_daily_report(
            report_date=date_str,
            sleeve="sleeve_1",
            nav=70000 + (day - 27) * 300,
            positions_value=28000 + (day - 27) * 100,
            cash_allocated=42000 + (day - 27) * 200,
            weight_pct=70.0,
            daily_return_pct=0.43 if day == 28 else None,
            db_path=db,
        )
        save_sleeve_daily_report(
            report_date=date_str,
            sleeve="sleeve_2",
            nav=30000 + (day - 27) * 100,
            positions_value=12000 + (day - 27) * 50,
            cash_allocated=18000 + (day - 27) * 50,
            weight_pct=30.0,
            daily_return_pct=0.33 if day == 28 else None,
            db_path=db,
        )


# ─── Daily report ─────────────────────────────────────────────────────────


class TestGenerateDailyReport:
    def test_basic_daily_report(self, db):
        _seed_daily_reports(db, 5)
        report = generate_daily_report(report_date="2026-02-24", db_path=db)

        assert report is not None
        assert isinstance(report, FundReport)
        assert report.report_date == "2026-02-24"
        assert report.period_label == "daily"
        assert report.performance.trading_days == 1

    def test_daily_return_in_report(self, db):
        save_fund_daily_report(
            report_date="2026-02-27",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=None,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=101500,
            high_water_mark=101500,
            daily_return_pct=1.5,
            drawdown_pct=0,
            db_path=db,
        )
        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is not None
        assert report.performance.return_pct == 1.5

    def test_no_data_returns_none(self, db):
        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is None

    def test_missing_date_returns_none(self, db):
        save_fund_daily_report(
            report_date="2026-02-20",
            total_nav=100000,
            high_water_mark=100000,
            db_path=db,
        )
        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is None

    def test_daily_with_sleeves(self, db):
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=0.5,
            db_path=db,
        )
        _seed_sleeve_reports(db)

        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is not None
        assert len(report.sleeves) == 2

    def test_daily_with_risk_snapshot(self, db):
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=0.5,
            db_path=db,
        )
        save_risk_daily_snapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=45.0,
            open_position_count=5,
            leverage_ratio=0.8,
            db_path=db,
        )

        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is not None
        assert report.risk_snapshot is not None
        assert report.risk_snapshot["total_heat_pct"] == 45.0


# ─── Period report ─────────────────────────────────────────────────────────


class TestGeneratePeriodReport:
    def test_7d_period_report(self, db):
        _seed_daily_reports(db, 9)
        report = generate_period_report(
            days=7, label="7d", end_date="2026-02-28", db_path=db
        )

        assert report is not None
        assert report.period_label == "7d"
        assert report.performance.trading_days >= 2

    def test_period_return_calculation(self, db):
        save_fund_daily_report(
            report_date="2026-02-21",
            total_nav=100000,
            high_water_mark=100000,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=105000,
            high_water_mark=105000,
            daily_return_pct=0.5,
            db_path=db,
        )
        report = generate_period_report(
            days=10, end_date="2026-02-28", db_path=db
        )
        assert report is not None
        assert abs(report.performance.return_pct - 5.0) < 0.01

    def test_insufficient_data_returns_none(self, db):
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=100000,
            high_water_mark=100000,
            db_path=db,
        )
        # Only 1 data point — need at least 2
        report = generate_period_report(
            days=7, end_date="2026-02-28", db_path=db
        )
        assert report is None

    def test_positive_negative_days_count(self, db):
        # 3 days: flat, up, down
        save_fund_daily_report(
            report_date="2026-02-25",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=0.0,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-26",
            total_nav=101000,
            high_water_mark=101000,
            daily_return_pct=1.0,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-27",
            total_nav=100500,
            high_water_mark=101000,
            daily_return_pct=-0.5,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=101200,
            high_water_mark=101200,
            daily_return_pct=0.7,
            db_path=db,
        )

        report = generate_period_report(
            days=7, end_date="2026-02-28", db_path=db
        )
        assert report is not None
        assert report.performance.positive_days == 2  # +1.0%, +0.7%
        assert report.performance.negative_days == 1  # -0.5%
        assert report.performance.best_day_pct == 1.0
        assert report.performance.worst_day_pct == -0.5

    def test_volatility_calculation(self, db):
        _seed_daily_reports(db, 9)
        report = generate_period_report(
            days=10, end_date="2026-02-28", db_path=db
        )
        if report and report.performance.volatility_ann_pct is not None:
            assert report.performance.volatility_ann_pct >= 0

    def test_max_drawdown(self, db):
        # Create a drawdown scenario: 100 → 105 → 95 → 98
        save_fund_daily_report(
            report_date="2026-02-24",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=0.0,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-25",
            total_nav=105000,
            high_water_mark=105000,
            daily_return_pct=5.0,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-26",
            total_nav=95000,
            high_water_mark=105000,
            daily_return_pct=-9.52,
            drawdown_pct=-9.52,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-27",
            total_nav=98000,
            high_water_mark=105000,
            daily_return_pct=3.16,
            drawdown_pct=-6.67,
            db_path=db,
        )

        report = generate_period_report(
            days=7, end_date="2026-02-27", db_path=db
        )
        assert report is not None
        # Max drawdown from 105k → 95k = -9.52%
        assert report.performance.max_drawdown_pct < -9.0


# ─── Report text formatting ───────────────────────────────────────────────


class TestFormatReportText:
    def test_daily_format(self, db):
        save_fund_daily_report(
            report_date="2026-02-27",
            total_nav=100000,
            high_water_mark=100000,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=101500,
            high_water_mark=101500,
            daily_return_pct=1.5,
            drawdown_pct=0,
            db_path=db,
        )
        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is not None

        text = format_report_text(report)
        assert "DAILY" in text
        assert "2026-02-28" in text
        assert "+1.50%" in text

    def test_format_with_sleeves(self, db):
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=0.5,
            db_path=db,
        )
        _seed_sleeve_reports(db)

        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is not None

        text = format_report_text(report)
        assert "Sleeves:" in text
        assert "sleeve_1" in text
        assert "sleeve_2" in text

    def test_format_with_risk(self, db):
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=0.5,
            db_path=db,
        )
        save_risk_daily_snapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=45.0,
            open_position_count=5,
            leverage_ratio=0.8,
            var_95_pct=2.5,
            db_path=db,
        )
        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is not None

        text = format_report_text(report)
        assert "Risk:" in text
        assert "Heat:" in text
        assert "VaR(95):" in text

    def test_deterministic_output(self, db):
        """Same report data should always produce the same text."""
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=0.5,
            db_path=db,
        )
        report = generate_daily_report(report_date="2026-02-28", db_path=db)
        assert report is not None

        text1 = format_report_text(report)
        text2 = format_report_text(report)
        assert text1 == text2

    def test_period_format(self, db):
        save_fund_daily_report(
            report_date="2026-02-21",
            total_nav=100000,
            high_water_mark=100000,
            daily_return_pct=0.0,
            db_path=db,
        )
        save_fund_daily_report(
            report_date="2026-02-28",
            total_nav=105000,
            high_water_mark=105000,
            daily_return_pct=0.5,
            db_path=db,
        )
        report = generate_period_report(
            days=10, label="7d", end_date="2026-02-28", db_path=db
        )
        assert report is not None

        text = format_report_text(report)
        assert "7D" in text
        assert "Trading Days:" in text
