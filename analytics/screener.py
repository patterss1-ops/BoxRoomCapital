"""
Strategy Screener — automated scan of all markets across all strategies.

Runs backtests (zero cost + realistic cost), stores results to SQLite,
and outputs a ranked viability assessment for portfolio construction.

Usage:
    python -m analytics.screener                   # full scan, all strategies
    python -m analytics.screener --strategy "IBS++ v3"  # single strategy
    python -m analytics.screener --report          # just print stored results
"""
import sys
import os
import json
import sqlite3
import argparse
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from analytics.backtester import (
    Backtester, BacktestResult,
    COST_MODE_ZERO, COST_MODE_REALISTIC,
    ENTRY_AT_NEXT_OPEN,
)
from analytics.options_backtester import OptionsBacktester, OptionsBacktestResult

# ─── Database ──────────────────────────────────────────────────────────────

SCREENER_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screener_results.db")


def init_screener_db(db_path: str = SCREENER_DB):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS screener_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            strategy TEXT NOT NULL,
            ticker TEXT NOT NULL,
            product_type TEXT,
            category TEXT,           -- proven / candidate
            cost_mode TEXT NOT NULL,  -- zero / realistic
            lookback_days INTEGER,
            period_start TEXT,
            period_end TEXT,
            -- Core metrics
            total_trades INTEGER,
            win_rate REAL,
            gross_pnl REAL,
            net_pnl REAL,
            spread_cost REAL,
            financing_cost REAL,
            profit_factor_gross REAL,
            profit_factor_net REAL,
            sharpe REAL,
            sortino REAL,
            max_drawdown_pct REAL,
            avg_bars_held REAL,
            expectancy_r REAL,
            -- Derived
            cost_drag_pct REAL,      -- costs as % of gross
            annual_return_pct REAL,
            -- Verdict
            viable TEXT,             -- YES / MARGINAL / NO
            notes TEXT,
            -- Full result JSON for drill-down
            result_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_screener_strategy ON screener_runs(strategy, ticker, cost_mode);
        CREATE INDEX IF NOT EXISTS idx_screener_date ON screener_runs(run_date);
    """)
    conn.close()


def store_result(strategy: str, ticker: str, category: str, cost_mode: str,
                 lookback: int, result: BacktestResult, db_path: str = SCREENER_DB,
                 product_type_override: str = ""):
    """Store a single backtest result to the screener database."""
    conn = sqlite3.connect(db_path)

    market_info = config.MARKET_MAP.get(ticker, {})
    product_type = product_type_override or market_info.get("product_type", "unknown")

    total_costs = result.total_spread_cost + result.total_financing
    cost_drag = (total_costs / abs(result.gross_pnl) * 100) if result.gross_pnl != 0 else 0

    # Estimate annualised return
    try:
        from datetime import datetime as dt
        d0 = dt.strptime(result.period_start, "%Y-%m-%d")
        d1 = dt.strptime(result.period_end, "%Y-%m-%d")
        years = max((d1 - d0).days / 365.25, 0.1)
        annual_return = result.total_return_pct / years
    except (ValueError, TypeError):
        annual_return = 0

    # Viability verdict
    viable = assess_viability(result, cost_drag, cost_mode)

    # Compact JSON of key stats (not full trades list — too large)
    stats_json = json.dumps({
        "pnl_by_market": result.pnl_by_market,
        "stats_by_market": {
            k: {kk: vv for kk, vv in v.items() if kk != "equity_curve"}
            for k, v in result.stats_by_market.items()
        } if hasattr(result, 'stats_by_market') else {},
        "equity_curve_len": len(result.equity_curve),
        "period_start": result.period_start,
        "period_end": result.period_end,
    })

    conn.execute("""
        INSERT INTO screener_runs (
            run_date, strategy, ticker, product_type, category, cost_mode,
            lookback_days, period_start, period_end,
            total_trades, win_rate, gross_pnl, net_pnl,
            spread_cost, financing_cost,
            profit_factor_gross, profit_factor_net,
            sharpe, sortino, max_drawdown_pct, avg_bars_held, expectancy_r,
            cost_drag_pct, annual_return_pct, viable, notes, result_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        strategy, ticker, product_type, category, cost_mode,
        lookback, result.period_start, result.period_end,
        result.total_trades, result.win_rate, result.gross_pnl, result.net_pnl,
        result.total_spread_cost, result.total_financing,
        result.profit_factor_gross, result.profit_factor,
        result.sharpe, result.sortino, result.max_drawdown_pct,
        result.avg_bars_held, result.expectancy_r,
        round(cost_drag, 1), round(annual_return, 1),
        viable, "", stats_json,
    ))
    conn.commit()
    conn.close()


def assess_viability(result: BacktestResult, cost_drag_pct: float, cost_mode: str) -> str:
    """
    Assess whether a market is viable for live trading.

    Criteria (realistic cost mode):
        YES:      PF > 1.5, Sharpe > 0.5, win rate > 50%, trades > 20, cost drag < 30%, DD < -25%
        MARGINAL: PF > 1.2, Sharpe > 0.3, trades > 10
        NO:       everything else

    For zero cost mode, we assess the raw edge (no cost drag check):
        YES:      PF > 1.3, Sharpe > 0.4, trades > 20
        MARGINAL: PF > 1.1, trades > 10
        NO:       everything else
    """
    pf = result.profit_factor
    sharpe = result.sharpe
    wr = result.win_rate
    n = result.total_trades
    dd = result.max_drawdown_pct

    if cost_mode == COST_MODE_ZERO:
        pf_gross = result.profit_factor_gross if hasattr(result, 'profit_factor_gross') else pf
        if pf_gross > 1.3 and sharpe > 0.4 and n >= 20:
            return "YES"
        elif pf_gross > 1.1 and n >= 10:
            return "MARGINAL"
        return "NO"

    # Realistic cost mode — the one that matters
    if pf > 1.5 and sharpe > 0.5 and wr > 50 and n >= 20 and cost_drag_pct < 30 and dd > -25:
        return "YES"
    elif pf > 1.2 and sharpe > 0.3 and n >= 10:
        return "MARGINAL"
    return "NO"


# ─── Options helpers ──────────────────────────────────────────────────────

def _run_options_backtest(ticker, lookback):
    """Run options backtester for a single ticker. Returns OptionsBacktestResult or None."""
    try:
        from data.provider import DataProvider
        dp = DataProvider()
        obt = OptionsBacktester(dp)
        lookback_years = max(lookback // 365, 1)
        result = obt.run(
            tickers=[ticker],
            params=config.IBS_CREDIT_SPREAD_PARAMS,
            lookback_years=lookback_years,
            equity=10000,
            cost_mode="realistic",
        )
        return result
    except Exception as e:
        print(f"Options backtest error: {e}")
        import traceback
        traceback.print_exc()
        return None


def _assess_options_viability(result):
    """Assess viability of an options strategy result."""
    if result.total_trades < 10:
        return "NO"
    if result.profit_factor > 1.5 and result.sharpe > 0.5 and result.win_rate > 0.55:
        return "YES"
    elif result.profit_factor > 1.2 and result.sharpe > 0.3:
        return "MARGINAL"
    return "NO"


def store_options_result(strategy, ticker, category, lookback, result, db_path=SCREENER_DB):
    """Store an options backtest result to the screener database."""
    conn = sqlite3.connect(db_path)

    cost_drag = (result.total_ig_spread_cost / max(result.gross_premium, 0.01) * 100)
    viable = _assess_options_viability(result)

    stats_json = json.dumps({
        "stats_by_market": result.stats_by_market,
        "avg_implied_vol": result.avg_implied_vol,
        "avg_realised_vol": result.avg_realised_vol,
        "vrp": result.vrp,
        "avg_premium_pct": result.avg_premium_pct,
    })

    conn.execute("""
        INSERT INTO screener_runs (
            run_date, strategy, ticker, product_type, category, cost_mode,
            lookback_days, period_start, period_end,
            total_trades, win_rate, gross_pnl, net_pnl,
            spread_cost, financing_cost,
            profit_factor_gross, profit_factor_net,
            sharpe, sortino, max_drawdown_pct, avg_bars_held, expectancy_r,
            cost_drag_pct, annual_return_pct, viable, notes, result_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        strategy, ticker, "option", category, "realistic",
        lookback, result.period[:10] if result.period else "", result.period[-10:] if result.period else "",
        result.total_trades, result.win_rate * 100, result.gross_premium, result.net_pnl,
        result.total_ig_spread_cost, 0.0,  # No financing for options
        result.profit_factor, result.profit_factor,
        result.sharpe, 0.0, result.max_drawdown * -100,
        result.avg_bars_held, result.avg_pnl_per_trade,
        round(cost_drag, 1), 0.0,
        viable, f"VRP={result.vrp:.1%}, avg_IV={result.avg_implied_vol:.1%}", stats_json,
    ))
    conn.commit()
    conn.close()


# ─── Runner ────────────────────────────────────────────────────────────────

def run_screener(strategies: Optional[list] = None, lookback: int = 2000,
                 include_candidates: bool = False, db_path: str = SCREENER_DB):
    """
    Run full screener across strategies and markets.

    For each strategy × market combination, runs BOTH zero-cost and realistic-cost
    backtests so we can see the raw edge AND the after-costs reality.
    """
    init_screener_db(db_path)

    if strategies is None:
        strategies = list(config.BACKTEST_MARKETS.keys())

    total_combos = 0
    for strat in strategies:
        markets = config.BACKTEST_MARKETS.get(strat, {})
        total_combos += len(markets.get("proven", {}))
        if include_candidates:
            total_combos += len(markets.get("candidates", {}))

    print(f"\n{'='*70}")
    print(f"STRATEGY SCREENER — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")
    print(f"Strategies: {', '.join(strategies)}")
    print(f"Lookback: {lookback} days ({'max' if lookback == 0 else f'~{lookback//365}y {(lookback%365)//30}m'})")
    print(f"Markets: {total_combos} ({'proven + candidates' if include_candidates else 'proven only'})")
    print(f"Running 2 cost modes per market (zero + realistic)")
    print(f"Total backtests: {total_combos * 2}")
    print(f"{'='*70}\n")

    results_summary = []
    completed = 0

    for strat in strategies:
        markets = config.BACKTEST_MARKETS.get(strat, {})
        market_lists = [("proven", markets.get("proven", {}))]
        if include_candidates:
            market_lists.append(("candidates", markets.get("candidates", {})))

        for category, market_dict in market_lists:
            for ticker, description in market_dict.items():
                # Options strategy uses different backtester (single cost mode)
                is_options = strat == "IBS Credit Spreads"
                cost_modes = ["realistic"] if is_options else [COST_MODE_ZERO, COST_MODE_REALISTIC]

                for cost_mode in cost_modes:
                    completed += 1
                    cost_label = "OPT" if is_options else ("ZERO" if cost_mode == COST_MODE_ZERO else "REAL")
                    print(f"  [{completed}/{total_combos*2}] {strat} | {ticker:12} | {cost_label:4} | ", end="", flush=True)

                    try:
                        if is_options:
                            # Options backtester
                            result = _run_options_backtest(ticker, lookback)
                            if result is None or result.total_trades == 0:
                                print("NO TRADES")
                                continue

                            # Store to DB using options-specific fields
                            store_options_result(strat, ticker, category, lookback, result, db_path)

                            results_summary.append({
                                "strategy": strat,
                                "ticker": ticker,
                                "category": category,
                                "product_type": "option",
                                "trades": result.total_trades,
                                "win_rate": result.win_rate * 100,
                                "pf_gross": result.profit_factor,
                                "pf_net": result.profit_factor,
                                "sharpe": result.sharpe,
                                "net_pnl": result.net_pnl,
                                "cost_drag": round(result.total_ig_spread_cost / max(result.gross_premium, 0.01) * 100, 1),
                                "max_dd": result.max_drawdown * -100,
                                "viable": _assess_options_viability(result),
                            })

                            viable = _assess_options_viability(result)
                            icon = {"YES": "OK", "MARGINAL": "?", "NO": "X"}.get(viable, "")
                            print(f"{result.total_trades:3} trades | PF={result.profit_factor:.2f} | "
                                  f"Sharpe={result.sharpe:.2f} | Net={result.net_pnl:>8.0f}pts | "
                                  f"WR={result.win_rate:.0%} | VRP={result.vrp:.1%} | {icon}")

                        else:
                            bt = Backtester(
                                equity=10000,
                                lookback_days=lookback,
                                cost_mode=cost_mode,
                                entry_timing=ENTRY_AT_NEXT_OPEN,
                            )
                            result = bt.run(strat, tickers=[ticker])

                            if result.total_trades == 0:
                                print("NO TRADES")
                                continue

                            # Determine product type (futures strategy overrides to "future")
                            pt_override = ""
                            store_result(strat, ticker, category, cost_mode, lookback, result, db_path,
                                         product_type_override=pt_override)

                            # Only track realistic for the summary
                            if cost_mode == COST_MODE_REALISTIC:
                                total_costs = result.total_spread_cost + result.total_financing
                                cost_drag = (total_costs / abs(result.gross_pnl) * 100) if result.gross_pnl != 0 else 0
                                viable = assess_viability(result, cost_drag, cost_mode)
                                effective_pt = config.MARKET_MAP.get(ticker, {}).get("product_type", "?")
                                results_summary.append({
                                    "strategy": strat,
                                    "ticker": ticker,
                                    "category": category,
                                    "product_type": effective_pt,
                                    "trades": result.total_trades,
                                    "win_rate": result.win_rate,
                                    "pf_gross": result.profit_factor_gross,
                                    "pf_net": result.profit_factor,
                                    "sharpe": result.sharpe,
                                    "net_pnl": result.net_pnl,
                                    "cost_drag": round(cost_drag, 1),
                                    "max_dd": result.max_drawdown_pct,
                                    "viable": viable,
                                })

                            icon = {"YES": "OK", "MARGINAL": "?", "NO": "X"}.get(
                                assess_viability(result,
                                    (result.total_spread_cost + result.total_financing) / max(abs(result.gross_pnl), 0.01) * 100,
                                    cost_mode), "")
                            print(f"{result.total_trades:3} trades | PF={result.profit_factor:.2f} | "
                                  f"Sharpe={result.sharpe:.2f} | Net={result.net_pnl:>8.2f} | {icon}")

                    except Exception as e:
                        import traceback
                        print(f"ERROR: {e}")
                        traceback.print_exc()

    # ─── Print summary ─────────────────────────────────────────────────
    print_summary(results_summary)
    return results_summary


def print_summary(results: list):
    """Print a ranked summary of all realistic-cost results."""
    if not results:
        print("\nNo results to summarise.")
        return

    print(f"\n{'='*90}")
    print("PORTFOLIO RECOMMENDATION — Realistic Costs")
    print(f"{'='*90}\n")

    # Sort: YES first, then by Sharpe descending
    order = {"YES": 0, "MARGINAL": 1, "NO": 2}
    results.sort(key=lambda r: (order.get(r["viable"], 3), -r["sharpe"]))

    # Header
    print(f"{'Verdict':8} {'Strategy':22} {'Ticker':12} {'Type':8} {'Cat':9} "
          f"{'Trades':>6} {'Win%':>5} {'PF(g)':>6} {'PF(n)':>6} {'Sharpe':>7} "
          f"{'Net P&L':>10} {'Costs%':>6} {'MaxDD':>6}")
    print("-" * 120)

    for r in results:
        icon = {"YES": "✅", "MARGINAL": "⚠️ ", "NO": "❌"}.get(r["viable"], "  ")
        print(f"{icon:8} {r['strategy']:22} {r['ticker']:12} {r['product_type']:8} {r['category']:9} "
              f"{r['trades']:>6} {r['win_rate']:>4.1f}% {r['pf_gross']:>5.2f}  {r['pf_net']:>5.2f}  "
              f"{r['sharpe']:>6.2f} £{r['net_pnl']:>9.2f} {r['cost_drag']:>5.1f}% {r['max_dd']:>5.1f}%")

    # Count by verdict
    yes_count = sum(1 for r in results if r["viable"] == "YES")
    marginal_count = sum(1 for r in results if r["viable"] == "MARGINAL")
    no_count = sum(1 for r in results if r["viable"] == "NO")

    print(f"\n{'='*90}")
    print(f"VIABLE: {yes_count} markets | MARGINAL: {marginal_count} | NOT VIABLE: {no_count}")
    print(f"{'='*90}")

    if yes_count > 0:
        print("\n📋 RECOMMENDED PORTFOLIO (viable markets only):")
        viable_results = [r for r in results if r["viable"] == "YES"]
        for r in viable_results:
            print(f"   • {r['ticker']:12} ({r['strategy']}) — "
                  f"PF={r['pf_net']:.2f}, Sharpe={r['sharpe']:.2f}, "
                  f"cost drag {r['cost_drag']:.0f}%, {r['trades']} trades")

        total_net = sum(r["net_pnl"] for r in viable_results)
        print(f"\n   Combined net P&L (on £10k per market): £{total_net:,.2f}")
    else:
        print("\n⚠️  No markets currently meet all viability criteria.")
        print("   Consider reviewing MARGINAL markets or adjusting strategy parameters.")


def print_stored_report(db_path: str = SCREENER_DB):
    """Print the most recent screener results from the database."""
    if not os.path.exists(db_path):
        print("No screener results found. Run the screener first.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get the most recent run date
    row = conn.execute("SELECT MAX(run_date) as latest FROM screener_runs").fetchone()
    if not row or not row["latest"]:
        print("No screener results found.")
        conn.close()
        return

    latest = row["latest"]
    print(f"Showing results from: {latest}\n")

    # Get realistic cost results from that run
    rows = conn.execute("""
        SELECT * FROM screener_runs
        WHERE run_date = ? AND cost_mode = 'realistic'
        ORDER BY viable ASC, sharpe DESC
    """, (latest,)).fetchall()

    results = []
    for r in rows:
        results.append({
            "strategy": r["strategy"],
            "ticker": r["ticker"],
            "category": r["category"],
            "product_type": r["product_type"],
            "trades": r["total_trades"],
            "win_rate": r["win_rate"],
            "pf_gross": r["profit_factor_gross"],
            "pf_net": r["profit_factor_net"],
            "sharpe": r["sharpe"],
            "net_pnl": r["net_pnl"],
            "cost_drag": r["cost_drag_pct"],
            "max_dd": r["max_drawdown_pct"],
            "viable": r["viable"],
        })

    conn.close()
    print_summary(results)


# ─── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy Screener")
    parser.add_argument("--strategy", type=str, help="Run single strategy (e.g. 'IBS++ v3')")
    parser.add_argument("--lookback", type=int, default=2000, help="Data lookback in days (default: 2000)")
    parser.add_argument("--candidates", action="store_true", help="Include candidate (untested) markets")
    parser.add_argument("--report", action="store_true", help="Print stored results without re-running")
    args = parser.parse_args()

    if args.report:
        print_stored_report()
    else:
        strats = [args.strategy] if args.strategy else None
        run_screener(strategies=strats, lookback=args.lookback, include_candidates=args.candidates)
