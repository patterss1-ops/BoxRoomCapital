"""
IBS++ Mean Reversion Strategy v3 — Python translation of IBS_Plus_Plus_v3.pine

Entry: IBS < threshold AND/OR RSI(2) < threshold, above 200 EMA, VIX regime filter
Exit:  IBS > threshold OR RSI > threshold OR max hold bars reached
Direction: Long only
"""
import logging
from typing import Optional

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType
from data.provider import calc_ibs, calc_rsi, calc_ema, calc_consecutive_down_days
import config

logger = logging.getLogger(__name__)


class IBSMeanReversion(BaseStrategy):
    """IBS++ v3 Mean Reversion with VIX regime filter."""

    def __init__(self, params: Optional[dict] = None):
        self.p = params or config.IBS_PARAMS

    @property
    def name(self) -> str:
        return "IBS++ v3"

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
        Generate IBS++ signal for the latest bar.

        Args:
            vix_close: Current VIX closing value (for regime filter)
        """
        if len(df) < self.p["ema_period"] + 10:
            return Signal(SignalType.NONE, ticker, self.name, "Insufficient data")

        # ─── Calculate indicators on latest bar ──────────────────────────
        ibs = calc_ibs(df)
        rsi = calc_rsi(df["Close"], self.p["rsi_period"])
        ema = calc_ema(df["Close"], self.p["ema_period"])
        down_days = calc_consecutive_down_days(df["Close"])

        # Latest values
        curr_ibs = ibs.iloc[-1]
        curr_rsi = rsi.iloc[-1]
        curr_ema = ema.iloc[-1]
        curr_close = df["Close"].iloc[-1]
        curr_down_days = down_days.iloc[-1]

        # ─── VIX regime classification ───────────────────────────────────
        vix_allows_trade = True
        vix_size_mult = 1.0

        if self.p["use_vix_filter"] and vix_close is not None:
            vix_is_low = vix_close < self.p["vix_low_thresh"]
            vix_is_elevated = (
                vix_close >= self.p["vix_high_thresh"]
                and vix_close < self.p["vix_extreme_thresh"]
            )
            vix_is_extreme = vix_close >= self.p["vix_extreme_thresh"]

            # Low VIX regime
            if vix_is_low:
                if self.p["vix_low_action"] == "Skip Trade":
                    vix_allows_trade = False
                elif self.p["vix_low_action"] == "Half Size":
                    vix_size_mult = 0.5

            # Elevated VIX regime
            if vix_is_elevated:
                if self.p["vix_high_action"] == "Boost 50%":
                    vix_size_mult = 1.5

            # Extreme VIX regime
            if vix_is_extreme:
                if self.p["vix_extreme_action"] == "Skip Trade":
                    vix_allows_trade = False

        # Cap size multiplier at 1.5
        vix_size_mult = min(vix_size_mult, 1.5)

        # ─── Entry conditions ────────────────────────────────────────────
        ibs_buy = curr_ibs < self.p["ibs_entry_thresh"]
        rsi_buy = curr_rsi < self.p["rsi_entry_thresh"]

        # Combined oversold signal
        if self.p["filter_mode"] == "Both":
            if self.p["use_rsi_filter"]:
                oversold = ibs_buy and rsi_buy
            else:
                oversold = ibs_buy
        else:  # "Either"
            if self.p["use_rsi_filter"]:
                oversold = ibs_buy or rsi_buy
            else:
                oversold = ibs_buy

        # Trend filter
        trend_ok = curr_close > curr_ema if self.p["use_trend_filter"] else True

        # Down days filter
        down_days_ok = (
            curr_down_days >= self.p["min_down_days"]
            if self.p["use_down_days"]
            else True
        )

        # ─── Exit conditions ─────────────────────────────────────────────
        ibs_exit = curr_ibs > self.p["ibs_exit_thresh"]
        rsi_exit = (
            curr_rsi > self.p["rsi_exit_thresh"] if self.p["use_rsi_filter"] else False
        )
        time_exit = bars_in_trade >= self.p["max_hold_bars"]

        # ─── Generate signal ─────────────────────────────────────────────

        # Check exits first (if we're in a position)
        if current_position > 0:
            if ibs_exit:
                return Signal(SignalType.LONG_EXIT, ticker, self.name, "IBS overbought")
            if rsi_exit:
                return Signal(SignalType.LONG_EXIT, ticker, self.name, "RSI overbought")
            if time_exit:
                return Signal(
                    SignalType.LONG_EXIT,
                    ticker,
                    self.name,
                    f"Max hold ({self.p['max_hold_bars']} bars)",
                )
            # Stop loss (if enabled)
            if self.p["use_stop_loss"]:
                # We'd need avg entry price here — handled by portfolio manager
                pass

            return Signal(SignalType.NONE, ticker, self.name, "Holding")

        # Check entries (if flat)
        if current_position == 0:
            if oversold and trend_ok and down_days_ok and vix_allows_trade:
                reason_parts = []
                if ibs_buy:
                    reason_parts.append(f"IBS={curr_ibs:.3f}")
                if rsi_buy and self.p["use_rsi_filter"]:
                    reason_parts.append(f"RSI={curr_rsi:.1f}")
                if vix_close is not None:
                    reason_parts.append(f"VIX={vix_close:.1f}")

                return Signal(
                    SignalType.LONG_ENTRY,
                    ticker,
                    self.name,
                    f"Oversold: {', '.join(reason_parts)}",
                    size_multiplier=vix_size_mult,
                )

            # Log why we didn't enter
            if oversold and not trend_ok:
                return Signal(SignalType.NONE, ticker, self.name, "Oversold but below EMA")
            if oversold and not vix_allows_trade:
                return Signal(SignalType.NONE, ticker, self.name, "Oversold but VIX blocked")

        return Signal(SignalType.NONE, ticker, self.name, "No signal")
