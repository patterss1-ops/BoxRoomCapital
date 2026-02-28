"""
IBS Credit Spread Strategy — Directional timing + option premium selling.

The thesis:
  1. IBS oversold signal gives us a directional edge (high-probability bounce)
  2. We EXPRESS that view by selling a bull put spread (credit spread)
  3. The option's time decay (theta) works in our favour every day
  4. No overnight financing — cost is the bid/offer spread on entry
  5. Defined risk — max loss is (spread width - premium collected)

Why this beats DFB IBS:
  - Zero overnight financing (was 7% pa drag on DFBs)
  - Defined risk at entry (no stop loss slippage)
  - Works even if the market just sits flat (theta decay still pays us)
  - Tax-free (spread betting)

Strategy rules:
  Entry:
    - IBS oversold signal fires (same IBS++ v3 logic)
    - Sell a bull put spread: sell OTM put, buy further OTM put
    - Short strike = current price × (1 - short_distance_pct)
    - Long strike = short_strike - spread_width
    - Use weekly options (5 days to expiry) for fastest theta decay
    - Kelly-constrained position sizing

  Exit:
    - Let spread expire worthless (max profit) if IBS recovers, OR
    - Close early if IBS exit signal fires (take partial profit), OR
    - Max loss if underlying drops below short strike (defined risk)

  Risk management:
    - Max loss is ALWAYS (spread_width - premium) per contract
    - Quarter-Kelly position sizing
    - Hard cap: max 2% of equity at risk per trade
    - VIX filter: skip in extreme VIX (>35)
    - Minimum premium: don't enter if credit < min_credit_pct of width

Variant B — IBS Iron Condor:
  When IBS is neutral (not oversold, not overbought), sell BOTH sides:
    - Bull put spread (below market) + bear call spread (above market)
    - Collect premium from both sides
    - Profit if market stays in range
    - Only when VIX is low (calm market = range-bound)
"""
import math
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass
class CreditSpreadSignal:
    """Output from the strategy: what to trade."""
    action: str              # "open_put_spread", "open_call_spread", "open_iron_condor", "close", "hold", "skip"
    underlying_price: float = 0.0
    short_strike: float = 0.0
    long_strike: float = 0.0
    # For iron condors:
    short_put: float = 0.0
    long_put: float = 0.0
    short_call: float = 0.0
    long_call: float = 0.0
    # Metadata
    ibs: float = 0.0
    rsi: float = 0.0
    vix: float = 0.0
    reason: str = ""
    days_to_expiry: int = 5


@dataclass
class OptionPosition:
    """Track an open options position."""
    trade_type: str          # "put_spread", "call_spread", "iron_condor"
    entry_date: str = ""
    entry_price: float = 0.0 # Underlying at entry
    short_strike: float = 0.0
    long_strike: float = 0.0
    # Iron condor legs
    short_put: float = 0.0
    long_put: float = 0.0
    short_call: float = 0.0
    long_call: float = 0.0
    premium_collected: float = 0.0  # Net credit
    spread_width: float = 0.0
    max_loss: float = 0.0
    contracts: int = 1
    bars_held: int = 0
    days_to_expiry: int = 5


# ─── Default parameters ──────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    # IBS entry/exit (same as IBS++ v3)
    "ibs_entry_thresh": 0.3,
    "ibs_exit_thresh": 0.7,
    "use_rsi_filter": True,
    "rsi_period": 2,
    "rsi_entry_thresh": 25.0,
    "rsi_exit_thresh": 65.0,
    "filter_mode": "Both",        # Both IBS and RSI must confirm
    "use_trend_filter": True,
    "ema_period": 200,

    # VIX regime
    "use_vix_filter": True,
    "vix_extreme_thresh": 35.0,   # Skip trades in crisis
    "vix_low_thresh": 15.0,       # Low VIX = iron condor territory

    # IV floor: skip trades in low-vol environments (not enough premium)
    "min_iv": 0.14,               # Don't trade if IV < 14% (premiums too thin)

    # Credit spread construction — OPTIMISED from parameter sweep
    "short_distance_pct": 1.0,    # Short strike 1% below (ATM-ish = max premium)
    "spread_width_pct": 1.5,      # 1.5% wide (amortises fixed IG cost)
    "min_credit_pct": 3.0,        # Minimum premium as % of spread width
    "expiry_days": 10,            # 10 DTE: 2× premium for same IG entry cost

    # Iron condor (when IBS is neutral)
    "enable_iron_condor": False,  # DISABLED: 4 legs = double IG cost, thin premiums
    "ic_ibs_low": 0.35,          # Below this = oversold (put spread only)
    "ic_ibs_high": 0.65,         # Above this = overbought (call spread only)
    # Between ic_ibs_low and ic_ibs_high = neutral = iron condor
    "ic_short_distance_pct": 2.5, # Wider strikes for iron condors (less directional)

    # Position sizing
    "max_risk_pct": 2.0,          # Max 2% equity at risk per trade
    "kelly_fraction": 0.25,       # Quarter Kelly

    # Early exit — IBS exit re-enabled. Backtester applies smart filter:
    # only closes early if net profit after exit spread cost > 0.
    "close_at_expiry": True,
    "close_early_ibs": True,      # IBS recovery triggers close (filtered by backtester)
    "close_early_pct": 999.0,     # Disabled (smart filter is better)
    "max_hold_bars": 10,          # Max bars = matches DTE
}


# ─── Signal generation ────────────────────────────────────────────────────────

def compute_ibs(high: float, low: float, close: float) -> float:
    """Internal Bar Strength = (Close - Low) / (High - Low)."""
    if high == low:
        return 0.5
    return (close - low) / (high - low)


def compute_rsi(closes: list, period: int = 2) -> float:
    """RSI(2) — short-period RSI for mean reversion."""
    if len(closes) < period + 1:
        return 50.0

    changes = [closes[i] - closes[i-1] for i in range(-period, 0)]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def generate_signal(bar: dict, prev_bars: list, position: Optional[OptionPosition],
                    params: dict = None, vix: float = None,
                    ema200: float = None) -> CreditSpreadSignal:
    """
    Generate a credit spread signal based on IBS + VIX + trend.

    Args:
        bar: Current bar {open, high, low, close, date}
        prev_bars: List of previous bars (most recent last) for RSI
        position: Current open position (or None)
        params: Strategy parameters (uses defaults if None)
        vix: Current VIX level (optional)
        ema200: Current 200-day EMA of underlying (optional)

    Returns:
        CreditSpreadSignal with action and trade details
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    price = bar["close"]
    ibs = compute_ibs(bar["high"], bar["low"], bar["close"])

    # RSI
    closes = [b["close"] for b in prev_bars] + [price]
    rsi = compute_rsi(closes, p["rsi_period"])

    # VIX check
    vix_val = vix or 20.0

    signal = CreditSpreadSignal(
        action="skip",  # Default, overridden below
        underlying_price=price,
        ibs=ibs,
        rsi=rsi,
        vix=vix_val,
        days_to_expiry=p["expiry_days"],
    )

    # ─── If we have a position, check for exit ────────────────────────
    if position:
        position.bars_held += 1

        # Expiry: auto-close at max hold
        if position.bars_held >= p["max_hold_bars"]:
            signal.action = "close"
            signal.reason = "expiry (max bars)"
            return signal

        # Early exit: IBS recovered — only if enabled (costs extra IG spread)
        if p.get("close_early_ibs", True):
            if position.trade_type == "put_spread" and ibs > p["ibs_exit_thresh"]:
                signal.action = "close"
                signal.reason = "IBS recovered (bullish exit)"
                return signal

            if position.trade_type == "call_spread" and ibs < p["ibs_entry_thresh"]:
                signal.action = "close"
                signal.reason = "IBS dropped (bearish exit)"
                return signal

        signal.action = "hold"
        signal.reason = f"holding ({position.bars_held}/{p['max_hold_bars']} bars)"
        return signal

    # ─── No position: check for entry ─────────────────────────────────

    # VIX extreme filter
    if p["use_vix_filter"] and vix_val > p["vix_extreme_thresh"]:
        signal.action = "skip"
        signal.reason = f"VIX extreme ({vix_val:.0f} > {p['vix_extreme_thresh']})"
        return signal

    # Trend filter
    if p["use_trend_filter"] and ema200 is not None:
        if price < ema200:
            signal.action = "skip"
            signal.reason = "below 200 EMA (bear regime)"
            return signal

    # IBS oversold → bull put spread
    ibs_oversold = ibs < p["ibs_entry_thresh"]
    rsi_oversold = rsi < p["rsi_entry_thresh"]

    if p["filter_mode"] == "Both":
        entry_long = ibs_oversold and (rsi_oversold if p["use_rsi_filter"] else True)
    else:
        entry_long = ibs_oversold or (rsi_oversold if p["use_rsi_filter"] else False)

    if entry_long:
        # Construct bull put spread strikes
        short_strike = round(price * (1 - p["short_distance_pct"] / 100))
        spread_width = round(price * p["spread_width_pct"] / 100)
        long_strike = short_strike - spread_width

        signal.action = "open_put_spread"
        signal.short_strike = short_strike
        signal.long_strike = long_strike
        signal.reason = f"IBS oversold ({ibs:.2f}), RSI({p['rsi_period']})={rsi:.0f}"
        return signal

    # Iron condor: IBS is neutral and VIX is low
    if p["enable_iron_condor"]:
        ibs_neutral = p["ic_ibs_low"] <= ibs <= p["ic_ibs_high"]
        vix_low = vix_val < p["vix_low_thresh"] if p["use_vix_filter"] else True

        if ibs_neutral and vix_low:
            dist = p["ic_short_distance_pct"] / 100
            spread_width = round(price * p["spread_width_pct"] / 100)

            signal.action = "open_iron_condor"
            signal.short_put = round(price * (1 - dist))
            signal.long_put = signal.short_put - spread_width
            signal.short_call = round(price * (1 + dist))
            signal.long_call = signal.short_call + spread_width
            signal.reason = f"IBS neutral ({ibs:.2f}), VIX low ({vix_val:.0f})"
            return signal

    signal.action = "skip"
    signal.reason = f"no signal (IBS={ibs:.2f})"
    return signal
