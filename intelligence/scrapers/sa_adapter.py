"""Adapter: makes SAScraper a drop-in replacement for SAQuantClient.

The job runner (sa_quant_job.py) calls SAQuantClient methods. This adapter
wraps SAScraper so we can swap in scraping without rewriting the job.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from intelligence.sa_quant_client import (
    SAQuantSnapshot,
    SAQuantClientError,
)
from intelligence.scrapers.sa_scraper import SAScraper, SAScraperConfig, _GRADE_MAP


class SAScraperAdapter:
    """Drop-in replacement for SAQuantClient backed by web scraping."""

    def __init__(self, config: Optional[SAScraperConfig] = None):
        self._scraper = SAScraper(config=config)
        self.config = self._scraper.config

    def fetch_payload(self, ticker: str) -> Dict[str, Any]:
        """Return raw scraped data as a dict (matches SAQuantClient interface)."""
        snap = self._scraper.fetch_snapshot(ticker)
        return {
            "ticker": snap.ticker,
            "quant_rating": snap.quant_rating,
            "quant_score": snap.quant_score,
            "sa_authors_rating": snap.sa_authors_rating,
            "wall_st_rating": snap.wall_st_rating,
            "factor_grades": dict(snap.factor_grades),
        }

    def fetch_snapshot(self, ticker: str) -> SAQuantSnapshot:
        """Fetch and return SAQuantSnapshot (same type the job uses)."""
        snap = self._scraper.fetch_snapshot(ticker)

        return SAQuantSnapshot(
            ticker=snap.ticker,
            rating=snap.quant_rating,
            quant_score_raw=snap.quant_score,
            sector_rank=None,
            industry_rank=None,
            updated_at=datetime.now(timezone.utc).isoformat(),
            source_ref=f"sa-scraper-{snap.ticker}",
            raw_fields={
                "rating": snap.quant_rating,
                "quant_score_raw": snap.quant_score,
                "sa_authors_rating": snap.sa_authors_rating,
                "wall_st_rating": snap.wall_st_rating,
            },
        )

    def fetch_layer_score(self, ticker: str, as_of: str):
        """Fetch SA data and return L8 LayerScore."""
        from intelligence.sa_quant_client import score_sa_quant_snapshot
        snapshot = self.fetch_snapshot(ticker)
        return score_sa_quant_snapshot(
            snapshot=snapshot,
            as_of=as_of,
            source="sa-scraper",
        )

    def fetch_factor_grades(self, ticker: str) -> Dict[str, Any]:
        """Fetch factor grades via scraping."""
        return self._scraper.fetch_factor_grades(ticker)

    def fetch_news(self, ticker: str, count: int = 20) -> List[Dict[str, Any]]:
        """Fetch news via scraping."""
        return self._scraper.fetch_news(ticker, count=count)

    def fetch_analyst_recs(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch analyst recommendations via scraping."""
        return self._scraper.fetch_analyst_recs(ticker)

    def close(self):
        self._scraper.close()
