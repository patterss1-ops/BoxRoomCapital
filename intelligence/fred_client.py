"""FRED (Federal Reserve Economic Data) client for macro regime signals.

Source: FRED API (free, requires key from fred.stlouisfed.org).
Series: T10Y2Y (yield curve), BAMLH0A0HYM2 (HY OAS), DFF (fed rate), ICSA (jobless claims).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import requests

from intelligence.feature_store import FeatureRecord, FeatureStore

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Key macro series
MACRO_SERIES = {
    "T10Y2Y": "yield_curve_spread",      # 10Y-2Y Treasury spread
    "BAMLH0A0HYM2": "hy_oas_spread",     # High-yield OAS
    "DFF": "fed_funds_rate",              # Effective fed funds rate
    "ICSA": "initial_claims",             # Weekly initial jobless claims
}


@dataclass(frozen=True)
class FREDClientConfig:
    """Configuration for FRED API client."""

    api_key: str = ""
    timeout_seconds: float = 10.0
    max_retries: int = 2
    request_delay: float = 0.5
    source: str = "fred-macro"


class FREDClient:
    """Client for FRED economic data API."""

    def __init__(
        self,
        config: Optional[FREDClientConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        cfg = config or FREDClientConfig()
        self.config = FREDClientConfig(
            api_key=cfg.api_key or os.getenv("FRED_API_KEY", ""),
            timeout_seconds=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            request_delay=cfg.request_delay,
            source=cfg.source,
        )
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    def fetch_series(
        self,
        series_id: str,
        lookback_days: int = 365,
    ) -> list[dict[str, Any]]:
        """Fetch observations for a FRED series."""
        if not self.config.api_key:
            logger.warning("FRED_API_KEY not configured")
            return []

        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        params = {
            "series_id": series_id,
            "api_key": self.config.api_key,
            "file_type": "json",
            "observation_start": start_date,
            "observation_end": end_date,
            "sort_order": "desc",
            "limit": "100",
        }

        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.get(
                    _BASE_URL,
                    params=params,
                    timeout=self.config.timeout_seconds,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("observations", [])
                if resp.status_code in (429, 500, 502, 503):
                    if attempt < self.config.max_retries:
                        self._sleep(self.config.request_delay * (2 ** attempt))
                        continue
            except requests.RequestException as exc:
                logger.warning("FRED request failed for %s: %s", series_id, exc)
                if attempt < self.config.max_retries:
                    self._sleep(self.config.request_delay * (2 ** attempt))

        return []

    def fetch_latest_value(self, series_id: str) -> Optional[float]:
        """Fetch the most recent value for a FRED series."""
        observations = self.fetch_series(series_id, lookback_days=30)
        for obs in observations:
            value = obs.get("value", ".")
            if value != ".":
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    def fetch_macro_snapshot(self) -> dict[str, float]:
        """Fetch latest values for all key macro series.

        Returns dict: {feature_name: value}
        """
        snapshot: dict[str, float] = {}
        for series_id, feature_name in MACRO_SERIES.items():
            self._sleep(self.config.request_delay)
            value = self.fetch_latest_value(series_id)
            if value is not None:
                snapshot[feature_name] = value
        return snapshot

    def store_macro_snapshot(
        self,
        feature_store: FeatureStore,
        as_of: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch macro data and store in FeatureStore as 'macro_regime'."""
        snapshot = self.fetch_macro_snapshot()
        if not snapshot:
            return None

        event_ts = as_of or datetime.now(timezone.utc).isoformat()
        record = FeatureRecord(
            entity_id="MACRO",
            event_ts=event_ts,
            feature_set="macro_regime",
            feature_version=1,
            features=snapshot,
            metadata={"source": self.config.source},
        )

        try:
            feature_store.save(record)
            return record.record_id
        except Exception as exc:
            logger.warning("Failed to store macro snapshot: %s", exc)
            return None
