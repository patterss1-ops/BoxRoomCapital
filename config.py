"""
Trading Bot Configuration
All strategy parameters match Pine Script defaults exactly.
Broker: IG (spread betting via REST API)
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── BROKER CONFIG ───────────────────────────────────────────────────────────

BROKER_MODE = os.getenv("BROKER_MODE", "paper")  # "paper", "demo", "live"

# IG API credentials — get your API key from https://labs.ig.com/
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")
IG_API_KEY = os.getenv("IG_API_KEY", "")
IG_ACC_TYPE = os.getenv("IG_ACC_TYPE", "DEMO")  # "DEMO" or "LIVE"
IG_ACC_NUMBER = os.getenv("IG_ACC_NUMBER", "")   # Your spread bet account number

# ─── MARKET MAPPING ──────────────────────────────────────────────────────────
# yfinance ticker → IG EPIC code for spread betting
# EPICs are IG's unique market identifiers (verified via search_markets)
# Format: IX.D.{INDEX}.DAILY.IP for daily-funded spread bets

MARKET_MAP = {
    # ─── IBS++ markets (long only) ────────────────────────────────────────
    # Indices — verified via discover_epics.py 2026-02-26
    #
    # IMPORTANT: ig_price_level is the approximate IG instrument price level.
    # yfinance ETF proxies (SPY ~500, QQQ ~500) trade at different price levels
    # than the IG cash indices (US 500 ~5200, US Tech 100 ~18000). The backtester
    # uses this ratio to correctly scale spread/financing costs against yfinance P&L.
    #
    # product_type controls how the backtester calculates costs:
    #   "dfb"     — Daily Funded Bet: spread + daily overnight financing (indices, spot commodities)
    #   "future"  — Forward/futures: wider spread, NO overnight financing (embedded in forward price)
    #   "fx"      — FX spot: spread + tom-next roll (approximated as small daily rate)
    #   "com_spot"— Spot commodity: spread + basis/roll adjustment (different from rate-based funding)
    #
    "SPY":      {"epic": "IX.D.SPTRD.DAILY.IP",    "ig_name": "US 500",              "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "dfb", "ig_price_level": 5200,  "yf_approx_level": 520},
    "QQQ":      {"epic": "IX.D.NASDAQ.CASH.IP",     "ig_name": "US Tech 100",         "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "dfb", "ig_price_level": 18500, "yf_approx_level": 500},
    "IWM":      {"epic": "IX.D.RUSSELL.DAILY.IP",   "ig_name": "US Russell 2000",     "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "dfb", "ig_price_level": 2100,  "yf_approx_level": 210},
    "DIA":      {"epic": "IX.D.DOW.DAILY.IP",       "ig_name": "Wall Street",         "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "dfb", "ig_price_level": 39000, "yf_approx_level": 390},
    "EWU":      {"epic": "IX.D.FTSE.DAILY.IP",      "ig_name": "FTSE 100",            "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "dfb", "ig_price_level": 8400,  "yf_approx_level": 34},
    "EWG":      {"epic": "IX.D.DAX.DAILY.IP",       "ig_name": "Germany 40",          "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "dfb", "ig_price_level": 22500, "yf_approx_level": 34},
    "EWJ":      {"epic": "IX.D.NIKKEI.DAILY.IP",    "ig_name": "Japan 225",           "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "dfb", "ig_price_level": 38000, "yf_approx_level": 70},
    # Bonds — monthly futures (no DFB available for T-Notes on IG)
    "IEF":      {"epic": "IR.D.10YEAR100.Month2.IP", "ig_name": "10-Yr T-Note Dec",   "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "expiry": "monthly", "product_type": "future", "ig_price_level": 110, "yf_approx_level": 95},
    # Commodities — spot DFB on IG (uses basis/roll, not rate-based funding)
    "CL=F":     {"epic": "CC.D.CL.USS.IP",          "ig_name": "Oil - US Crude",      "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "com_spot", "ig_price_level": 75, "yf_approx_level": 75},
    # Forex — tom-next roll, not rate-based DFB funding
    "GBPUSD=X": {"epic": "CS.D.GBPUSD.TODAY.IP",    "ig_name": "GBP/USD",             "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "fx", "ig_price_level": 1.27, "yf_approx_level": 1.27},
    # Metals — spot commodity on IG
    "GC=F":     {"epic": "CS.D.USCGC.TODAY.IP",     "ig_name": "Spot Gold",           "strategy": "ibs", "direction": "long_only",  "currency": "GBP", "verified": True, "product_type": "com_spot", "ig_price_level": 2650, "yf_approx_level": 2650},
    # ─── Trend following markets (long + short) ───────────────────────────
    "SI=F":       {"epic": "CS.D.USCSI.TODAY.IP",   "ig_name": "Spot Silver",         "strategy": "trend", "direction": "both", "currency": "GBP", "verified": True, "product_type": "com_spot", "ig_price_level": 30, "yf_approx_level": 30},
    "GC=F_trend": {"epic": "CS.D.USCGC.TODAY.IP",   "ig_name": "Spot Gold",           "strategy": "trend", "direction": "both", "currency": "GBP", "verified": True, "product_type": "com_spot", "ig_price_level": 2650, "yf_approx_level": 2650},
    "CL=F_trend": {"epic": "CC.D.CL.USS.IP",        "ig_name": "Oil - US Crude",      "strategy": "trend", "direction": "both", "currency": "GBP", "verified": True, "product_type": "com_spot", "ig_price_level": 75, "yf_approx_level": 75},
    "NG=F":       {"epic": "CC.D.NG.USS.IP",        "ig_name": "Natural Gas",         "strategy": "trend", "direction": "both", "currency": "GBP", "verified": True, "product_type": "com_spot", "ig_price_level": 3.5, "yf_approx_level": 3.5},
    "HG=F":       {"epic": "CC.D.HG.USS.IP",        "ig_name": "High Grade Copper",   "strategy": "trend", "direction": "both", "currency": "GBP", "verified": True, "product_type": "com_spot", "ig_price_level": 4.2, "yf_approx_level": 4.2},
}

# Fallback EPICs if primary fails (broker.verify_markets() tries these automatically)
EPIC_ALTERNATIVES = {
    "IX.D.NASDAQ.CASH.IP":    ["IX.D.NASDAQ.IFD.IP", "IX.D.NASDAQ.IFA.IP"],
    "IR.D.10YEAR100.Month2.IP": ["IR.D.10YEAR100.Month1.IP"],
    "CC.D.CL.USS.IP":         ["CC.D.CL.UNC.IP"],
    "CC.D.NG.USS.IP":         ["CC.D.NG.UNC.IP"],
    "CC.D.HG.USS.IP":         ["CS.D.COPPER.TODAY.IP", "CC.D.HG.UNC.IP"],
}

# SPY/TLT rotation is handled separately (only 2 instruments)
ROTATION_TICKERS = {"primary": "SPY", "partner": "TLT"}

# ─── IBS++ v3 PARAMETERS ────────────────────────────────────────────────────
# Match Pine Script: IBS_Plus_Plus_v3.pine defaults

IBS_PARAMS = {
    "ibs_entry_thresh": 0.3,
    "ibs_exit_thresh": 0.7,
    "use_rsi_filter": True,
    "rsi_period": 2,
    "rsi_entry_thresh": 25.0,
    "rsi_exit_thresh": 65.0,
    "filter_mode": "Both",        # "Both" = IBS AND RSI (best PF)
    "use_down_days": False,
    "min_down_days": 2,
    "use_trend_filter": True,
    "ema_period": 200,
    # VIX regime filter (new in v3)
    # RATIONALE (post-review): Low VIX = calm trending market above EMA = ideal
    # for mean reversion (orderly pullbacks snap back). High VIX = volatile
    # whipsaws = riskier for mean reversion. Extreme VIX = crisis = skip.
    "use_vix_filter": True,
    "vix_low_thresh": 15.0,
    "vix_low_action": "Normal",       # Low VIX = best regime, full size
    "vix_high_thresh": 25.0,
    "vix_high_action": "Normal",      # Elevated but manageable
    "vix_extreme_thresh": 35.0,
    "vix_extreme_action": "Skip Trade",  # Crisis mode — sit out
    # Risk management
    "use_stop_loss": False,           # Stop loss OFF = better for mean reversion
    "stop_loss_pct": 3.0,
    "max_hold_bars": 7,
}

# ─── IBS SHORT (Bear Regime) PARAMETERS ────────────────────────────────────
# Short-side mean reversion: short when IBS is HIGH (overbought), cover when LOW.
# When you short a DFB, IG PAYS you overnight financing (SONIA - markup, ~2% net).
# This turns the financing headwind into a tailwind.
# Only active when VIX is elevated AND price is BELOW 200 EMA (bear regime).

IBS_SHORT_PARAMS = {
    "ibs_entry_thresh": 0.7,          # Short when IBS > 0.7 (overbought in downtrend)
    "ibs_exit_thresh": 0.3,           # Cover when IBS < 0.3 (oversold bounce)
    "use_rsi_filter": True,
    "rsi_period": 2,
    "rsi_entry_thresh": 75.0,         # Short when RSI(2) > 75
    "rsi_exit_thresh": 35.0,          # Cover when RSI(2) < 35
    "filter_mode": "Both",            # Both IBS AND RSI must confirm
    "use_down_days": False,
    "min_up_days": 2,                 # Consecutive up days (opposite of long side)
    "use_trend_filter": True,         # Must be BELOW 200 EMA (bear regime)
    "ema_period": 200,
    # VIX filter — opposite logic: we WANT elevated VIX for short-side
    "use_vix_filter": True,
    "vix_low_thresh": 20.0,           # Don't short in calm markets
    "vix_low_action": "Skip Trade",   # Low VIX = bull regime = no shorts
    "vix_high_thresh": 25.0,
    "vix_high_action": "Normal",      # Elevated VIX = good for shorts
    "vix_extreme_thresh": 45.0,
    "vix_extreme_action": "Skip Trade",  # Extreme = squeeze risk, sit out
    # Risk management
    "use_stop_loss": True,            # Stops are essential for shorts
    "stop_loss_pct": 3.0,
    "max_hold_bars": 5,               # Shorter holds than long side
}

# ─── TREND FOLLOWING v2 PARAMETERS ──────────────────────────────────────────
# Match Pine Script: Trend_Following_v2.pine defaults
# WARNING: Screener results show this strategy is NOT VIABLE on IG DFBs.
# Every single proven market returned NO. Multi-day holds on DFBs bleed
# overnight financing that destroys the edge. Either use monthly futures
# on a different broker (Interactive Brokers, Saxo) or drop entirely.
# Kept here for reference only.

TREND_PARAMS = {
    "entry_mode": "MA Crossover",     # "MA Crossover", "Donchian Breakout", "Both"
    "ma_type": "EMA",
    "fast_length": 20,
    "slow_length": 50,
    "donchian_entry": 20,
    "donchian_exit": 10,
    "allow_long": True,
    "allow_short": True,
    "use_adx_filter": True,
    "adx_period": 14,
    "adx_threshold": 20.0,
    "use_trailing_stop": True,
    "atr_period": 14,
    "atr_mult_stop": 2.5,
    "use_cooldown": True,
    "cooldown_bars": 3,
    "_deprecated": True,  # Flagged as not viable on IG DFBs
    "_deprecation_reason": "All markets returned NO in screener. Overnight financing destroys edge on multi-day holds.",
}

# ─── SPY/TLT ROTATION v3 PARAMETERS ─────────────────────────────────────────
# Match Pine Script: SPY_TLT_Rotation_v3.pine defaults

ROTATION_PARAMS = {
    "lookback_days": 21,
    "rebalance_day": 1,               # Trading day # of month
    "use_abs_momentum": True,
    "abs_mom_lookback": 126,           # ~6 months
    "use_cash_filter": True,
    "cash_ma_period": 200,             # 200 SMA
    # v4 enhancement: short the loser instead of going to cash
    # When partner (TLT) wins, short SPY. When primary (SPY) wins, short TLT.
    # Shorting DFBs earns overnight financing (~2% net), turning cost into income.
    "allow_short_loser": False,        # Set True for v4 (short loser variant)
}

# ─── GTAA TREND FOLLOWING PARAMETERS ──────────────────────────────────────
# Meb Faber (2007): monthly rebalance, SMA trend filter, multi-asset universe.
# Designed for IBKR ISA — long-only ETFs, zero financing, tax-free.

GTAA_PARAMS = {
    "sma_period": 200,                # 10-month SMA (~200 trading days)
    "rebalance_day": 1,               # Trading day of month to rebalance
    "universe": ["SPY", "EFA", "IEF", "VNQ", "DBC"],  # Faber 5-asset
    "weight_mode": "equal",           # Equal weight (1/N)
    "use_trend_filter": True,         # Only hold assets above SMA
}

# ─── DUAL MOMENTUM GEM PARAMETERS ────────────────────────────────────────
# Gary Antonacci (2014): relative + absolute momentum, 3-asset rotation.
# All-in on one asset at a time. Designed for IBKR ISA.

DUAL_MOMENTUM_PARAMS = {
    "lookback_days": 252,             # 12-month lookback
    "rebalance_day": 1,               # Trading day of month
    "us_equity": "SPY",               # US equities proxy
    "intl_equity": "EFA",             # International equities proxy
    "safe_haven": "AGG",              # Bonds / safe haven
    "abs_momentum_threshold": 0.0,    # Winner must have positive return
    "use_excess_return": False,       # Simple returns (not excess over T-bill)
    "risk_free_ticker": "BIL",        # T-bill proxy (if use_excess_return=True)
}

# ─── BACKTESTER: MARKET SUGGESTIONS PER STRATEGY ──────────────────────────
# "proven" = currently live on the bot, backtested & verified
# "candidates" = not yet tested but fit the strategy criteria — worth backtesting
#
# IBS++ works on: liquid, mean-reverting markets above 200 EMA. Indices & large
#   ETFs are ideal. Forex pairs mean-revert intraday. Commodities with
#   inventory-driven mean reversion (oil, gas, metals).
#
# Trend Following works on: markets with persistent directional moves.
#   Commodities are the classic trend-following universe. Forex crosses
#   trend well. Some indices trend during macro shifts.
#
# SPY/TLT Rotation works on: equity/bond pairs with inverse correlation.
#   Can generalise to other equity/safe-haven pairs.

BACKTEST_MARKETS = {
    # ─── VIABLE STRATEGY: IBS++ Long (proven on IG DFBs) ─────────────────────
    "IBS++ v3": {
        "proven": {
            # These 6 passed all viability filters in screener (realistic costs)
            "SPY":      "US 500 — PF=1.67, Sharpe=0.75, 19% cost drag",
            "QQQ":      "US Tech 100 — PF=1.86, Sharpe=0.91, 11% cost drag",
            "DIA":      "Dow Jones — PF=1.76, Sharpe=0.80, 14% cost drag",
            "EWG":      "DAX 40 — PF=1.83, Sharpe=0.75, 14% cost drag",
            "GC=F":     "Gold — PF=3.16, Sharpe=1.62, 6% cost drag (STAR)",
            "CL=F":     "Crude Oil — PF=2.20, Sharpe=0.85, 7% cost drag",
        },
        "candidates": {
            # Marginal in screener — could be promoted with tighter params or futures
            "GBPUSD=X": "GBP/USD — PF=5.16 but 41% win rate (few big winners)",
            "^IBEX":    "Spain IBEX 35 — PF=1.60 but -7% DD, candidate promoted",
            "^STOXX50E": "Euro Stoxx 50 — PF=1.41, 24% cost drag, marginal",
            "^AXJO":    "ASX 200 — PF=1.28, 31% cost drag, marginal",
            # Failed in screener — kept for reference
            "IWM":      "Russell 2000 — FAILED: PF=0.85, 493% cost drag",
            "EWU":      "FTSE 100 — FAILED: PF=1.07, 65% cost drag",
            "EWJ":      "Nikkei 225 — FAILED: PF=0.79, 165% cost drag",
            "IEF":      "10-Yr T-Note — FAILED: PF=1.09, 81% cost drag (already on futures!)",
            # Untested
            "^FCHI":    "CAC 40 — untested, similar to DAX",
        },
    },

    # ─── NEW STRATEGY: IBS++ Short (bear regime, earns financing) ────────────
    "IBS Short (Bear)": {
        "proven": {
            # Same markets as IBS Long — short side during bear regimes
            # Shorting DFBs EARNS overnight financing (~2% net pa)
            "SPY":      "US 500 short — overbought sells in downtrends",
            "QQQ":      "US Tech 100 short — higher beta = stronger shorts",
            "DIA":      "Dow Jones short — blue chip mean reversion",
            "EWG":      "DAX 40 short — European bear markets are sharp",
        },
        "candidates": {
            "IWM":      "Russell 2000 short — small caps fall hardest in bears",
            "EWU":      "FTSE 100 short — UK bear regimes",
            "EWJ":      "Nikkei short — yen strengthening bear markets",
            "^STOXX50E": "Euro Stoxx 50 short — EU crisis trades",
        },
    },

    # ─── KILLED: IBS++ Futures — IG DOES NOT OFFER INDEX FUTURES FOR SPREAD BETTING
    # Discovery confirmed: IG only has DFBs and OPTIONS for indices, no monthly futures.
    # All "IBS++ Futures" screener results are meaningless. Strategy deleted.

    # ─── NEW: IBS Credit Spreads (options-based, zero overnight financing) ─────
    # Combines IBS directional timing with credit put spread selling.
    # Zero overnight financing (cost is option bid/offer spread at entry).
    # Defined risk at entry (max loss = spread_width - premium).
    # Tax-free (spread betting on options).
    # Uses weekly options (5-day expiry) for fastest theta decay.
    # Iron condors when IBS is neutral + VIX is low.
    "IBS Credit Spreads": {
        "proven": {
            # Available on IG as weekly/daily/monthly options
            "SPY":      "US 500 — weekly put spreads timed by IBS oversold, zero financing",
            "QQQ":      "US Tech 100 — daily + weekly options available",
            "DIA":      "Wall Street — daily + weekly options",
            "EWG":      "Germany 40 — daily + weekly + monthly options",
            "EWU":      "FTSE 100 — weekly + monthly options",
        },
        "candidates": {
            "GC=F":     "Gold — options may be available (check IG)",
            "CL=F":     "Crude Oil — options may be available (check IG)",
        },
    },

    # ─── REWORKED: SPY/TLT Rotation with Short Leg ───────────────────────────
    "SPY/TLT Rotation v4": {
        "proven": {
            "SPY": "US 500 vs TLT — with short loser leg (earns financing)",
        },
        "candidates": {
            "QQQ":  "US Tech vs Bonds — higher beta rotation with short leg",
        },
    },

    # ─── DEPRECATED: Trend Following (not viable on IG DFBs) ─────────────────
    # Every single market returned NO in screener. Multi-day holds bleed
    # overnight financing. Kept for reference / future non-IG broker use.
    "Trend Following v2 [DEPRECATED]": {
        "proven": {},
        "candidates": {
            "SI=F":       "Silver — FAILED on IG DFBs",
            "GC=F_trend": "Gold — FAILED on IG DFBs",
            "CL=F_trend": "Oil — FAILED on IG DFBs",
            "NG=F":       "Nat Gas — FAILED on IG DFBs",
            "HG=F":       "Copper — FAILED on IG DFBs",
        },
    },

    # ─── LEGACY: Original SPY/TLT (cash-only, no short leg) ─────────────────
    "SPY/TLT Rotation v3": {
        "proven": {
            "SPY": "US 500 vs TLT — FAILED: PF=0.45, 446% cost drag",
        },
        "candidates": {},
    },
}

# ─── IBS CREDIT SPREADS PARAMETERS ─────────────────────────────────────────
# Options-based strategy: IBS timing + credit put spread selling
# Zero overnight financing. Defined risk. Tax-free.
# See strategies/ibs_credit_spreads.py for full documentation.

IBS_CREDIT_SPREAD_PARAMS = {
    # IBS entry/exit (same thresholds as IBS++ v3)
    "ibs_entry_thresh": 0.3,
    "ibs_exit_thresh": 0.7,
    "use_rsi_filter": True,
    "rsi_period": 2,
    "rsi_entry_thresh": 25.0,
    "rsi_exit_thresh": 65.0,
    "filter_mode": "Both",
    "use_trend_filter": True,
    "ema_period": 200,
    # VIX
    "use_vix_filter": True,
    "vix_extreme_thresh": 35.0,
    "vix_low_thresh": 15.0,
    # IV floor: skip low-vol environments
    "min_iv": 0.14,               # Don't trade if IV < 14%
    # Spread construction — OPTIMISED from parameter sweep (2026-02-27)
    # Best Sharpe (7.60): 1.0%/1.0%/10 DTE → +41pts, PF=11.28
    # Best Net (7.55):    1.0%/1.5%/10 DTE → +64pts, PF=10.88
    # Using best net config (Sharpe difference is negligible):
    "short_distance_pct": 1.0,    # Short strike 1% below (ATM-ish = max premium)
    "spread_width_pct": 1.5,      # 1.5% wide spreads (amortises fixed IG cost)
    "min_credit_pct": 3.0,        # Min premium = 3% of width
    "expiry_days": 10,            # 10 DTE (was 5): 2× premium for same IG cost
    # Iron condor — DISABLED (4 legs = double IG cost on thin premiums)
    "enable_iron_condor": False,
    "ic_ibs_low": 0.35,
    "ic_ibs_high": 0.65,
    "ic_short_distance_pct": 2.5,
    # Sizing
    "max_risk_pct": 2.0,
    "kelly_fraction": 0.25,
    # Smart early exit: IBS recovery close, filtered by backtester —
    # only closes if net P&L after exit spread cost > 0.
    "max_hold_bars": 10,          # Matches DTE
    "close_early_ibs": True,
    "close_early_pct": 999.0,
}

# ─── PORTFOLIO SETTINGS ─────────────────────────────────────────────────────

PORTFOLIO = {
    "initial_capital": 10000,
    "default_stake_per_point": 0.50,   # IG minimum for most markets
    "max_open_positions": 8,           # With £10k we can hold more
    "max_exposure_pct": 50,            # Conservative: max 50% of equity in margin
}

# ─── LIVE OPTIONS TRADING ──────────────────────────────────────────────────

# Trading mode: "shadow" = log signals but don't execute, "live" = real orders
TRADING_MODE = os.getenv("TRADING_MODE", "shadow")

# Markets selected for live trading (run: python3 run_options_backtest.py --portfolio --top 6 --calibrate)
# Update after running market selection with calibration applied
LIVE_TRADING_TICKERS = [
    "SPY",   # US 500 — best calibrated (ratio 1.17-1.56)
    "QQQ",   # US Tech 100 — ~25% optimistic in backtest
    "EWJ",   # Japan 225 — needs EU hours calibration
    "GLD",   # Gold — needs EU hours calibration
    "EWU",   # FTSE 100 — needs EU hours calibration
    "EWG",   # Germany 40 — wide spreads in calibration, watch closely
]

# IG option EPIC search patterns for each index
# Used by the bot to find option markets dynamically
OPTION_EPIC_PATTERNS = {
    "SPY": {"search": "US 500", "index": "SPX", "epic_prefix": "OP.D.SPX."},
    "QQQ": {"search": "US Tech 100", "index": "USTECH", "epic_prefix": "OP.D.USTECH."},
    "DIA": {"search": "Wall Street", "index": "WALL", "epic_prefix": "OP.D.WALL."},
    "EWG": {"search": "Germany 40", "index": "DAX", "epic_prefix": "OP.D.DAX."},
    "EWU": {"search": "FTSE 100", "index": "FTSE", "epic_prefix": "OP.D.FTSE."},
    "EWJ": {"search": "Japan 225", "index": "JP225", "epic_prefix": "OP.D.JP225."},
    "GLD": {"search": "Gold", "index": "GOLD", "epic_prefix": "OP.D.GOLD."},
}

# Safety limits for options trading
OPTIONS_SAFETY = {
    "max_risk_per_trade_pct": 2.0,    # 2% of equity per trade (£100 on £5k)
    "max_total_heat_pct": 4.0,        # 4% across all open spreads (£200)
    "max_daily_loss_pct": 10.0,       # Kill switch at 10% daily loss (£500)
    "max_open_spreads": 6,            # Max 6 simultaneous spreads
    "min_premium_pct": 2.0,           # Don't trade if premium < 2% of spread width
    "max_contracts_per_trade": 20,    # Hard cap
}

# ─── TRADINGVIEW WEBHOOK INTAKE ────────────────────────────────────────────

TRADINGVIEW_WEBHOOK_TOKEN = os.getenv("TRADINGVIEW_WEBHOOK_TOKEN", "")
TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES = int(
    os.getenv("TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES", "65536")
)

# ─── NOTIFICATIONS ───────────────────────────────────────────────────────────

NOTIFICATIONS = {
    "enabled": os.getenv("NOTIFICATIONS_ENABLED", "false").lower() == "true",
    "email_to": os.getenv("NOTIFY_EMAIL", ""),
    "telegram_token": os.getenv("TELEGRAM_TOKEN", ""),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
}

# ─── LOGGING ─────────────────────────────────────────────────────────────────

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("BOT_LOG_FILE", "trading_bot.log")
TRADE_LOG_FILE = "trades.csv"
