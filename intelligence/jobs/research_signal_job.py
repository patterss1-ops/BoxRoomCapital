"""L9 research-signal ingestion job runner."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from app.signal.layers.research import ResearchSignalSnapshot, score_research_signal
from data.pg_connection import get_pg_connection, release_pg_connection
from data.trade_db import DB_PATH, create_job, log_event, update_job
from intelligence.event_store import EventRecord, EventStore
from research.artifacts import ArtifactType, Engine
from research.shared.sql import fetchall_dicts

from utils.datetime_utils import utc_now_iso as _utc_now_iso


@dataclass(frozen=True)
class ResearchSignalJobConfig:
    job_type: str = "research_signal_ingest"
    event_type: str = "signal_layer"
    source: str = "research-engine-b"


def _load_latest_research_snapshots(tickers: Sequence[str]) -> list[ResearchSignalSnapshot]:
    deduped = sorted({str(item or "").strip().upper() for item in tickers if str(item or "").strip()})
    if not deduped:
        return []

    clauses = [
        "a.artifact_type = %s",
        "a.engine = %s",
        "a.ticker = ANY(%s)",
    ]
    params: list[object] = [
        ArtifactType.SCORING_RESULT.value,
        Engine.ENGINE_B.value,
        deduped,
    ]

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT ON (a.ticker)
                       a.artifact_id,
                       a.chain_id,
                       a.ticker,
                       a.created_at,
                       a.body,
                       ps.current_stage,
                       ps.updated_at AS pipeline_updated_at
                FROM research.artifacts AS a
                LEFT JOIN research.pipeline_state AS ps
                  ON ps.chain_id = a.chain_id
                WHERE {' AND '.join(clauses)}
                ORDER BY a.ticker ASC,
                         COALESCE(ps.updated_at, a.created_at) DESC,
                         a.created_at DESC,
                         a.version DESC
                """,
                tuple(params),
            )
            rows = fetchall_dicts(cur)
    finally:
        release_pg_connection(conn)

    snapshots: list[ResearchSignalSnapshot] = []
    for row in rows:
        raw_body = row.get("body")
        if isinstance(raw_body, dict):
            body = dict(raw_body)
        elif isinstance(raw_body, str):
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                body = {}
        else:
            body = {}
        created_at = row.get("pipeline_updated_at") or row.get("created_at")
        as_of = created_at.isoformat().replace("+00:00", "Z") if hasattr(created_at, "isoformat") else str(created_at or "")
        snapshots.append(
            ResearchSignalSnapshot(
                ticker=str(row.get("ticker") or ""),
                artifact_id=str(row.get("artifact_id") or ""),
                chain_id=str(row.get("chain_id") or ""),
                as_of=as_of,
                final_score=float(body.get("final_score") or 0.0),
                outcome=str(body.get("outcome") or row.get("current_stage") or ""),
                outcome_reason=str(body.get("outcome_reason") or ""),
                raw_total=float(body.get("raw_total")) if body.get("raw_total") is not None else None,
                current_stage=str(row.get("current_stage") or "scored"),
                blocking_objections=list(body.get("blocking_objections") or []),
                metadata={
                    "edge_family": str(body.get("edge_family") or ""),
                    "next_stage": str(body.get("next_stage") or ""),
                },
            )
        )
    return snapshots


class ResearchSignalJobRunner:
    """Batch job runner for L9 research-overlay signal ingestion."""

    def __init__(
        self,
        snapshot_loader: Callable[[Sequence[str]], list[ResearchSignalSnapshot]] = _load_latest_research_snapshots,
        event_store: Optional[EventStore] = None,
        db_path: str = DB_PATH,
        config: ResearchSignalJobConfig = ResearchSignalJobConfig(),
        now_fn: Callable[[], str] = _utc_now_iso,
    ):
        self._snapshot_loader = snapshot_loader
        self.event_store = event_store or EventStore(db_path=db_path)
        self.db_path = db_path
        self.config = config
        self._now_fn = now_fn

    def run(self, tickers: Sequence[str], as_of: str = "", job_id: str = "") -> dict:
        deduped = sorted({str(item or "").strip().upper() for item in tickers if str(item or "").strip()})
        run_at = as_of.strip() or self._now_fn()
        run_id = job_id.strip() or uuid.uuid4().hex[:12]

        create_job(
            job_id=run_id,
            job_type=self.config.job_type,
            status="running",
            mode="shadow",
            detail=f"tickers={','.join(deduped)}",
            db_path=self.db_path,
        )
        log_event(
            category="RESEARCH",
            headline="Research signal job started",
            detail=f"job_id={run_id}, tickers={len(deduped)}",
            strategy="signal_engine",
            db_path=self.db_path,
        )

        successes = 0
        failures: dict[str, str] = {}
        scores: dict[str, dict] = {}

        try:
            snapshots = self._snapshot_loader(deduped)
        except Exception as exc:  # noqa: BLE001
            snapshots = []
            failures["_loader"] = str(exc)

        seen_tickers = set()
        for snapshot in snapshots:
            seen_tickers.add(snapshot.ticker)
            try:
                layer_score = score_research_signal(snapshot)
                self.event_store.write_event(
                    EventRecord(
                        event_type=self.config.event_type,
                        source=self.config.source,
                        source_ref=layer_score.provenance_ref or "",
                        retrieved_at=run_at,
                        event_timestamp=snapshot.as_of,
                        symbol=snapshot.ticker,
                        headline="L9 Research score",
                        detail=(
                            f"ticker={snapshot.ticker}, score={layer_score.score}, "
                            f"outcome={snapshot.outcome}"
                        ),
                        confidence=layer_score.confidence,
                        provenance_descriptor={
                            "layer_id": layer_score.layer_id.value,
                            "ticker": snapshot.ticker,
                            "as_of": snapshot.as_of,
                            "artifact_id": snapshot.artifact_id,
                            "chain_id": snapshot.chain_id,
                        },
                        payload=layer_score.to_dict(),
                    )
                )
                scores[snapshot.ticker] = layer_score.to_dict()
                successes += 1
            except Exception as exc:  # noqa: BLE001
                failures[snapshot.ticker] = str(exc)

        skipped = [ticker for ticker in deduped if ticker not in seen_tickers]
        summary = {
            "job_id": run_id,
            "as_of": run_at,
            "tickers_total": len(deduped),
            "tickers_success": successes,
            "tickers_failed": len(failures),
            "tickers_skipped": len(skipped),
            "scores": scores,
            "failures": failures,
            "skipped": skipped,
        }

        status = "completed" if successes > 0 or not deduped or skipped else "failed"
        detail = f"success={successes}, failed={len(failures)}, skipped={len(skipped)}"
        update_job(
            job_id=run_id,
            status=status,
            detail=detail,
            result=json.dumps(summary, sort_keys=True),
            error=json.dumps(failures, sort_keys=True) if failures and not successes else None,
            db_path=self.db_path,
        )
        log_event(
            category="RESEARCH",
            headline="Research signal job completed",
            detail=f"job_id={run_id}, {detail}",
            strategy="signal_engine",
            db_path=self.db_path,
        )
        return summary
