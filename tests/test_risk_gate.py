"""
Tests for risk/pre_trade_gate.py — Pre-trade risk gate.

Covers all 12 risk rules (R-001 through R-012), the verdict audit trail,
and integration with ledger-based portfolio snapshot building.
"""
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from data.trade_db import init_db, get_conn
from risk.pre_trade_gate import (
    RiskLimits,
    RiskVerdict,
    PreTradeRiskGate,
    OrderProposal,
    PortfolioSnapshot,
    build_portfolio_snapshot,
)
from execution.ledger import (
    register_broker_account,
    sync_positions,
    sync_cash_balance,
    save_nav_snapshot,
)


@pytest.fixture
def db_path():
    """Create a fresh temp DB for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    yield path
    os.unlink(path)


def _default_portfolio(**overrides) -> PortfolioSnapshot:
    """Create a default healthy portfolio for testing."""
    defaults = {
        "fund_nav": 100_000.0,
        "fund_cash": 30_000.0,
        "fund_peak_nav": 100_000.0,
        "daily_pnl": 0.0,
        "positions": [],
        "kill_switch_active": False,
    }
    defaults.update(overrides)
    return PortfolioSnapshot(**defaults)


def _default_proposal(**overrides) -> OrderProposal:
    """Create a default small-risk trade for testing.

    Defaults are sized to pass all 12 rules against a 100k portfolio:
    - notional 3k = 3% of NAV (under 5% concentration, 10% notional)
    - risk 500 = 0.5% (under 2% trade risk)
    - cash 30k - 3k = 27k = 27% (above 10% buffer)
    """
    defaults = {
        "ticker": "SPY",
        "direction": "long",
        "quantity": 10,
        "notional_value": 3_000.0,
        "risk_amount": 500.0,
        "strategy": "ibs_etf",
        "sleeve": "sleeve_2",
        "broker": "ibkr",
        "account_type": "PAPER",
        "sector": "Broad Market",
    }
    defaults.update(overrides)
    return OrderProposal(**defaults)


# ─── R-001: Kill switch ────────────────────────────────────────────────────


class TestKillSwitch:
    """R-001: Block all trades when kill switch is active."""

    def test_kill_switch_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(kill_switch_active=True)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-001"
        assert "kill switch" in verdict.reason.lower()

    def test_no_kill_switch_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(kill_switch_active=False)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-002: Fund drawdown ──────────────────────────────────────────────────


class TestFundDrawdown:
    """R-002: Block trades if fund drawdown exceeds limit."""

    def test_excessive_drawdown_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            fund_nav=65_000.0,   # 35% drawdown from peak
            fund_peak_nav=100_000.0,
        )
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-002"

    def test_moderate_drawdown_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            fund_nav=80_000.0,   # 20% drawdown
            fund_peak_nav=100_000.0,
        )
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_no_peak_passes(self, db_path):
        """If we don't have a peak NAV yet, skip this check."""
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(fund_peak_nav=0)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-003: Daily loss limit ───────────────────────────────────────────────


class TestDailyLossLimit:
    """R-003: Block trades if daily loss exceeds limit."""

    def test_daily_loss_exceeded_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            daily_pnl=-6_000.0,  # -6% of 100k NAV (limit is 5%)
        )
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-003"

    def test_small_daily_loss_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(daily_pnl=-2_000.0)  # -2%
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_positive_daily_pnl_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(daily_pnl=3_000.0)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-004: Cash buffer ────────────────────────────────────────────────────


class TestCashBuffer:
    """R-004: Ensure minimum cash buffer is maintained after trade."""

    def test_trade_depletes_cash_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            fund_nav=100_000.0,
            fund_cash=12_000.0,  # Only 12% cash
        )
        proposal = _default_proposal(notional_value=5_000.0)  # Would leave 7% cash
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-004"

    def test_sufficient_cash_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(fund_cash=30_000.0)
        proposal = _default_proposal(notional_value=5_000.0)  # 25% cash remains
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-005: Trade risk limit ───────────────────────────────────────────────


class TestTradeRiskLimit:
    """R-005: Max risk per trade as % of fund NAV."""

    def test_excessive_risk_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(risk_amount=3_000.0)  # 3% > max 2%
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-005"

    def test_acceptable_risk_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(risk_amount=1_500.0)  # 1.5% < max 2%
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_custom_limit(self, db_path):
        """Test with a tighter custom risk limit."""
        limits = RiskLimits(trade_max_risk_pct=1.0)
        gate = PreTradeRiskGate(limits=limits, db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(risk_amount=1_500.0)  # 1.5% > custom 1%
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-005"


# ─── R-006: Trade notional limit ──────────────────────────────────────────


class TestTradeNotionalLimit:
    """R-006: Max notional value per trade as % of NAV."""

    def test_large_trade_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(notional_value=15_000.0)  # 15% > max 10%
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-006"

    def test_normal_trade_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(notional_value=4_000.0)  # 4% notional < 10%, and 4% concentration < 5%
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-007: Position concentration ─────────────────────────────────────────


class TestPositionConcentration:
    """R-007: No single ticker > max_single_position_pct of NAV."""

    def test_over_concentrated_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            positions=[
                {"ticker": "SPY", "market_value": 4_000.0, "strategy": "ibs_etf"},
            ]
        )
        # Existing 4k + new 2k = 6k = 6% > max 5%
        proposal = _default_proposal(ticker="SPY", notional_value=2_000.0)
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-007"

    def test_under_limit_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            positions=[
                {"ticker": "SPY", "market_value": 2_000.0, "strategy": "ibs_etf"},
            ]
        )
        # Existing 2k + new 2k = 4k = 4% < 5%
        proposal = _default_proposal(ticker="SPY", notional_value=2_000.0)
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_new_ticker_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(ticker="AAPL", notional_value=4_000.0)
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-008: Sleeve allocation ──────────────────────────────────────────────


class TestSleeveAllocation:
    """R-008: No sleeve > sleeve_max_allocation_pct of fund NAV."""

    def test_sleeve_over_allocated_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            fund_cash=50_000.0,
            sleeve_navs={"sleeve_2": 32_000.0},
        )
        # 32k + 4k = 36k = 36% > max 35%, but 4k/100k = 4% concentration (OK)
        proposal = _default_proposal(sleeve="sleeve_2", notional_value=4_000.0, ticker="VUSA")
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-008"

    def test_sleeve_within_limit_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            sleeve_navs={"sleeve_2": 20_000.0},
        )
        # 20k + 5k = 25k = 25% < 35%
        proposal = _default_proposal(sleeve="sleeve_2", notional_value=5_000.0)
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_no_sleeve_skips_check(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(sleeve="")
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-009: Sleeve drawdown ────────────────────────────────────────────────


class TestSleeveDrawdown:
    """R-009: Block trades in a sleeve that has exceeded its drawdown limit."""

    def test_sleeve_drawdown_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            sleeve_navs={"sleeve_1": 8_000.0},
            sleeve_peak_navs={"sleeve_1": 10_000.0},  # 20% DD > max 15%
        )
        proposal = _default_proposal(sleeve="sleeve_1")
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-009"

    def test_sleeve_small_drawdown_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            sleeve_navs={"sleeve_1": 9_000.0},
            sleeve_peak_navs={"sleeve_1": 10_000.0},  # 10% DD < 15%
        )
        proposal = _default_proposal(sleeve="sleeve_1")
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-010: Strategy max positions ─────────────────────────────────────────


class TestStrategyMaxPositions:
    """R-010: Max open positions per strategy."""

    def test_too_many_positions_blocks(self, db_path):
        limits = RiskLimits(strategy_max_open_positions=3)
        gate = PreTradeRiskGate(limits=limits, db_path=db_path)
        portfolio = _default_portfolio(
            positions=[
                {"ticker": "AAPL", "strategy": "ibs_etf", "market_value": 2000},
                {"ticker": "MSFT", "strategy": "ibs_etf", "market_value": 2000},
                {"ticker": "GOOG", "strategy": "ibs_etf", "market_value": 2000},
            ]
        )
        # Use ticker not in existing positions to avoid concentration issues
        proposal = _default_proposal(strategy="ibs_etf", ticker="AMZN")
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-010"

    def test_under_limit_passes(self, db_path):
        limits = RiskLimits(strategy_max_open_positions=5)
        gate = PreTradeRiskGate(limits=limits, db_path=db_path)
        portfolio = _default_portfolio(
            positions=[
                {"ticker": "AAPL", "strategy": "ibs_etf", "market_value": 2000},
                {"ticker": "MSFT", "strategy": "ibs_etf", "market_value": 2000},
            ]
        )
        proposal = _default_proposal(strategy="ibs_etf", ticker="AMZN")
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_different_strategy_not_counted(self, db_path):
        limits = RiskLimits(strategy_max_open_positions=2)
        gate = PreTradeRiskGate(limits=limits, db_path=db_path)
        portfolio = _default_portfolio(
            positions=[
                {"ticker": "AAPL", "strategy": "other_strat", "market_value": 2000},
                {"ticker": "MSFT", "strategy": "other_strat", "market_value": 2000},
            ]
        )
        proposal = _default_proposal(strategy="ibs_etf", ticker="AMZN")
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-011: Sector concentration ───────────────────────────────────────────


class TestSectorConcentration:
    """R-011: No single sector > max_sector_exposure_pct of NAV."""

    def test_sector_over_limit_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            positions=[
                {"ticker": "AAPL", "sector": "Technology", "market_value": 20_000.0},
                {"ticker": "MSFT", "sector": "Technology", "market_value": 3_000.0},
            ]
        )
        # 23k + 5k = 28k = 28% > 25%
        proposal = _default_proposal(sector="Technology", notional_value=5_000.0)
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-011"

    def test_sector_within_limit_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(
            positions=[
                {"ticker": "AAPL", "sector": "Technology", "market_value": 15_000.0},
            ]
        )
        # 15k + 5k = 20k = 20% < 25%
        proposal = _default_proposal(sector="Technology", notional_value=5_000.0)
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_no_sector_skips_check(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(sector="")
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── R-012: Cooldown ───────────────────────────────────────────────────────


class TestCooldown:
    """R-012: Enforce cooldown period after kill switch reset."""

    def test_recent_reset_blocks(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        # Reset happened 1 hour ago (need 4h cooldown)
        recent = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        portfolio = _default_portfolio(kill_switch_reset_time=recent)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-012"

    def test_old_reset_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        # Reset happened 6 hours ago (cooldown is 4h)
        old = (datetime.utcnow() - timedelta(hours=6)).isoformat()
        portfolio = _default_portfolio(kill_switch_reset_time=old)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_no_reset_passes(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(kill_switch_reset_time=None)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved


# ─── Verdict structure ──────────────────────────────────────────────────────


class TestVerdictStructure:
    """Test the verdict object and its properties."""

    def test_approved_verdict_fields(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved
        assert verdict.rule_id is None
        assert verdict.reason == "OK"
        assert verdict.checks_run == 12  # All 12 rules checked
        assert len(verdict.details) == 12
        assert verdict.verdict_id  # UUID assigned
        assert verdict.timestamp  # Timestamp assigned

    def test_rejected_verdict_stops_at_first_failure(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(kill_switch_active=True)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.checks_run == 1  # Stopped at first rule
        assert verdict.rule_id == "R-001"

    def test_verdict_to_dict(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        d = verdict.to_dict()
        assert isinstance(d, dict)
        assert "approved" in d
        assert "details" in d
        assert "verdict_id" in d

    def test_details_contain_check_results(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)
        for detail in verdict.details:
            assert "rule_id" in detail
            assert "rule_name" in detail
            assert "passed" in detail
            assert "reason" in detail


# ─── Audit persistence ──────────────────────────────────────────────────────


class TestAuditPersistence:
    """Test that risk verdicts are persisted to the DB."""

    def test_approved_verdict_persisted(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)

        conn = get_conn(db_path)
        row = conn.execute(
            "SELECT * FROM risk_verdicts WHERE id=?", (verdict.verdict_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["approved"] == 1
        assert row["ticker"] == "SPY"
        assert row["strategy"] == "ibs_etf"

    def test_rejected_verdict_persisted(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio(kill_switch_active=True)
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)

        conn = get_conn(db_path)
        row = conn.execute(
            "SELECT * FROM risk_verdicts WHERE id=?", (verdict.verdict_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["approved"] == 0
        assert row["rule_id"] == "R-001"
        assert "kill switch" in row["reason"].lower()

    def test_verdict_details_stored_as_json(self, db_path):
        gate = PreTradeRiskGate(db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal()
        verdict = gate.evaluate(proposal, portfolio)

        conn = get_conn(db_path)
        row = conn.execute(
            "SELECT details FROM risk_verdicts WHERE id=?", (verdict.verdict_id,)
        ).fetchone()
        conn.close()

        details = json.loads(row["details"])
        assert isinstance(details, list)
        assert len(details) == 12


# ─── Custom limits ──────────────────────────────────────────────────────────


class TestCustomLimits:
    """Test with customized risk limits."""

    def test_tighter_limits_catch_more(self, db_path):
        limits = RiskLimits(
            trade_max_risk_pct=0.5,           # Very tight
            trade_max_notional_pct=3.0,       # Very tight
            max_single_position_pct=2.0,      # Very tight
        )
        gate = PreTradeRiskGate(limits=limits, db_path=db_path)
        portfolio = _default_portfolio()
        proposal = _default_proposal(
            risk_amount=1_000.0,             # 1% > 0.5% limit
            notional_value=5_000.0,
        )
        verdict = gate.evaluate(proposal, portfolio)
        assert not verdict.approved
        assert verdict.rule_id == "R-005"

    def test_relaxed_limits_allow_more(self, db_path):
        limits = RiskLimits(
            trade_max_risk_pct=10.0,
            trade_max_notional_pct=50.0,
            max_single_position_pct=50.0,
            sleeve_max_allocation_pct=80.0,
            trade_min_cash_buffer_pct=5.0,
            max_sector_exposure_pct=60.0,  # Relax sector limit too
        )
        gate = PreTradeRiskGate(limits=limits, db_path=db_path)
        portfolio = _default_portfolio(fund_cash=50_000.0)
        proposal = _default_proposal(
            risk_amount=8_000.0,
            notional_value=40_000.0,
        )
        verdict = gate.evaluate(proposal, portfolio)
        assert verdict.approved

    def test_frozen_limits_immutable(self):
        limits = RiskLimits()
        with pytest.raises(AttributeError):
            limits.trade_max_risk_pct = 999


# ─── Integration: build_portfolio_snapshot from ledger ───────────────────────


class TestBuildPortfolioSnapshot:
    """Test building portfolio snapshot from ledger data."""

    def test_snapshot_with_positions_and_cash(self, db_path):
        # Set up ledger data
        acct_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100,
             "avg_cost": 450.0, "market_value": 46000.0, "sleeve": "sleeve_2",
             "strategy": "ibs_etf"},
        ], db_path=db_path)
        sync_cash_balance(acct_id, balance=30000.0, currency="USD", db_path=db_path)
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=76000.0, cash=30000.0,
            positions_value=46000.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )

        snapshot = build_portfolio_snapshot(db_path=db_path)

        assert snapshot.fund_nav == 76000.0
        assert snapshot.fund_cash == 30000.0
        assert len(snapshot.positions) == 1
        assert snapshot.positions[0]["ticker"] == "SPY"

    def test_snapshot_empty_ledger(self, db_path):
        snapshot = build_portfolio_snapshot(db_path=db_path)
        assert snapshot.fund_nav == 0.0
        assert snapshot.fund_cash == 0.0
        assert len(snapshot.positions) == 0

    def test_snapshot_peak_nav_from_history(self, db_path):
        # Save multiple NAV snapshots to establish a peak
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=100000.0,
            snapshot_date="2026-02-25",
            db_path=db_path,
        )
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=105000.0,  # Peak
            snapshot_date="2026-02-26",
            db_path=db_path,
        )
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=98000.0,   # Current (below peak)
            snapshot_date="2026-02-28",
            db_path=db_path,
        )

        snapshot = build_portfolio_snapshot(db_path=db_path)
        assert snapshot.fund_nav == 98000.0
        assert snapshot.fund_peak_nav == 105000.0


# ─── Integration: full risk gate + ledger lifecycle ──────────────────────────


class TestRiskGateLedgerIntegration:
    """Test the risk gate reading from ledger and making decisions."""

    def test_safe_trade_approved_via_ledger(self, db_path):
        # Set up healthy portfolio in ledger
        acct_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        sync_positions(acct_id, [
            {"ticker": "QQQ", "direction": "long", "quantity": 50,
             "avg_cost": 380.0, "market_value": 19500.0, "sleeve": "sleeve_2",
             "strategy": "ibs_etf"},
        ], db_path=db_path)
        sync_cash_balance(acct_id, balance=80000.0, db_path=db_path)
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=100000.0, cash=80000.0,
            positions_value=19500.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )

        # Build snapshot and evaluate
        snapshot = build_portfolio_snapshot(db_path=db_path)
        gate = PreTradeRiskGate(db_path=db_path)
        proposal = _default_proposal(
            ticker="SPY",
            notional_value=3000.0,
            risk_amount=500.0,
        )
        verdict = gate.evaluate(proposal, snapshot)
        assert verdict.approved

    def test_concentrated_position_blocked_via_ledger(self, db_path):
        # Set up portfolio with existing large SPY position
        acct_id = register_broker_account(
            broker="ibkr", account_id="DU12345", account_type="PAPER",
            db_path=db_path,
        )
        sync_positions(acct_id, [
            {"ticker": "SPY", "direction": "long", "quantity": 100,
             "avg_cost": 450.0, "market_value": 46000.0, "sleeve": "sleeve_2",
             "strategy": "ibs_etf"},
        ], db_path=db_path)
        sync_cash_balance(acct_id, balance=54000.0, db_path=db_path)
        save_nav_snapshot(
            level="fund", level_id="fund",
            net_liquidation=100000.0, cash=54000.0,
            positions_value=46000.0,
            snapshot_date="2026-02-28",
            db_path=db_path,
        )

        # Try to add more SPY — would exceed 5% concentration
        snapshot = build_portfolio_snapshot(db_path=db_path)
        gate = PreTradeRiskGate(db_path=db_path)
        proposal = _default_proposal(
            ticker="SPY",
            notional_value=5000.0,  # 46k + 5k = 51k = 51% > max 5%
            risk_amount=500.0,
        )
        verdict = gate.evaluate(proposal, snapshot)
        assert not verdict.approved
        assert verdict.rule_id == "R-007"
        assert "SPY" in verdict.reason
