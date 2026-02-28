"""
BS Model vs IG Reality — Calibration Tool

Compares our Black-Scholes option pricing (used in the backtester) against
actual live IG quotes to measure how far off the model is.

For each of our top 6 markets, this script:
  1. Gets the current underlying price from IG
  2. Constructs strikes matching our strategy (1% OTM put, 2.5% OTM put)
  3. Fetches live IG bid/offer for those options
  4. Computes what BS would price them at (using realised vol × VRP multiplier)
  5. Calculates the ratio: IG_actual / BS_predicted
  6. Saves calibration ratios to calibration.json

The backtester can then apply these ratios to adjust its premium estimates.

A ratio of 1.0 = our model is perfectly calibrated.
A ratio of 0.7 = IG prices are 30% cheaper than our model → backtest is too optimistic.
A ratio of 1.3 = IG prices are 30% richer → backtest is too conservative.

IMPORTANT: Run during market hours (US: 14:30-21:00 UK time).

Usage:
    python3 calibrate_bs_vs_ig.py                   # calibrate all markets
    python3 calibrate_bs_vs_ig.py --index "US 500"  # one market
    python3 calibrate_bs_vs_ig.py --verbose          # show all probed strikes
"""
import os
import sys
import json
import math
import requests
import time
import argparse
from datetime import datetime
from urllib.parse import quote
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "https://api.ig.com/gateway/deal"
RATE_LIMIT_PAUSE = 0.4

# ─── Markets we want to calibrate ─────────────────────────────────────────────
# Map from our backtest ticker → IG underlying + option EPIC patterns.
# We probe multiple strikes around 1-5% OTM to find valid ones.

MARKETS = {
    "US 500": {
        "underlying_epic": "IX.D.SPTRD.DAILY.IP",
        "backtest_ticker": "SPY",
        "price_scale": 10.0,          # SPX ~5800 / SPY ~580
        "option_patterns": [
            # Weekly Wednesday puts: OP.D.SPXWED.{strike}P.IP
            ("weekly_wed", "OP.D.SPXWED.{strike}P.IP", 50),
        ],
    },
    "US Tech 100": {
        "underlying_epic": "IX.D.NASDAQ.CASH.IP",
        "backtest_ticker": "QQQ",
        "price_scale": 50.0,
        "option_patterns": [
            ("weekly_mon", "OP.D.NASMON.{strike}P.IP", 250),
        ],
    },
    "Wall Street": {
        "underlying_epic": "IX.D.DOW.DAILY.IP",
        "backtest_ticker": "DIA",
        "price_scale": 100.0,
        "option_patterns": [
            ("weekly", "OP.D.DOWWEEK.{strike}P.IP", 200),
        ],
    },
    "Germany 40": {
        "underlying_epic": "IX.D.DAX.DAILY.IP",
        "backtest_ticker": "EWG",
        "price_scale": 600.0,
        "option_patterns": [
            ("weekly_mon", "OP.D.DAXMON.{strike}P.IP", 100),
        ],
    },
    "Japan 225": {
        "underlying_epic": "IX.D.NIKFUT.DAILY.IP",  # Nikkei futures
        "backtest_ticker": "EWJ",
        "price_scale": 400.0,
        "option_patterns": [
            # Guess patterns — may need adjustment after first run
            ("weekly", "OP.D.NIKWEEK.{strike}P.IP", 500),
            ("weekly_mon", "OP.D.NIKMON.{strike}P.IP", 500),
            ("monthly", "OP.D.NIK225.{strike}P.IP", 500),
        ],
    },
    "Gold": {
        "underlying_epic": "CS.D.USCGC.TODAY.IP",  # Gold spot
        "backtest_ticker": "GLD",
        "price_scale": 14.0,
        "option_patterns": [
            ("weekly", "OP.D.GLDWEEK.{strike}P.IP", 50),
            ("weekly_mon", "OP.D.GLDMON.{strike}P.IP", 50),
            ("monthly", "OP.D.GOLD.{strike}P.IP", 50),
        ],
    },
    "FTSE 100": {
        "underlying_epic": "IX.D.FTSE.DAILY.IP",
        "backtest_ticker": "EWU",
        "price_scale": 230.0,
        "option_patterns": [
            ("weekly", "OP.D.FTSWEEK.{strike}P.IP", 50),
        ],
    },
}


# ─── Black-Scholes (inline — same as backtester) ──────────────────────────────

def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _bs_put_price(S, K, T, r, sigma):
    """BS European put price."""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

def _parse_dte_from_expiry(expiry_str):
    """
    Parse IG expiry string to days-to-expiry.
    IG formats: "04-MAR-26", "28-FEB-26", "DFB", "-" etc.
    Returns int DTE or None if unparseable.
    """
    if not expiry_str or expiry_str in ("DFB", "-", ""):
        return None
    try:
        # Try "DD-MMM-YY" format (e.g. "04-MAR-26")
        expiry_date = datetime.strptime(expiry_str, "%d-%b-%y")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        delta = (expiry_date - today).days
        return max(delta, 0)
    except ValueError:
        pass
    try:
        # Try "DD-MMM-YYYY" format
        expiry_date = datetime.strptime(expiry_str, "%d-%b-%Y")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        delta = (expiry_date - today).days
        return max(delta, 0)
    except ValueError:
        return None


def _realised_vol_from_yfinance(ticker, window=30):
    """Get recent realised vol from yfinance (if available)."""
    try:
        import yfinance as yf
        import numpy as np
        df = yf.download(ticker, period="3mo", progress=False)
        if df is None or len(df) < window + 1:
            return None
        closes = df["Close"].values
        log_rets = [math.log(closes[i] / closes[i-1])
                    for i in range(-window, 0) if closes[i-1] > 0]
        if not log_rets:
            return None
        return float(np.std(log_rets)) * math.sqrt(252)
    except Exception:
        return None


# ─── IG API helpers ────────────────────────────────────────────────────────────

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
    """Get market details. Returns dict or None."""
    encoded = quote(epic, safe="")
    s.headers["Version"] = version
    r = s.get(f"{BASE}/markets/{encoded}")
    s.headers["Version"] = "2"
    if r.status_code == 200:
        return r.json()
    return None


def get_market_info_multi_version(s, epic):
    """Try API versions 3, 2, 1."""
    for v in ["3", "2", "1"]:
        info = get_market_info(s, epic, version=v)
        if info:
            return info, v
    return None, None


def get_underlying_price(s, epic):
    """Get current mid-price for an underlying."""
    info = get_market_info(s, epic, version="3")
    if not info:
        return None
    snap = info.get("snapshot", {})
    bid = snap.get("bid", 0)
    offer = snap.get("offer", 0)
    if bid and offer:
        return (bid + offer) / 2
    return None


# ─── Calibration logic ────────────────────────────────────────────────────────

def calibrate_market(s, index_name, config, verbose=False):
    """
    Calibrate one market: compare IG live quotes to BS predictions.

    Returns dict with calibration data or None if market unavailable.
    """
    # 1. Get underlying price from IG
    underlying = get_underlying_price(s, config["underlying_epic"])
    if not underlying:
        print(f"  {index_name}: could not get underlying price (market closed?)")
        return None

    print(f"\n  {index_name}: underlying = {underlying:.0f}")

    # 2. Get realised vol from yfinance
    bt_ticker = config["backtest_ticker"]
    rv = _realised_vol_from_yfinance(bt_ticker)
    if rv is None:
        rv = 0.18  # Fallback
        print(f"    RV: using fallback 18% (yfinance unavailable)")
    else:
        print(f"    RV ({bt_ticker} 30d): {rv:.1%}")

    # IV estimate = RV × VRP multiplier (same as backtester)
    VRP_MULT = 1.30
    iv_est = rv * VRP_MULT
    print(f"    IV estimate (RV × {VRP_MULT}): {iv_est:.1%}")

    # 3. Generate target strikes: 0.5% to 5% OTM puts
    target_otm_pcts = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    results = []
    seen_epics = set()  # Deduplicate

    for expiry_type, pattern, interval in config["option_patterns"]:
        print(f"\n    Probing {expiry_type} puts...")

        # We'll extract the ACTUAL DTE from the first IG response
        actual_dte = None

        for otm_pct in target_otm_pcts:
            target_strike = underlying * (1 - otm_pct / 100)
            # Round to nearest interval
            strike = round(target_strike / interval) * interval
            actual_otm = (underlying - strike) / underlying * 100

            epic = pattern.format(strike=int(strike))

            # Skip duplicates (multiple OTM% can round to same strike)
            if epic in seen_epics:
                continue
            seen_epics.add(epic)

            time.sleep(RATE_LIMIT_PAUSE)

            info, ver = get_market_info_multi_version(s, epic)
            if not info:
                if verbose:
                    print(f"      {strike:>8.0f} ({actual_otm:>4.1f}% OTM): not found")
                continue

            snap = info.get("snapshot", {})
            inst = info.get("instrument", {})
            bid = snap.get("bid")
            offer = snap.get("offer")

            if bid is None or offer is None or bid <= 0:
                if verbose:
                    print(f"      {strike:>8.0f} ({actual_otm:>4.1f}% OTM): no price (market closed?)")
                continue

            ig_mid = (bid + offer) / 2
            ig_spread = offer - bid

            # Extract ACTUAL expiry from IG API response
            # IG returns expiry as "DFB", "04-MAR-26", "28-FEB-26" etc.
            expiry_str = inst.get("expiry", "")
            dte = _parse_dte_from_expiry(expiry_str)
            if dte is None:
                # Fallback to estimate
                dte_map = {
                    "weekly_wed": 5, "weekly_mon": 5, "weekly": 7,
                    "monthly": 25, "eom_near": 15,
                }
                dte = dte_map.get(expiry_type, 10)
                dte_source = "estimated"
            else:
                dte_source = "from IG"

            if actual_dte is None:
                actual_dte = dte
                print(f"    Expiry: {expiry_str} → DTE={dte} ({dte_source})")

            T = max(dte, 0.5) / 365.0  # Floor at 0.5 days to avoid BS edge cases

            # 4. BS prediction for the same option
            bs_price = _bs_put_price(underlying, strike, T, 0.05, iv_est)

            # Ratio
            ratio = ig_mid / bs_price if bs_price > 0.01 else None

            # Flag if IG spread is too wide to trade
            spread_pct = ig_spread / ig_mid * 100 if ig_mid > 0 else 999
            tradeable = spread_pct < 20  # Spread < 20% of premium

            result = {
                "index": index_name,
                "ticker": bt_ticker,
                "strike": strike,
                "otm_pct": round(actual_otm, 2),
                "expiry_type": expiry_type,
                "dte": dte,
                "dte_source": dte_source,
                "expiry_raw": expiry_str,
                "ig_bid": bid,
                "ig_offer": offer,
                "ig_mid": round(ig_mid, 2),
                "ig_spread": round(ig_spread, 2),
                "ig_spread_pct": round(spread_pct, 1),
                "tradeable": tradeable,
                "bs_price": round(bs_price, 4),
                "ratio_ig_vs_bs": round(ratio, 3) if ratio else None,
                "rv": round(rv, 4),
                "iv_est": round(iv_est, 4),
                "underlying": round(underlying, 1),
                "epic": epic,
            }
            results.append(result)

            ratio_str = f"{ratio:.2f}x" if ratio else "n/a"
            trade_flag = "" if tradeable else " ⚠WIDE"
            print(f"      {strike:>8.0f} ({actual_otm:>4.1f}% OTM): "
                  f"IG mid={ig_mid:>7.1f}  BS={bs_price:>7.1f}  "
                  f"ratio={ratio_str}  "
                  f"IG spread={ig_spread:.1f} ({spread_pct:.0f}%){trade_flag}")

    return results


def summarise_calibration(all_results):
    """Print calibration summary and compute per-market ratios."""
    print(f"\n\n{'='*80}")
    print("  CALIBRATION SUMMARY: IG Reality vs BS Model")
    print(f"{'='*80}")
    print()
    print("  ratio > 1.0 = IG prices RICHER than BS → backtest is conservative (good)")
    print("  ratio < 1.0 = IG prices CHEAPER than BS → backtest is OPTIMISTIC (bad)")
    print("  ratio = 1.0 = model perfectly calibrated")
    print()

    by_market = defaultdict(list)
    for r in all_results:
        by_market[r["index"]].append(r)

    calibration = {}

    # Focus on strategy-relevant strikes: 0.5-2.0% OTM (where our strategy trades)
    print("  NOTE: Only showing 0.5-2.0% OTM strikes (where our strategy trades).")
    print("  Far-OTM skew is ignored — it inflates ratios but we never trade there.")
    print()

    print(f"  {'Market':<16} {'Ticker':>6} {'Samp':>5} {'Ratio':>7} {'DTE':>4} "
          f"{'IG Sprd':>8} {'Sprd%':>6} {'RV':>6} {'IV':>6} {'Verdict':>14}")
    print("  " + "-" * 90)

    for market in sorted(by_market.keys()):
        quotes = by_market[market]
        ticker = quotes[0]["ticker"]
        rv = quotes[0]["rv"]
        iv_est = quotes[0]["iv_est"]

        # Filter to strategy-relevant strikes ONLY (0.5-2.0% OTM)
        strategy_quotes = [q for q in quotes
                           if q["ratio_ig_vs_bs"] is not None
                           and 0.3 <= q["otm_pct"] <= 2.5]

        if not strategy_quotes:
            # Fall back to all available
            strategy_quotes = [q for q in quotes if q["ratio_ig_vs_bs"] is not None]

        if not strategy_quotes:
            print(f"  {market:<16} {ticker:>6} {'no data':>8}")
            continue

        ratios = [q["ratio_ig_vs_bs"] for q in strategy_quotes]
        ig_spreads = [q["ig_spread"] for q in strategy_quotes]
        spread_pcts = [q.get("ig_spread_pct", 0) for q in strategy_quotes]
        dtes = [q.get("dte", 0) for q in strategy_quotes]

        avg_ratio = sum(ratios) / len(ratios)
        avg_spread = sum(ig_spreads) / len(ig_spreads) if ig_spreads else 0
        avg_spread_pct = sum(spread_pcts) / len(spread_pcts) if spread_pcts else 0
        avg_dte = sum(dtes) / len(dtes) if dtes else 0

        # Verdict based on strategy-relevant ratio
        if avg_spread_pct > 20:
            verdict = "TOO WIDE"
        elif avg_ratio >= 1.05:
            verdict = "CONSERVATIVE"
        elif avg_ratio >= 0.85:
            verdict = "WELL CALIBRATED"
        elif avg_ratio >= 0.65:
            verdict = "OPTIMISTIC"
        else:
            verdict = "V.OPTIMISTIC"

        calibration[ticker] = {
            "market": market,
            "strategy_ratio": round(avg_ratio, 3),
            "avg_ig_spread": round(avg_spread, 2),
            "avg_ig_spread_pct": round(avg_spread_pct, 1),
            "avg_dte": round(avg_dte, 1),
            "rv": rv,
            "iv_est": round(iv_est, 4),
            "samples": len(ratios),
            "tradeable": avg_spread_pct < 20,
        }

        print(f"  {market:<16} {ticker:>6} {len(ratios):>5} {avg_ratio:>7.2f} "
              f"{avg_dte:>4.0f} "
              f"{avg_spread:>8.1f} {avg_spread_pct:>5.0f}% "
              f"{rv:>5.1%} {iv_est:>5.1%} "
              f"{verdict:>14}")

    # Overall — only from tradeable markets with strategy-relevant strikes
    tradeable_ratios = []
    for market_quotes in by_market.values():
        sq = [q for q in market_quotes
              if q["ratio_ig_vs_bs"] is not None
              and 0.3 <= q["otm_pct"] <= 2.5
              and q.get("ig_spread_pct", 100) < 20]
        tradeable_ratios.extend([q["ratio_ig_vs_bs"] for q in sq])

    if tradeable_ratios:
        overall = sum(tradeable_ratios) / len(tradeable_ratios)
        print("  " + "-" * 90)
        print(f"  {'TRADEABLE AVG':<16} {'':>6} {len(tradeable_ratios):>5} {overall:>7.2f}")
        calibration["_overall"] = round(overall, 3)
    else:
        calibration["_overall"] = 1.0

    # Backtest adjustment advice
    print(f"\n  {'='*80}")
    print("  WHAT THIS MEANS FOR THE BACKTEST")
    print(f"  {'='*80}")
    print()

    overall = calibration.get("_overall", 1.0)
    if tradeable_ratios:
        if overall < 0.9:
            haircut = (1 - overall) * 100
            print(f"  IG prices are {haircut:.0f}% CHEAPER than our BS model predicts.")
            print(f"  The backtest is overestimating premiums collected.")
            print(f"  Adjusted annual return ≈ backtested return × {overall:.2f}")
            print(f"  E.g. if backtest shows 13.9% pa → realistic ≈ {13.9 * overall:.1f}% pa")
        elif overall > 1.1:
            bonus = (overall - 1) * 100
            print(f"  IG prices are {bonus:.0f}% RICHER than our BS model predicts.")
            print(f"  The backtest is actually UNDERESTIMATING real premiums.")
            print(f"  Real returns may be better than backtested.")
        else:
            print(f"  Model is reasonably well calibrated (ratio {overall:.2f}).")
            print(f"  Backtested returns are approximately realistic.")

    # Per-market advice
    print()
    for ticker, cal in calibration.items():
        if ticker.startswith("_"):
            continue
        if not cal.get("tradeable", True):
            print(f"  ⚠ {cal['market']} ({ticker}): IG spreads too wide ({cal['avg_ig_spread_pct']:.0f}% of premium). "
                  f"DROP from portfolio.")
        elif cal.get("strategy_ratio", 1.0) < 0.7:
            print(f"  ⚠ {cal['market']} ({ticker}): IG premiums {(1-cal['strategy_ratio'])*100:.0f}% below BS model. "
                  f"Backtest returns are OVERSTATED.")
        elif cal.get("strategy_ratio", 1.0) > 1.2:
            print(f"  ✓ {cal['market']} ({ticker}): IG premiums {(cal['strategy_ratio']-1)*100:.0f}% above BS model. "
                  f"Real returns likely BETTER than backtest.")
        else:
            print(f"  ~ {cal['market']} ({ticker}): Reasonably calibrated "
                  f"(ratio {cal['strategy_ratio']:.2f})")

    return calibration


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate BS model against live IG option quotes")
    parser.add_argument("--index", type=str,
                        help="Only calibrate one market (e.g. 'US 500')")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show all probed strikes including failures")
    args = parser.parse_args()

    print("=" * 80)
    print("  BS vs IG CALIBRATION")
    print("  Comparing Black-Scholes model predictions to live IG option quotes")
    print("=" * 80)
    print()
    print("  Run this during market hours for live prices.")
    print("  US markets: 14:30-21:00 UK time | EU: 08:00-16:30 UK time")
    print()

    s = login()
    if not s:
        return

    all_results = []
    markets_to_check = MARKETS

    if args.index:
        markets_to_check = {k: v for k, v in MARKETS.items()
                            if args.index.lower() in k.lower()}
        if not markets_to_check:
            print(f"No market matching '{args.index}'")
            return

    for index_name, config in markets_to_check.items():
        results = calibrate_market(s, index_name, config, verbose=args.verbose)
        if results:
            all_results.extend(results)

    if not all_results:
        print("\nNo calibration data collected. Are markets open?")
        return

    # Summarise and save
    calibration = summarise_calibration(all_results)

    # Save to file
    output = {
        "calibration_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_samples": len(all_results),
        "per_market": calibration,
        "raw_quotes": all_results,
    }

    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "calibration.json")
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n  Calibration saved to: {output_file}")
    print(f"  The backtester can load this to adjust premium estimates.")
    print()
    print("  Next steps:")
    print("  1. Review the ratios — are they consistent across strikes?")
    print("  2. Run the backtest with --calibration flag (coming soon)")
    print("  3. Paper trade for 1-2 months to validate over time")


if __name__ == "__main__":
    main()
