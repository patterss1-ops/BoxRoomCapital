"""
Advisory Holdings Service — manual investment portfolio tracking across
ISA / SIPP / GIA tax wrappers with live pricing via yfinance.

Tables (created lazily via _ensure_tables):
  advisory_holdings      — current positions (derived from transactions)
  advisory_transactions  — full audit trail of buys/sells/deposits/etc.
  advisory_price_cache   — cached live prices (15-min TTL)
  wrapper_allowances     — ISA/SIPP contribution tracking per tax year
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from data.trade_db import get_conn, DB_PATH

try:
    import yfinance as yf

    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRICE_CACHE_TTL_MINUTES = 15

WRAPPER_ALLOWANCES = {
    "ISA": 20_000,
    "SIPP": 60_000,
    "GIA": None,  # no statutory limit
}

VALID_WRAPPERS = {"ISA", "SIPP", "GIA"}

VALID_TX_TYPES = {
    "buy", "sell", "deposit", "withdrawal",
    "dividend", "fee", "transfer_in", "transfer_out",
}


def _current_tax_year() -> str:
    """Return the UK tax year string, e.g. '2025/26'.

    UK tax year runs 6 Apr – 5 Apr.
    """
    now = datetime.now(timezone.utc)
    if now.month > 4 or (now.month == 4 and now.day >= 6):
        start = now.year
    else:
        start = now.year - 1
    return f"{start}/{str(start + 1)[-2:]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_tables_ensured: set[str] = set()


def _ensure_tables(db_path: str = DB_PATH) -> None:
    if db_path in _tables_ensured:
        return
    conn = get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS advisory_holdings (
            id TEXT PRIMARY KEY,
            wrapper TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            quantity REAL NOT NULL,
            avg_cost REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'GBP',
            purchase_date TEXT,
            notes TEXT,
            benchmark_ticker TEXT,
            target_return_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            close_price REAL,
            closed_at TEXT,
            realized_pnl REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS advisory_transactions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            wrapper TEXT NOT NULL,
            tx_type TEXT NOT NULL,
            ticker TEXT,
            quantity REAL,
            price REAL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'GBP',
            notes TEXT,
            reference TEXT
        );

        CREATE TABLE IF NOT EXISTS advisory_price_cache (
            ticker TEXT PRIMARY KEY,
            price REAL NOT NULL,
            currency TEXT DEFAULT 'GBP',
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wrapper_allowances (
            id TEXT PRIMARY KEY,
            tax_year TEXT NOT NULL,
            wrapper TEXT NOT NULL,
            annual_limit REAL NOT NULL DEFAULT 0,
            used REAL DEFAULT 0,
            notes TEXT,
            UNIQUE(tax_year, wrapper)
        );
    """)
    _tables_ensured.add(db_path)


# ---------------------------------------------------------------------------
# Ticker validation
# ---------------------------------------------------------------------------

def _validate_ticker(ticker: str) -> bool:
    """Check that *ticker* resolves to something in yfinance."""
    if not _YF_AVAILABLE:
        log.warning("yfinance not installed — skipping ticker validation for %s", ticker)
        return True
    try:
        info = yf.Ticker(ticker).info
        # yfinance returns an empty-ish dict for invalid tickers
        return bool(info and info.get("regularMarketPrice") is not None)
    except Exception as exc:
        log.warning("Ticker validation failed for %s: %s", ticker, exc)
        return False


# ---------------------------------------------------------------------------
# Internal transaction writer
# ---------------------------------------------------------------------------

def _write_tx(
    conn,
    wrapper: str,
    tx_type: str,
    amount: float,
    ticker: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    currency: str = "GBP",
    notes: str | None = None,
    reference: str | None = None,
) -> str:
    """Insert a row into advisory_transactions. Returns the tx id."""
    tx_id = _make_id()
    conn.execute(
        """INSERT INTO advisory_transactions
           (id, created_at, wrapper, tx_type, ticker, quantity, price,
            amount, currency, notes, reference)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tx_id, _now_iso(), wrapper, tx_type, ticker, quantity, price,
         amount, currency, notes, reference),
    )
    return tx_id


# ---------------------------------------------------------------------------
# Position upsert (buy) and reduction (sell)
# ---------------------------------------------------------------------------

def _upsert_position_buy(
    conn,
    db_path: str,
    wrapper: str,
    ticker: str,
    quantity: float,
    price: float,
    name: str | None = None,
    currency: str = "GBP",
    benchmark_ticker: str | None = None,
    target_return_pct: float | None = None,
) -> str:
    """Upsert an open position for wrapper+ticker. Returns the holding id."""
    row = conn.execute(
        """SELECT id, quantity, avg_cost FROM advisory_holdings
           WHERE wrapper = ? AND ticker = ? AND status = 'open'""",
        (wrapper, ticker),
    ).fetchone()

    now = _now_iso()

    if row:
        old_qty = row["quantity"]
        old_avg = row["avg_cost"]
        new_qty = old_qty + quantity
        new_avg = (old_qty * old_avg + quantity * price) / new_qty
        conn.execute(
            """UPDATE advisory_holdings
               SET quantity = ?, avg_cost = ?, updated_at = ?
               WHERE id = ?""",
            (new_qty, round(new_avg, 6), now, row["id"]),
        )
        return row["id"]
    else:
        holding_id = _make_id()
        conn.execute(
            """INSERT INTO advisory_holdings
               (id, wrapper, ticker, name, quantity, avg_cost, currency,
                purchase_date, notes, benchmark_ticker, target_return_pct,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
            (holding_id, wrapper, ticker, name, quantity, price, currency,
             now[:10], None, benchmark_ticker, target_return_pct, now, now),
        )
        return holding_id


def _reduce_position_sell(
    conn,
    wrapper: str,
    ticker: str,
    quantity: float,
    price: float,
) -> tuple[str, float]:
    """Reduce an open position. Returns (holding_id, realized_pnl).

    Raises ValueError if no open position or insufficient quantity.
    """
    row = conn.execute(
        """SELECT id, quantity, avg_cost FROM advisory_holdings
           WHERE wrapper = ? AND ticker = ? AND status = 'open'""",
        (wrapper, ticker),
    ).fetchone()

    if row is None:
        raise ValueError(
            f"No open position for {ticker} in {wrapper}"
        )

    if quantity > row["quantity"] + 1e-9:
        raise ValueError(
            f"Cannot sell {quantity} of {ticker} in {wrapper} — "
            f"only {row['quantity']} held"
        )

    realized_pnl = round((price - row["avg_cost"]) * quantity, 2)
    new_qty = row["quantity"] - quantity
    now = _now_iso()

    if new_qty < 1e-9:
        # Close the position
        conn.execute(
            """UPDATE advisory_holdings
               SET quantity = 0, status = 'closed', close_price = ?,
                   closed_at = ?, realized_pnl = COALESCE(realized_pnl, 0) + ?,
                   updated_at = ?
               WHERE id = ?""",
            (price, now, realized_pnl, now, row["id"]),
        )
    else:
        conn.execute(
            """UPDATE advisory_holdings
               SET quantity = ?,
                   realized_pnl = COALESCE(realized_pnl, 0) + ?,
                   updated_at = ?
               WHERE id = ?""",
            (new_qty, realized_pnl, now, row["id"]),
        )

    return row["id"], realized_pnl


# ---------------------------------------------------------------------------
# Public transaction functions (new API)
# ---------------------------------------------------------------------------

def record_buy(
    db_path: str = DB_PATH,
    wrapper: str = "",
    ticker: str = "",
    quantity: float = 0.0,
    price: float = 0.0,
    name: str | None = None,
    currency: str = "GBP",
    notes: str | None = None,
    reference: str | None = None,
    benchmark_ticker: str | None = None,
    target_return_pct: float | None = None,
) -> str:
    """Record a buy transaction and upsert the position. Returns tx_id."""
    _ensure_tables(db_path)
    wrapper = wrapper.upper()
    if wrapper not in VALID_WRAPPERS:
        raise ValueError(f"Invalid wrapper '{wrapper}'. Must be one of {VALID_WRAPPERS}")
    ticker = ticker.upper()

    amount = round(quantity * price, 2)
    conn = get_conn(db_path)

    tx_id = _write_tx(
        conn, wrapper, "buy", -amount,
        ticker=ticker, quantity=quantity, price=price,
        currency=currency, notes=notes, reference=reference,
    )
    _upsert_position_buy(
        conn, db_path, wrapper, ticker, quantity, price,
        name=name, currency=currency,
        benchmark_ticker=benchmark_ticker,
        target_return_pct=target_return_pct,
    )
    conn.commit()
    log.info("Recorded buy tx %s: %s x%.4f @ %.4f in %s", tx_id, ticker, quantity, price, wrapper)
    return tx_id


def record_sell(
    db_path: str = DB_PATH,
    wrapper: str = "",
    ticker: str = "",
    quantity: float = 0.0,
    price: float = 0.0,
    notes: str | None = None,
    reference: str | None = None,
) -> dict:
    """Record a sell transaction and update the position.

    Returns {"tx_id": str, "realized_pnl": float}.
    """
    _ensure_tables(db_path)
    wrapper = wrapper.upper()
    if wrapper not in VALID_WRAPPERS:
        raise ValueError(f"Invalid wrapper '{wrapper}'. Must be one of {VALID_WRAPPERS}")
    ticker = ticker.upper()

    amount = round(quantity * price, 2)
    conn = get_conn(db_path)

    tx_id = _write_tx(
        conn, wrapper, "sell", amount,
        ticker=ticker, quantity=quantity, price=price,
        notes=notes, reference=reference,
    )
    holding_id, realized_pnl = _reduce_position_sell(conn, wrapper, ticker, quantity, price)
    conn.commit()
    log.info("Recorded sell tx %s: %s x%.4f @ %.4f in %s (P&L: %.2f)",
             tx_id, ticker, quantity, price, wrapper, realized_pnl)
    return {"tx_id": tx_id, "realized_pnl": realized_pnl}


def record_cash(
    db_path: str = DB_PATH,
    wrapper: str = "",
    tx_type: str = "",
    amount: float = 0.0,
    notes: str | None = None,
    reference: str | None = None,
) -> str:
    """Record a deposit or withdrawal. Auto-updates ISA/SIPP allowance.

    *amount* should be positive for deposits, negative for withdrawals,
    but tx_type takes precedence: deposits become positive, withdrawals negative.
    Returns tx_id.
    """
    _ensure_tables(db_path)
    wrapper = wrapper.upper()
    if wrapper not in VALID_WRAPPERS:
        raise ValueError(f"Invalid wrapper '{wrapper}'. Must be one of {VALID_WRAPPERS}")
    if tx_type not in ("deposit", "withdrawal"):
        raise ValueError(f"tx_type must be 'deposit' or 'withdrawal', got '{tx_type}'")

    # Normalise: deposits positive, withdrawals negative
    signed_amount = abs(amount) if tx_type == "deposit" else -abs(amount)

    conn = get_conn(db_path)
    tx_id = _write_tx(
        conn, wrapper, tx_type, signed_amount,
        notes=notes, reference=reference,
    )

    # Auto-update ISA/SIPP allowance for deposits
    if tx_type == "deposit" and wrapper in ("ISA", "SIPP"):
        tax_year = _current_tax_year()
        now = _now_iso()
        limit_val = WRAPPER_ALLOWANCES.get(wrapper) or 0
        conn.execute(
            """INSERT INTO wrapper_allowances (id, tax_year, wrapper, annual_limit, used)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(tax_year, wrapper) DO UPDATE
               SET used = used + excluded.used""",
            (_make_id(), tax_year, wrapper, limit_val, abs(amount)),
        )

    conn.commit()
    log.info("Recorded %s tx %s: %s %.2f in %s", tx_type, tx_id,
             "+" if signed_amount > 0 else "", signed_amount, wrapper)
    return tx_id


def record_dividend(
    db_path: str = DB_PATH,
    wrapper: str = "",
    ticker: str = "",
    amount: float = 0.0,
    notes: str | None = None,
) -> str:
    """Record a dividend payment. Returns tx_id."""
    _ensure_tables(db_path)
    wrapper = wrapper.upper()
    if wrapper not in VALID_WRAPPERS:
        raise ValueError(f"Invalid wrapper '{wrapper}'. Must be one of {VALID_WRAPPERS}")
    ticker = ticker.upper()

    conn = get_conn(db_path)
    tx_id = _write_tx(
        conn, wrapper, "dividend", abs(amount),
        ticker=ticker, notes=notes,
    )
    conn.commit()
    log.info("Recorded dividend tx %s: %s %.2f in %s", tx_id, ticker, amount, wrapper)
    return tx_id


# ---------------------------------------------------------------------------
# Transaction queries
# ---------------------------------------------------------------------------

def get_transactions(
    db_path: str = DB_PATH,
    wrapper: str | None = None,
    tx_type: str | None = None,
    ticker: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return transactions with optional filters, most recent first."""
    _ensure_tables(db_path)
    conn = get_conn(db_path)

    query = "SELECT * FROM advisory_transactions WHERE 1=1"
    params: list = []

    if wrapper:
        query += " AND wrapper = ?"
        params.append(wrapper.upper())
    if tx_type:
        query += " AND tx_type = ?"
        params.append(tx_type)
    if ticker:
        query += " AND ticker = ?"
        params.append(ticker.upper())

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_transaction_summary(
    db_path: str = DB_PATH,
    wrapper: str | None = None,
) -> dict:
    """Aggregate totals by tx_type.

    Returns dict with keys: deposits, withdrawals, dividends, fees,
    buys, sells, realized_pnl, net_cash_flow.
    """
    _ensure_tables(db_path)
    conn = get_conn(db_path)

    query = "SELECT tx_type, SUM(amount) as total FROM advisory_transactions"
    params: list = []

    if wrapper:
        query += " WHERE wrapper = ?"
        params.append(wrapper.upper())

    query += " GROUP BY tx_type"
    rows = conn.execute(query, params).fetchall()

    totals = {r["tx_type"]: r["total"] or 0.0 for r in rows}

    deposits = totals.get("deposit", 0.0)
    withdrawals = totals.get("withdrawal", 0.0)
    dividends = totals.get("dividend", 0.0)
    fees = totals.get("fee", 0.0)
    buys = totals.get("buy", 0.0)      # negative (outflow)
    sells = totals.get("sell", 0.0)     # positive (inflow)

    return {
        "deposits": round(deposits, 2),
        "withdrawals": round(withdrawals, 2),
        "dividends": round(dividends, 2),
        "fees": round(fees, 2),
        "buys": round(buys, 2),
        "sells": round(sells, 2),
        "realized_pnl": round(buys + sells, 2),
        "net_cash_flow": round(deposits + withdrawals + dividends + fees, 2),
    }


def get_wrapper_cash_summary(db_path: str = DB_PATH) -> dict:
    """Deposits minus withdrawals per wrapper.

    Returns {wrapper: {"deposits": float, "withdrawals": float, "net": float}}.
    """
    _ensure_tables(db_path)
    conn = get_conn(db_path)

    rows = conn.execute(
        """SELECT wrapper, tx_type, SUM(amount) as total
           FROM advisory_transactions
           WHERE tx_type IN ('deposit', 'withdrawal')
           GROUP BY wrapper, tx_type"""
    ).fetchall()

    result: dict[str, dict] = {}
    for r in rows:
        w = r["wrapper"]
        if w not in result:
            result[w] = {"deposits": 0.0, "withdrawals": 0.0, "net": 0.0}
        if r["tx_type"] == "deposit":
            result[w]["deposits"] = round(r["total"] or 0.0, 2)
        else:
            result[w]["withdrawals"] = round(r["total"] or 0.0, 2)

    for w_data in result.values():
        w_data["net"] = round(w_data["deposits"] + w_data["withdrawals"], 2)

    return result


# ---------------------------------------------------------------------------
# Holdings CRUD (backward-compatible API)
# ---------------------------------------------------------------------------

def add_holding(
    db_path: str = DB_PATH,
    wrapper: str = "",
    ticker: str = "",
    quantity: float = 0.0,
    avg_cost: float = 0.0,
    name: Optional[str] = None,
    purchase_date: Optional[str] = None,
    currency: str = "GBP",
    notes: Optional[str] = None,
    benchmark_ticker: Optional[str] = None,
    target_return_pct: Optional[float] = None,
) -> str:
    """Add a manual holding. Returns the new holding ID.

    Now also records a buy transaction for the audit trail AND upserts the
    position (averaging cost if same wrapper+ticker already exists).

    Raises ValueError if wrapper is invalid or ticker cannot be resolved.
    """
    _ensure_tables(db_path)

    wrapper = wrapper.upper()
    if wrapper not in VALID_WRAPPERS:
        raise ValueError(f"Invalid wrapper '{wrapper}'. Must be one of {VALID_WRAPPERS}")

    ticker = ticker.upper()
    if not _validate_ticker(ticker):
        raise ValueError(f"Ticker '{ticker}' could not be validated via yfinance")

    now = _now_iso()
    amount = round(quantity * avg_cost, 2)

    conn = get_conn(db_path)

    # Write buy transaction
    tx_id = _write_tx(
        conn, wrapper, "buy", -amount,
        ticker=ticker, quantity=quantity, price=avg_cost,
        currency=currency, notes=notes,
    )

    # Upsert position
    holding_id = _upsert_position_buy(
        conn, db_path, wrapper, ticker, quantity, avg_cost,
        name=name, currency=currency,
        benchmark_ticker=benchmark_ticker,
        target_return_pct=target_return_pct,
    )

    # If purchase_date was explicitly given, update the holding row
    if purchase_date:
        conn.execute(
            "UPDATE advisory_holdings SET purchase_date = ? WHERE id = ?",
            (purchase_date, holding_id),
        )

    conn.commit()
    log.info("Added holding %s: %s x%.2f @ %.2f in %s (tx: %s)",
             holding_id, ticker, quantity, avg_cost, wrapper, tx_id)
    return holding_id


def close_holding(holding_id: str, close_price: float, db_path: str = DB_PATH) -> dict:
    """Mark a holding as closed. Returns dict with realized P&L details.

    Also records a sell transaction for the full remaining quantity.
    """
    _ensure_tables(db_path)
    conn = get_conn(db_path)

    row = conn.execute(
        "SELECT * FROM advisory_holdings WHERE id = ?", (holding_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Holding '{holding_id}' not found")
    if row["status"] != "open":
        raise ValueError(f"Holding '{holding_id}' is already {row['status']}")

    quantity = row["quantity"]
    realized_pnl = round((close_price - row["avg_cost"]) * quantity, 2)
    now = _now_iso()
    amount = round(quantity * close_price, 2)

    # Record sell transaction
    tx_id = _write_tx(
        conn, row["wrapper"], "sell", amount,
        ticker=row["ticker"], quantity=quantity, price=close_price,
    )

    # Close the position
    conn.execute(
        """UPDATE advisory_holdings
           SET status = 'closed', close_price = ?, closed_at = ?,
               quantity = 0,
               realized_pnl = COALESCE(realized_pnl, 0) + ?,
               updated_at = ?
           WHERE id = ?""",
        (close_price, now, realized_pnl, now, holding_id),
    )
    conn.commit()

    result = {
        "holding_id": holding_id,
        "ticker": row["ticker"],
        "wrapper": row["wrapper"],
        "quantity": quantity,
        "avg_cost": row["avg_cost"],
        "close_price": close_price,
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": round((close_price / row["avg_cost"] - 1) * 100, 2) if row["avg_cost"] else 0.0,
        "tx_id": tx_id,
    }
    log.info("Closed holding %s (%s): P&L %.2f (tx: %s)",
             holding_id, row["ticker"], realized_pnl, tx_id)
    return result


def get_holdings(
    db_path: str = DB_PATH,
    wrapper: Optional[str] = None,
    status: str = "open",
) -> list[dict]:
    """Return holdings filtered by wrapper and/or status."""
    _ensure_tables(db_path)
    conn = get_conn(db_path)

    query = "SELECT * FROM advisory_holdings WHERE status = ?"
    params: list = [status]

    if wrapper:
        query += " AND wrapper = ?"
        params.append(wrapper.upper())

    query += " ORDER BY wrapper, ticker"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Price fetching with cache
# ---------------------------------------------------------------------------

def fetch_live_prices(
    tickers: list[str],
    db_path: Optional[str] = None,
) -> dict[str, float]:
    """Batch-fetch live prices via yfinance.

    Prices are cached in advisory_price_cache with a 15-minute TTL.
    Returns {ticker: price} for every ticker that could be resolved.
    """
    effective_db = db_path or DB_PATH
    _ensure_tables(effective_db)
    conn = get_conn(effective_db)

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=PRICE_CACHE_TTL_MINUTES)).isoformat()
    prices: dict[str, float] = {}
    need_fetch: list[str] = []

    # Check cache first
    for t in tickers:
        row = conn.execute(
            "SELECT price FROM advisory_price_cache WHERE ticker = ? AND fetched_at > ?",
            (t, cutoff),
        ).fetchone()
        if row:
            prices[t] = row["price"]
        else:
            need_fetch.append(t)

    if not need_fetch:
        return prices

    if not _YF_AVAILABLE:
        log.warning("yfinance not installed — cannot fetch live prices for %s", need_fetch)
        return prices

    # Batch download
    try:
        data = yf.download(need_fetch, period="1d", progress=False, threads=True)
        now_iso = _now_iso()

        if data.empty:
            return prices

        has_multiindex = isinstance(data.columns, __import__("pandas").MultiIndex)

        for ticker in need_fetch:
            try:
                if has_multiindex:
                    close_col = ("Close", ticker)
                    if close_col not in data.columns:
                        continue
                    val = data[close_col].dropna()
                else:
                    # Flat columns (single ticker download on some yfinance versions)
                    if "Close" not in data.columns:
                        continue
                    val = data["Close"].dropna()

                if len(val) == 0:
                    continue

                raw = val.iloc[-1]
                # Handle scalar or single-element Series
                if hasattr(raw, "item"):
                    price = float(raw.item())
                else:
                    price = float(raw)

                prices[ticker] = price
                conn.execute(
                    """INSERT OR REPLACE INTO advisory_price_cache
                       (ticker, price, fetched_at) VALUES (?, ?, ?)""",
                    (ticker, price, now_iso),
                )
            except Exception as exc:
                log.warning("Failed to extract price for %s: %s", ticker, exc)

        conn.commit()
    except Exception as exc:
        log.error("yfinance batch download failed: %s", exc)

    return prices


# ---------------------------------------------------------------------------
# Portfolio analytics
# ---------------------------------------------------------------------------

def calculate_portfolio_snapshot(db_path: str = DB_PATH) -> dict:
    """Build a full portfolio snapshot with per-wrapper breakdowns.

    Returns nested dict with total_value, total_cost, total_pnl, wrappers,
    and allowances.
    """
    _ensure_tables(db_path)
    holdings = get_holdings(db_path, status="open")
    if not holdings:
        return {
            "total_value": 0.0,
            "total_cost": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "wrappers": {},
            "allowances": get_wrapper_summary(db_path),
        }

    # Fetch live prices for all open tickers
    tickers = list({h["ticker"] for h in holdings})
    prices = fetch_live_prices(tickers, db_path)

    wrappers: dict[str, dict] = {}

    for h in holdings:
        w = h["wrapper"]
        if w not in wrappers:
            wrappers[w] = {"value": 0.0, "cost": 0.0, "pnl": 0.0, "holdings": []}

        live_price = prices.get(h["ticker"])
        cost = h["quantity"] * h["avg_cost"]
        value = h["quantity"] * live_price if live_price else cost
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost else 0.0

        holding_entry = {
            "id": h["id"],
            "ticker": h["ticker"],
            "name": h["name"],
            "quantity": h["quantity"],
            "avg_cost": h["avg_cost"],
            "live_price": live_price,
            "cost": round(cost, 2),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "benchmark_ticker": h.get("benchmark_ticker"),
            "target_return_pct": h.get("target_return_pct"),
            "currency": h["currency"],
        }
        wrappers[w]["holdings"].append(holding_entry)
        wrappers[w]["value"] += value
        wrappers[w]["cost"] += cost
        wrappers[w]["pnl"] += pnl

    # Compute per-wrapper percentages and round
    for w_data in wrappers.values():
        w_data["value"] = round(w_data["value"], 2)
        w_data["cost"] = round(w_data["cost"], 2)
        w_data["pnl"] = round(w_data["pnl"], 2)
        w_data["pnl_pct"] = round((w_data["pnl"] / w_data["cost"] * 100) if w_data["cost"] else 0.0, 2)

    total_value = sum(w["value"] for w in wrappers.values())
    total_cost = sum(w["cost"] for w in wrappers.values())
    total_pnl = total_value - total_cost

    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_cost * 100) if total_cost else 0.0, 2),
        "wrappers": wrappers,
        "allowances": get_wrapper_summary(db_path),
    }


def calculate_performance_vs_benchmark(
    db_path: str = DB_PATH,
    ticker: str = "",
    benchmark: str = "",
    since_date: str = "",
) -> dict:
    """Compare *ticker* vs *benchmark* return since *since_date*.

    Returns dict with ticker_return, benchmark_return, and relative_return
    (all as percentages).
    """
    if not _YF_AVAILABLE:
        return {
            "ticker": ticker,
            "benchmark": benchmark,
            "since": since_date,
            "ticker_return": None,
            "benchmark_return": None,
            "relative_return": None,
            "error": "yfinance not available",
        }

    def _return_since(symbol: str, start: str) -> Optional[float]:
        try:
            hist = yf.Ticker(symbol).history(start=start)
            if hist.empty or len(hist) < 2:
                return None
            first = float(hist["Close"].iloc[0])
            last = float(hist["Close"].iloc[-1])
            if first == 0:
                return None
            return round((last / first - 1) * 100, 2)
        except Exception as exc:
            log.warning("Failed to get history for %s: %s", symbol, exc)
            return None

    t_ret = _return_since(ticker, since_date)
    b_ret = _return_since(benchmark, since_date)
    relative = round(t_ret - b_ret, 2) if t_ret is not None and b_ret is not None else None

    return {
        "ticker": ticker,
        "benchmark": benchmark,
        "since": since_date,
        "ticker_return": t_ret,
        "benchmark_return": b_ret,
        "relative_return": relative,
    }


# ---------------------------------------------------------------------------
# Wrapper / allowance tracking
# ---------------------------------------------------------------------------

def get_wrapper_summary(db_path: str = DB_PATH) -> dict:
    """Return ISA/SIPP allowance usage for the current tax year."""
    _ensure_tables(db_path)
    conn = get_conn(db_path)
    tax_year = _current_tax_year()

    summary: dict = {}
    for wrapper, limit in WRAPPER_ALLOWANCES.items():
        row = conn.execute(
            "SELECT used FROM wrapper_allowances WHERE tax_year = ? AND wrapper = ?",
            (tax_year, wrapper),
        ).fetchone()
        used = row["used"] if row else 0.0

        entry: dict = {"used": used, "tax_year": tax_year}
        if limit is not None:
            entry["limit"] = limit
            entry["remaining"] = limit - used
        else:
            entry["limit"] = None
            entry["remaining"] = None
        summary[wrapper] = entry

    return summary


def update_wrapper_allowance(
    db_path: str = DB_PATH,
    tax_year: str = "",
    wrapper: str = "",
    used_amount: float = 0.0,
) -> None:
    """Set the cumulative used amount for a wrapper in a given tax year."""
    _ensure_tables(db_path)
    wrapper = wrapper.upper()
    if wrapper not in VALID_WRAPPERS:
        raise ValueError(f"Invalid wrapper '{wrapper}'. Must be one of {VALID_WRAPPERS}")

    conn = get_conn(db_path)
    limit_val = WRAPPER_ALLOWANCES.get(wrapper) or 0
    conn.execute(
        """INSERT INTO wrapper_allowances (id, tax_year, wrapper, annual_limit, used)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(tax_year, wrapper) DO UPDATE
           SET used = excluded.used""",
        (_make_id(), tax_year, wrapper, limit_val, used_amount),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Text summaries
# ---------------------------------------------------------------------------

def get_holdings_text(db_path: str = DB_PATH) -> str:
    """Plain text summary of all open holdings for advisor prompt context.

    Returns a compact multi-line string listing each position with current
    value, cost basis, and unrealized P&L.
    """
    _ensure_tables(db_path)
    holdings = get_holdings(db_path, status="open")
    if not holdings:
        return "No advisory holdings currently held."

    tickers = list({h["ticker"] for h in holdings})
    prices = fetch_live_prices(tickers, db_path)

    lines = ["Current advisory portfolio:"]
    total_value = 0.0
    total_cost = 0.0

    by_wrapper: dict[str, list] = {}
    for h in holdings:
        by_wrapper.setdefault(h["wrapper"], []).append(h)

    for wrapper in sorted(by_wrapper):
        lines.append(f"\n{wrapper}:")
        for h in by_wrapper[wrapper]:
            live = prices.get(h["ticker"])
            cost = h["quantity"] * h["avg_cost"]
            value = h["quantity"] * live if live else cost
            pnl = value - cost
            pnl_pct = (pnl / cost * 100) if cost else 0.0
            total_value += value
            total_cost += cost

            name = h["name"] or h["ticker"]
            price_str = f"@ {live:.2f}" if live else "@ N/A"
            lines.append(
                f"  {name} ({h['ticker']}): {h['quantity']:.2f} units {price_str} "
                f"| cost {_gbp(cost)} | value {_gbp(value)} | "
                f"P&L {_pnl_arrow(pnl)}{_gbp(pnl)} ({_pnl_arrow(pnl_pct)}{pnl_pct:.1f}%)"
            )

    total_pnl = total_value - total_cost
    total_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
    lines.append(
        f"\nTotal: value {_gbp(total_value)} | cost {_gbp(total_cost)} | "
        f"P&L {_pnl_arrow(total_pnl)}{_gbp(total_pnl)} ({_pnl_arrow(total_pct)}{total_pct:.1f}%)"
    )

    # Append recent transactions summary
    txs = get_transactions(db_path, limit=5)
    if txs:
        lines.append("\nRecent transactions:")
        for tx in txs:
            t_str = tx["ticker"] or ""
            q_str = f" x{tx['quantity']:.2f}" if tx["quantity"] else ""
            lines.append(
                f"  {tx['created_at'][:10]} {tx['tx_type'].upper()} "
                f"{t_str}{q_str} {_gbp(tx['amount'])} [{tx['wrapper']}]"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram formatters
# ---------------------------------------------------------------------------

def _pnl_arrow(pnl: float) -> str:
    if pnl > 0:
        return "+"
    return ""


def _gbp(amount: float) -> str:
    """Format a number as GBP with comma separators."""
    if amount < 0:
        return f"-\u00a3{abs(amount):,.2f}"
    return f"\u00a3{amount:,.2f}"


def format_holdings_telegram(db_path: str = DB_PATH) -> str:
    """Format current holdings as a Telegram-friendly text block."""
    snap = calculate_portfolio_snapshot(db_path)
    if snap["total_cost"] == 0:
        return "No advisory holdings recorded."

    lines = [
        "ADVISORY PORTFOLIO",
        f"Total: {_gbp(snap['total_value'])}  |  P&L: {_pnl_arrow(snap['total_pnl'])}{_gbp(snap['total_pnl'])} ({_pnl_arrow(snap['total_pnl_pct'])}{snap['total_pnl_pct']:.1f}%)",
        "",
    ]

    for wrapper, w_data in snap["wrappers"].items():
        lines.append(f"--- {wrapper} ---")
        lines.append(
            f"  Value: {_gbp(w_data['value'])}  P&L: {_pnl_arrow(w_data['pnl'])}{_gbp(w_data['pnl'])} ({_pnl_arrow(w_data['pnl_pct'])}{w_data['pnl_pct']:.1f}%)"
        )
        for h in w_data["holdings"]:
            label = h["name"] or h["ticker"]
            price_str = f"@ {h['live_price']:.2f}" if h["live_price"] else "@ N/A"
            pnl_str = f"{_pnl_arrow(h['pnl'])}{_gbp(h['pnl'])}"
            lines.append(f"  {label}: {h['quantity']:.2f} {price_str}  {pnl_str}")
        lines.append("")

    # Allowances
    allowances = snap.get("allowances", {})
    for w_name, a in allowances.items():
        if a.get("limit") is not None:
            lines.append(f"{w_name} allowance: {_gbp(a['used'])} / {_gbp(a['limit'])} used")

    # Recent transactions
    txs = get_transactions(db_path, limit=5)
    if txs:
        lines.append("")
        lines.append("Recent transactions:")
        for tx in txs:
            t_str = tx["ticker"] or ""
            q_str = f" x{tx['quantity']:.2f}" if tx["quantity"] else ""
            lines.append(
                f"  {tx['created_at'][:10]} {tx['tx_type'].upper()} "
                f"{t_str}{q_str} {_gbp(tx['amount'])} [{tx['wrapper']}]"
            )

    return "\n".join(lines)


def format_performance_telegram(db_path: str = DB_PATH) -> str:
    """Format a performance report showing each holding vs its benchmark."""
    _ensure_tables(db_path)
    holdings = get_holdings(db_path, status="open")
    if not holdings:
        return "No open advisory holdings."

    lines = ["PERFORMANCE REPORT", ""]

    for h in holdings:
        label = h["name"] or h["ticker"]
        benchmark = h.get("benchmark_ticker")
        purchase = h.get("purchase_date")

        if benchmark and purchase:
            perf = calculate_performance_vs_benchmark(db_path, h["ticker"], benchmark, purchase)
            t_ret = perf["ticker_return"]
            b_ret = perf["benchmark_return"]
            rel = perf["relative_return"]
            t_str = f"{_pnl_arrow(t_ret)}{t_ret:.1f}%" if t_ret is not None else "N/A"
            b_str = f"{_pnl_arrow(b_ret)}{b_ret:.1f}%" if b_ret is not None else "N/A"
            rel_str = f"{_pnl_arrow(rel)}{rel:.1f}%" if rel is not None else "N/A"
            lines.append(f"{label}: {t_str}  vs {benchmark}: {b_str}  (alpha: {rel_str})")
        else:
            lines.append(f"{label}: no benchmark configured")

        target = h.get("target_return_pct")
        if target is not None:
            lines.append(f"  Target: {target:.1f}%")

    # Transaction summary
    summary = get_transaction_summary(db_path)
    if summary["deposits"] or summary["dividends"] or summary["realized_pnl"]:
        lines.append("")
        lines.append("Transaction totals:")
        if summary["deposits"]:
            lines.append(f"  Deposits: {_gbp(summary['deposits'])}")
        if summary["withdrawals"]:
            lines.append(f"  Withdrawals: {_gbp(summary['withdrawals'])}")
        if summary["dividends"]:
            lines.append(f"  Dividends: {_gbp(summary['dividends'])}")
        if summary["realized_pnl"]:
            lines.append(f"  Realized P&L: {_pnl_arrow(summary['realized_pnl'])}{_gbp(summary['realized_pnl'])}")

    return "\n".join(lines)
