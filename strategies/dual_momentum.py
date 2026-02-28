"""
Dual Momentum GEM (Global Equities Momentum) Strategy.

Based on Gary Antonacci's "Dual Momentum Investing" (2014).

Two-step monthly rotation:
  1. Relative momentum: Compare US equities vs International equities
     over a lookback period. Pick the winner.
  2. Absolute momentum: If the winner's return over the lookback is negative,
     rotate entirely into bonds (safe haven) instead.

Evidence base:
  - Antonacci (2014): CAGR ~15%, max DD ~18%, Sharpe ~0.9 over 1974-2013
  - Academically validated: Jegadeesh & Titman (1993), Asness et al (2013)
  - Monthly rebalance = low turnover (~4-6 trades/year)
  - Simple 3-asset model, no curve-fitting, purely systematic

Default assets:
  SPY  — US Equities (S&P 500)
  EFA  — International Developed Equities (MSCI EAFE)
  AGG  — US Aggregate Bonds (safe haven)

The strategy is ALL-IN on one asset at a time (no partial allocation).
Designed for IBKR ISA (long-only ETFs, zero financing, tax-free).
"""
import logging
from typing import Optional

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType

logger = logging.getLogger(__name__)

# Default parameters
DEFAULT_DUAL_MOMENTUM_PARAMS = {
    # Momentum lookback
    "lookback_days": 252,            # 12 months (~252 trading days)
    # Rebalance cadence
    "rebalance_day": 1,              # Trading day of month to rebalance
    # Asset roles
    "us_equity": "SPY",              # US equities proxy
    "intl_equity": "EFA",            # International equities proxy
    "safe_haven": "AGG",             # Bonds / safe haven
    # Absolute momentum threshold
    "abs_momentum_threshold": 0.0,   # Winner must beat this return (0 = positive return)
    # Optional: use excess return over risk-free (T-bill) rate
    "use_excess_return": False,
    "risk_free_ticker": "BIL",       # 1-3 month T-bill ETF proxy
}


class DualMomentumStrategy(BaseStrategy):
    """
    Dual Momentum GEM — relative + absolute momentum rotation.

    Usage:
        dm = DualMomentumStrategy(params={"lookback_days": 252})

        # Call for each ticker with the full universe data:
        signal = dm.generate_signal(
            ticker="SPY",
            df=spy_daily_bars,
            current_position=1.0,
            bars_in_trade=22,
            universe_data={
                "SPY": spy_df,
                "EFA": efa_df,
                "AGG": agg_df,
            },
        )

    The strategy determines which single asset to hold. For the chosen asset,
    it returns LONG_ENTRY. For non-chosen assets, it returns LONG_EXIT (if
    currently held) or NONE (if already flat).
    """

    def __init__(self, params: Optional[dict] = None):
        self.p = {**DEFAULT_DUAL_MOMENTUM_PARAMS, **(params or {})}
        # Track rebalance state
        self._last_rebalance_month: Optional[int] = None
        self._trading_day_of_month: int = 0
        # Current regime: which asset should be held
        self._current_pick: Optional[str] = None
        self._pick_reason: str = "INIT"

    @property
    def name(self) -> str:
        return "Dual Momentum GEM"

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_position: float,
        bars_in_trade: int,
        **kwargs,
    ) -> Signal:
        """
        Generate dual momentum signal for a single ticker.

        The strategy considers all three assets (US, international, bonds)
        but returns a signal specific to the ticker being evaluated.
        Pass universe_data with all three assets' DataFrames.

        Args:
            ticker: The ticker being evaluated (e.g., "SPY")
            df: Daily OHLC DataFrame for this ticker
            current_position: Current position size
            bars_in_trade: Bars since entry
            **kwargs: Must include universe_data dict with all 3 assets

        Returns:
            Signal for this specific ticker
        """
        universe_data = kwargs.get("universe_data", {})

        min_bars = self.p["lookback_days"] + 20

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

        # ── Compute momentum scores ──────────────────────────────────────
        us_ticker = self.p["us_equity"]
        intl_ticker = self.p["intl_equity"]
        safe_ticker = self.p["safe_haven"]

        # Get DataFrames — the current ticker's df is passed directly,
        # others come from universe_data
        all_data = {**universe_data}
        all_data[ticker] = df  # Ensure current ticker is included

        us_df = all_data.get(us_ticker)
        intl_df = all_data.get(intl_ticker)
        safe_df = all_data.get(safe_ticker)

        # Need at least US and international data for relative momentum
        if us_df is None or intl_df is None:
            return Signal(
                SignalType.NONE, ticker, self.name,
                f"Missing universe data (need {us_ticker} and {intl_ticker})"
            )

        if len(us_df) < min_bars or len(intl_df) < min_bars:
            return Signal(
                SignalType.NONE, ticker, self.name,
                "Insufficient universe data for momentum calculation"
            )

        # Calculate returns over lookback period
        lb = self.p["lookback_days"]
        us_return = self._calc_return(us_df, lb)
        intl_return = self._calc_return(intl_df, lb)

        # Optional: excess return over risk-free
        rf_return = 0.0
        if self.p["use_excess_return"]:
            rf_df = all_data.get(self.p["risk_free_ticker"])
            if rf_df is not None and len(rf_df) >= min_bars:
                rf_return = self._calc_return(rf_df, lb)

        us_excess = us_return - rf_return
        intl_excess = intl_return - rf_return

        # ── Step 1: Relative Momentum ─────────────────────────────────────
        # Which equity market is stronger?
        if us_excess >= intl_excess:
            winner = us_ticker
            winner_return = us_return
            winner_excess = us_excess
            loser = intl_ticker
        else:
            winner = intl_ticker
            winner_return = intl_return
            winner_excess = intl_excess
            loser = us_ticker

        # ── Step 2: Absolute Momentum ─────────────────────────────────────
        # Does the winner have positive absolute momentum?
        abs_threshold = self.p["abs_momentum_threshold"]

        if winner_excess > abs_threshold:
            # Winner passes absolute momentum — hold winner
            pick = winner
            reason = (
                f"REL+ABS: {winner} wins "
                f"(US={us_return*100:+.1f}%, INTL={intl_return*100:+.1f}%, "
                f"winner excess={winner_excess*100:+.1f}%)"
            )
        else:
            # Winner fails absolute momentum — rotate to safe haven
            pick = safe_ticker
            reason = (
                f"ABS FAIL: {winner} has negative momentum "
                f"({winner_excess*100:+.1f}%), rotating to {safe_ticker} "
                f"(US={us_return*100:+.1f}%, INTL={intl_return*100:+.1f}%)"
            )

        self._current_pick = pick
        self._pick_reason = reason

        logger.info(f"Dual Momentum: pick={pick}, {reason}")

        # ── Generate signal for this specific ticker ──────────────────────
        if ticker == pick:
            # This ticker should be held
            if current_position <= 0:
                return Signal(
                    SignalType.LONG_ENTRY,
                    ticker,
                    self.name,
                    f"Rebalance: BUY — {reason}",
                    size_multiplier=1.0,  # All-in on winner
                )
            else:
                return Signal(
                    SignalType.NONE,
                    ticker,
                    self.name,
                    f"Rebalance done, HOLD — {reason}",
                )
        else:
            # This ticker should NOT be held
            if current_position > 0:
                return Signal(
                    SignalType.LONG_EXIT,
                    ticker,
                    self.name,
                    f"Rebalance: EXIT — pick is {pick} ({reason})",
                )
            else:
                return Signal(
                    SignalType.NONE,
                    ticker,
                    self.name,
                    f"Rebalance done, FLAT — pick is {pick}",
                )

    def get_current_pick(self) -> tuple[Optional[str], str]:
        """Return the current asset pick and reason (for dashboards)."""
        return self._current_pick, self._pick_reason

    def score_universe(
        self,
        universe_data: dict[str, pd.DataFrame],
    ) -> dict:
        """
        Score the universe (convenience method for dashboards).

        Returns dict with momentum scores and current pick.
        Does NOT generate signals — use generate_signal() for that.
        """
        us_ticker = self.p["us_equity"]
        intl_ticker = self.p["intl_equity"]
        safe_ticker = self.p["safe_haven"]
        lb = self.p["lookback_days"]
        min_bars = lb + 20

        result = {
            "us_ticker": us_ticker,
            "intl_ticker": intl_ticker,
            "safe_ticker": safe_ticker,
            "lookback_days": lb,
        }

        us_df = universe_data.get(us_ticker)
        intl_df = universe_data.get(intl_ticker)

        if us_df is None or intl_df is None or len(us_df) < min_bars or len(intl_df) < min_bars:
            result["error"] = "Insufficient data"
            return result

        us_ret = self._calc_return(us_df, lb)
        intl_ret = self._calc_return(intl_df, lb)

        rf_return = 0.0
        if self.p["use_excess_return"]:
            rf_df = universe_data.get(self.p["risk_free_ticker"])
            if rf_df is not None and len(rf_df) >= min_bars:
                rf_return = self._calc_return(rf_df, lb)

        us_excess = us_ret - rf_return
        intl_excess = intl_ret - rf_return

        winner = us_ticker if us_excess >= intl_excess else intl_ticker
        winner_excess = us_excess if winner == us_ticker else intl_excess

        abs_pass = winner_excess > self.p["abs_momentum_threshold"]
        pick = winner if abs_pass else safe_ticker

        result.update({
            "us_return_pct": round(us_ret * 100, 2),
            "intl_return_pct": round(intl_ret * 100, 2),
            "rf_return_pct": round(rf_return * 100, 2),
            "us_excess_pct": round(us_excess * 100, 2),
            "intl_excess_pct": round(intl_excess * 100, 2),
            "relative_winner": winner,
            "absolute_momentum_pass": abs_pass,
            "pick": pick,
        })

        return result

    def _is_rebalance_day(self, df: pd.DataFrame) -> bool:
        """Detect if the latest bar is a rebalance day."""
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

    @staticmethod
    def _calc_return(df: pd.DataFrame, lookback: int) -> float:
        """
        Calculate simple return over lookback period.

        Returns fractional return (e.g., 0.10 = +10%).
        """
        if df is None or len(df) < lookback + 1:
            return 0.0

        close = df["Close"]
        current = float(close.iloc[-1])
        past = float(close.iloc[-lookback - 1])

        if past == 0:
            return 0.0

        return (current - past) / past
