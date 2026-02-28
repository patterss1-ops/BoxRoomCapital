"""
SPY/TLT Dual Momentum Rotation Strategy v3/v4 — Python translation of SPY_TLT_Rotation_v3.pine

Monthly rebalance. Relative momentum picks winner, absolute momentum vetoes losers.
200 SMA cash filter. No daily exits (learned from v2 whipsaw death).

v3: Long or Cash (never short)
v4: Long or Short loser (earns DFB financing on short side)
    Set allow_short_loser=True in ROTATION_PARAMS to enable v4 behaviour.
"""
import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType
from data.provider import calc_sma
import config

logger = logging.getLogger(__name__)


class SPYTLTRotation(BaseStrategy):
    """SPY/TLT Dual Momentum Rotation v3/v4."""

    def __init__(self, params: Optional[dict] = None):
        self.p = params or config.ROTATION_PARAMS
        # Track state across calls
        self._hold_signal: int = 0  # 1 = hold primary, -1 = hold partner, 0 = cash
        self._hold_reason: str = "INIT"
        self._last_rebalance_month: Optional[int] = None
        self._trading_day_of_month: int = 0

    @property
    def name(self) -> str:
        allow_short = self.p.get("allow_short_loser", False)
        return "SPY/TLT Rotation v4" if allow_short else "SPY/TLT Rotation v3"

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_position: float,
        bars_in_trade: int,
        partner_df: Optional[pd.DataFrame] = None,
        **kwargs,
    ) -> Signal:
        """
        Generate rotation signal. Only acts on rebalance days.

        Args:
            ticker: Primary ticker (e.g., SPY)
            df: Primary ticker OHLC data
            partner_df: Partner ticker OHLC data (e.g., TLT)
        """
        lookback = max(self.p["lookback_days"], self.p["abs_mom_lookback"], self.p["cash_ma_period"]) + 20

        if partner_df is None or len(df) < lookback or len(partner_df) < lookback:
            return Signal(SignalType.NONE, ticker, self.name, "Insufficient data")

        # ─── Detect rebalance day ───────────────────────────────────────
        dates = df.index
        current_date = dates[-1]
        prev_date = dates[-2] if len(dates) > 1 else current_date

        current_month = current_date.month
        prev_month = prev_date.month
        is_new_month = current_month != prev_month

        if is_new_month:
            self._trading_day_of_month = 1
        else:
            self._trading_day_of_month += 1

        is_rebalance_day = self._trading_day_of_month == self.p["rebalance_day"]

        if not is_rebalance_day:
            # Not rebalance day — maintain current position
            return Signal(SignalType.NONE, ticker, self.name, f"Day {self._trading_day_of_month}, waiting for rebalance")

        # ─── Calculations (only on rebalance day) ───────────────────────
        this_close = df["Close"]
        other_close = partner_df["Close"]

        # Align dates (use the dates available in both)
        aligned = pd.DataFrame({
            "this": this_close,
            "other": other_close,
        }).dropna()

        if len(aligned) < lookback:
            return Signal(SignalType.NONE, ticker, self.name, "Insufficient aligned data")

        this_now = aligned["this"].iloc[-1]
        other_now = aligned["other"].iloc[-1]

        # Relative momentum (which asset is winning over lookback?)
        lb = self.p["lookback_days"]
        this_lb = aligned["this"].iloc[-lb - 1] if len(aligned) > lb else aligned["this"].iloc[0]
        other_lb = aligned["other"].iloc[-lb - 1] if len(aligned) > lb else aligned["other"].iloc[0]

        this_rel_mom = ((this_now - this_lb) / this_lb * 100) if this_lb != 0 else 0
        other_rel_mom = ((other_now - other_lb) / other_lb * 100) if other_lb != 0 else 0

        # Absolute momentum (is the asset going up over longer period?)
        abs_lb = self.p["abs_mom_lookback"]
        this_abs_lb = aligned["this"].iloc[-abs_lb - 1] if len(aligned) > abs_lb else aligned["this"].iloc[0]
        other_abs_lb = aligned["other"].iloc[-abs_lb - 1] if len(aligned) > abs_lb else aligned["other"].iloc[0]

        this_abs_mom = ((this_now - this_abs_lb) / this_abs_lb * 100) if this_abs_lb != 0 else 0
        other_abs_mom = ((other_now - other_abs_lb) / other_abs_lb * 100) if other_abs_lb != 0 else 0

        # Cash filter SMA
        this_sma = calc_sma(aligned["this"], self.p["cash_ma_period"])
        other_sma = calc_sma(aligned["other"], self.p["cash_ma_period"])

        both_below_sma = False
        if self.p["use_cash_filter"]:
            both_below_sma = this_now < this_sma.iloc[-1] and other_now < other_sma.iloc[-1]

        # ─── Rotation decision ──────────────────────────────────────────
        prev_signal = self._hold_signal

        if both_below_sma:
            self._hold_signal = 0
            self._hold_reason = "BOTH < SMA"
        elif this_rel_mom >= other_rel_mom:
            # Primary wins relative momentum
            if self.p["use_abs_momentum"] and this_abs_mom < 0:
                self._hold_signal = 0
                self._hold_reason = "ABS MOM NEG"
            else:
                self._hold_signal = 1
                self._hold_reason = "REL+ABS WIN"
        else:
            # Partner wins relative momentum
            if self.p["use_abs_momentum"] and other_abs_mom < 0:
                self._hold_signal = 0
                self._hold_reason = "OTH ABS NEG"
            else:
                self._hold_signal = -1
                self._hold_reason = "OTHER WINS"

        logger.info(
            f"Rotation rebalance: this_rel={this_rel_mom:.2f}%, other_rel={other_rel_mom:.2f}%, "
            f"this_abs={this_abs_mom:.2f}%, signal={self._hold_signal}, reason={self._hold_reason}"
        )

        allow_short = self.p.get("allow_short_loser", False)

        # ─── Generate signal ────────────────────────────────────────────
        # Signal  1 = hold primary LONG (buy SPY)
        # Signal -1 = partner wins → go cash OR short primary (v4)
        # Signal  0 = both weak   → go cash OR short primary (v4)
        #
        # v4: when allow_short_loser=True, instead of going to cash we
        # SHORT the primary. On IG DFBs, shorting earns overnight financing
        # (~2% net pa), turning the cost headwind into income.

        if self._hold_signal == 1:
            # Want to be LONG primary
            if current_position < 0:
                # Currently short — close short first
                return Signal(
                    SignalType.SHORT_EXIT,
                    ticker,
                    self.name,
                    f"Rebalance: cover short, {self._hold_reason}",
                )
            elif current_position == 0:
                return Signal(
                    SignalType.LONG_ENTRY,
                    ticker,
                    self.name,
                    f"Rebalance: {self._hold_reason} (rel={this_rel_mom:.1f}%)",
                )
        elif self._hold_signal <= 0:
            # Want to be CASH or SHORT
            if current_position > 0:
                # Currently long — close long
                return Signal(
                    SignalType.LONG_EXIT,
                    ticker,
                    self.name,
                    f"Rebalance: {self._hold_reason}",
                )
            elif current_position == 0 and allow_short:
                # Flat — enter short (v4 only)
                return Signal(
                    SignalType.SHORT_ENTRY,
                    ticker,
                    self.name,
                    f"Rebalance SHORT: {self._hold_reason} (rel={this_rel_mom:.1f}%)",
                )
            elif current_position < 0 and not allow_short:
                # Close stale short if allow_short was toggled off
                return Signal(
                    SignalType.SHORT_EXIT,
                    ticker,
                    self.name,
                    f"Rebalance: closing short (short_loser disabled)",
                )

        hold_str = "LONG" if self._hold_signal == 1 else ("SHORT" if allow_short and self._hold_signal <= 0 else "CASH")
        return Signal(
            SignalType.NONE,
            ticker,
            self.name,
            f"Rebalance done, holding={hold_str}",
        )
