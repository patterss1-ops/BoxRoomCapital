"""L6 News Sentiment job runner.

Fetches news from Finnhub and social sentiment sources, scores using
the news sentiment layer.
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
from intelligence.finnhub_news_client import FinnhubNewsClient
from intelligence.social_sentiment_client import SocialSentimentClient
from app.signal.layers.news_sentiment import score_news_sentiment

logger = logging.getLogger(__name__)


from utils.datetime_utils import utc_now_iso as _utc_now_iso


@dataclass(frozen=True)
class NewsJobConfig:
    job_type: str = "news_ingest"
    event_type: str = "signal_layer"
    source: str = "multi-source-news"


class NewsJobRunner:
    """Batch job runner for L6 News Sentiment signal ingestion."""

    def __init__(
        self,
        finnhub_client: Optional[FinnhubNewsClient] = None,
        social_client: Optional[SocialSentimentClient] = None,
        event_store: Optional[EventStore] = None,
        db_path: str = DB_PATH,
        config: NewsJobConfig = NewsJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.finnhub_client = finnhub_client or FinnhubNewsClient()
        self.social_client = social_client or SocialSentimentClient()
        self.db_path = db_path
        self.event_store = event_store or EventStore(db_path=db_path)
        self.config = config
        self._now_fn = now_fn

    def run(self, tickers: Sequence[str], as_of: str = "", job_id: str = "") -> dict:
        """Run news sentiment ingestion for a ticker batch."""
        deduped = sorted({t.strip().upper() for t in tickers if t.strip()})
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail=f"tickers={','.join(deduped)}", db_path=self.db_path)

        successes = 0
        failures: dict[str, str] = {}
        scores: dict[str, dict] = {}

        log_event(category="RESEARCH", headline="News job started",
                  detail=f"job_id={run_id}, tickers={len(deduped)}", strategy="signal_engine",
                  db_path=self.db_path)

        for ticker in deduped:
            try:
                # Collect articles from multiple sources
                articles = []

                # Finnhub news
                try:
                    finnhub_articles = self.finnhub_client.fetch_company_news(ticker)
                    articles.extend(finnhub_articles)
                except Exception as exc:
                    logger.debug("Finnhub failed for %s: %s", ticker, exc)

                # Social sentiment (Stocktwits + EODHD)
                try:
                    social_articles = self.social_client.fetch_social_sentiment(ticker)
                    articles.extend(social_articles)
                except Exception as exc:
                    logger.debug("Social sentiment failed for %s: %s", ticker, exc)

                if not articles:
                    scores[ticker] = {"score": 50.0, "articles_found": 0}
                    successes += 1
                    continue

                layer_score = score_news_sentiment(
                    ticker=ticker, articles=articles, as_of=run_at,
                )

                self.event_store.write_event(EventRecord(
                    event_type=self.config.event_type,
                    source=self.config.source,
                    source_ref=layer_score.provenance_ref or "",
                    retrieved_at=run_at,
                    event_timestamp=run_at,
                    symbol=ticker,
                    headline="L6 News Sentiment score",
                    detail=f"ticker={ticker}, score={layer_score.score}, articles={len(articles)}",
                    confidence=layer_score.confidence,
                    provenance_descriptor={"layer_id": "l6_news_sentiment", "ticker": ticker, "as_of": run_at},
                    payload=layer_score.to_dict(),
                ))
                scores[ticker] = layer_score.to_dict()
                successes += 1
            except Exception as exc:
                failures[ticker] = str(exc)
                log_event(category="ERROR", headline="News ticker failed",
                          detail=f"job_id={run_id}, ticker={ticker}, error={exc}",
                          strategy="signal_engine", db_path=self.db_path)

        summary = {
            "job_id": run_id, "as_of": run_at, "tickers_total": len(deduped),
            "tickers_success": successes, "tickers_failed": len(failures),
            "scores": scores, "failures": failures,
        }
        status = "completed" if successes > 0 or not deduped else "failed"
        update_job(job_id=run_id, status=status, detail=f"success={successes}, failed={len(failures)}",
                   result=json.dumps(summary, sort_keys=True),
                   error=json.dumps(failures) if failures and not successes else None,
                   db_path=self.db_path)
        log_event(category="RESEARCH", headline="News job completed",
                  detail=f"job_id={run_id}, success={successes}, failed={len(failures)}",
                  strategy="signal_engine", db_path=self.db_path)
        return summary
