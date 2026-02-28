"""
EPIC Discovery Tool — Find correct IG EPIC codes for all trading bot markets.
Uses raw REST API (no trading-ig library needed).

Searches IG, verifies each current EPIC, and flags any that fail.

Usage:
    python3 discover_epics.py
"""
import os
import json
import requests
import time
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.ig.com/gateway/deal"


def login():
    """Login and return session with auth headers."""
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json; charset=UTF-8",
        "X-IG-API-KEY": os.getenv("IG_API_KEY"),
        "Version": "2",
    })
    r = s.post(f"{BASE}/session", json={
        "identifier": os.getenv("IG_USERNAME"),
        "password": os.getenv("IG_PASSWORD"),
    })
    if r.status_code != 200:
        print(f"Login failed: {r.status_code} {r.text}")
        return None

    s.headers["CST"] = r.headers.get("CST", "")
    s.headers["X-SECURITY-TOKEN"] = r.headers.get("X-SECURITY-TOKEN", "")
    acct = r.json().get("currentAccountId", "?")
    print(f"Logged in. Account: {acct}\n")
    return s


def search_markets(s, term):
    """Search IG markets for a given term."""
    s.headers["Version"] = "1"
    r = s.get(f"{BASE}/markets", params={"searchTerm": term})
    s.headers["Version"] = "2"
    if r.status_code != 200:
        return []
    return r.json().get("markets", [])


def get_market_info(s, epic, verbose=False):
    """Get market details for a specific EPIC. Returns None if inaccessible."""
    s.headers["Version"] = "3"
    r = s.get(f"{BASE}/markets/{epic}")
    s.headers["Version"] = "2"
    if r.status_code == 200:
        return r.json()
    if verbose:
        print(f"    HTTP {r.status_code}: {r.text[:200]}")
    return None


def main():
    s = login()
    if not s:
        return

    # ─── Part 1: Search for all markets ─────────────────────────────────
    searches = [
        ("SPY",       ["US 500"]),
        ("QQQ",       ["US Tech 100"]),
        ("IWM",       ["Russell 2000"]),
        ("DIA",       ["Wall Street"]),
        ("EWU",       ["FTSE 100"]),
        ("EWG",       ["Germany 40"]),
        ("EWJ",       ["Japan 225", "Nikkei"]),
        ("IEF",       ["US 10-Yr", "T-Bond"]),
        ("CL=F",      ["Oil - US Crude", "US Crude Oil", "Crude Oil"]),
        ("GC=F",      ["Gold"]),
        ("SI=F",      ["Silver"]),
        ("NG=F",      ["Natural Gas"]),
        ("HG=F",      ["Copper"]),
        ("GBPUSD=X",  ["GBP/USD"]),
        ("TLT",       ["US T-Bond", "US Long Bond", "US Treasury Bond"]),
    ]

    # Load current config
    import config
    current_epics = {t: info["epic"] for t, info in config.MARKET_MAP.items()}

    print(f"{'Ticker':<14} {'EPIC':<38} {'Name':<40} {'Type':<15}")
    print("=" * 110)

    for ticker, terms in searches:
        print(f"\n--- {ticker} ---")
        seen = set()
        for term in terms:
            time.sleep(0.3)
            markets = search_markets(s, term)
            for m in markets:
                epic = m.get("epic", "")
                name = m.get("instrumentName", "")
                mtype = m.get("instrumentType", "")

                if epic in seen:
                    continue
                seen.add(epic)

                # Flag spread bet / daily funded markets
                is_sb = any(x in epic for x in [".DAILY.", ".TODAY.", ".UMP.", ".FWM"])
                marker = ""
                cfg_epic = current_epics.get(ticker, "")
                if epic == cfg_epic:
                    marker = " <-- CURRENT CONFIG"
                elif is_sb:
                    marker = " [spread bet]"

                if is_sb or epic == cfg_epic:
                    print(f"  {ticker:<12} {epic:<38} {name:<40} {mtype:<15}{marker}")

    # ─── Part 2: Verify every current config EPIC ───────────────────────
    print(f"\n\n{'='*80}")
    print(f"  VERIFYING ALL CURRENT CONFIG EPICs (with error details)")
    print(f"{'='*80}\n")

    all_epics = {}
    for ticker, info in config.MARKET_MAP.items():
        epic = info["epic"]
        if epic not in all_epics:
            all_epics[epic] = []
        all_epics[epic].append(ticker)

    ok_count = 0
    fail_count = 0

    for epic, tickers in sorted(all_epics.items()):
        time.sleep(0.3)
        info = get_market_info(s, epic, verbose=True)
        ticker_str = ", ".join(tickers)
        if info:
            inst = info.get("instrument", {})
            snap = info.get("snapshot", {})
            dealing = info.get("dealingRules", {})
            status = snap.get("marketStatus", "?")
            bid = snap.get("bid", "?")
            offer = snap.get("offer", "?")
            min_size = dealing.get("minDealSize", {}).get("value", "?")
            name = inst.get("name", "?")
            print(f"  OK   {epic:<38} {name:<35} status={status:<12} bid={bid} min={min_size}  ({ticker_str})")
            ok_count += 1
        else:
            print(f"  FAIL {epic:<38} {'<< CANNOT ACCESS >>':35}  ({ticker_str})")
            fail_count += 1

    print(f"\n  Results: {ok_count} OK, {fail_count} FAILED")

    # ─── Part 3: For failed indices, try alternative EPIC patterns ─────
    if fail_count > 0:
        print(f"\n\n{'='*80}")
        print(f"  TRYING ALTERNATIVE EPICs FOR FAILED MARKETS")
        print(f"{'='*80}\n")

        # Alternative patterns for indices
        index_alternatives = {
            "IX.D.SPTRD.DAILY.IP":  ["IX.D.SPTRD.IFD.IP", "IX.D.SPTRD.IFA.IP", "IX.D.SPTRD.IFE.IP", "IX.D.SPTRD.IFM.IP"],
            "IX.D.NASDAQ.DAILY.IP": ["IX.D.NASDAQ.IFD.IP", "IX.D.NASDAQ.IFA.IP", "IX.D.NASDAQ.IFE.IP", "IX.D.NASDAQ.IFM.IP"],
            "IX.D.RUSSELL.DAILY.IP":["IX.D.RUSSELL.IFD.IP", "IX.D.RUSSELL.IFA.IP", "IX.D.RUSSELL.IFE.IP", "IX.D.RUSSELL.IFM.IP"],
            "IX.D.DOW.DAILY.IP":    ["IX.D.DOW.IFD.IP", "IX.D.DOW.IFA.IP", "IX.D.DOW.IFE.IP", "IX.D.DOW.IFM.IP"],
            "IX.D.FTSE.DAILY.IP":   ["IX.D.FTSE.IFD.IP", "IX.D.FTSE.IFA.IP", "IX.D.FTSE.IFE.IP", "IX.D.FTSE.IFM.IP"],
            "IX.D.DAX.DAILY.IP":    ["IX.D.DAX.IFD.IP", "IX.D.DAX.IFA.IP", "IX.D.DAX.IFE.IP", "IX.D.DAX.IFM.IP"],
            "IX.D.NIKKEI.DAILY.IP": ["IX.D.NIKKEI.IFD.IP", "IX.D.NIKKEI.IFA.IP", "IX.D.NIKKEI.IFE.IP", "IX.D.NIKKEI.IFM.IP"],
            "IR.D.10USTBON.FWM2.IP":["IR.D.10USTBON.FWM1.IP", "IR.D.10USTBON.FWS2.IP", "IR.D.10USTBON.TD.IP"],
        }

        for failed_epic, tickers in sorted(all_epics.items()):
            if get_market_info(s, failed_epic) is not None:
                continue  # Already works

            alts = index_alternatives.get(failed_epic, [])
            if not alts:
                continue

            print(f"  Trying alternatives for {failed_epic} ({', '.join(tickers)}):")
            for alt in alts:
                time.sleep(0.3)
                info = get_market_info(s, alt, verbose=True)
                if info:
                    inst = info.get("instrument", {})
                    snap = info.get("snapshot", {})
                    name = inst.get("name", "?")
                    status = snap.get("marketStatus", "?")
                    print(f"    FOUND: {alt:<35} {name:<35} status={status}")
                else:
                    print(f"    NOPE:  {alt}")

    # ─── Part 4: Check account details ─────────────────────────────────
    print(f"\n\n{'='*80}")
    print(f"  ACCOUNT INFO")
    print(f"{'='*80}\n")
    s.headers["Version"] = "1"
    r = s.get(f"{BASE}/accounts")
    if r.status_code == 200:
        for acc in r.json().get("accounts", []):
            print(f"  Account: {acc.get('accountId')}  Type: {acc.get('accountType')}  Name: {acc.get('accountName')}")
            bal = acc.get("balance", {})
            print(f"    Balance: {bal.get('balance')}  P&L: {bal.get('profitLoss')}  Deposit: {bal.get('deposit')}")
            print(f"    Status: {acc.get('status')}  Currency: {acc.get('currency')}")
            print()

    print("Done. No session logout (web session preserved).")


if __name__ == "__main__":
    main()
