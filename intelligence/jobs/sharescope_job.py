"""ShareScope scraper job runner — weekly UK quality screen scrape."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from data.trade_db import DB_PATH, create_job, log_event, update_job
from intelligence.feature_store import FeatureStore

logger = logging.getLogger(__name__)


from utils.datetime_utils import utc_now_iso as _utc_now_iso


@dataclass(frozen=True)
class ShareScopeJobConfig:
    job_type: str = "sharescope_scrape"
    source: str = "sharescope-scraper"
    screens: tuple[str, ...] = ("quality", "income", "momentum")


class ShareScopeJobRunner:
    """Job runner for ShareScope web scraping."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        config: ShareScopeJobConfig = ShareScopeJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.db_path = db_path
        self.config = config
        self._now_fn = now_fn

    def run(self, as_of: str = "", job_id: str = "") -> dict:
        """Run ShareScope screen scraping."""
        from intelligence.scrapers.sharescope_scraper import ShareScopeScraper

        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail=f"screens={','.join(self.config.screens)}",
                   db_path=self.db_path)

        results: dict[str, int] = {}
        scraper = ShareScopeScraper()
        fs = FeatureStore()

        try:
            for screen in self.config.screens:
                try:
                    stored = scraper.store_uk_screen(screen, fs, as_of=run_at)
                    results[screen] = stored
                except Exception as exc:
                    results[screen] = 0
                    logger.warning("ShareScope screen '%s' failed: %s", screen, exc)
        finally:
            scraper.close()
            fs.close()

        summary = {"job_id": run_id, "as_of": run_at, "screens": results}
        update_job(job_id=run_id, status="completed", result=json.dumps(summary, sort_keys=True),
                   db_path=self.db_path)
        return summary
