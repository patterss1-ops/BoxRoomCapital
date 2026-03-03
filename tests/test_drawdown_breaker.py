"""Tests for I-003 drawdown circuit breaker."""

from __future__ import annotations

from datetime import date

import pytest

from data.trade_db import get_conn, init_db
from risk.drawdown_breaker import (
    DrawdownAction,
    DrawdownConfig,
    DrawdownDecision,
    check_drawdown,
)


@pytest.fixture
def db(tmp_path):
    """Initialize a fresh test database."""
    db_path = str(tmp_path / "drawdown_test.db")
    init_db(db_path)
    return db_path


def _insert_daily_report(db_path, report_date, total_nav, hwm, drawdown_pct):
    """Helper to insert a fund_daily_report row."""
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO fund_daily_report
           (report_date, total_nav, total_cash, total_positions_value,
            unrealised_pnl, realised_pnl, daily_return_pct,
            drawdown_pct, high_water_mark, currency, created_at)
           VALUES (?, ?, 0, ?, 0, 0, 0, ?, ?, 'GBP', ?)""",
        (report_date, total_nav, total_nav, drawdown_pct, hwm, report_date + "T00:00:00Z"),
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Decision data class
# ═══════════════════════════════════════════════════════════════════════════


class TestDrawdownDecision:
    def test_to_dict(self):
        d = DrawdownDecision(
            action=DrawdownAction.HALT,
            reason="DAILY_HALT",
            daily_drawdown_pct=6.0,
            weekly_drawdown_pct=8.0,
            current_nav=94000.0,
            high_water_mark=100000.0,
        )
        result = d.to_dict()
        assert result["action"] == "halt"
        assert result["daily_drawdown_pct"] == 6.0
        assert result["current_nav"] == 94000.0


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Core drawdown checks
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckDrawdown:
    def test_disabled_returns_allow(self, db):
        """Disabled config always allows."""
        config = DrawdownConfig(enabled=False)
        decision = check_drawdown(config=config, db_path=db)
        assert decision.action == DrawdownAction.ALLOW
        assert decision.reason == "DRAWDOWN_CHECK_DISABLED"

    def test_no_data_returns_allow(self, db):
        """Empty DB should allow trading."""
        decision = check_drawdown(
            config=DrawdownConfig(),
            report_date="2026-03-03",
            db_path=db,
        )
        assert decision.action == DrawdownAction.ALLOW

    def test_daily_halt_triggered(self, db):
        """Daily drawdown > threshold triggers halt."""
        _insert_daily_report(db, "2026-03-03", 94000.0, 100000.0, 6.0)
        config = DrawdownConfig(daily_halt_pct=5.0)
        decision = check_drawdown(config=config, report_date="2026-03-03", db_path=db)
        assert decision.action == DrawdownAction.HALT
        assert "DAILY_HALT" in decision.reason
        assert decision.daily_drawdown_pct == 6.0

    def test_weekly_halt_triggered(self, db):
        """Weekly drawdown > threshold triggers halt."""
        # Insert reports showing a decline over the week
        _insert_daily_report(db, "2026-02-24", 100000.0, 100000.0, 0.0)
        _insert_daily_report(db, "2026-03-03", 89000.0, 100000.0, 2.0)
        config = DrawdownConfig(
            daily_halt_pct=10.0,  # daily won't trigger (2% < 10%)
            weekly_halt_pct=10.0,  # weekly will trigger (11% > 10%)
        )
        decision = check_drawdown(config=config, report_date="2026-03-03", db_path=db)
        assert decision.action == DrawdownAction.HALT
        assert "WEEKLY_HALT" in decision.reason

    def test_daily_warn_triggered(self, db):
        """Daily drawdown > warn threshold but < halt threshold."""
        _insert_daily_report(db, "2026-03-03", 96500.0, 100000.0, 3.5)
        config = DrawdownConfig(daily_halt_pct=5.0, daily_warn_pct=3.0)
        decision = check_drawdown(config=config, report_date="2026-03-03", db_path=db)
        assert decision.action == DrawdownAction.WARN
        assert "DAILY_WARN" in decision.reason

    def test_weekly_warn_triggered(self, db):
        """Weekly drawdown > warn threshold but < halt threshold."""
        _insert_daily_report(db, "2026-02-25", 100000.0, 100000.0, 0.0)
        _insert_daily_report(db, "2026-03-03", 92000.0, 100000.0, 1.0)
        config = DrawdownConfig(
            daily_halt_pct=10.0,
            daily_warn_pct=5.0,  # daily 1% < 5%, won't trigger
            weekly_halt_pct=10.0,  # weekly 8% < 10%, won't halt
            weekly_warn_pct=7.0,  # weekly 8% > 7%, will warn
        )
        decision = check_drawdown(config=config, report_date="2026-03-03", db_path=db)
        assert decision.action == DrawdownAction.WARN
        assert "WEEKLY_WARN" in decision.reason

    def test_within_limits_allows(self, db):
        """Drawdown within all thresholds should allow."""
        _insert_daily_report(db, "2026-03-03", 99000.0, 100000.0, 1.0)
        config = DrawdownConfig(
            daily_halt_pct=5.0,
            daily_warn_pct=3.0,
            weekly_halt_pct=10.0,
            weekly_warn_pct=7.0,
        )
        decision = check_drawdown(config=config, report_date="2026-03-03", db_path=db)
        assert decision.action == DrawdownAction.ALLOW
        assert decision.reason == "DRAWDOWN_OK"

    def test_default_report_date(self, db):
        """No report_date should use today."""
        today = date.today().isoformat()
        _insert_daily_report(db, today, 99000.0, 100000.0, 1.0)
        decision = check_drawdown(config=DrawdownConfig(), db_path=db)
        assert decision.action == DrawdownAction.ALLOW

    def test_halt_priority_over_warn(self, db):
        """If both daily halt and weekly warn trigger, halt wins."""
        _insert_daily_report(db, "2026-03-03", 93000.0, 100000.0, 7.0)
        config = DrawdownConfig(
            daily_halt_pct=5.0,
            weekly_warn_pct=3.0,
        )
        decision = check_drawdown(config=config, report_date="2026-03-03", db_path=db)
        assert decision.action == DrawdownAction.HALT

    def test_decision_includes_nav_data(self, db):
        """Decision should include current_nav and high_water_mark."""
        _insert_daily_report(db, "2026-03-03", 95000.0, 100000.0, 5.0)
        config = DrawdownConfig(daily_halt_pct=4.0)
        decision = check_drawdown(config=config, report_date="2026-03-03", db_path=db)
        assert decision.current_nav == 95000.0
        assert decision.high_water_mark == 100000.0
