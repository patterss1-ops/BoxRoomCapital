"""
Data provider using yfinance for daily OHLC bars.
Also provides technical indicator calculations.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class DataProvider:
    """Fetches and caches daily OHLC data from yfinance."""

    def __init__(self, lookback_days: int = 400, market_monitor: Optional[object] = None, provider_name: str = "yfinance"):
        """
        Args:
            lookback_days: How many calendar days of history to fetch.
                          400 days covers 200 EMA warmup + trading signals.
        """
        self.lookback_days = lookback_days
        self._cache: dict[str, pd.DataFrame] = {}
        self.market_monitor = market_monitor
        self.provider_name = provider_name

    def get_daily_bars(self, ticker: str, force_refresh: bool = False) -> pd.DataFrame:
        """
        Fetch daily OHLC bars for a ticker.

        Returns DataFrame with columns: Open, High, Low, Close, Volume
        Index is DatetimeIndex (timezone-naive, daily).
        """
        if ticker in self._cache and not force_refresh:
            return self._cache[ticker]

        end = datetime.now()
        start = end - timedelta(days=self.lookback_days)
        use_max = self.lookback_days >= 10000

        if use_max:
            logger.info(f"Fetching {ticker} data — MAX available history")
        else:
            logger.info(f"Fetching {ticker} data from {start.date()} to {end.date()}")

        try:
            if use_max:
                df = yf.download(
                    ticker,
                    period="max",
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                )
            else:
                df = yf.download(
                    ticker,
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                )

            if df.empty:
                logger.warning(f"No data returned for {ticker}")
                self._record_failure()
                return pd.DataFrame()

            # Flatten multi-level columns if present (yfinance sometimes returns these)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Ensure timezone-naive index
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            self._cache[ticker] = df
            logger.info(f"Got {len(df)} bars for {ticker}, latest: {df.index[-1].date()}")
            self._record_success(ticker)
            return df

        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
            self._record_failure()
            return pd.DataFrame()

    def get_vix(self) -> pd.DataFrame:
        """Fetch VIX data."""
        return self.get_daily_bars("^VIX")

    def clear_cache(self):
        """Clear all cached data (call at start of each daily run)."""
        self._cache.clear()

    def _record_success(self, ticker: str):
        monitor = self.market_monitor
        if monitor and hasattr(monitor, "record_success"):
            try:
                monitor.record_success(self.provider_name, ticker=ticker)
            except Exception:
                logger.warning("Failed to record market data success", exc_info=True)

    def _record_failure(self):
        monitor = self.market_monitor
        if monitor and hasattr(monitor, "record_failure"):
            try:
                monitor.record_failure(self.provider_name)
            except Exception:
                logger.warning("Failed to record market data failure", exc_info=True)


# ─── INDICATOR CALCULATIONS ──────────────────────────────────────────────────


def calc_ibs(df: pd.DataFrame) -> pd.Series:
    """
    Internal Bar Strength: (Close - Low) / (High - Low)
    Returns 0.5 when range is zero.
    """
    bar_range = df["High"] - df["Low"]
    ibs = (df["Close"] - df["Low"]) / bar_range
    ibs = ibs.where(bar_range > 0, 0.5)
    return ibs


def calc_rsi(series: pd.Series, period: int = 2) -> pd.Series:
    """
    RSI using Wilder's smoothing (exponential moving average).
    Matches Pine Script's ta.rsi() exactly.
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Wilder's smoothing = RMA = EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average. Matches Pine Script's ta.ema()."""
    return series.ewm(span=period, adjust=False).mean()


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average. Matches Pine Script's ta.sma()."""
    return series.rolling(window=period).mean()


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range using Wilder's smoothing.
    Matches Pine Script's ta.atr().
    """
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index.
    Matches Pine Script's ADX calculation in Trend_Following_v2.pine.
    """
    high = df["High"]
    low = df["Low"]

    # Directional movement
    plus_dm = (high - high.shift(1)).clip(lower=0)
    minus_dm = (low.shift(1) - low).clip(lower=0)

    # Zero out the smaller one (Pine Script logic)
    mask_plus = plus_dm > minus_dm
    mask_minus = minus_dm > plus_dm
    mask_equal = plus_dm == minus_dm

    plus_dm_clean = plus_dm.where(mask_plus, 0.0)
    minus_dm_clean = minus_dm.where(mask_minus, 0.0)
    # When equal, both are zero (Pine Script behavior)
    plus_dm_clean = plus_dm_clean.where(~mask_equal, 0.0)
    minus_dm_clean = minus_dm_clean.where(~mask_equal, 0.0)

    # True range (single period)
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder's smoothing (RMA)
    smooth_plus_dm = plus_dm_clean.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smooth_minus_dm = minus_dm_clean.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smooth_tr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # Directional indicators
    plus_di = 100.0 * smooth_plus_dm / smooth_tr
    minus_di = 100.0 * smooth_minus_dm / smooth_tr

    # DX
    di_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    dx = dx.where(di_sum > 0, 0.0)

    # ADX = RMA of DX
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return adx


def calc_donchian_upper(series: pd.Series, period: int) -> pd.Series:
    """Donchian upper channel (highest high over N periods, shifted 1 bar)."""
    return series.rolling(window=period).max().shift(1)


def calc_donchian_lower(series: pd.Series, period: int) -> pd.Series:
    """Donchian lower channel (lowest low over N periods, shifted 1 bar)."""
    return series.rolling(window=period).min().shift(1)


def calc_consecutive_down_days(close: pd.Series) -> pd.Series:
    """Count consecutive days where close < previous close."""
    is_down = close < close.shift(1)
    # Group consecutive Trues and count within each group
    groups = (~is_down).cumsum()
    counts = is_down.groupby(groups).cumsum()
    return counts.astype(int)
