"""Execution helpers for pre-trade risk gating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from risk.pre_trade_gate import (
    RiskContext,
    RiskDecision,
    RiskLimits,
    RiskOrderRequest,
    evaluate_pre_trade_risk,
)

T = TypeVar("T")


@dataclass(frozen=True)
class RiskGateResult:
    """Result wrapper for risk-gated submission attempts."""

    decision: RiskDecision
    submitted: bool
    broker_result: Any = None


class RiskGateRejectedError(RuntimeError):
    """Raised when an order is blocked by hard-limit policy."""

    def __init__(self, decision: RiskDecision):
        self.decision = decision
        super().__init__(f"{decision.rule_id}: {decision.message}")


def enforce_pre_trade_risk(
    request: RiskOrderRequest,
    context: RiskContext,
    limits: RiskLimits,
) -> RiskDecision:
    """Evaluate and enforce hard limits, raising on rejection."""
    decision = evaluate_pre_trade_risk(request=request, context=context, limits=limits)
    if not decision.approved:
        raise RiskGateRejectedError(decision)
    return decision


def submit_with_risk_gate(
    request: RiskOrderRequest,
    context: RiskContext,
    limits: RiskLimits,
    submit_fn: Callable[[], T],
) -> RiskGateResult:
    """
    Execute broker submission only if hard-limit gate approves.

    The caller can persist `decision.to_audit_payload()` alongside order intents.
    """
    decision = evaluate_pre_trade_risk(request=request, context=context, limits=limits)
    if not decision.approved:
        return RiskGateResult(decision=decision, submitted=False, broker_result=None)
    broker_result = submit_fn()
    return RiskGateResult(decision=decision, submitted=True, broker_result=broker_result)
