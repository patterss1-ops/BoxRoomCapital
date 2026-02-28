"""
API Ping Test — Buy then Sell on US500 (confirmed working market).

Usage: python3 test_api_ping.py
"""
import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

USERNAME = os.getenv("IG_USERNAME")
PASSWORD = os.getenv("IG_PASSWORD")
API_KEY  = os.getenv("IG_API_KEY")
ACC_NUM  = os.getenv("IG_ACC_NUMBER", "")

BASE = "https://api.ig.com/gateway/deal"

# ─── Authenticate ────────────────────────────────────────────────────────────
print("=" * 60)
print("API PING TEST — BUY + SELL on US 500")
print("=" * 60)
print()

s = requests.Session()
s.headers.update({
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json; charset=UTF-8",
    "X-IG-API-KEY": API_KEY,
})

print("[1] Authenticating...")
r = s.post(f"{BASE}/session",
    json={"identifier": USERNAME, "password": PASSWORD},
    headers={**s.headers, "Version": "2"})

if r.status_code != 200:
    print(f"    FAILED: {r.status_code} — {r.text}")
    sys.exit(1)

s.headers.update({"CST": r.headers["CST"], "X-SECURITY-TOKEN": r.headers["X-SECURITY-TOKEN"]})
auth = r.json()
print(f"    OK — Account: {auth.get('currentAccountId')}")
print()

# ─── Switch to spread bet account if needed ──────────────────────────────────
if auth.get("currentAccountId") != ACC_NUM and ACC_NUM:
    print(f"[1b] Switching to {ACC_NUM}...")
    sw = s.put(f"{BASE}/session", json={"accountId": ACC_NUM, "defaultAccount": "false"},
               headers={**s.headers, "Version": "1"})
    print(f"     Switch: {sw.status_code}")
    print()

# ─── Get market info ─────────────────────────────────────────────────────────
print("[2] Fetching US 500 market info...")
EPIC = "IX.D.SPTRD.DAILY.IP"

mkt = s.get(f"{BASE}/markets/{EPIC}", headers={**s.headers, "Version": "3"})
if mkt.status_code != 200:
    # Try alternative epic
    EPIC = "IX.D.SPTRD.IFD.IP"
    print(f"    Trying {EPIC}...")
    mkt = s.get(f"{BASE}/markets/{EPIC}", headers={**s.headers, "Version": "3"})

if mkt.status_code == 200:
    m = mkt.json()
    snap = m.get("snapshot", {})
    inst = m.get("instrument", {})
    rules = m.get("dealingRules", {})
    min_size = rules.get("minDealSize", {}).get("value", "?")
    min_stop = rules.get("minNormalStopOrLimitDistance", {}).get("value", "?")
    print(f"    Epic:       {EPIC}")
    print(f"    Name:       {inst.get('name')}")
    print(f"    Status:     {snap.get('marketStatus')}")
    print(f"    Bid/Offer:  {snap.get('bid')}/{snap.get('offer')}")
    print(f"    Min size:   {min_size}")
    print(f"    Min stop:   {min_stop}")
    print()

    if snap.get("marketStatus") != "TRADEABLE":
        print(f"    *** Market not tradeable right now. Try during US hours. ***")
        # session expires naturally — no logout (avoids killing IG web session)
        sys.exit(0)
else:
    print(f"    Failed: {mkt.status_code} — {mkt.text[:200]}")
    # session expires naturally — no logout (avoids killing IG web session)
    sys.exit(1)

# ─── Place BUY order ─────────────────────────────────────────────────────────
print("[3] Placing BUY order (min size, with stop)...")
stop_dist = max(float(min_stop) * 2, 20)  # safe stop distance

order = {
    "epic": EPIC,
    "expiry": "DFB",
    "direction": "BUY",
    "size": str(min_size),
    "orderType": "MARKET",
    "currencyCode": "GBP",
    "forceOpen": True,
    "guaranteedStop": False,
    "stopDistance": str(stop_dist),
    "limitDistance": None,
}

print(f"    Payload: size={min_size}, stop={stop_dist}")
r = s.post(f"{BASE}/positions/otc", json=order, headers={**s.headers, "Version": "2"})
print(f"    HTTP: {r.status_code}")

if r.status_code != 200:
    print(f"    Error: {r.text[:300]}")
    # session expires naturally — no logout (avoids killing IG web session)
    sys.exit(1)

deal_ref = r.json().get("dealReference")
print(f"    Deal ref: {deal_ref}")
print()

# ─── Confirm ─────────────────────────────────────────────────────────────────
print("[4] Confirming deal...")
time.sleep(2)
c = s.get(f"{BASE}/confirms/{deal_ref}", headers={**s.headers, "Version": "1"})
print(f"    Confirm HTTP: {c.status_code}")

if c.status_code == 200:
    conf = c.json()
    status = conf.get("dealStatus")
    deal_id = conf.get("dealId")
    reason = conf.get("reason")
    print(f"    Status: {status}")
    print(f"    Reason: {reason}")
    print(f"    Deal ID: {deal_id}")

    if status != "ACCEPTED":
        print(f"\n    *** ORDER REJECTED: {reason} ***")
        print(f"    Full response: {json.dumps(conf, indent=2)}")
        # Check activity log for more detail
        time.sleep(1)
        act = s.get(f"{BASE}/history/activity",
                    params={"from": "2026-02-26T00:00:00", "to": "2026-02-27T00:00:00"},
                    headers={**s.headers, "Version": "3"})
        if act.status_code == 200:
            activities = act.json().get("activities", [])
            rejects = [a for a in activities if a.get("status") == "REJECTED"]
            if rejects:
                print(f"\n    Activity log rejection: {rejects[0].get('details', {})}")
        # session expires naturally — no logout (avoids killing IG web session)
        sys.exit(1)
else:
    print(f"    Confirm failed: {c.status_code} — {c.text[:200]}")
    print("    Checking positions instead...")

print()

# ─── Verify position is open ─────────────────────────────────────────────────
print("[5] Checking open positions...")
time.sleep(1)
pos = s.get(f"{BASE}/positions", headers={**s.headers, "Version": "2"})
if pos.status_code == 200:
    positions = pos.json().get("positions", [])
    print(f"    Open positions: {len(positions)}")
    our_pos = None
    for p in positions:
        pm = p.get("market", {})
        pp = p.get("position", {})
        print(f"    - {pm.get('instrumentName')}: {pp.get('direction')} {pp.get('size')} @ {pp.get('openLevel')}")
        print(f"      Deal ID: {pp.get('dealId')}")
        if pm.get("epic") == EPIC:
            our_pos = p

    if not our_pos:
        print(f"\n    *** No position found for {EPIC}! ***")
        # session expires naturally — no logout (avoids killing IG web session)
        sys.exit(1)
else:
    print(f"    Failed: {pos.status_code}")

close_deal_id = our_pos["position"]["dealId"]
print(f"\n    BUY confirmed! Deal ID: {close_deal_id}")
print()

# ─── Close the position (SELL) ────────────────────────────────────────────────
print("[6] Closing position (SELL)...")
time.sleep(2)

close_dir = "SELL"  # opposite of our BUY
close_order = {
    "dealId": close_deal_id,
    "direction": close_dir,
    "size": str(min_size),
    "orderType": "MARKET",
}

r2 = s.post(f"{BASE}/positions/otc", json=close_order,
            headers={**s.headers, "Version": "1", "_method": "DELETE"})
print(f"    HTTP: {r2.status_code}")

if r2.status_code == 200:
    close_ref = r2.json().get("dealReference")
    print(f"    Close ref: {close_ref}")

    time.sleep(2)
    c2 = s.get(f"{BASE}/confirms/{close_ref}", headers={**s.headers, "Version": "1"})
    if c2.status_code == 200:
        conf2 = c2.json()
        print(f"    Close status: {conf2.get('dealStatus')}")
        print(f"    Close reason: {conf2.get('reason')}")
        print(f"    P&L: {conf2.get('profit')}")
    else:
        print(f"    Close confirm: {c2.status_code}")
else:
    print(f"    Close failed: {r2.status_code} — {r2.text[:300]}")

print()

# ─── Final check ─────────────────────────────────────────────────────────────
print("[7] Final position check...")
time.sleep(1)
pos2 = s.get(f"{BASE}/positions", headers={**s.headers, "Version": "2"})
if pos2.status_code == 200:
    remaining = pos2.json().get("positions", [])
    us500_left = [p for p in remaining if p.get("market", {}).get("epic") == EPIC]
    if not us500_left:
        print("    All clear — no US500 positions remaining")
    else:
        print(f"    WARNING: {len(us500_left)} US500 position(s) still open!")

print()
print("=" * 60)
print("PING TEST COMPLETE")
print("=" * 60)

# session expires naturally — no logout (avoids killing IG web session)
