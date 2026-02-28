"""
CityIndex CIAPI broker implementation.
Handles authentication, order placement, and position management
via the CityIndex REST API for spread betting.
"""
import logging
import time
from datetime import datetime
from typing import Optional

import requests

from broker.base import BaseBroker, OrderResult, Position, AccountInfo
import config

logger = logging.getLogger(__name__)


class CityIndexBroker(BaseBroker):
    """CityIndex CIAPI spread betting broker."""

    def __init__(self, is_demo: bool = True):
        self.is_demo = is_demo
        self.base_url = config.CITYINDEX_URLS["demo" if is_demo else "live"]
        self.username = config.CITYINDEX_USERNAME
        self.password = config.CITYINDEX_PASSWORD
        self.session_token: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        # Cache of market name → market ID mappings
        self._market_ids: dict[str, int] = {}

    # ─── Authentication ──────────────────────────────────────────────────

    def connect(self) -> bool:
        """Authenticate with CIAPI and get session token."""
        url = f"{self.base_url}/session"
        payload = {
            "UserName": self.username,
            "Password": self.password,
            "AppKey": config.CITYINDEX_APP_KEY or "trading_bot",
        }

        try:
            resp = self.session.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            self.session_token = data.get("Session")
            if not self.session_token:
                logger.error(f"Login failed: {data}")
                return False

            logger.info(
                f"CityIndex {'DEMO' if self.is_demo else 'LIVE'} connected. "
                f"Session: {self.session_token[:8]}..."
            )
            return True

        except requests.RequestException as e:
            logger.error(f"CityIndex login error: {e}")
            return False

    def disconnect(self):
        """End the CIAPI session."""
        if not self.session_token:
            return

        url = f"{self.base_url}/session"
        params = {"UserName": self.username, "Session": self.session_token}

        try:
            self.session.delete(url, params=params, timeout=10)
            logger.info("CityIndex disconnected")
        except requests.RequestException as e:
            logger.warning(f"Disconnect error (non-fatal): {e}")
        finally:
            self.session_token = None

    def _api_get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """Make authenticated GET request."""
        if not self.session_token:
            logger.error("Not connected — call connect() first")
            return None

        url = f"{self.base_url}/{endpoint}"
        p = {"UserName": self.username, "Session": self.session_token}
        if params:
            p.update(params)

        try:
            resp = self.session.get(url, params=p, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"API GET {endpoint} error: {e}")
            return None

    def _api_post(self, endpoint: str, payload: dict) -> Optional[dict]:
        """Make authenticated POST request."""
        if not self.session_token:
            logger.error("Not connected — call connect() first")
            return None

        url = f"{self.base_url}/{endpoint}"
        params = {"UserName": self.username, "Session": self.session_token}

        try:
            resp = self.session.post(url, json=payload, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"API POST {endpoint} error: {e}")
            return None

    # ─── Market search ───────────────────────────────────────────────────

    def search_market(self, search_term: str) -> list[dict]:
        """
        Search for markets by name. Returns list of market info dicts.
        Used to find CityIndex market IDs for our instruments.
        """
        data = self._api_get("market/search", {"SearchByMarketName": search_term, "MaxResults": 10})
        if data and "Markets" in data:
            return data["Markets"]
        return []

    def get_market_id(self, ci_market_name: str) -> Optional[int]:
        """Get CityIndex market ID for a market name. Caches results."""
        if ci_market_name in self._market_ids:
            return self._market_ids[ci_market_name]

        markets = self.search_market(ci_market_name)
        if markets:
            # Take the first spread bet market that matches
            for m in markets:
                if "spread" in m.get("Name", "").lower() or "DFB" in m.get("Name", ""):
                    self._market_ids[ci_market_name] = m["MarketId"]
                    logger.info(f"Mapped '{ci_market_name}' → MarketId {m['MarketId']} ({m['Name']})")
                    return m["MarketId"]

            # Fallback to first result
            market_id = markets[0]["MarketId"]
            self._market_ids[ci_market_name] = market_id
            logger.info(f"Mapped '{ci_market_name}' → MarketId {market_id} ({markets[0]['Name']})")
            return market_id

        logger.warning(f"No market found for '{ci_market_name}'")
        return None

    # ─── Account info ────────────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        """Get account balance and margin info."""
        data = self._api_get("margin/ClientAccountMargin")
        if not data:
            return AccountInfo(balance=0, equity=0, unrealised_pnl=0, open_positions=0)

        margin = data.get("ClientAccountMargin", {})
        return AccountInfo(
            balance=margin.get("Cash", 0),
            equity=margin.get("TotalEquity", 0),
            unrealised_pnl=margin.get("UnrealisedProfitLoss", 0),
            open_positions=margin.get("OpenTradeCount", 0),
            currency=margin.get("Currency", "GBP"),
        )

    # ─── Positions ───────────────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        """Get all open positions."""
        data = self._api_get("order/openpositions")
        if not data or "OpenPositions" not in data:
            return []

        positions = []
        for op in data["OpenPositions"]:
            positions.append(Position(
                ticker=str(op.get("MarketId", "")),
                direction="long" if op.get("Direction", "").lower() == "buy" else "short",
                size=op.get("Quantity", 0),
                entry_price=op.get("Price", 0),
                entry_time=datetime.now(),  # CIAPI returns timestamps differently
                strategy="unknown",  # We track this separately
                unrealised_pnl=op.get("UnrealisedPnL", 0),
            ))
        return positions

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        """Get position for a specific ticker. Filters from all positions."""
        ci_name = config.MARKET_MAP.get(ticker, {}).get("ci_name", ticker)
        market_id = self.get_market_id(ci_name)
        if not market_id:
            return None

        all_pos = self.get_positions()
        for p in all_pos:
            if p.ticker == str(market_id):
                return p
        return None

    # ─── Order placement ─────────────────────────────────────────────────

    def place_long(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        """Place a buy spread bet."""
        return self._place_order(ticker, "buy", stake_per_point, strategy)

    def place_short(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        """Place a sell spread bet."""
        return self._place_order(ticker, "sell", stake_per_point, strategy)

    def _place_order(self, ticker: str, direction: str, stake: float, strategy: str) -> OrderResult:
        """Place a spread bet order via CIAPI."""
        ci_name = config.MARKET_MAP.get(ticker, {}).get("ci_name", ticker)
        market_id = self.get_market_id(ci_name)
        if not market_id:
            return OrderResult(success=False, message=f"Market not found: {ci_name}")

        payload = {
            "MarketId": market_id,
            "Direction": direction,
            "Quantity": stake,
            "TradingAccountId": "",  # Will be populated from account info
            "OfferPrice": 0,  # Market order
            "BidPrice": 0,
            "AuditId": "",
            "AutoRollover": False,
        }

        # Get trading account ID
        acct = self._api_get("useraccount/ClientAndTradingAccount")
        if acct and "TradingAccounts" in acct:
            for ta in acct["TradingAccounts"]:
                if ta.get("TradingAccountType", "").lower() == "spread":
                    payload["TradingAccountId"] = ta["TradingAccountId"]
                    break

        if not payload["TradingAccountId"]:
            return OrderResult(success=False, message="No spread betting account found")

        logger.info(f"Placing {direction} order: {ci_name} @ £{stake}/pt [{strategy}]")
        data = self._api_post("order/newtradeorder", payload)

        if data and data.get("OrderId"):
            return OrderResult(
                success=True,
                order_id=str(data["OrderId"]),
                fill_price=data.get("Price", 0),
                fill_qty=stake,
                timestamp=datetime.now(),
            )

        error_msg = data.get("StatusReason", "Unknown error") if data else "No response"
        return OrderResult(success=False, message=error_msg)

    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        """Close an open position by placing an opposing trade."""
        pos = self.get_position(ticker, strategy)
        if not pos:
            return OrderResult(success=False, message=f"No position found for {ticker}")

        # Close by placing opposite direction
        close_direction = "sell" if pos.direction == "long" else "buy"
        return self._place_order(ticker, close_direction, pos.size, strategy)
