"""
Account Diagnostic — Pull all available account info to identify trading restrictions.

Usage: python3 diagnose_account.py
"""
import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

USERNAME = os.getenv("IG_USERNAME")
PASSWORD = os.getenv("IG_PASSWORD")
API_KEY = os.getenv("IG_API_KEY")
ACC_NUMBER = os.getenv("IG_ACC_NUMBER", "")

BASE_URL = "https://api.ig.com/gateway/deal"

# ─── Authenticate ────────────────────────────────────────────────────────────
print("=" * 60)
print("IG ACCOUNT DIAGNOSTIC")
print("=" * 60)
print()

print("Authenticating (V2)...")
session = requests.Session()
session.headers.update({
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json; charset=UTF-8",
    "X-IG-API-KEY": API_KEY,
})

resp = session.post(
    f"{BASE_URL}/session",
    json={"identifier": USERNAME, "password": PASSWORD},
    headers={**session.headers, "Version": "2"},
)

if resp.status_code != 200:
    print(f"  Auth FAILED: {resp.status_code} — {resp.text}")
    sys.exit(1)

cst = resp.headers.get("CST", "")
xst = resp.headers.get("X-SECURITY-TOKEN", "")
auth = resp.json()
session.headers.update({"CST": cst, "X-SECURITY-TOKEN": xst})

print(f"  Auth OK")
print()

# ─── Session info (full dump) ────────────────────────────────────────────────
print("─" * 60)
print("SESSION INFO (everything returned at login):")
print("─" * 60)
for k, v in sorted(auth.items()):
    print(f"  {k}: {v}")
print()

# ─── All accounts ────────────────────────────────────────────────────────────
print("─" * 60)
print("ALL ACCOUNTS:")
print("─" * 60)
acc_resp = session.get(
    f"{BASE_URL}/accounts",
    headers={**session.headers, "Version": "1"},
)
if acc_resp.status_code == 200:
    accounts = acc_resp.json().get("accounts", [])
    for acc in accounts:
        print(f"\n  Account ID:  {acc.get('accountId')}")
        print(f"  Name:        {acc.get('accountName')}")
        print(f"  Type:        {acc.get('accountType')}")
        print(f"  Status:      {acc.get('status')}")
        print(f"  Preferred:   {acc.get('preferred')}")
        print(f"  Currency:    {acc.get('currency')}")
        balance = acc.get("balance", {})
        print(f"  Balance:     £{balance.get('balance', '?')}")
        print(f"  Deposit:     £{balance.get('deposit', '?')}")
        print(f"  P&L:         £{balance.get('profitLoss', '?')}")
        print(f"  Available:   £{balance.get('available', '?')}")
        # Check for any flags
        print(f"  Can transfer in:  {acc.get('canTransferFrom', '?')}")
        print(f"  Can transfer out: {acc.get('canTransferTo', '?')}")
        # Dump anything else
        known_keys = {'accountId','accountName','accountType','status','preferred',
                      'currency','balance','canTransferFrom','canTransferTo'}
        extras = {k: v for k, v in acc.items() if k not in known_keys}
        if extras:
            print(f"  Extra fields: {json.dumps(extras, indent=4)}")
else:
    print(f"  Failed: {acc_resp.status_code} — {acc_resp.text}")

print()

# ─── Switch to spread bet account explicitly ─────────────────────────────────
print("─" * 60)
print(f"SWITCHING TO ACCOUNT {ACC_NUMBER}...")
print("─" * 60)
switch_resp = session.put(
    f"{BASE_URL}/session",
    json={"accountId": ACC_NUMBER, "defaultAccount": "false"},
    headers={**session.headers, "Version": "1"},
)
print(f"  Switch: {switch_resp.status_code}")
if switch_resp.status_code == 200:
    sw = switch_resp.json()
    print(f"  Response: {json.dumps(sw, indent=2)}")
    # Re-check session
    sess2 = session.get(
        f"{BASE_URL}/session",
        headers={**session.headers, "Version": "1"},
    )
    if sess2.status_code == 200:
        s2 = sess2.json()
        print(f"\n  After switch:")
        print(f"    Current account: {s2.get('currentAccountId')}")
        print(f"    Dealing enabled: {s2.get('dealingEnabled')}")
        print(f"    Has active demo: {s2.get('hasActiveDemoAccounts')}")
        print(f"    Has active live: {s2.get('hasActiveLiveAccounts')}")
        print(f"    Trailing stops:  {s2.get('trailingStopsEnabled')}")
        # Dump everything
        for k, v in sorted(s2.items()):
            if k not in ('currentAccountId','dealingEnabled','hasActiveDemoAccounts',
                         'hasActiveLiveAccounts','trailingStopsEnabled'):
                print(f"    {k}: {v}")
else:
    print(f"  Switch failed: {switch_resp.text}")

print()

# ─── Client sentiment (test data endpoint) ───────────────────────────────────
print("─" * 60)
print("CLIENT SENTIMENT (data access test):")
print("─" * 60)
sent_resp = session.get(
    f"{BASE_URL}/clientsentiment?marketIds=CS.D.GBPUSD.TODAY.IP",
    headers={**session.headers, "Version": "1"},
)
print(f"  Sentiment: {sent_resp.status_code}")
if sent_resp.status_code == 200:
    print(f"  Data access OK — can read market data")
else:
    print(f"  {sent_resp.text[:200]}")

print()

# ─── Market details for GBP/USD ──────────────────────────────────────────────
print("─" * 60)
print("MARKET DETAILS (CS.D.GBPUSD.TODAY.IP):")
print("─" * 60)
mkt_resp = session.get(
    f"{BASE_URL}/markets/CS.D.GBPUSD.TODAY.IP",
    headers={**session.headers, "Version": "3"},
)
if mkt_resp.status_code == 200:
    mkt = mkt_resp.json()
    inst = mkt.get("instrument", {})
    snap = mkt.get("snapshot", {})
    rules = mkt.get("dealingRules", {})

    print(f"  Name:           {inst.get('name')}")
    print(f"  Type:           {inst.get('type')}")
    print(f"  Market status:  {snap.get('marketStatus')}")
    print(f"  Bid/Offer:      {snap.get('bid')}/{snap.get('offer')}")
    print(f"  Spread bet?:    {inst.get('sprintMarketsMaximumExpiryDate', 'N/A')}")
    print(f"  Force open:     {inst.get('forceOpenAllowed')}")
    print(f"  Controlled risk: {inst.get('controlledRiskAllowed')}")
    print(f"  Streaming OK:   {inst.get('streamingPricesAvailable')}")
    print(f"  Limited risk:   {inst.get('limitedRiskPremium')}")

    # Dealing rules
    print(f"\n  Dealing Rules:")
    min_size = rules.get("minDealSize", {})
    print(f"    Min deal size: {min_size.get('value')} ({min_size.get('unit')})")
    min_stop = rules.get("minNormalStopOrLimitDistance", {})
    print(f"    Min stop dist: {min_stop.get('value')} ({min_stop.get('unit')})")
    min_step = rules.get("minStepDistance", {})
    print(f"    Min step:      {min_step.get('value')} ({min_step.get('unit')})")

    # Check for special conditions
    special = inst.get("specialInfo", [])
    if special:
        print(f"\n  *** SPECIAL CONDITIONS: {special} ***")

    margin_factor = inst.get("marginFactor")
    margin_unit = inst.get("marginFactorUnit")
    print(f"    Margin:        {margin_factor} ({margin_unit})")

    # Dump any flags we haven't shown
    print(f"\n  All instrument flags:")
    for k in sorted(inst.keys()):
        if k not in ('name','type','sprintMarketsMaximumExpiryDate','forceOpenAllowed',
                     'controlledRiskAllowed','streamingPricesAvailable','limitedRiskPremium',
                     'specialInfo','marginFactor','marginFactorUnit','currencies','expiryDetails',
                     'rolloverDetails','openingHours','slippageFactor'):
            print(f"    {k}: {inst[k]}")
else:
    print(f"  Failed: {mkt_resp.status_code} — {mkt_resp.text[:200]}")

print()

# ─── Check all activity for rejections ────────────────────────────────────────
print("─" * 60)
print("RECENT REJECTION DETAILS:")
print("─" * 60)
act_resp = session.get(
    f"{BASE_URL}/history/activity",
    params={"from": "2026-02-25T00:00:00", "to": "2026-02-27T00:00:00", "detailed": "true"},
    headers={**session.headers, "Version": "3"},
)
if act_resp.status_code == 200:
    activities = act_resp.json().get("activities", [])
    rejections = [a for a in activities if a.get("status") == "REJECTED"]
    print(f"  Total activities: {len(activities)}")
    print(f"  Rejections: {len(rejections)}")
    if rejections:
        print(f"\n  Last rejection (full detail):")
        print(f"  {json.dumps(rejections[0], indent=4)}")
else:
    print(f"  Failed: {act_resp.status_code}")

print()

# ─── Try the /session/encryptionKey endpoint ──────────────────────────────────
print("─" * 60)
print("ACCOUNT PREFERENCES:")
print("─" * 60)
pref_resp = session.get(
    f"{BASE_URL}/accounts/preferences",
    headers={**session.headers, "Version": "1"},
)
print(f"  Preferences: {pref_resp.status_code}")
if pref_resp.status_code == 200:
    prefs = pref_resp.json()
    for k, v in sorted(prefs.items()):
        print(f"    {k}: {v}")
else:
    print(f"  {pref_resp.text[:200]}")

print()

# ─── Summary / Diagnosis ─────────────────────────────────────────────────────
print("=" * 60)
print("DIAGNOSIS SUMMARY")
print("=" * 60)
print("""
If dealingEnabled = True but orders are "Rejected: Sorry":

MOST LIKELY CAUSES:
1. T&Cs / Risk Warnings not accepted
   → Log into IG web platform → Look for banners/popups
   → Try: My IG → Settings → Agreements/Declarations
   → Or: dashboard.ig.com → look for outstanding actions

2. W-8BEN tax form (if trading US markets)
   → My IG → Tax information → Complete W-8BEN form

3. Suitability assessment expired / incomplete
   → IG may require periodic re-assessment
   → My IG → Settings → Appropriateness assessment

4. Account dormant / restricted
   → Call IG helpdesk: 0800 195 3100 (UK freephone)
   → Or live chat on ig.com

5. API key not enabled for dealing
   → labs.ig.com → Check your API key has "dealing" permission

QUICKEST FIX: Call IG on 0800 195 3100 and say:
"My spread bet account PUQ8X is rejecting all trades with
'Sorry'. Can you check if there are any outstanding T&Cs,
declarations, or restrictions on my account?"
""")

session.delete(f"{BASE_URL}/session")
print("Logged out. Done.")
