"""L2 Insider Buying job runner — SEC EDGAR Form 4 + FCA RNS ingestion.

Follows the SAQuantJobRunner pattern: batch fetch → score → persist events.
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
from intelligence.insider_signal_adapter import score_insider_activity
from intelligence.sec_edgar_client import SECEdgarClient

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class InsiderJobConfig:
    job_type: str = "insider_ingest"
    event_type: str = "signal_layer"
    source: str = "sec-edgar-form4"


class InsiderJobRunner:
    """Batch job runner for L2 Insider Buying signal ingestion."""

    def __init__(
        self,
        client: Optional[SECEdgarClient] = None,
        event_store: Optional[EventStore] = None,
        db_path: str = DB_PATH,
        config: InsiderJobConfig = InsiderJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.client = client or SECEdgarClient()
        self.db_path = db_path
        self.event_store = event_store or EventStore(db_path=db_path)
        self.config = config
        self._now_fn = now_fn

    def run(self, tickers: Sequence[str], as_of: str = "", job_id: str = "") -> dict:
        """Run insider buying ingestion for a ticker batch."""
        deduped = sorted({t.strip().upper() for t in tickers if t.strip()})
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(job_id=run_id, job_type=self.config.job_type, status="running",
                   mode="shadow", detail=f"tickers={','.join(deduped)}", db_path=self.db_path)

        successes = 0
        failures: dict[str, str] = {}
        scores: dict[str, dict] = {}

        log_event(category="RESEARCH", headline="Insider job started",
                  detail=f"job_id={run_id}, tickers={len(deduped)}", strategy="signal_engine",
                  db_path=self.db_path)

        for ticker in deduped:
            try:
                transactions = self.client.fetch_insider_transactions(ticker)
                if not transactions:
                    scores[ticker] = {"score": 0.0, "transactions": 0}
                    successes += 1
                    continue

                layer_score, vetoes = score_insider_activity(
                    ticker=ticker, transactions=transactions, as_of=run_at,
                )

                self.event_store.write_event(EventRecord(
                    event_type=self.config.event_type,
                    source=self.config.source,
                    source_ref=layer_score.provenance_ref or "",
                    retrieved_at=run_at,
                    event_timestamp=run_at,
                    symbol=ticker,
                    headline="L2 Insider score",
                    detail=f"ticker={ticker}, score={layer_score.score}, txns={len(transactions)}",
                    confidence=layer_score.confidence,
                    provenance_descriptor={"layer_id": "l2_insider", "ticker": ticker, "as_of": run_at},
                    payload=layer_score.to_dict(),
                ))
                scores[ticker] = layer_score.to_dict()
                successes += 1
            except Exception as exc:
                failures[ticker] = str(exc)
                log_event(category="ERROR", headline="Insider ticker failed",
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
        log_event(category="RESEARCH", headline="Insider job completed",
                  detail=f"job_id={run_id}, success={successes}, failed={len(failures)}",
                  strategy="signal_engine", db_path=self.db_path)
        return summary
