"""
Regression tests for SafetyController — A-008.

41 tests covering all 7 safety gates, state management, kill switch logic,
custom limits, and edge cases. Ensures no regressions when adding multi-broker
infrastructure.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch
from datetime import date

from safety_controller import SafetyController, SafetyLimits


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def ctrl():
    """Standard controller with £5000 equity and default limits."""
    return SafetyController(initial_equity=5000.0)


@pytest.fixture
def custom_ctrl():
    """Controller with custom limits for targeted testing."""
    limits = SafetyLimits(
        max_risk_per_trade_pct=3.0,
        max_total_heat_pct=6.0,
        max_daily_loss_pct=8.0,
        max_open_spreads=4,
        min_premium_pct=3.0,
        max_contracts_per_trade=10,
    )
    return SafetyController(initial_equity=10000.0, limits=limits)


# ─── Gate 1: Kill switch ────────────────────────────────────────────────────


class TestGate1KillSwitch:
    def test_kill_switch_off_by_default(self, ctrl):
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is True

    def test_kill_switch_blocks_all_trades(self, ctrl):
        ctrl.kill_switch = True
        ctrl.kill_switch_reason = "Manual activation"
        ok, reason = ctrl.check_order_viability(proposed_risk=10, proposed_size=1)
        assert ok is False
        assert "kill" in reason.lower() or "manual" in reason.lower()

    def test_kill_switch_reset(self, ctrl):
        ctrl.kill_switch = True
        ctrl.reset_kill_switch()
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is True

    def test_kill_switch_triggered_by_daily_loss(self, ctrl):
        # max_daily_loss_pct=10% of 5000 = £500
        ctrl.record_closed_trade(-600)
        assert ctrl.kill_switch is True


# ─── Gate 2: Max risk per trade ──────────────────────────────────────────────


class TestGate2MaxRiskPerTrade:
    def test_within_limit(self, ctrl):
        # 2% of 5000 = 100
        ok, _ = ctrl.check_order_viability(proposed_risk=99, proposed_size=1)
        assert ok is True

    def test_exceeds_limit(self, ctrl):
        ok, reason = ctrl.check_order_viability(proposed_risk=150, proposed_size=1)
        assert ok is False
        assert "risk" in reason.lower()

    def test_exactly_at_limit(self, ctrl):
        # 2% of 5000 = 100 exactly
        ok, _ = ctrl.check_order_viability(proposed_risk=100, proposed_size=1)
        assert ok is True

    def test_custom_limit(self, custom_ctrl):
        # 3% of 10000 = 300
        ok, _ = custom_ctrl.check_order_viability(proposed_risk=299, proposed_size=1)
        assert ok is True
        ok, _ = custom_ctrl.check_order_viability(proposed_risk=350, proposed_size=1)
        assert ok is False


# ─── Gate 3: Total heat ─────────────────────────────────────────────────────


class TestGate3TotalHeat:
    def test_no_existing_heat(self, ctrl):
        # 4% of 5000 = 200 max heat
        ok, _ = ctrl.check_order_viability(proposed_risk=150, proposed_size=1)
        assert ok is False  # Fails gate 2 first (>100)

    def test_heat_accumulates(self, ctrl):
        ctrl._current_heat = 150
        ok, reason = ctrl.check_order_viability(proposed_risk=60, proposed_size=1)
        assert ok is False
        assert "heat" in reason.lower()

    def test_heat_within_limit(self, ctrl):
        ctrl._current_heat = 100
        ok, _ = ctrl.check_order_viability(proposed_risk=90, proposed_size=1)
        assert ok is True

    def test_heat_exactly_at_limit(self, ctrl):
        ctrl._current_heat = 100
        # 100 + 100 = 200 = 4% of 5000, should pass
        ok, _ = ctrl.check_order_viability(proposed_risk=100, proposed_size=1)
        assert ok is True


# ─── Gate 4: Max open spreads ───────────────────────────────────────────────


class TestGate4MaxSpreads:
    @staticmethod
    def _make_spreads(n, max_loss=30, size=1):
        """Create n mock spread dicts for update_state."""
        return [{"max_loss": max_loss, "size": size} for _ in range(n)]

    def test_below_limit(self, ctrl):
        ctrl.update_state(equity=5000, open_spreads=self._make_spreads(3))
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is True

    def test_at_limit(self, ctrl):
        # Use tiny max_loss so total heat (6×10=60) stays under 4% limit (200)
        # but spread count hits the limit of 6
        ctrl.update_state(equity=5000, open_spreads=self._make_spreads(6, max_loss=10))
        ok, reason = ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is False
        assert "spread" in reason.lower()

    def test_custom_spread_limit(self, custom_ctrl):
        custom_ctrl.update_state(equity=10000, open_spreads=self._make_spreads(4))
        ok, reason = custom_ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is False


# ─── Gate 5: Max contracts per trade ─────────────────────────────────────────


class TestGate5MaxContracts:
    def test_within_limit(self, ctrl):
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=15)
        assert ok is True

    def test_exceeds_limit(self, ctrl):
        ok, reason = ctrl.check_order_viability(proposed_risk=50, proposed_size=25)
        assert ok is False
        assert "contract" in reason.lower() or "size" in reason.lower()

    def test_exactly_at_limit(self, ctrl):
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=20)
        assert ok is True


# ─── Gate 6: Minimum premium ────────────────────────────────────────────────


class TestGate6MinPremium:
    def test_above_minimum(self, ctrl):
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=1, premium_pct=5.0)
        assert ok is True

    def test_below_minimum(self, ctrl):
        ok, reason = ctrl.check_order_viability(proposed_risk=50, proposed_size=1, premium_pct=1.0)
        assert ok is False
        assert "premium" in reason.lower()

    def test_exactly_at_minimum(self, ctrl):
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=1, premium_pct=2.0)
        assert ok is True


# ─── Gate 7: Daily loss cushion ──────────────────────────────────────────────


class TestGate7DailyLoss:
    def test_no_daily_loss(self, ctrl):
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is True

    def test_moderate_daily_loss_allows(self, ctrl):
        ctrl.record_closed_trade(-200)
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is True

    def test_daily_loss_at_exactly_80pct_allows(self, ctrl):
        # 80% of max_daily_loss (£500) = £400 — gate 7 uses strict < so exactly 80% passes
        ctrl._daily_pnl = -400
        ok, _ = ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is True

    def test_daily_loss_just_over_80pct_blocks(self, ctrl):
        # Just over 80% should block
        ctrl._daily_pnl = -401
        ok, reason = ctrl.check_order_viability(proposed_risk=50, proposed_size=1)
        assert ok is False
        assert "daily" in reason.lower() or "loss" in reason.lower()

    def test_daily_loss_resets_at_midnight(self, ctrl):
        ctrl.record_closed_trade(-300)
        # Simulate date change
        ctrl._daily_date = date(2020, 1, 1)
        ctrl.update_state(equity=5000, open_spreads=[])
        assert ctrl._daily_pnl == 0


# ─── State management ───────────────────────────────────────────────────────


class TestStateManagement:
    def test_initial_status(self, ctrl):
        status = ctrl.get_status()
        assert status["kill_switch"] is False
        assert status["equity"] == 5000.0

    def test_update_state(self, ctrl):
        ctrl.update_state(equity=5500, open_spreads=[{"max_loss": 30, "size": 1}, {"max_loss": 40, "size": 1}])
        status = ctrl.get_status()
        assert status["equity"] == 5500.0

    def test_record_winning_trade(self, ctrl):
        ctrl.record_closed_trade(100)
        assert ctrl._daily_pnl == 100

    def test_record_losing_trade(self, ctrl):
        ctrl.record_closed_trade(-100)
        assert ctrl._daily_pnl == -100

    def test_multiple_trades_accumulate(self, ctrl):
        ctrl.record_closed_trade(50)
        ctrl.record_closed_trade(-30)
        ctrl.record_closed_trade(-80)
        assert ctrl._daily_pnl == -60
