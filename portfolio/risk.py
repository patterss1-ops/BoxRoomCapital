"""
Risk-based position sizing for spread betting.

Core formula:
    stake_per_point = (equity × risk_pct_per_trade) / stop_distance_in_points

This ensures every trade risks the same £ amount regardless of market or volatility.
Higher volatility → wider stop → smaller stake. Lower volatility → tighter stop → larger stake.

For markets without explicit stops (e.g. IBS++ mean reversion with no stop loss),
we use ATR as a proxy for expected adverse move to size defensively.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import numpy as np

import config

logger = logging.getLogger(__name__)


@dataclass
class SizeResult:
    """Result of position sizing calculation."""
    stake_per_point: float     # £/pt to trade
    risk_amount: float         # £ at risk on this trade
    stop_distance: float       # points
    stop_type: str             # "ATR", "fixed_pct", "strategy"
    margin_required: float     # estimated margin (stake × price × margin_rate)
    risk_pct_of_equity: float  # actual risk as % of equity
    notes: str = ""


# ─── Risk parameters ─────────────────────────────────────────────────────────

RISK_PARAMS = {
    # How much of equity to risk per trade
    "risk_per_trade_pct": 1.0,         # 1% of equity per trade

    # Maximum total portfolio risk (sum of all open position risks)
    "max_portfolio_heat_pct": 6.0,     # 6% max total risk across all positions

    # Maximum single position margin as % of equity
    "max_position_margin_pct": 15.0,   # No single position uses more than 15% margin

    # Maximum total margin as % of equity
    "max_total_margin_pct": 50.0,      # 50% max total margin (matches config)

    # Minimum stake (IG minimums)
    "min_stake": 0.50,                 # IG min for most markets

    # ATR multiplier for stop distance when strategy has no explicit stop
    "atr_stop_mult": {
        "IBS++ v3": 2.0,              # 2x ATR — mean reversion, expect tight moves
        "Trend Following v2": 2.5,     # 2.5x ATR — matches Pine Script trailing stop
        "SPY/TLT Rotation v3": 3.0,    # 3x ATR — monthly rebalance, wider stop
    },

    # ATR period
    "atr_period": 14,

    # Default margin rate for spread bets (retail)
    "default_margin_rate": 0.05,       # 5% = 20:1 leverage

    # Market-specific margin rates (IG retail)
    "margin_rates": {
        "IX.D.SPTRD.DAILY.IP": 0.05,    # US 500 — 5%
        "IX.D.NASDAQ.DAILY.IP": 0.05,    # US Tech 100 — 5%
        "IX.D.RUSSELL.DAILY.IP": 0.05,   # Russell 2000 — 5%
        "IX.D.DOW.DAILY.IP": 0.05,       # Wall Street — 5%
        "IX.D.FTSE.DAILY.IP": 0.05,      # FTSE 100 — 5%
        "IX.D.DAX.DAILY.IP": 0.05,       # Germany 40 — 5%
        "IX.D.NIKKEI.DAILY.IP": 0.05,    # Japan 225 — 5%
        "CS.D.GBPUSD.TODAY.IP": 0.0334,  # GBP/USD — 3.34%
        "CS.D.USCGC.TODAY.IP": 0.05,     # Gold — 5%
        "CS.D.USCSI.TODAY.IP": 0.10,     # Silver — 10%
        "CC.D.CL.UMP.IP": 0.10,          # Crude Oil — 10%
        "CC.D.NG.UMP.IP": 0.10,          # Natural Gas — 10%
        "CC.D.HG.UMP.IP": 0.10,          # Copper — 10%
        "IR.D.10USTBON.FWM2.IP": 0.02,   # US 10-Yr T-Note — 2%
    },
}


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate latest ATR value."""
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    return float(atr.iloc[-1])


def calc_position_size(
    ticker: str,
    strategy_name: str,
    df: pd.DataFrame,
    equity: float,
    current_portfolio_risk: float = 0.0,
    current_total_margin: float = 0.0,
    vix_size_multiplier: float = 1.0,
) -> SizeResult:
    """
    Calculate risk-adjusted position size for a spread bet.

    Args:
        ticker: Market ticker (e.g. "SPY", "CL=F")
        strategy_name: Strategy placing the trade
        df: Price DataFrame with OHLC
        equity: Current account equity in £
        current_portfolio_risk: Sum of £-risk across all open positions
        current_total_margin: Sum of margin used by all open positions
        vix_size_multiplier: VIX regime multiplier from IBS++ strategy (1.0 = normal)

    Returns:
        SizeResult with stake, risk, stop distance, margin
    """
    market_info = config.MARKET_MAP.get(ticker, {})
    epic = market_info.get("epic", "")
    current_price = float(df["Close"].iloc[-1])
    atr_period = RISK_PARAMS["atr_period"]

    # ─── 1. Calculate stop distance ──────────────────────────────────────
    atr = calc_atr(df, atr_period)
    atr_mult = RISK_PARAMS["atr_stop_mult"].get(strategy_name, 2.5)

    if strategy_name == "Trend Following v2":
        # Use the exact ATR trailing stop from the strategy
        stop_distance = atr * config.TREND_PARAMS["atr_mult_stop"]
        stop_type = "ATR trailing"
    elif strategy_name == "IBS++ v3" and config.IBS_PARAMS.get("use_stop_loss"):
        # Fixed percentage stop if enabled
        stop_distance = current_price * (config.IBS_PARAMS["stop_loss_pct"] / 100)
        stop_type = f"fixed {config.IBS_PARAMS['stop_loss_pct']}%"
    else:
        # Default: ATR-based risk estimate
        stop_distance = atr * atr_mult
        stop_type = f"ATR×{atr_mult}"

    if stop_distance <= 0:
        logger.warning(f"{ticker}: stop distance is 0, using 1% of price")
        stop_distance = current_price * 0.01
        stop_type = "fallback 1%"

    # ─── 2. Calculate base stake from risk budget ────────────────────────
    risk_per_trade = equity * (RISK_PARAMS["risk_per_trade_pct"] / 100)
    base_stake = risk_per_trade / stop_distance

    # ─── 3. Apply VIX multiplier ─────────────────────────────────────────
    stake = base_stake * vix_size_multiplier

    # ─── 4. Apply portfolio-level risk constraints ───────────────────────
    notes = []

    # Check portfolio heat limit
    max_heat = equity * (RISK_PARAMS["max_portfolio_heat_pct"] / 100)
    remaining_risk_budget = max_heat - current_portfolio_risk
    if remaining_risk_budget <= 0:
        notes.append("BLOCKED: portfolio heat limit reached")
        return SizeResult(
            stake_per_point=0, risk_amount=0, stop_distance=stop_distance,
            stop_type=stop_type, margin_required=0, risk_pct_of_equity=0,
            notes="Portfolio heat limit reached — no new trades",
        )

    trade_risk = stake * stop_distance
    if trade_risk > remaining_risk_budget:
        stake = remaining_risk_budget / stop_distance
        notes.append(f"Reduced by heat limit (max £{remaining_risk_budget:.0f} risk remaining)")

    # Check margin constraints
    margin_rate = RISK_PARAMS["margin_rates"].get(epic, RISK_PARAMS["default_margin_rate"])
    margin_required = stake * current_price * margin_rate

    max_position_margin = equity * (RISK_PARAMS["max_position_margin_pct"] / 100)
    if margin_required > max_position_margin:
        stake = max_position_margin / (current_price * margin_rate)
        margin_required = max_position_margin
        notes.append(f"Reduced by position margin limit (£{max_position_margin:.0f})")

    max_total_margin = equity * (RISK_PARAMS["max_total_margin_pct"] / 100)
    remaining_margin = max_total_margin - current_total_margin
    if margin_required > remaining_margin:
        if remaining_margin <= 0:
            notes.append("BLOCKED: total margin limit reached")
            return SizeResult(
                stake_per_point=0, risk_amount=0, stop_distance=stop_distance,
                stop_type=stop_type, margin_required=0, risk_pct_of_equity=0,
                notes="Total margin limit reached — no new trades",
            )
        stake = remaining_margin / (current_price * margin_rate)
        margin_required = remaining_margin
        notes.append(f"Reduced by total margin limit (£{remaining_margin:.0f} remaining)")

    # ─── 5. Enforce minimum stake ────────────────────────────────────────
    min_stake = RISK_PARAMS["min_stake"]
    if stake < min_stake:
        # Check if minimum stake exceeds our risk budget
        min_risk = min_stake * stop_distance
        min_margin = min_stake * current_price * margin_rate
        if min_risk > remaining_risk_budget or min_margin > remaining_margin:
            notes.append(f"BLOCKED: min stake £{min_stake}/pt exceeds risk budget")
            return SizeResult(
                stake_per_point=0, risk_amount=0, stop_distance=stop_distance,
                stop_type=stop_type, margin_required=0, risk_pct_of_equity=0,
                notes=f"Min stake £{min_stake}/pt would exceed risk limits",
            )
        stake = min_stake
        notes.append(f"Raised to IG minimum £{min_stake}/pt")

    # ─── 6. Round to 2 decimal places (IG precision) ─────────────────────
    stake = round(stake, 2)
    actual_risk = stake * stop_distance
    margin_required = stake * current_price * margin_rate
    risk_pct = (actual_risk / equity * 100) if equity > 0 else 0

    logger.info(
        f"SIZE: {ticker} [{strategy_name}] — "
        f"£{stake:.2f}/pt, risk=£{actual_risk:.0f} ({risk_pct:.1f}%), "
        f"stop={stop_distance:.1f}pts ({stop_type}), "
        f"margin=£{margin_required:.0f}, "
        f"ATR={atr:.2f}, price={current_price:.2f}"
        + (f" | {'; '.join(notes)}" if notes else "")
    )

    return SizeResult(
        stake_per_point=stake,
        risk_amount=round(actual_risk, 2),
        stop_distance=round(stop_distance, 2),
        stop_type=stop_type,
        margin_required=round(margin_required, 2),
        risk_pct_of_equity=round(risk_pct, 2),
        notes="; ".join(notes) if notes else "OK",
    )


def calc_option_spread_size(
    equity: float,
    spread_width: float,
    premium: float,
    max_risk_pct: float = 2.0,
    kelly_fraction: float = 0.25,
    win_rate: float = 0.93,
    min_size: float = 1.0,
    max_size: float = 20.0,
) -> SizeResult:
    """
    Size an options credit spread using defined max loss.

    For IG spread betting: 1 contract = £1 per point of movement.
    Max loss = spread_width - premium_collected (in points).
    Risk per trade = num_contracts × max_loss_per_contract.

    Args:
        equity: Current account equity in £
        spread_width: Width of spread in points (e.g. 75 for SPY)
        premium: Net credit received in points
        max_risk_pct: Max % of equity to risk on this trade
        kelly_fraction: Fraction of Kelly criterion to use
        win_rate: Historical win rate for Kelly calc
        min_size: Minimum contracts (IG minimum)
        max_size: Maximum contracts (hard cap)

    Returns:
        SizeResult with stake_per_point = num_contracts
    """
    # Max loss per contract
    max_loss_per_contract = spread_width - premium
    if max_loss_per_contract <= 0:
        return SizeResult(
            stake_per_point=0, risk_amount=0, stop_distance=spread_width,
            stop_type="defined_max_loss", margin_required=0, risk_pct_of_equity=0,
            notes="Premium >= spread width — free money? Check pricing.",
        )

    # Risk budget from % of equity
    risk_budget = equity * (max_risk_pct / 100.0)

    # Kelly sizing: f* = (p × b - q) / b where p=win_rate, b=win/loss ratio, q=1-p
    # For credit spreads: avg_win ≈ premium, avg_loss ≈ max_loss
    if premium > 0 and max_loss_per_contract > 0:
        b = premium / max_loss_per_contract  # reward-to-risk
        q = 1 - win_rate
        kelly_full = (win_rate * b - q) / b if b > 0 else 0
        kelly_size = max(0, kelly_full * kelly_fraction)
        kelly_risk = equity * kelly_size
        # Take the more conservative of risk % and Kelly
        risk_budget = min(risk_budget, kelly_risk) if kelly_risk > 0 else risk_budget

    # Number of contracts
    num_contracts = risk_budget / max_loss_per_contract
    num_contracts = max(min_size, min(num_contracts, max_size))

    # IG margin for credit spreads ≈ spread_width × size (full notional),
    # not just max loss.  Cap contracts so margin stays within equity.
    if spread_width > 0:
        margin_cap = equity * 0.90 / spread_width  # keep 10% buffer
        if margin_cap < num_contracts:
            num_contracts = max(min_size, margin_cap)

    num_contracts = int(num_contracts)  # Round down

    actual_risk = num_contracts * max_loss_per_contract
    risk_pct = (actual_risk / equity * 100) if equity > 0 else 0

    notes = (
        f"Max loss: £{max_loss_per_contract:.0f}/contract × {num_contracts} = "
        f"£{actual_risk:.0f} ({risk_pct:.1f}% of equity)"
    )

    # IG margin for credit spreads ≈ spread_width × contracts (full notional)
    margin_required = spread_width * num_contracts

    logger.info(
        f"OPTIONS SIZE: {num_contracts} contracts, "
        f"risk=£{actual_risk:.0f} ({risk_pct:.1f}%), "
        f"max_loss/contract=£{max_loss_per_contract:.0f}, "
        f"premium={premium:.1f}pts, width={spread_width:.0f}pts"
    )

    return SizeResult(
        stake_per_point=float(num_contracts),
        risk_amount=round(actual_risk, 2),
        stop_distance=round(spread_width, 2),
        stop_type="defined_max_loss",
        margin_required=round(margin_required, 2),
        risk_pct_of_equity=round(risk_pct, 2),
        notes=notes,
    )


def get_portfolio_risk_summary(
    positions: list,
    equity: float,
) -> dict:
    """
    Calculate portfolio-level risk metrics for the dashboard.

    Args:
        positions: List of open position dicts (from trade_db or broker)
        equity: Current account equity

    Returns:
        Dict with risk metrics
    """
    total_risk = 0.0
    total_margin = 0.0
    position_details = []

    for pos in positions:
        ticker = pos.get("ticker", "")
        size = float(pos.get("size", 0))
        entry_price = float(pos.get("entry_price", pos.get("open_level", 0)))
        strategy = pos.get("strategy", "unknown")
        epic = config.MARKET_MAP.get(ticker, {}).get("epic", "")

        # Estimate risk using ATR
        margin_rate = RISK_PARAMS["margin_rates"].get(epic, RISK_PARAMS["default_margin_rate"])
        margin = size * entry_price * margin_rate

        # Use 2x ATR as estimated risk distance (conservative)
        # In a real scenario we'd look up the actual stop level
        estimated_risk = size * entry_price * 0.02  # ~2% of notional as fallback

        total_risk += estimated_risk
        total_margin += margin

        position_details.append({
            "ticker": ticker,
            "strategy": strategy,
            "size": size,
            "entry_price": entry_price,
            "est_risk": round(estimated_risk, 2),
            "margin": round(margin, 2),
            "risk_pct": round(estimated_risk / equity * 100, 2) if equity > 0 else 0,
        })

    return {
        "total_risk": round(total_risk, 2),
        "total_margin": round(total_margin, 2),
        "portfolio_heat_pct": round(total_risk / equity * 100, 2) if equity > 0 else 0,
        "margin_utilisation_pct": round(total_margin / equity * 100, 2) if equity > 0 else 0,
        "risk_budget_remaining": round(equity * RISK_PARAMS["max_portfolio_heat_pct"] / 100 - total_risk, 2),
        "margin_remaining": round(equity * RISK_PARAMS["max_total_margin_pct"] / 100 - total_margin, 2),
        "max_heat_pct": RISK_PARAMS["max_portfolio_heat_pct"],
        "max_margin_pct": RISK_PARAMS["max_total_margin_pct"],
        "risk_per_trade_pct": RISK_PARAMS["risk_per_trade_pct"],
        "positions": position_details,
    }
