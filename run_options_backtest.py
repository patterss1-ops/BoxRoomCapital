"""
Run options credit spread backtest on real market data.

Quick script to test the IBS Credit Spreads strategy using yfinance data.
Run this on your Mac (not in sandbox — yfinance needs internet).

Usage:
    python3 run_options_backtest.py                              # default tickers
    python3 run_options_backtest.py --tickers SPY EWG            # specific tickers
    python3 run_options_backtest.py --zero-cost                  # no IG costs
    python3 run_options_backtest.py --sweep                      # parameter sweep
    python3 run_options_backtest.py --portfolio                  # full portfolio mode
    python3 run_options_backtest.py --portfolio --pounds 50000   # real £ returns
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.provider import DataProvider
from analytics.options_backtester import OptionsBacktester
import config


# ─── Full portfolio: all liquid markets IG likely offers options on ───────────
PORTFOLIO_TICKERS = [
    # ─── Equity indices ───
    "SPY",   # S&P 500 — CONFIRMED on IG
    "QQQ",   # Nasdaq 100
    "DIA",   # Dow Jones
    "IWM",   # Russell 2000
    "EWG",   # Germany (DAX proxy)
    "EWU",   # UK (FTSE proxy)
    "EWQ",   # France (CAC 40 proxy)
    "EWJ",   # Japan (Nikkei proxy)
    # ─── Bonds (INVERSELY correlated to equities — key diversifier) ───
    "TLT",   # 20+ Year US Treasury — rallies in crashes
    "IEF",   # 7-10 Year US Treasury — less volatile, steadier
    "LQD",   # Investment Grade Corporate Bonds
    # ─── Commodities (low equity correlation) ───
    "GLD",   # Gold — crisis hedge, different cycle
    "SLV",   # Silver — more volatile than gold
    "USO",   # Oil (WTI proxy) — supply/demand driven
    # ─── Crypto (high vol = fat premiums, uncorrelated) ───
    "BITO",  # Bitcoin futures ETF (ProShares)
    "ETHA",  # Ethereum futures ETF (if available)
]


def main():
    parser = argparse.ArgumentParser(description="Options Backtest Runner")
    parser.add_argument("--tickers", nargs="+", default=["SPY", "QQQ", "DIA", "EWG", "EWU"],
                        help="Tickers to backtest")
    parser.add_argument("--lookback", type=int, default=5,
                        help="Years of history (default 5)")
    parser.add_argument("--zero-cost", action="store_true",
                        help="Run with zero IG spread costs")
    parser.add_argument("--sweep", action="store_true",
                        help="Parameter sweep: test different strike distances and widths")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show each trade entry/skip/close")
    parser.add_argument("--portfolio", action="store_true",
                        help="Run full portfolio across all markets with £ returns")
    parser.add_argument("--pounds", type=float, default=10000,
                        help="Starting capital in £ (default 10000)")
    parser.add_argument("--risk", type=float, default=None,
                        help="Override max_risk_pct (e.g. 5.0 for 5%%)")
    parser.add_argument("--kelly", type=float, default=None,
                        help="Override kelly_fraction (e.g. 0.5 for half Kelly)")
    parser.add_argument("--full-equity", action="store_true",
                        help="Portfolio: give full equity to each market (unrealistic, for comparison)")
    parser.add_argument("--top", type=int, default=0,
                        help="Portfolio: only trade the top N markets by Sharpe (0 = all)")
    parser.add_argument("--min-trades", type=int, default=15,
                        help="Portfolio --top: minimum trades to qualify (default 15)")
    parser.add_argument("--calibrate", action="store_true",
                        help="Load calibration.json to adjust BS premiums to match IG reality")
    args = parser.parse_args()

    # Load calibration if requested
    if args.calibrate:
        loaded = OptionsBacktester.load_calibration()
        if not loaded:
            print("  WARNING: No calibration.json found.")
            print("  Run: python3 calibrate_bs_vs_ig.py (during market hours)")
            print("  Proceeding with uncalibrated BS model.\n")

    # Need lots of data for EMA warmup + backtesting
    dp = DataProvider(lookback_days=args.lookback * 365 + 300)

    if args.sweep:
        run_sweep(dp, args.tickers, args.lookback)
    elif args.portfolio:
        run_portfolio(dp, args)
    else:
        run_single(dp, args)


def run_single(dp, args):
    """Single backtest run with optional zero-cost comparison."""
    obt = OptionsBacktester(dp)
    cost_mode = "zero" if args.zero_cost else "realistic"

    params = dict(config.IBS_CREDIT_SPREAD_PARAMS)
    if args.risk is not None:
        params["max_risk_pct"] = args.risk
    if args.kelly is not None:
        params["kelly_fraction"] = args.kelly

    print(f"IBS Credit Spreads Backtest")
    print(f"Tickers: {args.tickers}")
    print(f"Lookback: {args.lookback} years")
    print(f"Cost mode: {cost_mode}")
    print(f"Starting capital: £{args.pounds:,.0f}")
    if args.risk:
        print(f"Max risk override: {args.risk}%")
    if args.kelly:
        print(f"Kelly override: {args.kelly}")
    print()

    result = obt.run(
        tickers=args.tickers,
        params=params,
        lookback_years=args.lookback,
        equity=args.pounds,
        cost_mode=cost_mode,
        verbose=args.verbose,
    )

    obt.print_summary(result)
    print_money_summary(result, args.pounds, args.lookback)

    # Zero cost comparison
    if not args.zero_cost:
        print("\n\n--- ZERO COST COMPARISON ---")
        result_zero = obt.run(
            tickers=args.tickers,
            params=params,
            lookback_years=args.lookback,
            equity=args.pounds,
            cost_mode="zero",
        )
        print(f"  Zero-cost: {result_zero.total_trades} trades, "
              f"PF={result_zero.profit_factor:.2f}, "
              f"Win={result_zero.win_rate:.0%}, "
              f"Net=£{result_zero.net_pnl:+,.0f}, "
              f"Sharpe={result_zero.sharpe:.2f}")
        print(f"  Realistic: {result.total_trades} trades, "
              f"PF={result.profit_factor:.2f}, "
              f"Win={result.win_rate:.0%}, "
              f"Net=£{result.net_pnl:+,.0f}, "
              f"Sharpe={result.sharpe:.2f}")
        print(f"  IG spread drag: £{result_zero.net_pnl - result.net_pnl:,.0f} "
              f"(£{result.total_ig_spread_cost:,.0f} total)")


def run_portfolio(dp, args):
    """
    Run the strategy across ALL markets and show combined portfolio returns.

    Each market runs independently with its own IG cost model.
    Portfolio P&L = sum of all markets.
    Position sizing is per-market (max_risk_pct applies to each separately).
    """
    obt = OptionsBacktester(dp)
    tickers = PORTFOLIO_TICKERS

    params = dict(config.IBS_CREDIT_SPREAD_PARAMS)
    if args.risk is not None:
        params["max_risk_pct"] = args.risk
    if args.kelly is not None:
        params["kelly_fraction"] = args.kelly

    equity = args.pounds
    cost_mode = "zero" if args.zero_cost else "realistic"
    top_n = args.top
    min_trades = args.min_trades

    # ── Step 1: If --top N, screen all markets first to find the best ones ──
    if top_n > 0:
        print("=" * 90)
        print(f"  SCREENING {len(tickers)} MARKETS (selecting top {top_n} by Sharpe)...")
        print("=" * 90)
        print()

        screen_results = []
        for ticker in tickers:
            try:
                # Screen with full equity to get clean signal quality
                r = obt.run(
                    tickers=[ticker], params=params,
                    lookback_years=args.lookback,
                    equity=10000,  # Arbitrary — just measuring signal quality
                    cost_mode=cost_mode,
                )
                if r.total_trades >= min_trades:
                    screen_results.append((ticker, r.sharpe, r.profit_factor,
                                           r.total_trades, r.win_rate))
                    print(f"  {ticker:<8} Sharpe={r.sharpe:>6.2f}  PF={r.profit_factor:>6.2f}  "
                          f"trades={r.total_trades:>3}  win={r.win_rate:.0%}")
                else:
                    print(f"  {ticker:<8} only {r.total_trades} trades (< {min_trades} minimum), skipped")
            except Exception as e:
                print(f"  {ticker:<8} ERROR: {e}")

        # Rank by Sharpe, take top N
        screen_results.sort(key=lambda x: x[1], reverse=True)
        tickers = [t[0] for t in screen_results[:top_n]]

        if not tickers:
            print(f"\n  No markets passed screening (>= {min_trades} trades)!")
            return

        print(f"\n  SELECTED: {', '.join(tickers)}")
        print()

    # ── Step 2: Allocate equity across selected markets ──
    n_markets = len(tickers)
    if args.full_equity:
        equity_per_market = equity  # Unrealistic: full equity to each market
    else:
        equity_per_market = equity / n_markets

    # Minimum capital warning
    min_useful = 500  # Below this, every trade is 1 contract
    if equity_per_market < min_useful:
        print(f"  ⚠ WARNING: £{equity_per_market:.0f} per market is very thin.")
        print(f"    Every trade will be minimum 1 contract regardless of risk %.")
        print(f"    Consider: fewer markets (--top {min(6, n_markets)}) or more capital (--pounds {int(min_useful * n_markets)})")
        print()

    print("=" * 90)
    print("  IBS CREDIT SPREADS — PORTFOLIO BACKTEST")
    print("=" * 90)
    print(f"  Markets:    {n_markets} ({', '.join(tickers)})")
    print(f"  Lookback:   {args.lookback} years")
    print(f"  Capital:    £{equity:,.0f} total (£{equity_per_market:,.0f} per market)")
    print(f"  Cost mode:  {cost_mode}")
    print(f"  Risk/trade: {params['max_risk_pct']}% of per-market allocation")
    print(f"  Params:     {params['short_distance_pct']}% OTM, "
          f"{params['spread_width_pct']}% wide, {params['expiry_days']} DTE")
    print("=" * 90)
    print()

    # ── Step 3: Run each selected market ──
    print(f"  {'Market':<8} {'Trades':>7} {'Win%':>6} {'PF':>7} {'Sharpe':>7} "
          f"{'Net £':>9} {'IG Cost':>8} {'Avg IV':>7} {'VRP':>5}")
    print("  " + "-" * 78)

    all_results = {}
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_net_pnl = 0
    total_gross_pnl = 0
    total_ig_cost = 0
    total_gross_premium = 0

    for ticker in tickers:
        try:
            result = obt.run(
                tickers=[ticker],
                params=params,
                lookback_years=args.lookback,
                equity=equity_per_market,
                cost_mode=cost_mode,
            )

            if result.total_trades == 0:
                print(f"  {ticker:<8} {'—':>7} {'no signals':>20}")
                continue

            all_results[ticker] = result
            total_trades += result.total_trades
            total_wins += result.wins
            total_losses += result.losses
            total_net_pnl += result.net_pnl
            total_ig_cost += result.total_ig_spread_cost
            total_gross_premium += result.gross_premium

            # Gross P&L = net + costs
            gross = result.net_pnl + result.total_ig_spread_cost
            total_gross_pnl += gross

            print(f"  {ticker:<8} {result.total_trades:>7} "
                  f"{result.win_rate:>5.0%} "
                  f"{result.profit_factor:>7.2f} "
                  f"{result.sharpe:>7.2f} "
                  f"£{result.net_pnl:>+8.0f} "
                  f"{result.total_ig_spread_cost:>8.0f} "
                  f"{result.avg_implied_vol:>6.1%} "
                  f"{result.vrp:>5.1%}")

        except Exception as e:
            print(f"  {ticker:<8} ERROR: {e}")

    # Portfolio summary
    if total_trades == 0:
        print("\n  No trades across any market!")
        return

    total_win_rate = total_wins / total_trades if total_trades > 0 else 0
    cost_drag_pct = total_ig_cost / total_gross_premium * 100 if total_gross_premium > 0 else 0

    print("  " + "-" * 78)
    print(f"  {'TOTAL':<8} {total_trades:>7} "
          f"{total_win_rate:>5.0%} "
          f"{'':>7} {'':>7} "
          f"£{total_net_pnl:>+8.0f} "
          f"{total_ig_cost:>8.0f}")

    print()
    print_money_summary_portfolio(
        all_results, equity, equity_per_market, args.lookback, total_trades,
        total_wins, total_losses, total_net_pnl, total_ig_cost,
        total_gross_premium, params
    )


def print_money_summary(result, equity, lookback_years):
    """Print real £ returns for a single market."""
    if result.total_trades == 0:
        return

    # Net P&L in points × £ per point (assumed £1/pt for spread betting)
    # On IG, you choose your stake per point. With £10k and 2% risk:
    # risk_amount = £200, max_loss ~8pts, so stake = £200/8 = £25/pt
    # But the backtest already sizes positions — the P&L is in portfolio %.
    net_pct = result.net_pnl / equity * 100
    annual_pct = net_pct / lookback_years if lookback_years > 0 else net_pct
    final_equity = equity + result.net_pnl

    print(f"\n\n--- REAL MONEY (£{equity:,.0f} starting capital) ---")
    print(f"  Net P&L:         £{result.net_pnl:+,.0f} ({net_pct:+.2f}%)")
    print(f"  Final equity:    £{final_equity:,.0f}")
    print(f"  Annual return:   {annual_pct:+.2f}% pa")
    print(f"  Max drawdown:    {result.max_drawdown:.1%}")
    print(f"  Trades/year:     {result.total_trades / lookback_years:.0f}")


def print_money_summary_portfolio(all_results, equity, equity_per_market,
                                   lookback_years,
                                   total_trades, total_wins, total_losses,
                                   total_net_pnl, total_ig_cost,
                                   total_gross_premium, params):
    """Print combined portfolio £ returns."""
    net_pct = total_net_pnl / equity * 100
    annual_pct = net_pct / lookback_years if lookback_years > 0 else net_pct
    final_equity = equity + total_net_pnl
    total_win_rate = total_wins / total_trades if total_trades > 0 else 0
    cost_drag = total_ig_cost / total_gross_premium * 100 if total_gross_premium > 0 else 0

    print("=" * 90)
    print("  PORTFOLIO SUMMARY")
    print("=" * 90)
    print(f"  Starting capital:  £{equity:,.0f} (£{equity_per_market:,.0f} per market)")
    print(f"  Final equity:      £{final_equity:,.0f}")
    print(f"  Net P&L:           £{total_net_pnl:+,.0f} ({net_pct:+.2f}%)")
    print(f"  Annual return:     {annual_pct:+.2f}% pa")
    print(f"  Total trades:      {total_trades} ({total_trades / lookback_years:.0f}/year)")
    print(f"  Win rate:          {total_win_rate:.0%} ({total_wins}W / {total_losses}L)")
    print(f"  IG spread cost:    £{total_ig_cost:,.0f} ({cost_drag:.1f}% of gross)")
    print(f"  Markets traded:    {len(all_results)} of {len(PORTFOLIO_TICKERS)}")
    print()

    # What-if with higher capital
    print("  --- WHAT IF: Different capital levels ---")
    for mult, label in [(2, "£10k"), (5, "£25k"), (10, "£50k")]:
        scaled_pnl = total_net_pnl * mult
        scaled_annual = annual_pct  # Return % stays the same
        print(f"  {label} capital: £{scaled_pnl:+,.0f}/yr ({scaled_annual:+.1f}% pa) "
              f"[linear scale — actual compounding differs]")
    print()

    # Per-year breakdown if we have equity curves
    print("  --- NOTES ---")
    print(f"  • Equity split equally across markets (£{equity_per_market:,.0f} each)")
    print(f"  • Risk per trade: {params['max_risk_pct']}% of per-market allocation")
    print(f"  • Max portfolio risk if all markets fire: {params['max_risk_pct']}% × {len(PORTFOLIO_TICKERS)} markets = {params['max_risk_pct'] * len(PORTFOLIO_TICKERS):.0f}% of total")
    print(f"  • IG costs CONFIRMED for SPY (1.2pts/leg). Others are estimated.")
    print(f"  • P&L uses BS-estimated premiums, not live IG quotes.")
    print(f"  • Run with --full-equity for old mode (full capital to each market)")
    print(f"  • To scale up: --pounds 50000")


def run_sweep(dp, tickers, lookback):
    """Sweep key parameters to find optimal configuration."""
    obt = OptionsBacktester(dp)

    short_distances = [1.0, 1.5, 2.0, 2.5, 3.0]
    spread_widths = [0.5, 1.0, 1.5]
    expiry_days = [3, 5, 10]

    print(f"\n{'='*100}")
    print(f"PARAMETER SWEEP")
    print(f"{'='*100}")
    print(f"{'Dist%':>6} {'Width%':>7} {'DTE':>4} {'Trades':>7} {'Win%':>6} "
          f"{'PF':>6} {'Sharpe':>7} {'Net PnL':>9} {'IG Cost':>8} {'VRP':>6}")
    print("-" * 80)

    best_sharpe = -999
    best_params = {}

    for dist in short_distances:
        for width in spread_widths:
            for dte in expiry_days:
                params = {
                    **config.IBS_CREDIT_SPREAD_PARAMS,
                    "short_distance_pct": dist,
                    "spread_width_pct": width,
                    "expiry_days": dte,
                    "max_hold_bars": dte,
                }

                try:
                    result = obt.run(
                        tickers=tickers,
                        params=params,
                        lookback_years=lookback,
                        equity=10000,
                        cost_mode="realistic",
                    )

                    if result.total_trades < 5:
                        continue

                    print(f"{dist:>5.1f}% {width:>6.1f}% {dte:>4} {result.total_trades:>7} "
                          f"{result.win_rate:>5.0%} {result.profit_factor:>6.2f} "
                          f"{result.sharpe:>7.2f} {result.net_pnl:>9.0f} "
                          f"{result.total_ig_spread_cost:>8.0f} {result.vrp:>5.1%}")

                    if result.sharpe > best_sharpe and result.total_trades >= 20:
                        best_sharpe = result.sharpe
                        best_params = {"dist": dist, "width": width, "dte": dte}

                except Exception as e:
                    print(f"{dist:>5.1f}% {width:>6.1f}% {dte:>4}  ERROR: {e}")

    if best_params:
        print(f"\n  BEST: dist={best_params['dist']}%, width={best_params['width']}%, "
              f"DTE={best_params['dte']}, Sharpe={best_sharpe:.2f}")
    else:
        print("\n  No configuration with >= 20 trades found.")


if __name__ == "__main__":
    main()
