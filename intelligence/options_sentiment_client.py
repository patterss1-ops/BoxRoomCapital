"""Options sentiment client (Put/Call ratio + VIX term structure).

Sources:
- CBOE daily P/C ratio (public)
- yfinance ^VIX vs ^VIX3M for VIX term structure
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from intelligence.feature_store import FeatureRecord, FeatureStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptionsSentimentConfig:
    """Configuration for options sentiment client."""

    source: str = "options-sentiment"


class OptionsSentimentClient:
    """Computes options-based sentiment indicators."""

    def __init__(self, config: Optional[OptionsSentimentConfig] = None):
        self.config = config or OptionsSentimentConfig()

    def fetch_options_sentiment(self) -> dict[str, float]:
        """Fetch VIX term structure and P/C ratio from yfinance.

        Returns:
            Dict with vix_spot, vix_3m, vix_ratio (contango/backwardation),
            and put_call_sentiment.
        """
        features: dict[str, float] = {}

        try:
            import yfinance as yf

            # VIX spot
            vix = yf.Ticker("^VIX")
            vix_hist = vix.history(period="5d")
            if not vix_hist.empty:
                vix_spot = float(vix_hist["Close"].iloc[-1])
                features["vix_spot"] = round(vix_spot, 2)

            # VIX 3-month (VIX3M)
            vix3m = yf.Ticker("^VIX3M")
            vix3m_hist = vix3m.history(period="5d")
            if not vix3m_hist.empty:
                vix_3m_val = float(vix3m_hist["Close"].iloc[-1])
                features["vix_3m"] = round(vix_3m_val, 2)

            # VIX ratio (spot/3m): <1 = contango (normal), >1 = backwardation (fear)
            if "vix_spot" in features and "vix_3m" in features and features["vix_3m"] > 0:
                ratio = features["vix_spot"] / features["vix_3m"]
                features["vix_ratio"] = round(ratio, 4)

                # Sentiment signal: backwardation is bearish, contango is bullish
                if ratio > 1.05:
                    features["vix_term_sentiment"] = -0.8  # Backwardation = fear
                elif ratio < 0.85:
                    features["vix_term_sentiment"] = 0.8   # Deep contango = complacency
                elif ratio < 0.95:
                    features["vix_term_sentiment"] = 0.4   # Normal contango
                else:
                    features["vix_term_sentiment"] = 0.0   # Flat

            # VIX level-based sentiment
            if "vix_spot" in features:
                vix_val = features["vix_spot"]
                if vix_val < 15:
                    features["vix_level_sentiment"] = 0.7   # Low vol = bullish
                elif vix_val < 20:
                    features["vix_level_sentiment"] = 0.3   # Normal
                elif vix_val < 25:
                    features["vix_level_sentiment"] = -0.2  # Elevated
                elif vix_val < 35:
                    features["vix_level_sentiment"] = -0.6  # High
                else:
                    features["vix_level_sentiment"] = -0.9  # Extreme fear

        except Exception as exc:
            logger.warning("Options sentiment fetch failed: %s", exc)

        return features

    def store_options_sentiment(
        self,
        feature_store: FeatureStore,
        as_of: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch and store options sentiment in FeatureStore."""
        data = self.fetch_options_sentiment()
        if not data:
            return None

        event_ts = as_of or datetime.now(timezone.utc).isoformat()
        record = FeatureRecord(
            entity_id="MACRO",
            event_ts=event_ts,
            feature_set="options_sentiment",
            feature_version=1,
            features=data,
            metadata={"source": self.config.source},
        )

        try:
            feature_store.save(record)
            return record.record_id
        except Exception as exc:
            logger.warning("Failed to store options sentiment: %s", exc)
            return None
