"""Macro regime data ingestion job runner.

Fetches FRED macro data and options sentiment, stores in FeatureStore.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from data.trade_db import DB_PATH, create_job, log_event, update_job
from intelligence.feature_store import FeatureStore
from intelligence.fred_client import FREDClient
from intelligence.options_sentiment_client import OptionsSentimentClient

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class MacroJobConfig:
    job_type: str = "macro_ingest"
    source: str = "fred-macro"


class MacroJobRunner:
    """Job runner for macro regime data ingestion."""

    def __init__(
        self,
        fred_client: Optional[FREDClient] = None,
        options_client: Optional[OptionsSentimentClient] = None,
        feature_store: Optional[FeatureStore] = None,
        db_path: str = DB_PATH,
        config: MacroJobConfig = MacroJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.fred_client = fred_client or FREDClient()
        self.options_client = options_client or OptionsSentimentClient()
        self.feature_store = feature_store or FeatureStore()
        self.db_path = db_path
        self.config = config
        self._now_fn = now_fn

    def run(self, as_of: str = "", job_id: str = "") -> dict:
        """Run macro data ingestion."""
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail="macro regime + options sentiment",
                   db_path=self.db_path)

        results: dict[str, str] = {}

        # FRED macro data
        try:
            record_id = self.fred_client.store_macro_snapshot(self.feature_store, as_of=run_at)
            results["fred_macro"] = f"stored={record_id is not None}"
        except Exception as exc:
            results["fred_macro"] = f"failed: {exc}"

        # Options sentiment
        try:
            record_id = self.options_client.store_options_sentiment(self.feature_store, as_of=run_at)
            results["options_sentiment"] = f"stored={record_id is not None}"
        except Exception as exc:
            results["options_sentiment"] = f"failed: {exc}"

        summary = {"job_id": run_id, "as_of": run_at, "results": results}
        update_job(job_id=run_id, status="completed", detail=json.dumps(results),
                   result=json.dumps(summary, sort_keys=True), db_path=self.db_path)
        log_event(category="RESEARCH", headline="Macro job completed",
                  detail=f"job_id={run_id}", strategy="signal_engine", db_path=self.db_path)
        return summary
