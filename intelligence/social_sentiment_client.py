"""Multi-source social sentiment aggregator for L6 enhancement.

Sources:
1. Stocktwits (free, no auth) — bullish/bearish sentiment per ticker
2. EODHD Tweets Sentiment (~$20/mo) — pre-aggregated $cashtag sentiment
3. SA News via existing SA_RAPIDAPI_KEY

Normalizes all into NewsArticle records for L6 scoring.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import requests

from app.signal.layers.news_sentiment import NewsArticle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SocialSentimentConfig:
    """Configuration for social sentiment aggregator."""

    eodhd_api_key: str = ""
    sa_rapidapi_key: str = ""
    timeout_seconds: float = 10.0
    request_delay: float = 0.5
    source: str = "social-sentiment"


class SocialSentimentClient:
    """Multi-source social sentiment aggregator."""

    def __init__(
        self,
        config: Optional[SocialSentimentConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        cfg = config or SocialSentimentConfig()
        self.config = SocialSentimentConfig(
            eodhd_api_key=cfg.eodhd_api_key or os.getenv("EODHD_API_KEY", ""),
            sa_rapidapi_key=cfg.sa_rapidapi_key or os.getenv("SA_RAPIDAPI_KEY", ""),
            timeout_seconds=cfg.timeout_seconds,
            request_delay=cfg.request_delay,
            source=cfg.source,
        )
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def _fetch_stocktwits(self, ticker: str) -> list[NewsArticle]:
        """Fetch sentiment from Stocktwits public API (no auth required)."""
        symbol = ticker.strip().upper()
        articles: list[NewsArticle] = []

        try:
            url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
            resp = self._session.get(url, timeout=self.config.timeout_seconds)
            if resp.status_code != 200:
                return articles

            data = resp.json()
            messages = data.get("messages", [])
            for msg in messages[:20]:  # Limit to 20 most recent
                body = msg.get("body", "")
                if not body:
                    continue

                # Map Stocktwits sentiment
                entities = msg.get("entities", {})
                sentiment_obj = entities.get("sentiment", {}) if isinstance(entities, dict) else {}
                basic = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None

                if basic == "Bullish":
                    sentiment = 0.6
                elif basic == "Bearish":
                    sentiment = -0.6
                else:
                    sentiment = 0.0

                created = msg.get("created_at", "")
                if created:
                    # Stocktwits format: "2024-01-15T10:30:00Z"
                    published = created
                else:
                    published = datetime.now(timezone.utc).isoformat()

                articles.append(
                    NewsArticle(
                        ticker=symbol,
                        headline=body[:300],
                        published_at=published,
                        sentiment=sentiment,
                        source="stocktwits",
                        relevance=0.6,
                    )
                )
        except Exception as exc:
            logger.debug("Stocktwits fetch failed for %s: %s", symbol, exc)

        return articles

    def _fetch_eodhd_tweets(self, ticker: str) -> list[NewsArticle]:
        """Fetch tweet sentiment from EODHD API."""
        if not self.config.eodhd_api_key:
            return []

        symbol = ticker.strip().upper()
        articles: list[NewsArticle] = []

        try:
            url = f"https://eodhd.com/api/sentiments?s={symbol}&api_token={self.config.eodhd_api_key}&fmt=json"
            resp = self._session.get(url, timeout=self.config.timeout_seconds)
            if resp.status_code != 200:
                return articles

            data = resp.json()
            if not isinstance(data, dict):
                return articles

            # EODHD returns aggregated sentiment data
            ticker_data = data.get(symbol, {})
            if isinstance(ticker_data, dict):
                sentiment_score = ticker_data.get("normalized", 0.0)
                count = ticker_data.get("count", 0)
                if count > 0:
                    articles.append(
                        NewsArticle(
                            ticker=symbol,
                            headline=f"Twitter sentiment aggregate: {count} tweets",
                            published_at=datetime.now(timezone.utc).isoformat(),
                            sentiment=max(-1.0, min(1.0, float(sentiment_score))),
                            source="twitter",
                            relevance=0.7,
                        )
                    )
        except Exception as exc:
            logger.debug("EODHD tweet sentiment failed for %s: %s", symbol, exc)

        return articles

    def fetch_social_sentiment(self, ticker: str) -> list[NewsArticle]:
        """Fetch sentiment from all available social sources."""
        symbol = ticker.strip().upper()
        articles: list[NewsArticle] = []

        # Source 1: Stocktwits (always free)
        articles.extend(self._fetch_stocktwits(symbol))
        self._sleep(self.config.request_delay)

        # Source 2: EODHD tweets (if configured)
        articles.extend(self._fetch_eodhd_tweets(symbol))

        return articles

    def fetch_batch(self, tickers: Sequence[str]) -> dict[str, list[NewsArticle]]:
        """Fetch social sentiment for multiple tickers."""
        result: dict[str, list[NewsArticle]] = {}
        for ticker in tickers:
            self._sleep(self.config.request_delay)
            result[ticker.upper()] = self.fetch_social_sentiment(ticker)
        return result
