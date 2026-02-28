"""
Options Credit Spread Backtester — Simulates IBS-timed put spreads on historical data.

Key difference from directional backtester:
  - P&L is determined by whether the underlying breaches the short strike by expiry
  - Premium is calculated via Black-Scholes using historical realised vol
  - Costs are the IG bid/offer spread on option legs (no overnight financing)
  - Risk is always defined at entry: max_loss = spread_width - premium

We simulate the FULL lifecycle:
  1. IBS signal fires → construct credit spread using BS pricing
  2. Track the underlying price each bar during the hold period
  3. At expiry (or early close):
     - If underlying > short_put: full profit (premium collected)
     - If underlying < long_put: max loss
     - In between: partial loss = short_strike - underlying_price - premium
  4. Account for IG spread cost on entry (and exit if closed early)

Historical vol is computed from the trailing 30-day realised vol of the underlying,
then inflated by the VRP (Volatility Risk Premium) to simulate implied vol.

Usage:
    from analytics.options_backtester import OptionsBacktester

    bt = OptionsBacktester(data_provider)
    result = bt.run(tickers=["SPY", "QQQ"], params={...})
"""
import math
import os
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta

import numpy as np


# ─── Result containers ────────────────────────────────────────────────────────

@dataclass
class OptionTrade:
    """Record of one completed credit spread trade."""
    ticker: str = ""
    trade_type: str = ""         # "put_spread", "call_spread", "iron_condor"
    entry_date: str = ""
    exit_date: str = ""
    underlying_entry: float = 0.0
    underlying_exit: float = 0.0
    short_strike: float = 0.0
    long_strike: float = 0.0
    spread_width: float = 0.0
    premium_collected: float = 0.0  # Theoretical BS premium
    ig_spread_cost: float = 0.0     # IG bid/offer cost
    net_premium: float = 0.0        # premium - ig_spread_cost
    pnl: float = 0.0               # Final P&L (total, all contracts)
    contracts: int = 1             # Number of contracts (£/pt on IG)
    max_loss: float = 0.0
    bars_held: int = 0
    exit_reason: str = ""
    implied_vol: float = 0.0
    realised_vol: float = 0.0
    ibs_at_entry: float = 0.0
    vix_at_entry: float = 0.0
    # For iron condors
    short_put: float = 0.0
    long_put: float = 0.0
    short_call: float = 0.0
    long_call: float = 0.0
    put_premium: float = 0.0
    call_premium: float = 0.0


@dataclass
class OptionsBacktestResult:
    """Aggregate results from an options backtest."""
    strategy: str = ""
    tickers: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    period: str = ""
    trades: list = field(default_factory=list)

    # Aggregate metrics
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_premium: float = 0.0
    total_ig_spread_cost: float = 0.0
    net_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    avg_bars_held: float = 0.0
    avg_premium_pct: float = 0.0    # Avg premium as % of spread width
    avg_implied_vol: float = 0.0
    avg_realised_vol: float = 0.0
    vrp: float = 0.0               # Avg vol risk premium (IV - RV)

    # Per-market breakdown
    stats_by_market: dict = field(default_factory=dict)

    # Equity curve
    equity_curve: list = field(default_factory=list)


# ─── Black-Scholes (inline to avoid circular import) ─────────────────────────

def _norm_cdf(x):
    """Standard normal CDF using error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x):
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_price(S, K, T, r, sigma, option_type="put"):
    """Black-Scholes European option price."""
    if T <= 0 or sigma <= 0:
        # At expiry: intrinsic value
        if option_type == "put":
            return max(K - S, 0)
        else:
            return max(S - K, 0)

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "put":
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    else:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _kelly_fraction(win_prob, win_amount, loss_amount, fraction=0.25):
    """Fractional Kelly criterion. No external deps."""
    if loss_amount <= 0 or win_amount <= 0:
        return 0.0
    b = win_amount / loss_amount
    q = 1 - win_prob
    kelly = (win_prob * b - q) / b
    if kelly <= 0:
        return 0.0
    return kelly * fraction


def _realised_vol(closes, window=30):
    """
    Trailing realised volatility (annualised) from close prices.
    Uses log returns.
    """
    if len(closes) < window + 1:
        return 0.20  # Default 20% if not enough data

    log_rets = [math.log(closes[i] / closes[i-1])
                for i in range(-window, 0) if closes[i-1] > 0]
    if not log_rets:
        return 0.20

    std = np.std(log_rets)
    return std * math.sqrt(252)  # Annualise


# ─── Backtester ──────────────────────────────────────────────────────────────

class OptionsBacktester:
    """
    Backtest IBS-timed credit spread strategies on historical data.

    Uses yfinance data for the underlying, Black-Scholes for option pricing,
    and configurable IG spread costs.
    """

    # IG option spread costs — FIXED POINTS per leg, NOT percentage.
    #
    # Calibrated from LIVE IG data (2026-02-27 probe mode):
    #   US 500 (SPX) Wednesday weeklies:
    #     - 1.2 pts flat on most options (ATM through 2% OTM)
    #     - 0.68 pts on very cheap far-OTM options (bid < 10)
    #     - 0.5 pts on near-worthless options (bid < 2)
    #   US Tech 100 (NAS) Monday weeklies:
    #     - 8.0 pts flat (only one data point so far)
    #
    # For a credit spread we pay the spread on 2 legs at entry.
    # At expiry (both OTM): no exit cost. Early close: 2 more legs.
    #
    # IMPORTANT: This is the dominant cost. A 2-leg entry on SPX costs
    # 2 × 1.2 = 2.4 pts. On a credit of ~7 pts that's 34% drag.
    # Wider spreads collect more premium but same 2.4pt fixed cost.
    # IG option spread costs — FIXED POINTS per leg on the IG INDEX.
    # We backtest with yfinance ETFs (SPY, QQQ, etc.) which trade at
    # different price scales to the IG indices.
    # PRICE_SCALE = IG index price / yfinance ETF price.
    # E.g. SPX ~5800 / SPY ~580 = 10x.
    # We divide IG costs by this scale to get ETF-equivalent costs.
    # IG option costs are in INDEX POINTS. We backtest with yfinance ETFs.
    # PRICE_SCALE = IG index / yfinance ETF price.
    # IG_SPREAD_INDEX = fixed IG bid/offer in index points per leg.
    # Cost per trade (ETF) = IG spread per leg / PRICE_SCALE × 2 legs.
    #
    # CONFIRMED from live IG data: SPX weekly = 1.2pts/leg.
    # Others: estimated as % of index price (0.02% = typical for liquid markets).
    # Mark with * = needs live calibration via fetch_option_prices.py --probe.

    PRICE_SCALE = {
        # US equity indices
        "SPY": 10.0,      # SPX ~5800 / SPY ~580
        "QQQ": 50.0,      # NAS100 ~20000 / QQQ ~400
        "DIA": 100.0,     # DJIA ~40000 / DIA ~400
        "IWM": 10.0,      # Russell 2000 ~2000 / IWM ~200
        # European indices
        "EWG": 600.0,     # DAX ~18000 / EWG ~30
        "EWU": 230.0,     # FTSE ~8000 / EWU ~35
        "EWQ": 180.0,     # CAC40 ~7500 / EWQ ~42 (rough)
        # Asia-Pacific
        "EWJ": 400.0,     # Nikkei ~38000 / EWJ ~95 (rough)
        "EWA": 30.0,      # ASX200 ~8000 / EWA ~27 (rough)
        "EWH": 700.0,     # Hang Seng ~20000 / EWH ~28 (rough)
        # Bonds — IG trades these as interest rate markets
        # TLT ~$90, IG "US T-Bond" ~120 (futures price)
        "TLT": 1.3,       # US 20+ yr Treasury bond
        "IEF": 1.1,       # US 7-10yr Treasury
        "LQD": 1.0,       # Corp bonds — direct scale (no IG equivalent, use default)
        # Commodities — IG trades at futures price level
        "GLD": 14.0,      # Gold ~$2600/oz, GLD ~$185 → ~14×
        "SLV": 1.1,       # Silver ~$30, SLV ~$27 → ~1.1×
        "USO": 1.0,       # Oil WTI ~$70, USO ~$70 → ~1×
        "GC=F": 1.0,
        "CL=F": 1.0,
        "SI=F": 1.0,
        # Crypto — IG trades BTC at actual BTC price level
        "BITO": 4000.0,   # BTC ~$100k, BITO ~$25 → ~4000×
        "ETHA": 1000.0,   # ETH ~$3500, ETHA ~$3.50 → ~1000× (rough)
    }

    IG_OPTION_SPREAD_INDEX = {
        # CONFIRMED: SPX Wed weeklies = 1.2pts/leg
        "SPY":  {"daily": 1.0, "weekly": 1.2, "monthly": 1.2},
        # * ESTIMATED: ~0.04% of index for less liquid markets
        "QQQ":  {"daily": 6.0, "weekly": 8.0, "monthly": 8.0},
        "DIA":  {"daily": 8.0, "weekly": 10.0, "monthly": 10.0},
        "IWM":  {"daily": 0.8, "weekly": 1.0, "monthly": 1.0},
        "EWG":  {"daily": 3.0, "weekly": 4.0, "monthly": 4.0},
        "EWU":  {"daily": 2.0, "weekly": 3.0, "monthly": 3.0},
        "EWQ":  {"daily": 2.0, "weekly": 3.0, "monthly": 3.0},
        "EWJ":  {"daily": 8.0, "weekly": 12.0, "monthly": 12.0},
        "EWA":  {"daily": 2.0, "weekly": 3.0, "monthly": 3.0},
        "EWH":  {"daily": 5.0, "weekly": 8.0, "monthly": 8.0},
        # Bonds — IG typically 0.03-0.05% of notional
        "TLT":  {"daily": 0.03, "weekly": 0.04, "monthly": 0.04},
        "IEF":  {"daily": 0.02, "weekly": 0.03, "monthly": 0.03},
        "LQD":  {"daily": 0.03, "weekly": 0.04, "monthly": 0.04},
        # Commodities
        "GLD":  {"daily": 0.5, "weekly": 0.8, "monthly": 0.8},    # Gold ~$2600, 0.03%
        "SLV":  {"daily": 0.01, "weekly": 0.02, "monthly": 0.02},  # Silver ~$30, wider %
        "USO":  {"daily": 0.02, "weekly": 0.03, "monthly": 0.03},  # Oil ~$70
        "GC=F": {"daily": 1.0, "weekly": 1.5, "monthly": 1.5},
        "CL=F": {"daily": 0.3, "weekly": 0.5, "monthly": 0.5},
        "SI=F": {"daily": 0.05, "weekly": 0.08, "monthly": 0.08},
        # Crypto — IG has WIDE spreads on crypto options (~0.1% of notional)
        "BITO": {"daily": 50, "weekly": 80, "monthly": 80},    # BTC ~$100k, ~0.08%
        "ETHA": {"daily": 2.0, "weekly": 3.5, "monthly": 3.5}, # ETH ~$3500, ~0.1%
    }
    # Fallback: estimate as 0.02% of underlying price per leg
    DEFAULT_SPREAD_PCT_OF_UNDERLYING = 0.0002

    # Volatility Risk Premium: IV typically exceeds RV by this ratio.
    # VIX averages ~18-20% while SPX 30-day RV averages ~14-15%.
    # Ratio = ~1.30. Previous value of 1.15 was underpricing options.
    VRP_MULTIPLIER = 1.30  # IV = RV × 1.30 (calibrated from VIX/RV historical ratio)

    def __init__(self, data_provider):
        """
        Args:
            data_provider: Object with get_daily_bars(ticker) method
                           returning DataFrame with columns: Open, High, Low, Close
        """
        self.data = data_provider

    # Calibration: IG actual / BS predicted premium ratios.
    # Loaded from calibration.json (generated by calibrate_bs_vs_ig.py).
    # Applied as a multiplier to BS-computed premiums.
    # ratio < 1.0 = IG is cheaper → reduce backtest premiums.
    # ratio > 1.0 = IG is richer → increase backtest premiums.
    _calibration = None

    @classmethod
    def load_calibration(cls, filepath=None):
        """Load calibration ratios from calibrate_bs_vs_ig.py output."""
        import json
        if filepath is None:
            filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                     "calibration.json")
        try:
            with open(filepath) as f:
                data = json.load(f)
            cls._calibration = data.get("per_market", {})
            print(f"  Loaded calibration from {filepath}")
            for ticker, cal in cls._calibration.items():
                if ticker.startswith("_"):
                    continue
                ratio = cal.get("strategy_ratio", cal.get("avg_ratio", 1.0))
                print(f"    {ticker}: premium adjustment = {ratio:.2f}x")
            return True
        except FileNotFoundError:
            return False

    def _get_calibration_ratio(self, ticker):
        """Get the calibration ratio for a ticker (1.0 = no adjustment)."""
        if not self._calibration:
            return 1.0
        cal = self._calibration.get(ticker, {})
        return cal.get("strategy_ratio", cal.get("avg_ratio", 1.0))

    def run(self, tickers: list, params: dict = None,
            lookback_years: int = 3, equity: float = 10000,
            cost_mode: str = "realistic", verbose: bool = False) -> OptionsBacktestResult:
        """
        Run the options backtest across multiple tickers.

        Args:
            tickers: List of yfinance tickers
            params: Strategy parameters (merged with defaults)
            lookback_years: Years of history to test
            equity: Starting equity (for position sizing)
            cost_mode: "realistic" (estimated IG spreads), "zero" (no costs), "double" (stress test)

        Returns:
            OptionsBacktestResult
        """
        from strategies.ibs_credit_spreads import (
            DEFAULT_PARAMS, generate_signal, OptionPosition
        )

        p = {**DEFAULT_PARAMS, **(params or {})}

        all_trades = []
        equity_curve = [equity]
        current_equity = equity

        # Get VIX data
        vix_df = self.data.get_daily_bars("^VIX")
        vix_closes = {}
        if vix_df is not None and len(vix_df) > 0:
            for idx, row in vix_df.iterrows():
                date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                vix_closes[date_str] = row["Close"]

        for ticker in tickers:
            df = self.data.get_daily_bars(ticker)
            if df is None or len(df) < 252:
                print(f"  {ticker}: insufficient data, skipping")
                continue

            # Convert to list of bars
            bars = []
            for idx, row in df.iterrows():
                date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                bars.append({
                    "date": date_str,
                    "open": row["Open"],
                    "high": row["High"],
                    "low": row["Low"],
                    "close": row["Close"],
                })

            # Trim to lookback period
            if lookback_years:
                bars = bars[-(lookback_years * 252):]

            # EMA 200
            closes_all = [b["close"] for b in bars]
            ema = self._compute_ema(closes_all, p["ema_period"])

            # IG spread costs for this ticker — convert INDEX points to ETF points
            scale = self.PRICE_SCALE.get(ticker, 1.0)
            ig_index_spreads = self.IG_OPTION_SPREAD_INDEX.get(ticker, None)
            if ig_index_spreads is not None:
                # Scale down: 1.2 SPX pts / 10 = 0.12 SPY pts per leg
                ig_fixed_spreads = {k: v / scale for k, v in ig_index_spreads.items()}
            else:
                # Fallback: estimate from underlying price
                est_spread = bars[-1]["close"] * self.DEFAULT_SPREAD_PCT_OF_UNDERLYING
                ig_fixed_spreads = {"daily": est_spread, "weekly": est_spread * 1.2, "monthly": est_spread * 1.2}

            # Simulation
            position = None
            position_obj = None

            for i in range(max(p["ema_period"], 30), len(bars)):
                bar = bars[i]
                prev_bars = bars[max(0, i-10):i]
                vix = vix_closes.get(bar["date"], 20.0)
                ema_val = ema[i] if i < len(ema) else None

                # Realised vol from trailing closes
                trail_closes = closes_all[max(0, i-30):i+1]
                rv = _realised_vol(trail_closes, window=30)

                # Implied vol = RV × VRP multiplier
                iv = rv * self.VRP_MULTIPLIER

                signal = generate_signal(
                    bar=bar, prev_bars=prev_bars, position=position_obj,
                    params=p, vix=vix, ema200=ema_val
                )

                if signal.action == "open_put_spread" and position_obj is None:
                    # IV floor: skip in dead-vol environments
                    min_iv = p.get("min_iv", 0)
                    if min_iv and iv < min_iv:
                        if verbose:
                            print(f"    SKIP {bar['date']} put_spread: IV={iv:.1%} < floor {min_iv:.0%}")
                        continue

                    # Price the put spread using BS
                    T = p["expiry_days"] / 365.0
                    short_put_price = _bs_price(bar["close"], signal.short_strike, T, 0.05, iv, "put")
                    long_put_price = _bs_price(bar["close"], signal.long_strike, T, 0.05, iv, "put")
                    premium = short_put_price - long_put_price

                    # Apply calibration adjustment if available
                    # (ratio < 1 = IG cheaper than BS → reduce premium)
                    cal_ratio = self._get_calibration_ratio(ticker)
                    if cal_ratio != 1.0:
                        premium *= cal_ratio

                    spread_width = signal.short_strike - signal.long_strike

                    # Min credit check
                    if premium < spread_width * p["min_credit_pct"] / 100:
                        if verbose:
                            print(f"    SKIP {bar['date']} put_spread: premium={premium:.3f} "
                                  f"< min_credit ({p['min_credit_pct']:.0f}% of {spread_width:.1f} = "
                                  f"{spread_width * p['min_credit_pct'] / 100:.3f}), IV={iv:.1%}")
                        continue

                    # IG spread cost (entry: 2 legs × fixed spread per leg)
                    # From live calibration: SPX weekly = 1.2 pts per leg flat
                    expiry_key = "weekly" if p["expiry_days"] <= 7 else "monthly"
                    spread_per_leg = ig_fixed_spreads.get(expiry_key, ig_fixed_spreads.get("weekly", 0.12))
                    ig_cost = spread_per_leg * 2  # 2 legs at entry

                    if cost_mode == "zero":
                        ig_cost = 0.0
                    elif cost_mode == "double":
                        ig_cost *= 2

                    net_premium = premium - ig_cost
                    if net_premium <= 0:
                        if verbose:
                            print(f"    SKIP {bar['date']} put_spread: premium={premium:.3f} <= ig_cost={ig_cost:.3f}")
                        continue  # Cost eats all premium

                    max_loss = spread_width - net_premium

                    if verbose:
                        print(f"    OPEN {bar['date']} put_spread: S={bar['close']:.1f} "
                              f"short={signal.short_strike} long={signal.long_strike} "
                              f"width={spread_width:.1f} prem={premium:.3f} "
                              f"ig={ig_cost:.3f} net={net_premium:.3f} IV={iv:.1%}")

                    # Position sizing: flat % of equity at risk per trade.
                    #
                    # NOTE: Kelly criterion doesn't work for credit spreads because
                    # it uses BS-theoretical win prob (~63%) not the actual empirical
                    # win rate (~94%). With 8:1 adverse risk/reward, Kelly returns 0.
                    # We use flat risk sizing instead: risk X% of equity per trade,
                    # where contracts = risk_amount / max_loss_per_contract.
                    risk_pct = p["max_risk_pct"] / 100
                    risk_amount = current_equity * risk_pct
                    contracts = max(int(risk_amount / max(max_loss, 1)), 1)

                    position_obj = OptionPosition(
                        trade_type="put_spread",
                        entry_date=bar["date"],
                        entry_price=bar["close"],
                        short_strike=signal.short_strike,
                        long_strike=signal.long_strike,
                        premium_collected=premium,
                        spread_width=spread_width,
                        max_loss=max_loss,
                        contracts=contracts,
                        days_to_expiry=p["expiry_days"],
                    )
                    # Store metadata for the trade record
                    position_obj._iv = iv
                    position_obj._rv = rv
                    position_obj._ibs = signal.ibs
                    position_obj._vix = vix
                    position_obj._ig_cost = ig_cost
                    position_obj._net_premium = net_premium

                elif signal.action == "open_iron_condor" and position_obj is None:
                    # IV floor
                    min_iv = p.get("min_iv", 0)
                    if min_iv and iv < min_iv:
                        if verbose:
                            print(f"    SKIP {bar['date']} iron_condor: IV={iv:.1%} < floor {min_iv:.0%}")
                        continue

                    # Price both sides
                    T = p["expiry_days"] / 365.0
                    put_short_price = _bs_price(bar["close"], signal.short_put, T, 0.05, iv, "put")
                    put_long_price = _bs_price(bar["close"], signal.long_put, T, 0.05, iv, "put")
                    call_short_price = _bs_price(bar["close"], signal.short_call, T, 0.05, iv, "call")
                    call_long_price = _bs_price(bar["close"], signal.long_call, T, 0.05, iv, "call")

                    put_credit = put_short_price - put_long_price
                    call_credit = call_short_price - call_long_price
                    total_premium = put_credit + call_credit
                    put_width = signal.short_put - signal.long_put
                    call_width = signal.long_call - signal.short_call

                    # IG cost: 4 legs at open (fixed spread per leg)
                    expiry_key = "weekly" if p["expiry_days"] <= 7 else "monthly"
                    spread_per_leg = ig_fixed_spreads.get(expiry_key, ig_fixed_spreads.get("weekly", 0.12))
                    ig_cost = spread_per_leg * 4  # 4 legs for iron condor

                    if cost_mode == "zero":
                        ig_cost = 0.0
                    elif cost_mode == "double":
                        ig_cost *= 2

                    net_premium = total_premium - ig_cost
                    if net_premium <= 0:
                        continue

                    max_loss = max(put_width, call_width) - net_premium

                    # Flat risk sizing (same rationale as put spreads — Kelly
                    # returns 0 for credit spreads due to adverse risk/reward)
                    risk_pct = p["max_risk_pct"] / 100
                    risk_amount = current_equity * risk_pct
                    contracts = max(int(risk_amount / max(max_loss, 1)), 1)

                    position_obj = OptionPosition(
                        trade_type="iron_condor",
                        entry_date=bar["date"],
                        entry_price=bar["close"],
                        short_put=signal.short_put,
                        long_put=signal.long_put,
                        short_call=signal.short_call,
                        long_call=signal.long_call,
                        premium_collected=total_premium,
                        spread_width=max(put_width, call_width),
                        max_loss=max_loss,
                        contracts=contracts,
                        days_to_expiry=p["expiry_days"],
                    )
                    position_obj._iv = iv
                    position_obj._rv = rv
                    position_obj._ibs = signal.ibs
                    position_obj._vix = vix
                    position_obj._ig_cost = ig_cost
                    position_obj._net_premium = net_premium
                    position_obj._put_premium = put_credit
                    position_obj._call_premium = call_credit

                elif signal.action == "close" and position_obj is not None:
                    # Smart early exit: only close early if profit after exit
                    # cost is worth it. For "expiry (max bars)" always close.
                    is_early = signal.reason not in ("expiry (max bars)", "end_of_backtest")
                    if is_early and cost_mode == "realistic":
                        # Preview P&L: what would we make if we close now?
                        preview_pnl = self._preview_close_pnl(
                            position_obj, bar, ig_fixed_spreads, p
                        )
                        # Only close early if net profit after exit cost > 0
                        # Otherwise hold to expiry and avoid the exit spread cost
                        if preview_pnl <= 0:
                            if verbose:
                                print(f"    HOLD {bar['date']}: early close would lose "
                                      f"{preview_pnl:.3f}pts after exit cost, holding")
                            signal.action = "hold"
                        else:
                            if verbose:
                                print(f"    EARLY CLOSE {bar['date']}: net {preview_pnl:.3f}pts "
                                      f"({signal.reason})")

                    if signal.action == "close":
                        trade = self._close_position(
                            position_obj, bar, signal.reason, ticker, ig_fixed_spreads,
                            cost_mode, p, iv
                        )
                        all_trades.append(trade)
                        current_equity += trade.pnl
                        equity_curve.append(current_equity)
                        position_obj = None

                elif signal.action == "hold" and position_obj is not None:
                    # Check if expired (bars_held >= max_hold_bars already handled in signal)
                    pass

            # Close any remaining position
            if position_obj is not None and len(bars) > 0:
                trade = self._close_position(
                    position_obj, bars[-1], "end_of_backtest", ticker, ig_fixed_spreads,
                    cost_mode, p, rv
                )
                all_trades.append(trade)
                current_equity += trade.pnl
                equity_curve.append(current_equity)

        # Compile results
        return self._compile_result(all_trades, equity_curve, tickers, p, equity)

    def _preview_close_pnl(self, pos, bar, ig_fixed_spreads, params):
        """Preview P&L of closing early, INCLUDING exit spread cost."""
        price = bar["close"]
        if pos.trade_type == "put_spread":
            if price >= pos.short_strike:
                raw_pnl = pos._net_premium
            elif price <= pos.long_strike:
                raw_pnl = pos._net_premium - pos.spread_width
            else:
                raw_pnl = pos._net_premium - (pos.short_strike - price)
        else:
            # Iron condor — just return 0, let it close
            return 0.0

        # Subtract exit spread cost
        expiry_key = "weekly" if pos.days_to_expiry <= 7 else "monthly"
        spread_per_leg = ig_fixed_spreads.get(expiry_key, ig_fixed_spreads.get("weekly", 0.12))
        exit_cost = spread_per_leg * 2
        return raw_pnl - exit_cost

    def _close_position(self, pos, bar, reason, ticker, ig_fixed_spreads, cost_mode, params, current_vol=0.20):
        """Close an option position and calculate P&L."""
        price = bar["close"]

        if pos.trade_type == "put_spread":
            # P&L at current price (simulating expiry)
            if price >= pos.short_strike:
                # Both OTM → full profit
                raw_pnl = pos._net_premium
            elif price <= pos.long_strike:
                # Max loss
                raw_pnl = pos._net_premium - pos.spread_width
            else:
                # Partial loss
                raw_pnl = pos._net_premium - (pos.short_strike - price)

            # If closing early (not at expiry), add exit spread cost
            # Fixed spread per leg × 2 legs
            if reason not in ("expiry (max bars)", "end_of_backtest"):
                expiry_key = "weekly" if pos.days_to_expiry <= 7 else "monthly"
                spread_per_leg = ig_fixed_spreads.get(expiry_key, ig_fixed_spreads.get("weekly", 0.12))
                exit_cost = spread_per_leg * 2  # 2 legs to close
                if cost_mode == "double":
                    exit_cost *= 2
                elif cost_mode == "zero":
                    exit_cost = 0
                raw_pnl -= exit_cost

            # Scale by contracts (= £/pt stake on IG)
            n = pos.contracts
            trade = OptionTrade(
                ticker=ticker,
                trade_type="put_spread",
                entry_date=pos.entry_date,
                exit_date=bar["date"],
                underlying_entry=pos.entry_price,
                underlying_exit=price,
                short_strike=pos.short_strike,
                long_strike=pos.long_strike,
                spread_width=pos.spread_width,
                premium_collected=pos.premium_collected * n,
                ig_spread_cost=pos._ig_cost * n,
                net_premium=pos._net_premium * n,
                pnl=raw_pnl * n,
                contracts=n,
                max_loss=pos.max_loss * n,
                bars_held=pos.bars_held,
                exit_reason=reason,
                implied_vol=pos._iv,
                realised_vol=pos._rv,
                ibs_at_entry=pos._ibs,
                vix_at_entry=pos._vix,
            )

        elif pos.trade_type == "iron_condor":
            # Put side P&L
            if price >= pos.short_put:
                put_pnl = pos._put_premium
            elif price <= pos.long_put:
                put_width = pos.short_put - pos.long_put
                put_pnl = pos._put_premium - put_width
            else:
                put_pnl = pos._put_premium - (pos.short_put - price)

            # Call side P&L
            if price <= pos.short_call:
                call_pnl = pos._call_premium
            elif price >= pos.long_call:
                call_width = pos.long_call - pos.short_call
                call_pnl = pos._call_premium - call_width
            else:
                call_pnl = pos._call_premium - (price - pos.short_call)

            raw_pnl = put_pnl + call_pnl - pos._ig_cost

            # Early close exit cost: fixed spread × 4 legs
            if reason not in ("expiry (max bars)", "end_of_backtest"):
                expiry_key = "weekly" if pos.days_to_expiry <= 7 else "monthly"
                spread_per_leg = ig_fixed_spreads.get(expiry_key, ig_fixed_spreads.get("weekly", 0.12))
                exit_cost = spread_per_leg * 4  # 4 legs to close iron condor
                if cost_mode == "double":
                    exit_cost *= 2
                elif cost_mode == "zero":
                    exit_cost = 0
                raw_pnl -= exit_cost

            n = pos.contracts
            trade = OptionTrade(
                ticker=ticker,
                trade_type="iron_condor",
                entry_date=pos.entry_date,
                exit_date=bar["date"],
                underlying_entry=pos.entry_price,
                underlying_exit=price,
                short_put=pos.short_put,
                long_put=pos.long_put,
                short_call=pos.short_call,
                long_call=pos.long_call,
                spread_width=pos.spread_width,
                premium_collected=pos.premium_collected * n,
                ig_spread_cost=pos._ig_cost * n,
                net_premium=pos._net_premium * n,
                pnl=raw_pnl * n,
                contracts=n,
                max_loss=pos.max_loss * n,
                bars_held=pos.bars_held,
                exit_reason=reason,
                implied_vol=pos._iv,
                realised_vol=pos._rv,
                ibs_at_entry=pos._ibs,
                vix_at_entry=pos._vix,
                put_premium=pos._put_premium * n,
                call_premium=pos._call_premium * n,
            )
        else:
            trade = OptionTrade(
                ticker=ticker, trade_type=pos.trade_type,
                pnl=0, exit_reason=reason
            )

        return trade

    def _compute_ema(self, closes, period):
        """Compute exponential moving average."""
        ema = [0.0] * len(closes)
        if len(closes) < period:
            return ema
        # Seed with SMA
        ema[period - 1] = sum(closes[:period]) / period
        k = 2.0 / (period + 1)
        for i in range(period, len(closes)):
            ema[i] = closes[i] * k + ema[i-1] * (1 - k)
        return ema

    def _compile_result(self, trades, equity_curve, tickers, params, initial_equity):
        """Compile trades into aggregate results."""
        result = OptionsBacktestResult(
            strategy="IBS Credit Spreads",
            tickers=tickers,
            params=params,
            trades=trades,
            equity_curve=equity_curve,
        )

        if not trades:
            return result

        result.total_trades = len(trades)
        result.wins = sum(1 for t in trades if t.pnl > 0)
        result.losses = sum(1 for t in trades if t.pnl <= 0)
        result.win_rate = result.wins / result.total_trades if result.total_trades > 0 else 0

        pnls = [t.pnl for t in trades]
        result.gross_premium = sum(t.premium_collected for t in trades)
        result.total_ig_spread_cost = sum(t.ig_spread_cost for t in trades)
        result.net_pnl = sum(pnls)
        result.avg_pnl_per_trade = result.net_pnl / result.total_trades

        # Profit factor
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Sharpe (annualised, assuming weekly trades)
        if len(pnls) > 1:
            pnl_std = np.std(pnls)
            if pnl_std > 0:
                trades_per_year = min(len(pnls), 52)  # ~weekly
                result.sharpe = (np.mean(pnls) / pnl_std) * math.sqrt(trades_per_year)

        # Max drawdown
        peak = initial_equity
        max_dd = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = max_dd

        result.avg_bars_held = sum(t.bars_held for t in trades) / len(trades)

        # Vol metrics
        ivs = [t.implied_vol for t in trades if t.implied_vol > 0]
        rvs = [t.realised_vol for t in trades if t.realised_vol > 0]
        result.avg_implied_vol = np.mean(ivs) if ivs else 0
        result.avg_realised_vol = np.mean(rvs) if rvs else 0
        result.vrp = result.avg_implied_vol - result.avg_realised_vol

        # Premium as % of spread width
        prem_pcts = [t.net_premium / t.spread_width * 100
                     for t in trades if t.spread_width > 0]
        result.avg_premium_pct = np.mean(prem_pcts) if prem_pcts else 0

        # Per-market stats
        from collections import defaultdict
        by_market = defaultdict(list)
        for t in trades:
            by_market[t.ticker].append(t)

        for ticker, market_trades in by_market.items():
            mt_pnls = [t.pnl for t in market_trades]
            mt_wins = sum(1 for p in mt_pnls if p > 0)
            mt_gross_profit = sum(p for p in mt_pnls if p > 0)
            mt_gross_loss = abs(sum(p for p in mt_pnls if p < 0))

            result.stats_by_market[ticker] = {
                "trades": len(market_trades),
                "wins": mt_wins,
                "win_rate": mt_wins / len(market_trades) if market_trades else 0,
                "net_pnl": sum(mt_pnls),
                "gross_premium": sum(t.premium_collected for t in market_trades),
                "ig_spread_cost": sum(t.ig_spread_cost for t in market_trades),
                "profit_factor": mt_gross_profit / mt_gross_loss if mt_gross_loss > 0 else float('inf'),
                "avg_pnl": np.mean(mt_pnls) if mt_pnls else 0,
                "avg_bars": np.mean([t.bars_held for t in market_trades]),
                "avg_iv": np.mean([t.implied_vol for t in market_trades if t.implied_vol > 0]),
                "avg_rv": np.mean([t.realised_vol for t in market_trades if t.realised_vol > 0]),
                "put_spreads": sum(1 for t in market_trades if t.trade_type == "put_spread"),
                "iron_condors": sum(1 for t in market_trades if t.trade_type == "iron_condor"),
            }

        # Period
        if trades:
            result.period = f"{trades[0].entry_date} to {trades[-1].exit_date}"

        return result

    def print_summary(self, result: OptionsBacktestResult):
        """Print a formatted summary of backtest results."""
        r = result
        print(f"\n{'='*70}")
        print(f"  OPTIONS BACKTEST: {r.strategy}")
        print(f"  Tickers: {', '.join(r.tickers)}")
        print(f"  Period: {r.period}")
        print(f"{'='*70}\n")

        print(f"  Total trades:     {r.total_trades}")
        print(f"  Wins/Losses:      {r.wins}/{r.losses}  ({r.win_rate:.0%} win rate)")
        print(f"  Net P&L:          £{r.net_pnl:+,.0f}")
        print(f"  Avg P&L/trade:    £{r.avg_pnl_per_trade:+,.1f}")
        print(f"  Profit Factor:    {r.profit_factor:.2f}")
        print(f"  Sharpe:           {r.sharpe:.2f}")
        print(f"  Max Drawdown:     {r.max_drawdown:.1%}")
        print(f"  Avg Bars Held:    {r.avg_bars_held:.1f}")
        print(f"\n  Gross Premium:    £{r.gross_premium:,.0f}")
        print(f"  IG Spread Cost:   £{r.total_ig_spread_cost:,.0f}")
        print(f"  Cost as %% of Gross: {r.total_ig_spread_cost / r.gross_premium * 100:.1f}%%"
              if r.gross_premium > 0 else "")
        print(f"\n  Avg Implied Vol:  {r.avg_implied_vol:.1%}")
        print(f"  Avg Realised Vol: {r.avg_realised_vol:.1%}")
        print(f"  VRP Edge:         {r.vrp:.1%}")
        print(f"  Avg Premium %%:    {r.avg_premium_pct:.1f}%% of spread width")

        if r.stats_by_market:
            print(f"\n  {'Market':<10} {'Trades':>7} {'Win%%':>6} {'Net PnL':>10} "
                  f"{'PF':>6} {'IG Cost':>8} {'Puts':>5} {'ICs':>4}")
            print(f"  {'-'*60}")
            for ticker, stats in sorted(r.stats_by_market.items()):
                print(f"  {ticker:<10} {stats['trades']:>7} "
                      f"{stats['win_rate']:>5.0%} "
                      f"{stats['net_pnl']:>10,.0f} "
                      f"{stats['profit_factor']:>6.2f} "
                      f"{stats['ig_spread_cost']:>8,.0f} "
                      f"{stats['put_spreads']:>5} "
                      f"{stats['iron_condors']:>4}")
