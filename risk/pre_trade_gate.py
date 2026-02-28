"""
Pre-trade risk gate — hierarchical hard limits evaluated before broker submission.

A-006: Every order must pass all risk rules before reaching any broker.
Rules are evaluated top-down: fund → sleeve → strategy → trade level.

The gate integrates with the multi-broker ledger (A-005) to read current
portfolio state, and returns a RiskVerdict with exact rule IDs and threshold
values for any rejection.

Design principles:
- Pure functions where possible — easy to test, no hidden state
- Verdict includes machine-readable rule_id + human-readable reason
- Audit-trail friendly: every check recorded in structured format
- Extensible: new rules added by appending to the rule chain
"""
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Optional

from data.trade_db import get_conn, DB_PATH

logger = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskLimits:
    """
    Hierarchical risk limits for the fund.

    All percentages expressed as floats (e.g. 2.0 means 2%).
    """
    # Fund level
    fund_max_drawdown_pct: float = 30.0           # Kill-switch at -30% fund NAV
    fund_daily_loss_limit_pct: float = 5.0        # Pause new trades at -5% in a day

    # Sleeve level
    sleeve_max_allocation_pct: float = 35.0       # No sleeve > 35% of fund NAV
    sleeve_max_drawdown_pct: float = 15.0         # Per-sleeve drawdown limit

    # Strategy level
    strategy_max_drawdown_pct: float = 10.0       # Per-strategy drawdown limit
    strategy_max_open_positions: int = 20          # Max positions per strategy

    # Trade level
    trade_max_risk_pct: float = 2.0               # Max risk per trade as % of fund NAV
    trade_max_notional_pct: float = 10.0          # Max notional value per trade as % of NAV
    trade_min_cash_buffer_pct: float = 10.0       # Keep 10% NAV in cash minimum

    # Position concentration
    max_single_position_pct: float = 5.0          # No single ticker > 5% of NAV
    max_sector_exposure_pct: float = 25.0         # No single sector > 25% of NAV
    max_correlated_cluster_pct: float = 15.0      # Placeholder for correlation-based limits

    # Cooldown
    cooldown_after_kill_switch_hours: float = 4.0  # Wait 4h after kill switch reset


# ─── Verdict ────────────────────────────────────────────────────────────────


@dataclass
class RiskVerdict:
    """
    Result of a pre-trade risk evaluation.

    Attributes:
        approved: True if the trade passes all checks.
        rule_id: ID of the first failing rule (None if approved).
        reason: Human-readable rejection reason.
        checks_run: Number of rules evaluated.
        details: Full list of check results for audit.
        verdict_id: Unique ID for this evaluation (for audit trail).
        timestamp: When the evaluation was performed.
    """
    approved: bool
    rule_id: Optional[str] = None
    reason: str = "OK"
    checks_run: int = 0
    details: list = field(default_factory=list)
    verdict_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Order proposal (what we're evaluating) ─────────────────────────────────


@dataclass
class OrderProposal:
    """
    A proposed trade to evaluate against risk rules.

    This is broker-agnostic — the router (A-004) will have already selected
    the target broker by the time the risk gate sees this.
    """
    ticker: str
    direction: str                    # "long" or "short"
    quantity: float                   # Number of shares/units/contracts
    notional_value: float             # Estimated £/$ value of the position
    risk_amount: float                # Max £/$ at risk (e.g. stop distance × size)
    strategy: str                     # Strategy requesting the trade
    sleeve: str = ""                  # Target sleeve
    broker: str = ""                  # Target broker
    account_type: str = ""            # ISA, GIA, SPREADBET, PAPER
    sector: str = ""                  # For sector concentration check
    order_type: str = "MKT"           # MKT or LMT


# ─── Portfolio snapshot (current state) ──────────────────────────────────────


@dataclass
class PortfolioSnapshot:
    """
    Current portfolio state used for risk evaluation.

    Can be built from the ledger (A-005) or passed directly for testing.
    """
    fund_nav: float = 0.0               # Total fund net liquidation
    fund_cash: float = 0.0              # Total cash across all accounts
    fund_peak_nav: float = 0.0          # High water mark for drawdown calc
    daily_pnl: float = 0.0              # Today's realised + unrealised P&L

    # Sleeve-level
    sleeve_navs: dict = field(default_factory=dict)        # {sleeve_id: nav}
    sleeve_peak_navs: dict = field(default_factory=dict)   # {sleeve_id: peak_nav}

    # Position-level
    positions: list = field(default_factory=list)  # list of dicts with ticker, direction, market_value, strategy, sleeve, sector

    # Timing
    kill_switch_active: bool = False
    kill_switch_reset_time: Optional[str] = None   # ISO timestamp of last reset


# ─── Pre-trade risk gate ────────────────────────────────────────────────────


class PreTradeRiskGate:
    """
    Evaluates an OrderProposal against hierarchical risk limits.

    Usage:
        gate = PreTradeRiskGate(limits=RiskLimits())
        verdict = gate.evaluate(proposal, portfolio)
        if not verdict.approved:
            reject_order(verdict.rule_id, verdict.reason)
    """

    def __init__(self, limits: Optional[RiskLimits] = None, db_path: str = DB_PATH):
        self.limits = limits or RiskLimits()
        self.db_path = db_path

    def evaluate(
        self,
        proposal: OrderProposal,
        portfolio: PortfolioSnapshot,
    ) -> RiskVerdict:
        """
        Run all risk checks against the proposal.

        Returns a RiskVerdict. If any check fails, evaluation stops
        and the verdict contains the failing rule.
        """
        checks = []
        checks_run = 0

        # Define the rule chain — evaluated in order, short-circuit on first failure
        rules = [
            self._check_kill_switch,
            self._check_fund_drawdown,
            self._check_daily_loss_limit,
            self._check_cash_buffer,
            self._check_trade_risk_limit,
            self._check_trade_notional_limit,
            self._check_position_concentration,
            self._check_sleeve_allocation,
            self._check_sleeve_drawdown,
            self._check_strategy_max_positions,
            self._check_sector_concentration,
            self._check_cooldown,
        ]

        for rule_fn in rules:
            checks_run += 1
            result = rule_fn(proposal, portfolio)
            checks.append(result)

            if not result["passed"]:
                verdict = RiskVerdict(
                    approved=False,
                    rule_id=result["rule_id"],
                    reason=result["reason"],
                    checks_run=checks_run,
                    details=checks,
                )
                self._persist_verdict(verdict, proposal)
                logger.warning(
                    f"Risk gate REJECTED: {result['rule_id']} — {result['reason']} "
                    f"[{proposal.ticker} {proposal.direction} x{proposal.quantity}]"
                )
                return verdict

        verdict = RiskVerdict(
            approved=True,
            checks_run=checks_run,
            details=checks,
        )
        self._persist_verdict(verdict, proposal)
        logger.info(
            f"Risk gate APPROVED: {proposal.ticker} {proposal.direction} "
            f"x{proposal.quantity} [{proposal.strategy}] — {checks_run} checks passed"
        )
        return verdict

    # ─── Individual risk rules ────────────────────────────────────────────

    def _check_kill_switch(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-001: Block all trades when kill switch is active."""
        if portfolio.kill_switch_active:
            return {
                "rule_id": "R-001",
                "rule_name": "kill_switch",
                "passed": False,
                "reason": "Kill switch is active — all trading halted",
                "threshold": "kill_switch=False",
                "actual": "kill_switch=True",
            }
        return self._pass("R-001", "kill_switch")

    def _check_fund_drawdown(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-002: Block trades if fund drawdown exceeds limit."""
        if portfolio.fund_peak_nav <= 0:
            return self._pass("R-002", "fund_drawdown")

        drawdown_pct = (
            (portfolio.fund_peak_nav - portfolio.fund_nav) / portfolio.fund_peak_nav * 100
        )
        if drawdown_pct >= self.limits.fund_max_drawdown_pct:
            return {
                "rule_id": "R-002",
                "rule_name": "fund_drawdown",
                "passed": False,
                "reason": (
                    f"Fund drawdown {drawdown_pct:.1f}% exceeds max "
                    f"{self.limits.fund_max_drawdown_pct}%"
                ),
                "threshold": f"{self.limits.fund_max_drawdown_pct}%",
                "actual": f"{drawdown_pct:.1f}%",
            }
        return self._pass("R-002", "fund_drawdown")

    def _check_daily_loss_limit(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-003: Block trades if daily loss exceeds limit."""
        if portfolio.fund_nav <= 0:
            return self._pass("R-003", "daily_loss_limit")

        daily_loss_pct = abs(min(portfolio.daily_pnl, 0)) / portfolio.fund_nav * 100
        if daily_loss_pct >= self.limits.fund_daily_loss_limit_pct:
            return {
                "rule_id": "R-003",
                "rule_name": "daily_loss_limit",
                "passed": False,
                "reason": (
                    f"Daily loss {daily_loss_pct:.1f}% exceeds max "
                    f"{self.limits.fund_daily_loss_limit_pct}%"
                ),
                "threshold": f"{self.limits.fund_daily_loss_limit_pct}%",
                "actual": f"{daily_loss_pct:.1f}%",
            }
        return self._pass("R-003", "daily_loss_limit")

    def _check_cash_buffer(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-004: Ensure minimum cash buffer is maintained after trade."""
        if portfolio.fund_nav <= 0:
            return self._pass("R-004", "cash_buffer")

        cash_after = portfolio.fund_cash - proposal.notional_value
        cash_pct_after = cash_after / portfolio.fund_nav * 100

        if cash_pct_after < self.limits.trade_min_cash_buffer_pct:
            return {
                "rule_id": "R-004",
                "rule_name": "cash_buffer",
                "passed": False,
                "reason": (
                    f"Cash after trade would be {cash_pct_after:.1f}% of NAV, "
                    f"below minimum {self.limits.trade_min_cash_buffer_pct}%"
                ),
                "threshold": f"{self.limits.trade_min_cash_buffer_pct}%",
                "actual": f"{cash_pct_after:.1f}%",
            }
        return self._pass("R-004", "cash_buffer")

    def _check_trade_risk_limit(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-005: Max risk per trade as % of fund NAV."""
        if portfolio.fund_nav <= 0:
            return self._pass("R-005", "trade_risk_limit")

        risk_pct = proposal.risk_amount / portfolio.fund_nav * 100
        if risk_pct > self.limits.trade_max_risk_pct:
            return {
                "rule_id": "R-005",
                "rule_name": "trade_risk_limit",
                "passed": False,
                "reason": (
                    f"Trade risk {risk_pct:.1f}% of NAV exceeds max "
                    f"{self.limits.trade_max_risk_pct}%"
                ),
                "threshold": f"{self.limits.trade_max_risk_pct}%",
                "actual": f"{risk_pct:.1f}%",
            }
        return self._pass("R-005", "trade_risk_limit")

    def _check_trade_notional_limit(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-006: Max notional value per trade as % of NAV."""
        if portfolio.fund_nav <= 0:
            return self._pass("R-006", "trade_notional_limit")

        notional_pct = proposal.notional_value / portfolio.fund_nav * 100
        if notional_pct > self.limits.trade_max_notional_pct:
            return {
                "rule_id": "R-006",
                "rule_name": "trade_notional_limit",
                "passed": False,
                "reason": (
                    f"Trade notional {notional_pct:.1f}% of NAV exceeds max "
                    f"{self.limits.trade_max_notional_pct}%"
                ),
                "threshold": f"{self.limits.trade_max_notional_pct}%",
                "actual": f"{notional_pct:.1f}%",
            }
        return self._pass("R-006", "trade_notional_limit")

    def _check_position_concentration(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-007: No single ticker > max_single_position_pct of NAV."""
        if portfolio.fund_nav <= 0:
            return self._pass("R-007", "position_concentration")

        # Sum existing exposure in this ticker
        existing_value = sum(
            p.get("market_value", 0)
            for p in portfolio.positions
            if p.get("ticker") == proposal.ticker
        )
        total_after = existing_value + proposal.notional_value
        concentration_pct = total_after / portfolio.fund_nav * 100

        if concentration_pct > self.limits.max_single_position_pct:
            return {
                "rule_id": "R-007",
                "rule_name": "position_concentration",
                "passed": False,
                "reason": (
                    f"{proposal.ticker} would be {concentration_pct:.1f}% of NAV "
                    f"(max {self.limits.max_single_position_pct}%)"
                ),
                "threshold": f"{self.limits.max_single_position_pct}%",
                "actual": f"{concentration_pct:.1f}%",
            }
        return self._pass("R-007", "position_concentration")

    def _check_sleeve_allocation(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-008: No sleeve > sleeve_max_allocation_pct of fund NAV."""
        if not proposal.sleeve or portfolio.fund_nav <= 0:
            return self._pass("R-008", "sleeve_allocation")

        sleeve_nav = portfolio.sleeve_navs.get(proposal.sleeve, 0)
        sleeve_after = sleeve_nav + proposal.notional_value
        sleeve_pct = sleeve_after / portfolio.fund_nav * 100

        if sleeve_pct > self.limits.sleeve_max_allocation_pct:
            return {
                "rule_id": "R-008",
                "rule_name": "sleeve_allocation",
                "passed": False,
                "reason": (
                    f"Sleeve '{proposal.sleeve}' would be {sleeve_pct:.1f}% of fund NAV "
                    f"(max {self.limits.sleeve_max_allocation_pct}%)"
                ),
                "threshold": f"{self.limits.sleeve_max_allocation_pct}%",
                "actual": f"{sleeve_pct:.1f}%",
            }
        return self._pass("R-008", "sleeve_allocation")

    def _check_sleeve_drawdown(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-009: Block trades in a sleeve that has exceeded its drawdown limit."""
        if not proposal.sleeve:
            return self._pass("R-009", "sleeve_drawdown")

        sleeve_nav = portfolio.sleeve_navs.get(proposal.sleeve, 0)
        sleeve_peak = portfolio.sleeve_peak_navs.get(proposal.sleeve, 0)

        if sleeve_peak <= 0:
            return self._pass("R-009", "sleeve_drawdown")

        sleeve_dd_pct = (sleeve_peak - sleeve_nav) / sleeve_peak * 100
        if sleeve_dd_pct >= self.limits.sleeve_max_drawdown_pct:
            return {
                "rule_id": "R-009",
                "rule_name": "sleeve_drawdown",
                "passed": False,
                "reason": (
                    f"Sleeve '{proposal.sleeve}' drawdown {sleeve_dd_pct:.1f}% "
                    f"exceeds max {self.limits.sleeve_max_drawdown_pct}%"
                ),
                "threshold": f"{self.limits.sleeve_max_drawdown_pct}%",
                "actual": f"{sleeve_dd_pct:.1f}%",
            }
        return self._pass("R-009", "sleeve_drawdown")

    def _check_strategy_max_positions(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-010: Max open positions per strategy."""
        strategy_positions = sum(
            1 for p in portfolio.positions
            if p.get("strategy") == proposal.strategy
        )
        if strategy_positions >= self.limits.strategy_max_open_positions:
            return {
                "rule_id": "R-010",
                "rule_name": "strategy_max_positions",
                "passed": False,
                "reason": (
                    f"Strategy '{proposal.strategy}' has {strategy_positions} positions "
                    f"(max {self.limits.strategy_max_open_positions})"
                ),
                "threshold": str(self.limits.strategy_max_open_positions),
                "actual": str(strategy_positions),
            }
        return self._pass("R-010", "strategy_max_positions")

    def _check_sector_concentration(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-011: No single sector > max_sector_exposure_pct of NAV."""
        if not proposal.sector or portfolio.fund_nav <= 0:
            return self._pass("R-011", "sector_concentration")

        sector_value = sum(
            p.get("market_value", 0)
            for p in portfolio.positions
            if p.get("sector") == proposal.sector
        )
        sector_after = sector_value + proposal.notional_value
        sector_pct = sector_after / portfolio.fund_nav * 100

        if sector_pct > self.limits.max_sector_exposure_pct:
            return {
                "rule_id": "R-011",
                "rule_name": "sector_concentration",
                "passed": False,
                "reason": (
                    f"Sector '{proposal.sector}' would be {sector_pct:.1f}% of NAV "
                    f"(max {self.limits.max_sector_exposure_pct}%)"
                ),
                "threshold": f"{self.limits.max_sector_exposure_pct}%",
                "actual": f"{sector_pct:.1f}%",
            }
        return self._pass("R-011", "sector_concentration")

    def _check_cooldown(self, proposal: OrderProposal, portfolio: PortfolioSnapshot) -> dict:
        """R-012: Enforce cooldown period after kill switch reset."""
        if not portfolio.kill_switch_reset_time:
            return self._pass("R-012", "cooldown")

        try:
            reset_time = datetime.fromisoformat(portfolio.kill_switch_reset_time)
            hours_since = (datetime.utcnow() - reset_time).total_seconds() / 3600
            if hours_since < self.limits.cooldown_after_kill_switch_hours:
                return {
                    "rule_id": "R-012",
                    "rule_name": "cooldown",
                    "passed": False,
                    "reason": (
                        f"Cooldown active: {hours_since:.1f}h since kill switch reset "
                        f"(need {self.limits.cooldown_after_kill_switch_hours}h)"
                    ),
                    "threshold": f"{self.limits.cooldown_after_kill_switch_hours}h",
                    "actual": f"{hours_since:.1f}h",
                }
        except (ValueError, TypeError):
            pass

        return self._pass("R-012", "cooldown")

    # ─── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _pass(rule_id: str, rule_name: str) -> dict:
        """Helper for a passed check."""
        return {
            "rule_id": rule_id,
            "rule_name": rule_name,
            "passed": True,
            "reason": "OK",
            "threshold": "",
            "actual": "",
        }

    def _persist_verdict(self, verdict: RiskVerdict, proposal: OrderProposal):
        """Save the risk verdict to the DB for audit trail."""
        try:
            conn = get_conn(self.db_path)
            conn.execute(
                """INSERT INTO risk_verdicts
                   (id, created_at, ticker, direction, quantity, strategy, sleeve, broker,
                    approved, rule_id, reason, checks_run, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    verdict.verdict_id,
                    verdict.timestamp,
                    proposal.ticker,
                    proposal.direction,
                    proposal.quantity,
                    proposal.strategy,
                    proposal.sleeve,
                    proposal.broker,
                    int(verdict.approved),
                    verdict.rule_id,
                    verdict.reason,
                    verdict.checks_run,
                    json.dumps(verdict.details),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            # Don't let audit failures block trading
            logger.error(f"Failed to persist risk verdict: {e}")


# ─── Portfolio snapshot builder (from ledger) ────────────────────────────────


def build_portfolio_snapshot(db_path: str = DB_PATH) -> PortfolioSnapshot:
    """
    Build a PortfolioSnapshot from the multi-broker ledger.

    Reads unified positions, latest cash balances, and NAV snapshots
    to construct the current portfolio state for risk evaluation.
    """
    from execution.ledger import (
        get_unified_positions,
        get_latest_cash_balances,
        get_nav_history,
    )

    # Get positions across all brokers
    positions_raw = get_unified_positions(db_path=db_path)
    positions = [
        {
            "ticker": p["ticker"],
            "direction": p["direction"],
            "market_value": p.get("market_value", 0),
            "strategy": p.get("strategy", ""),
            "sleeve": p.get("sleeve", ""),
            "sector": p.get("sector", ""),
            "broker": p.get("broker", ""),
        }
        for p in positions_raw
    ]

    # Get total cash across all accounts
    cash_balances = get_latest_cash_balances(db_path=db_path)
    total_cash = sum(b["balance"] for b in cash_balances)

    # Get fund-level NAV
    fund_nav_history = get_nav_history(level="fund", level_id="fund", days=365, db_path=db_path)

    fund_nav = fund_nav_history[0]["net_liquidation"] if fund_nav_history else 0
    fund_peak = max(
        (n["net_liquidation"] for n in fund_nav_history), default=fund_nav
    )

    # Get sleeve-level NAVs
    sleeve_navs = {}
    sleeve_peak_navs = {}
    sleeves_seen = {p.get("sleeve") for p in positions if p.get("sleeve")}
    for sleeve_id in sleeves_seen:
        sleeve_history = get_nav_history(
            level="sleeve", level_id=sleeve_id, days=365, db_path=db_path
        )
        if sleeve_history:
            sleeve_navs[sleeve_id] = sleeve_history[0]["net_liquidation"]
            sleeve_peak_navs[sleeve_id] = max(
                n["net_liquidation"] for n in sleeve_history
            )

    # Daily P&L from today's NAV vs yesterday's
    daily_pnl = 0.0
    if len(fund_nav_history) >= 2:
        daily_pnl = fund_nav_history[0]["net_liquidation"] - fund_nav_history[1]["net_liquidation"]

    return PortfolioSnapshot(
        fund_nav=fund_nav,
        fund_cash=total_cash,
        fund_peak_nav=fund_peak,
        daily_pnl=daily_pnl,
        sleeve_navs=sleeve_navs,
        sleeve_peak_navs=sleeve_peak_navs,
        positions=positions,
    )
