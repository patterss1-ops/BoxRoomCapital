"""Fundamental quality job runner — weekly Sunday 06:00 UTC."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from data.trade_db import DB_PATH, create_job, log_event, update_job
from intelligence.feature_store import FeatureStore
from intelligence.fundamentals_client import FundamentalsClient

logger = logging.getLogger(__name__)


from utils.datetime_utils import utc_now_iso as _utc_now_iso


@dataclass(frozen=True)
class FundamentalsJobConfig:
    job_type: str = "fundamentals_ingest"
    source: str = "yfinance-fundamentals"


class FundamentalsJobRunner:
    """Job runner for fundamental quality data ingestion."""

    def __init__(
        self,
        client: Optional[FundamentalsClient] = None,
        feature_store: Optional[FeatureStore] = None,
        db_path: str = DB_PATH,
        config: FundamentalsJobConfig = FundamentalsJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.client = client or FundamentalsClient()
        self.feature_store = feature_store or FeatureStore()
        self.db_path = db_path
        self.config = config
        self._now_fn = now_fn

    def run(self, tickers: Sequence[str], as_of: str = "", job_id: str = "") -> dict:
        """Run fundamentals ingestion for a ticker batch."""
        deduped = sorted({t.strip().upper() for t in tickers if t.strip()})
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail=f"tickers={','.join(deduped)}", db_path=self.db_path)

        successes = 0
        failures: dict[str, str] = {}

        for ticker in deduped:
            try:
                record_id = self.client.store_quality_metrics(ticker, self.feature_store, as_of=run_at)
                if record_id:
                    successes += 1
                else:
                    failures[ticker] = "no data"
            except Exception as exc:
                failures[ticker] = str(exc)

        summary = {
            "job_id": run_id, "as_of": run_at, "tickers_total": len(deduped),
            "tickers_success": successes, "tickers_failed": len(failures),
            "failures": failures,
        }
        status = "completed" if successes > 0 or not deduped else "failed"
        update_job(job_id=run_id, status=status, detail=f"success={successes}, failed={len(failures)}",
                   result=json.dumps(summary, sort_keys=True), db_path=self.db_path)
        return summary
