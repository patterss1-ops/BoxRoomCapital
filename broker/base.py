"""
Abstract broker interface. All broker implementations (paper, CityIndex) implement this.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class BrokerCapabilities:
    """Broker feature matrix used by pre-trade capability validation."""

    supports_spreadbet: bool = False
    supports_cfd: bool = False
    supports_spot_etf: bool = False
    supports_options: bool = False
    supports_futures: bool = False
    supports_short: bool = False
    supports_paper: bool = False
    supports_live: bool = True


@dataclass
class OrderResult:
    """Result of an order placement."""
    success: bool
    order_id: str = ""
    fill_price: float = 0.0
    fill_qty: float = 0.0
    message: str = ""
    timestamp: Optional[datetime] = None


@dataclass
class Position:
    """An open position."""
    ticker: str
    direction: str  # "long" or "short"
    size: float  # stake per point
    entry_price: float
    entry_time: datetime
    strategy: str
    unrealised_pnl: float = 0.0
    deal_id: str = ""


@dataclass
class AccountInfo:
    """Broker account summary."""
    balance: float
    equity: float
    unrealised_pnl: float
    open_positions: int
    currency: str = "GBP"


@dataclass
class OptionMarket:
    """An available option on IG."""
    epic: str
    strike: float
    option_type: str  # "PUT" or "CALL"
    expiry: str  # e.g. "04-MAR-26"
    bid: float = 0.0
    offer: float = 0.0
    mid: float = 0.0
    spread_pct: float = 0.0  # bid/offer spread as % of mid
    instrument_name: str = ""


@dataclass
class SpreadOrderResult:
    """Result of a 2-leg option spread order."""
    success: bool
    short_deal_id: str = ""
    long_deal_id: str = ""
    short_fill_price: float = 0.0
    long_fill_price: float = 0.0
    net_premium: float = 0.0  # credit received (short - long)
    size: float = 0.0
    message: str = ""
    timestamp: Optional[datetime] = None


class BaseBroker(ABC):
    """Abstract broker interface."""

    capabilities = BrokerCapabilities()

    def get_capabilities(self) -> BrokerCapabilities:
        """Return broker feature matrix for pre-trade validation."""
        return getattr(self, "capabilities", BrokerCapabilities())

    def supports_capability(self, capability_name: str) -> bool:
        """Boolean helper for dynamic capability checks."""
        caps = self.get_capabilities()
        return bool(getattr(caps, capability_name, False))

    @abstractmethod
    def connect(self) -> bool:
        """Connect to broker / authenticate. Returns True if successful."""
        pass

    @abstractmethod
    def disconnect(self):
        """Close broker connection / logout."""
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """Get current account status."""
        pass

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Get all open positions."""
        pass

    @abstractmethod
    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        """Get position for a specific ticker+strategy combination."""
        pass

    @abstractmethod
    def place_long(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        """Open a long position (buy spread bet)."""
        pass

    @abstractmethod
    def place_short(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        """Open a short position (sell spread bet)."""
        pass

    @abstractmethod
    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        """Close an open position."""
        pass

    # ─── Options methods (optional — only IGBroker implements these) ───────

    def search_option_markets(self, search_term: str) -> list[OptionMarket]:
        """Search for available option markets. Returns list of OptionMarket."""
        raise NotImplementedError("Options not supported by this broker")

    def get_option_price(self, epic: str) -> Optional[OptionMarket]:
        """Get current bid/offer for a specific option EPIC."""
        raise NotImplementedError("Options not supported by this broker")

    def place_option_spread(
        self,
        short_epic: str,
        long_epic: str,
        size: float,
        ticker: str,
        strategy: str,
        correlation_id: str = "",
    ) -> SpreadOrderResult:
        """
        Place a 2-leg credit spread: sell short_epic, buy long_epic.
        Returns SpreadOrderResult with both deal IDs.
        """
        raise NotImplementedError("Options not supported by this broker")

    def close_option_spread(
        self,
        short_deal_id: str,
        long_deal_id: str,
        size: float,
        correlation_id: str = "",
    ) -> SpreadOrderResult:
        """Close both legs of an option spread."""
        raise NotImplementedError("Options not supported by this broker")

    def validate_option_leg(self, epic: str, size: float) -> dict:
        """Validate that an option order leg is currently tradeable."""
        raise NotImplementedError("Options not supported by this broker")
