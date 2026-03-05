"""FCA Regulatory News Service client for UK director dealings.

Source: London Stock Exchange RNS feed (free, public).
Replaces ShareScope for UK insider buying data in L2.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

import requests

from intelligence.insider_signal_adapter import InsiderRole, InsiderTransaction, TransactionType

logger = logging.getLogger(__name__)

_LSE_NEWS_URL = "https://api.londonstockexchange.com/api/v1/components/newsexplorer"


@dataclass(frozen=True)
class FCAClientConfig:
    """Configuration for FCA RNS client."""

    timeout_seconds: float = 15.0
    max_retries: int = 2
    request_delay: float = 1.0
    source: str = "fca-rns-director-dealings"


class FCARNSClient:
    """Client for FCA RNS director dealing announcements.

    Parses RNS announcements from the LSE website to extract
    director buy/sell transactions for UK-listed securities.
    """

    def __init__(
        self,
        config: Optional[FCAClientConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        self.config = config or FCAClientConfig()
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def fetch_director_dealings(
        self,
        ticker: str,
        days_back: int = 90,
    ) -> list[InsiderTransaction]:
        """Fetch UK director dealing announcements for a ticker.

        Note: The LSE API structure may change; this provides a best-effort
        scrape of director dealing RNS announcements.
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        transactions: list[InsiderTransaction] = []

        try:
            # Search for director dealing announcements
            params = {
                "noofitems": "20",
                "source": "rns",
                "category": "director-dealings",
                "searchterm": symbol,
            }

            resp = self._session.get(
                _LSE_NEWS_URL,
                params=params,
                timeout=self.config.timeout_seconds,
                headers={"Accept": "application/json"},
            )

            if resp.status_code != 200:
                logger.debug("LSE RNS returned HTTP %d for %s", resp.status_code, symbol)
                return transactions

            data = resp.json()
            items = data.get("items", []) or data.get("results", [])
            if not isinstance(items, list):
                return transactions

            for item in items:
                try:
                    title = str(item.get("title", "") or item.get("headline", ""))
                    if not title:
                        continue

                    # Parse direction from title
                    title_lower = title.lower()
                    if "purchase" in title_lower or "buy" in title_lower:
                        txn_type = TransactionType.PURCHASE
                    elif "sale" in title_lower or "sell" in title_lower or "disposal" in title_lower:
                        txn_type = TransactionType.SALE
                    else:
                        continue

                    # Extract director name (usually in title)
                    director_name = "UK Director"
                    if " - " in title:
                        parts = title.split(" - ")
                        if len(parts) >= 2:
                            director_name = parts[0].strip()

                    # Date
                    date_str = str(item.get("date", "") or item.get("published", ""))
                    if not date_str:
                        date_str = datetime.now(timezone.utc).date().isoformat()

                    transactions.append(
                        InsiderTransaction(
                            ticker=symbol,
                            insider_name=director_name,
                            role=InsiderRole.DIRECTOR,
                            transaction_type=txn_type,
                            shares=0.0,  # Not always in summary
                            price_per_share=0.0,
                            filing_date=date_str[:10],
                            source_ref="fca-rns",
                        )
                    )
                except Exception:
                    continue

        except Exception as exc:
            logger.warning("FCA RNS fetch failed for %s: %s", symbol, exc)

        return transactions

    def fetch_batch(
        self,
        tickers: Sequence[str],
        days_back: int = 90,
    ) -> dict[str, list[InsiderTransaction]]:
        """Fetch director dealings for multiple UK tickers."""
        result: dict[str, list[InsiderTransaction]] = {}
        for ticker in tickers:
            self._sleep(self.config.request_delay)
            result[ticker.upper()] = self.fetch_director_dealings(ticker, days_back)
        return result
