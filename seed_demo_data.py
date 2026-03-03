"""
O-001: Seed the database with realistic demo data across all 26+ tables.
Run:  python3 seed_demo_data.py           # seed everything
      python3 seed_demo_data.py --clear-only  # wipe all tables, no re-seed

Populates every table that the UI fragments query so the dashboard renders
with realistic data for look-and-feel validation.
"""
import sys
import os
import json
import uuid
import random
import sqlite3
import argparse
import hashlib

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta, date
from data.trade_db import init_db, DB_PATH, get_conn
from data.order_intent_store import ensure_order_intent_schema

# ─── Constants ────────────────────────────────────────────────────────────────

STRATEGIES = ["GTAA", "DualMomentum", "IBS_SPY", "IBS_QQQ"]
BROKERS = ["IBKR_ISA", "IBKR_TRADING", "CITYINDEX"]
TICKERS = ["SPY", "QQQ", "IWM", "DIA", "EWU", "EWG", "EWJ", "IEF", "GLD", "TLT", "VGK", "XLE"]
NOW = datetime.now()
TODAY = date.today()


def _ts(dt):
    """ISO timestamp from datetime."""
    return dt.isoformat()


def _uid():
    return str(uuid.uuid4())


def _date_str(d):
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    return d.isoformat()


# ─── Clear ────────────────────────────────────────────────────────────────────

# Tables in safe deletion order (children before parents)
CLEAR_ORDER = [
    # order_intent tables (children first)
    "order_execution_metrics",
    "order_intent_transitions",
    "order_intent_attempts",
    "order_intents",
    # ledger children
    "broker_cash_balances",
    "broker_positions",
    "reconciliation_reports",
    "nav_snapshots",
    "risk_verdicts",
    "broker_accounts",
    # strategy
    "strategy_promotions",
    "strategy_parameter_sets",
    # calibration
    "calibration_points",
    "calibration_runs",
    # core trading
    "trades",
    "daily_snapshots",
    "positions",
    "bot_events",
    "option_positions",
    "shadow_trades",
    "option_contracts",
    "order_actions",
    "control_actions",
    "jobs",
    "research_events",
    "strategy_state",
    # fund reporting
    "fund_daily_report",
    "sleeve_daily_report",
    "risk_daily_snapshot",
]


def clear_all(db_path=DB_PATH):
    """Delete all rows from all tables in FK-safe order."""
    conn = get_conn(db_path)
    for table in CLEAR_ORDER:
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # table may not exist yet
    conn.commit()
    conn.close()


# ─── Tier 1: No FK dependencies ──────────────────────────────────────────────

def seed_strategies(conn):
    """4 strategy_parameter_sets + 4 strategy_state rows."""
    now_ts = _ts(NOW)
    rows = []
    for i, strat in enumerate(STRATEGIES):
        sid = _uid()
        params = json.dumps({"lookback": 200, "rebalance_day": 1, "seed": True})
        conn.execute(
            """INSERT INTO strategy_parameter_sets
               (id, created_at, updated_at, strategy_key, name, version, status,
                source_run_id, parameters_payload, notes, created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, now_ts, now_ts, strat, f"{strat}-live-v1", 1, "live",
             None, params, "Seed data", "seed_demo"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO strategy_state (key, value, updated) VALUES (?,?,?)",
            (f"strategy_{strat}_enabled", "true", now_ts),
        )
        rows.append(sid)
    return rows


def seed_broker_positions(conn):
    """6 broker positions across 3 broker accounts."""
    now_ts = _ts(NOW)
    account_ids = []
    broker_configs = [
        ("IBKR_ISA", "ibkr", "ISA", "IBKR ISA Account"),
        ("IBKR_TRADING", "ibkr", "GIA", "IBKR Trading Account"),
        ("CITYINDEX", "cityindex", "SPREADBET", "City Index Spread Betting"),
    ]
    for acc_id, broker, acc_type, label in broker_configs:
        conn.execute(
            """INSERT OR REPLACE INTO broker_accounts
               (id, broker, account_id, account_type, currency, label, is_active, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (acc_id, broker, acc_id, acc_type, "GBP", label, 1, now_ts, now_ts),
        )
        account_ids.append(acc_id)

    positions = [
        (account_ids[0], "SPY", "long", 10.0, 520.0, 5350.0, 150.0, "GTAA", "isa"),
        (account_ids[0], "EWU", "long", 25.0, 32.0, 825.0, 25.0, "DualMomentum", "isa"),
        (account_ids[1], "QQQ", "long", 8.0, 475.0, 3920.0, 120.0, "IBS_QQQ", "trading"),
        (account_ids[1], "IWM", "long", 15.0, 210.0, 3225.0, 75.0, "IBS_SPY", "trading"),
        (account_ids[2], "DIA", "long", 5.0, 395.0, 2000.0, 25.0, "GTAA", "spreadbet"),
        (account_ids[2], "EWG", "short", 12.0, 28.0, -340.0, -4.0, "DualMomentum", "spreadbet"),
    ]
    for acc, ticker, direction, qty, cost, mv, upnl, strat, sleeve in positions:
        conn.execute(
            """INSERT OR REPLACE INTO broker_positions
               (broker_account_id, ticker, direction, quantity, avg_cost, market_value,
                unrealised_pnl, currency, strategy, sleeve, last_synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (acc, ticker, direction, qty, cost, mv, upnl, "USD", strat, sleeve, now_ts),
        )

    # Cash balances
    for acc in account_ids:
        conn.execute(
            """INSERT INTO broker_cash_balances
               (broker_account_id, balance, buying_power, currency, synced_at)
               VALUES (?,?,?,?,?)""",
            (acc, round(random.uniform(5000, 25000), 2),
             round(random.uniform(3000, 20000), 2), "GBP", now_ts),
        )

    return account_ids


def seed_config_snapshots(conn):
    """2 daily config snapshots."""
    for i in range(2):
        d = TODAY - timedelta(days=i)
        conn.execute(
            """INSERT OR REPLACE INTO strategy_state (key, value, updated) VALUES (?,?,?)""",
            (f"config_snapshot_{_date_str(d)}", json.dumps({"snapshot": True, "day": i}),
             _ts(NOW - timedelta(days=i))),
        )


def seed_fund_daily_reports(conn):
    """90 days of equity curve data."""
    nav = 100000.0
    hwm = nav
    for i in range(90, 0, -1):
        d = TODAY - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        daily_ret = random.gauss(0.05, 1.2)
        nav *= (1 + daily_ret / 100)
        cash = nav * random.uniform(0.15, 0.30)
        positions_val = nav - cash
        upnl = random.gauss(50, 200)
        rpnl = random.gauss(30, 150)
        hwm = max(hwm, nav)
        dd = ((nav - hwm) / hwm * 100) if hwm > 0 else 0

        conn.execute(
            """INSERT OR REPLACE INTO fund_daily_report
               (report_date, total_nav, total_cash, total_positions_value,
                unrealised_pnl, realised_pnl, daily_return_pct, drawdown_pct,
                high_water_mark, currency, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (_date_str(d), round(nav, 2), round(cash, 2), round(positions_val, 2),
             round(upnl, 2), round(rpnl, 2), round(daily_ret, 4), round(dd, 4),
             round(hwm, 2), "GBP", _ts(NOW)),
        )


def seed_system_events(conn):
    """60 bot events across last 7 days."""
    categories = [
        ("STARTUP", "Bot started successfully", "System initialized in shadow mode"),
        ("STARTUP", "Bot resumed after restart", "Recovered 3 open positions"),
        ("SCAN", "Signal scan completed", "Scanned 22 markets, 3 candidates found"),
        ("SCAN", "Full market sweep done", "14 sectors analysed, 5 signals above threshold"),
        ("SIGNAL", "Entry signal detected", "IBS < 0.15 on SPY — strong mean-reversion setup"),
        ("SIGNAL", "Exit signal triggered", "QQQ momentum reversal detected"),
        ("SIGNAL", "Multi-factor alert", "DIA: IBS=0.08, RSI=22, MACD bearish crossover"),
        ("ORDER", "Order filled", "BUY 10 SPY @ 530.25 via IBKR_ISA"),
        ("ORDER", "Limit order placed", "SELL 5 QQQ @ 485.00 GTC"),
        ("ORDER", "Spread opened", "Bull put spread SPY 520/510 @ $2.80 credit"),
        ("REJECTION", "Order rejected by broker", "Insufficient margin for 15 IWM"),
        ("REJECTION", "Risk check failed", "Position size exceeds 20% portfolio limit"),
        ("HEARTBEAT", "System heartbeat", "All systems operational — latency 45ms"),
        ("HEARTBEAT", "Broker connectivity OK", "IBKR: 32ms, CityIndex: 78ms"),
        ("MARKET", "Market opened", "US equity session started 09:30 ET"),
        ("MARKET", "Market closed", "US equity session ended 16:00 ET"),
        ("POSITION", "Position updated", "SPY unrealised +$150.30 (+2.8%)"),
        ("POSITION", "Stop adjusted", "IWM trailing stop moved to $208.50"),
        ("ERROR", "Data feed timeout", "Yahoo Finance delayed >30s, retrying"),
        ("ERROR", "API rate limited", "IG Markets: 429 Too Many Requests"),
        ("SNAPSHOT", "Daily snapshot saved", "End-of-day snapshot — NAV $102,450"),
        ("SNAPSHOT", "Risk snapshot taken", "VaR: 2.1%, heat: 35%, margin: 42%"),
        ("RECONCILE", "Reconciliation clean", "All 8 positions matched across brokers"),
        ("RECONCILE", "Mismatch detected", "SPY qty: expected 10, broker shows 9"),
    ]
    for i in range(60):
        cat, headline, detail = random.choice(categories)
        ts = NOW - timedelta(days=random.randint(0, 6), hours=random.randint(0, 23),
                             minutes=random.randint(0, 59))
        ticker = random.choice(TICKERS + [None])
        strat = random.choice(STRATEGIES + [None])
        icon_map = {
            "STARTUP": "🚀", "SCAN": "🔍", "SIGNAL": "📊", "ORDER": "✅",
            "REJECTION": "❌", "ERROR": "⚠️", "MARKET": "🏛️", "POSITION": "📋",
            "HEARTBEAT": "💓", "SHUTDOWN": "🛑", "SNAPSHOT": "📸",
        }
        conn.execute(
            """INSERT INTO bot_events (timestamp, category, icon, headline, detail, ticker, strategy)
               VALUES (?,?,?,?,?,?,?)""",
            (_ts(ts), cat, icon_map.get(cat, "🤖"), headline, detail, ticker, strat),
        )


def seed_incidents(conn):
    """12 control_actions as incidents (4 open, 8 resolved)."""
    incidents = [
        ("kill_switch", "enabled", "Drawdown exceeded 5%", "system", NOW - timedelta(hours=2)),
        ("risk_throttle", "50%", "High volatility detected — VIX > 30", "operator", NOW - timedelta(hours=6)),
        ("cooldown", "600", "SPY: Post-rejection cooldown", "system", NOW - timedelta(hours=8)),
        ("risk_throttle", "75%", "Earnings week throttle", "operator", NOW - timedelta(hours=12)),
        ("kill_switch", "disabled", "Manual clear after review", "operator", NOW - timedelta(days=1)),
        ("cooldown", "1800", "Post-loss cooldown period", "system", NOW - timedelta(days=2)),
        ("recovery", "started", "Partial fill recovery initiated", "dispatcher", NOW - timedelta(days=3)),
        ("kill_switch", "enabled", "Circuit breaker triggered", "system", NOW - timedelta(days=3, hours=6)),
        ("kill_switch", "disabled", "All clear after market stabilised", "operator", NOW - timedelta(days=3, hours=2)),
        ("risk_throttle", "100%", "Risk throttle restored to full", "operator", NOW - timedelta(days=4)),
        ("cooldown", "3600", "QQQ: Gap down cooldown", "system", NOW - timedelta(days=5)),
        ("recovery", "completed", "All partial fills reconciled", "dispatcher", NOW - timedelta(days=6)),
    ]
    for action, value, reason, actor, ts in incidents:
        conn.execute(
            """INSERT INTO control_actions (timestamp, action, value, reason, actor, metadata)
               VALUES (?,?,?,?,?,?)""",
            (_ts(ts), action, value, reason, actor, json.dumps({"seed": True})),
        )


def seed_reconcile_results(conn):
    """3 reconciliation reports."""
    for i, (status, mismatches) in enumerate([("clean", 0), ("mismatch", 2), ("clean", 0)]):
        conn.execute(
            """INSERT INTO reconciliation_reports
               (id, created_at, broker_account_id, status, positions_checked, mismatches_found, details)
               VALUES (?,?,?,?,?,?,?)""",
            (_uid(), _ts(NOW - timedelta(days=i)), "IBKR_ISA", status,
             random.randint(5, 15), mismatches,
             json.dumps([{"ticker": "SPY", "field": "qty", "expected": 10, "actual": 9}] if mismatches else [])),
        )


def seed_kill_switch(conn):
    """Kill switch state (currently off)."""
    conn.execute(
        "INSERT OR REPLACE INTO strategy_state (key, value, updated) VALUES (?,?,?)",
        ("kill_switch_active", "false", _ts(NOW)),
    )


def seed_broker_health(conn):
    """3 broker health entries via strategy_state."""
    for broker in BROKERS:
        conn.execute(
            "INSERT OR REPLACE INTO strategy_state (key, value, updated) VALUES (?,?,?)",
            (f"broker_health_{broker}", json.dumps({
                "broker": broker,
                "status": "healthy",
                "latency_ms": random.randint(20, 150),
                "last_heartbeat": _ts(NOW - timedelta(seconds=random.randint(5, 60))),
                "open_connections": random.randint(1, 3),
            }), _ts(NOW)),
        )


# ─── Tier 2: FK-dependent tables ─────────────────────────────────────────────

def seed_trades(conn):
    """80+ trades across 4 strategies with realistic P&L."""
    trade_count = 0
    for strat in STRATEGIES:
        for _ in range(18 if strat.startswith("IBS") else 22):
            ticker = random.choice(TICKERS[:4] if strat.startswith("IBS") else TICKERS[4:])
            direction = "BUY"
            is_win = random.random() < 0.58
            entry_price = round(random.uniform(180, 550), 2)
            size = round(random.choice([1.0, 2.0, 5.0, 10.0]), 2)
            days_ago = random.randint(1, 60)
            entry_ts = NOW - timedelta(days=days_ago, hours=random.randint(9, 15))
            deal_id = f"DEMO-{_uid()[:8]}"

            # Entry trade
            conn.execute(
                """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, deal_id, pnl, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (_ts(entry_ts), ticker, strat, direction, "OPEN", size, entry_price, deal_id, None, "Seed demo"),
            )
            trade_count += 1

            # Exit trade (most are closed)
            if days_ago > 3:
                hold_days = random.randint(1, min(days_ago - 1, 10))
                exit_ts = entry_ts + timedelta(days=hold_days)
                pnl = round(random.gauss(12.0, 8.0) if is_win else random.gauss(-7.0, 4.0), 2)
                exit_price = round(entry_price + (pnl / size), 2)

                conn.execute(
                    """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, deal_id, pnl, notes)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (_ts(exit_ts), ticker, strat, "SELL", "CLOSE", size, exit_price, deal_id, pnl, "Seed demo"),
                )
                trade_count += 1

    return trade_count


def seed_daily_snapshots(conn):
    """30 days of position snapshots."""
    balance = 10000.0
    for i in range(30, 0, -1):
        d = TODAY - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        daily_pnl = round(random.gauss(15, 45), 2)
        balance += daily_pnl
        unrealised = round(random.gauss(5, 20), 2)
        equity = balance + unrealised

        conn.execute(
            """INSERT OR REPLACE INTO daily_snapshots
               (date, balance, equity, unrealised_pnl, realised_pnl_today, open_positions, drawdown_pct)
               VALUES (?,?,?,?,?,?,?)""",
            (_date_str(d), round(balance, 2), round(equity, 2),
             unrealised, daily_pnl, random.randint(2, 8), round(random.uniform(-3, 0), 2)),
        )


def seed_positions(conn):
    """12 current open positions."""
    positions_data = [
        ("SPY", "GTAA", "long", 10.0, 525.50),
        ("QQQ", "IBS_QQQ", "long", 8.0, 478.20),
        ("IWM", "IBS_SPY", "long", 15.0, 212.30),
        ("EWU", "DualMomentum", "long", 25.0, 31.80),
        ("DIA", "GTAA", "long", 5.0, 396.00),
        ("EWG", "DualMomentum", "short", 12.0, 28.50),
        ("EWJ", "GTAA", "long", 20.0, 67.40),
        ("IEF", "DualMomentum", "long", 30.0, 95.20),
        ("GLD", "GTAA", "long", 15.0, 188.30),
        ("TLT", "DualMomentum", "long", 10.0, 92.70),
        ("VGK", "GTAA", "long", 18.0, 62.40),
        ("XLE", "IBS_SPY", "short", 8.0, 85.10),
    ]
    for ticker, strat, direction, size, price in positions_data:
        deal_id = f"OPEN-{_uid()[:8]}"
        entry_ts = _ts(NOW - timedelta(days=random.randint(1, 14)))
        conn.execute(
            """INSERT OR REPLACE INTO positions
               (deal_id, ticker, strategy, direction, size, entry_price, entry_time,
                current_price, unrealised_pnl, last_updated)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (deal_id, ticker, strat, direction, size, price, entry_ts,
             round(price * (1 + random.gauss(0.005, 0.02)), 2),
             round(random.gauss(20, 50), 2), _ts(NOW)),
        )


def seed_order_intents(conn):
    """35 order intents with fills + audit entries."""
    statuses = (["COMPLETED"] * 18 + ["FAILED"] * 7 + ["QUEUED"] * 5 + ["RUNNING"] * 5)

    for i, status in enumerate(statuses):
        intent_id = _uid()
        corr_id = _uid()
        action_id = _uid()
        ts = NOW - timedelta(hours=random.randint(1, 168))
        strat = random.choice(STRATEGIES)
        ticker = random.choice(TICKERS)
        side = random.choice(["buy", "sell"])
        qty = round(random.choice([1.0, 5.0, 10.0, 25.0]), 2)

        conn.execute(
            """INSERT INTO order_intents
               (intent_id, created_at, updated_at, correlation_id, action_id,
                strategy_id, strategy_version, sleeve, account_type, broker_target,
                instrument, side, qty, order_type, risk_tags, metadata, status, actor, latest_attempt)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (intent_id, _ts(ts), _ts(ts), corr_id, action_id,
             strat, "1", "isa", "ISA", "IBKR_ISA",
             ticker, side, qty, "market", json.dumps(["pre_trade_risk"]),
             json.dumps({"source": "seed"}), status, "seed_demo", 1 if status != "QUEUED" else 0),
        )

        # Transition: QUEUED
        conn.execute(
            """INSERT INTO order_intent_transitions
               (intent_id, transition_at, actor, from_status, to_status, attempt)
               VALUES (?,?,?,?,?,?)""",
            (intent_id, _ts(ts), "seed_demo", None, "QUEUED", 0),
        )

        if status in ("COMPLETED", "FAILED", "RUNNING"):
            conn.execute(
                """INSERT INTO order_intent_transitions
                   (intent_id, transition_at, actor, from_status, to_status, attempt)
                   VALUES (?,?,?,?,?,?)""",
                (intent_id, _ts(ts + timedelta(seconds=1)), "dispatcher", "QUEUED", "RUNNING", 1),
            )

        if status == "COMPLETED":
            fill_price = round(random.uniform(100, 550), 2)
            conn.execute(
                """INSERT INTO order_intent_transitions
                   (intent_id, transition_at, actor, from_status, to_status, attempt,
                    response_payload)
                   VALUES (?,?,?,?,?,?,?)""",
                (intent_id, _ts(ts + timedelta(seconds=2)), "dispatcher", "RUNNING", "COMPLETED", 1,
                 json.dumps({"fill_price": fill_price, "fill_qty": qty})),
            )

            # Execution metric
            conn.execute(
                """INSERT INTO order_execution_metrics
                   (intent_id, action_id, correlation_id, attempt, event_at, status, actor,
                    broker_target, account_type, strategy_id, sleeve, instrument, side,
                    qty_requested, qty_filled, reference_price, fill_price, slippage_bps,
                    dispatch_latency_ms, notional_requested, notional_filled, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (intent_id, action_id, corr_id, 1, _ts(ts + timedelta(seconds=2)), "COMPLETED",
                 "dispatcher", "IBKR_ISA", "ISA", strat, "isa", ticker, side,
                 qty, qty, fill_price + random.uniform(-2, 2), fill_price,
                 round(random.gauss(5, 15), 2), round(random.uniform(50, 500), 1),
                 round(qty * fill_price, 2), round(qty * fill_price, 2),
                 json.dumps({"seed": True})),
            )

        if status == "FAILED":
            conn.execute(
                """INSERT INTO order_intent_transitions
                   (intent_id, transition_at, actor, from_status, to_status, attempt,
                    error_code, error_message)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (intent_id, _ts(ts + timedelta(seconds=3)), "dispatcher", "RUNNING", "FAILED", 1,
                 "BROKER_REJECT", "Insufficient margin"),
            )


def seed_research_results(conn):
    """15 research event entries."""
    event_types = [
        "option_discovery", "calibration", "signal_scan",
        "earnings_event", "insider_trade", "news_sentiment",
        "option_discovery", "signal_scan", "calibration",
        "vol_surface_update", "sector_rotation", "correlation_break",
        "earnings_event", "macro_indicator", "option_discovery",
    ]
    for i, etype in enumerate(event_types):
        eid = _uid()
        ts = NOW - timedelta(days=i)
        payload = json.dumps({"type": etype, "contracts_found": random.randint(5, 50)})
        prov_desc = f"seed_demo/{etype}"
        prov_hash = hashlib.sha256(prov_desc.encode()).hexdigest()[:16]
        conn.execute(
            """INSERT INTO research_events
               (id, created_at, updated_at, event_type, source, source_ref, retrieved_at,
                event_timestamp, symbol, headline, detail, confidence,
                provenance_descriptor, provenance_hash, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, _ts(ts), _ts(ts), etype, "seed_demo", f"seed-{i}", _ts(ts),
             _ts(ts), random.choice(TICKERS), f"Demo {etype} result",
             f"Seed data for {etype}", round(random.uniform(0.5, 0.95), 2),
             prov_desc, prov_hash, payload),
        )


def seed_calibration_runs(conn):
    """6 calibration runs with results."""
    for i in range(6):
        run_id = _uid()
        ts = NOW - timedelta(days=i * 3)
        status = "completed" if i < 2 else "running"
        samples = random.randint(10, 50)
        ratio = round(random.uniform(0.8, 1.4), 3)

        conn.execute(
            """INSERT INTO calibration_runs
               (id, created_at, updated_at, status, scope, samples, overall_ratio, summary_payload, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, _ts(ts), _ts(ts), status, "US 500", samples, ratio,
             json.dumps({"markets": ["US 500"], "seed": True}), None),
        )

        # Calibration points
        for j in range(min(samples, 5)):
            conn.execute(
                """INSERT INTO calibration_points
                   (run_id, timestamp, index_name, ticker, strike, otm_pct, expiry_type,
                    dte, epic, ig_bid, ig_offer, ig_mid, ig_spread, ig_spread_pct,
                    bs_price, ratio_ig_vs_bs, tradeable)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, _ts(ts), "US 500", "SPY", round(520 + j * 10, 0),
                 round(2 + j * 0.5, 1), "DAILY", round(7 + j, 0),
                 f"OPT.US500.{520 + j * 10}.CALL", round(random.uniform(5, 20), 2),
                 round(random.uniform(6, 22), 2), round(random.uniform(5.5, 21), 2),
                 round(random.uniform(0.5, 2), 2), round(random.uniform(5, 15), 1),
                 round(random.uniform(4, 18), 2), round(ratio + random.gauss(0, 0.1), 3),
                 1 if random.random() > 0.3 else 0),
            )


def seed_signal_engine_runs(conn):
    """10 signal engine snapshots via strategy_state."""
    for i in range(10):
        ts = NOW - timedelta(hours=i * 6)
        run_data = {
            "run_id": _uid(),
            "timestamp": _ts(ts),
            "tickers_scanned": random.randint(10, 22),
            "signals_found": random.randint(0, 5),
            "top_candidate": random.choice(TICKERS),
            "composite_score": round(random.uniform(-1, 1), 3),
        }
        conn.execute(
            "INSERT OR REPLACE INTO strategy_state (key, value, updated) VALUES (?,?,?)",
            (f"signal_engine_run_{i}", json.dumps(run_data), _ts(ts)),
        )


def seed_jobs(conn):
    """20 jobs (4 running, 8 completed, 5 failed, 3 queued)."""
    job_configs = [
        ("signal_scan", "running", "Scanning 22 markets for IBS signals"),
        ("discovery", "running", "Discovering option contracts on US 500"),
        ("calibration", "running", "Calibrating IG vs Black-Scholes pricing"),
        ("backtest", "running", "Backtesting GTAA strategy 2024-2025"),
        ("signal_scan", "completed", "Found 3 candidates: SPY, QQQ, IWM"),
        ("eod_reconcile", "completed", "All 8 positions reconciled across 3 brokers"),
        ("signal_scan", "completed", "Full sweep done — 14 sectors, 5 signals"),
        ("discovery", "completed", "45 option contracts catalogued"),
        ("calibration", "completed", "US 500: ratio 1.12, 28 points sampled"),
        ("backtest", "completed", "IBS_SPY: Sharpe 1.4, Return 18.2%, MaxDD -8.1%"),
        ("eod_reconcile", "completed", "Clean reconcile — no mismatches"),
        ("snapshot", "completed", "Daily NAV snapshot saved — $102,450"),
        ("signal_scan", "failed", "Yahoo Finance timeout after 30s"),
        ("calibration", "failed", "IG API rate limited (429)"),
        ("discovery", "failed", "Network error connecting to IG Markets"),
        ("backtest", "failed", "Insufficient data for date range"),
        ("signal_scan", "failed", "Data feed returned stale prices"),
        ("signal_scan", "queued", "Pending scheduled scan"),
        ("discovery", "queued", "Queued option chain refresh"),
        ("calibration", "queued", "Queued EU 50 calibration"),
    ]
    for i, (jtype, status, detail) in enumerate(job_configs):
        jid = _uid()
        ts = NOW - timedelta(hours=i * 3)
        if status == "completed" and jtype == "backtest":
            result = json.dumps({
                "total_return_pct": round(random.gauss(12, 8), 1),
                "sharpe_ratio": round(random.uniform(0.5, 2.0), 2),
                "max_drawdown_pct": round(random.uniform(-15, -3), 1),
                "total_trades": random.randint(40, 200),
                "win_rate": round(random.uniform(48, 68), 1),
            })
        elif status == "completed":
            result = json.dumps({"candidates": random.randint(2, 8)})
        else:
            result = None
        error = detail if status == "failed" else None
        conn.execute(
            """INSERT INTO jobs (id, created_at, updated_at, job_type, status, mode, detail, result, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (jid, _ts(ts), _ts(ts), jtype, status, "paper", detail, result, error),
        )


def seed_ledger_entries(conn):
    """20 nav_snapshot entries for ledger."""
    for i in range(20):
        d = TODAY - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        nav_val = round(100000 + random.gauss(500, 2000), 2)
        conn.execute(
            """INSERT OR REPLACE INTO nav_snapshots
               (snapshot_date, level, level_id, net_liquidation, cash, positions_value,
                unrealised_pnl, realised_pnl, currency, broker, account_type, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_date_str(d), "fund", "fund", nav_val,
             round(nav_val * 0.25, 2), round(nav_val * 0.75, 2),
             round(random.gauss(50, 200), 2), round(random.gauss(30, 100), 2),
             "GBP", None, None, _ts(NOW)),
        )


def seed_promotion_log(conn):
    """8 strategy promotion entries."""
    promotions = [
        ("GTAA", "shadow", "staged_live", "operator", NOW - timedelta(days=30)),
        ("GTAA", "staged_live", "live", "operator", NOW - timedelta(days=20)),
        ("DualMomentum", "shadow", "staged_live", "operator", NOW - timedelta(days=25)),
        ("DualMomentum", "staged_live", "live", "operator", NOW - timedelta(days=15)),
        ("IBS_SPY", "shadow", "staged_live", "system", NOW - timedelta(days=12)),
        ("IBS_SPY", "staged_live", "live", "operator", NOW - timedelta(days=5)),
        ("IBS_QQQ", "shadow", "staged_live", "system", NOW - timedelta(days=7)),
        ("IBS_QQQ", "staged_live", "live", "operator", NOW - timedelta(days=2)),
    ]
    for strat, from_s, to_s, actor, ts in promotions:
        conn.execute(
            """INSERT INTO strategy_promotions
               (timestamp, strategy_key, set_id, from_status, to_status, actor, acknowledgement, note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (_ts(ts), strat, _uid(), from_s, to_s, actor,
             "Approved after soak period", f"Seed promotion {strat}"),
        )


def seed_execution_quality(conn):
    """25 extra execution quality metric records."""
    for i in range(25):
        ts = NOW - timedelta(days=i)
        strat = random.choice(STRATEGIES)
        ticker = random.choice(TICKERS)
        side = random.choice(["buy", "sell"])
        qty = round(random.choice([1.0, 5.0, 10.0]), 2)
        fill_price = round(random.uniform(100, 550), 2)
        ref_price = round(fill_price + random.gauss(0, 2), 2)

        conn.execute(
            """INSERT INTO order_execution_metrics
               (intent_id, action_id, correlation_id, attempt, event_at, status, actor,
                broker_target, account_type, strategy_id, sleeve, instrument, side,
                qty_requested, qty_filled, reference_price, fill_price, slippage_bps,
                dispatch_latency_ms, notional_requested, notional_filled, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_uid(), _uid(), _uid(), 1, _ts(ts), "COMPLETED", "dispatcher",
             random.choice(BROKERS), "ISA", strat, "isa", ticker, side,
             qty, qty, ref_price, fill_price,
             round(abs(fill_price - ref_price) / ref_price * 10000, 2),
             round(random.uniform(30, 300), 1),
             round(qty * ref_price, 2), round(qty * fill_price, 2),
             json.dumps({"seed": True})),
        )


def seed_risk_snapshots(conn):
    """Risk daily snapshots for the last 7 days."""
    for i in range(7):
        d = TODAY - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO risk_daily_snapshot
               (snapshot_date, total_heat_pct, total_margin_pct, max_position_pct,
                open_position_count, open_spread_count, leverage_ratio, var_95_pct, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (_date_str(d), round(random.uniform(15, 45), 2),
             round(random.uniform(20, 60), 2), round(random.uniform(5, 20), 2),
             random.randint(3, 12), random.randint(0, 4),
             round(random.uniform(1.0, 2.5), 2), round(random.uniform(1.5, 5.0), 2),
             _ts(NOW)),
        )


def seed_sleeve_reports(conn):
    """Sleeve daily reports for the last 30 days."""
    sleeves = ["isa", "trading", "spreadbet"]
    for i in range(30, 0, -1):
        d = TODAY - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        for s in sleeves:
            nav = round(random.uniform(20000, 50000), 2)
            conn.execute(
                """INSERT OR REPLACE INTO sleeve_daily_report
                   (report_date, sleeve, nav, positions_value, cash_allocated,
                    unrealised_pnl, realised_pnl, weight_pct, daily_return_pct, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (_date_str(d), s, nav, round(nav * 0.8, 2), round(nav * 0.2, 2),
                 round(random.gauss(20, 80), 2), round(random.gauss(10, 50), 2),
                 round(100.0 / len(sleeves), 2), round(random.gauss(0.05, 0.8), 4),
                 _ts(NOW)),
            )


def seed_order_actions(conn):
    """18 order_actions entries."""
    statuses = (["completed"] * 7 + ["failed"] * 4 + ["queued"] * 4 + ["running"] * 3)
    for i, status in enumerate(statuses):
        ts = NOW - timedelta(hours=i * 4)
        conn.execute(
            """INSERT INTO order_actions
               (id, created_at, updated_at, correlation_id, action_type, status, ticker,
                spread_id, attempt, max_attempts, recoverable, error_code, error_message,
                request_payload, result_payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_uid(), _ts(ts), _ts(ts), _uid(), random.choice(["open_spread", "close_spread"]),
             status, random.choice(TICKERS), None, 1, 3, 1 if status == "failed" else 0,
             "BROKER_REJECT" if status == "failed" else None,
             "Insufficient margin" if status == "failed" else None,
             json.dumps({"seed": True}), json.dumps({"result": "ok"}) if status == "completed" else None),
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def seed_all(db_path=DB_PATH):
    """Seed all tables with demo data."""
    init_db(db_path)
    ensure_order_intent_schema(db_path)
    clear_all(db_path)

    conn = get_conn(db_path)

    # Tier 1 — no FK deps
    seed_strategies(conn)
    seed_broker_positions(conn)
    seed_config_snapshots(conn)
    seed_fund_daily_reports(conn)
    seed_system_events(conn)
    seed_incidents(conn)
    seed_reconcile_results(conn)
    seed_kill_switch(conn)
    seed_broker_health(conn)

    # Tier 2 — FK-dependent
    trade_count = seed_trades(conn)
    seed_daily_snapshots(conn)
    seed_positions(conn)
    seed_order_intents(conn)
    seed_research_results(conn)
    seed_calibration_runs(conn)
    seed_signal_engine_runs(conn)
    seed_jobs(conn)
    seed_ledger_entries(conn)
    seed_promotion_log(conn)
    seed_execution_quality(conn)
    seed_risk_snapshots(conn)
    seed_sleeve_reports(conn)
    seed_order_actions(conn)

    conn.commit()
    conn.close()

    # Print summary
    print_summary(db_path)


def print_summary(db_path=DB_PATH):
    """Print row counts per table."""
    conn = get_conn(db_path)
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║           Seed Demo Data — Summary                  ║")
    print("╠══════════════════════════════════════════════════════╣")

    tables = [
        "strategies" if False else None,  # placeholder
        "strategy_parameter_sets", "strategy_state", "strategy_promotions",
        "broker_accounts", "broker_positions", "broker_cash_balances",
        "fund_daily_report", "sleeve_daily_report", "risk_daily_snapshot",
        "trades", "daily_snapshots", "positions",
        "bot_events", "control_actions", "reconciliation_reports",
        "order_intents", "order_intent_transitions", "order_execution_metrics",
        "research_events", "calibration_runs", "calibration_points",
        "jobs", "nav_snapshots", "order_actions",
    ]
    total = 0
    for table in tables:
        if table is None:
            continue
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            count = row[0] if row else 0
            total += count
            print(f"║  {table:<40s} {count:>6d}  ║")
        except sqlite3.OperationalError:
            print(f"║  {table:<40s}    N/A  ║")

    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  {'TOTAL':<40s} {total:>6d}  ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"\nDatabase: {db_path}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed demo data for UI testing")
    parser.add_argument("--clear-only", action="store_true",
                        help="Clear all tables without re-seeding")
    parser.add_argument("--db-path", default=DB_PATH,
                        help="Path to SQLite database")
    args = parser.parse_args()

    if args.clear_only:
        init_db(args.db_path)
        ensure_order_intent_schema(args.db_path)
        clear_all(args.db_path)
        print("All tables cleared.")
        print_summary(args.db_path)
    else:
        seed_all(args.db_path)
        print("\nDone! Run: python -m app.api.server")
