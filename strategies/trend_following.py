"""
Trend Following Strategy v2 — Python translation of Trend_Following_v2.pine

Entry modes: MA Crossover, Donchian Breakout, or Both
Features: ADX filter, ATR trailing stop, re-entry cooldown
Direction: Long + Short (configurable)
"""
import json
import logging
from typing import Optional

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType
from data.provider import (
    calc_ema, calc_sma, calc_atr, calc_adx,
    calc_donchian_upper, calc_donchian_lower,
)
import config

logger = logging.getLogger(__name__)


class TrendFollowing(BaseStrategy):
    """Trend Following v2 with MA Crossover + Donchian Breakout."""

    def __init__(self, params: Optional[dict] = None):
        self.p = params or config.TREND_PARAMS
        # Trailing stop state (per-ticker)
        self._trail_stops: dict[str, float] = {}
        # Cooldown tracking (per-ticker)
        self._bars_since_exit: dict[str, int] = {}
        # Previous MA values for crossover detection
        self._prev_fast_ma: dict[str, float] = {}
        self._prev_slow_ma: dict[str, float] = {}
        # Restore persisted state from database (survives restarts)
        self._restore_state()

    @property
    def name(self) -> str:
        return "Trend Following v2"

    def _calc_mas(self, close: pd.Series) -> tuple[pd.Series, pd.Series]:
        """Calculate fast and slow moving averages."""
        if self.p["ma_type"] == "EMA":
            fast = calc_ema(close, self.p["fast_length"])
            slow = calc_ema(close, self.p["slow_length"])
        else:
            fast = calc_sma(close, self.p["fast_length"])
            slow = calc_sma(close, self.p["slow_length"])
        return fast, slow

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_position: float,
        bars_in_trade: int,
        bars_since_exit: Optional[int] = None,
        **kwargs,
    ) -> Signal:
        """Generate trend following signal for latest bar."""
        min_bars = max(self.p["slow_length"], self.p["donchian_entry"], self.p["adx_period"]) + 20
        if len(df) < min_bars:
            return Signal(SignalType.NONE, ticker, self.name, "Insufficient data")

        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        # ─── Moving averages ────────────────────────────────────────────
        fast_ma, slow_ma = self._calc_mas(close)

        curr_fast = fast_ma.iloc[-1]
        curr_slow = slow_ma.iloc[-1]
        prev_fast = fast_ma.iloc[-2]
        prev_slow = slow_ma.iloc[-2]

        # Crossover detection
        bull_cross = prev_fast <= prev_slow and curr_fast > curr_slow
        bear_cross = prev_fast >= prev_slow and curr_fast < curr_slow
        bull_trend = curr_fast > curr_slow
        bear_trend = curr_fast < curr_slow

        # ─── Donchian channels ──────────────────────────────────────────
        donch_upper_entry = calc_donchian_upper(high, self.p["donchian_entry"])
        donch_lower_entry = calc_donchian_lower(low, self.p["donchian_entry"])
        donch_upper_exit = calc_donchian_upper(high, self.p["donchian_exit"])
        donch_lower_exit = calc_donchian_lower(low, self.p["donchian_exit"])

        curr_close = close.iloc[-1]
        curr_donch_upper = donch_upper_entry.iloc[-1]
        curr_donch_lower = donch_lower_entry.iloc[-1]
        curr_donch_exit_low = donch_lower_exit.iloc[-1]
        curr_donch_exit_high = donch_upper_exit.iloc[-1]

        bull_breakout = curr_close > curr_donch_upper if not np.isnan(curr_donch_upper) else False
        bear_breakout = curr_close < curr_donch_lower if not np.isnan(curr_donch_lower) else False

        # ─── ADX filter ─────────────────────────────────────────────────
        adx = calc_adx(df, self.p["adx_period"])
        adx_ok = adx.iloc[-1] >= self.p["adx_threshold"] if self.p["use_adx_filter"] else True

        # ─── ATR for trailing stop ──────────────────────────────────────
        atr = calc_atr(df, self.p["atr_period"])
        curr_atr = atr.iloc[-1]

        # ─── Cooldown ───────────────────────────────────────────────────
        if bars_since_exit is None:
            bars_since_exit = self._bars_since_exit.get(ticker, 100)
        cooldown_ok = (
            bars_since_exit >= self.p["cooldown_bars"]
            if self.p["use_cooldown"]
            else True
        )

        # ─── Entry signals based on mode ────────────────────────────────
        long_signal = False
        short_signal = False

        mode = self.p["entry_mode"]
        if mode == "MA Crossover":
            long_signal = bull_cross
            short_signal = bear_cross
        elif mode == "Donchian Breakout":
            long_signal = bull_breakout and current_position <= 0
            short_signal = bear_breakout and current_position >= 0
        else:  # "Both"
            long_signal = bull_cross or (bull_breakout and current_position <= 0)
            short_signal = bear_cross or (bear_breakout and current_position >= 0)

        # Apply filters
        long_entry = long_signal and adx_ok and cooldown_ok and self.p["allow_long"]
        short_entry = short_signal and adx_ok and cooldown_ok and self.p["allow_short"]

        # ─── Exit signals ───────────────────────────────────────────────
        # MA exit
        long_exit_ma = bear_cross
        short_exit_ma = bull_cross

        # Donchian exit
        long_exit_donch = curr_close < curr_donch_exit_low if not np.isnan(curr_donch_exit_low) else False
        short_exit_donch = curr_close > curr_donch_exit_high if not np.isnan(curr_donch_exit_high) else False

        # Trailing stop
        long_exit_trail = False
        short_exit_trail = False

        if self.p["use_trailing_stop"]:
            if current_position > 0:
                new_stop = curr_close - self.p["atr_mult_stop"] * curr_atr
                trail = self._trail_stops.get(ticker)
                if trail is None:
                    self._trail_stops[ticker] = new_stop
                else:
                    self._trail_stops[ticker] = max(trail, new_stop)
                long_exit_trail = curr_close < self._trail_stops[ticker]
                self._persist_state()

            elif current_position < 0:
                new_stop = curr_close + self.p["atr_mult_stop"] * curr_atr
                trail = self._trail_stops.get(ticker)
                if trail is None:
                    self._trail_stops[ticker] = new_stop
                else:
                    self._trail_stops[ticker] = min(trail, new_stop)
                short_exit_trail = curr_close > self._trail_stops[ticker]
                self._persist_state()

        # Combined exit based on mode
        if mode == "MA Crossover":
            long_exit = long_exit_ma or long_exit_trail
            short_exit = short_exit_ma or short_exit_trail
        elif mode == "Donchian Breakout":
            long_exit = long_exit_donch or long_exit_trail
            short_exit = short_exit_donch or short_exit_trail
        else:  # "Both"
            long_exit = long_exit_donch or long_exit_trail
            short_exit = short_exit_donch or short_exit_trail

        # ─── Generate signal ────────────────────────────────────────────

        # Exits first
        if current_position > 0 and long_exit:
            self._trail_stops.pop(ticker, None)
            self._bars_since_exit[ticker] = 0
            self._persist_state()
            reason = "Trail stop" if long_exit_trail else ("Donchian exit" if long_exit_donch else "MA cross exit")
            return Signal(SignalType.LONG_EXIT, ticker, self.name, reason)

        if current_position < 0 and short_exit:
            self._trail_stops.pop(ticker, None)
            self._bars_since_exit[ticker] = 0
            self._persist_state()
            reason = "Trail stop" if short_exit_trail else ("Donchian exit" if short_exit_donch else "MA cross exit")
            return Signal(SignalType.SHORT_EXIT, ticker, self.name, reason)

        # Entries (and reversals)
        if long_entry and current_position <= 0:
            self._trail_stops.pop(ticker, None)
            reason = "MA bullish cross" if bull_cross else "Donchian breakout"
            if current_position < 0:
                reason = f"Reverse: {reason}"
                # Close short first, then long entry
                return Signal(SignalType.LONG_ENTRY, ticker, self.name, reason)
            return Signal(SignalType.LONG_ENTRY, ticker, self.name, reason)

        if short_entry and current_position >= 0:
            self._trail_stops.pop(ticker, None)
            reason = "MA bearish cross" if bear_cross else "Donchian breakdown"
            if current_position > 0:
                reason = f"Reverse: {reason}"
            return Signal(SignalType.SHORT_ENTRY, ticker, self.name, reason)

        # Update cooldown tracker for flat positions
        if current_position == 0:
            self._bars_since_exit[ticker] = self._bars_since_exit.get(ticker, 100) + 1

        return Signal(SignalType.NONE, ticker, self.name, "No signal")

    # ─── State persistence (survives bot restarts) ─────────────────────

    def _persist_state(self):
        """Save trailing stops and cooldowns to database so they survive restarts."""
        try:
            from data.trade_db import save_strategy_state
            if self._trail_stops:
                save_strategy_state("trend_trail_stops", json.dumps(self._trail_stops))
            if self._bars_since_exit:
                save_strategy_state("trend_bars_since_exit", json.dumps(self._bars_since_exit))
        except Exception as e:
            logger.warning(f"Could not persist trend state: {e}")

    def _restore_state(self):
        """Restore trailing stops and cooldowns from database after restart."""
        try:
            from data.trade_db import load_strategy_state
            stops_json = load_strategy_state("trend_trail_stops")
            if stops_json:
                self._trail_stops = json.loads(stops_json)
                logger.info(f"Restored trailing stops for {len(self._trail_stops)} tickers")
            cooldown_json = load_strategy_state("trend_bars_since_exit")
            if cooldown_json:
                self._bars_since_exit = json.loads(cooldown_json)
        except Exception as e:
            logger.warning(f"Could not restore trend state: {e}")
