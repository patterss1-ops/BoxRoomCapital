"""Finnhub news API client for L6 News Sentiment.

Source: Finnhub news API (free tier, 60 calls/min).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import requests

from app.signal.layers.news_sentiment import NewsArticle

logger = logging.getLogger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"


@dataclass(frozen=True)
class FinnhubConfig:
    """Configuration for Finnhub client."""

    api_key: str = ""
    timeout_seconds: float = 10.0
    max_retries: int = 2
    request_delay: float = 1.1  # ~55 calls/min to stay under 60/min
    source: str = "finnhub-news"


class FinnhubNewsClient:
    """Client for Finnhub company news and sentiment."""

    def __init__(
        self,
        config: Optional[FinnhubConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        cfg = config or FinnhubConfig()
        self.config = FinnhubConfig(
            api_key=cfg.api_key or os.getenv("FINNHUB_API_KEY", ""),
            timeout_seconds=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            request_delay=cfg.request_delay,
            source=cfg.source,
        )
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def _request(self, endpoint: str, params: dict[str, str]) -> Any:
        """Make authenticated request to Finnhub API."""
        if not self.config.api_key:
            logger.warning("FINNHUB_API_KEY not configured")
            return []

        params["token"] = self.config.api_key
        url = f"{_BASE_URL}/{endpoint}"

        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.config.timeout_seconds)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503):
                    if attempt < self.config.max_retries:
                        self._sleep(self.config.request_delay * (2 ** attempt))
                        continue
                logger.warning("Finnhub HTTP %d for %s", resp.status_code, endpoint)
                return []
            except requests.RequestException as exc:
                logger.warning("Finnhub request failed: %s", exc)
                if attempt < self.config.max_retries:
                    self._sleep(self.config.request_delay * (2 ** attempt))
                    continue
        return []

    def fetch_company_news(
        self,
        ticker: str,
        days_back: int = 7,
    ) -> list[NewsArticle]:
        """Fetch company news articles and normalize to NewsArticle."""
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")

        data = self._request("company-news", {
            "symbol": symbol,
            "from": from_date,
            "to": to_date,
        })

        if not isinstance(data, list):
            return []

        articles: list[NewsArticle] = []
        for item in data:
            try:
                headline = item.get("headline", "")
                if not headline:
                    continue

                # Convert Unix timestamp to ISO
                ts = item.get("datetime", 0)
                if isinstance(ts, (int, float)) and ts > 0:
                    published = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                else:
                    published = now.isoformat()

                # Finnhub provides basic sentiment in some endpoints
                sentiment = item.get("sentiment", 0.0)
                if not isinstance(sentiment, (int, float)):
                    sentiment = 0.0

                source = item.get("source", "finnhub")
                url = item.get("url", "")

                articles.append(
                    NewsArticle(
                        ticker=symbol,
                        headline=str(headline)[:500],
                        published_at=published,
                        sentiment=max(-1.0, min(1.0, float(sentiment))),
                        source=str(source) or "finnhub",
                        relevance=0.8,
                    )
                )
            except Exception:
                continue

        return articles

    def fetch_batch(self, tickers: Sequence[str], days_back: int = 7) -> dict[str, list[NewsArticle]]:
        """Fetch news for multiple tickers."""
        result: dict[str, list[NewsArticle]] = {}
        for ticker in tickers:
            self._sleep(self.config.request_delay)
            result[ticker.upper()] = self.fetch_company_news(ticker, days_back)
        return result
