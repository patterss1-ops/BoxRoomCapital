"""Tests for H-002 sleeve-drift rebalance planning."""

from __future__ import annotations

import pytest

from portfolio.rebalance import (
    DriftPlanner,
    build_rebalance_plan,
    build_rebalance_intents,
    default_target_weights_from_slots,
    run_rebalance_drift_check,
)


def test_default_target_weights_from_slots_equal_across_enabled_sleeves():
    slots = [
        {"id": "a", "sleeve": "core", "enabled": True},
        {"id": "b", "sleeve": "sat", "enabled": True},
        {"id": "c", "sleeve": "core", "enabled": True},
        {"id": "d", "sleeve": "ignored", "enabled": False},
    ]
    weights = default_target_weights_from_slots(slots)
    assert weights == {"core": 0.5, "sat": 0.5}


def test_build_rebalance_plan_flags_actions_above_threshold():
    plan = build_rebalance_plan(
        current_nav_by_sleeve={"core": 600.0, "sat": 400.0},
        target_weight_by_sleeve={"core": 0.5, "sat": 0.5},
        drift_threshold_pct=5.0,
        min_trade_notional=50.0,
        report_date="2026-03-03",
        generated_at="2026-03-03T11:55:00Z",
    )
    assert plan.total_nav == 1000.0
    assert plan.requires_rebalance is True

    by_sleeve = {action.sleeve: action for action in plan.actions}
    assert by_sleeve["core"].action == "SELL"
    assert by_sleeve["core"].delta_nav == pytest.approx(-100.0)
    assert by_sleeve["core"].drift_pct == pytest.approx(10.0)
    assert by_sleeve["core"].exceeds_threshold is True

    assert by_sleeve["sat"].action == "BUY"
    assert by_sleeve["sat"].delta_nav == pytest.approx(100.0)
    assert by_sleeve["sat"].drift_pct == pytest.approx(-10.0)
    assert by_sleeve["sat"].exceeds_threshold is True


def test_build_rebalance_plan_respects_drift_and_notional_thresholds():
    plan = build_rebalance_plan(
        current_nav_by_sleeve={"core": 510.0, "sat": 490.0},
        target_weight_by_sleeve={"core": 0.5, "sat": 0.5},
        drift_threshold_pct=5.0,
        min_trade_notional=100.0,
    )
    assert plan.requires_rebalance is False
    assert all(not action.exceeds_threshold for action in plan.actions)


def test_build_rebalance_plan_handles_new_and_legacy_sleeves():
    plan = build_rebalance_plan(
        current_nav_by_sleeve={"legacy": 1000.0},
        target_weight_by_sleeve={"new": 1.0},
        drift_threshold_pct=1.0,
        min_trade_notional=1.0,
    )
    by_sleeve = {action.sleeve: action for action in plan.actions}
    assert by_sleeve["legacy"].action == "SELL"
    assert by_sleeve["legacy"].delta_nav == pytest.approx(-1000.0)
    assert by_sleeve["new"].action == "BUY"
    assert by_sleeve["new"].delta_nav == pytest.approx(1000.0)
    assert plan.requires_rebalance is True


def test_run_rebalance_drift_check_uses_latest_report_snapshot(monkeypatch):
    rows = [
        {"report_date": "2026-03-03", "sleeve": "core", "nav": 700.0},
        {"report_date": "2026-03-03", "sleeve": "sat", "nav": 300.0},
        {"report_date": "2026-03-02", "sleeve": "core", "nav": 600.0},
    ]

    def fake_get_sleeve_daily_reports(days: int, db_path: str):
        assert days == 120
        assert db_path == "test.db"
        return rows

    monkeypatch.setattr(
        "portfolio.rebalance.get_sleeve_daily_reports",
        fake_get_sleeve_daily_reports,
    )

    plan = run_rebalance_drift_check(
        target_weight_by_sleeve={"core": 0.5, "sat": 0.5},
        drift_threshold_pct=5.0,
        min_trade_notional=1.0,
        db_path="test.db",
    )
    assert plan.report_date == "2026-03-03"
    assert plan.total_nav == pytest.approx(1000.0)
    assert plan.requires_rebalance is True


def test_build_rebalance_plan_rejects_negative_target_weight():
    with pytest.raises(ValueError, match="target weights must be >= 0"):
        build_rebalance_plan(
            current_nav_by_sleeve={"core": 100.0},
            target_weight_by_sleeve={"core": -1.0},
        )


def test_build_rebalance_intents_only_includes_threshold_breaches():
    plan = build_rebalance_plan(
        current_nav_by_sleeve={"core": 620.0, "sat": 380.0},
        target_weight_by_sleeve={"core": 0.5, "sat": 0.5},
        drift_threshold_pct=5.0,
        min_trade_notional=25.0,
    )
    intents = build_rebalance_intents(plan)
    assert len(intents) == 2

    by_sleeve = {intent.sleeve: intent for intent in intents}
    assert by_sleeve["core"].side == "SELL"
    assert by_sleeve["core"].notional == pytest.approx(120.0)
    assert by_sleeve["sat"].side == "BUY"
    assert by_sleeve["sat"].notional == pytest.approx(120.0)


def test_rebalance_plan_payload_includes_intents():
    plan = build_rebalance_plan(
        current_nav_by_sleeve={"core": 600.0, "sat": 400.0},
        target_weight_by_sleeve={"core": 0.5, "sat": 0.5},
        drift_threshold_pct=5.0,
        min_trade_notional=50.0,
    )
    payload = plan.to_dict()
    assert payload["rebalance_intents_count"] == 2
    assert len(payload["rebalance_intents"]) == 2
    assert payload["rebalance_intents"][0]["side"] in {"BUY", "SELL"}


def test_drift_planner_returns_plan_and_intents(monkeypatch):
    rows = [
        {"report_date": "2026-03-03", "sleeve": "core", "nav": 700.0},
        {"report_date": "2026-03-03", "sleeve": "sat", "nav": 300.0},
    ]

    def fake_get_sleeve_daily_reports(days: int, db_path: str):
        assert db_path == "test.db"
        return rows

    monkeypatch.setattr(
        "portfolio.rebalance.get_sleeve_daily_reports",
        fake_get_sleeve_daily_reports,
    )

    planner = DriftPlanner(
        target_weight_by_sleeve={"core": 0.5, "sat": 0.5},
        drift_threshold_pct=5.0,
        min_trade_notional=1.0,
        db_path="test.db",
    )
    plan = planner.check()
    intents = planner.intents()

    assert plan.requires_rebalance is True
    assert len(intents) == 2
