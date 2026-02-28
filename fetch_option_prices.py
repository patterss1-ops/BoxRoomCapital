"""
Options Price Fetcher — Get live bid/offer/spread data for IG options.

The discover_options.py detail calls failed because:
  1. Market was likely closed (options only tradeable during market hours)
  2. Need to try multiple API versions (1, 2, 3)
  3. Some EPIC formats for options need URL encoding

This script:
  - Loads discovered options from options_discovery.json
  - Fetches bid/offer for each option across all API versions
  - Calculates the actual IG spread cost per option
  - Computes implied volatility from market mid-price
  - Saves results for backtester calibration

Run during US/EU market hours for live prices.

Usage:
    python3 fetch_option_prices.py                    # fetch all
    python3 fetch_option_prices.py --index "US 500"   # one index
    python3 fetch_option_prices.py --daily-only        # just daily options
    python3 fetch_option_prices.py --weekly-only       # just weekly options
"""
import os
import sys
import json
import requests
import time
import argparse
from datetime import datetime
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.ig.com/gateway/deal"
RATE_LIMIT_PAUSE = 0.4  # seconds between API calls


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


def get_market_info(s, epic, version="3"):
    """
    Get market details for a specific EPIC.
    Try multiple API versions if needed — options may only work on certain versions.
    Returns dict or None.
    """
    # URL-encode the epic (some options EPICs have special chars)
    encoded_epic = quote(epic, safe="")

    s.headers["Version"] = version
    r = s.get(f"{BASE}/markets/{encoded_epic}")
    s.headers["Version"] = "2"

    if r.status_code == 200:
        return r.json()
    return None


def get_market_info_multi_version(s, epic):
    """Try API versions 3, 2, 1 to get market info."""
    for v in ["3", "2", "1"]:
        info = get_market_info(s, epic, version=v)
        if info:
            return info, v
    return None, None


def classify_option_epic(epic, name=""):
    """
    Classify an option EPIC into type (daily/weekly/monthly/eom) and
    extract strike and direction (call/put) where possible.
    """
    epic_upper = epic.upper()
    name_lower = name.lower()

    # Daily options: DO.D.D{INDEX}.{NUM}.IP
    if epic_upper.startswith("DO.D.D"):
        return {"expiry_type": "daily", "is_daily": True}

    # Weekly Monday: OP.D.{INDEX}MON.{STRIKE}C/P.IP
    if "MON." in epic_upper:
        return {"expiry_type": "weekly_mon", "is_weekly": True}

    # Weekly Wednesday: OP.D.{INDEX}WED.{STRIKE}C/P.IP
    if "WED." in epic_upper:
        return {"expiry_type": "weekly_wed", "is_weekly": True}

    # Weekly generic: OP.D.{INDEX}WEEK.{STRIKE}C/P.IP
    if "WEEK." in epic_upper:
        return {"expiry_type": "weekly", "is_weekly": True}

    # End of Month near: OP.D.{INDEX}EMO.{STRIKE}C/P.IP
    if "EMO." in epic_upper:
        return {"expiry_type": "eom_near", "is_monthly": True}

    # End of Month far: OP.D.{INDEX}EOM.{STRIKE}C/P.IP
    if "EOM." in epic_upper:
        return {"expiry_type": "eom_far", "is_monthly": True}

    # Monthly numbered: OP.D.{INDEX}{NUM}.{STRIKE}C/P.IP
    # Detect by presence of a digit before the strike
    if epic_upper.startswith("OP.D."):
        return {"expiry_type": "monthly", "is_monthly": True}

    return {"expiry_type": "unknown"}


def extract_option_info(epic, name=""):
    """Extract call/put and strike from EPIC or name."""
    info = classify_option_epic(epic, name)

    # Direction from EPIC
    if epic.upper().endswith("C.IP"):
        info["direction"] = "call"
    elif epic.upper().endswith("P.IP"):
        info["direction"] = "put"
    else:
        # Try from name
        name_lower = name.lower()
        if "call" in name_lower:
            info["direction"] = "call"
        elif "put" in name_lower:
            info["direction"] = "put"
        else:
            info["direction"] = "unknown"

    # Strike from EPIC — pattern: .{STRIKE}C.IP or .{STRIKE}P.IP
    parts = epic.split(".")
    for p in parts:
        # Look for numeric-ish part that could be a strike
        cleaned = p.rstrip("CcPp")
        if cleaned.isdigit() and len(cleaned) >= 3:
            info["strike"] = int(cleaned)
            break

    return info


def fetch_prices(s, options_by_index, filters=None, max_per_index=30):
    """
    Fetch bid/offer for discovered options.

    Args:
        options_by_index: dict from options_discovery.json search_results
        filters: dict with optional keys: index, expiry_type (daily/weekly/monthly)
        max_per_index: max options to fetch per index (rate limit safety)

    Returns:
        list of dicts with price data
    """
    results = []
    total_fetched = 0
    total_failed = 0

    for index_name, options in options_by_index.items():
        if filters and filters.get("index"):
            if filters["index"].lower() not in index_name.lower():
                continue

        print(f"\n{'='*70}")
        print(f"  Fetching prices for: {index_name} ({len(options)} options)")
        print(f"{'='*70}")

        fetched_this_index = 0

        for opt in options:
            if fetched_this_index >= max_per_index:
                print(f"  (hit limit of {max_per_index} per index)")
                break

            epic = opt.get("epic", "")
            name = opt.get("name", "")

            # Apply expiry type filter
            opt_info = extract_option_info(epic, name)
            if filters:
                if filters.get("daily_only") and not opt_info.get("is_daily"):
                    continue
                if filters.get("weekly_only") and not opt_info.get("is_weekly"):
                    continue
                if filters.get("monthly_only") and not opt_info.get("is_monthly"):
                    continue

            time.sleep(RATE_LIMIT_PAUSE)
            info, version = get_market_info_multi_version(s, epic)

            if info:
                inst = info.get("instrument", {})
                snap = info.get("snapshot", {})
                dealing = info.get("dealingRules", {})

                bid = snap.get("bid")
                offer = snap.get("offer")
                spread = None
                mid = None
                if bid is not None and offer is not None:
                    spread = round(offer - bid, 2)
                    mid = round((bid + offer) / 2, 2)

                result = {
                    "epic": epic,
                    "name": name,
                    "index": index_name,
                    "bid": bid,
                    "offer": offer,
                    "spread": spread,
                    "mid": mid,
                    "status": snap.get("marketStatus", ""),
                    "high": snap.get("high"),
                    "low": snap.get("low"),
                    "net_change": snap.get("netChange"),
                    "pct_change": snap.get("percentageChange"),
                    "min_deal_size": (dealing.get("minDealSize") or {}).get("value"),
                    "margin_factor": inst.get("marginFactor"),
                    "margin_unit": inst.get("marginFactorUnit"),
                    "lot_size": inst.get("lotSize"),
                    "contract_size": inst.get("contractSize"),
                    "expiry": inst.get("expiry"),
                    "currency": (inst.get("currencies", [{}])[0].get("code", "")
                                 if inst.get("currencies") else ""),
                    "api_version": version,
                    **opt_info,
                }
                results.append(result)
                fetched_this_index += 1
                total_fetched += 1

                dir_str = opt_info.get("direction", "?")
                exp_str = opt_info.get("expiry_type", "?")
                strike_str = opt_info.get("strike", "?")
                print(f"  OK  {epic:<45} bid={bid:<8} offer={offer:<8} "
                      f"spread={spread:<6} {dir_str} K={strike_str} [{exp_str}]")
            else:
                total_failed += 1
                if fetched_this_index < 5:  # Only show first few failures
                    print(f"  FAIL {epic:<44} {name[:40]}")

    print(f"\n  Total: {total_fetched} fetched, {total_failed} failed")
    return results


def compute_spread_costs(results, underlying_prices=None):
    """
    Calculate actual spread cost as % of underlying for each option.
    This is the REAL cost of trading options on IG.

    Also compute implied vol if we have underlying price.
    """
    # Default underlying prices (approximate, from config)
    if underlying_prices is None:
        underlying_prices = {
            "US 500": 5900,
            "US Tech 100": 21500,
            "Wall Street": 44000,
            "Germany 40": 22500,
            "FTSE 100": 8700,
        }

    for r in results:
        index = r.get("index", "")
        underlying = underlying_prices.get(index)
        if underlying and r.get("spread") is not None:
            # Spread cost as % of option mid-price
            if r["mid"] and r["mid"] > 0:
                r["spread_pct_of_premium"] = round(r["spread"] / r["mid"] * 100, 1)
            else:
                r["spread_pct_of_premium"] = None

            # Spread cost as % of underlying (like a DFB spread)
            r["spread_pct_of_underlying"] = round(r["spread"] / underlying * 100, 4)

        # Try to compute implied vol from mid-price
        # (Needs scipy — only if available)
        try:
            from analytics.options_pricing import BlackScholes
            if (r.get("mid") and r["mid"] > 0 and
                r.get("strike") and underlying and
                r.get("direction") in ("call", "put")):

                # Estimate days to expiry from expiry type
                dte_map = {
                    "daily": 1,
                    "weekly_mon": 5,
                    "weekly_wed": 3,
                    "weekly": 5,
                    "eom_near": 15,
                    "eom_far": 30,
                    "monthly": 25,
                }
                dte = dte_map.get(r.get("expiry_type"), 20)
                T = dte / 365.0

                iv = BlackScholes.implied_vol(
                    S=underlying, K=r["strike"], T=T, r=0.05,
                    market_price=r["mid"], option_type=r["direction"]
                )
                if iv:
                    r["implied_vol"] = round(iv * 100, 1)  # As percentage
        except ImportError:
            pass

    return results


def summarise_results(results):
    """Print a summary of spread costs by index and expiry type."""
    print(f"\n{'='*70}")
    print("SPREAD COST ANALYSIS")
    print(f"{'='*70}\n")

    # Group by index and expiry type
    from collections import defaultdict
    by_index = defaultdict(lambda: defaultdict(list))

    for r in results:
        idx = r.get("index", "unknown")
        exp = r.get("expiry_type", "unknown")
        if r.get("spread") is not None:
            by_index[idx][exp].append(r)

    for idx in sorted(by_index.keys()):
        print(f"\n  {idx}:")
        for exp in sorted(by_index[idx].keys()):
            opts = by_index[idx][exp]
            spreads = [o["spread"] for o in opts if o.get("spread") is not None]
            mids = [o["mid"] for o in opts if o.get("mid") and o["mid"] > 0]
            ivs = [o["implied_vol"] for o in opts if o.get("implied_vol")]

            if spreads:
                avg_spread = sum(spreads) / len(spreads)
                min_spread = min(spreads)
                max_spread = max(spreads)
                avg_mid = sum(mids) / len(mids) if mids else 0
                avg_iv = sum(ivs) / len(ivs) if ivs else 0
                spread_pcts = [o.get("spread_pct_of_premium") for o in opts
                               if o.get("spread_pct_of_premium") is not None]
                avg_spread_pct = sum(spread_pcts) / len(spread_pcts) if spread_pcts else 0

                print(f"    {exp:<15} n={len(opts):<4} "
                      f"spread: avg={avg_spread:.1f} min={min_spread:.1f} max={max_spread:.1f}  "
                      f"avg_mid={avg_mid:.1f}  spread_%%_of_prem={avg_spread_pct:.1f}%%  "
                      f"avg_IV={avg_iv:.0f}%%")

    # Key metric for strategy design: cost of a typical credit spread
    print(f"\n{'='*70}")
    print("CREDIT SPREAD COST ESTIMATE")
    print(f"{'='*70}\n")
    print("  For a credit spread, you pay the spread on BOTH legs (open + close).")
    print("  Total round-trip cost = 2 x option_spread per leg = 4 x option_spread.")
    print("  (But if held to expiry and both expire OTM, you only pay 2 x spread.)\n")

    for idx in sorted(by_index.keys()):
        weeklies = by_index[idx].get("weekly_mon", []) + by_index[idx].get("weekly_wed", []) + by_index[idx].get("weekly", [])
        dailies = by_index[idx].get("daily", [])
        monthlies = by_index[idx].get("monthly", []) + by_index[idx].get("eom_near", []) + by_index[idx].get("eom_far", [])

        for label, opts in [("Daily", dailies), ("Weekly", weeklies), ("Monthly", monthlies)]:
            if opts:
                spreads = [o["spread"] for o in opts if o.get("spread") is not None]
                if spreads:
                    avg = sum(spreads) / len(spreads)
                    held_to_expiry_cost = avg * 2  # Open both legs
                    print(f"  {idx} {label}: avg_option_spread={avg:.1f}  "
                          f"credit_spread_open_cost={held_to_expiry_cost:.1f} points")


def build_strike_epics(s):
    """
    Build EPIC codes for strikes around the current market price.

    The discovery JSON only has round strikes from search (5400, 5500 etc).
    For a real spread strategy we need strikes 1-5% OTM from current price.

    EPIC patterns (from discover_options.py output):
      Weekly Wed SPX:  OP.D.SPXWED.{STRIKE}C.IP / OP.D.SPXWED.{STRIKE}P.IP
      Weekly Mon NAS:  OP.D.NASMON.{STRIKE}C.IP / OP.D.NASMON.{STRIKE}P.IP
      Weekly Dow:      OP.D.DOWWEEK.{STRIKE}C.IP / OP.D.DOWWEEK.{STRIKE}P.IP
      Weekly DAX:      OP.D.DAXMON.{STRIKE}C.IP / OP.D.DAXMON.{STRIKE}P.IP
      Weekly FTSE:     OP.D.FTSWEEK.{STRIKE}C.IP / OP.D.FTSWEEK.{STRIKE}P.IP
      Daily SPX:       DO.D.DSPX.{NUM}.IP (different pattern, numbered)
    """
    # Get current underlying prices
    underlyings = {
        "US 500":     {"epic": "IX.D.SPTRD.DAILY.IP",  "patterns": [
            ("weekly_wed", "OP.D.SPXWED.{strike}{dir}.IP", 50),   # 50-pt strike intervals
        ]},
        "US Tech 100": {"epic": "IX.D.NASDAQ.CASH.IP", "patterns": [
            ("weekly_mon", "OP.D.NASMON.{strike}{dir}.IP", 250),  # 250-pt intervals
        ]},
        "Wall Street": {"epic": "IX.D.DOW.DAILY.IP",   "patterns": [
            ("weekly", "OP.D.DOWWEEK.{strike}{dir}.IP", 200),    # 200-pt intervals
        ]},
        "Germany 40":  {"epic": "IX.D.DAX.DAILY.IP",   "patterns": [
            ("weekly_mon", "OP.D.DAXMON.{strike}{dir}.IP", 100), # 100-pt intervals
        ]},
        "FTSE 100":    {"epic": "IX.D.FTSE.DAILY.IP",  "patterns": [
            ("weekly", "OP.D.FTSWEEK.{strike}{dir}.IP", 50),     # 50-pt intervals
        ]},
    }

    all_epics = {}

    for index_name, cfg in underlyings.items():
        # Get current price
        info = get_market_info(s, cfg["epic"], version="3")
        if not info:
            print(f"  Could not get underlying price for {index_name}")
            continue

        snap = info.get("snapshot", {})
        bid = snap.get("bid", 0)
        offer = snap.get("offer", 0)
        current = (bid + offer) / 2 if bid and offer else 0
        if current <= 0:
            continue

        print(f"  {index_name}: current price = {current:.0f}")

        epics_for_index = []

        for expiry_type, pattern, interval in cfg["patterns"]:
            # Generate strikes from -5% to +5% around current price
            # Round to nearest interval
            base = round(current / interval) * interval

            for offset_pct in range(-5, 6):  # -5% to +5%
                strike = base + offset_pct * interval
                if strike <= 0:
                    continue

                for direction in ["C", "P"]:
                    epic = pattern.format(strike=int(strike), dir=direction)
                    epics_for_index.append({
                        "epic": epic,
                        "name": f"{index_name} {expiry_type} {strike} {'Call' if direction == 'C' else 'Put'}",
                        "constructed": True,
                    })

        all_epics[index_name] = epics_for_index
        print(f"  Generated {len(epics_for_index)} EPIC codes to probe")

    return all_epics


def main():
    parser = argparse.ArgumentParser(description="IG Options Price Fetcher")
    parser.add_argument("--index", type=str, help="Only fetch for one index (e.g. 'US 500')")
    parser.add_argument("--daily-only", action="store_true", help="Only daily options")
    parser.add_argument("--weekly-only", action="store_true", help="Only weekly options")
    parser.add_argument("--monthly-only", action="store_true", help="Only monthly options")
    parser.add_argument("--max-per-index", type=int, default=30,
                        help="Max options to fetch per index (default 30)")
    parser.add_argument("--probe", action="store_true",
                        help="Build EPIC codes around current price and probe them (best mode)")
    args = parser.parse_args()

    # Login
    s = login()
    if not s:
        return

    if args.probe:
        # PROBE MODE: build EPIC codes around current prices and fetch them
        print("PROBE MODE: constructing EPICs around current market prices...\n")
        probe_epics = build_strike_epics(s)

        if not probe_epics:
            print("Could not build probe EPICs (markets may be closed)")
            return

        # Filter by index if specified
        if args.index:
            probe_epics = {k: v for k, v in probe_epics.items()
                           if args.index.lower() in k.lower()}

        results = fetch_prices(s, probe_epics, filters=None,
                               max_per_index=args.max_per_index)
    else:
        # DISCOVERY MODE: use previously discovered EPICs
        disc_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "options_discovery.json")
        if not os.path.exists(disc_file):
            print("No options_discovery.json found. Run discover_options.py first,")
            print("or use --probe mode to construct EPICs automatically.")
            return

        with open(disc_file) as f:
            discovery = json.load(f)

        search_results = discovery.get("search_results", {})
        if not search_results:
            print("No search results in options_discovery.json")
            return

        total_opts = sum(len(v) for v in search_results.values())
        print(f"Loaded {total_opts} discovered options across {len(search_results)} indices\n")

        # Build filters
        filters = {}
        if args.index:
            filters["index"] = args.index
        if args.daily_only:
            filters["daily_only"] = True
        if args.weekly_only:
            filters["weekly_only"] = True
        if args.monthly_only:
            filters["monthly_only"] = True

        results = fetch_prices(s, search_results, filters=filters,
                               max_per_index=args.max_per_index)

    if not results:
        print("\nNo prices fetched. Are markets open?")
        print("Options only trade during market hours:")
        print("  US indices: 14:30 - 21:00 UK time (Mon-Fri)")
        print("  European: 08:00 - 16:30 UK time (Mon-Fri)")
        return

    # Compute spread costs and IV
    results = compute_spread_costs(results)

    # Save results
    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "option_prices.json")
    with open(output_file, "w") as f:
        json.dump({
            "fetch_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_fetched": len(results),
            "prices": results,
        }, f, indent=2, default=str)
    print(f"\n  Results saved to: {output_file}")

    # Print summary
    summarise_results(results)


if __name__ == "__main__":
    main()
