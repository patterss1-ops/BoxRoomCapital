"""SA Quant ingestion job helpers (E-003).

Runs batch fetch + normalization for L8 SA Quant scores and persists results
as research events for audit/provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid
from typing import Callable, Optional, Sequence

from data.trade_db import DB_PATH, create_job, log_event, update_job
from intelligence.event_store import EventRecord, EventStore
from intelligence.sa_quant_client import SAQuantClient


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SAQuantJobConfig:
    """Configuration for SA Quant batch ingestion jobs."""

    job_type: str = "sa_quant_ingest"
    event_type: str = "signal_layer"
    source: str = "sa-quant-rapidapi"


class SAQuantJobRunner:
    """Batch job runner for SA Quant ingestion."""

    def __init__(
        self,
        client: Optional[SAQuantClient] = None,
        event_store: Optional[EventStore] = None,
        db_path: str = DB_PATH,
        config: SAQuantJobConfig = SAQuantJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self.client = client or SAQuantClient()
        self.db_path = db_path
        self.event_store = event_store or EventStore(db_path=db_path)
        self.config = config
        self._now_fn = now_fn

    def run(self, tickers: Sequence[str], as_of: str = "", job_id: str = "") -> dict:
        """Run SA Quant ingestion for a ticker batch.

        Returns a deterministic summary payload with successes/failures.
        """
        normalized_tickers = [t.strip().upper() for t in tickers if str(t).strip()]
        deduped_tickers = sorted(set(normalized_tickers))
        run_as_of = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(
            job_id=run_id,
            job_type=self.config.job_type,
            status="running",
            mode="shadow",
            detail=f"tickers={','.join(deduped_tickers)}",
            db_path=self.db_path,
        )

        successes = 0
        failures: dict[str, str] = {}
        scores: dict[str, dict] = {}

        log_event(
            category="RESEARCH",
            headline="SA Quant job started",
            detail=f"job_id={run_id}, tickers={len(deduped_tickers)}",
            strategy="signal_engine",
            db_path=self.db_path,
        )

        for ticker in deduped_tickers:
            try:
                layer_score = self.client.fetch_layer_score(ticker=ticker, as_of=run_as_of)
                event_detail = (
                    f"ticker={ticker}, score={layer_score.score}, "
                    f"rating={layer_score.details.get('rating', '')}"
                )
                self.event_store.write_event(
                    EventRecord(
                        event_type=self.config.event_type,
                        source=self.config.source,
                        source_ref=layer_score.provenance_ref or "",
                        retrieved_at=run_as_of,
                        event_timestamp=run_as_of,
                        symbol=ticker,
                        headline="L8 SA Quant score",
                        detail=event_detail,
                        confidence=layer_score.confidence,
                        provenance_descriptor={
                            "layer_id": layer_score.layer_id.value,
                            "ticker": ticker,
                            "as_of": run_as_of,
                        },
                        payload=layer_score.to_dict(),
                    )
                )
                scores[ticker] = layer_score.to_dict()
                successes += 1
            except Exception as exc:  # noqa: BLE001 - aggregate batch errors
                failures[ticker] = str(exc)
                log_event(
                    category="ERROR",
                    headline="SA Quant ticker failed",
                    detail=f"job_id={run_id}, ticker={ticker}, error={exc}",
                    strategy="signal_engine",
                    db_path=self.db_path,
                )

        summary = {
            "job_id": run_id,
            "as_of": run_as_of,
            "tickers_total": len(deduped_tickers),
            "tickers_success": successes,
            "tickers_failed": len(failures),
            "scores": scores,
            "failures": failures,
        }

        status = "completed" if successes > 0 or not deduped_tickers else "failed"
        detail = f"success={successes}, failed={len(failures)}"
        error = json.dumps(failures, sort_keys=True) if failures and not successes else None

        update_job(
            job_id=run_id,
            status=status,
            detail=detail,
            result=json.dumps(summary, sort_keys=True),
            error=error,
            db_path=self.db_path,
        )

        log_event(
            category="RESEARCH",
            headline="SA Quant job completed",
            detail=f"job_id={run_id}, {detail}",
            strategy="signal_engine",
            db_path=self.db_path,
        )

        return summary
