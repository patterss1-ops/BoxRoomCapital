"""
Base strategy interface. All strategies implement this.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


class SignalType(Enum):
    """Trading signal types."""
    NONE = "none"
    LONG_ENTRY = "long_entry"
    LONG_EXIT = "long_exit"
    SHORT_ENTRY = "short_entry"
    SHORT_EXIT = "short_exit"


@dataclass
class Signal:
    """A trading signal from a strategy."""
    signal_type: SignalType
    ticker: str
    strategy_name: str
    reason: str = ""
    size_multiplier: float = 1.0  # VIX sizing etc.
    timestamp: Optional[pd.Timestamp] = None

    def __repr__(self):
        return f"Signal({self.signal_type.value}, {self.ticker}, {self.strategy_name}, '{self.reason}', size={self.size_multiplier})"


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name for logging."""
        pass

    @abstractmethod
    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_position: float,
        bars_in_trade: int,
        **kwargs,
    ) -> Signal:
        """
        Generate a trading signal for the given ticker.

        Args:
            ticker: The instrument ticker
            df: DataFrame with OHLC data (most recent bar is last row)
            current_position: Current position size (>0 long, <0 short, 0 flat)
            bars_in_trade: How many bars the current position has been held
            **kwargs: Strategy-specific extra data (e.g., VIX data, partner data)

        Returns:
            Signal object
        """
        pass
