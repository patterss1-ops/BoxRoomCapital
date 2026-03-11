"""TradingView news headlines client.

Fetches public news headlines from TradingView's news API.
No API key required — public endpoint with rate limiting via request delay.

Endpoint: https://news-headlines.tradingview.com/v2/view/headlines/symbol
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://news-headlines.tradingview.com/v2/view/headlines/symbol"

# Map yfinance-style tickers to TradingView symbol format
_EXCHANGE_MAP = {
    "SPY": "AMEX:SPY",
    "QQQ": "NASDAQ:QQQ",
    "AAPL": "NASDAQ:AAPL",
    "MSFT": "NASDAQ:MSFT",
    "NVDA": "NASDAQ:NVDA",
    "TSLA": "NASDAQ:TSLA",
    "GOOGL": "NASDAQ:GOOGL",
    "AMZN": "NASDAQ:AMZN",
    "META": "NASDAQ:META",
    "DIA": "AMEX:DIA",
    "IWM": "AMEX:IWM",
    "GLD": "AMEX:GLD",
    "EWU": "AMEX:EWU",
    "EWG": "AMEX:EWG",
    "EWJ": "AMEX:EWJ",
    "EFA": "AMEX:EFA",
    "IEF": "NASDAQ:IEF",
    "VNQ": "AMEX:VNQ",
}


@dataclass(frozen=True)
class TradingViewHeadline:
    """A single news headline from TradingView."""

    headline_id: str
    title: str
    provider: str
    published_at: str  # ISO format
    ticker: str
    story_path: str = ""


@dataclass(frozen=True)
class TradingViewNewsConfig:
    """Configuration for TradingView news client."""

    timeout_seconds: float = 10.0
    max_retries: int = 2
    request_delay: float = 1.5  # Conservative — no published rate limit
    headlines_per_ticker: int = 20
    source: str = "tradingview-news"


class TradingViewNewsClient:
    """Client for TradingView public news headlines API."""

    def __init__(
        self,
        config: Optional[TradingViewNewsConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        self.config = config or TradingViewNewsConfig()
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    @staticmethod
    def resolve_symbol(ticker: str) -> str:
        """Map a yfinance ticker to TradingView exchange:symbol format."""
        upper = ticker.strip().upper()
        if upper in _EXCHANGE_MAP:
            return _EXCHANGE_MAP[upper]
        # Default: try NASDAQ, then bare symbol
        if ":" in upper:
            return upper
        return f"NASDAQ:{upper}"

    def fetch_headlines(
        self,
        ticker: str,
        limit: int | None = None,
    ) -> list[TradingViewHeadline]:
        """Fetch news headlines for a single ticker."""
        symbol = self.resolve_symbol(ticker)
        upper_ticker = ticker.strip().upper()
        count = limit or self.config.headlines_per_ticker

        params = {
            "client": "web",
            "lang": "en",
            "section": "",
            "streaming": "",
            "symbol": symbol,
            "limit": str(count),
        }

        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.get(
                    _BASE_URL,
                    params=params,
                    timeout=self.config.timeout_seconds,
                    headers={"User-Agent": "BoxRoomCapital/1.0"},
                )
                if resp.status_code == 200:
                    return self._parse_response(resp.json(), upper_ticker)
                if resp.status_code in (429, 500, 502, 503):
                    if attempt < self.config.max_retries:
                        self._sleep(self.config.request_delay * (2 ** attempt))
                        continue
                logger.warning("TradingView news HTTP %d for %s", resp.status_code, symbol)
                return []
            except requests.RequestException as exc:
                logger.warning("TradingView news request failed for %s: %s", symbol, exc)
                if attempt < self.config.max_retries:
                    self._sleep(self.config.request_delay * (2 ** attempt))
                    continue
        return []

    def fetch_batch(
        self,
        tickers: Sequence[str],
        limit: int | None = None,
    ) -> dict[str, list[TradingViewHeadline]]:
        """Fetch headlines for multiple tickers."""
        result: dict[str, list[TradingViewHeadline]] = {}
        for ticker in tickers:
            self._sleep(self.config.request_delay)
            result[ticker.upper()] = self.fetch_headlines(ticker, limit=limit)
        return result

    @staticmethod
    def _parse_response(
        data: Any,
        ticker: str,
    ) -> list[TradingViewHeadline]:
        """Parse raw API response into TradingViewHeadline objects."""
        items = data if isinstance(data, list) else data.get("items", data.get("stories", []))
        if not isinstance(items, list):
            return []

        headlines: list[TradingViewHeadline] = []
        for item in items:
            try:
                title = item.get("title", "")
                if not title:
                    continue

                headline_id = str(item.get("id", ""))
                provider = item.get("provider", item.get("source", "tradingview"))

                ts = item.get("published", 0)
                if isinstance(ts, (int, float)) and ts > 0:
                    published = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                else:
                    published = datetime.now(timezone.utc).isoformat()

                story_path = item.get("storyPath", "")

                headlines.append(
                    TradingViewHeadline(
                        headline_id=headline_id,
                        title=str(title)[:500],
                        provider=str(provider),
                        published_at=published,
                        ticker=ticker,
                        story_path=str(story_path),
                    )
                )
            except Exception:
                continue

        return headlines
