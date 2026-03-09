"""Read models for research dashboard fragments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from data.pg_connection import get_pg_connection, release_pg_connection
from research.artifacts import ArtifactType, Engine
from research.shared.sql import fetchall_dicts

_FUNNEL_STAGE_ORDER = [
    "intake",
    "hypothesis",
    "challenge",
    "scored",
    "test_spec",
    "experiment",
    "pilot_ready",
    "review_pending",
    "review_cleared",
    "review_revise",
    "review_parked",
    "review_rejected",
    "taxonomy_rejected",
    "retired",
]


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _slug_label(value: str) -> str:
    return value.replace("_", " ").title()


class ResearchDashboardService:
    """Small query layer for artifact- and pipeline-backed dashboard fragments."""

    def __init__(self, connection_factory=get_pg_connection, release_factory=release_pg_connection):
        self._get_connection = connection_factory
        self._release_connection = release_factory

    def pipeline_funnel(self) -> list[dict[str, Any]]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT current_stage, COUNT(*) AS total
                    FROM research.pipeline_state
                    GROUP BY current_stage
                    """,
                )
                rows = fetchall_dicts(cur)
        finally:
            self._release_connection(conn)

        counts = {str(row["current_stage"]): int(row["total"]) for row in rows}
        stages: list[dict[str, Any]] = []
        for stage in _FUNNEL_STAGE_ORDER:
            stages.append(
                {
                    "stage": stage,
                    "label": _slug_label(stage),
                    "total": counts.pop(stage, 0),
                }
            )
        for stage, total in sorted(counts.items()):
            stages.append({"stage": stage, "label": _slug_label(stage), "total": int(total)})
        return stages

    def active_hypotheses(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT chain_id, engine, ticker, edge_family, current_stage, outcome,
                           score, created_at, updated_at
                    FROM research.pipeline_state
                    WHERE engine = %s
                      AND current_stage NOT IN ('retired', 'review_rejected', 'taxonomy_rejected')
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT %s
                    """,
                    (Engine.ENGINE_B.value, limit),
                )
                rows = fetchall_dicts(cur)
        finally:
            self._release_connection(conn)

        return [
            {
                "chain_id": str(row["chain_id"]),
                "ticker": row.get("ticker") or "-",
                "edge_family": row.get("edge_family") or "-",
                "stage": row.get("current_stage") or "-",
                "outcome": row.get("outcome") or "",
                "score": float(row["score"]) if row.get("score") is not None else None,
                "created_at": _iso(row.get("created_at")),
                "updated_at": _iso(row.get("updated_at")),
            }
            for row in rows
        ]

    def recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT artifact_id, chain_id, ticker,
                           body->>'strategy_id' AS strategy_id,
                           body->>'operator_decision' AS decision,
                           body->>'operator_notes' AS notes,
                           body->>'health_status' AS health_status,
                           body->>'acknowledged_at' AS acknowledged_at,
                           created_at
                    FROM research.artifacts
                    WHERE artifact_type = %s
                      AND COALESCE(body->>'operator_ack', 'false') = 'true'
                    ORDER BY created_at DESC, version DESC
                    LIMIT %s
                    """,
                    (ArtifactType.REVIEW_TRIGGER.value, limit),
                )
                rows = fetchall_dicts(cur)
        finally:
            self._release_connection(conn)

        decisions: list[dict[str, Any]] = []
        for row in rows:
            decisions.append(
                {
                    "artifact_id": str(row["artifact_id"]),
                    "chain_id": str(row["chain_id"]),
                    "strategy_id": row.get("strategy_id") or row.get("ticker") or "-",
                    "decision": row.get("decision") or "",
                    "notes": row.get("notes") or "",
                    "health_status": row.get("health_status") or "",
                    "decided_at": row.get("acknowledged_at") or _iso(row.get("created_at")),
                }
            )
        return decisions

    def alerts(self, limit: int = 20) -> dict[str, list[dict[str, Any]]]:
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT artifact_id, chain_id, ticker,
                           body->>'strategy_id' AS strategy_id,
                           body->>'health_status' AS health_status,
                           body->'flags' AS flags,
                           body->>'recommended_action' AS recommended_action,
                           created_at
                    FROM research.artifacts
                    WHERE artifact_type = %s
                      AND COALESCE(body->>'operator_ack', 'false') = 'false'
                    ORDER BY created_at DESC, version DESC
                    LIMIT %s
                    """,
                    (ArtifactType.REVIEW_TRIGGER.value, limit),
                )
                pending_reviews = fetchall_dicts(cur)

                cur.execute(
                    """
                    SELECT artifact_id, chain_id, ticker,
                           body->>'hypothesis_ref' AS hypothesis_ref,
                           body->>'trigger' AS trigger,
                           body->>'trigger_detail' AS trigger_detail,
                           body->>'final_status' AS final_status,
                           created_at
                    FROM research.artifacts
                    WHERE artifact_type = %s
                    ORDER BY created_at DESC, version DESC
                    LIMIT %s
                    """,
                    (ArtifactType.RETIREMENT_MEMO.value, limit),
                )
                retirements = fetchall_dicts(cur)
        finally:
            self._release_connection(conn)

        return {
            "pending_reviews": [
                {
                    "artifact_id": str(row["artifact_id"]),
                    "chain_id": str(row["chain_id"]),
                    "strategy_id": row.get("strategy_id") or row.get("ticker") or "-",
                    "health_status": row.get("health_status") or "warning",
                    "flags": list(row.get("flags") or []),
                    "recommended_action": row.get("recommended_action") or "",
                    "created_at": _iso(row.get("created_at")),
                }
                for row in pending_reviews
            ],
            "kill_alerts": [
                {
                    "artifact_id": str(row["artifact_id"]),
                    "chain_id": str(row["chain_id"]),
                    "hypothesis_ref": row.get("hypothesis_ref") or row.get("ticker") or "-",
                    "trigger": row.get("trigger") or "",
                    "trigger_detail": row.get("trigger_detail") or "",
                    "final_status": row.get("final_status") or "",
                    "created_at": _iso(row.get("created_at")),
                }
                for row in retirements
            ],
        }
