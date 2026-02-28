"""
Portfolio-level risk metrics aggregation.

B-003: Computes daily portfolio risk metrics from the multi-broker ledger,
including heat utilisation, concentration, leverage, and a basic VaR estimate.
Results are persisted to the risk_daily_snapshot table for dashboards and
operator briefings (B-004).
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from data.trade_db import (
    DB_PATH,
    get_conn,
    save_risk_daily_snapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class PortfolioRiskSnapshot:
    """Point-in-time portfolio risk metrics."""

    snapshot_date: str
    total_heat_pct: float      # Total risk as % of NAV
    total_margin_pct: float    # Margin used as % of equity
    max_position_pct: float    # Largest single position as % of NAV
    open_position_count: int
    open_spread_count: int
    leverage_ratio: float      # Gross exposure / NAV
    var_95_pct: Optional[float]  # 1-day parametric VaR at 95% CI as % of NAV


@dataclass
class PositionRiskDetail:
    """Risk contribution from a single position."""

    ticker: str
    broker: str
    direction: str
    market_value: float
    weight_pct: float
    unrealised_pnl: float
    sleeve: Optional[str]


def calculate_portfolio_risk(
    total_nav: float,
    snapshot_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> PortfolioRiskSnapshot:
    """
    Calculate portfolio-level risk metrics from current positions.

    Args:
        total_nav: Current fund NAV (from fund/nav.py calculation).
        snapshot_date: Date for the snapshot, defaults to today.
        db_path: Database path.

    Returns:
        PortfolioRiskSnapshot with all risk metrics computed.
    """
    snap_date = snapshot_date or date.today().isoformat()
    conn = get_conn(db_path)

    # ── Position-level data ──
    positions = conn.execute("""
        SELECT
            bp.ticker, bp.direction, bp.quantity, bp.market_value,
            bp.unrealised_pnl, bp.sleeve, bp.strategy,
            ba.broker
        FROM broker_positions bp
        JOIN broker_accounts ba ON bp.broker_account_id = ba.id
        WHERE ba.is_active = 1
    """).fetchall()

    # ── Spread count (option positions table) ──
    spread_row = conn.execute("""
        SELECT COUNT(*) as c FROM option_positions WHERE status = 'open'
    """).fetchone()
    spread_count = int(spread_row["c"]) if spread_row else 0

    conn.close()

    pos_list = [dict(p) for p in positions]

    # ── Compute metrics ──
    open_count = len(pos_list)

    # Gross exposure (absolute market value of all positions)
    gross_exposure = sum(abs(float(p.get("market_value", 0))) for p in pos_list)

    # Heat: total absolute exposure as % of NAV
    total_heat_pct = (gross_exposure / total_nav * 100.0) if total_nav > 0 else 0.0

    # Max single position as % of NAV
    max_pos_value = max(
        (abs(float(p.get("market_value", 0))) for p in pos_list),
        default=0.0,
    )
    max_position_pct = (max_pos_value / total_nav * 100.0) if total_nav > 0 else 0.0

    # Leverage: gross exposure / NAV
    leverage_ratio = (gross_exposure / total_nav) if total_nav > 0 else 0.0

    # Margin: currently estimated from spread positions + any short positions
    # Real margin requires broker API; this is a conservative proxy
    margin_notional = _estimate_margin(pos_list, spread_count)
    total_margin_pct = (margin_notional / total_nav * 100.0) if total_nav > 0 else 0.0

    # Parametric VaR (95%, 1-day)
    var_95 = _calc_parametric_var(pos_list, total_nav)

    return PortfolioRiskSnapshot(
        snapshot_date=snap_date,
        total_heat_pct=round(total_heat_pct, 2),
        total_margin_pct=round(total_margin_pct, 2),
        max_position_pct=round(max_position_pct, 2),
        open_position_count=open_count,
        open_spread_count=spread_count,
        leverage_ratio=round(leverage_ratio, 4),
        var_95_pct=round(var_95, 2) if var_95 is not None else None,
    )


def get_position_risk_details(
    total_nav: float,
    db_path: str = DB_PATH,
) -> list[PositionRiskDetail]:
    """
    Get per-position risk breakdown for drill-down displays.

    Returns positions sorted by absolute weight (largest first).
    """
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT
            bp.ticker, bp.direction, bp.market_value,
            bp.unrealised_pnl, bp.sleeve, ba.broker
        FROM broker_positions bp
        JOIN broker_accounts ba ON bp.broker_account_id = ba.id
        WHERE ba.is_active = 1
        ORDER BY ABS(bp.market_value) DESC
    """).fetchall()
    conn.close()

    results = []
    for r in rows:
        mv = float(r["market_value"] or 0)
        weight = (abs(mv) / total_nav * 100.0) if total_nav > 0 else 0.0
        results.append(PositionRiskDetail(
            ticker=r["ticker"],
            broker=r["broker"],
            direction=r["direction"],
            market_value=mv,
            weight_pct=round(weight, 2),
            unrealised_pnl=float(r["unrealised_pnl"] or 0),
            sleeve=r["sleeve"],
        ))

    return results


def persist_risk_snapshot(
    snapshot: PortfolioRiskSnapshot,
    db_path: str = DB_PATH,
):
    """Persist a risk snapshot to the risk_daily_snapshot table."""
    save_risk_daily_snapshot(
        snapshot_date=snapshot.snapshot_date,
        total_heat_pct=snapshot.total_heat_pct,
        total_margin_pct=snapshot.total_margin_pct,
        max_position_pct=snapshot.max_position_pct,
        open_position_count=snapshot.open_position_count,
        open_spread_count=snapshot.open_spread_count,
        leverage_ratio=snapshot.leverage_ratio,
        var_95_pct=snapshot.var_95_pct,
        db_path=db_path,
    )
    logger.info(
        f"Risk snapshot persisted for {snapshot.snapshot_date}: "
        f"heat={snapshot.total_heat_pct:.1f}%, "
        f"positions={snapshot.open_position_count}, "
        f"leverage={snapshot.leverage_ratio:.2f}x"
    )


def run_daily_risk(
    total_nav: float,
    snapshot_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> dict:
    """
    End-of-day risk job: calculate and persist portfolio risk metrics.

    Args:
        total_nav: Current fund NAV (must be provided from NAV calculation).
        snapshot_date: Date for the snapshot.
        db_path: Database path.

    Returns summary dict for logging/Telegram.
    """
    snapshot = calculate_portfolio_risk(
        total_nav=total_nav,
        snapshot_date=snapshot_date,
        db_path=db_path,
    )
    persist_risk_snapshot(snapshot, db_path=db_path)

    return {
        "snapshot_date": snapshot.snapshot_date,
        "total_heat_pct": snapshot.total_heat_pct,
        "total_margin_pct": snapshot.total_margin_pct,
        "max_position_pct": snapshot.max_position_pct,
        "open_position_count": snapshot.open_position_count,
        "open_spread_count": snapshot.open_spread_count,
        "leverage_ratio": snapshot.leverage_ratio,
        "var_95_pct": snapshot.var_95_pct,
    }


def generate_risk_verdict(snapshot: PortfolioRiskSnapshot) -> dict:
    """
    Generate a deterministic risk verdict from a portfolio risk snapshot.

    Returns a dict with status (GREEN/AMBER/RED) and any triggered alerts.
    Thresholds are hard-coded per the fund specification:
      - Heat > 80%: RED
      - Heat > 60%: AMBER
      - Max position > 10%: AMBER
      - Max position > 15%: RED
      - Leverage > 2.0x: RED
      - Leverage > 1.5x: AMBER
    """
    alerts: list[str] = []
    status = "GREEN"

    # Heat thresholds
    if snapshot.total_heat_pct > 80:
        alerts.append(f"HEAT_CRITICAL: {snapshot.total_heat_pct:.1f}% (limit 80%)")
        status = "RED"
    elif snapshot.total_heat_pct > 60:
        alerts.append(f"HEAT_ELEVATED: {snapshot.total_heat_pct:.1f}% (warning 60%)")
        if status != "RED":
            status = "AMBER"

    # Position concentration
    if snapshot.max_position_pct > 15:
        alerts.append(f"CONCENTRATION_CRITICAL: {snapshot.max_position_pct:.1f}% (limit 15%)")
        status = "RED"
    elif snapshot.max_position_pct > 10:
        alerts.append(f"CONCENTRATION_ELEVATED: {snapshot.max_position_pct:.1f}% (warning 10%)")
        if status != "RED":
            status = "AMBER"

    # Leverage
    if snapshot.leverage_ratio > 2.0:
        alerts.append(f"LEVERAGE_CRITICAL: {snapshot.leverage_ratio:.2f}x (limit 2.0x)")
        status = "RED"
    elif snapshot.leverage_ratio > 1.5:
        alerts.append(f"LEVERAGE_ELEVATED: {snapshot.leverage_ratio:.2f}x (warning 1.5x)")
        if status != "RED":
            status = "AMBER"

    return {
        "status": status,
        "alerts": alerts,
        "snapshot_date": snapshot.snapshot_date,
        "total_heat_pct": snapshot.total_heat_pct,
        "max_position_pct": snapshot.max_position_pct,
        "leverage_ratio": snapshot.leverage_ratio,
    }


def get_risk_briefing(
    total_nav: float,
    daily_return_pct: Optional[float] = None,
    drawdown_pct: float = 0.0,
    total_cash: float = 0.0,
    snapshot_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> dict:
    """
    B-004 integration contract: produce a structured risk briefing dict.

    Returns the exact fields B-004 expects for the operator risk panel:
      fund_nav, day_pnl, drawdown_pct, gross_exposure_pct, net_exposure_pct,
      cash_buffer_pct, open_risk_pct, generated_at, alerts[], limits[].

    Args:
        total_nav: Current fund NAV.
        daily_return_pct: Today's return % (from fund/nav.py).
        drawdown_pct: Current drawdown % (from fund/nav.py).
        total_cash: Total cash across brokers (from fund/nav.py).
        snapshot_date: Date for the briefing.
        db_path: Database path.
    """
    snap_date = snapshot_date or date.today().isoformat()

    risk = calculate_portfolio_risk(
        total_nav=total_nav,
        snapshot_date=snap_date,
        db_path=db_path,
    )
    verdict = generate_risk_verdict(risk)

    # Day P&L: derive from return % and NAV.
    # daily_return_pct = ((current - prev) / prev) * 100, so:
    #   prev = current / (1 + r/100)
    #   day_pnl = current - prev
    day_pnl = 0.0
    if daily_return_pct is not None and total_nav > 0:
        prev_nav = total_nav / (1.0 + daily_return_pct / 100.0)
        day_pnl = total_nav - prev_nav

    # Net exposure: longs - shorts (as % of NAV)
    conn = get_conn(db_path)
    net_row = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN bp.direction='long' THEN bp.market_value ELSE 0 END), 0) as long_val,
            COALESCE(SUM(CASE WHEN bp.direction='short' THEN bp.market_value ELSE 0 END), 0) as short_val
        FROM broker_positions bp
        JOIN broker_accounts ba ON bp.broker_account_id = ba.id
        WHERE ba.is_active = 1
    """).fetchone()
    conn.close()

    long_val = float(net_row["long_val"])
    short_val = float(net_row["short_val"])
    net_exposure_pct = ((long_val - short_val) / total_nav * 100.0) if total_nav > 0 else 0.0

    # Cash buffer as % of NAV
    cash_buffer_pct = (total_cash / total_nav * 100.0) if total_nav > 0 else 0.0

    # Format alerts for B-004
    alerts = []
    for alert_msg in verdict["alerts"]:
        # Parse "CODE: message" format
        parts = alert_msg.split(":", 1)
        code = parts[0].strip()
        message = parts[1].strip() if len(parts) > 1 else alert_msg
        severity = "critical" if "CRITICAL" in code else "warning"
        action = "reduce exposure" if "HEAT" in code else (
            "diversify" if "CONCENTRATION" in code else "deleverage"
        )
        alerts.append({
            "severity": severity,
            "code": code,
            "message": message,
            "action": action,
        })

    # Static limits for UI rendering
    limits = [
        {"rule": "max_heat_pct", "threshold": 80.0, "warning": 60.0, "current": risk.total_heat_pct},
        {"rule": "max_position_pct", "threshold": 15.0, "warning": 10.0, "current": risk.max_position_pct},
        {"rule": "max_leverage", "threshold": 2.0, "warning": 1.5, "current": risk.leverage_ratio},
    ]

    return {
        "fund_nav": round(total_nav, 2),
        "day_pnl": round(day_pnl, 2),
        "drawdown_pct": round(drawdown_pct, 4),
        "gross_exposure_pct": round(risk.total_heat_pct, 2),
        "net_exposure_pct": round(net_exposure_pct, 2),
        "cash_buffer_pct": round(cash_buffer_pct, 2),
        "open_risk_pct": round(risk.total_heat_pct, 2),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "status": verdict["status"],
        "alerts": alerts,
        "limits": limits,
    }


# ─── Internal helpers ─────────────────────────────────────────────────────


def _estimate_margin(positions: list[dict], spread_count: int) -> float:
    """
    Estimate margin utilisation from positions.

    This is a conservative proxy — real margin requires broker API data.
    For spreadbet/CFD positions we estimate 5% margin (20:1 leverage).
    For options spreads we estimate a flat per-spread margin.
    """
    margin = 0.0

    for p in positions:
        mv = abs(float(p.get("market_value", 0)))
        direction = str(p.get("direction", "")).lower()

        # Short positions typically require more margin
        if direction == "short":
            margin += mv * 0.10  # 10% margin for shorts
        else:
            margin += mv * 0.05  # 5% margin for longs (spreadbet/CFD)

    # Option spreads: conservative flat estimate per spread
    margin += spread_count * 500  # £500 per spread (typical IG barrier spread margin)

    return margin


def _calc_parametric_var(
    positions: list[dict],
    total_nav: float,
    confidence: float = 1.645,  # 95% one-tail z-score
    daily_vol_assumption: float = 0.015,  # 1.5% daily vol per position as default
) -> Optional[float]:
    """
    Calculate 1-day parametric VaR at 95% confidence.

    Uses a simple undiversified approach (sum of individual VaRs) which is
    conservative — it ignores correlation benefits.

    For a proper VaR, we'd need historical returns per position, which requires
    the data provider. This is a starting point that B-004/Phase C can improve.

    Returns VaR as percentage of NAV, or None if no positions.
    """
    if not positions or total_nav <= 0:
        return None

    # Sum of individual position VaRs (undiversified — conservative)
    total_var = 0.0
    for p in positions:
        mv = abs(float(p.get("market_value", 0)))
        individual_var = mv * daily_vol_assumption * confidence
        total_var += individual_var

    var_pct = (total_var / total_nav) * 100.0
    return var_pct
