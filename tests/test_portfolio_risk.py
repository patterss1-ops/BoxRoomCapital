"""
Tests for risk/portfolio_risk.py — portfolio-level risk metrics (B-003).

Tests cover:
- Portfolio risk calculation from positions
- Heat utilisation (gross exposure / NAV)
- Position concentration (max single position %)
- Leverage ratio
- Margin estimation
- Parametric VaR (95%, 1-day)
- Position risk detail drill-down
- Risk verdict generation (GREEN/AMBER/RED)
- Persistence to risk_daily_snapshot table
- Edge cases: no positions, single position, zero NAV
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from data.trade_db import init_db, get_risk_daily_snapshots, get_conn
from execution.ledger import (
    register_broker_account,
    sync_positions,
)
from risk.portfolio_risk import (
    PortfolioRiskSnapshot,
    PositionRiskDetail,
    calculate_portfolio_risk,
    get_position_risk_details,
    persist_risk_snapshot,
    run_daily_risk,
    generate_risk_verdict,
)


@pytest.fixture
def db(tmp_path):
    """Create a fresh test database and return its path."""
    path = str(tmp_path / "test_portfolio_risk.db")
    init_db(path)
    return path


def _seed_positions(db: str) -> str:
    """Seed standard positions. Returns account id."""
    acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
    sync_positions(acct_id, [
        {"ticker": "FTSE100", "direction": "long", "quantity": 2,
         "market_value": 15000, "unrealised_pnl": 500, "sleeve": "sleeve_1"},
        {"ticker": "DAX40", "direction": "long", "quantity": 1,
         "market_value": 10000, "unrealised_pnl": 200, "sleeve": "sleeve_1"},
        {"ticker": "GOLD", "direction": "short", "quantity": 1,
         "market_value": 5000, "unrealised_pnl": -100, "sleeve": "sleeve_2"},
    ], db_path=db)
    return acct_id


def _seed_concentrated_positions(db: str) -> str:
    """Seed a concentrated portfolio for threshold testing."""
    acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
    sync_positions(acct_id, [
        {"ticker": "TSLA", "direction": "long", "quantity": 100,
         "market_value": 80000, "unrealised_pnl": 5000, "sleeve": "sleeve_4"},
        {"ticker": "AAPL", "direction": "long", "quantity": 50,
         "market_value": 20000, "unrealised_pnl": 1000, "sleeve": "sleeve_4"},
    ], db_path=db)
    return acct_id


# ─── Portfolio risk calculation ────────────────────────────────────────────


class TestCalculatePortfolioRisk:
    def test_basic_risk_metrics(self, db):
        _seed_positions(db)
        risk = calculate_portfolio_risk(
            total_nav=100000,
            snapshot_date="2026-02-28",
            db_path=db,
        )

        assert isinstance(risk, PortfolioRiskSnapshot)
        assert risk.snapshot_date == "2026-02-28"
        assert risk.open_position_count == 3

    def test_heat_utilisation(self, db):
        _seed_positions(db)
        risk = calculate_portfolio_risk(total_nav=100000, db_path=db)

        # Gross exposure: |15000| + |10000| + |5000| = 30000
        # Heat: 30000 / 100000 * 100 = 30%
        assert abs(risk.total_heat_pct - 30.0) < 0.01

    def test_max_position_concentration(self, db):
        _seed_positions(db)
        risk = calculate_portfolio_risk(total_nav=100000, db_path=db)

        # Max position: FTSE100 at 15000 = 15% of NAV
        assert abs(risk.max_position_pct - 15.0) < 0.01

    def test_leverage_ratio(self, db):
        _seed_positions(db)
        risk = calculate_portfolio_risk(total_nav=100000, db_path=db)

        # Leverage: 30000 / 100000 = 0.3x
        assert abs(risk.leverage_ratio - 0.3) < 0.01

    def test_no_positions(self, db):
        risk = calculate_portfolio_risk(total_nav=100000, db_path=db)

        assert risk.open_position_count == 0
        assert risk.total_heat_pct == 0
        assert risk.max_position_pct == 0
        assert risk.leverage_ratio == 0
        assert risk.var_95_pct is None

    def test_var_calculation(self, db):
        _seed_positions(db)
        risk = calculate_portfolio_risk(total_nav=100000, db_path=db)

        # VaR should be positive (risk expressed as % of NAV)
        assert risk.var_95_pct is not None
        assert risk.var_95_pct > 0

    def test_spread_count(self, db):
        """Spread count should come from option_positions table."""
        _seed_positions(db)

        # Insert an open option spread (schema uses spread_id, trade_type, entry_date)
        conn = get_conn(db)
        conn.execute("""
            INSERT INTO option_positions (spread_id, ticker, strategy, trade_type, size,
                                          entry_date, status)
            VALUES ('SPR-001', 'FTSE100', 'ibs_credit', 'credit_spread', 1, '2026-02-28', 'open')
        """)
        conn.commit()
        conn.close()

        risk = calculate_portfolio_risk(total_nav=100000, db_path=db)
        assert risk.open_spread_count == 1

    def test_margin_estimation(self, db):
        _seed_positions(db)
        risk = calculate_portfolio_risk(total_nav=100000, db_path=db)

        # Should have some margin estimate > 0
        assert risk.total_margin_pct > 0

    def test_short_position_higher_margin(self, db):
        """Short positions should use higher margin estimate."""
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)

        # All-short portfolio
        sync_positions(acct_id, [
            {"ticker": "TSLA", "direction": "short", "quantity": 10,
             "market_value": 50000, "unrealised_pnl": 0},
        ], db_path=db)
        risk_short = calculate_portfolio_risk(total_nav=100000, db_path=db)

        # Clean up and create all-long
        sync_positions(acct_id, [
            {"ticker": "TSLA", "direction": "long", "quantity": 10,
             "market_value": 50000, "unrealised_pnl": 0},
        ], db_path=db)
        risk_long = calculate_portfolio_risk(total_nav=100000, db_path=db)

        # Short margin should be higher than long margin
        assert risk_short.total_margin_pct > risk_long.total_margin_pct


# ─── Position risk details ─────────────────────────────────────────────────


class TestPositionRiskDetails:
    def test_detail_list(self, db):
        _seed_positions(db)
        details = get_position_risk_details(total_nav=100000, db_path=db)

        assert len(details) == 3
        assert all(isinstance(d, PositionRiskDetail) for d in details)

    def test_sorted_by_weight(self, db):
        _seed_positions(db)
        details = get_position_risk_details(total_nav=100000, db_path=db)

        # Should be sorted by absolute market value (largest first)
        assert details[0].ticker == "FTSE100"  # 15000
        assert details[1].ticker == "DAX40"    # 10000
        assert details[2].ticker == "GOLD"     # 5000

    def test_weight_calculation(self, db):
        _seed_positions(db)
        details = get_position_risk_details(total_nav=100000, db_path=db)

        ftse = details[0]
        assert abs(ftse.weight_pct - 15.0) < 0.01

    def test_includes_broker_info(self, db):
        _seed_positions(db)
        details = get_position_risk_details(total_nav=100000, db_path=db)
        assert all(d.broker == "ig" for d in details)

    def test_empty_portfolio(self, db):
        details = get_position_risk_details(total_nav=100000, db_path=db)
        assert details == []


# ─── Risk verdict generation ──────────────────────────────────────────────


class TestGenerateRiskVerdict:
    def test_green_verdict(self, db):
        """Low-risk portfolio should get GREEN."""
        # Use a larger NAV so max position (15000) is only 7.5% — below 10% threshold
        _seed_positions(db)
        risk = calculate_portfolio_risk(total_nav=200000, db_path=db)
        verdict = generate_risk_verdict(risk)

        assert verdict["status"] == "GREEN"
        assert len(verdict["alerts"]) == 0

    def test_amber_heat(self):
        """Heat between 60-80% should trigger AMBER."""
        snapshot = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=65.0,
            total_margin_pct=10.0,
            max_position_pct=8.0,
            open_position_count=5,
            open_spread_count=0,
            leverage_ratio=0.65,
            var_95_pct=3.0,
        )
        verdict = generate_risk_verdict(snapshot)
        assert verdict["status"] == "AMBER"
        assert any("HEAT_ELEVATED" in a for a in verdict["alerts"])

    def test_red_heat(self):
        """Heat > 80% should trigger RED."""
        snapshot = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=85.0,
            total_margin_pct=15.0,
            max_position_pct=8.0,
            open_position_count=10,
            open_spread_count=0,
            leverage_ratio=0.85,
            var_95_pct=5.0,
        )
        verdict = generate_risk_verdict(snapshot)
        assert verdict["status"] == "RED"
        assert any("HEAT_CRITICAL" in a for a in verdict["alerts"])

    def test_amber_concentration(self):
        """Max position 10-15% should trigger AMBER."""
        snapshot = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=40.0,
            total_margin_pct=5.0,
            max_position_pct=12.0,
            open_position_count=3,
            open_spread_count=0,
            leverage_ratio=0.4,
            var_95_pct=2.0,
        )
        verdict = generate_risk_verdict(snapshot)
        assert verdict["status"] == "AMBER"
        assert any("CONCENTRATION_ELEVATED" in a for a in verdict["alerts"])

    def test_red_concentration(self):
        """Max position > 15% should trigger RED."""
        snapshot = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=50.0,
            total_margin_pct=10.0,
            max_position_pct=18.0,
            open_position_count=3,
            open_spread_count=0,
            leverage_ratio=0.5,
            var_95_pct=3.0,
        )
        verdict = generate_risk_verdict(snapshot)
        assert verdict["status"] == "RED"
        assert any("CONCENTRATION_CRITICAL" in a for a in verdict["alerts"])

    def test_amber_leverage(self):
        """Leverage 1.5-2.0x should trigger AMBER."""
        snapshot = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=50.0,
            total_margin_pct=10.0,
            max_position_pct=8.0,
            open_position_count=5,
            open_spread_count=0,
            leverage_ratio=1.7,
            var_95_pct=4.0,
        )
        verdict = generate_risk_verdict(snapshot)
        assert verdict["status"] == "AMBER"
        assert any("LEVERAGE_ELEVATED" in a for a in verdict["alerts"])

    def test_red_leverage(self):
        """Leverage > 2.0x should trigger RED."""
        snapshot = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=50.0,
            total_margin_pct=10.0,
            max_position_pct=8.0,
            open_position_count=5,
            open_spread_count=0,
            leverage_ratio=2.5,
            var_95_pct=5.0,
        )
        verdict = generate_risk_verdict(snapshot)
        assert verdict["status"] == "RED"
        assert any("LEVERAGE_CRITICAL" in a for a in verdict["alerts"])

    def test_multiple_alerts(self):
        """Multiple threshold breaches should all be reported."""
        snapshot = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=90.0,
            total_margin_pct=20.0,
            max_position_pct=20.0,
            open_position_count=3,
            open_spread_count=0,
            leverage_ratio=2.5,
            var_95_pct=8.0,
        )
        verdict = generate_risk_verdict(snapshot)
        assert verdict["status"] == "RED"
        assert len(verdict["alerts"]) == 3  # Heat + Concentration + Leverage

    def test_verdict_deterministic(self, db):
        """Same snapshot should always produce the same verdict."""
        _seed_positions(db)
        risk = calculate_portfolio_risk(total_nav=100000, db_path=db)
        v1 = generate_risk_verdict(risk)
        v2 = generate_risk_verdict(risk)
        assert v1 == v2


# ─── Risk persistence ─────────────────────────────────────────────────────


class TestPersistRiskSnapshot:
    def test_persist_and_retrieve(self, db):
        snapshot = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=30.0,
            total_margin_pct=5.0,
            max_position_pct=15.0,
            open_position_count=3,
            open_spread_count=1,
            leverage_ratio=0.3,
            var_95_pct=2.5,
        )
        persist_risk_snapshot(snapshot, db_path=db)

        rows = get_risk_daily_snapshots(days=5, db_path=db)
        assert len(rows) == 1
        assert rows[0]["snapshot_date"] == "2026-02-28"
        assert rows[0]["total_heat_pct"] == 30.0
        assert rows[0]["open_position_count"] == 3

    def test_upsert_same_date(self, db):
        snapshot1 = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=30.0,
            total_margin_pct=5.0,
            max_position_pct=15.0,
            open_position_count=3,
            open_spread_count=1,
            leverage_ratio=0.3,
            var_95_pct=2.5,
        )
        persist_risk_snapshot(snapshot1, db_path=db)

        snapshot2 = PortfolioRiskSnapshot(
            snapshot_date="2026-02-28",
            total_heat_pct=35.0,
            total_margin_pct=6.0,
            max_position_pct=16.0,
            open_position_count=4,
            open_spread_count=2,
            leverage_ratio=0.35,
            var_95_pct=3.0,
        )
        persist_risk_snapshot(snapshot2, db_path=db)

        rows = get_risk_daily_snapshots(days=5, db_path=db)
        assert len(rows) == 1
        assert rows[0]["total_heat_pct"] == 35.0
        assert rows[0]["open_position_count"] == 4


# ─── End-to-end daily risk job ─────────────────────────────────────────────


class TestRunDailyRisk:
    def test_full_risk_job(self, db):
        _seed_positions(db)
        result = run_daily_risk(
            total_nav=100000,
            snapshot_date="2026-02-28",
            db_path=db,
        )

        assert result["snapshot_date"] == "2026-02-28"
        assert result["open_position_count"] == 3
        assert result["total_heat_pct"] == 30.0
        assert result["leverage_ratio"] == 0.3

    def test_risk_job_persists(self, db):
        _seed_positions(db)
        run_daily_risk(
            total_nav=100000,
            snapshot_date="2026-02-28",
            db_path=db,
        )

        rows = get_risk_daily_snapshots(days=5, db_path=db)
        assert len(rows) == 1
        assert rows[0]["snapshot_date"] == "2026-02-28"

    def test_risk_job_empty_portfolio(self, db):
        result = run_daily_risk(
            total_nav=100000,
            snapshot_date="2026-02-28",
            db_path=db,
        )
        assert result["open_position_count"] == 0
        assert result["total_heat_pct"] == 0
        assert result["var_95_pct"] is None


# ─── B-004 risk briefing contract ──────────────────────────────────────────


class TestRiskBriefing:
    def test_briefing_contract_fields(self, db):
        """Briefing should contain all fields B-004 expects."""
        from risk.portfolio_risk import get_risk_briefing
        from execution.ledger import sync_cash_balance

        _seed_positions(db)
        acct_id = register_broker_account("ig", "ACC-IG-2", "spreadbet", db_path=db)
        sync_cash_balance(acct_id, balance=50000, db_path=db)

        briefing = get_risk_briefing(
            total_nav=100000,
            daily_return_pct=1.5,
            drawdown_pct=-2.0,
            total_cash=50000,
            snapshot_date="2026-02-28",
            db_path=db,
        )

        # All B-004 contract fields present
        assert "fund_nav" in briefing
        assert "day_pnl" in briefing
        assert "drawdown_pct" in briefing
        assert "gross_exposure_pct" in briefing
        assert "net_exposure_pct" in briefing
        assert "cash_buffer_pct" in briefing
        assert "open_risk_pct" in briefing
        assert "generated_at" in briefing
        assert "alerts" in briefing
        assert "limits" in briefing
        assert "status" in briefing

    def test_briefing_values(self, db):
        """Briefing values should be correctly computed."""
        from risk.portfolio_risk import get_risk_briefing

        _seed_positions(db)

        briefing = get_risk_briefing(
            total_nav=100000,
            daily_return_pct=1.5,
            drawdown_pct=-2.0,
            total_cash=40000,
            snapshot_date="2026-02-28",
            db_path=db,
        )

        assert briefing["fund_nav"] == 100000
        assert briefing["day_pnl"] == 1500.0  # 1.5% of 100k
        assert briefing["drawdown_pct"] == -2.0
        assert briefing["cash_buffer_pct"] == 40.0  # 40k/100k
        assert briefing["gross_exposure_pct"] == 30.0  # heat from positions

    def test_briefing_limits_list(self, db):
        """Briefing should include static limits for UI rendering."""
        from risk.portfolio_risk import get_risk_briefing

        briefing = get_risk_briefing(
            total_nav=100000,
            snapshot_date="2026-02-28",
            db_path=db,
        )

        assert len(briefing["limits"]) == 3
        rules = {l["rule"] for l in briefing["limits"]}
        assert "max_heat_pct" in rules
        assert "max_position_pct" in rules
        assert "max_leverage" in rules

    def test_briefing_alerts_on_breach(self, db):
        """Briefing alerts should fire on threshold breaches."""
        from risk.portfolio_risk import get_risk_briefing

        # Create a concentrated position that triggers AMBER
        acct_id = register_broker_account("ig", "ACC-IG-1", "spreadbet", db_path=db)
        sync_positions(acct_id, [
            {"ticker": "TSLA", "direction": "long", "quantity": 100,
             "market_value": 80000, "unrealised_pnl": 5000},
        ], db_path=db)

        briefing = get_risk_briefing(
            total_nav=100000,
            snapshot_date="2026-02-28",
            db_path=db,
        )

        assert briefing["status"] == "RED"
        assert len(briefing["alerts"]) > 0
        assert all("severity" in a for a in briefing["alerts"])
        assert all("code" in a for a in briefing["alerts"])
        assert all("action" in a for a in briefing["alerts"])
