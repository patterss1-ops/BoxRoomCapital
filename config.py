"""
Trading Bot Configuration
All strategy parameters match Pine Script defaults exactly.
Broker: IG (spread betting via REST API)
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

_RUNTIME_DIR = Path(__file__).resolve().parent / ".runtime"
_SETTINGS_OVERRIDE_PATH = _RUNTIME_DIR / "settings_override.json"


def _load_runtime_overrides() -> dict:
    try:
        if _SETTINGS_OVERRIDE_PATH.exists():
            return json.loads(_SETTINGS_OVERRIDE_PATH.read_text())
    except Exception:
        pass
    return {}


_runtime_overrides = _load_runtime_overrides()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(
    name: str,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _env_float(
    name: str,
    default: float,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip() or default

# ─── BROKER CONFIG ───────────────────────────────────────────────────────────

BROKER_MODE = _runtime_overrides.get("broker_mode", os.getenv("BROKER_MODE", "paper"))

# IG API credentials — get your API key from https://labs.ig.com/
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")
IG_API_KEY = os.getenv("IG_API_KEY", "")
IG_ACC_TYPE = os.getenv("IG_ACC_TYPE", "DEMO")  # "DEMO" or "LIVE"
IG_ACC_NUMBER = os.getenv("IG_ACC_NUMBER", "")   # Your spread bet account number
IG_DEMO_USERNAME = os.getenv("IG_DEMO_USERNAME", "")
IG_DEMO_PASSWORD = os.getenv("IG_DEMO_PASSWORD", "")
IG_DEMO_API_KEY = os.getenv("IG_DEMO_API_KEY", "")
IG_DEMO_ACC_NUMBER = os.getenv("IG_DEMO_ACC_NUMBER", "")
IG_LIVE_USERNAME = os.getenv("IG_LIVE_USERNAME", "")
IG_LIVE_PASSWORD = os.getenv("IG_LIVE_PASSWORD", "")
IG_LIVE_API_KEY = os.getenv("IG_LIVE_API_KEY", "")
IG_LIVE_ACC_NUMBER = os.getenv("IG_LIVE_ACC_NUMBER", "")
IG_ATTACH_PROTECTIVE_STOPS = _env_bool("IG_ATTACH_PROTECTIVE_STOPS", False)
IG_PROTECTIVE_STOP_FACTOR = _env_float("IG_PROTECTIVE_STOP_FACTOR", 2.0, min_value=0.0, max_value=50.0)


def broker_mode() -> str:
    """Return the active broker mode after applying runtime overrides."""
    value = str(_load_runtime_overrides().get("broker_mode", BROKER_MODE) or "").strip().lower()
    return value if value in {"paper", "demo", "live"} else "paper"


def ig_broker_is_demo() -> bool:
    """Resolve whether the active IG target should use the demo environment."""
    broker_mode_value = broker_mode()
    if broker_mode_value == "demo":
        return True
    if broker_mode_value == "live":
        return False
    return str(IG_ACC_TYPE or "DEMO").strip().upper() != "LIVE"


def ig_credentials(is_demo: bool) -> dict[str, str]:
    """Return IG credentials for the requested environment."""
    legacy_is_demo = str(IG_ACC_TYPE or "DEMO").strip().upper() != "LIVE"
    if is_demo:
        return {
            "username": IG_DEMO_USERNAME or (IG_USERNAME if legacy_is_demo else ""),
            "password": IG_DEMO_PASSWORD or (IG_PASSWORD if legacy_is_demo else ""),
            "api_key": IG_DEMO_API_KEY or (IG_API_KEY if legacy_is_demo else ""),
            "account_number": IG_DEMO_ACC_NUMBER or (IG_ACC_NUMBER if legacy_is_demo else ""),
        }
    return {
        "username": IG_LIVE_USERNAME or IG_USERNAME,
        "password": IG_LIVE_PASSWORD or IG_PASSWORD,
        "api_key": IG_LIVE_API_KEY or IG_API_KEY,
        "account_number": IG_LIVE_ACC_NUMBER or IG_ACC_NUMBER,
    }


def ig_account_number(is_demo: bool) -> str:
    """Return the configured account number for the requested IG environment."""
    return str(ig_credentials(is_demo).get("account_number") or "")


def ig_credentials_available(is_demo: bool) -> bool:
    """Return True when the requested IG environment has enough auth to connect."""
    creds = ig_credentials(is_demo)
    return bool(creds["username"] and creds["password"] and creds["api_key"])

# PostgreSQL research database
RESEARCH_DB_DSN = os.getenv("RESEARCH_DB_DSN") or os.getenv("DATABASE_URL") or "postgresql://localhost:5432/boxroom_research"

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
    "ibs_entry_thresh": _runtime_overrides.get("ibs_entry_thresh", 0.3),
    "ibs_exit_thresh": _runtime_overrides.get("ibs_exit_thresh", 0.7),
    "use_rsi_filter": _runtime_overrides.get("ibs_use_rsi_filter", True),
    "rsi_period": _runtime_overrides.get("ibs_rsi_period", 2),
    "rsi_entry_thresh": _runtime_overrides.get("ibs_rsi_entry_thresh", 25.0),
    "rsi_exit_thresh": _runtime_overrides.get("ibs_rsi_exit_thresh", 65.0),
    "filter_mode": "Both",        # "Both" = IBS AND RSI (best PF)
    "use_down_days": False,
    "min_down_days": 2,
    "use_trend_filter": True,
    "ema_period": _runtime_overrides.get("ibs_ema_period", 200),
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

# ─── DEFAULT STRATEGY KEY ─────────────────────────────────────────────────
DEFAULT_STRATEGY_KEY = "ibs_credit_spreads"

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
    "initial_capital": _runtime_overrides.get("portfolio_initial_capital", 10000),
    "default_stake_per_point": _runtime_overrides.get("portfolio_default_stake", 0.50),
    "max_open_positions": _runtime_overrides.get("portfolio_max_positions", 8),
    "max_exposure_pct": _runtime_overrides.get("portfolio_max_exposure_pct", 50),
}

# ─── LIVE OPTIONS TRADING ──────────────────────────────────────────────────

# Trading mode: "shadow" = log signals but don't execute, "live" = real orders
TRADING_MODE = _runtime_overrides.get("trading_mode", os.getenv("TRADING_MODE", "shadow"))

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

# ─── STRATEGY SLOTS (D-001) ────────────────────────────────────────────────
# Config-driven strategy slot definitions for the multi-strategy orchestrator.
# Each slot binds a strategy class to tickers, a portfolio sleeve, broker target,
# account type, and risk tags.  The pipeline module parses these into StrategySlot
# objects consumed by the orchestrator (C-001) and scheduler (C-003).
#
# Fields:
#   id              — Unique slot identifier (e.g. "gtaa_isa")
#   strategy_class  — Registry key matching a registered BaseStrategy subclass
#   strategy_version— Semver string for audit/provenance
#   params          — Dict of constructor params (merged with strategy defaults)
#   sleeve          — Portfolio sleeve name for risk/reporting attribution
#   account_type    — Account routing lane: ISA, GIA, SPREADBET, PAPER
#   broker_target   — Target broker: ig, ibkr, paper
#   tickers         — List of ticker symbols this slot trades
#   base_qty        — Base position quantity (scaled by signal.size_multiplier)
#   risk_tags       — List of risk tag strings for pre-trade risk gate
#   requirements    — Dict of StrategyRequirements fields (capability checks)
#   enabled         — Boolean: False skips this slot entirely

# Feature gate for IG-oriented slots in the orchestrator pipeline.
ENABLE_IG_ORCHESTRATOR_STRATEGIES = _env_bool("ENABLE_IG_ORCHESTRATOR_STRATEGIES", True)

# Orchestrator path currently doesn't inject VIX into strategy kwargs.
# Disable VIX gating for these specific slots to keep deterministic signal flow.
ORCHESTRATOR_IBS_PARAMS = {
    **IBS_PARAMS,
    "use_vix_filter": False,
}
ORCHESTRATOR_IBS_SHORT_PARAMS = {
    **IBS_SHORT_PARAMS,
    "use_vix_filter": False,
}

STRATEGY_SLOTS = [
    {
        "id": "gtaa_isa",
        "strategy_class": "GTAAStrategy",
        "strategy_version": "1.0",
        "params": GTAA_PARAMS,
        "sleeve": "sleeve_6_rotation",
        "account_type": "ISA",
        "broker_target": "ibkr",
        "tickers": ["SPY", "EFA", "IEF", "VNQ", "DBC"],
        "base_qty": 1.0,
        "risk_tags": ["trend_following", "monthly_rebalance"],
        "requirements": {"requires_spot_etf": True},
        "enabled": True,
    },
    {
        "id": "dual_momentum_isa",
        "strategy_class": "DualMomentumStrategy",
        "strategy_version": "1.0",
        "params": DUAL_MOMENTUM_PARAMS,
        "sleeve": "sleeve_6_rotation",
        "account_type": "ISA",
        "broker_target": "ibkr",
        "tickers": ["SPY", "EFA", "AGG"],
        "base_qty": 1.0,
        "risk_tags": ["momentum", "monthly_rebalance"],
        "requirements": {"requires_spot_etf": True},
        "enabled": True,
    },
    {
        "id": "ibs_spreadbet_long",
        "strategy_class": "IBSMeanReversion",
        "strategy_version": "1.0",
        "params": ORCHESTRATOR_IBS_PARAMS,
        "sleeve": "sleeve_1_ibs",
        "account_type": "SPREADBET",
        "broker_target": "ig",
        "tickers": ["SPY", "QQQ"],
        "base_qty": 1.0,
        "risk_tags": ["mean_reversion", "ibs", "ig"],
        "requirements": {"requires_spreadbet": True},
        "enabled": ENABLE_IG_ORCHESTRATOR_STRATEGIES,
    },
    {
        "id": "ibs_spreadbet_short",
        "strategy_class": "IBSShort",
        "strategy_version": "1.0",
        "params": ORCHESTRATOR_IBS_SHORT_PARAMS,
        "sleeve": "sleeve_2_ibs_short",
        "account_type": "SPREADBET",
        "broker_target": "ig",
        "tickers": ["SPY", "QQQ"],
        "base_qty": 1.0,
        "risk_tags": ["mean_reversion", "ibs_short", "ig"],
        "requirements": {"requires_spreadbet": True, "requires_short": True},
        "enabled": ENABLE_IG_ORCHESTRATOR_STRATEGIES,
    },
]

# ─── KRAKEN CRYPTO BROKER ─────────────────────────────────────────────────
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

# Crypto market mapping for the orchestrator
CRYPTO_MARKETS = {
    "BTC": {"pair": "XXBTZUSD", "strategy": "momentum", "direction": "both"},
    "ETH": {"pair": "XETHZUSD", "strategy": "momentum", "direction": "both"},
    "SOL": {"pair": "SOLUSD", "strategy": "momentum", "direction": "both"},
}

# ─── X / TWITTER API ────────────────────────────────────────────────────
X_CONSUMER_KEY = os.getenv("X_CONSUMER_KEY", "")
X_CONSUMER_SECRET = os.getenv("X_CONSUMER_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET", "")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

# ─── INTRADAY EVENT LOOP ─────────────────────────────────────────────────
INTRADAY_ENABLED = _env_bool("INTRADAY_ENABLED", False)
INTRADAY_POLL_SECONDS = _env_int("INTRADAY_POLL_SECONDS", 300, min_value=60, max_value=3600)
INTRADAY_TICKERS = [t.strip() for t in os.getenv("INTRADAY_TICKERS", "SPY,QQQ").split(",") if t.strip()]

# ─── TRADINGVIEW WEBHOOK INTAKE ────────────────────────────────────────────

TRADINGVIEW_WEBHOOK_TOKEN = os.getenv("TRADINGVIEW_WEBHOOK_TOKEN", "")
TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES = _env_int(
    "TRADINGVIEW_WEBHOOK_MAX_PAYLOAD_BYTES",
    65536,
    min_value=1024,
    max_value=1_048_576,
)
SA_BROWSER_CAPTURE_MAX_AGE_SECONDS = _env_int(
    "SA_BROWSER_CAPTURE_MAX_AGE_SECONDS",
    86_400,
    min_value=300,
    max_value=604_800,
)
TRADINGVIEW_MAX_SIGNAL_AGE_SECONDS = _env_int(
    "TRADINGVIEW_MAX_SIGNAL_AGE_SECONDS",
    600,
    min_value=0,
    max_value=86_400,
)
TRADINGVIEW_ENABLED_STRATEGIES = [
    item.strip().lower()
    for item in os.getenv(
        "TRADINGVIEW_ENABLED_STRATEGIES",
        "ibs_spreadbet_long,ibs_spreadbet_short",
    ).split(",")
    if item.strip()
]

# ─── PORTFOLIO ANALYTICS API (O-005) ───────────────────────────────────────

PORTFOLIO_ANALYTICS_DEFAULT_DAYS = _env_int(
    "PORTFOLIO_ANALYTICS_DEFAULT_DAYS",
    90,
    min_value=7,
    max_value=3650,
)
PORTFOLIO_ANALYTICS_MAX_DAYS = _env_int(
    "PORTFOLIO_ANALYTICS_MAX_DAYS",
    365,
    min_value=30,
    max_value=3650,
)
PORTFOLIO_ANALYTICS_ROLLING_WINDOW = _env_int(
    "PORTFOLIO_ANALYTICS_ROLLING_WINDOW",
    21,
    min_value=5,
    max_value=252,
)
PORTFOLIO_ANALYTICS_RISK_FREE_RATE = _env_float(
    "PORTFOLIO_ANALYTICS_RISK_FREE_RATE",
    0.0,
    min_value=-0.05,
    max_value=0.25,
)

# ─── IDEA PIPELINE (council trade ideas lifecycle) ───────────────────────────

IDEA_PIPELINE_ENABLED = _env_bool("IDEA_PIPELINE_ENABLED", True)
IDEA_BACKTEST_AUTO = _env_bool("IDEA_BACKTEST_AUTO", False)
IDEA_PAPER_SOAK_HOURS = _env_int("IDEA_PAPER_SOAK_HOURS", 24, min_value=1, max_value=720)
IDEA_PAPER_DEFAULT_STAKE = _env_float("IDEA_PAPER_DEFAULT_STAKE", 1.0, min_value=0.1, max_value=100.0)
IDEA_BACKTEST_MIN_SHARPE = _env_float("IDEA_BACKTEST_MIN_SHARPE", 0.0, min_value=-5.0, max_value=10.0)
IDEA_BACKTEST_MIN_PF = _env_float("IDEA_BACKTEST_MIN_PF", 1.0, min_value=0.0, max_value=10.0)
IDEA_LIVE_STRATEGY_SLOT = os.getenv("IDEA_LIVE_STRATEGY_SLOT", "discretionary")

# ─── Automated Idea Research Pipeline ────────────────────────────────────────
IDEA_RESEARCH_AUTO = _runtime_overrides.get("idea_research_auto", _env_bool("IDEA_RESEARCH_AUTO", True))
IDEA_RESEARCH_MODEL_HYPOTHESIS = os.getenv("IDEA_RESEARCH_MODEL_HYPOTHESIS", "grok")
IDEA_RESEARCH_MODEL_REVIEW = os.getenv("IDEA_RESEARCH_MODEL_REVIEW", "claude")
IDEA_RESEARCH_MODEL_STRATEGY = os.getenv("IDEA_RESEARCH_MODEL_STRATEGY", "openai")
IDEA_REVIEW_MIN_SCORE = _runtime_overrides.get("idea_review_min_score", _env_float("IDEA_REVIEW_MIN_SCORE", 5.0, min_value=0.0, max_value=10.0))
IDEA_AUTO_PROMOTE_BACKTEST = _runtime_overrides.get("idea_auto_promote_backtest", _env_bool("IDEA_AUTO_PROMOTE_BACKTEST", True))
IDEA_AUTO_PROMOTE_PAPER = _runtime_overrides.get("idea_auto_promote_paper", _env_bool("IDEA_AUTO_PROMOTE_PAPER", False))
IDEA_DYNAMIC_BT_MIN_SHARPE = _runtime_overrides.get("idea_dynamic_bt_min_sharpe", _env_float("IDEA_DYNAMIC_BT_MIN_SHARPE", 0.5, min_value=-5.0, max_value=10.0))
IDEA_DYNAMIC_BT_MIN_PF = _runtime_overrides.get("idea_dynamic_bt_min_pf", _env_float("IDEA_DYNAMIC_BT_MIN_PF", 1.2, min_value=0.0, max_value=10.0))
IDEA_DYNAMIC_BT_MIN_TRADES = _runtime_overrides.get("idea_dynamic_bt_min_trades", _env_int("IDEA_DYNAMIC_BT_MIN_TRADES", 20, min_value=1, max_value=1000))
IDEA_DYNAMIC_BT_WF_STATUS = os.getenv("IDEA_DYNAMIC_BT_WF_STATUS", "marginal")

# ─── Council & Research Timeouts ─────────────────────────────────────────────
COUNCIL_MODEL_TIMEOUT = _runtime_overrides.get("council_model_timeout", _env_int("COUNCIL_MODEL_TIMEOUT", 90, min_value=15, max_value=300))
COUNCIL_ROUND_TIMEOUT = _runtime_overrides.get("council_round_timeout", _env_int("COUNCIL_ROUND_TIMEOUT", 100, min_value=20, max_value=600))

RESEARCH_MODEL_CONFIG = {
    "signal_extraction": {
        "provider": "anthropic",
        "model_id": "claude-opus-4-6",
        "timeout_s": 120.0,
        "max_retries": 2,
        "backoff_s": 1.0,
        "thinking": True,
        "thinking_budget": 10000,
        "temperature": 1.0,
        "max_tokens": 16000,
        "prompt_version": "v1",
        "fallback": "signal_extraction_fallback",
    },
    "signal_extraction_fallback": {
        "provider": "openai",
        "model_id": "gpt-5.4",
        "timeout_s": 120.0,
        "max_retries": 1,
        "backoff_s": 1.0,
        "temperature": 0.2,
        "max_tokens": 16000,
        "prompt_version": "v1",
    },
    "hypothesis_formation": {
        "provider": "anthropic",
        "model_id": "claude-opus-4-6",
        "timeout_s": 120.0,
        "max_retries": 2,
        "backoff_s": 1.0,
        "thinking": True,
        "thinking_budget": 10000,
        "temperature": 1.0,
        "max_tokens": 16000,
        "prompt_version": "v1",
        "fallback": "hypothesis_formation_fallback",
    },
    "hypothesis_formation_fallback": {
        "provider": "openai",
        "model_id": "gpt-5.4",
        "timeout_s": 120.0,
        "max_retries": 1,
        "backoff_s": 1.0,
        "temperature": 0.2,
        "max_tokens": 16000,
        "prompt_version": "v1",
    },
    "hypothesis_challenge": {
        "provider": "openai",
        "model_id": "gpt-5.4",
        "timeout_s": 120.0,
        "max_retries": 2,
        "backoff_s": 1.0,
        "temperature": 0.2,
        "max_tokens": 16000,
        "prompt_version": "v1_challenge",
        "fallback": "hypothesis_challenge_fallback",
    },
    "hypothesis_challenge_fallback": {
        "provider": "xai",
        "model_id": "grok-3",
        "timeout_s": 120.0,
        "max_retries": 1,
        "backoff_s": 1.0,
        "temperature": 0.2,
        "max_tokens": 8192,
        "prompt_version": "v1_challenge",
    },
    "post_mortem": {
        "provider": "google",
        "model_id": "gemini-2.5-pro",
        "timeout_s": 120.0,
        "max_retries": 1,
        "backoff_s": 1.0,
        "thinking": True,
        "thinking_budget": 8000,
        "temperature": 0.2,
        "max_tokens": 8192,
        "prompt_version": "v1",
    },
    "research_synthesis": {
        "provider": "openai",
        "model_id": "gpt-5.4",
        "timeout_s": 90.0,
        "max_retries": 1,
        "backoff_s": 1.0,
        "temperature": 0.2,
        "max_tokens": 4096,
        "prompt_version": "v1",
    },
    "regime_journal": {
        "provider": "google",
        "model_id": "gemini-2.5-pro",
        "timeout_s": 60.0,
        "max_retries": 1,
        "backoff_s": 1.0,
        "thinking": False,
        "temperature": 0.2,
        "max_tokens": 2048,
        "prompt_version": "v1",
    },
}

# ─── PIPELINE & ORCHESTRATOR ─────────────────────────────────────────────────

ORCHESTRATOR_ENABLED = _env_bool("ORCHESTRATOR_ENABLED", False)
ORCHESTRATOR_DRY_RUN = _env_bool("ORCHESTRATOR_DRY_RUN", True)
AI_PANEL_ENABLED = _env_bool("AI_PANEL_ENABLED", False)
DISPATCHER_ENABLED = _env_bool("DISPATCHER_ENABLED", False)
ENGINE_A_ENABLED = _env_bool("ENGINE_A_ENABLED", False)
ENGINE_B_ENABLED = _env_bool("ENGINE_B_ENABLED", False)
RESEARCH_SYSTEM_ACTIVE = _env_bool("RESEARCH_SYSTEM_ACTIVE", False)
ENGINE_A_INTERVAL_SECONDS = _env_int(
    "ENGINE_A_INTERVAL_SECONDS", 300, min_value=5, max_value=86400
)

# ─── DAILY MARKET DATA REFRESH ──────────────────────────────────────────────
MARKET_DATA_REFRESH_ENABLED = _env_bool("MARKET_DATA_REFRESH_ENABLED", False)
MARKET_DATA_REFRESH_HOUR = _env_int("MARKET_DATA_REFRESH_HOUR", 20, min_value=0, max_value=23)
MARKET_DATA_REFRESH_MINUTE = _env_int("MARKET_DATA_REFRESH_MINUTE", 0, min_value=0, max_value=59)

# ─── FEED AGGREGATOR (Engine B automated intake) ────────────────────────────
FEED_AGGREGATOR_ENABLED = _env_bool("FEED_AGGREGATOR_ENABLED", False)
FEED_AGGREGATOR_TICKERS = [
    t.strip() for t in os.getenv("FEED_AGGREGATOR_TICKERS", "SPY,QQQ,AAPL,MSFT,NVDA").split(",") if t.strip()
]
FEED_AGGREGATOR_FINNHUB_INTERVAL = _env_int("FEED_AGGREGATOR_FINNHUB_INTERVAL", 300, min_value=60, max_value=3600)
FEED_AGGREGATOR_AV_INTERVAL = _env_int("FEED_AGGREGATOR_AV_INTERVAL", 900, min_value=300, max_value=7200)
FEED_AGGREGATOR_FRED_INTERVAL = _env_int("FEED_AGGREGATOR_FRED_INTERVAL", 3600, min_value=600, max_value=86400)
FEED_AGGREGATOR_FRED_SERIES = [
    t.strip() for t in os.getenv("FEED_AGGREGATOR_FRED_SERIES", "T10Y2Y,DFF,BAMLH0A0HYM2").split(",") if t.strip()
]
FEED_AGGREGATOR_TV_INTERVAL = _env_int("FEED_AGGREGATOR_TV_INTERVAL", 600, min_value=120, max_value=7200)
FEED_AGGREGATOR_TV_ENABLED = _env_bool("FEED_AGGREGATOR_TV_ENABLED", True)  # on by default (no API key needed)
ENGINE_A_CAPITAL_BASE = _env_float(
    "ENGINE_A_CAPITAL_BASE", 750000.0, min_value=1000.0, max_value=100000000.0
)
DISPATCHER_INTERVAL_SECONDS = _env_int(
    "DISPATCHER_INTERVAL_SECONDS", 60, min_value=10, max_value=600
)

# ─── NOTIFICATIONS ───────────────────────────────────────────────────────────

NOTIFICATIONS = {
    "enabled": _runtime_overrides.get("notifications_enabled", os.getenv("NOTIFICATIONS_ENABLED", "false").lower() == "true"),
    "email_to": _runtime_overrides.get("notifications_email_to", os.getenv("NOTIFY_EMAIL", "")),
    "telegram_token": os.getenv("TELEGRAM_TOKEN", ""),
    "telegram_chat_id": _runtime_overrides.get("notifications_telegram_chat_id", os.getenv("TELEGRAM_CHAT_ID", "")),
}

# ─── ADVISORY MODULE ─────────────────────────────────────────────────────────

ADVISOR_ENABLED = _env_bool("ADVISOR_ENABLED", False)
ADVISOR_MODEL = os.getenv("ADVISOR_MODEL", "claude-opus-4-6")
ADVISOR_MAX_CONTEXT_MESSAGES = _env_int("ADVISOR_MAX_CONTEXT_MESSAGES", 20, min_value=1, max_value=100)
ADVISOR_MAX_MEMORY_ITEMS = _env_int("ADVISOR_MAX_MEMORY_ITEMS", 15, min_value=1, max_value=50)
ADVISOR_SESSION_TIMEOUT_HOURS = _env_int("ADVISOR_SESSION_TIMEOUT_HOURS", 4, min_value=1, max_value=168)
ADVISOR_MEMORY_EXTRACTION_ENABLED = _env_bool("ADVISOR_MEMORY_EXTRACTION_ENABLED", True)

# RSS feed aggregator for advisory context
RSS_AGGREGATOR_ENABLED = _env_bool("RSS_AGGREGATOR_ENABLED", False)
RSS_POLL_INTERVAL = _env_int("RSS_POLL_INTERVAL", 1800, min_value=300, max_value=86400)
RSS_FEEDS_OVERRIDE = os.getenv("RSS_FEEDS_OVERRIDE", "")

# X/Twitter bookmarks polling
X_BOOKMARKS_ENABLED = _env_bool("X_BOOKMARKS_ENABLED", False)
X_BOOKMARKS_POLL_INTERVAL = _env_int("X_BOOKMARKS_POLL_INTERVAL", 1800, min_value=300, max_value=86400)

# Advisory proactive alerts
ADVISOR_WEEKLY_REVIEW_ENABLED = _env_bool("ADVISOR_WEEKLY_REVIEW_ENABLED", False)
ADVISOR_WEEKLY_REVIEW_DAY = _env_int("ADVISOR_WEEKLY_REVIEW_DAY", 6, min_value=0, max_value=6)  # 6=Sunday
ADVISOR_WEEKLY_REVIEW_HOUR = _env_int("ADVISOR_WEEKLY_REVIEW_HOUR", 18, min_value=0, max_value=23)
ADVISOR_DAILY_CHECK_ENABLED = _env_bool("ADVISOR_DAILY_CHECK_ENABLED", False)
ADVISOR_DRAWDOWN_ALERT_PCT = _env_float("ADVISOR_DRAWDOWN_ALERT_PCT", 10.0, min_value=1.0, max_value=50.0)

# ─── SIPP STRATEGY SLOTS ────────────────────────────────────────────────────

SIPP_STRATEGY_SLOTS = [
    {
        "id": "gtaa_sipp",
        "strategy_class": "GTAAStrategy",
        "strategy_version": "1.0",
        "params": GTAA_PARAMS,
        "sleeve": "sleeve_7_sipp",
        "account_type": "SIPP",
        "broker_target": "ibkr",
        "tickers": ["SPY", "EFA", "IEF", "VNQ", "DBC"],
        "base_qty": 1.0,
        "risk_tags": ["trend_following", "monthly_rebalance", "sipp"],
        "requirements": {"requires_spot_etf": True},
        "enabled": False,
    },
    {
        "id": "dual_momentum_sipp",
        "strategy_class": "DualMomentumStrategy",
        "strategy_version": "1.0",
        "params": DUAL_MOMENTUM_PARAMS,
        "sleeve": "sleeve_7_sipp",
        "account_type": "SIPP",
        "broker_target": "ibkr",
        "tickers": ["SPY", "EFA", "AGG"],
        "base_qty": 1.0,
        "risk_tags": ["momentum", "monthly_rebalance", "sipp"],
        "requirements": {"requires_spot_etf": True},
        "enabled": False,
    },
]

# ─── LOGGING ─────────────────────────────────────────────────────────────────

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("BOT_LOG_FILE", "trading_bot.log")
TRADE_LOG_FILE = "trades.csv"
