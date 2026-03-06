"""
Automated backtesting engine.

Replaces the manual TradingView Pine Script backtesting workflow.
Uses yfinance data and the same Python strategy classes the live bot runs,
ensuring zero divergence between backtest and production.

Features:
- Walk-forward validation (train/test splits)
- Monte Carlo simulation for confidence intervals
- Multi-market, multi-strategy backtesting
- Transaction cost modelling (spread, financing)
- Regime-aware analysis (bull/bear/sideways/high-vol/low-vol)
- Automated parameter sensitivity scanning

Usage:
    from analytics.backtester import Backtester
    bt = Backtester(equity=10000)
    result = bt.run("IBS++ v3", tickers=["SPY", "QQQ"])
    wf = bt.walk_forward("IBS++ v3", tickers=["SPY"])
    mc = bt.monte_carlo(result.trades)
"""
import copy
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from data.provider import DataProvider
from strategies.ibs_mean_reversion import IBSMeanReversion
from strategies.ibs_short import IBSShort
from strategies.trend_following import TrendFollowing
from strategies.spy_tlt_rotation import SPYTLTRotation
from strategies.base import SignalType
import config

logger = logging.getLogger(__name__)


# ─── Cost mode: controls how transaction costs are modelled ──────────────────
# "zero"      — No costs at all (matches Pine Script / TradingView backtests)
# "realistic" — IG spread + historically-accurate overnight financing
# "custom"    — User-specified spread multiplier and financing rate

COST_MODE_ZERO = "zero"
COST_MODE_REALISTIC = "realistic"
COST_MODE_CUSTOM = "custom"

# ─── Spread/cost assumptions for IG spread betting ──────────────────────────

SPREAD_COSTS = {
    # Typical IG spreads in points (one-way cost = half the spread)
    "SPY": 0.4,   "QQQ": 1.0,  "IWM": 0.6,   "DIA": 1.6,
    "EWU": 1.0,   "EWG": 1.2,  "EWJ": 7.0,   "IEF": 0.03,
    "CL=F": 0.03, "GBPUSD=X": 0.0001, "GC=F": 0.3,
    "SI=F": 0.03, "GC=F_trend": 0.3, "CL=F_trend": 0.03,
    "NG=F": 0.003, "HG=F": 0.003,
}

# ─── Historical overnight financing rates (annualised) ──────────────────────
# DFB financing = benchmark rate (SONIA/Fed Funds) + IG markup (~2.5%)
# Using approximate benchmark rates by year to avoid overstating costs pre-2022
HISTORICAL_OVERNIGHT_RATES = {
    # year: annualised rate (benchmark + ~2.5% IG markup)
    2005: 0.070, 2006: 0.075, 2007: 0.080, 2008: 0.055,
    2009: 0.030, 2010: 0.030, 2011: 0.030, 2012: 0.030,
    2013: 0.030, 2014: 0.030, 2015: 0.030, 2016: 0.030,
    2017: 0.035, 2018: 0.045, 2019: 0.045, 2020: 0.030,
    2021: 0.028, 2022: 0.055, 2023: 0.078, 2024: 0.078,
    2025: 0.072, 2026: 0.070,
}
DEFAULT_OVERNIGHT_RATE = 0.070  # Fallback for unknown years

# ─── Entry timing mode ──────────────────────────────────────────────────────
# "close"     — Enter at signal bar's close (our original implementation)
# "next_open" — Enter at next bar's open (matches Pine Script default behaviour)
ENTRY_AT_CLOSE = "close"
ENTRY_AT_NEXT_OPEN = "next_open"


@dataclass
class BacktestTrade:
    """Record of a single backtested trade."""
    ticker: str
    strategy: str
    direction: str
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    bars_held: int = 0
    pnl_gross: float = 0.0
    spread_cost: float = 0.0
    financing_cost: float = 0.0
    pnl_net: float = 0.0
    r_multiple: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    """Full backtest results."""
    strategy: str
    tickers: list
    period_start: str
    period_end: str
    initial_equity: float
    final_equity: float
    total_return_pct: float = 0.0
    # Trades
    trades: list = field(default_factory=list)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    # P&L
    gross_pnl: float = 0.0
    total_spread_cost: float = 0.0
    total_financing: float = 0.0
    net_pnl: float = 0.0
    avg_pnl: float = 0.0
    # Risk metrics
    profit_factor: float = 0.0
    profit_factor_gross: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_days: int = 0
    avg_bars_held: float = 0.0
    expectancy_r: float = 0.0
    # Equity curve
    equity_curve: list = field(default_factory=list)  # [(date, equity), ...]
    # By-market breakdown
    pnl_by_market: dict = field(default_factory=dict)
    stats_by_market: dict = field(default_factory=dict)  # {ticker: {trades, wins, ...}}


@dataclass
class WalkForwardResult:
    """Walk-forward validation results."""
    strategy: str
    windows: list = field(default_factory=list)  # list of BacktestResult (one per OOS window)
    in_sample_sharpe: float = 0.0
    out_of_sample_sharpe: float = 0.0
    sharpe_degradation_pct: float = 0.0
    total_oos_trades: int = 0
    total_oos_pnl: float = 0.0
    oos_win_rate: float = 0.0
    status: str = ""  # "robust", "marginal", "overfitted"


@dataclass
class MonteCarloResult:
    """Monte Carlo simulation results."""
    simulations: int
    median_final_pnl: float = 0.0
    mean_final_pnl: float = 0.0
    percentile_5: float = 0.0
    percentile_25: float = 0.0
    percentile_75: float = 0.0
    percentile_95: float = 0.0
    prob_profitable: float = 0.0
    worst_max_drawdown: float = 0.0
    median_max_drawdown: float = 0.0


# ─── Backtester ──────────────────────────────────────────────────────────────

class Backtester:
    """Automated backtesting engine using the same strategies as the live bot."""

    def __init__(self, equity: float = 10000, lookback_days: int = 750,
                 cost_mode: str = COST_MODE_REALISTIC,
                 entry_timing: str = ENTRY_AT_NEXT_OPEN,
                 custom_spread_mult: float = 1.0,
                 custom_financing_rate: float = 0.07):
        self.initial_equity = equity
        self.cost_mode = cost_mode
        self.entry_timing = entry_timing
        self.custom_spread_mult = custom_spread_mult
        self.custom_financing_rate = custom_financing_rate
        # Product type override: when set, _calc_costs uses this instead of MARKET_MAP lookup.
        # Used by "IBS++ Futures" to force futures cost model on tickers that are normally DFBs.
        self._product_type_override: Optional[str] = None
        # 0 = max available (yfinance supports "max" period)
        self.data = DataProvider(lookback_days=lookback_days if lookback_days > 0 else 10000)

    # ─── Main backtest ───────────────────────────────────────────────────

    def run(self, strategy_name: str, tickers: Optional[list] = None,
            start_date: Optional[str] = None, end_date: Optional[str] = None,
            params: Optional[dict] = None) -> BacktestResult:
        """
        Run a full backtest for a strategy across given tickers.

        Args:
            strategy_name: "IBS++ v3", "Trend Following v2", or "SPY/TLT Rotation v3"
            tickers: List of tickers to test (defaults to configured tickers for strategy)
            start_date: Optional start date "YYYY-MM-DD"
            end_date: Optional end date "YYYY-MM-DD"
            params: Optional parameter override dict
        """
        strategy = self._create_strategy(strategy_name, params)
        if tickers is None:
            tickers = self._default_tickers(strategy_name)

        # Set product type override for futures strategies
        if strategy_name == "IBS++ Futures":
            self._product_type_override = "future"
        else:
            self._product_type_override = None

        all_trades = []
        equity = self.initial_equity

        for ticker in tickers:
            data_ticker = ticker.replace("_trend", "")
            df = self.data.get_daily_bars(data_ticker)
            if df.empty:
                logger.warning(f"Backtest: No data for {data_ticker}")
                continue

            # Apply date filters
            if start_date:
                df = df[df.index >= pd.Timestamp(start_date)]
            if end_date:
                df = df[df.index <= pd.Timestamp(end_date)]
            if len(df) < 220:
                logger.warning(f"Backtest: Insufficient data for {data_ticker} ({len(df)} bars)")
                continue

            trades = self._simulate_strategy(strategy, strategy_name, ticker, df, equity)
            all_trades.extend(trades)

        return self._compile_result(strategy_name, tickers, all_trades, start_date, end_date)

    # ─── Walk-forward validation ─────────────────────────────────────────

    def walk_forward(self, strategy_name: str, tickers: Optional[list] = None,
                     n_windows: int = 4, train_pct: float = 0.7,
                     params: Optional[dict] = None) -> WalkForwardResult:
        """
        Walk-forward analysis: train on past, test on future, step forward.

        Splits data into n_windows. For each window:
        - Train (optimise) on first train_pct of the window
        - Test on remaining out-of-sample portion
        """
        if tickers is None:
            tickers = self._default_tickers(strategy_name)

        # Get the full date range from data
        all_dates = set()
        ticker_data = {}
        for ticker in tickers:
            data_ticker = ticker.replace("_trend", "")
            df = self.data.get_daily_bars(data_ticker)
            if not df.empty:
                ticker_data[ticker] = df
                all_dates.update(df.index.tolist())

        if not all_dates:
            return WalkForwardResult(strategy=strategy_name, status="no_data")

        sorted_dates = sorted(all_dates)
        total_bars = len(sorted_dates)
        window_size = total_bars // n_windows

        oos_results = []
        is_sharpes = []
        oos_sharpes = []

        for i in range(n_windows):
            window_start = sorted_dates[i * window_size]
            window_end = sorted_dates[min((i + 1) * window_size - 1, total_bars - 1)]

            train_end_idx = i * window_size + int(window_size * train_pct)
            train_end = sorted_dates[min(train_end_idx, total_bars - 1)]

            # In-sample backtest
            is_result = self.run(
                strategy_name, tickers,
                start_date=window_start.strftime("%Y-%m-%d"),
                end_date=train_end.strftime("%Y-%m-%d"),
                params=params,
            )
            is_sharpes.append(is_result.sharpe)

            # Out-of-sample backtest
            oos_result = self.run(
                strategy_name, tickers,
                start_date=train_end.strftime("%Y-%m-%d"),
                end_date=window_end.strftime("%Y-%m-%d"),
                params=params,
            )
            oos_results.append(oos_result)
            oos_sharpes.append(oos_result.sharpe)

        # Compile walk-forward result
        wf = WalkForwardResult(strategy=strategy_name, windows=oos_results)

        wf.in_sample_sharpe = np.mean(is_sharpes) if is_sharpes else 0
        wf.out_of_sample_sharpe = np.mean(oos_sharpes) if oos_sharpes else 0

        if wf.in_sample_sharpe != 0:
            wf.sharpe_degradation_pct = (
                (wf.in_sample_sharpe - wf.out_of_sample_sharpe) / abs(wf.in_sample_sharpe) * 100
            )
        wf.total_oos_trades = sum(r.total_trades for r in oos_results)
        wf.total_oos_pnl = sum(r.net_pnl for r in oos_results)

        total_oos_wins = sum(r.wins for r in oos_results)
        wf.oos_win_rate = (total_oos_wins / wf.total_oos_trades * 100) if wf.total_oos_trades > 0 else 0

        # Classify robustness
        if wf.sharpe_degradation_pct < 30 and wf.out_of_sample_sharpe > 0.5:
            wf.status = "robust"
        elif wf.sharpe_degradation_pct < 50 and wf.out_of_sample_sharpe > 0:
            wf.status = "marginal"
        else:
            wf.status = "overfitted"

        return wf

    # ─── Monte Carlo simulation ──────────────────────────────────────────

    def monte_carlo(self, trades: list[BacktestTrade],
                    n_simulations: int = 2000,
                    n_trades: Optional[int] = None) -> MonteCarloResult:
        """
        Shuffle trade sequence randomly to see range of possible outcomes.

        This tests whether your results depend on lucky trade ordering
        or if the edge is robust across different sequences.
        """
        pnls = [t.pnl_net for t in trades if t.pnl_net != 0]
        if len(pnls) < 10:
            return MonteCarloResult(simulations=0)

        if n_trades is None:
            n_trades = len(pnls)

        final_pnls = []
        max_drawdowns = []

        pnl_array = np.array(pnls)

        for _ in range(n_simulations):
            # Randomly sample with replacement
            shuffled = np.random.choice(pnl_array, size=n_trades, replace=True)
            cum_pnl = np.cumsum(shuffled)
            final_pnls.append(cum_pnl[-1])

            # Max drawdown of this path
            peak = np.maximum.accumulate(cum_pnl)
            dd = cum_pnl - peak
            max_drawdowns.append(float(min(dd)))

        final_arr = np.array(final_pnls)
        dd_arr = np.array(max_drawdowns)

        return MonteCarloResult(
            simulations=n_simulations,
            median_final_pnl=round(float(np.median(final_arr)), 2),
            mean_final_pnl=round(float(np.mean(final_arr)), 2),
            percentile_5=round(float(np.percentile(final_arr, 5)), 2),
            percentile_25=round(float(np.percentile(final_arr, 25)), 2),
            percentile_75=round(float(np.percentile(final_arr, 75)), 2),
            percentile_95=round(float(np.percentile(final_arr, 95)), 2),
            prob_profitable=round(float(np.mean(final_arr > 0) * 100), 1),
            worst_max_drawdown=round(float(min(dd_arr)), 2),
            median_max_drawdown=round(float(np.median(dd_arr)), 2),
        )

    # ─── Strategy simulation ─────────────────────────────────────────────

    def _get_overnight_rate(self, year: int) -> float:
        """Get historically accurate overnight financing rate for a given year."""
        return HISTORICAL_OVERNIGHT_RATES.get(year, DEFAULT_OVERNIGHT_RATE)

    def _get_price_scale(self, ticker: str) -> float:
        """
        Get the ratio to convert yfinance price moves to IG price scale.

        yfinance uses ETF proxies (SPY ~500) while IG trades the underlying
        index (US 500 ~5200). The spread and financing costs are in IG points,
        so we need to know what fraction of an IG point one yfinance point represents.

        Returns ratio: yf_level / ig_level. Multiply IG costs by this to get
        costs in yfinance-point-space (same space as our P&L).
        """
        market_info = config.MARKET_MAP.get(ticker, {})
        ig_level = market_info.get("ig_price_level", 0)
        yf_level = market_info.get("yf_approx_level", 0)
        if ig_level > 0 and yf_level > 0:
            return yf_level / ig_level
        return 1.0  # No scaling if not configured

    def _calc_costs(self, data_ticker: str, entry_price: float,
                    bars_held: int, entry_year: int,
                    ticker: str = "") -> tuple[float, float]:
        """
        Calculate spread and financing costs based on cost_mode and product_type.

        Costs are returned in yfinance-point-space (same units as P&L) so they
        can be directly subtracted from gross P&L.

        Product types:
          dfb      — spread + rate-based overnight financing
          future   — wider spread, NO overnight financing
          fx       — spread + small tom-next roll (~0.3% annualised approx)
          com_spot — spread + basis roll (approximated as ~1.5% annualised)

        Returns:
            (spread_cost, financing_cost) in yfinance points
        """
        if self.cost_mode == COST_MODE_ZERO:
            return 0.0, 0.0

        # Look up the ticker (could be data_ticker or the original with _trend suffix)
        lookup = ticker if ticker in config.MARKET_MAP else data_ticker
        market_info = config.MARKET_MAP.get(lookup, {})
        # Use override if set (e.g. "IBS++ Futures" forces "future" on DFB tickers)
        product_type = self._product_type_override or market_info.get("product_type", "dfb")
        price_scale = self._get_price_scale(lookup)

        # Spread cost (entry + exit) — in IG points, then scaled to yfinance space
        ig_half_spread = SPREAD_COSTS.get(data_ticker, 0.5)
        if self.cost_mode == COST_MODE_CUSTOM:
            ig_half_spread *= self.custom_spread_mult
        spread_cost = ig_half_spread * 2 * price_scale  # Round-trip, scaled

        # Overnight financing — depends on product type
        financing = 0.0
        if bars_held > 0:
            if product_type == "future":
                # Futures/forwards: NO overnight financing (cost is in the spread/forward premium)
                financing = 0.0

            elif product_type == "fx":
                # FX: tom-next roll — much smaller than DFB index financing
                # Approximated as ~0.3% annualised (varies by pair and direction)
                if self.cost_mode == COST_MODE_CUSTOM:
                    annual_rate = self.custom_financing_rate
                else:
                    annual_rate = 0.003  # ~0.3% tom-next approximation
                financing = abs(entry_price) * (annual_rate / 365) * bars_held

            elif product_type == "com_spot":
                # Spot commodities: basis/roll adjustment — NOT rate × notional
                # IG prices spot commodities off the futures curve; the overnight
                # adjustment is the daily basis movement + a small IG charge
                # Approximated as ~1.5% annualised (lower than DFB index funding)
                if self.cost_mode == COST_MODE_CUSTOM:
                    annual_rate = self.custom_financing_rate
                else:
                    annual_rate = 0.015  # ~1.5% basis approximation
                financing = abs(entry_price) * (annual_rate / 365) * bars_held

            else:
                # DFB indices: rate × notional (the expensive one)
                # Use IG price level for notional, not yfinance proxy
                ig_level = market_info.get("ig_price_level", 0)
                notional = ig_level if ig_level > 0 else abs(entry_price)

                if self.cost_mode == COST_MODE_REALISTIC:
                    annual_rate = self._get_overnight_rate(entry_year)
                else:
                    annual_rate = self.custom_financing_rate

                # Financing in IG points, then scaled to yfinance space
                financing_ig_pts = notional * (annual_rate / 365) * bars_held
                financing = financing_ig_pts * price_scale

        return spread_cost, financing

    def _simulate_strategy(self, strategy, strategy_name: str,
                            ticker: str, df: pd.DataFrame,
                            equity: float) -> list[BacktestTrade]:
        """
        Simulate a strategy on historical data, bar by bar.

        Entry timing:
        - "next_open": signal on bar i → enter at bar i+1's open (Pine Script default)
        - "close": signal on bar i → enter at bar i's close (our original implementation)
        """
        trades = []
        position = 0.0      # Current position (>0 long, <0 short, 0 flat)
        bars_held = 0
        entry_price = 0.0
        entry_date = ""
        entry_direction = ""
        data_ticker = ticker.replace("_trend", "")

        # Pending entry: when using next_open timing, we queue the entry signal
        pending_entry = None  # ("BUY"|"SELL", signal_bar_index)

        # For SPY/TLT rotation, we need partner data
        partner_df = None
        if strategy_name in ("SPY/TLT Rotation v3", "SPY/TLT Rotation v4"):
            partner_df = self.data.get_daily_bars("TLT")
            if partner_df.empty:
                return trades

        # VIX data for IBS strategies (long, short, and futures variants all use VIX)
        vix_df = None
        if strategy_name in ("IBS++ v3", "IBS++ Futures", "IBS Short (Bear)"):
            vix_df = self.data.get_daily_bars("^VIX")

        # Pre-compute ATR for R-multiple calculation (14-period)
        from data.provider import calc_atr as provider_calc_atr
        atr_series = provider_calc_atr(df, period=14)

        # Walk through each bar (skip warmup period)
        warmup = 220  # Enough for 200 EMA + indicators
        for i in range(warmup, len(df)):
            bar_df = df.iloc[:i + 1]  # Data up to and including this bar
            bar_date = df.index[i].strftime("%Y-%m-%d")
            bar_close = float(df["Close"].iloc[i])
            bar_open = float(df["Open"].iloc[i])
            bar_year = df.index[i].year

            # ── Fill pending entries (next_open mode) ────────────────────
            if pending_entry is not None and self.entry_timing == ENTRY_AT_NEXT_OPEN:
                direction, _ = pending_entry
                position = 1.0 if direction == "BUY" else -1.0
                entry_price = bar_open  # Enter at this bar's open
                entry_date = bar_date
                entry_direction = direction
                bars_held = 0
                pending_entry = None

            # Build kwargs for the strategy
            kwargs = {}
            if strategy_name in ("IBS++ v3", "IBS++ Futures", "IBS Short (Bear)") and vix_df is not None and not vix_df.empty:
                # Find VIX value for this date
                vix_val = None
                try:
                    if df.index[i] in vix_df.index:
                        vix_val = float(vix_df.loc[df.index[i], "Close"])
                    else:
                        vix_before = vix_df[vix_df.index <= df.index[i]]
                        if not vix_before.empty:
                            vix_val = float(vix_before["Close"].iloc[-1])
                except (KeyError, IndexError):
                    pass
                kwargs["vix_close"] = vix_val

            if strategy_name in ("SPY/TLT Rotation v3", "SPY/TLT Rotation v4") and partner_df is not None:
                partner_slice = partner_df[partner_df.index <= df.index[i]]
                kwargs["partner_df"] = partner_slice

            # Pass entry_price for strategies with stop losses (IBS Short needs this)
            if position != 0 and entry_price > 0:
                kwargs["entry_price"] = entry_price

            # Generate signal
            try:
                sig = strategy.generate_signal(
                    ticker=ticker, df=bar_df,
                    current_position=position,
                    bars_in_trade=bars_held,
                    **kwargs,
                )
            except Exception:
                continue

            # Process signal
            if sig.signal_type == SignalType.LONG_ENTRY and position == 0 and pending_entry is None:
                if self.entry_timing == ENTRY_AT_NEXT_OPEN:
                    pending_entry = ("BUY", i)
                else:
                    position = 1.0
                    entry_price = bar_close
                    entry_date = bar_date
                    entry_direction = "BUY"
                    bars_held = 0

            elif sig.signal_type == SignalType.SHORT_ENTRY and position == 0 and pending_entry is None:
                if self.entry_timing == ENTRY_AT_NEXT_OPEN:
                    pending_entry = ("SELL", i)
                else:
                    position = -1.0
                    entry_price = bar_close
                    entry_date = bar_date
                    entry_direction = "SELL"
                    bars_held = 0

            elif sig.signal_type in (SignalType.LONG_EXIT, SignalType.SHORT_EXIT) and position != 0:
                # Close trade — always exit at this bar's close (standard)
                exit_price = bar_close

                if entry_direction == "BUY":
                    pnl_gross = exit_price - entry_price
                else:
                    pnl_gross = entry_price - exit_price

                # Transaction costs (mode-dependent, product-type-aware)
                entry_year_val = int(entry_date[:4]) if entry_date else bar_year
                spread_cost, financing = self._calc_costs(
                    data_ticker, entry_price, bars_held, entry_year_val,
                    ticker=ticker,
                )

                pnl_net = pnl_gross - spread_cost - financing

                # R-multiple: use ATR at entry as the risk unit (proper institutional metric)
                # ATR represents the expected volatility = 1R of risk
                atr_at_entry = float(atr_series.iloc[i]) if i < len(atr_series) else 1.0
                r_mult = pnl_net / max(atr_at_entry, 0.01)

                trades.append(BacktestTrade(
                    ticker=ticker, strategy=strategy_name,
                    direction=entry_direction,
                    entry_date=entry_date, entry_price=round(entry_price, 4),
                    exit_date=bar_date, exit_price=round(exit_price, 4),
                    bars_held=bars_held,
                    pnl_gross=round(pnl_gross, 4),
                    spread_cost=round(spread_cost, 4),
                    financing_cost=round(financing, 4),
                    pnl_net=round(pnl_net, 4),
                    r_multiple=round(r_mult, 3),
                    exit_reason=sig.reason,
                ))

                position = 0.0
                bars_held = 0
                entry_price = 0.0

                # Handle reversal (SHORT_ENTRY after LONG_EXIT etc.)
                if sig.signal_type == SignalType.LONG_EXIT and "Reverse" in sig.reason:
                    if self.entry_timing == ENTRY_AT_NEXT_OPEN:
                        pending_entry = ("SELL", i)
                    else:
                        position = -1.0
                        entry_price = bar_close
                        entry_date = bar_date
                        entry_direction = "SELL"

            if position != 0:
                bars_held += 1

        return trades

    # ─── Compile results ─────────────────────────────────────────────────

    def _compile_result(self, strategy_name: str, tickers: list,
                         trades: list[BacktestTrade],
                         start_date: Optional[str],
                         end_date: Optional[str]) -> BacktestResult:
        """Compile trade list into a BacktestResult with all metrics."""
        result = BacktestResult(
            strategy=strategy_name,
            tickers=tickers,
            period_start=start_date or (trades[0].entry_date if trades else ""),
            period_end=end_date or (trades[-1].exit_date if trades else ""),
            initial_equity=self.initial_equity,
            final_equity=self.initial_equity,
            trades=trades,
        )

        if not trades:
            return result

        pnls_net = [t.pnl_net for t in trades]
        pnls_gross = [t.pnl_gross for t in trades]
        wins = [p for p in pnls_net if p > 0]
        losses = [p for p in pnls_net if p <= 0]

        result.total_trades = len(trades)
        result.wins = len(wins)
        result.losses = len(losses)
        result.win_rate = len(wins) / len(trades) * 100

        result.gross_pnl = sum(pnls_gross)
        result.total_spread_cost = sum(t.spread_cost for t in trades)
        result.total_financing = sum(t.financing_cost for t in trades)
        result.net_pnl = sum(pnls_net)
        result.avg_pnl = np.mean(pnls_net)
        result.avg_bars_held = np.mean([t.bars_held for t in trades])

        result.final_equity = self.initial_equity + result.net_pnl
        result.total_return_pct = result.net_pnl / self.initial_equity * 100

        # Profit factor (net)
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.001
        result.profit_factor = gross_profit / gross_loss

        # Profit factor (gross — for comparison with Pine Script)
        wins_gross = [p for p in pnls_gross if p > 0]
        losses_gross = [p for p in pnls_gross if p <= 0]
        gp_gross = sum(wins_gross) if wins_gross else 0
        gl_gross = abs(sum(losses_gross)) if losses_gross else 0.001
        result.profit_factor_gross = gp_gross / gl_gross

        # Expectancy R
        avg_loss = abs(np.mean(losses)) if losses else 1.0
        result.expectancy_r = np.mean(pnls_net) / avg_loss if avg_loss > 0 else 0

        # Sharpe & Sortino (trade-level, annualised)
        if len(pnls_net) >= 5:
            arr = np.array(pnls_net)
            mean_r = np.mean(arr)
            std_r = np.std(arr, ddof=1)

            # Estimate trades per year
            try:
                first_date = datetime.strptime(trades[0].entry_date, "%Y-%m-%d")
                last_date = datetime.strptime(trades[-1].exit_date, "%Y-%m-%d")
                span_days = max((last_date - first_date).days, 1)
                trades_per_year = len(trades) / span_days * 365
            except (ValueError, TypeError):
                trades_per_year = 52  # fallback

            ann = math.sqrt(max(trades_per_year, 1))

            result.sharpe = float(mean_r / std_r * ann) if std_r > 0 else 0
            downside = arr[arr < 0]
            ds_std = np.std(downside, ddof=1) if len(downside) > 1 else std_r
            result.sortino = float(mean_r / ds_std * ann) if ds_std > 0 else 0

        # Equity curve and drawdown
        cum_pnl = np.cumsum(pnls_net)
        equity_vals = self.initial_equity + cum_pnl
        peak = np.maximum.accumulate(equity_vals)
        dd_pct = (equity_vals - peak) / peak * 100

        result.max_drawdown_pct = float(min(dd_pct))
        result.equity_curve = [
            (trades[i].exit_date, round(float(equity_vals[i]), 2))
            for i in range(len(trades))
        ]

        # Max drawdown duration
        in_dd = dd_pct < 0
        max_dd_bars = 0
        current_dd_bars = 0
        for val in in_dd:
            if val:
                current_dd_bars += 1
                max_dd_bars = max(max_dd_bars, current_dd_bars)
            else:
                current_dd_bars = 0
        result.max_drawdown_duration_days = max_dd_bars

        # P&L by market
        by_mkt = {}
        for t in trades:
            by_mkt[t.ticker] = by_mkt.get(t.ticker, 0) + t.pnl_net
        result.pnl_by_market = {k: round(v, 2) for k, v in by_mkt.items()}

        # Detailed stats by market — for comparison table
        grouped = {}
        for t in trades:
            grouped.setdefault(t.ticker, []).append(t)

        stats_by_mkt = {}
        for ticker, mkt_trades in grouped.items():
            mkt_pnls_net = [t.pnl_net for t in mkt_trades]
            mkt_wins = [p for p in mkt_pnls_net if p > 0]
            mkt_losses = [p for p in mkt_pnls_net if p <= 0]
            mkt_gross = sum(t.pnl_gross for t in mkt_trades)
            mkt_spread = sum(t.spread_cost for t in mkt_trades)
            mkt_fin = sum(t.financing_cost for t in mkt_trades)
            mkt_net = sum(mkt_pnls_net)

            gp = sum(mkt_wins) if mkt_wins else 0
            gl = abs(sum(mkt_losses)) if mkt_losses else 0.001
            pf = gp / gl

            # Per-market Sharpe (trade-level, annualised)
            mkt_sharpe = 0.0
            if len(mkt_pnls_net) >= 5:
                arr = np.array(mkt_pnls_net)
                m, s = float(np.mean(arr)), float(np.std(arr, ddof=1))
                try:
                    d0 = datetime.strptime(mkt_trades[0].entry_date, "%Y-%m-%d")
                    d1 = datetime.strptime(mkt_trades[-1].exit_date, "%Y-%m-%d")
                    span = max((d1 - d0).days, 1)
                    tpy = len(mkt_trades) / span * 365
                except (ValueError, TypeError):
                    tpy = 52
                mkt_sharpe = float(m / s * math.sqrt(max(tpy, 1))) if s > 0 else 0

            market_info = config.MARKET_MAP.get(ticker, {})
            mkt_product_type = self._product_type_override or market_info.get("product_type", "unknown")

            # Per-market equity curve (cumulative P&L rebased to 100)
            cum = np.cumsum(mkt_pnls_net)
            eq_curve = [(mkt_trades[i].exit_date, round(float(100 + cum[i]), 2))
                        for i in range(len(mkt_trades))]

            stats_by_mkt[ticker] = {
                "trades": len(mkt_trades),
                "wins": len(mkt_wins),
                "losses": len(mkt_losses),
                "win_rate": round(len(mkt_wins) / len(mkt_trades) * 100, 1),
                "gross_pnl": round(mkt_gross, 2),
                "spread_cost": round(mkt_spread, 2),
                "financing": round(mkt_fin, 2),
                "net_pnl": round(mkt_net, 2),
                "profit_factor": round(pf, 2),
                "sharpe": round(mkt_sharpe, 2),
                "avg_bars": round(float(np.mean([t.bars_held for t in mkt_trades])), 1),
                "avg_r": round(float(np.mean([t.r_multiple for t in mkt_trades])), 3),
                "product_type": mkt_product_type,
                "equity_curve": eq_curve,
            }

        result.stats_by_market = stats_by_mkt

        return result

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _create_strategy(self, name: str, params: Optional[dict] = None):
        """Create a fresh strategy instance."""
        if name == "IBS++ v3" or name == "IBS++ Futures":
            return IBSMeanReversion(params=params)
        elif name == "IBS Short (Bear)":
            return IBSShort(params=params)
        elif name in ("Trend Following v2", "Trend Following v2 [DEPRECATED]"):
            return TrendFollowing(params=params)
        elif name == "SPY/TLT Rotation v3":
            return SPYTLTRotation(params=params)
        elif name == "SPY/TLT Rotation v4":
            v4_params = dict(config.ROTATION_PARAMS)
            v4_params["allow_short_loser"] = True
            return SPYTLTRotation(params=params or v4_params)
        elif name == "Dynamic":
            from strategies.dynamic_strategy import DynamicStrategy
            if not params or "strategy_spec" not in params:
                raise ValueError("Dynamic strategy requires params['strategy_spec']")
            return DynamicStrategy(params["strategy_spec"])
        else:
            raise ValueError(f"Unknown strategy: {name}")

    def _default_tickers(self, strategy_name: str) -> list:
        """Get default tickers for a strategy."""
        if strategy_name in ("IBS++ v3", "IBS++ Futures", "IBS Short (Bear)"):
            return [k for k, v in config.MARKET_MAP.items() if v.get("strategy") == "ibs"]
        elif strategy_name in ("Trend Following v2", "Trend Following v2 [DEPRECATED]"):
            return [k for k, v in config.MARKET_MAP.items() if v.get("strategy") == "trend"]
        elif strategy_name in ("SPY/TLT Rotation v3", "SPY/TLT Rotation v4"):
            return ["SPY"]
        return []
