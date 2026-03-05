"""Earnings data fetcher for L1 PEAD (Post-Earnings Announcement Drift).

Sources:
- yfinance (already a dependency) for EPS surprise data
- Alpha Vantage free tier (optional, 500 calls/day) for revenue supplement
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EarningsClientConfig:
    """Configuration for earnings data fetcher."""

    alpha_vantage_key: str = ""
    source: str = "yfinance-earnings"
    timeout_seconds: float = 15.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class EarningsClient:
    """Fetches earnings surprise data for PEAD scoring."""

    def __init__(self, config: Optional[EarningsClientConfig] = None):
        cfg = config or EarningsClientConfig()
        self.config = EarningsClientConfig(
            alpha_vantage_key=cfg.alpha_vantage_key or os.getenv("ALPHA_VANTAGE_API_KEY", ""),
            source=cfg.source,
            timeout_seconds=cfg.timeout_seconds,
        )

    def fetch_earnings_surprise(self, ticker: str) -> list[dict[str, Any]]:
        """Fetch earnings surprise data from yfinance.

        Returns list of dicts with keys:
            ticker, earnings_date, actual_eps, consensus_eps, surprise_pct
        """
        symbol = ticker.strip().upper()
        if not symbol:
            return []

        results: list[dict[str, Any]] = []
        try:
            import yfinance as yf

            yf_ticker = yf.Ticker(symbol)

            # Try quarterly earnings
            earnings = getattr(yf_ticker, "quarterly_earnings", None)
            if earnings is not None and not earnings.empty:
                for idx, row in earnings.iterrows():
                    actual = row.get("Actual") or row.get("actual")
                    estimate = row.get("Estimate") or row.get("estimate")
                    if actual is not None and estimate is not None:
                        try:
                            actual_f = float(actual)
                            estimate_f = float(estimate)
                            surprise_pct = (
                                ((actual_f - estimate_f) / abs(estimate_f)) * 100.0
                                if estimate_f != 0
                                else 0.0
                            )
                            results.append({
                                "ticker": symbol,
                                "earnings_date": str(idx) if idx else _utc_now_iso(),
                                "actual_eps": actual_f,
                                "consensus_eps": estimate_f,
                                "surprise_pct": round(surprise_pct, 2),
                            })
                        except (ValueError, TypeError):
                            continue

            # Try earnings_dates for upcoming/recent
            if not results:
                earnings_dates = getattr(yf_ticker, "earnings_dates", None)
                if earnings_dates is not None and not earnings_dates.empty:
                    for idx, row in earnings_dates.iterrows():
                        actual = row.get("Reported EPS")
                        estimate = row.get("EPS Estimate")
                        surprise = row.get("Surprise(%)")
                        if actual is not None and estimate is not None:
                            try:
                                actual_f = float(actual)
                                estimate_f = float(estimate)
                                surprise_pct = float(surprise) if surprise is not None else (
                                    ((actual_f - estimate_f) / abs(estimate_f)) * 100.0
                                    if estimate_f != 0 else 0.0
                                )
                                date_str = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
                                results.append({
                                    "ticker": symbol,
                                    "earnings_date": date_str,
                                    "actual_eps": actual_f,
                                    "consensus_eps": estimate_f,
                                    "surprise_pct": round(surprise_pct, 2),
                                })
                            except (ValueError, TypeError):
                                continue

        except Exception as exc:
            logger.warning("Earnings fetch failed for %s: %s", symbol, exc)

        return results

    def fetch_batch(self, tickers: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        """Fetch earnings data for multiple tickers."""
        result: dict[str, list[dict[str, Any]]] = {}
        for ticker in tickers:
            result[ticker.upper()] = self.fetch_earnings_surprise(ticker)
        return result
