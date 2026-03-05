"""L5 Congressional Trading job runner.

Fetches congressional trading data from House Stock Watcher and existing
capitol trades client, scores using the congressional layer.
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
from intelligence.house_stock_watcher_client import HouseStockWatcherClient
from app.signal.layers.congressional import score_congressional

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CongressionalJobConfig:
    job_type: str = "congressional_ingest"
    event_type: str = "signal_layer"
    source: str = "house-stock-watcher"


class CongressionalJobRunner:
    """Batch job runner for L5 Congressional Trading signal ingestion."""

    def __init__(
        self,
        client: Optional[HouseStockWatcherClient] = None,
        event_store: Optional[EventStore] = None,
        db_path: str = DB_PATH,
        config: CongressionalJobConfig = CongressionalJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.client = client or HouseStockWatcherClient()
        self.db_path = db_path
        self.event_store = event_store or EventStore(db_path=db_path)
        self.config = config
        self._now_fn = now_fn

    def run(self, tickers: Sequence[str], as_of: str = "", job_id: str = "") -> dict:
        """Run congressional trading ingestion for a ticker batch."""
        deduped = sorted({t.strip().upper() for t in tickers if t.strip()})
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail=f"tickers={','.join(deduped)}", db_path=self.db_path)

        successes = 0
        failures: dict[str, str] = {}
        scores: dict[str, dict] = {}

        log_event(category="RESEARCH", headline="Congressional job started",
                  detail=f"job_id={run_id}, tickers={len(deduped)}", strategy="signal_engine",
                  db_path=self.db_path)

        for ticker in deduped:
            try:
                trades = self.client.fetch_trades_for_ticker(ticker)
                if not trades:
                    scores[ticker] = {"score": 0.0, "trades_found": 0}
                    successes += 1
                    continue

                layer_score = score_congressional(
                    ticker=ticker, trades=trades, as_of=run_at,
                )

                self.event_store.write_event(EventRecord(
                    event_type=self.config.event_type,
                    source=self.config.source,
                    source_ref=layer_score.provenance_ref or "",
                    retrieved_at=run_at,
                    event_timestamp=run_at,
                    symbol=ticker,
                    headline="L5 Congressional score",
                    detail=f"ticker={ticker}, score={layer_score.score}, trades={len(trades)}",
                    confidence=layer_score.confidence,
                    provenance_descriptor={"layer_id": "l5_congressional", "ticker": ticker, "as_of": run_at},
                    payload=layer_score.to_dict(),
                ))
                scores[ticker] = layer_score.to_dict()
                successes += 1
            except Exception as exc:
                failures[ticker] = str(exc)
                log_event(category="ERROR", headline="Congressional ticker failed",
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
        log_event(category="RESEARCH", headline="Congressional job completed",
                  detail=f"job_id={run_id}, success={successes}, failed={len(failures)}",
                  strategy="signal_engine", db_path=self.db_path)
        return summary
