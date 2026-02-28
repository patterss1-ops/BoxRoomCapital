"""
GTAA (Global Tactical Asset Allocation) Trend Following Strategy.

Based on Meb Faber's "A Quantitative Approach to Tactical Asset Allocation" (2007).

Monthly rebalance across a diversified multi-asset universe.
Each asset held only when above its 10-month (200-day) SMA — absolute momentum filter.
Equal-weight across all qualifying assets; cash for those below SMA.

Evidence base:
  - Faber (2007): CAGR ~10%, max DD ~15%, Sharpe ~0.8 over 1973-2008
  - Survives out-of-sample: 2009-2025 backtests confirm similar risk-adjusted returns
  - Simple, monthly, rules-based — minimal execution cost and turnover

Default universe (5 assets):
  SPY  — US Equities (S&P 500)
  EFA  — International Developed Equities
  IEF  — US Intermediate Treasuries
  VNQ  — REITs
  DBC  — Commodities (or GSG)

Designed for IBKR ISA (long-only ETFs, zero financing, tax-free).
"""
import logging
from typing import Optional

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType
from data.provider import calc_sma

logger = logging.getLogger(__name__)

# Default parameters — can be overridden via config or constructor
DEFAULT_GTAA_PARAMS = {
    # Trend filter
    "sma_period": 200,              # 10-month SMA (~200 trading days)
    # Rebalance cadence
    "rebalance_day": 1,             # Trading day of month to rebalance
    # Universe tickers (default Faber 5-asset)
    "universe": ["SPY", "EFA", "IEF", "VNQ", "DBC"],
    # Weight mode: "equal" = 1/N among universe, regardless of how many qualify
    # Each qualifying asset gets 1/N of total portfolio. Non-qualifying go to cash.
    "weight_mode": "equal",
    # Use absolute momentum (SMA filter). If False, always hold all assets.
    "use_trend_filter": True,
}


class GTAAStrategy(BaseStrategy):
    """
    GTAA trend following with monthly rebalance and SMA filter.

    Usage:
        gtaa = GTAAStrategy(params={"sma_period": 200, "rebalance_day": 1})

        For each ticker in the universe, call:
            signal = gtaa.generate_signal(
                ticker="SPY",
                df=spy_daily_bars,
                current_position=1.0,     # >0 if holding
                bars_in_trade=45,
                universe_data={           # other assets' DataFrames (optional)
                    "EFA": efa_df,
                    "IEF": ief_df,
                    ...
                },
            )

    The strategy makes independent hold/don't-hold decisions per asset based
    on whether price > SMA. Cross-asset data (universe_data) is accepted
    for future extensions (e.g., risk parity weighting, correlation adjustments)
    but is not required for the core GTAA logic.
    """

    def __init__(self, params: Optional[dict] = None):
        self.p = {**DEFAULT_GTAA_PARAMS, **(params or {})}
        # Track rebalance state
        self._last_rebalance_month: Optional[int] = None
        self._trading_day_of_month: int = 0

    @property
    def name(self) -> str:
        return "GTAA Trend Following"

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_position: float,
        bars_in_trade: int,
        **kwargs,
    ) -> Signal:
        """
        Generate GTAA signal for a single asset.

        Each asset is independently scored: hold if price > SMA, exit if below.
        Signals only fire on rebalance days (monthly).

        Args:
            ticker: Asset ticker (e.g., "SPY")
            df: Daily OHLC DataFrame with DatetimeIndex
            current_position: Current position size (>0 = holding, 0 = flat)
            bars_in_trade: Bars since entry
            **kwargs: Optional universe_data dict with other assets' DataFrames

        Returns:
            Signal with LONG_ENTRY, LONG_EXIT, or NONE
        """
        min_bars = self.p["sma_period"] + 20

        if df is None or len(df) < min_bars:
            return Signal(
                SignalType.NONE, ticker, self.name,
                f"Insufficient data ({len(df) if df is not None else 0} bars, need {min_bars})"
            )

        # ── Detect rebalance day ──────────────────────────────────────────
        is_rebalance = self._is_rebalance_day(df)

        if not is_rebalance:
            return Signal(
                SignalType.NONE, ticker, self.name,
                f"Day {self._trading_day_of_month}, waiting for rebalance"
            )

        # ── Trend filter: price vs SMA ────────────────────────────────────
        close = df["Close"]
        current_price = float(close.iloc[-1])
        sma = calc_sma(close, self.p["sma_period"])
        current_sma = float(sma.iloc[-1])

        above_sma = current_price > current_sma
        trend_pass = above_sma if self.p["use_trend_filter"] else True

        # ── Size multiplier ───────────────────────────────────────────────
        # Equal weight: 1/N where N = total universe size
        universe_size = len(self.p["universe"])
        weight = 1.0 / universe_size if universe_size > 0 else 1.0

        # ── Generate signal ───────────────────────────────────────────────
        pct_from_sma = ((current_price - current_sma) / current_sma * 100) if current_sma != 0 else 0

        logger.info(
            f"GTAA {ticker}: price={current_price:.2f}, SMA({self.p['sma_period']})={current_sma:.2f}, "
            f"{'ABOVE' if above_sma else 'BELOW'} ({pct_from_sma:+.1f}%), "
            f"trend_pass={trend_pass}, weight={weight:.2f}"
        )

        if trend_pass and current_position <= 0:
            # Above SMA and not holding — enter long
            return Signal(
                SignalType.LONG_ENTRY,
                ticker,
                self.name,
                f"Rebalance: ABOVE SMA({self.p['sma_period']}) by {pct_from_sma:+.1f}%",
                size_multiplier=weight,
            )

        elif not trend_pass and current_position > 0:
            # Below SMA and holding — exit to cash
            return Signal(
                SignalType.LONG_EXIT,
                ticker,
                self.name,
                f"Rebalance: BELOW SMA({self.p['sma_period']}) by {pct_from_sma:+.1f}%",
            )

        else:
            # Already correctly positioned
            status = "HOLD" if current_position > 0 else "CASH"
            return Signal(
                SignalType.NONE,
                ticker,
                self.name,
                f"Rebalance done, {status} (SMA {pct_from_sma:+.1f}%)",
            )

    def _is_rebalance_day(self, df: pd.DataFrame) -> bool:
        """
        Detect if the latest bar is a rebalance day.

        Tracks trading day of month and fires on the configured day.
        """
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

        return self._trading_day_of_month == self.p["rebalance_day"]

    def score_universe(
        self,
        universe_data: dict[str, pd.DataFrame],
    ) -> dict[str, dict]:
        """
        Score all assets in the universe (convenience method for dashboards).

        Returns dict of {ticker: {above_sma, pct_from_sma, should_hold, weight}}.
        Does NOT generate signals — use generate_signal() for that.
        """
        universe_size = len(self.p["universe"])
        weight = 1.0 / universe_size if universe_size > 0 else 1.0
        results = {}

        for ticker in self.p["universe"]:
            df = universe_data.get(ticker)
            if df is None or len(df) < self.p["sma_period"] + 1:
                results[ticker] = {
                    "above_sma": None,
                    "pct_from_sma": None,
                    "should_hold": False,
                    "weight": 0.0,
                    "error": "Insufficient data",
                }
                continue

            close = df["Close"]
            current_price = float(close.iloc[-1])
            sma_val = float(calc_sma(close, self.p["sma_period"]).iloc[-1])
            above = current_price > sma_val
            pct = ((current_price - sma_val) / sma_val * 100) if sma_val != 0 else 0

            should_hold = above if self.p["use_trend_filter"] else True

            results[ticker] = {
                "above_sma": above,
                "pct_from_sma": round(pct, 2),
                "should_hold": should_hold,
                "weight": weight if should_hold else 0.0,
            }

        return results
