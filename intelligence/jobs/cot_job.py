"""CFTC Commitment of Traders job runner — weekly Saturday 10:00 UTC."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from data.trade_db import DB_PATH, create_job, log_event, update_job
from intelligence.cot_client import COTClient
from intelligence.feature_store import FeatureStore

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class COTJobConfig:
    job_type: str = "cot_ingest"
    source: str = "cftc-cot"


class COTJobRunner:
    """Job runner for CFTC COT data ingestion."""

    def __init__(
        self,
        client: Optional[COTClient] = None,
        feature_store: Optional[FeatureStore] = None,
        db_path: str = DB_PATH,
        config: COTJobConfig = COTJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.client = client or COTClient()
        self.feature_store = feature_store or FeatureStore()
        self.db_path = db_path
        self.config = config
        self._now_fn = now_fn

    def run(self, as_of: str = "", job_id: str = "") -> dict:
        """Run COT data ingestion."""
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail="CFTC COT positioning", db_path=self.db_path)

        try:
            record_id = self.client.store_cot_data(self.feature_store, as_of=run_at)
            status = "completed" if record_id else "completed_empty"
        except Exception as exc:
            status = "failed"
            record_id = None
            logger.warning("COT job failed: %s", exc)

        summary = {"job_id": run_id, "as_of": run_at, "stored": record_id is not None}
        update_job(job_id=run_id, status=status, result=json.dumps(summary, sort_keys=True),
                   db_path=self.db_path)
        return summary
