"""M-003: Adaptive volatility-adjusted position sizing.

Computes position sizes that scale inversely with asset volatility,
ensuring consistent dollar-risk per trade across instruments of
varying realised vol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class VolatilityMethod(Enum):
    """Supported volatility estimation methods."""

    ATR = "atr"
    ROLLING_STD = "rolling_std"
    EWMA = "ewma"


@dataclass
class SizingConfig:
    """Configuration for adaptive position sizing."""

    method: VolatilityMethod = VolatilityMethod.ATR
    lookback_period: int = 20
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 10.0
    min_position_size: float = 1.0
    volatility_scale_factor: float = 1.0
    ewma_span: int = 20


@dataclass
class SizingResult:
    """Result of an adaptive position size calculation."""

    ticker: str
    raw_size: float
    adjusted_size: float
    volatility: float
    risk_amount: float
    method: VolatilityMethod
    capped: bool
    floored: bool


class AdaptivePositionSizer:
    """Volatility-adaptive position sizer.

    Sizes positions so that the dollar risk per trade stays constant
    regardless of the underlying asset's realised volatility.
    """

    def __init__(self, config: Optional[SizingConfig] = None) -> None:
        self._config = config if config is not None else SizingConfig()

    # ------------------------------------------------------------------
    # Volatility estimation
    # ------------------------------------------------------------------

    def compute_volatility(
        self,
        prices: list[float],
        method: Optional[VolatilityMethod] = None,
    ) -> float:
        """Estimate volatility from a price series.

        Parameters
        ----------
        prices:
            Chronological list of prices (oldest first).
        method:
            Override the config method for this call.  Falls back to
            ``self._config.method`` when *None*.

        Returns
        -------
        float
            Non-negative volatility estimate.  Returns ``0.0`` when the
            price series is too short to compute a meaningful value.
        """
        method = method if method is not None else self._config.method
        lookback = self._config.lookback_period

        if len(prices) < 2:
            return 0.0

        if method == VolatilityMethod.ATR:
            return self._atr(prices, lookback)
        if method == VolatilityMethod.ROLLING_STD:
            return self._rolling_std(prices, lookback)
        if method == VolatilityMethod.EWMA:
            return self._ewma_std(prices)

        return 0.0  # pragma: no cover — defensive fallback

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_size(
        self,
        ticker: str,
        prices: list[float],
        portfolio_value: float,
        current_price: Optional[float] = None,
    ) -> SizingResult:
        """Calculate a volatility-adjusted position size.

        Parameters
        ----------
        ticker:
            Instrument identifier.
        prices:
            Historical price series (oldest first).
        portfolio_value:
            Total portfolio equity in dollar terms.
        current_price:
            Price used for share-count conversion.  Defaults to the
            last element of *prices*.
        """
        if current_price is None:
            current_price = prices[-1] if prices else 0.0

        method = self._config.method
        volatility = self.compute_volatility(prices, method)

        risk_amount = portfolio_value * self._config.risk_per_trade_pct / 100.0

        # Guard against zero / negative portfolio or zero volatility.
        if portfolio_value <= 0 or volatility <= 0 or current_price <= 0:
            return SizingResult(
                ticker=ticker,
                raw_size=0.0,
                adjusted_size=0.0,
                volatility=volatility,
                risk_amount=risk_amount,
                method=method,
                capped=False,
                floored=False,
            )

        scaled_vol = volatility * self._config.volatility_scale_factor
        raw_size = risk_amount / scaled_vol

        # --- constraints ---
        max_shares = (portfolio_value * self._config.max_position_pct / 100.0) / current_price
        capped = raw_size > max_shares
        adjusted = min(raw_size, max_shares)

        # Floor
        floored = adjusted < self._config.min_position_size and raw_size > 0
        if floored:
            adjusted = self._config.min_position_size

        # Round down to whole shares
        adjusted = float(math.floor(adjusted))

        return SizingResult(
            ticker=ticker,
            raw_size=raw_size,
            adjusted_size=adjusted,
            volatility=volatility,
            risk_amount=risk_amount,
            method=method,
            capped=capped,
            floored=floored,
        )

    def calculate_batch(
        self,
        tickers: list[str],
        prices_map: dict[str, list[float]],
        portfolio_value: float,
    ) -> list[SizingResult]:
        """Calculate sizes for multiple tickers."""
        results: list[SizingResult] = []
        for ticker in tickers:
            prices = prices_map.get(ticker, [])
            results.append(self.calculate_size(ticker, prices, portfolio_value))
        return results

    def get_config(self) -> SizingConfig:
        """Return a reference to the current configuration."""
        return self._config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _atr(prices: list[float], lookback: int) -> float:
        """ATR approximated as mean |price[i] - price[i-1]| over lookback."""
        diffs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        window = diffs[-lookback:] if len(diffs) >= lookback else diffs
        return sum(window) / len(window) if window else 0.0

    @staticmethod
    def _rolling_std(prices: list[float], lookback: int) -> float:
        """Standard deviation of simple returns over the lookback window."""
        returns = [
            (prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(1, len(prices))
            if prices[i - 1] != 0
        ]
        window = returns[-lookback:] if len(returns) >= lookback else returns
        if len(window) < 2:
            return 0.0
        mean = sum(window) / len(window)
        var = sum((r - mean) ** 2 for r in window) / (len(window) - 1)
        return math.sqrt(var)

    def _ewma_std(self, prices: list[float]) -> float:
        """Exponentially weighted standard deviation of returns."""
        returns = [
            (prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(1, len(prices))
            if prices[i - 1] != 0
        ]
        if len(returns) < 2:
            return 0.0

        span = self._config.ewma_span
        alpha = 2.0 / (span + 1.0)

        # Compute EWMA mean
        ewma_mean = returns[0]
        for r in returns[1:]:
            ewma_mean = alpha * r + (1 - alpha) * ewma_mean

        # Compute EWMA variance
        ewma_var = 0.0
        for r in returns:
            ewma_var = alpha * (r - ewma_mean) ** 2 + (1 - alpha) * ewma_var

        return math.sqrt(ewma_var)
