"""
Find the correct EPICs for QQQ (US Tech 100) and IEF (US 10-Year T-Note).
These two returned 404 in the main discovery.

Run: python3 fix_missing_epics.py
"""
import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.ig.com/gateway/deal"


def login():
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
        print(f"Login failed: {r.status_code}")
        return None
    s.headers["CST"] = r.headers.get("CST", "")
    s.headers["X-SECURITY-TOKEN"] = r.headers.get("X-SECURITY-TOKEN", "")
    print(f"Logged in: {r.json().get('currentAccountId')}\n")
    return s


def search(s, term):
    s.headers["Version"] = "1"
    r = s.get(f"{BASE}/markets", params={"searchTerm": term})
    s.headers["Version"] = "2"
    if r.status_code != 200:
        print(f"  Search '{term}' failed: {r.status_code}")
        return []
    return r.json().get("markets", [])


def verify(s, epic):
    """Try to access a market. Returns info dict or None."""
    s.headers["Version"] = "3"
    r = s.get(f"{BASE}/markets/{epic}")
    s.headers["Version"] = "2"
    if r.status_code == 200:
        return r.json()
    return None


def main():
    s = login()
    if not s:
        return

    # ─── QQQ: Find the Nasdaq/US Tech 100 DFB ───────────────────────────
    print("=" * 70)
    print("  FINDING: QQQ (US Tech 100 / Nasdaq 100)")
    print("=" * 70)

    # Try known EPIC patterns first
    nasdaq_guesses = [
        "IX.D.NASDAQ.DAILY.IP",
        "IX.D.NASDAQ.IFD.IP",
        "IX.D.NASDAQ.IFA.IP",
        "IX.D.NASDAQ.IFE.IP",
        "IX.D.NASAQ.DAILY.IP",
        "IX.D.NAS100.DAILY.IP",
        "IX.D.USTEC.DAILY.IP",
        "IX.D.TECH100.DAILY.IP",
    ]
    print("\nTrying known EPIC patterns:")
    for epic in nasdaq_guesses:
        time.sleep(0.5)  # Gentle rate limiting
        info = verify(s, epic)
        if info:
            inst = info.get("instrument", {})
            snap = info.get("snapshot", {})
            print(f"  FOUND! {epic:<35} {inst.get('name', '?'):<35} bid={snap.get('bid')}")
        else:
            print(f"  nope:  {epic}")

    # Search and verify each result
    print("\nSearching 'US Tech 100':")
    for m in search(s, "US Tech 100"):
        epic = m.get("epic", "")
        name = m.get("instrumentName", "")
        mtype = m.get("instrumentType", "")
        if mtype == "INDICES":
            time.sleep(0.5)
            info = verify(s, epic)
            status = "ACCESSIBLE" if info else "blocked"
            bid = info.get("snapshot", {}).get("bid", "?") if info else "?"
            print(f"  {epic:<35} {name:<40} {status}  bid={bid}")

    print("\nSearching 'Nasdaq 100':")
    for m in search(s, "Nasdaq 100"):
        epic = m.get("epic", "")
        name = m.get("instrumentName", "")
        mtype = m.get("instrumentType", "")
        if mtype == "INDICES":
            time.sleep(0.5)
            info = verify(s, epic)
            status = "ACCESSIBLE" if info else "blocked"
            bid = info.get("snapshot", {}).get("bid", "?") if info else "?"
            print(f"  {epic:<35} {name:<40} {status}  bid={bid}")

    # ─── IEF: Find the US 10-Year T-Note ─────────────────────────────────
    print("\n" + "=" * 70)
    print("  FINDING: IEF (US 10-Year T-Note / Treasury Bond)")
    print("=" * 70)

    bond_guesses = [
        "IR.D.10USTBON.FWM2.IP",
        "IR.D.10USTBON.FWM1.IP",
        "IR.D.10USTBON.FWS2.IP",
        "IR.D.10USTBON.TD.IP",
        "IR.D.10USTBON.DAILY.IP",
        "CC.D.10USTBON.USS.IP",
        "IR.D.USTBOND.FWM2.IP",
        "IX.D.10USTBON.DAILY.IP",
    ]
    print("\nTrying known EPIC patterns:")
    for epic in bond_guesses:
        time.sleep(0.5)
        info = verify(s, epic)
        if info:
            inst = info.get("instrument", {})
            snap = info.get("snapshot", {})
            print(f"  FOUND! {epic:<35} {inst.get('name', '?'):<35} bid={snap.get('bid')}")
        else:
            print(f"  nope:  {epic}")

    # Search various terms
    for term in ["10 Year T-Note", "US Treasury", "T-Bond", "US Bond"]:
        print(f"\nSearching '{term}':")
        time.sleep(0.5)
        for m in search(s, term):
            epic = m.get("epic", "")
            name = m.get("instrumentName", "")
            mtype = m.get("instrumentType", "")
            # Show interest rate / bond instruments
            if any(x in mtype for x in ["RATES", "BOND", "INTEREST"]) or "IR." in epic or "bond" in name.lower() or "treasury" in name.lower() or "t-note" in name.lower():
                time.sleep(0.5)
                info = verify(s, epic)
                status = "ACCESSIBLE" if info else "blocked"
                bid = info.get("snapshot", {}).get("bid", "?") if info else "?"
                print(f"  {epic:<35} {name:<40} [{mtype}] {status}  bid={bid}")

    print("\nDone. No session logout.")


if __name__ == "__main__":
    main()
