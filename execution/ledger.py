"""
Multi-broker ledger — unified position, cash, and NAV tracking across brokers.

A-005: Provides a single view of positions and cash across IG + IBKR (and future
brokers), with periodic sync from broker APIs and reconciliation diff reporting.
"""
import json
import logging
import uuid
from datetime import datetime, date
from typing import Optional

from data.trade_db import get_conn, DB_PATH

logger = logging.getLogger(__name__)


# ─── Broker account registry ────────────────────────────────────────────────


def register_broker_account(
    broker: str,
    account_id: str,
    account_type: str,
    currency: str = "GBP",
    label: str = "",
    db_path: str = DB_PATH,
) -> str:
    """
    Register a broker account in the ledger.

    Returns the internal account ID (UUID).
    """
    internal_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO broker_accounts (id, broker, account_id, account_type, currency, label, is_active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
           ON CONFLICT(broker, account_id) DO UPDATE SET
             account_type=excluded.account_type, currency=excluded.currency,
             label=excluded.label, updated_at=excluded.updated_at""",
        (internal_id, broker, account_id, account_type, currency, label, now, now),
    )
    conn.commit()

    # Fetch the actual ID (may differ if ON CONFLICT fired)
    row = conn.execute(
        "SELECT id FROM broker_accounts WHERE broker=? AND account_id=?",
        (broker, account_id),
    ).fetchone()
    conn.close()
    return row["id"] if row else internal_id


def get_broker_accounts(
    broker: Optional[str] = None,
    active_only: bool = True,
    db_path: str = DB_PATH,
) -> list[dict]:
    """List registered broker accounts."""
    conn = get_conn(db_path)
    sql = "SELECT * FROM broker_accounts WHERE 1=1"
    params: list = []
    if broker:
        sql += " AND broker=?"
        params.append(broker)
    if active_only:
        sql += " AND is_active=1"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Position sync ──────────────────────────────────────────────────────────


def sync_positions(
    broker_account_id: str,
    positions: list[dict],
    db_path: str = DB_PATH,
) -> dict:
    """
    Sync positions from a broker into the ledger.

    Each position dict should have: ticker, direction, quantity, avg_cost,
    and optionally: market_value, unrealised_pnl, currency, strategy, sleeve, con_id.

    Returns sync summary: {synced, inserted, updated, removed}.
    """
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)

    # Get existing positions for this account
    existing = conn.execute(
        "SELECT id, ticker, direction FROM broker_positions WHERE broker_account_id=?",
        (broker_account_id,),
    ).fetchall()
    existing_keys = {(r["ticker"], r["direction"]): r["id"] for r in existing}

    inserted = 0
    updated = 0
    incoming_keys = set()

    for p in positions:
        key = (p["ticker"], p["direction"])
        incoming_keys.add(key)

        conn.execute(
            """INSERT INTO broker_positions
               (broker_account_id, ticker, direction, quantity, avg_cost,
                market_value, unrealised_pnl, currency, strategy, sleeve, con_id, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(broker_account_id, ticker, direction) DO UPDATE SET
                 quantity=excluded.quantity, avg_cost=excluded.avg_cost,
                 market_value=excluded.market_value, unrealised_pnl=excluded.unrealised_pnl,
                 strategy=excluded.strategy, sleeve=excluded.sleeve,
                 con_id=excluded.con_id, last_synced_at=excluded.last_synced_at""",
            (
                broker_account_id,
                p["ticker"],
                p["direction"],
                p["quantity"],
                p.get("avg_cost", 0),
                p.get("market_value", 0),
                p.get("unrealised_pnl", 0),
                p.get("currency", "USD"),
                p.get("strategy"),
                p.get("sleeve"),
                p.get("con_id"),
                now,
            ),
        )

        if key in existing_keys:
            updated += 1
        else:
            inserted += 1

    # Remove positions that no longer exist at broker
    removed = 0
    for key, row_id in existing_keys.items():
        if key not in incoming_keys:
            conn.execute("DELETE FROM broker_positions WHERE id=?", (row_id,))
            removed += 1

    conn.commit()
    conn.close()

    summary = {
        "synced": len(positions),
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
    }
    logger.info(f"Position sync for {broker_account_id}: {summary}")
    return summary


def get_unified_positions(
    broker: Optional[str] = None,
    sleeve: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """
    Get all positions across all brokers in a unified view.

    Optionally filter by broker or sleeve.
    """
    conn = get_conn(db_path)
    sql = """
        SELECT bp.*, ba.broker, ba.account_id, ba.account_type, ba.label as account_label
        FROM broker_positions bp
        JOIN broker_accounts ba ON bp.broker_account_id = ba.id
        WHERE ba.is_active=1
    """
    params: list = []
    if broker:
        sql += " AND ba.broker=?"
        params.append(broker)
    if sleeve:
        sql += " AND bp.sleeve=?"
        params.append(sleeve)
    sql += " ORDER BY ba.broker, bp.ticker"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Cash balance sync ──────────────────────────────────────────────────────


def sync_cash_balance(
    broker_account_id: str,
    balance: float,
    buying_power: float = 0,
    currency: str = "GBP",
    db_path: str = DB_PATH,
):
    """Record a cash balance snapshot for a broker account."""
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO broker_cash_balances (broker_account_id, balance, buying_power, currency, synced_at)
           VALUES (?, ?, ?, ?, ?)""",
        (broker_account_id, balance, buying_power, currency, now),
    )
    conn.commit()
    conn.close()


def get_latest_cash_balances(db_path: str = DB_PATH) -> list[dict]:
    """Get the most recent cash balance for each broker account."""
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT bcb.*, ba.broker, ba.account_id, ba.account_type, ba.label as account_label
        FROM broker_cash_balances bcb
        JOIN broker_accounts ba ON bcb.broker_account_id = ba.id
        INNER JOIN (
            SELECT broker_account_id, MAX(synced_at) as max_synced
            FROM broker_cash_balances
            GROUP BY broker_account_id
        ) latest ON bcb.broker_account_id = latest.broker_account_id
                 AND bcb.synced_at = latest.max_synced
        WHERE ba.is_active=1
        ORDER BY ba.broker
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── NAV snapshots ──────────────────────────────────────────────────────────


def save_nav_snapshot(
    level: str,
    level_id: str,
    net_liquidation: float,
    cash: float = 0,
    positions_value: float = 0,
    unrealised_pnl: float = 0,
    realised_pnl: float = 0,
    currency: str = "GBP",
    broker: Optional[str] = None,
    account_type: Optional[str] = None,
    snapshot_date: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """
    Save a NAV snapshot at fund, sleeve, or account level.

    level: 'fund' / 'sleeve' / 'account'
    level_id: e.g. 'fund', 'sleeve_1', broker account internal id
    """
    snap_date = snapshot_date or date.today().isoformat()
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO nav_snapshots
           (snapshot_date, level, level_id, net_liquidation, cash, positions_value,
            unrealised_pnl, realised_pnl, currency, broker, account_type, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(snapshot_date, level, level_id) DO UPDATE SET
             net_liquidation=excluded.net_liquidation, cash=excluded.cash,
             positions_value=excluded.positions_value,
             unrealised_pnl=excluded.unrealised_pnl, realised_pnl=excluded.realised_pnl,
             created_at=excluded.created_at""",
        (snap_date, level, level_id, net_liquidation, cash, positions_value,
         unrealised_pnl, realised_pnl, currency, broker, account_type, now),
    )
    conn.commit()
    conn.close()


def get_nav_history(
    level: str = "fund",
    level_id: str = "fund",
    days: int = 30,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get NAV history for charting and reporting."""
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM nav_snapshots
           WHERE level=? AND level_id=?
           ORDER BY snapshot_date DESC LIMIT ?""",
        (level, level_id, days),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Reconciliation ─────────────────────────────────────────────────────────


def reconcile_positions(
    broker_account_id: str,
    broker_positions: list[dict],
    db_path: str = DB_PATH,
) -> dict:
    """
    Compare broker-reported positions against ledger and report mismatches.

    broker_positions: list of dicts with ticker, direction, quantity, avg_cost.

    Returns: {status, positions_checked, mismatches, details[]}.
    """
    conn = get_conn(db_path)

    # Get ledger positions for this account
    ledger_rows = conn.execute(
        "SELECT ticker, direction, quantity, avg_cost FROM broker_positions WHERE broker_account_id=?",
        (broker_account_id,),
    ).fetchall()
    ledger_map = {(r["ticker"], r["direction"]): dict(r) for r in ledger_rows}

    mismatches = []
    checked = 0

    # Check each broker position against ledger
    for bp in broker_positions:
        key = (bp["ticker"], bp["direction"])
        checked += 1

        if key not in ledger_map:
            mismatches.append({
                "type": "missing_in_ledger",
                "ticker": bp["ticker"],
                "direction": bp["direction"],
                "broker_qty": bp["quantity"],
                "ledger_qty": 0,
                "suggestion": f"Add {bp['ticker']} {bp['direction']} x{bp['quantity']} to ledger",
            })
            continue

        lp = ledger_map[key]
        if abs(lp["quantity"] - bp["quantity"]) > 0.001:
            mismatches.append({
                "type": "quantity_mismatch",
                "ticker": bp["ticker"],
                "direction": bp["direction"],
                "broker_qty": bp["quantity"],
                "ledger_qty": lp["quantity"],
                "suggestion": f"Update {bp['ticker']} qty from {lp['quantity']} to {bp['quantity']}",
            })

    # Check for ledger positions not in broker (phantom positions)
    broker_keys = {(bp["ticker"], bp["direction"]) for bp in broker_positions}
    for key, lp in ledger_map.items():
        if key not in broker_keys:
            checked += 1
            mismatches.append({
                "type": "phantom_in_ledger",
                "ticker": lp["ticker"],
                "direction": lp["direction"],
                "broker_qty": 0,
                "ledger_qty": lp["quantity"],
                "suggestion": f"Remove phantom {lp['ticker']} {lp['direction']} from ledger",
            })

    status = "clean" if not mismatches else "mismatch"
    report_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # Persist the reconciliation report
    conn.execute(
        """INSERT INTO reconciliation_reports
           (id, created_at, broker_account_id, status, positions_checked, mismatches_found, details)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (report_id, now, broker_account_id, status, checked, len(mismatches), json.dumps(mismatches)),
    )
    conn.commit()
    conn.close()

    result = {
        "report_id": report_id,
        "status": status,
        "positions_checked": checked,
        "mismatches": len(mismatches),
        "details": mismatches,
    }

    if mismatches:
        logger.warning(f"Reconciliation found {len(mismatches)} mismatches for {broker_account_id}")
    else:
        logger.info(f"Reconciliation clean for {broker_account_id}: {checked} positions checked")

    return result


def get_reconciliation_reports(
    broker_account_id: Optional[str] = None,
    limit: int = 10,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent reconciliation reports."""
    conn = get_conn(db_path)
    if broker_account_id:
        rows = conn.execute(
            "SELECT * FROM reconciliation_reports WHERE broker_account_id=? ORDER BY created_at DESC LIMIT ?",
            (broker_account_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM reconciliation_reports ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        if d.get("details"):
            d["details"] = json.loads(d["details"])
        results.append(d)
    return results
