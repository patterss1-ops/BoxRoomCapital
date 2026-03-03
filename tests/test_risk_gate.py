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


def test_approved_when_all_limits_within_bounds():
    request = RiskOrderRequest(
        ticker="AAPL",
        sleeve="core",
        order_exposure_notional=100.0,
        correlated_group="tech",
    )
    context = RiskContext(
        equity=100000.0,
        ticker_exposure_notional={"AAPL": 0.0},
        sleeve_exposure_notional={"core": 0.0},
        correlated_exposure_notional={"tech": 0.0},
    )
    decision = evaluate_pre_trade_risk(request, context, _limits())
    assert decision.approved is True
    assert decision.rule_id == "APPROVED"


def test_position_exactly_at_limit_is_rejected():
    limits = RiskLimits(
        max_position_pct_equity=10.0,
        max_sleeve_pct_equity=50.0,
        max_correlated_pct_equity=60.0,
    )
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="core",
        order_exposure_notional=500.0,
    )
    context = RiskContext(
        equity=5000.0,
        ticker_exposure_notional={"SPY": 0.0},
    )
    decision = evaluate_pre_trade_risk(request, context, limits)
    assert decision.approved is True
    assert decision.observed_pct == 0.0


def test_position_just_over_limit_rejected():
    limits = RiskLimits(
        max_position_pct_equity=10.0,
        max_sleeve_pct_equity=50.0,
        max_correlated_pct_equity=60.0,
    )
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="core",
        order_exposure_notional=500.01,
    )
    context = RiskContext(
        equity=5000.0,
        ticker_exposure_notional={"SPY": 0.0},
    )
    decision = evaluate_pre_trade_risk(request, context, limits)
    assert not decision.approved
    assert decision.rule_id == "MAX_POSITION_PCT_EQUITY"


def test_sleeve_exactly_at_limit_passes():
    limits = RiskLimits(
        max_position_pct_equity=50.0,
        max_sleeve_pct_equity=10.0,
        max_correlated_pct_equity=60.0,
    )
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options",
        order_exposure_notional=500.0,
    )
    context = RiskContext(
        equity=5000.0,
        sleeve_exposure_notional={"options": 0.0},
    )
    decision = evaluate_pre_trade_risk(request, context, limits)
    assert decision.approved is True


def test_sleeve_just_over_limit_rejected():
    limits = RiskLimits(
        max_position_pct_equity=50.0,
        max_sleeve_pct_equity=10.0,
        max_correlated_pct_equity=60.0,
    )
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="options",
        order_exposure_notional=500.01,
    )
    context = RiskContext(
        equity=5000.0,
        sleeve_exposure_notional={"options": 0.0},
    )
    decision = evaluate_pre_trade_risk(request, context, limits)
    assert not decision.approved
    assert decision.rule_id == "MAX_SLEEVE_PCT_EQUITY"


def test_correlated_just_over_limit_rejected():
    limits = RiskLimits(
        max_position_pct_equity=50.0,
        max_sleeve_pct_equity=50.0,
        max_correlated_pct_equity=10.0,
    )
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="core",
        order_exposure_notional=500.01,
        correlated_group="equity_beta",
    )
    context = RiskContext(
        equity=5000.0,
        correlated_exposure_notional={"equity_beta": 0.0},
    )
    decision = evaluate_pre_trade_risk(request, context, limits)
    assert not decision.approved
    assert decision.rule_id == "MAX_CORRELATED_PCT_EQUITY"


def test_no_correlated_group_skips_correlation_check():
    limits = RiskLimits(
        max_position_pct_equity=50.0,
        max_sleeve_pct_equity=50.0,
        max_correlated_pct_equity=1.0,
    )
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="core",
        order_exposure_notional=100.0,
        correlated_group="",
    )
    context = RiskContext(equity=10000.0)
    decision = evaluate_pre_trade_risk(request, context, limits)
    assert decision.approved is True


def test_kill_switch_takes_priority_over_limit_checks():
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="core",
        order_exposure_notional=1.0,
    )
    context = RiskContext(
        equity=1000000.0,
        kill_switch_active=True,
        kill_switch_reason="emergency halt",
    )
    decision = evaluate_pre_trade_risk(request, context, _limits())
    assert not decision.approved
    assert decision.rule_id == "KILL_SWITCH_ACTIVE"
    assert "emergency halt" in decision.message


def test_cooldown_case_insensitive():
    request = RiskOrderRequest(
        ticker="spy",
        sleeve="core",
        order_exposure_notional=100.0,
    )
    context = RiskContext(
        equity=100000.0,
        cooldown_tickers={"SPY"},
    )
    decision = evaluate_pre_trade_risk(request, context, _limits())
    assert not decision.approved
    assert decision.rule_id == "MARKET_COOLDOWN_ACTIVE"


def test_cooldown_takes_priority_over_limit_checks():
    request = RiskOrderRequest(
        ticker="AAPL",
        sleeve="core",
        order_exposure_notional=100.0,
    )
    context = RiskContext(
        equity=100000.0,
        cooldown_tickers={"AAPL"},
    )
    decision = evaluate_pre_trade_risk(request, context, _limits())
    assert not decision.approved
    assert decision.rule_id == "MARKET_COOLDOWN_ACTIVE"


def test_existing_exposure_adds_to_order_for_position_check():
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="core",
        order_exposure_notional=500.0,
    )
    context = RiskContext(
        equity=10000.0,
        ticker_exposure_notional={"SPY": 1200.0},
    )
    decision = evaluate_pre_trade_risk(request, context, _limits())
    assert not decision.approved
    assert decision.rule_id == "MAX_POSITION_PCT_EQUITY"
    assert decision.observed_pct == 17.0


def test_audit_payload_structure():
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="core",
        order_exposure_notional=100.0,
    )
    context = RiskContext(equity=100000.0)
    decision = evaluate_pre_trade_risk(request, context, _limits())
    payload = decision.to_audit_payload()
    assert "approved" in payload
    assert "rule_id" in payload
    assert "message" in payload
    assert "observed_pct" in payload
    assert "threshold_pct" in payload
    assert "details" in payload
    assert isinstance(payload["details"], dict)


def test_risk_limits_rejects_zero_or_negative():
    import pytest

    with pytest.raises(ValueError):
        RiskLimits(max_position_pct_equity=0.0, max_sleeve_pct_equity=10.0, max_correlated_pct_equity=10.0)
    with pytest.raises(ValueError):
        RiskLimits(max_position_pct_equity=10.0, max_sleeve_pct_equity=-1.0, max_correlated_pct_equity=10.0)


def test_risk_order_request_validates_required_fields():
    import pytest

    with pytest.raises(ValueError, match="ticker"):
        RiskOrderRequest(ticker="", sleeve="core", order_exposure_notional=100.0)
    with pytest.raises(ValueError, match="sleeve"):
        RiskOrderRequest(ticker="SPY", sleeve="", order_exposure_notional=100.0)
    with pytest.raises(ValueError, match="order_exposure_notional"):
        RiskOrderRequest(ticker="SPY", sleeve="core", order_exposure_notional=0.0)


def test_risk_context_rejects_non_positive_equity():
    import pytest

    with pytest.raises(ValueError, match="equity"):
        RiskContext(equity=0.0)
    with pytest.raises(ValueError, match="equity"):
        RiskContext(equity=-100.0)


def test_enforce_pre_trade_risk_approves_valid_order():
    request = RiskOrderRequest(
        ticker="SPY",
        sleeve="core",
        order_exposure_notional=100.0,
    )
    context = RiskContext(equity=100000.0)
    decision = enforce_pre_trade_risk(request=request, context=context, limits=_limits())
    assert decision.approved is True
    assert decision.rule_id == "APPROVED"


def test_risk_gate_rejected_error_carries_decision():
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
        assert "MAX_POSITION_PCT_EQUITY" in str(exc)
