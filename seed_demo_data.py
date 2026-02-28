"""
Seed the database with realistic demo data so the dashboard has something to show.
Run once: python3 seed_demo_data.py
Safe to delete after you have real trades.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from data.trade_db import init_db, log_trade, save_daily_snapshot, upsert_position, DB_PATH
from datetime import datetime, timedelta
import random
import sqlite3

# Clear existing demo data
conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM trades")
conn.execute("DELETE FROM daily_snapshots")
conn.execute("DELETE FROM positions")
conn.commit()
conn.close()

init_db()

print("Seeding demo data...")

# ─── Simulated trades over the last 30 days ──────────────────────────────────

strategies = {
    "IBS++ v3": {
        "tickers": ["SPY", "QQQ", "IWM", "DIA", "EWU", "EWG"],
        "avg_hold": 3,
        "win_rate": 0.62,
        "avg_win": 8.5,
        "avg_loss": -5.2,
    },
    "Trend Following v2": {
        "tickers": ["SI=F", "GC=F_trend", "CL=F_trend", "NG=F"],
        "avg_hold": 12,
        "win_rate": 0.42,
        "avg_win": 22.0,
        "avg_loss": -9.5,
    },
    "SPY/TLT Rotation v3": {
        "tickers": ["SPY"],
        "avg_hold": 21,
        "win_rate": 0.65,
        "avg_win": 35.0,
        "avg_loss": -15.0,
    },
}

base_date = datetime.now() - timedelta(days=30)
trade_id = 1000

for strat_name, params in strategies.items():
    for ticker in params["tickers"]:
        # Generate 3-8 round trips per ticker
        n_trades = random.randint(3, 8) if strat_name != "SPY/TLT Rotation v3" else 1
        current_date = base_date + timedelta(days=random.randint(0, 5))

        for _ in range(n_trades):
            if current_date > datetime.now():
                break

            # Entry
            is_win = random.random() < params["win_rate"]
            direction = "BUY" if random.random() > 0.3 else "SELL"
            if strat_name == "IBS++ v3":
                direction = "BUY"  # Long only

            entry_price = round(random.uniform(100, 5500), 2)
            size = round(random.choice([0.50, 0.25, 1.00]), 2)

            entry_time = current_date.isoformat()
            deal_id = f"DEMO{trade_id}"
            trade_id += 1

            log_trade(
                ticker=ticker, strategy=strat_name, direction=direction,
                action="OPEN", size=size, price=entry_price,
                deal_id=deal_id, notes="Demo data",
            )

            # Exit after avg_hold days
            hold_days = max(1, int(random.gauss(params["avg_hold"], params["avg_hold"] * 0.3)))
            exit_date = current_date + timedelta(days=hold_days)

            if exit_date < datetime.now():
                pnl = round(random.gauss(params["avg_win"], params["avg_win"] * 0.4), 2) if is_win \
                    else round(random.gauss(params["avg_loss"], abs(params["avg_loss"]) * 0.3), 2)

                exit_price = entry_price + (pnl / size if direction == "BUY" else -pnl / size)

                # Override timestamp for the exit
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    """INSERT INTO trades (timestamp, ticker, strategy, direction, action, size, price, deal_id, pnl, notes)
                       VALUES (?, ?, ?, ?, 'CLOSE', ?, ?, ?, ?, 'Demo data')""",
                    (exit_date.isoformat(), ticker, strat_name,
                     "SELL" if direction == "BUY" else "BUY",
                     size, round(exit_price, 2), deal_id, pnl),
                )
                # Fix entry timestamp too
                conn.execute(
                    "UPDATE trades SET timestamp=? WHERE deal_id=? AND action='OPEN'",
                    (entry_time, deal_id),
                )
                conn.commit()
                conn.close()
            else:
                # Still open — add to positions
                upsert_position(
                    deal_id=deal_id, ticker=ticker, strategy=strat_name,
                    direction="long" if direction == "BUY" else "short",
                    size=size, entry_price=entry_price, entry_time=entry_time,
                )

            current_date = exit_date + timedelta(days=random.randint(1, 3))

# ─── Daily snapshots ─────────────────────────────────────────────────────────

balance = 10000.0
for day_offset in range(30):
    d = base_date + timedelta(days=day_offset)
    if d > datetime.now():
        break
    if d.weekday() >= 5:  # skip weekends
        continue

    daily_pnl = round(random.gauss(15, 45), 2)
    balance += daily_pnl
    unrealised = round(random.gauss(5, 20), 2)

    # Use raw SQL to set the correct date
    conn = sqlite3.connect(DB_PATH)
    peak_row = conn.execute("SELECT MAX(equity) as peak FROM daily_snapshots").fetchone()
    peak = peak_row[0] if peak_row and peak_row[0] else balance + unrealised
    equity = balance + unrealised
    peak = max(peak, equity)
    dd = ((equity - peak) / peak * 100) if peak > 0 else 0

    conn.execute(
        """INSERT OR REPLACE INTO daily_snapshots (date, balance, equity, unrealised_pnl, realised_pnl_today, open_positions, drawdown_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (d.strftime("%Y-%m-%d"), round(balance, 2), round(equity, 2),
         unrealised, daily_pnl, random.randint(1, 5), round(dd, 2)),
    )
    conn.commit()
    conn.close()

print(f"Done! Seeded trades.db with demo data.")
print(f"Database: {DB_PATH}")
print(f"\nNow run: streamlit run dashboard.py")
