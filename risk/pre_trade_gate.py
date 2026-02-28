"""Pre-trade hard-limit risk gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RiskLimits:
    """Hard limit thresholds represented as percent-of-equity."""

    max_position_pct_equity: float
    max_sleeve_pct_equity: float
    max_correlated_pct_equity: float

    def __post_init__(self):
        for field_name in (
            "max_position_pct_equity",
            "max_sleeve_pct_equity",
            "max_correlated_pct_equity",
        ):
            value = float(getattr(self, field_name))
            if value <= 0:
                raise ValueError(f"{field_name} must be > 0")


@dataclass(frozen=True)
class RiskOrderRequest:
    """Candidate order request evaluated by the pre-trade gate."""

    ticker: str
    sleeve: str
    order_exposure_notional: float
    correlated_group: str = ""
    actor: str = "system"

    def __post_init__(self):
        if not str(self.ticker or "").strip():
            raise ValueError("ticker is required")
        if not str(self.sleeve or "").strip():
            raise ValueError("sleeve is required")
        if float(self.order_exposure_notional) <= 0:
            raise ValueError("order_exposure_notional must be > 0")


@dataclass(frozen=True)
class RiskContext:
    """Portfolio state required to evaluate hard limits."""

    equity: float
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    cooldown_tickers: set[str] = field(default_factory=set)
    ticker_exposure_notional: dict[str, float] = field(default_factory=dict)
    sleeve_exposure_notional: dict[str, float] = field(default_factory=dict)
    correlated_exposure_notional: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if float(self.equity) <= 0:
            raise ValueError("equity must be > 0")

    def has_cooldown(self, ticker: str) -> bool:
        target = str(ticker or "").upper()
        return target in {str(item).upper() for item in self.cooldown_tickers}


@dataclass(frozen=True)
class RiskDecision:
    """Risk gate decision with a machine-readable rule ID."""

    approved: bool
    rule_id: str
    message: str
    observed_pct: float = 0.0
    threshold_pct: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_audit_payload(self) -> dict[str, Any]:
        """Structured payload suitable for persistence in audit envelopes."""
        return {
            "approved": self.approved,
            "rule_id": self.rule_id,
            "message": self.message,
            "observed_pct": self.observed_pct,
            "threshold_pct": self.threshold_pct,
            "details": dict(self.details),
        }


def _pct_of_equity(amount: float, equity: float) -> float:
    return (float(amount) / float(equity)) * 100.0


def evaluate_pre_trade_risk(
    request: RiskOrderRequest,
    context: RiskContext,
    limits: RiskLimits,
) -> RiskDecision:
    """
    Evaluate hard risk limits for one order request.

    Rule order is intentional and deterministic:
    1) kill switch
    2) ticker cooldown
    3) max position % equity
    4) max sleeve exposure % equity
    5) max correlated exposure % equity (if group provided)
    """
    if context.kill_switch_active:
        reason = context.kill_switch_reason.strip() or "Kill switch active"
        return RiskDecision(
            approved=False,
            rule_id="KILL_SWITCH_ACTIVE",
            message=reason,
            details={"ticker": request.ticker, "actor": request.actor},
        )

    if context.has_cooldown(request.ticker):
        return RiskDecision(
            approved=False,
            rule_id="MARKET_COOLDOWN_ACTIVE",
            message=f"Cooldown active for {request.ticker.upper()}",
            details={"ticker": request.ticker.upper()},
        )

    current_position = float(context.ticker_exposure_notional.get(request.ticker.upper(), 0.0) or 0.0)
    projected_position = current_position + float(request.order_exposure_notional)
    position_pct = _pct_of_equity(projected_position, context.equity)
    if position_pct > float(limits.max_position_pct_equity):
        return RiskDecision(
            approved=False,
            rule_id="MAX_POSITION_PCT_EQUITY",
            message="Projected single-position exposure exceeds hard limit",
            observed_pct=position_pct,
            threshold_pct=float(limits.max_position_pct_equity),
            details={
                "ticker": request.ticker.upper(),
                "projected_notional": projected_position,
                "equity": float(context.equity),
            },
        )

    current_sleeve = float(context.sleeve_exposure_notional.get(request.sleeve, 0.0) or 0.0)
    projected_sleeve = current_sleeve + float(request.order_exposure_notional)
    sleeve_pct = _pct_of_equity(projected_sleeve, context.equity)
    if sleeve_pct > float(limits.max_sleeve_pct_equity):
        return RiskDecision(
            approved=False,
            rule_id="MAX_SLEEVE_PCT_EQUITY",
            message="Projected sleeve exposure exceeds hard limit",
            observed_pct=sleeve_pct,
            threshold_pct=float(limits.max_sleeve_pct_equity),
            details={
                "sleeve": request.sleeve,
                "projected_notional": projected_sleeve,
                "equity": float(context.equity),
            },
        )

    group = str(request.correlated_group or "").strip()
    if group:
        current_corr = float(context.correlated_exposure_notional.get(group, 0.0) or 0.0)
        projected_corr = current_corr + float(request.order_exposure_notional)
        corr_pct = _pct_of_equity(projected_corr, context.equity)
        if corr_pct > float(limits.max_correlated_pct_equity):
            return RiskDecision(
                approved=False,
                rule_id="MAX_CORRELATED_PCT_EQUITY",
                message="Projected correlated-group exposure exceeds hard limit",
                observed_pct=corr_pct,
                threshold_pct=float(limits.max_correlated_pct_equity),
                details={
                    "correlated_group": group,
                    "projected_notional": projected_corr,
                    "equity": float(context.equity),
                },
            )

    return RiskDecision(
        approved=True,
        rule_id="APPROVED",
        message="Risk gate approved order",
        details={
            "ticker": request.ticker.upper(),
            "sleeve": request.sleeve,
            "actor": request.actor,
        },
    )
