"""Tests for A-006 hard pre-trade risk gate."""

from execution.risk_gate import (
    RiskGateRejectedError,
    enforce_pre_trade_risk,
    submit_with_risk_gate,
)
from risk.pre_trade_gate import RiskContext, RiskLimits, RiskOrderRequest, evaluate_pre_trade_risk


def _limits() -> RiskLimits:
    return RiskLimits(
        max_position_pct_equity=15.0,
        max_sleeve_pct_equity=35.0,
        max_correlated_pct_equity=45.0,
    )


def test_kill_switch_and_cooldown_reject_with_explicit_rule_ids():
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options_income",
        order_exposure_notional=1000.0,
        actor="system",
    )

    kill_ctx = RiskContext(
        equity=10000.0,
        kill_switch_active=True,
        kill_switch_reason="manual stop",
    )
    kill_decision = evaluate_pre_trade_risk(request, kill_ctx, _limits())
    assert not kill_decision.approved
    assert kill_decision.rule_id == "KILL_SWITCH_ACTIVE"

    cooldown_ctx = RiskContext(
        equity=10000.0,
        cooldown_tickers={"spy"},
    )
    cooldown_decision = evaluate_pre_trade_risk(request, cooldown_ctx, _limits())
    assert not cooldown_decision.approved
    assert cooldown_decision.rule_id == "MARKET_COOLDOWN_ACTIVE"


def test_position_sleeve_and_correlated_hard_limits():
    position_request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options_income",
        order_exposure_notional=2000.0,
        correlated_group="us_equity_beta",
    )

    position_ctx = RiskContext(
        equity=10000.0,
        ticker_exposure_notional={"SPY": 1000.0},
    )
    position_decision = evaluate_pre_trade_risk(position_request, position_ctx, _limits())
    assert not position_decision.approved
    assert position_decision.rule_id == "MAX_POSITION_PCT_EQUITY"
    assert position_decision.observed_pct == 30.0
    assert position_decision.threshold_pct == 15.0

    sleeve_request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options_income",
        order_exposure_notional=1000.0,
        correlated_group="us_equity_beta",
    )
    sleeve_ctx = RiskContext(
        equity=10000.0,
        ticker_exposure_notional={"SPY": 0.0},
        sleeve_exposure_notional={"options_income": 2600.0},
    )
    sleeve_decision = evaluate_pre_trade_risk(sleeve_request, sleeve_ctx, _limits())
    assert not sleeve_decision.approved
    assert sleeve_decision.rule_id == "MAX_SLEEVE_PCT_EQUITY"
    assert sleeve_decision.observed_pct == 36.0
    assert sleeve_decision.threshold_pct == 35.0

    correlated_request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options_income",
        order_exposure_notional=1000.0,
        correlated_group="us_equity_beta",
    )
    corr_ctx = RiskContext(
        equity=10000.0,
        ticker_exposure_notional={"SPY": 0.0},
        sleeve_exposure_notional={"options_income": 1000.0},
        correlated_exposure_notional={"us_equity_beta": 3600.0},
    )
    corr_decision = evaluate_pre_trade_risk(correlated_request, corr_ctx, _limits())
    assert not corr_decision.approved
    assert corr_decision.rule_id == "MAX_CORRELATED_PCT_EQUITY"
    assert corr_decision.observed_pct == 46.0
    assert corr_decision.threshold_pct == 45.0


def test_enforce_pre_trade_risk_raises_and_carries_decision():
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options_income",
        order_exposure_notional=2000.0,
    )
    context = RiskContext(
        equity=10000.0,
        ticker_exposure_notional={"SPY": 1000.0},
    )

    try:
        enforce_pre_trade_risk(request=request, context=context, limits=_limits())
        assert False, "Expected RiskGateRejectedError"
    except RiskGateRejectedError as exc:
        assert exc.decision.rule_id == "MAX_POSITION_PCT_EQUITY"


def test_submit_with_risk_gate_blocks_broker_call_on_rejection():
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options_income",
        order_exposure_notional=2000.0,
    )
    context = RiskContext(
        equity=10000.0,
        ticker_exposure_notional={"SPY": 1000.0},
    )
    calls = {"submit": 0}

    def submit_fn():
        calls["submit"] += 1
        return {"order_id": "should-not-run"}

    result = submit_with_risk_gate(
        request=request,
        context=context,
        limits=_limits(),
        submit_fn=submit_fn,
    )

    assert result.submitted is False
    assert result.broker_result is None
    assert result.decision.rule_id == "MAX_POSITION_PCT_EQUITY"
    assert calls["submit"] == 0


def test_submit_with_risk_gate_calls_broker_on_approval():
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options_income",
        order_exposure_notional=500.0,
        correlated_group="us_equity_beta",
    )
    context = RiskContext(
        equity=10000.0,
        ticker_exposure_notional={"SPY": 500.0},
        sleeve_exposure_notional={"options_income": 1000.0},
        correlated_exposure_notional={"us_equity_beta": 2000.0},
    )
    calls = {"submit": 0}

    def submit_fn():
        calls["submit"] += 1
        return {"order_id": "OK-123"}

    result = submit_with_risk_gate(
        request=request,
        context=context,
        limits=_limits(),
        submit_fn=submit_fn,
    )

    assert result.submitted is True
    assert result.decision.approved is True
    assert result.decision.rule_id == "APPROVED"
    assert result.broker_result == {"order_id": "OK-123"}
    assert calls["submit"] == 1
