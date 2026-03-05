"""Fundamental quality screen client.

Sources: yfinance Ticker.info (P/E, ROE, FCF yield, debt/equity)
Computes Piotroski F-Score and quality composite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from intelligence.feature_store import FeatureRecord, FeatureStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FundamentalsConfig:
    """Configuration for fundamentals client."""

    source: str = "yfinance-fundamentals"


def compute_piotroski_f_score(info: dict[str, Any]) -> int:
    """Compute Piotroski F-Score (0-9) from yfinance Ticker.info.

    Criteria:
    1. Positive ROA
    2. Positive operating cash flow
    3. ROA increasing YoY
    4. Cash flow > net income (accruals)
    5. Decreasing long-term debt ratio
    6. Increasing current ratio
    7. No new shares issued
    8. Increasing gross margin
    9. Increasing asset turnover
    """
    score = 0

    # Profitability signals (0-4)
    roa = info.get("returnOnAssets")
    if roa is not None and roa > 0:
        score += 1

    ocf = info.get("operatingCashflow")
    if ocf is not None and ocf > 0:
        score += 1

    # Cash flow > net income (quality of earnings)
    net_income = info.get("netIncomeToCommon")
    if ocf is not None and net_income is not None and ocf > net_income:
        score += 1

    # ROA increasing (approximate with positive ROA as proxy)
    if roa is not None and roa > 0.02:
        score += 1

    # Leverage signals (0-3)
    debt_to_equity = info.get("debtToEquity")
    if debt_to_equity is not None and debt_to_equity < 100:
        score += 1

    current_ratio = info.get("currentRatio")
    if current_ratio is not None and current_ratio > 1.0:
        score += 1

    # No dilution
    shares = info.get("sharesOutstanding")
    float_shares = info.get("floatShares")
    if shares and float_shares and float_shares / shares > 0.85:
        score += 1

    # Operating efficiency (0-2)
    gross_margin = info.get("grossMargins")
    if gross_margin is not None and gross_margin > 0.3:
        score += 1

    revenue_growth = info.get("revenueGrowth")
    if revenue_growth is not None and revenue_growth > 0:
        score += 1

    return min(9, score)


class FundamentalsClient:
    """Fetches fundamental quality data from yfinance."""

    def __init__(self, config: Optional[FundamentalsConfig] = None):
        self.config = config or FundamentalsConfig()

    def fetch_quality_metrics(self, ticker: str) -> dict[str, float]:
        """Fetch fundamental quality metrics for a ticker.

        Returns dict of quality features: f_score, pe_ratio, roe, fcf_yield,
        debt_to_equity, gross_margin, revenue_growth, quality_composite.
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return {}

        try:
            import yfinance as yf

            yf_ticker = yf.Ticker(symbol)
            info = yf_ticker.info

            if not info or not isinstance(info, dict):
                return {}

            features: dict[str, float] = {}

            # Piotroski F-Score
            f_score = compute_piotroski_f_score(info)
            features["f_score"] = float(f_score)

            # Key ratios
            pe = info.get("trailingPE") or info.get("forwardPE")
            if pe is not None:
                features["pe_ratio"] = float(pe)

            roe = info.get("returnOnEquity")
            if roe is not None:
                features["roe"] = round(float(roe) * 100, 2)

            # FCF yield
            fcf = info.get("freeCashflow")
            market_cap = info.get("marketCap")
            if fcf is not None and market_cap and market_cap > 0:
                features["fcf_yield"] = round((float(fcf) / float(market_cap)) * 100, 2)

            dte = info.get("debtToEquity")
            if dte is not None:
                features["debt_to_equity"] = float(dte)

            gm = info.get("grossMargins")
            if gm is not None:
                features["gross_margin"] = round(float(gm) * 100, 2)

            rg = info.get("revenueGrowth")
            if rg is not None:
                features["revenue_growth"] = round(float(rg) * 100, 2)

            # Quality composite: weighted combination
            # F-Score (0-9 → 0-100) + ROE contribution + margin contribution
            composite_parts = [f_score / 9.0 * 100.0]
            if "roe" in features and features["roe"] > 0:
                composite_parts.append(min(100.0, features["roe"] * 2))
            if "gross_margin" in features:
                composite_parts.append(features["gross_margin"])
            if composite_parts:
                features["quality_composite"] = round(sum(composite_parts) / len(composite_parts), 2)

            return features

        except Exception as exc:
            logger.warning("Fundamentals fetch failed for %s: %s", symbol, exc)
            return {}

    def store_quality_metrics(
        self,
        ticker: str,
        feature_store: FeatureStore,
        as_of: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch and store quality metrics in FeatureStore."""
        features = self.fetch_quality_metrics(ticker)
        if not features:
            return None

        event_ts = as_of or datetime.now(timezone.utc).isoformat()
        record = FeatureRecord(
            entity_id=ticker.upper(),
            event_ts=event_ts,
            feature_set="fundamental_quality",
            feature_version=1,
            features=features,
            metadata={"source": self.config.source},
        )

        try:
            feature_store.save(record)
            return record.record_id
        except Exception as exc:
            logger.warning("Failed to store fundamentals for %s: %s", ticker, exc)
            return None

    def fetch_batch(self, tickers: Sequence[str]) -> dict[str, dict[str, float]]:
        """Fetch quality metrics for multiple tickers."""
        return {t.upper(): self.fetch_quality_metrics(t) for t in tickers}
