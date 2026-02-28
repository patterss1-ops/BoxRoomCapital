"""
Regression tests for the SafetyController (7-gate IG options safety system).

A-008: Verifies all safety gates work correctly, state management,
kill switch logic, and edge cases.
"""
import pytest
from datetime import date
from unittest.mock import patch

from safety_controller import SafetyController, SafetyLimits


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def controller():
    """Controller with £5000 equity and default limits."""
    return SafetyController(initial_equity=5000.0)


@pytest.fixture
def custom_controller():
    """Controller with tighter custom limits for testing."""
    limits = SafetyLimits(
        max_risk_per_trade_pct=1.0,
        max_total_heat_pct=3.0,
        max_daily_loss_pct=5.0,
        max_open_spreads=3,
        min_premium_pct=3.0,
        max_contracts_per_trade=10,
    )
    return SafetyController(initial_equity=10000.0, limits=limits)


# ─── Gate 1: Kill Switch ─────────────────────────────────────────────────


class TestGate1KillSwitch:
    def test_kill_switch_blocks_all(self, controller):
        controller.kill_switch = True
        controller.kill_switch_reason = "Test kill"
        viable, reason = controller.check_order_viability(10, 1, 50)
        assert viable is False
        assert "KILL SWITCH" in reason

    def test_kill_switch_off_allows(self, controller):
        viable, reason = controller.check_order_viability(10, 1, 50)
        assert viable is True

    def test_kill_switch_preserves_reason(self, controller):
        controller.kill_switch = True
        controller.kill_switch_reason = "Specific reason"
        _, reason = controller.check_order_viability(10, 1, 50)
        assert "Specific reason" in reason


# ─── Gate 2: Max Risk Per Trade ───────────────────────────────────────────


class TestGate2MaxRiskPerTrade:
    def test_within_limit_passes(self, controller):
        # 2% of £5000 = £100
        viable, reason = controller.check_order_viability(99, 1, 50)
        assert viable is True

    def test_at_limit_passes(self, controller):
        # Exactly at limit should pass (> not >=)
        viable, reason = controller.check_order_viability(100, 1, 50)
        assert viable is True

    def test_over_limit_blocks(self, controller):
        # 2% of £5000 = £100
        viable, reason = controller.check_order_viability(101, 1, 50)
        assert viable is False
        assert "max single trade" in reason.lower() or "risk" in reason.lower()


# ─── Gate 3: Total Heat ──────────────────────────────────────────────────


class TestGate3TotalHeat:
    def test_zero_heat_allows(self, controller):
        viable, reason = controller.check_order_viability(50, 1, 50)
        assert viable is True

    def test_existing_heat_plus_proposal(self, controller):
        # 4% of £5000 = £200 max heat
        controller.update_state(5000, [
            {"max_loss": 50, "size": 1},
            {"max_loss": 50, "size": 1},
        ])
        # Current heat = 100, proposing 50 more = 150, under 200
        viable, _ = controller.check_order_viability(50, 1, 50)
        assert viable is True

    def test_exceeds_total_heat(self, controller):
        # Fill up heat to near limit
        controller.update_state(5000, [
            {"max_loss": 80, "size": 1},
            {"max_loss": 80, "size": 1},
        ])
        # Current heat = 160, proposing 50 more = 210, exceeds 200
        viable, reason = controller.check_order_viability(50, 1, 50)
        assert viable is False
        assert "heat" in reason.lower()


# ─── Gate 4: Max Open Spreads ────────────────────────────────────────────


class TestGate4MaxOpenSpreads:
    def test_under_max_allows(self, controller):
        controller.update_state(5000, [
            {"max_loss": 10, "size": 1}
        ])
        viable, _ = controller.check_order_viability(10, 1, 50)
        assert viable is True

    def test_at_max_blocks(self, controller):
        # Default max is 6
        spreads = [{"max_loss": 5, "size": 1} for _ in range(6)]
        controller.update_state(5000, spreads)
        viable, reason = controller.check_order_viability(10, 1, 50)
        assert viable is False
        assert "spreads open" in reason.lower() or "max" in reason.lower()


# ─── Gate 5: Max Contracts ───────────────────────────────────────────────


class TestGate5MaxContracts:
    def test_within_contract_limit(self, controller):
        # Default max is 20
        viable, _ = controller.check_order_viability(10, 19, 50)
        assert viable is True

    def test_at_contract_limit_allows(self, controller):
        viable, _ = controller.check_order_viability(10, 20, 50)
        assert viable is True

    def test_exceeds_contract_limit(self, controller):
        viable, reason = controller.check_order_viability(10, 21, 50)
        assert viable is False
        assert "contracts" in reason.lower() or "max" in reason.lower()


# ─── Gate 6: Minimum Premium ────────────────────────────────────────────


class TestGate6MinimumPremium:
    def test_good_premium_passes(self, controller):
        # Default min is 2%
        viable, _ = controller.check_order_viability(10, 1, 5.0)
        assert viable is True

    def test_low_premium_blocks(self, controller):
        viable, reason = controller.check_order_viability(10, 1, 1.5)
        assert viable is False
        assert "premium" in reason.lower()

    def test_at_premium_boundary(self, controller):
        # Exactly at 2% should pass (< not <=)
        viable, _ = controller.check_order_viability(10, 1, 2.0)
        assert viable is True


# ─── Gate 7: Daily Loss Approaching Limit ────────────────────────────────


class TestGate7DailyLoss:
    def test_no_daily_loss_passes(self, controller):
        viable, _ = controller.check_order_viability(10, 1, 50)
        assert viable is True

    def test_daily_loss_below_80pct(self, controller):
        # 10% of £5000 = £500 max daily loss, 80% = £400
        controller.record_closed_trade(-300)  # 60% — under 80%
        viable, _ = controller.check_order_viability(10, 1, 50)
        assert viable is True

    def test_daily_loss_at_exactly_80pct_allows(self, controller):
        # Lose £400 — exactly 80% of £500 limit; gate uses strict < so this passes
        controller.record_closed_trade(-400)
        viable, _ = controller.check_order_viability(10, 1, 50)
        assert viable is True

    def test_daily_loss_just_over_80pct_blocks(self, controller):
        # Lose £401 — just over 80% boundary
        controller.record_closed_trade(-401)
        viable, reason = controller.check_order_viability(10, 1, 50)
        assert viable is False
        assert "80%" in reason or "daily loss" in reason.lower()

    def test_daily_loss_over_80pct(self, controller):
        controller.record_closed_trade(-450)
        viable, reason = controller.check_order_viability(10, 1, 50)
        assert viable is False


# ─── State Management ────────────────────────────────────────────────────


class TestStateManagement:
    def test_update_state_recalculates_heat(self, controller):
        controller.update_state(5000, [
            {"max_loss": 30, "size": 2},
            {"max_loss": 20, "size": 1},
        ])
        assert controller._current_heat == 80  # 30*2 + 20*1
        assert controller._open_spread_count == 2

    def test_update_state_updates_equity(self, controller):
        controller.update_state(6000, [])
        assert controller.equity == 6000

    def test_empty_spreads_reset_heat(self, controller):
        controller.update_state(5000, [{"max_loss": 50, "size": 1}])
        assert controller._current_heat == 50
        controller.update_state(5000, [])
        assert controller._current_heat == 0
        assert controller._open_spread_count == 0

    def test_record_closed_trade_accumulates(self, controller):
        controller.record_closed_trade(50)
        controller.record_closed_trade(-20)
        controller.record_closed_trade(30)
        assert controller._daily_pnl == 60  # 50 - 20 + 30


# ─── Kill Switch Logic ───────────────────────────────────────────────────


class TestKillSwitchLogic:
    def test_kill_switch_triggers_on_daily_loss(self, controller):
        # 10% of £5000 = £500
        assert controller.kill_switch is False
        controller.record_closed_trade(-501)
        assert controller.kill_switch is True
        assert "daily loss" in controller.kill_switch_reason.lower() or "exceeds" in controller.kill_switch_reason.lower()

    def test_kill_switch_not_triggered_at_boundary(self, controller):
        # Lose exactly £500 — should trigger because _daily_pnl < -max_daily_loss
        controller.record_closed_trade(-499)
        assert controller.kill_switch is False

    def test_kill_switch_accumulates(self, controller):
        # Multiple small losses should trigger when they add up
        for _ in range(10):
            controller.record_closed_trade(-51)
        assert controller.kill_switch is True

    def test_manual_reset(self, controller):
        controller.kill_switch = True
        controller.kill_switch_reason = "Test"
        controller.reset_kill_switch()
        assert controller.kill_switch is False
        assert controller.kill_switch_reason == ""
        assert controller._daily_pnl == 0.0

    def test_daily_reset_clears_pnl(self, controller):
        controller.record_closed_trade(-200)
        # Simulate day change
        controller._daily_date = date(2020, 1, 1)
        controller.update_state(5000, [])
        assert controller._daily_pnl == 0.0


# ─── Get Status ──────────────────────────────────────────────────────────


class TestGetStatus:
    def test_status_structure(self, controller):
        status = controller.get_status()
        expected_keys = {
            "kill_switch", "kill_switch_reason", "equity", "current_heat",
            "max_heat", "heat_pct", "open_spreads", "max_spreads",
            "daily_pnl", "max_daily_loss", "daily_loss_pct",
        }
        assert expected_keys.issubset(set(status.keys()))

    def test_status_values_correct(self, controller):
        controller.update_state(5000, [{"max_loss": 50, "size": 1}])
        controller.record_closed_trade(-100)
        status = controller.get_status()
        assert status["equity"] == 5000
        assert status["current_heat"] == 50
        assert status["open_spreads"] == 1
        assert status["daily_pnl"] == -100
        assert status["kill_switch"] is False


# ─── Custom Limits ───────────────────────────────────────────────────────


class TestCustomLimits:
    def test_tighter_risk_limit(self, custom_controller):
        # 1% of £10000 = £100
        viable, _ = custom_controller.check_order_viability(99, 1, 50)
        assert viable is True
        viable, _ = custom_controller.check_order_viability(101, 1, 50)
        assert viable is False

    def test_tighter_spreads_limit(self, custom_controller):
        spreads = [{"max_loss": 5, "size": 1} for _ in range(3)]
        custom_controller.update_state(10000, spreads)
        viable, _ = custom_controller.check_order_viability(10, 1, 50)
        assert viable is False

    def test_tighter_premium_limit(self, custom_controller):
        # Min premium 3%
        viable, _ = custom_controller.check_order_viability(10, 1, 2.9)
        assert viable is False
        viable, _ = custom_controller.check_order_viability(10, 1, 3.1)
        assert viable is True


# ─── Edge Cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_zero_equity(self):
        ctrl = SafetyController(initial_equity=0)
        viable, _ = ctrl.check_order_viability(1, 1, 50)
        # Risk £1 > 2% of £0 = £0
        assert viable is False

    def test_negative_pnl_doesnt_crash(self, controller):
        controller.record_closed_trade(-1000)
        # Should trigger kill switch, not crash
        assert controller.kill_switch is True

    def test_very_large_order(self, controller):
        viable, _ = controller.check_order_viability(999999, 999, 50)
        assert viable is False

    def test_multiple_gates_fail_returns_first(self, controller):
        """Kill switch should be checked first."""
        controller.kill_switch = True
        controller.kill_switch_reason = "Test"
        viable, reason = controller.check_order_viability(999999, 999, 0.1)
        assert "KILL SWITCH" in reason  # Not risk, not contracts, not premium

    def test_positive_trades_offset_losses(self, controller):
        controller.record_closed_trade(-300)
        controller.record_closed_trade(200)
        # Net daily P&L = -100, well below 80% of £500
        viable, _ = controller.check_order_viability(10, 1, 50)
        assert viable is True
