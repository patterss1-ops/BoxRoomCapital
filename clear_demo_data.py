"""Clear demo/seed data from the database. Run once: python3 clear_demo_data.py"""
import sqlite3
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from data.trade_db import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM trades")
conn.execute("DELETE FROM daily_snapshots")
conn.execute("DELETE FROM positions")
conn.commit()
conn.close()
print(f"Cleared all data from {DB_PATH}")
print("Dashboard will now show empty until the bot starts logging real trades.")
