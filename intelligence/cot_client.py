"""CFTC Commitment of Traders (COT) client.

Source: CFTC public data (free, weekly Friday release).
Parses trader positioning for ES/NQ/GC/CL/ZN futures.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from intelligence.feature_store import FeatureRecord, FeatureStore

logger = logging.getLogger(__name__)

# CFTC Disaggregated Futures-Only report
_COT_URL = "https://www.cftc.gov/dea/newcot/deafut.txt"

# Contract name fragments → feature keys
_CONTRACT_MAP = {
    "E-MINI S&P 500": "es_net_position",
    "NASDAQ-100": "nq_net_position",
    "GOLD": "gc_net_position",
    "CRUDE OIL": "cl_net_position",
    "10-YEAR": "zn_net_position",
    "10 YEAR": "zn_net_position",
}


@dataclass(frozen=True)
class COTClientConfig:
    """Configuration for CFTC COT client."""

    timeout_seconds: float = 30.0
    max_retries: int = 2
    source: str = "cftc-cot"


class COTClient:
    """Client for CFTC Commitment of Traders data."""

    def __init__(
        self,
        config: Optional[COTClientConfig] = None,
        session: Optional[requests.Session] = None,
    ):
        self.config = config or COTClientConfig()
        self._session = session or requests.Session()

    def fetch_cot_data(self) -> dict[str, float]:
        """Fetch latest COT data and extract net positions.

        Returns dict of feature_key → commercial net position.
        Commercial net long = positive (bullish contrarian).
        """
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.get(
                    _COT_URL,
                    timeout=self.config.timeout_seconds,
                )
                if resp.status_code == 200:
                    return self._parse_cot_txt(resp.text)
                if attempt < self.config.max_retries:
                    time.sleep(2.0 * (attempt + 1))
            except Exception as exc:
                logger.warning("COT fetch failed (attempt %d): %s", attempt, exc)
                if attempt < self.config.max_retries:
                    time.sleep(2.0 * (attempt + 1))

        return {}

    def _parse_cot_txt(self, text: str) -> dict[str, float]:
        """Parse CFTC TXT format into feature dict."""
        positions: dict[str, float] = {}

        try:
            reader = csv.reader(io.StringIO(text))
            headers = next(reader, None)
            if headers is None:
                return positions

            # Find relevant column indices
            header_lower = [h.strip().lower() for h in headers]

            # Try to find commercial long/short columns
            name_idx = 0  # Market name is typically first column
            comm_long_idx = None
            comm_short_idx = None

            for i, h in enumerate(header_lower):
                if "commercial" in h and "long" in h and "spread" not in h:
                    comm_long_idx = i
                elif "commercial" in h and "short" in h and "spread" not in h:
                    comm_short_idx = i

            if comm_long_idx is None or comm_short_idx is None:
                # Fallback: parse positional (standard CFTC format)
                # Commercial Long is typically column 8, Short column 9
                comm_long_idx = 8 if len(header_lower) > 10 else None
                comm_short_idx = 9 if len(header_lower) > 10 else None

            if comm_long_idx is None:
                return positions

            for row in reader:
                if len(row) <= max(comm_long_idx, comm_short_idx):
                    continue

                market_name = row[name_idx].strip().upper()

                for contract_fragment, feature_key in _CONTRACT_MAP.items():
                    if contract_fragment in market_name:
                        try:
                            comm_long = float(row[comm_long_idx].replace(",", ""))
                            comm_short = float(row[comm_short_idx].replace(",", ""))
                            positions[feature_key] = comm_long - comm_short
                        except (ValueError, IndexError):
                            pass
                        break

        except Exception as exc:
            logger.warning("COT parse failed: %s", exc)

        return positions

    def store_cot_data(
        self,
        feature_store: FeatureStore,
        as_of: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch COT data and store in FeatureStore as 'cot_positioning'."""
        data = self.fetch_cot_data()
        if not data:
            return None

        event_ts = as_of or datetime.now(timezone.utc).isoformat()
        record = FeatureRecord(
            entity_id="MACRO",
            event_ts=event_ts,
            feature_set="cot_positioning",
            feature_version=1,
            features=data,
            metadata={"source": self.config.source},
        )

        try:
            feature_store.save(record)
            return record.record_id
        except Exception as exc:
            logger.warning("Failed to store COT data: %s", exc)
            return None
