"""
SQLite trade database — persistent storage for all trades, P&L, and daily snapshots.
Used by both the bot (writes) and the Streamlit dashboard (reads).
"""
import sqlite3
import os
from datetime import datetime, date, timedelta
from typing import Any, Optional
import uuid

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trades.db")


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str = DB_PATH):
    """Create tables if they don't exist."""
    conn = get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            direction TEXT NOT NULL,          -- BUY / SELL
            action TEXT NOT NULL,             -- OPEN / CLOSE
            size REAL NOT NULL,
            price REAL,
            deal_id TEXT,
            deal_ref TEXT,
            pnl REAL,                         -- only set on CLOSE
            commission REAL DEFAULT 0,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            balance REAL NOT NULL,
            equity REAL NOT NULL,
            unrealised_pnl REAL DEFAULT 0,
            realised_pnl_today REAL DEFAULT 0,
            open_positions INTEGER DEFAULT 0,
            drawdown_pct REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            direction TEXT NOT NULL,
            size REAL NOT NULL,
            entry_price REAL,
            entry_time TEXT,
            current_price REAL,
            unrealised_pnl REAL DEFAULT 0,
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS bot_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,           -- STARTUP, SCAN, SIGNAL, ORDER, REJECTION, ERROR, MARKET, POSITION, HEARTBEAT, SHUTDOWN
            icon TEXT NOT NULL DEFAULT '🤖',
            headline TEXT NOT NULL,            -- Short human-readable summary
            detail TEXT,                       -- Longer explanation
            ticker TEXT,
            strategy TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
        CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_snapshots(date);
        CREATE INDEX IF NOT EXISTS idx_events_timestamp ON bot_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_category ON bot_events(category);

        CREATE TABLE IF NOT EXISTS strategy_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS option_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spread_id TEXT UNIQUE NOT NULL,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            trade_type TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            expiry_date TEXT,
            short_deal_id TEXT,
            long_deal_id TEXT,
            short_strike REAL,
            long_strike REAL,
            short_epic TEXT,
            long_epic TEXT,
            spread_width REAL,
            premium_collected REAL,
            max_loss REAL,
            size REAL NOT NULL,
            current_value REAL DEFAULT 0,
            unrealised_pnl REAL DEFAULT 0,
            exit_date TEXT,
            exit_pnl REAL,
            exit_reason TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            action TEXT NOT NULL,
            short_strike REAL,
            long_strike REAL,
            spread_width REAL,
            estimated_premium REAL,
            max_loss REAL,
            size REAL,
            reason TEXT,
            would_have_traded TEXT NOT NULL DEFAULT 'yes'
        );

        CREATE INDEX IF NOT EXISTS idx_option_positions_status ON option_positions(status);
        CREATE INDEX IF NOT EXISTS idx_option_positions_ticker ON option_positions(ticker);
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_timestamp ON shadow_trades(timestamp);

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT,
            detail TEXT,
            result TEXT,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

        CREATE TABLE IF NOT EXISTS research_events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL,
            source_ref TEXT,
            retrieved_at TEXT NOT NULL,
            event_timestamp TEXT,
            symbol TEXT,
            headline TEXT,
            detail TEXT,
            confidence REAL,
            provenance_descriptor TEXT NOT NULL,
            provenance_hash TEXT NOT NULL,
            payload TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_research_events_created_at ON research_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_research_events_retrieved_at ON research_events(retrieved_at);
        CREATE INDEX IF NOT EXISTS idx_research_events_event_type ON research_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_research_events_source ON research_events(source);
        CREATE INDEX IF NOT EXISTS idx_research_events_prov_hash ON research_events(provenance_hash);

        CREATE TABLE IF NOT EXISTS order_actions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            action_type TEXT NOT NULL,       -- open_spread / close_spread
            status TEXT NOT NULL,            -- queued/running/retrying/completed/failed/aborted
            ticker TEXT,
            spread_id TEXT,
            attempt INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 1,
            recoverable INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            request_payload TEXT,
            result_payload TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_order_actions_created_at ON order_actions(created_at);
        CREATE INDEX IF NOT EXISTS idx_order_actions_status ON order_actions(status);
        CREATE INDEX IF NOT EXISTS idx_order_actions_corr ON order_actions(correlation_id);

        CREATE TABLE IF NOT EXISTS control_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action TEXT NOT NULL,            -- kill_switch/risk_throttle/cooldown/recovery/etc
            value TEXT,
            reason TEXT,
            actor TEXT NOT NULL DEFAULT 'operator',
            metadata TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_control_actions_timestamp ON control_actions(timestamp);
        CREATE INDEX IF NOT EXISTS idx_control_actions_action ON control_actions(action);

        CREATE TABLE IF NOT EXISTS option_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discovered_at TEXT NOT NULL,
            index_name TEXT,
            epic TEXT NOT NULL UNIQUE,
            instrument_name TEXT,
            option_type TEXT,
            expiry_type TEXT,
            expiry TEXT,
            strike REAL,
            status TEXT,
            bid REAL,
            offer REAL,
            mid REAL,
            spread REAL,
            min_deal_size REAL,
            margin_factor REAL,
            margin_factor_unit TEXT,
            source TEXT,
            raw_payload TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_option_contracts_index ON option_contracts(index_name);
        CREATE INDEX IF NOT EXISTS idx_option_contracts_expiry_type ON option_contracts(expiry_type);
        CREATE INDEX IF NOT EXISTS idx_option_contracts_discovered_at ON option_contracts(discovered_at);

        CREATE TABLE IF NOT EXISTS calibration_runs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            scope TEXT,
            samples INTEGER DEFAULT 0,
            overall_ratio REAL,
            summary_payload TEXT,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_calibration_runs_created_at ON calibration_runs(created_at);
        CREATE INDEX IF NOT EXISTS idx_calibration_runs_status ON calibration_runs(status);

        CREATE TABLE IF NOT EXISTS calibration_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            index_name TEXT,
            ticker TEXT,
            strike REAL,
            otm_pct REAL,
            expiry_type TEXT,
            dte REAL,
            epic TEXT,
            ig_bid REAL,
            ig_offer REAL,
            ig_mid REAL,
            ig_spread REAL,
            ig_spread_pct REAL,
            bs_price REAL,
            ratio_ig_vs_bs REAL,
            tradeable INTEGER DEFAULT 0,
            rv REAL,
            iv_est REAL,
            underlying REAL,
            raw_payload TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_calibration_points_run_id ON calibration_points(run_id);
        CREATE INDEX IF NOT EXISTS idx_calibration_points_index ON calibration_points(index_name);
        CREATE INDEX IF NOT EXISTS idx_calibration_points_timestamp ON calibration_points(timestamp);

        CREATE TABLE IF NOT EXISTS strategy_parameter_sets (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            strategy_key TEXT NOT NULL,
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            source_run_id TEXT,
            parameters_payload TEXT NOT NULL,
            notes TEXT,
            created_by TEXT NOT NULL DEFAULT 'operator'
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_params_key_version
            ON strategy_parameter_sets(strategy_key, version);
        CREATE INDEX IF NOT EXISTS idx_strategy_params_status
            ON strategy_parameter_sets(status);
        CREATE INDEX IF NOT EXISTS idx_strategy_params_updated_at
            ON strategy_parameter_sets(updated_at);

        CREATE TABLE IF NOT EXISTS strategy_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy_key TEXT NOT NULL,
            set_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            actor TEXT NOT NULL,
            acknowledgement TEXT NOT NULL,
            note TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_strategy_promotions_timestamp
            ON strategy_promotions(timestamp);
        CREATE INDEX IF NOT EXISTS idx_strategy_promotions_strategy
            ON strategy_promotions(strategy_key);

        -- ─── A-005: Multi-broker ledger tables (Claude schema — canonical) ────

        CREATE TABLE IF NOT EXISTS broker_accounts (
            id TEXT PRIMARY KEY,
            broker TEXT NOT NULL,                -- ig, ibkr, cityindex, paper
            account_id TEXT NOT NULL,
            account_type TEXT NOT NULL,           -- ISA, SIPP, GIA, SPREADBET, PAPER
            currency TEXT NOT NULL DEFAULT 'GBP',
            label TEXT,                           -- human-readable label
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(broker, account_id)
        );

        CREATE INDEX IF NOT EXISTS idx_broker_accounts_broker
            ON broker_accounts(broker);
        CREATE INDEX IF NOT EXISTS idx_broker_accounts_type
            ON broker_accounts(account_type);

        CREATE TABLE IF NOT EXISTS broker_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_account_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,              -- long / short
            quantity REAL NOT NULL,
            avg_cost REAL NOT NULL DEFAULT 0,
            market_value REAL DEFAULT 0,
            unrealised_pnl REAL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            strategy TEXT,                        -- strategy attribution
            sleeve TEXT,                          -- sleeve attribution
            con_id TEXT,                          -- broker-specific contract/instrument ID
            last_synced_at TEXT NOT NULL,
            UNIQUE(broker_account_id, ticker, direction),
            FOREIGN KEY (broker_account_id) REFERENCES broker_accounts(id)
        );

        CREATE INDEX IF NOT EXISTS idx_broker_positions_account
            ON broker_positions(broker_account_id);
        CREATE INDEX IF NOT EXISTS idx_broker_positions_ticker
            ON broker_positions(ticker);
        CREATE INDEX IF NOT EXISTS idx_broker_positions_strategy
            ON broker_positions(strategy);
        CREATE INDEX IF NOT EXISTS idx_broker_positions_sleeve
            ON broker_positions(sleeve);

        CREATE TABLE IF NOT EXISTS broker_cash_balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker_account_id TEXT NOT NULL,
            balance REAL NOT NULL,
            buying_power REAL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'GBP',
            synced_at TEXT NOT NULL,
            FOREIGN KEY (broker_account_id) REFERENCES broker_accounts(id)
        );

        CREATE INDEX IF NOT EXISTS idx_broker_cash_account
            ON broker_cash_balances(broker_account_id);
        CREATE INDEX IF NOT EXISTS idx_broker_cash_synced
            ON broker_cash_balances(synced_at);

        CREATE TABLE IF NOT EXISTS nav_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            level TEXT NOT NULL,                  -- fund / sleeve / account
            level_id TEXT NOT NULL,               -- e.g. 'fund', 'sleeve_1', account id
            net_liquidation REAL NOT NULL,
            cash REAL NOT NULL DEFAULT 0,
            positions_value REAL NOT NULL DEFAULT 0,
            unrealised_pnl REAL DEFAULT 0,
            realised_pnl REAL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'GBP',
            broker TEXT,                          -- null for fund-level
            account_type TEXT,                    -- null for fund-level
            created_at TEXT NOT NULL,
            UNIQUE(snapshot_date, level, level_id)
        );

        CREATE INDEX IF NOT EXISTS idx_nav_snapshots_date
            ON nav_snapshots(snapshot_date);
        CREATE INDEX IF NOT EXISTS idx_nav_snapshots_level
            ON nav_snapshots(level, level_id);

        CREATE TABLE IF NOT EXISTS reconciliation_reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            broker_account_id TEXT NOT NULL,
            status TEXT NOT NULL,                 -- clean / mismatch / error
            positions_checked INTEGER DEFAULT 0,
            mismatches_found INTEGER DEFAULT 0,
            details TEXT,                         -- JSON array of mismatch details
            FOREIGN KEY (broker_account_id) REFERENCES broker_accounts(id)
        );

        CREATE INDEX IF NOT EXISTS idx_recon_reports_created
            ON reconciliation_reports(created_at);
        CREATE INDEX IF NOT EXISTS idx_recon_reports_status
            ON reconciliation_reports(status);

        -- ─── A-006: Pre-trade risk gate verdicts ────────────────────────────

        CREATE TABLE IF NOT EXISTS risk_verdicts (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            quantity REAL NOT NULL,
            strategy TEXT,
            sleeve TEXT,
            broker TEXT,
            approved INTEGER NOT NULL,              -- 1 = approved, 0 = rejected
            rule_id TEXT,                           -- ID of first failing rule
            reason TEXT NOT NULL,                   -- Human-readable reason or 'OK'
            checks_run INTEGER NOT NULL DEFAULT 0,
            details TEXT                            -- JSON array of all check results
        );

        CREATE INDEX IF NOT EXISTS idx_risk_verdicts_created
            ON risk_verdicts(created_at);
        CREATE INDEX IF NOT EXISTS idx_risk_verdicts_approved
            ON risk_verdicts(approved);
        CREATE INDEX IF NOT EXISTS idx_risk_verdicts_ticker
            ON risk_verdicts(ticker);
        CREATE INDEX IF NOT EXISTS idx_risk_verdicts_rule_id
            ON risk_verdicts(rule_id);

        -- ─── B-003: Fund daily reports ────────────────────────────────────────

        CREATE TABLE IF NOT EXISTS fund_daily_report (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            total_nav REAL NOT NULL,
            total_cash REAL NOT NULL DEFAULT 0,
            total_positions_value REAL NOT NULL DEFAULT 0,
            unrealised_pnl REAL NOT NULL DEFAULT 0,
            realised_pnl REAL NOT NULL DEFAULT 0,
            daily_return_pct REAL,
            drawdown_pct REAL NOT NULL DEFAULT 0,
            high_water_mark REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'GBP',
            created_at TEXT NOT NULL,
            UNIQUE(report_date)
        );

        CREATE INDEX IF NOT EXISTS idx_fund_daily_report_date
            ON fund_daily_report(report_date);

        -- ─── B-003: Sleeve daily reports ──────────────────────────────────────

        CREATE TABLE IF NOT EXISTS sleeve_daily_report (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            sleeve TEXT NOT NULL,
            nav REAL NOT NULL,
            positions_value REAL NOT NULL DEFAULT 0,
            cash_allocated REAL NOT NULL DEFAULT 0,
            unrealised_pnl REAL NOT NULL DEFAULT 0,
            realised_pnl REAL NOT NULL DEFAULT 0,
            weight_pct REAL NOT NULL DEFAULT 0,
            daily_return_pct REAL,
            created_at TEXT NOT NULL,
            UNIQUE(report_date, sleeve)
        );

        CREATE INDEX IF NOT EXISTS idx_sleeve_daily_report_date
            ON sleeve_daily_report(report_date);
        CREATE INDEX IF NOT EXISTS idx_sleeve_daily_report_sleeve
            ON sleeve_daily_report(sleeve);

        -- ─── B-003: Risk daily snapshots ──────────────────────────────────────

        CREATE TABLE IF NOT EXISTS risk_daily_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_heat_pct REAL NOT NULL DEFAULT 0,
            total_margin_pct REAL NOT NULL DEFAULT 0,
            max_position_pct REAL NOT NULL DEFAULT 0,
            open_position_count INTEGER NOT NULL DEFAULT 0,
            open_spread_count INTEGER NOT NULL DEFAULT 0,
            leverage_ratio REAL NOT NULL DEFAULT 0,
            var_95_pct REAL,
            created_at TEXT NOT NULL,
            UNIQUE(snapshot_date)
        );

        CREATE INDEX IF NOT EXISTS idx_risk_daily_snapshot_date
            ON risk_daily_snapshot(snapshot_date);
    """)
    conn.commit()
    conn.close()


# ─── Bot activity log ──────────────────────────────────────────────────────

def log_event(
    category: str,
    headline: str,
    detail: Optional[str] = None,
    ticker: Optional[str] = None,
    strategy: Optional[str] = None,
    icon: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """
    Log a human-readable bot event for the dashboard activity feed.

    Categories & default icons:
        STARTUP  🚀   Bot started / connected
        SCAN     🔍   Signal scan started/completed
        SIGNAL   📊   Signal detected (entry/exit condition met)
        ORDER    ✅   Order placed and filled
        REJECTION ❌  Order rejected by broker
        ERROR    ⚠️   Something went wrong
        MARKET   🏛️   Market status info (closed, opened, etc.)
        POSITION 📋   Position monitoring event
        HEARTBEAT 💓  Periodic alive check
        SHUTDOWN 🛑   Bot stopping
        SNAPSHOT 📸   Daily snapshot saved
    """
    icon_map = {
        "STARTUP": "🚀", "SCAN": "🔍", "SIGNAL": "📊", "ORDER": "✅",
        "REJECTION": "❌", "ERROR": "⚠️", "MARKET": "🏛️", "POSITION": "📋",
        "HEARTBEAT": "💓", "SHUTDOWN": "🛑", "SNAPSHOT": "📸",
    }
    if icon is None:
        icon = icon_map.get(category, "🤖")

    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO bot_events (timestamp, category, icon, headline, detail, ticker, strategy)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), category, icon, headline, detail, ticker, strategy),
    )
    conn.commit()
    conn.close()


def get_bot_events(limit: int = 100, category: Optional[str] = None,
                   db_path: str = DB_PATH) -> list[dict]:
    """Get recent bot events for the dashboard activity feed."""
    conn = get_conn(db_path)
    if category:
        rows = conn.execute(
            "SELECT * FROM bot_events WHERE category=? ORDER BY timestamp DESC LIMIT ?",
            (category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bot_events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Strategy state persistence (trailing stops, cooldowns, etc.) ──────────

def save_strategy_state(key: str, value: str, db_path: str = DB_PATH):
    """Save a strategy state value (e.g. trailing stop levels) to survive restarts."""
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO strategy_state (key, value, updated) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated""",
        (key, value, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def load_strategy_state(key: str, db_path: str = DB_PATH) -> Optional[str]:
    """Load a strategy state value. Returns None if not found."""
    conn = get_conn(db_path)
    row = conn.execute("SELECT value FROM strategy_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def delete_strategy_state(key: str, db_path: str = DB_PATH):
    """Delete a strategy state value (e.g. when a position is closed)."""
    conn = get_conn(db_path)
    conn.execute("DELETE FROM strategy_state WHERE key=?", (key,))
    conn.commit()
    conn.close()


# ─── Trade logging ──────────────────────────────────────────────────────────

def log_trade(
    ticker: str,
    strategy: str,
    direction: str,
    action: str,
    size: float,
    price: Optional[float] = None,
    deal_id: Optional[str] = None,
    deal_ref: Optional[str] = None,
    pnl: Optional[float] = None,
    notes: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Log a trade to the database."""
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, deal_id, deal_ref, pnl, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), ticker, strategy, direction, action, size, price, deal_id, deal_ref, pnl, notes),
    )
    conn.commit()
    conn.close()


# ─── Position tracking ─────────────────────────────────────────────────────

def upsert_position(deal_id: str, ticker: str, strategy: str, direction: str,
                    size: float, entry_price: float, entry_time: str,
                    db_path: str = DB_PATH):
    """Insert or update an open position."""
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO positions (deal_id, ticker, strategy, direction, size, entry_price, entry_time, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(deal_id) DO UPDATE SET
             current_price=excluded.current_price, unrealised_pnl=excluded.unrealised_pnl, last_updated=excluded.last_updated""",
        (deal_id, ticker, strategy, direction, size, entry_price, entry_time, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def remove_position(deal_id: str, db_path: str = DB_PATH):
    """Remove a closed position."""
    conn = get_conn(db_path)
    conn.execute("DELETE FROM positions WHERE deal_id = ?", (deal_id,))
    conn.commit()
    conn.close()


def update_position_price(deal_id: str, current_price: float, unrealised_pnl: float,
                          db_path: str = DB_PATH):
    """Update current price and unrealised P&L for an open position."""
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE positions SET current_price=?, unrealised_pnl=?, last_updated=? WHERE deal_id=?""",
        (current_price, unrealised_pnl, datetime.now().isoformat(), deal_id),
    )
    conn.commit()
    conn.close()


# ─── Daily snapshots ───────────────────────────────────────────────────────

def save_daily_snapshot(balance: float, equity: float, unrealised_pnl: float = 0,
                        realised_pnl_today: float = 0, open_positions: int = 0,
                        db_path: str = DB_PATH):
    """Save end-of-day snapshot. Replaces if exists for today."""
    today = date.today().isoformat()
    conn = get_conn(db_path)

    # Calculate drawdown from peak
    peak_row = conn.execute("SELECT MAX(equity) as peak FROM daily_snapshots").fetchone()
    peak = peak_row["peak"] if peak_row and peak_row["peak"] else equity
    peak = max(peak, equity)
    dd_pct = ((equity - peak) / peak * 100) if peak > 0 else 0

    conn.execute(
        """INSERT INTO daily_snapshots (date, balance, equity, unrealised_pnl, realised_pnl_today, open_positions, drawdown_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
             balance=excluded.balance, equity=excluded.equity, unrealised_pnl=excluded.unrealised_pnl,
             realised_pnl_today=excluded.realised_pnl_today, open_positions=excluded.open_positions,
             drawdown_pct=excluded.drawdown_pct""",
        (today, balance, equity, unrealised_pnl, realised_pnl_today, open_positions, dd_pct),
    )
    conn.commit()
    conn.close()


# ─── Option position tracking ─────────────────────────────────────────────

def upsert_option_position(
    spread_id: str, ticker: str, strategy: str, trade_type: str,
    short_deal_id: str, long_deal_id: str,
    short_strike: float, long_strike: float,
    short_epic: str, long_epic: str,
    spread_width: float, premium_collected: float, max_loss: float,
    size: float, expiry_date: str = "",
    db_path: str = DB_PATH,
):
    """Insert or update an open option spread position."""
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO option_positions
           (spread_id, ticker, strategy, trade_type, entry_date, expiry_date,
            short_deal_id, long_deal_id, short_strike, long_strike,
            short_epic, long_epic, spread_width, premium_collected, max_loss,
            size, status, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
           ON CONFLICT(spread_id) DO UPDATE SET
             current_value=excluded.current_value, unrealised_pnl=excluded.unrealised_pnl,
             last_updated=excluded.last_updated""",
        (spread_id, ticker, strategy, trade_type,
         datetime.now().isoformat(), expiry_date,
         short_deal_id, long_deal_id, short_strike, long_strike,
         short_epic, long_epic, spread_width, premium_collected, max_loss,
         size, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def close_option_position(spread_id: str, exit_pnl: float, exit_reason: str,
                           db_path: str = DB_PATH):
    """Mark an option spread as closed with final P&L."""
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE option_positions SET status='closed', exit_date=?, exit_pnl=?, exit_reason=?,
           last_updated=? WHERE spread_id=?""",
        (datetime.now().isoformat(), exit_pnl, exit_reason,
         datetime.now().isoformat(), spread_id),
    )
    conn.commit()
    conn.close()


def get_open_option_positions(db_path: str = DB_PATH) -> list[dict]:
    """Get all open option spread positions."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM option_positions WHERE status='open' ORDER BY entry_date DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_closed_option_positions(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """Get closed option positions."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM option_positions WHERE status='closed' ORDER BY exit_date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_option_positions(db_path: str = DB_PATH) -> list[dict]:
    """Get all option positions (open + closed)."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM option_positions ORDER BY entry_date DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Shadow trade logging ────────────────────────────────────────────────

def log_shadow_trade(
    ticker: str, strategy: str, action: str,
    short_strike: float = 0, long_strike: float = 0,
    spread_width: float = 0, estimated_premium: float = 0,
    max_loss: float = 0, size: float = 0,
    reason: str = "", db_path: str = DB_PATH,
):
    """Log what the bot WOULD have traded in shadow mode."""
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO shadow_trades
           (timestamp, ticker, strategy, action, short_strike, long_strike,
            spread_width, estimated_premium, max_loss, size, reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), ticker, strategy, action,
         short_strike, long_strike, spread_width, estimated_premium,
         max_loss, size, reason),
    )
    conn.commit()
    conn.close()


def get_shadow_trades(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """Get recent shadow trades."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM shadow_trades ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Research persistence (options discovery + calibration) ───────────────

def upsert_option_contracts(contracts: list[dict], db_path: str = DB_PATH) -> int:
    """Upsert discovered option contracts by EPIC."""
    if not contracts:
        return 0

    now = datetime.now().isoformat()
    conn = get_conn(db_path)
    count = 0
    for c in contracts:
        conn.execute(
            """INSERT INTO option_contracts
               (discovered_at, index_name, epic, instrument_name, option_type, expiry_type, expiry,
                strike, status, bid, offer, mid, spread, min_deal_size, margin_factor,
                margin_factor_unit, source, raw_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(epic) DO UPDATE SET
                 discovered_at=excluded.discovered_at,
                 index_name=excluded.index_name,
                 instrument_name=excluded.instrument_name,
                 option_type=excluded.option_type,
                 expiry_type=excluded.expiry_type,
                 expiry=excluded.expiry,
                 strike=excluded.strike,
                 status=excluded.status,
                 bid=excluded.bid,
                 offer=excluded.offer,
                 mid=excluded.mid,
                 spread=excluded.spread,
                 min_deal_size=excluded.min_deal_size,
                 margin_factor=excluded.margin_factor,
                 margin_factor_unit=excluded.margin_factor_unit,
                 source=excluded.source,
                 raw_payload=excluded.raw_payload""",
            (
                now,
                c.get("index_name"),
                c.get("epic"),
                c.get("instrument_name"),
                c.get("option_type"),
                c.get("expiry_type"),
                c.get("expiry"),
                c.get("strike"),
                c.get("status"),
                c.get("bid"),
                c.get("offer"),
                c.get("mid"),
                c.get("spread"),
                c.get("min_deal_size"),
                c.get("margin_factor"),
                c.get("margin_factor_unit"),
                c.get("source"),
                c.get("raw_payload"),
            ),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_option_contracts(
    limit: int = 200,
    index_name: Optional[str] = None,
    expiry_type: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get discovered option contracts with optional filters."""
    conn = get_conn(db_path)
    query = "SELECT * FROM option_contracts"
    where = []
    params: list = []
    if index_name:
        where.append("index_name LIKE ?")
        params.append(f"%{index_name}%")
    if expiry_type:
        where.append("expiry_type=?")
        params.append(expiry_type)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY discovered_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_option_contract_summary(db_path: str = DB_PATH) -> list[dict]:
    """Get option contract counts grouped by index and expiry type."""
    conn = get_conn(db_path)
    rows = conn.execute(
        """SELECT
               index_name,
               expiry_type,
               COUNT(*) AS contracts,
               MAX(discovered_at) AS last_seen
           FROM option_contracts
           GROUP BY index_name, expiry_type
           ORDER BY index_name ASC, expiry_type ASC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_calibration_run(
    run_id: str,
    scope: str,
    status: str = "running",
    db_path: str = DB_PATH,
):
    """Create a calibration run record."""
    now = datetime.now().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO calibration_runs
           (id, created_at, updated_at, status, scope)
           VALUES (?, ?, ?, ?, ?)""",
        (run_id, now, now, status, scope),
    )
    conn.commit()
    conn.close()


def complete_calibration_run(
    run_id: str,
    status: str,
    samples: int = 0,
    overall_ratio: Optional[float] = None,
    summary_payload: Optional[str] = None,
    error: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Finalize a calibration run with summary/error."""
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE calibration_runs
           SET updated_at=?, status=?, samples=?, overall_ratio=?, summary_payload=?, error=?
           WHERE id=?""",
        (datetime.now().isoformat(), status, samples, overall_ratio, summary_payload, error, run_id),
    )
    conn.commit()
    conn.close()


def insert_calibration_points(run_id: str, points: list[dict], db_path: str = DB_PATH) -> int:
    """Persist quote-level calibration points for a run."""
    if not points:
        return 0
    now = datetime.now().isoformat()
    conn = get_conn(db_path)
    count = 0
    for p in points:
        conn.execute(
            """INSERT INTO calibration_points
               (run_id, timestamp, index_name, ticker, strike, otm_pct, expiry_type, dte, epic,
                ig_bid, ig_offer, ig_mid, ig_spread, ig_spread_pct, bs_price, ratio_ig_vs_bs,
                tradeable, rv, iv_est, underlying, raw_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                now,
                p.get("index"),
                p.get("ticker"),
                p.get("strike"),
                p.get("otm_pct"),
                p.get("expiry_type"),
                p.get("dte"),
                p.get("epic"),
                p.get("ig_bid"),
                p.get("ig_offer"),
                p.get("ig_mid"),
                p.get("ig_spread"),
                p.get("ig_spread_pct"),
                p.get("bs_price"),
                p.get("ratio_ig_vs_bs"),
                int(bool(p.get("tradeable", False))),
                p.get("rv"),
                p.get("iv_est"),
                p.get("underlying"),
                str(p),
            ),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_calibration_runs(limit: int = 20, db_path: str = DB_PATH) -> list[dict]:
    """Get recent calibration runs."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM calibration_runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_calibration_points(
    run_id: str,
    limit: int = 200,
    index_name: Optional[str] = None,
    ticker: Optional[str] = None,
    expiry_type: Optional[str] = None,
    strike_min: Optional[float] = None,
    strike_max: Optional[float] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent calibration points for a run with optional filters."""
    conn = get_conn(db_path)
    query = "SELECT * FROM calibration_points WHERE run_id=?"
    params: list = [run_id]
    if index_name:
        query += " AND index_name LIKE ?"
        params.append(f"%{index_name}%")
    if ticker:
        query += " AND ticker LIKE ?"
        params.append(f"%{ticker}%")
    if expiry_type:
        query += " AND expiry_type=?"
        params.append(expiry_type)
    if strike_min is not None:
        query += " AND strike>=?"
        params.append(float(strike_min))
    if strike_max is not None:
        query += " AND strike<=?"
        params.append(float(strike_max))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_calibration_run(run_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Get a single calibration run by ID."""
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM calibration_runs WHERE id=?",
        (run_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ─── Strategy parameter versioning + promotions ───────────────────────────

VALID_PARAM_SET_STATUSES = {"shadow", "staged_live", "live", "archived"}


def create_strategy_parameter_set(
    strategy_key: str,
    name: str,
    parameters_payload: str,
    status: str = "shadow",
    source_run_id: Optional[str] = None,
    notes: Optional[str] = None,
    created_by: str = "operator",
    db_path: str = DB_PATH,
) -> dict:
    """Create a new versioned strategy parameter set."""
    clean_strategy = strategy_key.strip().lower()
    clean_name = name.strip()
    clean_status = status.strip().lower() or "shadow"
    if clean_status not in VALID_PARAM_SET_STATUSES:
        raise ValueError(f"Invalid status '{status}'")
    if not clean_strategy:
        raise ValueError("strategy_key is required")
    if not clean_name:
        raise ValueError("name is required")

    now = datetime.now().isoformat()
    set_id = str(uuid.uuid4())
    conn = get_conn(db_path)
    version_row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS current_version FROM strategy_parameter_sets WHERE strategy_key=?",
        (clean_strategy,),
    ).fetchone()
    next_version = int(version_row["current_version"] or 0) + 1

    conn.execute(
        """INSERT INTO strategy_parameter_sets
           (id, created_at, updated_at, strategy_key, name, version, status, source_run_id,
            parameters_payload, notes, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            set_id,
            now,
            now,
            clean_strategy,
            clean_name,
            next_version,
            clean_status,
            source_run_id,
            parameters_payload,
            notes,
            created_by,
        ),
    )
    row = conn.execute(
        "SELECT * FROM strategy_parameter_sets WHERE id=?",
        (set_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def get_strategy_parameter_set(set_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Get one parameter set by ID."""
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM strategy_parameter_sets WHERE id=?",
        (set_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_strategy_parameter_sets(
    limit: int = 50,
    strategy_key: Optional[str] = None,
    status: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent strategy parameter sets with optional filters."""
    conn = get_conn(db_path)
    query = "SELECT * FROM strategy_parameter_sets"
    where: list[str] = []
    params: list = []
    if strategy_key:
        where.append("strategy_key=?")
        params.append(strategy_key.strip().lower())
    if status:
        where.append("status=?")
        params.append(status.strip().lower())
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY created_at DESC, version DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_strategy_parameter_set(
    strategy_key: str,
    status: str = "live",
    db_path: str = DB_PATH,
) -> Optional[dict]:
    """Get the newest active parameter set for one strategy and status."""
    conn = get_conn(db_path)
    row = conn.execute(
        """SELECT * FROM strategy_parameter_sets
           WHERE strategy_key=? AND status=?
           ORDER BY version DESC, updated_at DESC
           LIMIT 1""",
        (strategy_key.strip().lower(), status.strip().lower()),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def promote_strategy_parameter_set(
    set_id: str,
    to_status: str,
    actor: str,
    acknowledgement: str,
    note: Optional[str] = None,
    db_path: str = DB_PATH,
) -> dict:
    """Promote/demote a parameter set and append a promotion audit record."""
    target = to_status.strip().lower()
    clean_actor = actor.strip() or "operator"
    ack = acknowledgement.strip()
    if target not in VALID_PARAM_SET_STATUSES:
        return {"ok": False, "message": f"Invalid target status '{to_status}'."}
    if not ack:
        return {"ok": False, "message": "Acknowledgement is required for promotion actions."}

    conn = get_conn(db_path)
    current = conn.execute(
        "SELECT * FROM strategy_parameter_sets WHERE id=?",
        (set_id,),
    ).fetchone()
    if not current:
        conn.close()
        return {"ok": False, "message": f"Parameter set '{set_id}' not found."}

    current_item = dict(current)
    from_status = current_item.get("status")
    strategy_key = current_item.get("strategy_key")
    now = datetime.now().isoformat()

    if target in {"staged_live", "live"}:
        conn.execute(
            """UPDATE strategy_parameter_sets
               SET status='archived', updated_at=?
               WHERE strategy_key=? AND status=? AND id<>?""",
            (now, strategy_key, target, set_id),
        )

    conn.execute(
        "UPDATE strategy_parameter_sets SET status=?, updated_at=? WHERE id=?",
        (target, now, set_id),
    )
    conn.execute(
        """INSERT INTO strategy_promotions
           (timestamp, strategy_key, set_id, from_status, to_status, actor, acknowledgement, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, strategy_key, set_id, from_status, target, clean_actor, ack, note),
    )
    updated = conn.execute(
        "SELECT * FROM strategy_parameter_sets WHERE id=?",
        (set_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "message": f"Set {set_id[:8]} promoted from {from_status} to {target}.",
        "item": dict(updated),
        "from_status": from_status,
        "to_status": target,
    }


def get_strategy_promotions(
    limit: int = 50,
    strategy_key: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent parameter promotion actions."""
    conn = get_conn(db_path)
    if strategy_key:
        rows = conn.execute(
            """SELECT * FROM strategy_promotions
               WHERE strategy_key=?
               ORDER BY timestamp DESC LIMIT ?""",
            (strategy_key.strip().lower(), limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM strategy_promotions ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Job tracking (control-plane actions) ────────────────────────────────

def create_job(job_id: str, job_type: str, status: str = "queued",
               mode: Optional[str] = None, detail: Optional[str] = None,
               db_path: str = DB_PATH):
    """Create a new job record."""
    now = datetime.now().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO jobs (id, created_at, updated_at, job_type, status, mode, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (job_id, now, now, job_type, status, mode, detail),
    )
    conn.commit()
    conn.close()


def update_job(job_id: str, status: str, detail: Optional[str] = None,
               result: Optional[str] = None, error: Optional[str] = None,
               db_path: str = DB_PATH):
    """Update an existing job record."""
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE jobs
           SET updated_at=?, status=?, detail=COALESCE(?, detail),
               result=COALESCE(?, result), error=COALESCE(?, error)
           WHERE id=?""",
        (datetime.now().isoformat(), status, detail, result, error, job_id),
    )
    conn.commit()
    conn.close()


def get_jobs(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """Get recent jobs."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_job(job_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Get one job by ID."""
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ─── Research event store (Phase C) ──────────────────────────────────────

def upsert_research_event(
    event_id: str,
    event_type: str,
    source: str,
    retrieved_at: str,
    provenance_descriptor: str,
    provenance_hash: str,
    source_ref: Optional[str] = None,
    event_timestamp: Optional[str] = None,
    symbol: Optional[str] = None,
    headline: Optional[str] = None,
    detail: Optional[str] = None,
    confidence: Optional[float] = None,
    payload: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Insert or update one normalized research event row."""
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO research_events
           (id, created_at, updated_at, event_type, source, source_ref, retrieved_at,
            event_timestamp, symbol, headline, detail, confidence,
            provenance_descriptor, provenance_hash, payload)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             updated_at=excluded.updated_at,
             event_type=excluded.event_type,
             source=excluded.source,
             source_ref=excluded.source_ref,
             retrieved_at=excluded.retrieved_at,
             event_timestamp=excluded.event_timestamp,
             symbol=excluded.symbol,
             headline=excluded.headline,
             detail=excluded.detail,
             confidence=excluded.confidence,
             provenance_descriptor=excluded.provenance_descriptor,
             provenance_hash=excluded.provenance_hash,
             payload=excluded.payload""",
        (
            event_id,
            now,
            now,
            event_type,
            source,
            source_ref,
            retrieved_at,
            event_timestamp,
            symbol,
            headline,
            detail,
            confidence,
            provenance_descriptor,
            provenance_hash,
            payload,
        ),
    )
    conn.commit()
    conn.close()


def get_research_events(
    limit: int = 100,
    event_type: Optional[str] = None,
    source: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent research events with optional source/event_type filters."""
    conn = get_conn(db_path)
    query = "SELECT * FROM research_events"
    where: list[str] = []
    params: list[Any] = []
    if event_type:
        where.append("event_type=?")
        params.append(event_type)
    if source:
        where.append("source=?")
        params.append(source)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY retrieved_at DESC, created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Order action state machine (execution reliability) ──────────────────

def create_order_action(
    action_id: str,
    correlation_id: str,
    action_type: str,
    ticker: Optional[str] = None,
    spread_id: Optional[str] = None,
    max_attempts: int = 1,
    request_payload: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Create an order action record."""
    now = datetime.now().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO order_actions
           (id, created_at, updated_at, correlation_id, action_type, status, ticker, spread_id,
            attempt, max_attempts, request_payload)
           VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, 0, ?, ?)""",
        (action_id, now, now, correlation_id, action_type, ticker, spread_id, max_attempts, request_payload),
    )
    conn.commit()
    conn.close()


def update_order_action(
    action_id: str,
    status: str,
    attempt: Optional[int] = None,
    recoverable: Optional[bool] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    result_payload: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Transition an order action to a new state."""
    conn = get_conn(db_path)
    conn.execute(
        """UPDATE order_actions
           SET updated_at=?,
               status=?,
               attempt=COALESCE(?, attempt),
               recoverable=COALESCE(?, recoverable),
               error_code=COALESCE(?, error_code),
               error_message=COALESCE(?, error_message),
               result_payload=COALESCE(?, result_payload)
           WHERE id=?""",
        (
            datetime.now().isoformat(),
            status,
            attempt,
            int(recoverable) if recoverable is not None else None,
            error_code,
            error_message,
            result_payload,
            action_id,
        ),
    )
    conn.commit()
    conn.close()


def get_order_actions(limit: int = 100, status: Optional[str] = None,
                      db_path: str = DB_PATH) -> list[dict]:
    """Get recent order actions, optionally filtered by status."""
    conn = get_conn(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM order_actions WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM order_actions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_order_actions_by_statuses(statuses: list[str], limit: int = 200,
                                  db_path: str = DB_PATH) -> list[dict]:
    """Get recent order actions for any status in `statuses`."""
    if not statuses:
        return []
    placeholders = ",".join(["?"] * len(statuses))
    sql = (
        f"SELECT * FROM order_actions WHERE status IN ({placeholders}) "
        "ORDER BY updated_at DESC LIMIT ?"
    )
    conn = get_conn(db_path)
    rows = conn.execute(sql, (*statuses, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_control_action(
    action: str,
    value: Optional[str] = None,
    reason: Optional[str] = None,
    actor: str = "operator",
    metadata: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Persist an operator/system control action for audit trail."""
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO control_actions (timestamp, action, value, reason, actor, metadata)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), action, value, reason, actor, metadata),
    )
    conn.commit()
    conn.close()


def get_control_actions(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """Get recent control actions (operator/system acknowledgements)."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM control_actions ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_incidents(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """
    Return unified operational incidents from bot_events and failed order actions.
    """
    conn = get_conn(db_path)

    events = conn.execute(
        """SELECT
               timestamp,
               category,
               headline AS title,
               detail AS detail,
               ticker,
               strategy,
               'bot_event' AS source,
               NULL AS correlation_id
           FROM bot_events
           WHERE category IN ('ERROR', 'REJECTION')
           ORDER BY timestamp DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    actions = conn.execute(
        """SELECT
               updated_at AS timestamp,
               CASE WHEN recoverable=1 THEN 'RETRYING' ELSE 'FAILED' END AS category,
               action_type || ' failed' AS title,
               COALESCE(error_message, result_payload, 'execution failure') AS detail,
               ticker,
               NULL AS strategy,
               'order_action' AS source,
               correlation_id
           FROM order_actions
           WHERE status IN ('failed', 'retrying')
           ORDER BY updated_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()

    merged = [dict(r) for r in events] + [dict(r) for r in actions]
    merged.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return merged[:limit]


# ─── Query helpers (for dashboard) ─────────────────────────────────────────

def get_all_trades(db_path: str = DB_PATH) -> list[dict]:
    conn = get_conn(db_path)
    rows = conn.execute("SELECT * FROM trades ORDER BY timestamp DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trades_by_strategy(strategy: str, db_path: str = DB_PATH) -> list[dict]:
    conn = get_conn(db_path)
    rows = conn.execute("SELECT * FROM trades WHERE strategy=? ORDER BY timestamp DESC", (strategy,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_closed_trades(db_path: str = DB_PATH) -> list[dict]:
    conn = get_conn(db_path)
    rows = conn.execute("SELECT * FROM trades WHERE action='CLOSE' AND pnl IS NOT NULL ORDER BY timestamp DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_positions(db_path: str = DB_PATH) -> list[dict]:
    conn = get_conn(db_path)
    rows = conn.execute("SELECT * FROM positions ORDER BY entry_time DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_daily_snapshots(db_path: str = DB_PATH) -> list[dict]:
    conn = get_conn(db_path)
    rows = conn.execute("SELECT * FROM daily_snapshots ORDER BY date ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_strategy_stats(db_path: str = DB_PATH) -> dict:
    """Calculate per-strategy performance stats from closed trades."""
    conn = get_conn(db_path)
    strategies = conn.execute("SELECT DISTINCT strategy FROM trades").fetchall()
    stats = {}

    for row in strategies:
        strat = row["strategy"]
        closed = conn.execute(
            "SELECT pnl FROM trades WHERE strategy=? AND action='CLOSE' AND pnl IS NOT NULL", (strat,)
        ).fetchall()

        if not closed:
            stats[strat] = {"trades": 0, "total_pnl": 0, "win_rate": 0, "avg_pnl": 0,
                            "best_trade": 0, "worst_trade": 0, "profit_factor": 0}
            continue

        pnls = [r["pnl"] for r in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.001

        stats[strat] = {
            "trades": len(pnls),
            "total_pnl": round(sum(pnls), 2),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
            "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
            "best_trade": round(max(pnls), 2) if pnls else 0,
            "worst_trade": round(min(pnls), 2) if pnls else 0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        }

    conn.close()
    return stats


def get_summary(db_path: str = DB_PATH) -> dict:
    """Overall portfolio summary."""
    conn = get_conn(db_path)

    total_trades = conn.execute("SELECT COUNT(*) as c FROM trades WHERE action='CLOSE'").fetchone()["c"]
    total_pnl_row = conn.execute("SELECT SUM(pnl) as s FROM trades WHERE action='CLOSE' AND pnl IS NOT NULL").fetchone()
    total_pnl = total_pnl_row["s"] if total_pnl_row["s"] else 0
    open_count = conn.execute("SELECT COUNT(*) as c FROM positions").fetchone()["c"]

    # Today's P&L
    today = date.today().isoformat()
    today_pnl_row = conn.execute(
        "SELECT SUM(pnl) as s FROM trades WHERE action='CLOSE' AND pnl IS NOT NULL AND timestamp LIKE ?",
        (f"{today}%",)
    ).fetchone()
    today_pnl = today_pnl_row["s"] if today_pnl_row["s"] else 0

    # Latest snapshot
    snap = conn.execute("SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT 1").fetchone()

    conn.close()

    return {
        "total_closed_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "today_pnl": round(today_pnl, 2),
        "open_positions": open_count,
        "latest_balance": round(snap["balance"], 2) if snap else 0,
        "latest_equity": round(snap["equity"], 2) if snap else 0,
        "max_drawdown": round(snap["drawdown_pct"], 2) if snap else 0,
    }


def _resolve_broker_account_id(
    conn, broker: str, account_id: str
) -> Optional[str]:
    """Look up the surrogate id for a (broker, account_id) pair."""
    row = conn.execute(
        "SELECT id FROM broker_accounts WHERE broker=? AND account_id=?",
        (broker.strip().lower(), account_id.strip()),
    ).fetchone()
    return row["id"] if row else None


def upsert_broker_account(
    broker: str,
    account_id: str,
    account_type: Optional[str] = None,
    account_label: Optional[str] = None,
    currency: Optional[str] = None,
    status: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Upsert a broker account identity record (Claude schema)."""
    now = datetime.now().isoformat()
    acct_id = f"{broker.strip().lower()}_{account_id.strip()}"
    is_active = 1 if status != "inactive" else 0
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO broker_accounts
           (id, broker, account_id, account_type, currency, label, is_active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(broker, account_id) DO UPDATE SET
             account_type=COALESCE(excluded.account_type, account_type),
             currency=COALESCE(excluded.currency, currency),
             label=COALESCE(excluded.label, label),
             is_active=excluded.is_active,
             updated_at=excluded.updated_at""",
        (
            acct_id,
            broker.strip().lower(),
            account_id.strip(),
            account_type or "GIA",
            currency or "GBP",
            account_label,
            is_active,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def upsert_broker_position(
    broker: str,
    account_id: str,
    position_id: str,
    ticker: str,
    instrument_type: Optional[str] = None,
    direction: Optional[str] = None,
    qty: float = 0,
    avg_price: Optional[float] = None,
    market_price: Optional[float] = None,
    unrealised_pnl: Optional[float] = None,
    as_of: Optional[str] = None,
    raw_payload: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Upsert one broker position row (adapted to Claude schema)."""
    stamp = as_of or datetime.now().isoformat()
    broker_account_id = f"{broker.strip().lower()}_{account_id.strip()}"
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO broker_positions
           (broker_account_id, ticker, direction, quantity, avg_cost,
            market_value, unrealised_pnl, last_synced_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(broker_account_id, ticker, direction) DO UPDATE SET
             quantity=excluded.quantity,
             avg_cost=excluded.avg_cost,
             market_value=excluded.market_value,
             unrealised_pnl=excluded.unrealised_pnl,
             last_synced_at=excluded.last_synced_at""",
        (
            broker_account_id,
            ticker.strip().upper(),
            direction or "long",
            float(qty),
            avg_price,
            market_price,
            unrealised_pnl,
            stamp,
        ),
    )
    conn.commit()
    conn.close()


def replace_broker_positions(
    broker: str,
    account_id: str,
    positions: list[dict],
    as_of: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Replace all positions for one broker/account snapshot (Claude schema)."""
    broker_account_id = f"{broker.strip().lower()}_{account_id.strip()}"
    stamp = as_of or datetime.now().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        "DELETE FROM broker_positions WHERE broker_account_id=?",
        (broker_account_id,),
    )
    count = 0
    for row in positions:
        conn.execute(
            """INSERT INTO broker_positions
               (broker_account_id, ticker, direction, quantity, avg_cost,
                market_value, unrealised_pnl, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                broker_account_id,
                str(row.get("ticker", "")).strip().upper(),
                row.get("direction", "long"),
                float(row.get("qty", 0) or row.get("quantity", 0) or 0),
                float(row.get("avg_price") or row.get("avg_cost") or 0),
                float(row.get("market_price") or row.get("market_value") or 0),
                row.get("unrealised_pnl"),
                row.get("as_of") or row.get("last_synced_at") or stamp,
            ),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def upsert_broker_cash_balance(
    broker: str,
    account_id: str,
    currency: str,
    balance: float,
    equity: Optional[float] = None,
    available: Optional[float] = None,
    margin_used: Optional[float] = None,
    as_of: Optional[str] = None,
    raw_payload: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Upsert broker cash/equity snapshot (Claude schema)."""
    stamp = as_of or datetime.now().isoformat()
    broker_account_id = f"{broker.strip().lower()}_{account_id.strip()}"
    buying_power = available or 0
    conn = get_conn(db_path)
    # Use INSERT OR REPLACE since Claude schema uses autoincrement PK
    conn.execute(
        """INSERT INTO broker_cash_balances
           (broker_account_id, balance, buying_power, currency, synced_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            broker_account_id,
            float(balance),
            float(buying_power),
            currency.strip().upper(),
            stamp,
        ),
    )
    conn.commit()
    conn.close()


def insert_nav_snapshot(
    timestamp: str,
    sleeve: str,
    nav: float,
    cash: float = 0.0,
    gross_exposure: float = 0.0,
    net_exposure: float = 0.0,
    broker: Optional[str] = None,
    account_id: Optional[str] = None,
    source: Optional[str] = None,
    db_path: str = DB_PATH,
):
    """Insert or update one NAV snapshot row (Claude schema)."""
    now = datetime.now().isoformat()
    level_id = sleeve
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO nav_snapshots
           (snapshot_date, level, level_id, net_liquidation, cash, positions_value,
            unrealised_pnl, realised_pnl, currency, broker, account_type, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(snapshot_date, level, level_id) DO UPDATE SET
             net_liquidation=excluded.net_liquidation,
             cash=excluded.cash,
             positions_value=excluded.positions_value,
             created_at=excluded.created_at""",
        (
            timestamp,
            "sleeve",
            level_id,
            float(nav),
            float(cash),
            float(gross_exposure),
            float(net_exposure),
            0.0,
            "GBP",
            broker.strip().lower() if broker else None,
            None,
            now,
        ),
    )
    conn.commit()
    conn.close()


def get_broker_accounts(
    broker: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Return broker accounts, optionally filtered by broker key."""
    conn = get_conn(db_path)
    if broker:
        rows = conn.execute(
            "SELECT * FROM broker_accounts WHERE broker=? ORDER BY account_id",
            (broker.strip().lower(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM broker_accounts ORDER BY broker, account_id"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_broker_positions(
    broker: Optional[str] = None,
    account_id: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Return broker positions with optional broker/account filters (Claude schema — JOINs to get broker info)."""
    conn = get_conn(db_path)
    query = """SELECT bp.*, ba.broker, ba.account_id
               FROM broker_positions bp
               JOIN broker_accounts ba ON bp.broker_account_id = ba.id"""
    where: list[str] = []
    params: list[Any] = []
    if broker:
        where.append("ba.broker=?")
        params.append(broker.strip().lower())
    if account_id:
        where.append("ba.account_id=?")
        params.append(account_id.strip())
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY ba.broker, ba.account_id, bp.ticker"
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_broker_cash_balances(
    broker: Optional[str] = None,
    account_id: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Return broker cash snapshots with optional broker/account filters (Claude schema)."""
    conn = get_conn(db_path)
    query = """SELECT bc.*, ba.broker, ba.account_id
               FROM broker_cash_balances bc
               JOIN broker_accounts ba ON bc.broker_account_id = ba.id"""
    where: list[str] = []
    params: list[Any] = []
    if broker:
        where.append("ba.broker=?")
        params.append(broker.strip().lower())
    if account_id:
        where.append("ba.account_id=?")
        params.append(account_id.strip())
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY ba.broker, ba.account_id, bc.currency"
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_nav_snapshots(
    limit: int = 100,
    sleeve: Optional[str] = None,
    broker: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Return NAV snapshots with optional level/broker filters (Claude schema)."""
    conn = get_conn(db_path)
    query = "SELECT * FROM nav_snapshots"
    where: list[str] = []
    params: list[Any] = []
    if sleeve:
        where.append("level_id=?")
        params.append(sleeve.strip())
    if broker:
        where.append("broker=?")
        params.append(broker.strip().lower())
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY snapshot_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, tuple(params)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_unified_ledger_snapshot(
    nav_limit: int = 50,
    db_path: str = DB_PATH,
) -> dict:
    """Return unified broker accounts, positions, cash balances, and NAV snapshots."""
    accounts = get_broker_accounts(db_path=db_path)
    positions = get_broker_positions(db_path=db_path)
    cash_balances = get_broker_cash_balances(db_path=db_path)
    nav = get_nav_snapshots(limit=nav_limit, db_path=db_path)

    total_cash = sum(float(row.get("balance", 0) or 0) for row in cash_balances)
    # In Claude schema, equity ≈ balance + unrealised PnL; buying_power is available margin
    total_equity = total_cash + sum(float(row.get("unrealised_pnl", 0) or 0) for row in positions)
    total_unrealised = sum(float(row.get("unrealised_pnl", 0) or 0) for row in positions)

    return {
        "accounts": accounts,
        "positions": positions,
        "cash_balances": cash_balances,
        "nav_snapshots": nav,
        "summary": {
            "accounts": len(accounts),
            "positions": len(positions),
            "cash_rows": len(cash_balances),
            "total_cash": round(total_cash, 2),
            "total_equity": round(total_equity, 2),
            "total_unrealised_pnl": round(total_unrealised, 2),
        },
    }


def get_ledger_reconcile_report(
    stale_after_minutes: int = 30,
    db_path: str = DB_PATH,
) -> dict:
    """Build a unified ledger reconciliation report with actionable suggestions."""
    accounts = get_broker_accounts(db_path=db_path)
    open_option_positions = get_open_option_positions(db_path=db_path)

    # Use LEFT JOIN to detect orphan positions (no matching broker_accounts row)
    conn = get_conn(db_path)
    all_positions = [
        dict(r) for r in conn.execute(
            """SELECT bp.*, ba.broker, ba.account_id
               FROM broker_positions bp
               LEFT JOIN broker_accounts ba ON bp.broker_account_id = ba.id
               ORDER BY bp.ticker"""
        ).fetchall()
    ]
    conn.close()

    orphan_positions = [row for row in all_positions if row.get("broker") is None]

    cutoff = datetime.now() - timedelta(minutes=max(1, int(stale_after_minutes)))
    stale_positions = []
    for row in all_positions:
        synced = str(row.get("last_synced_at") or row.get("synced_at") or "")
        try:
            ts = datetime.fromisoformat(synced)
            if ts < cutoff:
                stale_positions.append(row)
        except ValueError:
            stale_positions.append(row)

    ig_position_count = len([row for row in all_positions if row.get("broker") == "ig"])
    ig_option_spread_count = len(open_option_positions)
    ig_count_mismatch = ig_option_spread_count != ig_position_count and (ig_option_spread_count or ig_position_count)

    suggestions: list[str] = []
    if orphan_positions:
        suggestions.append("Broker positions exist without matching broker account metadata. Run account sync.")
    if stale_positions:
        suggestions.append("Broker positions are stale. Trigger broker ledger ingestion and reconcile again.")
    if ig_count_mismatch:
        suggestions.append(
            "IG ledger position count differs from option spread count. Run manual reconcile and inspect stale spreads."
        )
    if not suggestions:
        suggestions.append("Ledger reconciliation is clean.")

    return {
        "ok": not (orphan_positions or stale_positions or ig_count_mismatch),
        "stale_after_minutes": max(1, int(stale_after_minutes)),
        "broker_accounts": len(accounts),
        "broker_positions": len(all_positions),
        "orphan_position_count": len(orphan_positions),
        "stale_position_count": len(stale_positions),
        "ig_option_spread_count": ig_option_spread_count,
        "ig_ledger_position_count": ig_position_count,
        "ig_count_mismatch": bool(ig_count_mismatch),
        "suggestions": suggestions,
    }


# ─── A-006: Risk verdict queries ──────────────────────────────────────────

def get_risk_verdicts(
    limit: int = 50,
    approved: Optional[int] = None,
    ticker: Optional[str] = None,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent risk verdicts with optional filters."""
    import json as _json
    conn = get_conn(db_path)
    sql = "SELECT * FROM risk_verdicts"
    where: list[str] = []
    params: list = []
    if approved is not None:
        where.append("approved=?")
        params.append(int(approved))
    if ticker:
        where.append("ticker=?")
        params.append(ticker)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, tuple(params)).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        if d.get("details"):
            try:
                d["details"] = _json.loads(d["details"])
            except (ValueError, TypeError):
                pass
        results.append(d)
    return results


def get_risk_verdict_summary(db_path: str = DB_PATH) -> dict:
    """Get summary stats for risk verdicts."""
    conn = get_conn(db_path)
    total = conn.execute("SELECT COUNT(*) as c FROM risk_verdicts").fetchone()["c"]
    approved = conn.execute("SELECT COUNT(*) as c FROM risk_verdicts WHERE approved=1").fetchone()["c"]
    rejected = conn.execute("SELECT COUNT(*) as c FROM risk_verdicts WHERE approved=0").fetchone()["c"]
    top_rules = conn.execute(
        """SELECT rule_id, COUNT(*) as cnt FROM risk_verdicts
           WHERE approved=0 AND rule_id IS NOT NULL
           GROUP BY rule_id ORDER BY cnt DESC LIMIT 5"""
    ).fetchall()
    conn.close()
    return {
        "total": total, "approved": approved, "rejected": rejected,
        "approval_rate": round(approved / total * 100, 1) if total > 0 else 0,
        "top_rejection_rules": [dict(r) for r in top_rules],
    }


# ─── B-003: Fund / sleeve / risk daily report persistence ─────────────────


def save_fund_daily_report(
    report_date: str,
    total_nav: float,
    total_cash: float = 0,
    total_positions_value: float = 0,
    unrealised_pnl: float = 0,
    realised_pnl: float = 0,
    daily_return_pct: Optional[float] = None,
    drawdown_pct: float = 0,
    high_water_mark: float = 0,
    currency: str = "GBP",
    db_path: str = DB_PATH,
):
    """Persist a daily fund-level report row (upsert by report_date)."""
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO fund_daily_report
           (report_date, total_nav, total_cash, total_positions_value,
            unrealised_pnl, realised_pnl, daily_return_pct, drawdown_pct,
            high_water_mark, currency, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(report_date) DO UPDATE SET
             total_nav=excluded.total_nav, total_cash=excluded.total_cash,
             total_positions_value=excluded.total_positions_value,
             unrealised_pnl=excluded.unrealised_pnl, realised_pnl=excluded.realised_pnl,
             daily_return_pct=excluded.daily_return_pct, drawdown_pct=excluded.drawdown_pct,
             high_water_mark=excluded.high_water_mark, created_at=excluded.created_at""",
        (report_date, total_nav, total_cash, total_positions_value,
         unrealised_pnl, realised_pnl, daily_return_pct, drawdown_pct,
         high_water_mark, currency, now),
    )
    conn.commit()
    conn.close()


def get_fund_daily_reports(
    days: int = 30,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent fund daily reports for charting and analysis."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM fund_daily_report ORDER BY report_date DESC LIMIT ?",
        (days,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_sleeve_daily_report(
    report_date: str,
    sleeve: str,
    nav: float,
    positions_value: float = 0,
    cash_allocated: float = 0,
    unrealised_pnl: float = 0,
    realised_pnl: float = 0,
    weight_pct: float = 0,
    daily_return_pct: Optional[float] = None,
    db_path: str = DB_PATH,
):
    """Persist a daily sleeve-level report row (upsert by report_date + sleeve)."""
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO sleeve_daily_report
           (report_date, sleeve, nav, positions_value, cash_allocated,
            unrealised_pnl, realised_pnl, weight_pct, daily_return_pct, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(report_date, sleeve) DO UPDATE SET
             nav=excluded.nav, positions_value=excluded.positions_value,
             cash_allocated=excluded.cash_allocated,
             unrealised_pnl=excluded.unrealised_pnl, realised_pnl=excluded.realised_pnl,
             weight_pct=excluded.weight_pct, daily_return_pct=excluded.daily_return_pct,
             created_at=excluded.created_at""",
        (report_date, sleeve, nav, positions_value, cash_allocated,
         unrealised_pnl, realised_pnl, weight_pct, daily_return_pct, now),
    )
    conn.commit()
    conn.close()


def get_sleeve_daily_reports(
    sleeve: Optional[str] = None,
    days: int = 30,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent sleeve daily reports, optionally filtered by sleeve.

    The ``days`` parameter selects the most recent N *distinct report dates*,
    then returns all sleeve rows for those dates.  This avoids silently
    truncating sleeves when multiple sleeves exist (a global LIMIT N would
    return only N total rows regardless of sleeve count).
    """
    conn = get_conn(db_path)
    if sleeve:
        rows = conn.execute(
            """SELECT * FROM sleeve_daily_report
               WHERE sleeve = ?
                 AND report_date IN (
                     SELECT DISTINCT report_date FROM sleeve_daily_report
                     WHERE sleeve = ?
                     ORDER BY report_date DESC LIMIT ?
                 )
               ORDER BY report_date DESC""",
            (sleeve, sleeve, days),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM sleeve_daily_report
               WHERE report_date IN (
                   SELECT DISTINCT report_date FROM sleeve_daily_report
                   ORDER BY report_date DESC LIMIT ?
               )
               ORDER BY report_date DESC, sleeve""",
            (days,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_risk_daily_snapshot(
    snapshot_date: str,
    total_heat_pct: float = 0,
    total_margin_pct: float = 0,
    max_position_pct: float = 0,
    open_position_count: int = 0,
    open_spread_count: int = 0,
    leverage_ratio: float = 0,
    var_95_pct: Optional[float] = None,
    db_path: str = DB_PATH,
):
    """Persist a daily risk metrics snapshot (upsert by snapshot_date)."""
    now = datetime.utcnow().isoformat()
    conn = get_conn(db_path)
    conn.execute(
        """INSERT INTO risk_daily_snapshot
           (snapshot_date, total_heat_pct, total_margin_pct, max_position_pct,
            open_position_count, open_spread_count, leverage_ratio, var_95_pct, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(snapshot_date) DO UPDATE SET
             total_heat_pct=excluded.total_heat_pct, total_margin_pct=excluded.total_margin_pct,
             max_position_pct=excluded.max_position_pct,
             open_position_count=excluded.open_position_count,
             open_spread_count=excluded.open_spread_count,
             leverage_ratio=excluded.leverage_ratio, var_95_pct=excluded.var_95_pct,
             created_at=excluded.created_at""",
        (snapshot_date, total_heat_pct, total_margin_pct, max_position_pct,
         open_position_count, open_spread_count, leverage_ratio, var_95_pct, now),
    )
    conn.commit()
    conn.close()


def get_risk_daily_snapshots(
    days: int = 30,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Get recent risk daily snapshots for charting and analysis."""
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM risk_daily_snapshot ORDER BY snapshot_date DESC LIMIT ?",
        (days,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Initialise on import
init_db()
