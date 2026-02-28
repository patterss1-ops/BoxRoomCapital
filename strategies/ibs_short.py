"""
IBS Short (Bear Regime) Strategy — Short-side mean reversion for bear markets.

Entry: IBS > threshold AND/OR RSI(2) > threshold, BELOW 200 EMA, VIX elevated
Exit:  IBS < threshold OR RSI < threshold OR max hold bars reached
Direction: Short only

KEY INSIGHT: When you short a DFB on IG, you EARN overnight financing
(SONIA - IG markup, currently ~2% net). This turns the financing cost
headwind (that hurts IBS Long) into a tailwind.

Only active in bear regimes: price below 200 EMA + elevated VIX.
"""
import logging
from typing import Optional

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType
from data.provider import calc_ibs, calc_rsi, calc_ema
import config

logger = logging.getLogger(__name__)


class IBSShort(BaseStrategy):
    """IBS Short — bear regime mean reversion that earns financing."""

    def __init__(self, params: Optional[dict] = None):
        self.p = params or config.IBS_SHORT_PARAMS

    @property
    def name(self) -> str:
        return "IBS Short (Bear)"

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_position: float,
        bars_in_trade: int,
        vix_close: Optional[float] = None,
        **kwargs,
    ) -> Signal:
        """
        Generate short-side IBS signal for bear regimes.

        Mirror of IBS Long but reversed:
        - Short when IBS is HIGH (overbought in a downtrend = likely to fall back)
        - Cover when IBS is LOW (oversold bounce)
        - Only active below 200 EMA (bear regime)
        - VIX must be elevated (calm markets = bull, don't short)
        """
        if len(df) < self.p["ema_period"] + 10:
            return Signal(SignalType.NONE, ticker, self.name, "Insufficient data")

        # ─── Calculate indicators ──────────────────────────────────────
        ibs = calc_ibs(df)
        rsi = calc_rsi(df["Close"], self.p["rsi_period"])
        ema = calc_ema(df["Close"], self.p["ema_period"])

        curr_ibs = ibs.iloc[-1]
        curr_rsi = rsi.iloc[-1]
        curr_ema = ema.iloc[-1]
        curr_close = df["Close"].iloc[-1]

        # ─── VIX regime filter (opposite logic to IBS Long) ───────────
        vix_allows_trade = True
        vix_size_mult = 1.0

        if self.p["use_vix_filter"] and vix_close is not None:
            vix_is_low = vix_close < self.p["vix_low_thresh"]
            vix_is_elevated = (
                vix_close >= self.p["vix_high_thresh"]
                and vix_close < self.p["vix_extreme_thresh"]
            )
            vix_is_extreme = vix_close >= self.p["vix_extreme_thresh"]

            # Low VIX = calm bull market = don't short
            if vix_is_low:
                if self.p["vix_low_action"] == "Skip Trade":
                    vix_allows_trade = False

            # Elevated VIX = bear territory = good for shorts
            if vix_is_elevated:
                if self.p["vix_high_action"] == "Boost 50%":
                    vix_size_mult = 1.5

            # Extreme VIX = squeeze risk = sit out
            if vix_is_extreme:
                if self.p["vix_extreme_action"] == "Skip Trade":
                    vix_allows_trade = False

        vix_size_mult = min(vix_size_mult, 1.5)

        # ─── Entry conditions (reversed from IBS Long) ─────────────────
        # Short when OVERBOUGHT in a DOWNTREND
        ibs_sell = curr_ibs > self.p["ibs_entry_thresh"]
        rsi_sell = curr_rsi > self.p["rsi_entry_thresh"]

        if self.p["filter_mode"] == "Both":
            if self.p["use_rsi_filter"]:
                overbought = ibs_sell and rsi_sell
            else:
                overbought = ibs_sell
        else:  # "Either"
            if self.p["use_rsi_filter"]:
                overbought = ibs_sell or rsi_sell
            else:
                overbought = ibs_sell

        # Trend filter: must be BELOW 200 EMA (bear regime)
        trend_ok = curr_close < curr_ema if self.p["use_trend_filter"] else True

        # ─── Exit conditions ───────────────────────────────────────────
        ibs_cover = curr_ibs < self.p["ibs_exit_thresh"]
        rsi_cover = (
            curr_rsi < self.p["rsi_exit_thresh"] if self.p["use_rsi_filter"] else False
        )
        time_exit = bars_in_trade >= self.p["max_hold_bars"]

        # Stop loss for shorts (price moved against us = up)
        stop_hit = False
        if self.p["use_stop_loss"] and current_position < 0 and bars_in_trade > 0:
            entry_price = kwargs.get("entry_price", 0)
            if entry_price > 0:
                move_pct = (curr_close - entry_price) / entry_price * 100
                if move_pct > self.p["stop_loss_pct"]:
                    stop_hit = True

        # ─── Generate signal ───────────────────────────────────────────

        # Check exits first (if we're in a short position)
        if current_position < 0:
            if stop_hit:
                return Signal(SignalType.SHORT_EXIT, ticker, self.name,
                              f"Stop loss ({self.p['stop_loss_pct']}%)")
            if ibs_cover:
                return Signal(SignalType.SHORT_EXIT, ticker, self.name, "IBS oversold — cover")
            if rsi_cover:
                return Signal(SignalType.SHORT_EXIT, ticker, self.name, "RSI oversold — cover")
            if time_exit:
                return Signal(SignalType.SHORT_EXIT, ticker, self.name,
                              f"Max hold ({self.p['max_hold_bars']} bars)")

        # Check entries (if flat)
        if current_position == 0:
            if not vix_allows_trade:
                return Signal(SignalType.NONE, ticker, self.name,
                              f"VIX regime skip (VIX={vix_close:.1f})" if vix_close else "VIX filter active")

            if overbought and trend_ok:
                reason = (
                    f"SHORT: IBS={curr_ibs:.2f}, RSI={curr_rsi:.0f}, "
                    f"below EMA ({curr_close:.2f} < {curr_ema:.2f})"
                )
                if vix_close:
                    reason += f", VIX={vix_close:.1f}"
                return Signal(
                    SignalType.SHORT_ENTRY,
                    ticker,
                    self.name,
                    reason,
                    size_multiplier=vix_size_mult,
                )

        return Signal(SignalType.NONE, ticker, self.name, "No signal")
