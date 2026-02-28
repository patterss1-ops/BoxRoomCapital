"""
Close the open US500 position.

Usage: python3 close_position.py
"""
import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.ig.com/gateway/deal"

s = requests.Session()
s.headers.update({
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json; charset=UTF-8",
    "X-IG-API-KEY": os.getenv("IG_API_KEY"),
})

# Auth
print("Authenticating...")
r = s.post(f"{BASE}/session",
    json={"identifier": os.getenv("IG_USERNAME"), "password": os.getenv("IG_PASSWORD")},
    headers={**s.headers, "Version": "2"})
s.headers.update({"CST": r.headers["CST"], "X-SECURITY-TOKEN": r.headers["X-SECURITY-TOKEN"]})
print(f"  OK — {r.json().get('currentAccountId')}")

# Find the position
print("\nFinding open positions...")
pos = s.get(f"{BASE}/positions", headers={**s.headers, "Version": "2"})
positions = pos.json().get("positions", [])
print(f"  {len(positions)} open position(s)")

for p in positions:
    pm = p.get("market", {})
    pp = p.get("position", {})
    deal_id = pp.get("dealId")
    direction = pp.get("direction")
    size = pp.get("size")
    epic = pm.get("epic")
    name = pm.get("instrumentName")

    print(f"\n  Closing: {name} — {direction} {size}")
    print(f"  Deal ID: {deal_id}")

    # Close direction is opposite of open direction
    close_dir = "SELL" if direction == "BUY" else "BUY"

    close_payload = {
        "dealId": deal_id,
        "direction": close_dir,
        "size": str(size),
        "orderType": "MARKET",
    }

    # IG uses _method=DELETE header for closing positions
    r2 = s.post(
        f"{BASE}/positions/otc",
        json=close_payload,
        headers={**s.headers, "Version": "1", "_method": "DELETE"},
    )
    print(f"  HTTP: {r2.status_code}")
    print(f"  Response: {r2.text[:300]}")

    if r2.status_code == 200:
        close_ref = r2.json().get("dealReference")
        time.sleep(2)
        c = s.get(f"{BASE}/confirms/{close_ref}", headers={**s.headers, "Version": "1"})
        if c.status_code == 200:
            conf = c.json()
            print(f"  Status: {conf.get('dealStatus')}")
            print(f"  Reason: {conf.get('reason')}")
            print(f"  P&L: {conf.get('profit')}")

# Final check
time.sleep(1)
pos2 = s.get(f"{BASE}/positions", headers={**s.headers, "Version": "2"})
remaining = pos2.json().get("positions", [])
print(f"\nRemaining positions: {len(remaining)}")

s.delete(f"{BASE}/session")
print("Done.")
