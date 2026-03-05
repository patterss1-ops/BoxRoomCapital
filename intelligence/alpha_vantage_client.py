"""Alpha Vantage client for L4 Analyst Revisions data.

Source: Alpha Vantage ANALYST_RATINGS endpoint (free tier, 500 calls/day).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import requests

from app.signal.layers.analyst_revisions import AnalystRevision, EstimateType, RevisionDirection

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.alphavantage.co/query"


@dataclass(frozen=True)
class AlphaVantageConfig:
    """Configuration for Alpha Vantage client."""

    api_key: str = ""
    timeout_seconds: float = 10.0
    max_retries: int = 2
    request_delay: float = 0.5  # Stay within 5 calls/min on free tier
    source: str = "alpha-vantage-analyst"


def _map_direction(action: str) -> RevisionDirection:
    """Map Alpha Vantage action to RevisionDirection."""
    lower = action.strip().lower()
    if lower in ("upgrade", "initiated", "reiterated", "up"):
        return RevisionDirection.UP
    if lower in ("downgrade", "down"):
        return RevisionDirection.DOWN
    return RevisionDirection.MAINTAINED


class AlphaVantageClient:
    """Client for Alpha Vantage analyst ratings and related data."""

    def __init__(
        self,
        config: Optional[AlphaVantageConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        cfg = config or AlphaVantageConfig()
        self.config = AlphaVantageConfig(
            api_key=cfg.api_key or os.getenv("ALPHA_VANTAGE_API_KEY", ""),
            timeout_seconds=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            request_delay=cfg.request_delay,
            source=cfg.source,
        )
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        """Make a request to Alpha Vantage API with retry logic."""
        if not self.config.api_key:
            logger.warning("ALPHA_VANTAGE_API_KEY not configured")
            return {}

        params["apikey"] = self.config.api_key

        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.get(
                    _BASE_URL,
                    params=params,
                    timeout=self.config.timeout_seconds,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Check for rate limit message
                    if "Note" in data or "Information" in data:
                        logger.warning("Alpha Vantage rate limit hit")
                        if attempt < self.config.max_retries:
                            self._sleep(self.config.request_delay * (2 ** attempt))
                            continue
                    return data
                if resp.status_code in (429, 500, 502, 503):
                    if attempt < self.config.max_retries:
                        self._sleep(self.config.request_delay * (2 ** attempt))
                        continue
            except requests.RequestException as exc:
                logger.warning("Alpha Vantage request failed: %s", exc)
                if attempt < self.config.max_retries:
                    self._sleep(self.config.request_delay * (2 ** attempt))
                    continue
        return {}

    def fetch_analyst_revisions(self, ticker: str) -> list[AnalystRevision]:
        """Fetch analyst ratings/revisions for a ticker."""
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        data = self._request({
            "function": "ANALYST_RATINGS",
            "symbol": symbol,
        })

        revisions: list[AnalystRevision] = []
        feed = data.get("feed", [])
        if not isinstance(feed, list):
            return revisions

        for item in feed:
            try:
                analyst = item.get("analyst", "") or item.get("source", "Unknown")
                action = item.get("action_type", "") or item.get("action", "")
                direction = _map_direction(action)

                # Estimate change percentage
                old_target = item.get("old_target")
                new_target = item.get("new_target")
                change_pct = 0.0
                if old_target and new_target:
                    try:
                        old_f = float(old_target)
                        new_f = float(new_target)
                        if old_f > 0:
                            change_pct = ((new_f - old_f) / old_f) * 100.0
                    except (ValueError, TypeError):
                        pass

                revision_date = item.get("time_published", "") or item.get("date", "")
                if revision_date and len(revision_date) >= 10:
                    revision_date = revision_date[:10]

                revisions.append(
                    AnalystRevision(
                        ticker=symbol,
                        analyst_name=str(analyst),
                        direction=direction,
                        estimate_type=EstimateType.PRICE_TARGET,
                        change_pct=round(change_pct, 2),
                        revision_date=revision_date,
                        source_ref="alpha-vantage",
                    )
                )
            except Exception:
                continue

        return revisions

    def fetch_batch(self, tickers: Sequence[str]) -> dict[str, list[AnalystRevision]]:
        """Fetch analyst revisions for multiple tickers."""
        result: dict[str, list[AnalystRevision]] = {}
        for ticker in tickers:
            self._sleep(self.config.request_delay)
            result[ticker.upper()] = self.fetch_analyst_revisions(ticker)
        return result
