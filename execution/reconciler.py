"""Broker snapshot reconciliation and live-equity helpers (D-003)."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional

from broker.base import AccountInfo, BaseBroker, Position
from data.trade_db import DB_PATH, get_conn
from execution import ledger

logger = logging.getLogger(__name__)


@dataclass
class ReconcileSummary:
    """Summary of one broker snapshot ingestion."""

    broker: str
    account_id: str
    broker_account_id: str
    positions_synced: int
    positions_inserted: int
    positions_updated: int
    positions_removed: int
    cash_balance: float
    net_liquidation: float

    def to_dict(self) -> dict[str, object]:
        return {
            "broker": self.broker,
            "account_id": self.account_id,
            "broker_account_id": self.broker_account_id,
            "positions_synced": self.positions_synced,
            "positions_inserted": self.positions_inserted,
            "positions_updated": self.positions_updated,
            "positions_removed": self.positions_removed,
            "cash_balance": self.cash_balance,
            "net_liquidation": self.net_liquidation,
        }


def sync_broker_snapshot(
    broker: BaseBroker,
    broker_name: str,
    account_id: str,
    account_type: str,
    sleeve: Optional[str] = None,
    db_path: str = DB_PATH,
) -> ReconcileSummary:
    """Ingest one broker account snapshot into ledger tables.

    Reads account + positions from the broker adapter, upserts account registry,
    replaces position rows, stores latest cash, and writes an account NAV snapshot.
    """
    is_connected = getattr(broker, "is_connected", None)
    already_connected = False
    if callable(is_connected):
        try:
            already_connected = bool(is_connected())
        except Exception:  # pragma: no cover - defensive adapter guard
            logger.warning("Broker is_connected() check failed for '%s'", broker_name, exc_info=True)

    if not already_connected and not broker.connect():
        raise RuntimeError(f"Broker connect failed for '{broker_name}'")

    acct = broker.get_account_info()
    positions = broker.get_positions()

    broker_account_id = ledger.register_broker_account(
        broker=broker_name,
        account_id=account_id,
        account_type=account_type,
        currency=acct.currency,
        db_path=db_path,
    )

    normalized = [
        _position_to_ledger_row(p, sleeve=sleeve, currency=acct.currency)
        for p in positions
    ]
    sync = ledger.sync_positions(
        broker_account_id=broker_account_id,
        positions=normalized,
        db_path=db_path,
    )

    ledger.sync_cash_balance(
        broker_account_id=broker_account_id,
        balance=float(acct.balance),
        buying_power=float(acct.equity),
        currency=acct.currency,
        db_path=db_path,
    )

    positions_value = sum(float(p.get("market_value", 0.0) or 0.0) for p in normalized)
    ledger.save_nav_snapshot(
        level="account",
        level_id=broker_account_id,
        net_liquidation=float(acct.equity),
        cash=float(acct.balance),
        positions_value=positions_value,
        unrealised_pnl=float(acct.unrealised_pnl),
        currency=acct.currency,
        broker=broker_name,
        account_type=account_type,
        db_path=db_path,
    )

    # Keep a fund-level snapshot available for risk/equity lookups.
    fund_cash = 0.0
    fund_positions_value = 0.0
    fund_equity = 0.0
    conn = None
    try:
        conn = get_conn(db_path)
        fund_cash, fund_positions_value = _read_live_components(conn)
        fund_equity = fund_cash + fund_positions_value
    except Exception as exc:
        logger.warning("Failed to compute fund snapshot decomposition: %s", exc)
    finally:
        if conn is not None:
            conn.close()

    if fund_equity > 0:
        ledger.save_nav_snapshot(
            level="fund",
            level_id="fund",
            net_liquidation=fund_equity,
            cash=fund_cash,
            positions_value=fund_positions_value,
            currency=acct.currency,
            db_path=db_path,
        )

    summary = ReconcileSummary(
        broker=broker_name,
        account_id=account_id,
        broker_account_id=broker_account_id,
        positions_synced=int(sync.get("synced", 0)),
        positions_inserted=int(sync.get("inserted", 0)),
        positions_updated=int(sync.get("updated", 0)),
        positions_removed=int(sync.get("removed", 0)),
        cash_balance=float(acct.balance),
        net_liquidation=float(acct.equity),
    )
    logger.info("Broker snapshot synced: %s", summary.to_dict())
    return summary


def compute_live_equity(default_equity: float, db_path: str = DB_PATH) -> float:
    """Compute live equity from latest ledger positions + cash snapshots.

    Falls back to ``default_equity`` when ledger data is unavailable.
    """
    conn = None
    try:
        conn = get_conn(db_path)
        total_cash, positions_value = _read_live_components(conn)
        live_equity = total_cash + positions_value
        if live_equity > 0:
            return live_equity
    except Exception as exc:
        logger.warning("Failed to compute live equity from ledger: %s", exc)
    finally:
        if conn is not None:
            conn.close()

    return float(default_equity)


def _position_to_ledger_row(
    position: Position,
    sleeve: Optional[str],
    currency: str,
) -> dict[str, object]:
    quantity = float(position.size or 0.0)
    avg_cost = float(position.entry_price or 0.0)
    unrealised_pnl = float(position.unrealised_pnl or 0.0)

    # Estimate mark-to-market notional from entry notional + current unrealised PnL.
    # For shorts, unrealised PnL has the opposite sign impact on notional.
    direction = str(position.direction or "").lower()
    if quantity <= 0:
        market_value = 0.0
    elif direction == "short":
        market_value = max((quantity * avg_cost) - unrealised_pnl, 0.0)
    else:
        market_value = max((quantity * avg_cost) + unrealised_pnl, 0.0)

    return {
        "ticker": str(position.ticker),
        "direction": str(position.direction),
        "quantity": quantity,
        "avg_cost": avg_cost,
        "market_value": market_value,
        "unrealised_pnl": unrealised_pnl,
        "currency": currency,
        "strategy": str(position.strategy or ""),
        "sleeve": sleeve,
        "con_id": str(position.deal_id or "") or None,
    }


def _read_live_components(conn) -> tuple[float, float]:
    """Return latest (cash, positions_value) totals across active broker accounts."""
    pos_row = conn.execute(
        """SELECT COALESCE(SUM(CAST(bp.market_value AS REAL)), 0) as total_positions
           FROM broker_positions bp
           JOIN broker_accounts ba ON bp.broker_account_id = ba.id
           WHERE ba.is_active = 1"""
    ).fetchone()
    positions_value = float(pos_row["total_positions"]) if pos_row else 0.0

    cash_row = conn.execute(
        """SELECT COALESCE(SUM(bcb.balance), 0) as total_cash
           FROM broker_cash_balances bcb
           INNER JOIN (
               SELECT broker_account_id, MAX(synced_at) as max_synced
               FROM broker_cash_balances
               GROUP BY broker_account_id
           ) latest ON bcb.broker_account_id = latest.broker_account_id
                    AND bcb.synced_at = latest.max_synced
           JOIN broker_accounts ba ON bcb.broker_account_id = ba.id
           WHERE ba.is_active = 1"""
    ).fetchone()
    total_cash = float(cash_row["total_cash"]) if cash_row else 0.0
    return total_cash, positions_value


def account_info_equity(acct: Optional[AccountInfo], fallback: float) -> float:
    """Return account equity when positive, else fallback."""
    if acct is None:
        return fallback
    value = float(getattr(acct, "equity", 0.0) or 0.0)
    if value > 0:
        return value
    return fallback
