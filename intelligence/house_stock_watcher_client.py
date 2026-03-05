"""House Stock Watcher client for L5 Congressional Trading.

Source: Free GitHub dataset at house-stock-watcher-data.s3-us-west-2.amazonaws.com
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import requests

from intelligence.capitol_trades_client import Chamber, CongressionalTrade, TradeDirection

logger = logging.getLogger(__name__)

_DATA_URL = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"


@dataclass(frozen=True)
class HouseStockWatcherConfig:
    """Configuration for House Stock Watcher client."""

    timeout_seconds: float = 30.0
    max_retries: int = 2
    source: str = "house-stock-watcher"


def _parse_direction(txn_type: str) -> Optional[TradeDirection]:
    """Parse transaction type to TradeDirection."""
    lower = txn_type.strip().lower()
    if "purchase" in lower:
        return TradeDirection.BUY
    if "sale" in lower:
        return TradeDirection.SELL
    return None


def _parse_value_range(amount: str) -> tuple[float, float]:
    """Parse value range like '$1,001 - $15,000' into (low, high)."""
    try:
        cleaned = amount.replace("$", "").replace(",", "").strip()
        if " - " in cleaned:
            parts = cleaned.split(" - ")
            return float(parts[0].strip()), float(parts[1].strip())
        if cleaned:
            val = float(cleaned)
            return val, val
    except (ValueError, IndexError):
        pass
    return 0.0, 0.0


class HouseStockWatcherClient:
    """Client for House Stock Watcher congressional trading data."""

    def __init__(
        self,
        config: Optional[HouseStockWatcherConfig] = None,
        session: Optional[requests.Session] = None,
    ):
        self.config = config or HouseStockWatcherConfig()
        self._session = session or requests.Session()
        self._cache: list[dict[str, Any]] = []
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 3600.0  # 1 hour cache

    def _fetch_all(self) -> list[dict[str, Any]]:
        """Fetch full transaction dataset (cached for 1 hour)."""
        now = time.time()
        if self._cache and (now - self._cache_ts) < self._cache_ttl:
            return self._cache

        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.get(
                    _DATA_URL,
                    timeout=self.config.timeout_seconds,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        self._cache = data
                        self._cache_ts = now
                        return data
            except Exception as exc:
                logger.warning("House Stock Watcher fetch failed (attempt %d): %s", attempt, exc)
                if attempt < self.config.max_retries:
                    time.sleep(1.0 * (attempt + 1))

        return self._cache  # Return stale cache if available

    def fetch_trades_for_ticker(self, ticker: str, days_back: int = 180) -> list[CongressionalTrade]:
        """Fetch congressional trades for a specific ticker."""
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        all_txns = self._fetch_all()
        trades: list[CongressionalTrade] = []

        for item in all_txns:
            try:
                item_ticker = str(item.get("ticker", "")).strip().upper()
                if item_ticker != symbol:
                    continue

                direction = _parse_direction(str(item.get("type", "")))
                if direction is None:
                    continue

                member = str(item.get("representative", "")).strip()
                if not member:
                    continue

                trade_date = str(item.get("transaction_date", "")).strip()
                disclosure_date = str(item.get("disclosure_date", "")).strip()
                if not trade_date:
                    continue

                amount = str(item.get("amount", ""))
                low, high = _parse_value_range(amount)

                trades.append(
                    CongressionalTrade(
                        ticker=symbol,
                        member_name=member,
                        chamber=Chamber.HOUSE,
                        direction=direction,
                        trade_date=trade_date,
                        disclosure_date=disclosure_date or trade_date,
                        estimated_value_low=low,
                        estimated_value_high=high,
                        source_ref="house-stock-watcher",
                    )
                )
            except Exception:
                continue

        return trades

    def fetch_batch(self, tickers: Sequence[str], days_back: int = 180) -> dict[str, list[CongressionalTrade]]:
        """Fetch congressional trades for multiple tickers."""
        # Pre-fetch the full dataset once
        self._fetch_all()
        return {t.upper(): self.fetch_trades_for_ticker(t, days_back) for t in tickers}
