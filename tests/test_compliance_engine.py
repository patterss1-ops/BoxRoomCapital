"""Tests for M-006 compliance rule engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from risk.compliance_engine import ComplianceEngine, ComplianceRuleConfig


def _base_order(**overrides):
    base = {"symbol": "AAPL", "qty": 100, "price": 100.0, "side": "buy"}
    base.update(overrides)
    return base


def test_pre_trade_allows_valid_order():
    engine = ComplianceEngine(
        ComplianceRuleConfig(
            allowed_symbols={"AAPL", "MSFT"},
            blocked_symbols={"GME"},
            max_order_notional=50_000,
            max_daily_trades=10,
            max_position_notional=200_000,
        )
    )
    decision = engine.evaluate_pre_trade(
        _base_order(),
        context={"daily_trade_count": 2, "projected_position_notional": 120_000},
    )
    assert decision.allowed is True
    assert decision.violations == []


def test_pre_trade_blocks_disallowed_symbol():
    engine = ComplianceEngine(ComplianceRuleConfig(allowed_symbols={"MSFT"}))
    decision = engine.evaluate_pre_trade(_base_order(symbol="AAPL"))
    assert decision.allowed is False
    assert any(v.code == "SYMBOL_NOT_ALLOWED" for v in decision.violations)


def test_pre_trade_blocks_blocked_symbol():
    engine = ComplianceEngine(ComplianceRuleConfig(blocked_symbols={"AAPL"}))
    decision = engine.evaluate_pre_trade(_base_order(symbol="AAPL"))
    assert decision.allowed is False
    assert any(v.code == "SYMBOL_BLOCKED" for v in decision.violations)


def test_pre_trade_blocks_notional_limit():
    engine = ComplianceEngine(ComplianceRuleConfig(max_order_notional=5_000))
    decision = engine.evaluate_pre_trade(_base_order(qty=100, price=100))
    assert decision.allowed is False
    assert any(v.code == "MAX_ORDER_NOTIONAL_EXCEEDED" for v in decision.violations)


def test_pre_trade_blocks_daily_trade_limit():
    engine = ComplianceEngine(ComplianceRuleConfig(max_daily_trades=3))
    decision = engine.evaluate_pre_trade(
        _base_order(),
        context={"daily_trade_count": 3},
    )
    assert decision.allowed is False
    assert any(v.code == "MAX_DAILY_TRADES_EXCEEDED" for v in decision.violations)


def test_pre_trade_blocks_position_limit():
    engine = ComplianceEngine(ComplianceRuleConfig(max_position_notional=100_000))
    decision = engine.evaluate_pre_trade(
        _base_order(),
        context={"projected_position_notional": 200_000},
    )
    assert decision.allowed is False
    assert any(v.code == "MAX_POSITION_NOTIONAL_EXCEEDED" for v in decision.violations)


def test_post_trade_wash_trade_risk_is_warning_only():
    engine = ComplianceEngine(ComplianceRuleConfig(wash_trade_cooldown_seconds=300))
    now = datetime.now(timezone.utc)
    decision = engine.evaluate_post_trade(
        {"symbol": "AAPL", "side": "buy", "fill_ts": now.isoformat()},
        context={
            "recent_fills": [
                {"symbol": "AAPL", "side": "sell", "fill_ts": (now - timedelta(seconds=60)).isoformat()}
            ]
        },
    )
    assert decision.allowed is True
    assert any(v.code == "WASH_TRADE_RISK" for v in decision.violations)
    assert all(v.severity in {"warning", "error"} for v in decision.violations)


def test_breach_report_contains_only_error_decisions():
    engine = ComplianceEngine(ComplianceRuleConfig(max_order_notional=1_000))
    good = engine.evaluate_pre_trade(_base_order(qty=1, price=10))
    bad = engine.evaluate_pre_trade(_base_order(qty=100, price=100))
    report = engine.breach_report()
    assert good.allowed is True
    assert bad.allowed is False
    assert len(report) == 1
    assert report[0]["audit_id"] == bad.audit_id
    assert "MAX_ORDER_NOTIONAL_EXCEEDED" in report[0]["violation_codes"]


def test_audit_events_are_recorded_for_all_checks():
    engine = ComplianceEngine(ComplianceRuleConfig(max_order_notional=10_000))
    engine.evaluate_pre_trade(_base_order(qty=1, price=10))
    engine.evaluate_post_trade({"symbol": "AAPL", "side": "sell", "fill_ts": datetime.now(timezone.utc).isoformat()})
    events = engine.audit_events
    assert len(events) == 2
    assert events[0].phase == "pre_trade"
    assert events[1].phase == "post_trade"
