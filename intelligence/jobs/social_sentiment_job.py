"""Social sentiment job runner — runs every 4 hours alongside L6 news refresh.

Fetches from Stocktwits + EODHD and feeds into L6 News Sentiment scoring.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from data.trade_db import DB_PATH, create_job, log_event, update_job
from intelligence.event_store import EventRecord, EventStore
from intelligence.social_sentiment_client import SocialSentimentClient

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SocialSentimentJobConfig:
    job_type: str = "social_sentiment_ingest"
    event_type: str = "signal_layer"
    source: str = "social-sentiment"


class SocialSentimentJobRunner:
    """Batch job runner for social sentiment data collection."""

    def __init__(
        self,
        client: Optional[SocialSentimentClient] = None,
        event_store: Optional[EventStore] = None,
        db_path: str = DB_PATH,
        config: SocialSentimentJobConfig = SocialSentimentJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.client = client or SocialSentimentClient()
        self.db_path = db_path
        self.event_store = event_store or EventStore(db_path=db_path)
        self.config = config
        self._now_fn = now_fn

    def run(self, tickers: Sequence[str], as_of: str = "", job_id: str = "") -> dict:
        """Run social sentiment collection for a ticker batch."""
        deduped = sorted({t.strip().upper() for t in tickers if t.strip()})
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail=f"tickers={','.join(deduped)}", db_path=self.db_path)

        successes = 0
        failures: dict[str, str] = {}
        results: dict[str, dict] = {}

        for ticker in deduped:
            try:
                articles = self.client.fetch_social_sentiment(ticker)
                results[ticker] = {
                    "articles_count": len(articles),
                    "sources": list({a.source for a in articles}),
                }
                for article in articles:
                    self.event_store.write_event(EventRecord(
                        event_type=self.config.event_type,
                        source=article.source,
                        retrieved_at=run_at,
                        event_timestamp=article.published_at,
                        symbol=ticker,
                        headline=article.headline[:200],
                        detail=f"sentiment={article.sentiment}",
                        confidence=abs(article.sentiment),
                        provenance_descriptor={"source": article.source, "ticker": ticker},
                    ))
                successes += 1
            except Exception as exc:
                failures[ticker] = str(exc)

        summary = {
            "job_id": run_id, "as_of": run_at, "tickers_total": len(deduped),
            "tickers_success": successes, "tickers_failed": len(failures),
            "results": results, "failures": failures,
        }
        status = "completed" if successes > 0 or not deduped else "failed"
        update_job(job_id=run_id, status=status, detail=f"success={successes}, failed={len(failures)}",
                   result=json.dumps(summary, sort_keys=True, default=str),
                   error=json.dumps(failures) if failures and not successes else None,
                   db_path=self.db_path)
        return summary
