"""
Options Discovery Tool — Find all IG spread bet options for our target indices.

Browses the IG market navigation tree and searches for options on:
  US 500, US Tech 100, Wall Street, Germany 40, FTSE 100

For each option found, retrieves:
  - EPIC code, instrument name, expiry type (daily/weekly/monthly)
  - Strike prices available
  - Current bid/offer (premium)
  - Implied volatility (if available)
  - Dealing rules (min stake, margin)

Stores everything to options_discovery.json for analysis.

Usage:
    python3 discover_options.py                # full discovery
    python3 discover_options.py --search-only  # just search, no tree walk
    python3 discover_options.py --strikes "US 500"  # get strikes for one market
"""
import os
import sys
import json
import requests
import time
import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.ig.com/gateway/deal"
RATE_LIMIT_PAUSE = 0.35  # seconds between API calls (stay under 40/min)


# ─── Session management (reuse pattern from discover_epics.py) ──────────────

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


# ─── IG API helpers ────────────────────────────────────────────────────────

def search_markets(s, term, max_results=50):
    """Search IG markets for a given term. Returns list of market dicts."""
    s.headers["Version"] = "1"
    r = s.get(f"{BASE}/markets", params={"searchTerm": term})
    s.headers["Version"] = "2"
    if r.status_code != 200:
        print(f"  Search '{term}' failed: {r.status_code}")
        return []
    return r.json().get("markets", [])


def get_market_info(s, epic):
    """Get full market details for a specific EPIC. Returns dict or None."""
    s.headers["Version"] = "3"
    r = s.get(f"{BASE}/markets/{epic}")
    s.headers["Version"] = "2"
    if r.status_code == 200:
        return r.json()
    return None


def browse_navigation(s, node_id=None):
    """
    Browse the IG market navigation tree.
    Returns {"nodes": [...], "markets": [...]} for the given node.
    Top-level if node_id is None.
    """
    s.headers["Version"] = "1"
    if node_id:
        r = s.get(f"{BASE}/marketnavigation/{node_id}")
    else:
        r = s.get(f"{BASE}/marketnavigation")
    s.headers["Version"] = "2"
    if r.status_code == 200:
        return r.json()
    print(f"  Navigation failed (node={node_id}): {r.status_code}")
    return {"nodes": [], "markets": []}


# ─── Options discovery ─────────────────────────────────────────────────────

# Target indices — the ones we want options for
TARGET_INDICES = {
    "US 500":       {"dfb_epic": "IX.D.SPTRD.DAILY.IP",  "yf_ticker": "SPY"},
    "US Tech 100":  {"dfb_epic": "IX.D.NASDAQ.CASH.IP",   "yf_ticker": "QQQ"},
    "Wall Street":  {"dfb_epic": "IX.D.DOW.DAILY.IP",     "yf_ticker": "DIA"},
    "Germany 40":   {"dfb_epic": "IX.D.DAX.DAILY.IP",     "yf_ticker": "EWG"},
    "FTSE 100":     {"dfb_epic": "IX.D.FTSE.DAILY.IP",    "yf_ticker": "EWU"},
}


def find_options_via_search(s):
    """
    Search for options on target indices using market search.
    This is the fast approach — search for option-related terms.
    """
    all_options = {}

    for index_name, info in TARGET_INDICES.items():
        print(f"\n{'='*70}")
        print(f"  Searching options for: {index_name}")
        print(f"{'='*70}")

        options_found = []
        search_terms = [
            f"{index_name} call",
            f"{index_name} put",
            f"{index_name} option",
            f"{index_name} daily",
            f"{index_name} weekly",
            f"{index_name} monthly",
        ]

        seen_epics = set()
        for term in search_terms:
            time.sleep(RATE_LIMIT_PAUSE)
            markets = search_markets(s, term)
            for m in markets:
                epic = m.get("epic", "")
                if epic in seen_epics:
                    continue
                seen_epics.add(epic)

                name = m.get("instrumentName", "")
                mtype = m.get("instrumentType", "")
                expiry = m.get("expiry", "")

                # Filter: only options-related instruments
                is_option = any(x in name.lower() for x in ["call", "put", "option"]) or \
                            any(x in epic.upper() for x in ["OP.", "OPT."])
                # Also catch by instrument type
                if mtype in ("OPT_COMMODITIES", "OPT_RATES", "OPT_SHARES",
                              "BUNGEE_CAPPED", "BUNGEE_COMMODITIES", "BUNGEE_CURRENCIES",
                              "BUNGEE_INDICES"):
                    is_option = True

                if is_option or "option" in mtype.lower():
                    opt_info = {
                        "epic": epic,
                        "name": name,
                        "type": mtype,
                        "expiry": expiry,
                        "index": index_name,
                    }
                    options_found.append(opt_info)
                    print(f"  OPTION: {epic:<45} {name:<50} type={mtype} expiry={expiry}")

                # Also show any non-DFB spread bet markets (forwards, futures)
                is_forward = any(x in epic for x in [".IFS.", ".IFE.", ".IFD.", ".IFA.", ".IFM.", ".FW"])
                if is_forward and not is_option:
                    print(f"  FORWARD: {epic:<44} {name:<50} type={mtype} expiry={expiry}")

        if not options_found:
            print(f"  No options found via search for {index_name}")

        all_options[index_name] = options_found

    return all_options


def find_options_via_navigation(s):
    """
    Browse the IG navigation tree to find the Options node.
    This is slower but more thorough — finds everything IG offers.
    """
    print(f"\n{'='*70}")
    print(f"  BROWSING MARKET NAVIGATION TREE FOR OPTIONS")
    print(f"{'='*70}\n")

    # Step 1: Get top-level nodes
    time.sleep(RATE_LIMIT_PAUSE)
    top = browse_navigation(s)
    nodes = top.get("nodes", [])

    print("Top-level nodes:")
    options_node_id = None
    for n in nodes:
        node_name = n.get("name", "")
        node_id = n.get("id", "")
        print(f"  {node_id:<15} {node_name}")
        if "option" in node_name.lower():
            options_node_id = node_id

    if not options_node_id:
        print("\n  No 'Options' top-level node found. Searching sub-nodes...")
        # Try common node IDs
        for n in nodes:
            time.sleep(RATE_LIMIT_PAUSE)
            sub = browse_navigation(s, n.get("id"))
            for sn in sub.get("nodes", []):
                sn_name = sn.get("name", "")
                sn_id = sn.get("id", "")
                if "option" in sn_name.lower():
                    print(f"  Found options node: {sn_id} — {sn_name}")
                    options_node_id = sn_id
                    break
            if options_node_id:
                break

    if not options_node_id:
        print("  Could not find options node in navigation tree.")
        return {}

    # Step 2: Browse the options node
    print(f"\n  Browsing options node: {options_node_id}")
    time.sleep(RATE_LIMIT_PAUSE)
    opts = browse_navigation(s, options_node_id)

    print(f"\n  Sub-nodes under Options:")
    all_options = {}
    for n in opts.get("nodes", []):
        node_name = n.get("name", "")
        node_id = n.get("id", "")
        print(f"    {node_id:<15} {node_name}")

        # Check if this matches our target indices
        for target in TARGET_INDICES:
            if target.lower() in node_name.lower():
                print(f"    >>> MATCH: {target}")
                # Drill into this node to get strikes
                time.sleep(RATE_LIMIT_PAUSE)
                idx_opts = browse_navigation(s, node_id)

                # Show sub-nodes (expiry types: daily/weekly/monthly)
                for sub_n in idx_opts.get("nodes", []):
                    sub_name = sub_n.get("name", "")
                    sub_id = sub_n.get("id", "")
                    print(f"      {sub_id:<15} {sub_name}")

                    # Drill into expiry type to get strikes
                    time.sleep(RATE_LIMIT_PAUSE)
                    strike_level = browse_navigation(s, sub_id)

                    # Show available strikes/markets
                    for strike_node in strike_level.get("nodes", []):
                        s_name = strike_node.get("name", "")
                        s_id = strike_node.get("id", "")
                        print(f"        {s_id:<15} {s_name}")

                    for mkt in strike_level.get("markets", []):
                        epic = mkt.get("epic", "")
                        mkt_name = mkt.get("instrumentName", "")
                        print(f"        MKT: {epic:<40} {mkt_name}")

                        if target not in all_options:
                            all_options[target] = []
                        all_options[target].append({
                            "epic": epic,
                            "name": mkt_name,
                            "type": mkt.get("instrumentType", ""),
                            "expiry": mkt.get("expiry", ""),
                            "index": target,
                        })

                # Also check direct markets at this level
                for mkt in idx_opts.get("markets", []):
                    epic = mkt.get("epic", "")
                    mkt_name = mkt.get("instrumentName", "")
                    print(f"      MKT: {epic:<40} {mkt_name}")

    return all_options


def get_option_details(s, epics, max_detail=20):
    """
    Get full details for a list of option EPICs.
    Returns bid/offer, margin, dealing rules, implied vol where available.
    """
    details = []
    for i, epic in enumerate(epics[:max_detail]):
        time.sleep(RATE_LIMIT_PAUSE)
        info = get_market_info(s, epic)
        if not info:
            print(f"  [{i+1}/{min(len(epics), max_detail)}] {epic} — FAILED")
            continue

        inst = info.get("instrument", {})
        snap = info.get("snapshot", {})
        dealing = info.get("dealingRules", {})

        detail = {
            "epic": epic,
            "name": inst.get("name", ""),
            "type": inst.get("type", ""),
            "expiry": inst.get("expiry", ""),
            "currency": inst.get("currencies", [{}])[0].get("code", "") if inst.get("currencies") else "",
            "status": snap.get("marketStatus", ""),
            "bid": snap.get("bid"),
            "offer": snap.get("offer"),
            "high": snap.get("high"),
            "low": snap.get("low"),
            "net_change": snap.get("netChange"),
            "pct_change": snap.get("percentageChange"),
            "min_deal_size": dealing.get("minDealSize", {}).get("value"),
            "margin_factor": inst.get("marginFactor"),
            "margin_factor_unit": inst.get("marginFactorUnit"),
            # Options-specific fields (if available)
            "lot_size": inst.get("lotSize"),
            "contract_size": inst.get("contractSize"),
            "controlled_risk_allowed": inst.get("controlledRiskAllowed"),
            "streaming_prices": inst.get("streamingPricesAvailable"),
        }
        details.append(detail)

        status = detail["status"]
        bid = detail["bid"] or "?"
        offer = detail["offer"] or "?"
        print(f"  [{i+1}/{min(len(epics), max_detail)}] {epic:<40} "
              f"bid={bid:<8} offer={offer:<8} status={status}")

    return details


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IG Options Discovery Tool")
    parser.add_argument("--search-only", action="store_true",
                        help="Only use market search (faster)")
    parser.add_argument("--nav-only", action="store_true",
                        help="Only use navigation tree (more thorough)")
    parser.add_argument("--strikes", type=str,
                        help="Get detailed strikes for one index (e.g. 'US 500')")
    parser.add_argument("--details", action="store_true",
                        help="Get full details for discovered options (slower)")
    args = parser.parse_args()

    s = login()
    if not s:
        return

    results = {
        "discovery_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "search_results": {},
        "navigation_results": {},
        "option_details": [],
    }

    # ─── Search-based discovery ──────────────────────────────────────
    if not args.nav_only:
        search_results = find_options_via_search(s)
        results["search_results"] = search_results

        total_found = sum(len(v) for v in search_results.values())
        print(f"\n  Search found {total_found} options across {len(search_results)} indices")

    # ─── Navigation tree discovery ───────────────────────────────────
    if not args.search_only:
        nav_results = find_options_via_navigation(s)
        results["navigation_results"] = nav_results

        total_nav = sum(len(v) for v in nav_results.values())
        print(f"\n  Navigation found {total_nav} options across {len(nav_results)} indices")

    # ─── Get detailed info for discovered options ────────────────────
    if args.details or args.strikes:
        # Collect all unique EPICs
        all_epics = set()
        for source in [results["search_results"], results["navigation_results"]]:
            for index_name, opts in source.items():
                if args.strikes and args.strikes.lower() not in index_name.lower():
                    continue
                for opt in opts:
                    all_epics.add(opt["epic"])

        if all_epics:
            print(f"\n{'='*70}")
            print(f"  GETTING DETAILED INFO FOR {len(all_epics)} OPTIONS")
            print(f"{'='*70}\n")
            results["option_details"] = get_option_details(s, list(all_epics))
        else:
            print("\n  No options EPICs to get details for.")

    # ─── Save results ────────────────────────────────────────────────
    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "options_discovery.json")
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_file}")

    # ─── Summary ─────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")

    for source_name, source_data in [("Search", results["search_results"]),
                                      ("Navigation", results["navigation_results"])]:
        if source_data:
            print(f"\n  {source_name} results:")
            for index_name, opts in source_data.items():
                if opts:
                    # Categorise by type
                    calls = [o for o in opts if "call" in o.get("name", "").lower()]
                    puts = [o for o in opts if "put" in o.get("name", "").lower()]
                    other = [o for o in opts if o not in calls and o not in puts]
                    print(f"    {index_name}: {len(opts)} options "
                          f"({len(calls)} calls, {len(puts)} puts, {len(other)} other)")

                    # Show sample epics
                    for o in opts[:3]:
                        print(f"      e.g. {o['epic']:<40} {o['name']}")
                else:
                    print(f"    {index_name}: no options found")

    print(f"\n  Run with --details to get bid/offer/margin for each option")
    print(f"  Run with --strikes 'US 500' to focus on one index")
    print(f"\nDone.")


if __name__ == "__main__":
    main()
