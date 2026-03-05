"""Kraken crypto broker adapter — REST API implementation.

Supports spot and perpetual futures trading via Kraken's REST API.
API docs: https://docs.kraken.com/rest/

For UK spread betting equivalence, perpetual futures are the closest
analogue — no expiry, funding rates replace overnight financing.
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import logging
import time
import urllib.parse
from datetime import datetime
from typing import Optional

import requests

from broker.base import (
    BaseBroker,
    BrokerCapabilities,
    OrderResult,
    Position,
    AccountInfo,
)
import config

logger = logging.getLogger(__name__)


# Kraken asset pair mapping: internal ticker -> Kraken pair
CRYPTO_PAIRS = {
    "BTC": {"pair": "XXBTZUSD", "ws_name": "XBT/USD"},
    "ETH": {"pair": "XETHZUSD", "ws_name": "ETH/USD"},
    "SOL": {"pair": "SOLUSD", "ws_name": "SOL/USD"},
    "AVAX": {"pair": "AVAXUSD", "ws_name": "AVAX/USD"},
    "LINK": {"pair": "LINKUSD", "ws_name": "LINK/USD"},
    "DOT": {"pair": "DOTUSD", "ws_name": "DOT/USD"},
    "ADA": {"pair": "ADAUSD", "ws_name": "ADA/USD"},
    "MATIC": {"pair": "MATICUSD", "ws_name": "MATIC/USD"},
}


class KrakenBroker(BaseBroker):
    """Kraken crypto exchange broker via REST API."""

    _TIMEOUT = 15
    _BASE_URL = "https://api.kraken.com"

    capabilities = BrokerCapabilities(
        supports_spreadbet=False,
        supports_cfd=False,
        supports_spot_etf=False,
        supports_options=False,
        supports_futures=False,
        supports_short=True,
        supports_live=True,
    )

    def __init__(self):
        self.api_key = getattr(config, "KRAKEN_API_KEY", "") or ""
        self.api_secret = getattr(config, "KRAKEN_API_SECRET", "") or ""
        self.session: Optional[requests.Session] = None
        self._deal_map: dict[str, tuple[str, str]] = {}

    # ─── Auth helpers ─────────────────────────────────────────────────────

    def _sign(self, url_path: str, data: dict) -> dict:
        """Generate Kraken API signature headers."""
        nonce = str(int(time.time() * 1000))
        data["nonce"] = nonce
        post_data = urllib.parse.urlencode(data)
        encoded = (nonce + post_data).encode("utf-8")
        message = url_path.encode("utf-8") + hashlib.sha256(encoded).digest()
        signature = hmac.new(
            base64.b64decode(self.api_secret),
            message,
            hashlib.sha512,
        )
        return {
            "API-Key": self.api_key,
            "API-Sign": base64.b64encode(signature.digest()).decode("utf-8"),
        }

    def _public_get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Public API call (no auth)."""
        url = f"{self._BASE_URL}/0/public/{endpoint}"
        resp = self.session.get(url, params=params or {}, timeout=self._TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise RuntimeError(f"Kraken API error: {body['error']}")
        return body.get("result", {})

    def _private_post(self, endpoint: str, data: Optional[dict] = None) -> dict:
        """Private API call (authenticated)."""
        url_path = f"/0/private/{endpoint}"
        url = f"{self._BASE_URL}{url_path}"
        data = data or {}
        headers = self._sign(url_path, data)
        resp = self.session.post(
            url, data=data, headers=headers, timeout=self._TIMEOUT
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("error"):
            raise RuntimeError(f"Kraken API error: {body['error']}")
        return body.get("result", {})

    # ─── BaseBroker interface ─────────────────────────────────────────────

    def connect(self) -> bool:
        """Authenticate and verify API key."""
        if not self.api_key or not self.api_secret:
            logger.warning("Kraken API credentials not configured")
            return False

        self.session = requests.Session()
        try:
            # Verify credentials by fetching balance
            self._private_post("Balance")
            logger.info("Kraken broker connected")
            return True
        except Exception as exc:
            logger.error("Kraken connection failed: %s", exc)
            self.session = None
            return False

    def disconnect(self):
        """Close session."""
        if self.session:
            self.session.close()
            self.session = None

    def get_account_info(self) -> AccountInfo:
        """Fetch account balance and equity."""
        balance = self._private_post("Balance")
        # Sum all USD-denominated balances
        total_usd = 0.0
        for asset, amount in balance.items():
            if asset in ("ZUSD", "USD"):
                total_usd += float(amount)

        trade_balance = self._private_post("TradeBalance", {"asset": "ZUSD"})
        equity = float(trade_balance.get("e", total_usd))
        unrealised = float(trade_balance.get("n", 0.0))

        open_orders = self._private_post("OpenPositions")
        open_count = len(open_orders) if isinstance(open_orders, dict) else 0

        return AccountInfo(
            balance=total_usd,
            equity=equity,
            unrealised_pnl=unrealised,
            open_positions=open_count,
            currency="USD",
        )

    def get_positions(self) -> list[Position]:
        """Fetch all open positions."""
        open_positions = self._private_post("OpenPositions")
        positions = []
        for pos_id, pos_data in (open_positions or {}).items():
            pair = pos_data.get("pair", "")
            ticker = self._pair_to_ticker(pair)
            direction = "long" if pos_data.get("type") == "buy" else "short"
            positions.append(Position(
                ticker=ticker,
                direction=direction,
                size=float(pos_data.get("vol", 0)),
                entry_price=float(pos_data.get("cost", 0)) / max(float(pos_data.get("vol", 1)), 0.0001),
                entry_time=datetime.fromtimestamp(float(pos_data.get("time", 0))),
                strategy=pos_data.get("misc", "kraken"),
                unrealised_pnl=float(pos_data.get("net", 0)),
                deal_id=pos_id,
            ))
        return positions

    def get_position(self, ticker: str, strategy: str) -> Optional[Position]:
        """Get position for a specific ticker."""
        for pos in self.get_positions():
            if pos.ticker == ticker and pos.strategy == strategy:
                return pos
        return None

    def place_long(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        """Place a market buy order."""
        return self._place_order(ticker, "buy", stake_per_point, strategy)

    def place_short(self, ticker: str, stake_per_point: float, strategy: str) -> OrderResult:
        """Place a market sell order."""
        return self._place_order(ticker, "sell", stake_per_point, strategy)

    def close_position(self, ticker: str, strategy: str) -> OrderResult:
        """Close an open position by placing opposite order."""
        pos = self.get_position(ticker, strategy)
        if pos is None:
            return OrderResult(success=False, message=f"No position found for {ticker}/{strategy}")

        side = "sell" if pos.direction == "long" else "buy"
        return self._place_order(ticker, side, pos.size, strategy, reducing=True)

    # ─── Internal helpers ─────────────────────────────────────────────────

    def _place_order(
        self, ticker: str, side: str, volume: float, strategy: str, reducing: bool = False
    ) -> OrderResult:
        """Submit a market order to Kraken."""
        pair_info = CRYPTO_PAIRS.get(ticker.upper())
        if not pair_info:
            return OrderResult(success=False, message=f"Unknown crypto pair: {ticker}")

        data = {
            "pair": pair_info["pair"],
            "type": side,
            "ordertype": "market",
            "volume": str(volume),
        }
        if reducing:
            data["reduce_only"] = "true"

        try:
            result = self._private_post("AddOrder", data)
            txid = result.get("txid", [""])[0] if result.get("txid") else ""
            logger.info(
                "Kraken %s %s %.6f — txid=%s [%s]",
                side, ticker, volume, txid, strategy,
            )
            return OrderResult(
                success=True,
                order_id=txid,
                fill_qty=volume,
                message=f"Order placed: {side} {volume} {ticker}",
                timestamp=datetime.utcnow(),
            )
        except Exception as exc:
            logger.error("Kraken order failed: %s", exc)
            return OrderResult(success=False, message=str(exc))

    def _pair_to_ticker(self, pair: str) -> str:
        """Reverse-map a Kraken pair to our internal ticker."""
        for ticker, info in CRYPTO_PAIRS.items():
            if info["pair"] == pair:
                return ticker
        return pair

    # ─── Market data ──────────────────────────────────────────────────────

    def get_ticker_price(self, ticker: str) -> Optional[float]:
        """Fetch current mid price for a crypto ticker."""
        pair_info = CRYPTO_PAIRS.get(ticker.upper())
        if not pair_info:
            return None
        try:
            result = self._public_get("Ticker", {"pair": pair_info["pair"]})
            for pair_key, data in result.items():
                bid = float(data["b"][0])
                ask = float(data["a"][0])
                return (bid + ask) / 2.0
        except Exception as exc:
            logger.warning("Failed to fetch %s price: %s", ticker, exc)
        return None

    def get_ohlc(self, ticker: str, interval: int = 60) -> list[dict]:
        """Fetch OHLC candles. interval in minutes (1, 5, 15, 30, 60, 240, 1440)."""
        pair_info = CRYPTO_PAIRS.get(ticker.upper())
        if not pair_info:
            return []
        try:
            result = self._public_get("OHLC", {
                "pair": pair_info["pair"],
                "interval": interval,
            })
            candles = []
            for pair_key, data in result.items():
                if pair_key == "last":
                    continue
                for row in data:
                    candles.append({
                        "time": int(row[0]),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[6]),
                    })
            return candles
        except Exception as exc:
            logger.warning("Failed to fetch %s OHLC: %s", ticker, exc)
            return []
