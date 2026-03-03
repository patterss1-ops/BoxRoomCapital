"""
Daily NAV calculation from multi-broker ledger data.

B-003: Aggregates positions and cash across all broker accounts to produce
fund-level and sleeve-level NAV snapshots. Reads from the A-005 ledger tables
(broker_positions, broker_cash_balances, nav_snapshots) and writes to
fund_daily_report / sleeve_daily_report via trade_db persistence functions.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from data.trade_db import (
    DB_PATH,
    get_conn,
    get_fund_daily_reports,
    save_fund_daily_report,
    save_sleeve_daily_report,
)

logger = logging.getLogger(__name__)


@dataclass
class NAVSnapshot:
    """Point-in-time NAV snapshot for a fund or sleeve."""

    report_date: str
    total_nav: float
    total_cash: float
    total_positions_value: float
    unrealised_pnl: float
    realised_pnl: float
    daily_return_pct: Optional[float]
    drawdown_pct: float
    high_water_mark: float
    currency: str = "GBP"


@dataclass
class SleeveNAV:
    """Point-in-time NAV snapshot for a single sleeve."""

    sleeve: str
    nav: float
    positions_value: float
    cash_allocated: float
    unrealised_pnl: float
    realised_pnl: float
    weight_pct: float
    daily_return_pct: Optional[float]


def calculate_fund_nav(
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> NAVSnapshot:
    """
    Calculate total fund NAV by aggregating all active broker positions and cash.

    Reads the latest data from broker_positions and broker_cash_balances tables,
    aggregates across all active accounts, and returns a NAVSnapshot.

    The NAV formula:
        total_nav = total_cash + total_positions_value
    where:
        total_positions_value = sum(market_value) across all broker positions
        total_cash = sum(latest cash balance) across all broker accounts
    """
    snap_date = report_date or date.today().isoformat()
    conn = get_conn(db_path)

    # ── Aggregate positions value and unrealised P&L ──
    pos_row = conn.execute("""
        SELECT
            COALESCE(SUM(bp.market_value), 0) as total_positions_value,
            COALESCE(SUM(bp.unrealised_pnl), 0) as total_unrealised_pnl
        FROM broker_positions bp
        JOIN broker_accounts ba ON bp.broker_account_id = ba.id
        WHERE ba.is_active = 1
    """).fetchone()

    total_positions_value = float(pos_row["total_positions_value"])
    total_unrealised_pnl = float(pos_row["total_unrealised_pnl"])

    # ── Latest cash balance per account ──
    cash_row = conn.execute("""
        SELECT COALESCE(SUM(bcb.balance), 0) as total_cash
        FROM broker_cash_balances bcb
        INNER JOIN (
            SELECT broker_account_id, MAX(synced_at) as max_synced
            FROM broker_cash_balances
            GROUP BY broker_account_id
        ) latest ON bcb.broker_account_id = latest.broker_account_id
                 AND bcb.synced_at = latest.max_synced
        JOIN broker_accounts ba ON bcb.broker_account_id = ba.id
        WHERE ba.is_active = 1
    """).fetchone()

    total_cash = float(cash_row["total_cash"])

    # ── Realised P&L from today's closed trades ──
    pnl_row = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0) as realised_pnl
        FROM trades
        WHERE date(timestamp) = ? AND pnl IS NOT NULL
    """, (snap_date,)).fetchone()

    realised_pnl = float(pnl_row["realised_pnl"])

    conn.close()

    total_nav = total_cash + total_positions_value

    # ── Daily return calculation ──
    daily_return_pct = _calc_daily_return(total_nav, snap_date, db_path)

    # ── Drawdown and high water mark ──
    hwm, dd_pct = _calc_drawdown(total_nav, snap_date, db_path)

    return NAVSnapshot(
        report_date=snap_date,
        total_nav=total_nav,
        total_cash=total_cash,
        total_positions_value=total_positions_value,
        unrealised_pnl=total_unrealised_pnl,
        realised_pnl=realised_pnl,
        daily_return_pct=daily_return_pct,
        drawdown_pct=dd_pct,
        high_water_mark=hwm,
    )


def _compute_sleeve_realised_pnl(db_path: str = DB_PATH) -> dict[str, float]:
    """
    O-006: Compute realised P&L per sleeve from closed trades.

    Strategy → sleeve mapping comes from broker_positions (which has both
    strategy and sleeve columns). Trades missing a sleeve mapping fall into
    the 'default' sleeve.
    """
    conn = get_conn(db_path)

    # Build strategy → sleeve map from broker_positions
    strat_sleeve_rows = conn.execute("""
        SELECT DISTINCT strategy, COALESCE(sleeve, 'default') as sleeve
        FROM broker_positions
        WHERE strategy IS NOT NULL
    """).fetchall()
    strategy_to_sleeve: dict[str, str] = {}
    for row in strat_sleeve_rows:
        if row["strategy"]:
            strategy_to_sleeve[row["strategy"]] = row["sleeve"]

    # Sum realised P&L from closed trades grouped by strategy
    pnl_rows = conn.execute("""
        SELECT strategy, COALESCE(SUM(pnl), 0) as total_pnl
        FROM trades
        WHERE action = 'CLOSE' AND pnl IS NOT NULL
        GROUP BY strategy
    """).fetchall()
    conn.close()

    sleeve_pnl: dict[str, float] = {}
    for row in pnl_rows:
        strat = row["strategy"]
        sleeve = strategy_to_sleeve.get(strat, "default")
        sleeve_pnl[sleeve] = sleeve_pnl.get(sleeve, 0.0) + float(row["total_pnl"])

    return sleeve_pnl


def calculate_sleeve_navs(
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[SleeveNAV]:
    """
    Calculate per-sleeve NAV breakdown.

    Groups positions by their sleeve attribution in broker_positions.
    Cash is allocated proportionally to sleeve weight (positions_value / total).
    """
    snap_date = report_date or date.today().isoformat()
    conn = get_conn(db_path)

    # ── Sleeve-level position aggregation ──
    rows = conn.execute("""
        SELECT
            COALESCE(bp.sleeve, 'unassigned') as sleeve,
            COALESCE(SUM(bp.market_value), 0) as positions_value,
            COALESCE(SUM(bp.unrealised_pnl), 0) as unrealised_pnl
        FROM broker_positions bp
        JOIN broker_accounts ba ON bp.broker_account_id = ba.id
        WHERE ba.is_active = 1
        GROUP BY COALESCE(bp.sleeve, 'unassigned')
    """).fetchall()

    # ── Total cash ──
    cash_row = conn.execute("""
        SELECT COALESCE(SUM(bcb.balance), 0) as total_cash
        FROM broker_cash_balances bcb
        INNER JOIN (
            SELECT broker_account_id, MAX(synced_at) as max_synced
            FROM broker_cash_balances
            GROUP BY broker_account_id
        ) latest ON bcb.broker_account_id = latest.broker_account_id
                 AND bcb.synced_at = latest.max_synced
        JOIN broker_accounts ba ON bcb.broker_account_id = ba.id
        WHERE ba.is_active = 1
    """).fetchone()
    total_cash = float(cash_row["total_cash"])

    conn.close()

    # O-006: Get realised P&L per sleeve from closed trades
    sleeve_rpnl = _compute_sleeve_realised_pnl(db_path)

    # ── Build sleeve NAVs ──
    sleeve_data = []
    total_positions = sum(float(r["positions_value"]) for r in rows)

    for r in rows:
        s = str(r["sleeve"])
        pv = float(r["positions_value"])
        upnl = float(r["unrealised_pnl"])

        # Allocate cash proportionally to position weight
        if total_positions > 0:
            cash_alloc = total_cash * (pv / total_positions)
        else:
            # No positions — split cash equally
            cash_alloc = total_cash / max(len(rows), 1)

        nav = pv + cash_alloc
        total_nav = total_positions + total_cash

        weight = (nav / total_nav * 100.0) if total_nav > 0 else 0.0

        # Sleeve daily return from previous sleeve report
        daily_ret = _calc_sleeve_daily_return(s, nav, snap_date, db_path)

        sleeve_data.append(SleeveNAV(
            sleeve=s,
            nav=nav,
            positions_value=pv,
            cash_allocated=cash_alloc,
            unrealised_pnl=upnl,
            realised_pnl=round(sleeve_rpnl.get(s, 0.0), 2),
            weight_pct=round(weight, 2),
            daily_return_pct=daily_ret,
        ))

    return sleeve_data


def persist_nav_report(
    nav: NAVSnapshot,
    sleeves: list[SleeveNAV],
    db_path: str = DB_PATH,
):
    """
    Persist fund and sleeve NAV reports to the database.

    Writes to fund_daily_report and sleeve_daily_report tables.
    """
    save_fund_daily_report(
        report_date=nav.report_date,
        total_nav=nav.total_nav,
        total_cash=nav.total_cash,
        total_positions_value=nav.total_positions_value,
        unrealised_pnl=nav.unrealised_pnl,
        realised_pnl=nav.realised_pnl,
        daily_return_pct=nav.daily_return_pct,
        drawdown_pct=nav.drawdown_pct,
        high_water_mark=nav.high_water_mark,
        currency=nav.currency,
        db_path=db_path,
    )

    for s in sleeves:
        save_sleeve_daily_report(
            report_date=nav.report_date,
            sleeve=s.sleeve,
            nav=s.nav,
            positions_value=s.positions_value,
            cash_allocated=s.cash_allocated,
            unrealised_pnl=s.unrealised_pnl,
            realised_pnl=s.realised_pnl,
            weight_pct=s.weight_pct,
            daily_return_pct=s.daily_return_pct,
            db_path=db_path,
        )

    logger.info(
        f"NAV report persisted for {nav.report_date}: "
        f"NAV={nav.total_nav:.2f}, sleeves={len(sleeves)}"
    )


def run_daily_nav(
    report_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> dict:
    """
    End-of-day NAV job: calculate fund + sleeve NAVs and persist.

    Returns a summary dict for logging/Telegram notifications.
    """
    nav = calculate_fund_nav(report_date=report_date, db_path=db_path)
    sleeves = calculate_sleeve_navs(report_date=report_date, db_path=db_path)
    persist_nav_report(nav, sleeves, db_path=db_path)

    return {
        "report_date": nav.report_date,
        "total_nav": round(nav.total_nav, 2),
        "total_cash": round(nav.total_cash, 2),
        "total_positions_value": round(nav.total_positions_value, 2),
        "unrealised_pnl": round(nav.unrealised_pnl, 2),
        "realised_pnl": round(nav.realised_pnl, 2),
        "daily_return_pct": round(nav.daily_return_pct, 4) if nav.daily_return_pct is not None else None,
        "drawdown_pct": round(nav.drawdown_pct, 4),
        "high_water_mark": round(nav.high_water_mark, 2),
        "sleeves": [
            {
                "sleeve": s.sleeve,
                "nav": round(s.nav, 2),
                "weight_pct": round(s.weight_pct, 2),
                "daily_return_pct": round(s.daily_return_pct, 4) if s.daily_return_pct is not None else None,
            }
            for s in sleeves
        ],
    }


# ─── Internal helpers ─────────────────────────────────────────────────────


def _calc_daily_return(
    current_nav: float,
    report_date: str,
    db_path: str,
) -> Optional[float]:
    """Calculate daily return % from previous fund_daily_report."""
    reports = get_fund_daily_reports(days=2, db_path=db_path)
    # Find previous day (not same date)
    for r in reports:
        if r["report_date"] < report_date:
            prev_nav = r["total_nav"]
            if prev_nav > 0:
                return ((current_nav - prev_nav) / prev_nav) * 100.0
            break
    return None


def _calc_drawdown(
    current_nav: float,
    report_date: str,
    db_path: str,
) -> tuple[float, float]:
    """
    Calculate high water mark and drawdown percentage.

    Returns (high_water_mark, drawdown_pct).
    """
    reports = get_fund_daily_reports(days=9999, db_path=db_path)

    # Find historical high water mark (exclude today — we'll update it)
    prev_hwm = 0.0
    for r in reports:
        if r["report_date"] < report_date:
            if r["high_water_mark"] > prev_hwm:
                prev_hwm = r["high_water_mark"]
            if r["total_nav"] > prev_hwm:
                prev_hwm = r["total_nav"]

    hwm = max(current_nav, prev_hwm)
    dd_pct = ((current_nav - hwm) / hwm * 100.0) if hwm > 0 else 0.0

    return hwm, dd_pct


def _calc_sleeve_daily_return(
    sleeve: str,
    current_nav: float,
    report_date: str,
    db_path: str,
) -> Optional[float]:
    """Calculate daily return % for a single sleeve from previous sleeve_daily_report."""
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT nav FROM sleeve_daily_report
           WHERE sleeve=? AND report_date < ?
           ORDER BY report_date DESC LIMIT 1""",
        (sleeve, report_date),
    ).fetchone()
    conn.close()

    if row and row["nav"] > 0:
        return ((current_nav - row["nav"]) / row["nav"]) * 100.0
    return None
