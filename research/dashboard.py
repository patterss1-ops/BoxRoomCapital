"""Read models for research dashboard fragments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

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

_STAGE_GROUPS = {
    "intake": "intake",
    "hypothesis": "formation",
    "challenge": "challenge",
    "scored": "decision",
    "test_spec": "experiment",
    "experiment": "experiment",
    "pilot_ready": "operator",
    "review_pending": "operator",
    "review_cleared": "closed",
    "review_revise": "operator",
    "review_parked": "operator",
    "review_rejected": "closed",
    "taxonomy_rejected": "closed",
    "retired": "closed",
}

_ACTIVE_CHAIN_OPERATOR_LANES = {
    "pilot_ready": "Pilot Lane",
    "review_pending": "Review Lane",
    "review_revise": "Review Lane",
    "review_parked": "Review Lane",
}

_ACTIVE_CHAIN_OPERATOR_NOW_STAGES = {
    "pilot_ready",
    "review_pending",
    "review_revise",
}

_ACTIVE_CHAIN_FLOW_LANES = {
    "intake": ("formation", "Formation", 1),
    "hypothesis": ("formation", "Formation", 1),
    "challenge": ("challenge", "Challenge", 2),
    "scored": ("decision", "Decision", 3),
    "test_spec": ("experiment", "Experiment", 4),
    "experiment": ("experiment", "Experiment", 4),
    "review_parked": ("parked_follow_up", "Parked Follow-Up", 5),
    "review_cleared": ("follow_through", "Follow-Through", 6),
}


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


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _age_label(value: Any, *, now: datetime) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return "-"
    seconds = max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    if days < 14:
        return f"{days}d ago"
    weeks = days // 7
    return f"{weeks}w ago"


def _freshness_bucket(value: Any, *, now: datetime, fresh_minutes: int = 30, stale_minutes: int = 180) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return "unknown"
    age_minutes = max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds() // 60))
    if age_minutes <= fresh_minutes:
        return "fresh"
    if age_minutes <= stale_minutes:
        return "aging"
    return "stale"


def _next_action_for_stage(stage: str, outcome: str) -> str:
    normalized_stage = str(stage or "").strip().lower()
    normalized_outcome = str(outcome or "").strip().lower()
    if normalized_stage == "intake":
        return "wait for hypothesis formation"
    if normalized_stage == "hypothesis":
        return "stress-test the thesis"
    if normalized_stage == "challenge":
        return "score and synthesize"
    if normalized_stage == "scored":
        if normalized_outcome == "promote":
            return "open chain and prepare pilot path"
        if normalized_outcome == "revise":
            return "refine thesis before promotion"
        if normalized_outcome == "park":
            return "park or gather more evidence"
        if normalized_outcome == "reject":
            return "reject or retire the idea"
        return "inspect score and synthesize"
    if normalized_stage in {"test_spec", "experiment"}:
        return "review experiment evidence"
    if normalized_stage == "pilot_ready":
        return "approve or reject pilot"
    if normalized_stage == "review_pending":
        return "acknowledge review"
    if normalized_stage == "review_revise":
        return "revise and resubmit"
    if normalized_stage == "review_parked":
        return "recheck later"
    if normalized_stage == "review_cleared":
        return "monitor follow-through"
    return "inspect chain"


def _decision_tone(decision: str) -> str:
    normalized = str(decision or "").strip().lower()
    if normalized in {"promote", "approve", "approved", "review_cleared"}:
        return "positive"
    if normalized in {"reject", "rejected", "retired", "kill"}:
        return "negative"
    if normalized in {"revise", "park", "review_parked"}:
        return "warning"
    return "neutral"


def _queue_priority(*, recommended_action: str, created_at: Any, now: datetime) -> str:
    action = str(recommended_action or "").strip().lower()
    freshness = _freshness_bucket(created_at, now=now, fresh_minutes=20, stale_minutes=120)
    if action == "reject" or freshness == "stale":
        return "urgent"
    if action in {"revise", "park"} or freshness == "aging":
        return "watch"
    return "routine"


def _active_chain_operator_context(stage: str, *, updated_at: Any, now: datetime) -> dict[str, Any]:
    normalized_stage = str(stage or "").strip().lower()
    operator_now = normalized_stage in _ACTIVE_CHAIN_OPERATOR_NOW_STAGES
    return {
        "operator_now": operator_now,
        "operator_lane_label": _ACTIVE_CHAIN_OPERATOR_LANES.get(normalized_stage, ""),
        "operator_priority": _lane_priority_from_activity(updated_at, now=now) if operator_now else "",
        "board_group": "operator" if operator_now else "flow",
    }


def _active_chain_flow_context(stage: str) -> dict[str, Any]:
    normalized_stage = str(stage or "").strip().lower()
    lane_key, lane_label, lane_order = _ACTIVE_CHAIN_FLOW_LANES.get(normalized_stage, ("active", "Active", 99))
    return {
        "flow_lane_key": lane_key,
        "flow_lane_label": lane_label,
        "flow_lane_order": lane_order,
    }


def _format_active_chain_row(row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    stage = str(row.get("current_stage") or "")
    outcome = str(row.get("outcome") or "")
    operator_context = _active_chain_operator_context(stage, updated_at=row.get("updated_at"), now=now)
    flow_context = _active_chain_flow_context(stage)
    return {
        "chain_id": str(row["chain_id"]),
        "ticker": row.get("ticker") or "-",
        "edge_family": row.get("edge_family") or "-",
        "stage": stage or "-",
        "stage_group": _STAGE_GROUPS.get(stage.strip().lower(), "active"),
        "outcome": outcome,
        "score": float(row["score"]) if row.get("score") is not None else None,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "updated_label": _age_label(row.get("updated_at"), now=now),
        "created_label": _age_label(row.get("created_at"), now=now),
        "freshness": _freshness_bucket(row.get("updated_at"), now=now),
        "next_action": _next_action_for_stage(stage, outcome),
        **operator_context,
        **flow_context,
    }


def _format_decision_row(row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    decision = str(row.get("decision") or "")
    return {
        "artifact_id": str(row["artifact_id"]),
        "chain_id": str(row["chain_id"]),
        "strategy_id": row.get("strategy_id") or row.get("ticker") or "-",
        "decision": decision,
        "notes": row.get("notes") or "",
        "health_status": row.get("health_status") or "",
        "decided_at": row.get("acknowledged_at") or _iso(row.get("created_at")),
        "decided_label": _age_label(row.get("acknowledged_at") or row.get("created_at"), now=now),
        "decision_tone": _decision_tone(decision),
    }


def _format_review_row(row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    recommended_action = str(row.get("recommended_action") or "")
    return {
        "artifact_id": str(row["artifact_id"]),
        "chain_id": str(row["chain_id"]),
        "strategy_id": row.get("strategy_id") or row.get("ticker") or "-",
        "health_status": row.get("health_status") or "warning",
        "flags": list(row.get("flags") or []),
        "recommended_action": recommended_action,
        "created_at": _iso(row.get("created_at")),
        "created_label": _age_label(row.get("created_at"), now=now),
        "priority": _queue_priority(
            recommended_action=recommended_action,
            created_at=row.get("created_at"),
            now=now,
        ),
    }


def _lane_priority_from_activity(value: Any, *, now: datetime) -> str:
    freshness = _freshness_bucket(value, now=now, fresh_minutes=20, stale_minutes=120)
    if freshness == "stale":
        return "urgent"
    if freshness == "aging":
        return "watch"
    return "routine"


def _format_pilot_row(row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    updated_at = row.get("updated_at") or row.get("created_at")
    return {
        "chain_id": str(row["chain_id"]),
        "ticker": row.get("ticker") or "-",
        "edge_family": row.get("edge_family") or "-",
        "outcome": str(row.get("outcome") or ""),
        "score": float(row["score"]) if row.get("score") is not None else None,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(updated_at),
        "created_label": _age_label(row.get("created_at"), now=now),
        "updated_label": _age_label(updated_at, now=now),
        "freshness": _freshness_bucket(updated_at, now=now),
        "priority": _lane_priority_from_activity(updated_at, now=now),
        "next_action": "approve or reject pilot",
    }


class ResearchDashboardService:
    """Small query layer for artifact- and pipeline-backed dashboard fragments."""

    def __init__(
        self,
        connection_factory=get_pg_connection,
        release_factory=release_pg_connection,
        now_factory: Callable[[], datetime] | None = None,
    ):
        self._get_connection = connection_factory
        self._release_connection = release_factory
        self._now = now_factory or (lambda: datetime.now(timezone.utc))

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
        now = self._now()
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

        return [_format_active_chain_row(row, now=now) for row in rows]

    def recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        now = self._now()
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

        return [_format_decision_row(row, now=now) for row in rows]

    def alerts(self, limit: int = 20) -> dict[str, list[dict[str, Any]]]:
        now = self._now()
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
                    SELECT chain_id, engine, ticker, edge_family, current_stage, outcome,
                           score, created_at, updated_at
                    FROM research.pipeline_state
                    WHERE engine = %s
                      AND current_stage = 'pilot_ready'
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT %s
                    """,
                    (Engine.ENGINE_B.value, limit),
                )
                pending_pilots = fetchall_dicts(cur)

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
            "pending_reviews": [_format_review_row(row, now=now) for row in pending_reviews],
            "pending_pilots": [_format_pilot_row(row, now=now) for row in pending_pilots],
            "kill_alerts": [
                {
                    "artifact_id": str(row["artifact_id"]),
                    "chain_id": str(row["chain_id"]),
                    "hypothesis_ref": row.get("hypothesis_ref") or row.get("ticker") or "-",
                    "trigger": row.get("trigger") or "",
                    "trigger_detail": row.get("trigger_detail") or "",
                    "final_status": row.get("final_status") or "",
                    "created_at": _iso(row.get("created_at")),
                    "created_label": _age_label(row.get("created_at"), now=now),
                }
                for row in retirements
            ],
        }

    def operating_summary(self) -> dict[str, Any]:
        now = self._now()
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
                    """,
                    (Engine.ENGINE_B.value,),
                )
                active_rows = fetchall_dicts(cur)

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
                    (ArtifactType.REVIEW_TRIGGER.value, 20),
                )
                pending_reviews = fetchall_dicts(cur)

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
                    LIMIT 1
                    """,
                    (ArtifactType.REVIEW_TRIGGER.value,),
                )
                latest_decision_rows = fetchall_dicts(cur)
        finally:
            self._release_connection(conn)

        active = [_format_active_chain_row(row, now=now) for row in active_rows]
        reviews = [_format_review_row(row, now=now) for row in pending_reviews]
        latest_decision = _format_decision_row(latest_decision_rows[0], now=now) if latest_decision_rows else None

        freshness_counts = {
            "fresh": sum(1 for row in active if row["freshness"] == "fresh"),
            "aging": sum(1 for row in active if row["freshness"] == "aging"),
            "stale": sum(1 for row in active if row["freshness"] == "stale"),
        }
        stage_counts: dict[str, int] = {}
        for row in active:
            stage = str(row.get("stage") or "").strip().lower()
            if stage:
                stage_counts[stage] = stage_counts.get(stage, 0) + 1

        urgent_reviews = sum(1 for row in reviews if row["priority"] == "urgent")
        watch_reviews = sum(1 for row in reviews if row["priority"] == "watch")
        pilot_ready_count = int(stage_counts.get("pilot_ready", 0))
        latest_chain = active[0] if active else None

        focus_title = "No active research"
        focus_detail = "The loop is idle. Use intake to start a fresh chain."
        focus_tone = "idle"
        focus_anchor = "#research-intake"
        if urgent_reviews:
            focus_title = "Urgent operator queue"
            focus_detail = f"{urgent_reviews} review item(s) are urgent and need an operator call now."
            focus_tone = "urgent"
            focus_anchor = "#research-workbench"
        elif pilot_ready_count:
            focus_title = "Pilot sign-off waiting"
            focus_detail = f"{pilot_ready_count} chain(s) reached pilot-ready state and need approval or rejection."
            focus_tone = "warning"
            focus_anchor = "#research-workbench"
        elif freshness_counts["stale"]:
            focus_title = "Stale active chains"
            focus_detail = f"{freshness_counts['stale']} chain(s) have gone stale and should be reviewed before the queue drifts."
            focus_tone = "warning"
            focus_anchor = "#research-loop"
        elif latest_chain:
            focus_title = "Research loop flowing"
            focus_detail = (
                f"Latest chain is {latest_chain['ticker']} in {latest_chain['stage']} and was updated "
                f"{latest_chain['updated_label']}."
            )
            focus_tone = "clear"
            focus_anchor = "#research-loop"

        return {
            "focus_title": focus_title,
            "focus_detail": focus_detail,
            "focus_tone": focus_tone,
            "focus_anchor": focus_anchor,
            "active_chain_count": len(active),
            "freshness_counts": freshness_counts,
            "pending_review_count": len(reviews),
            "urgent_review_count": urgent_reviews,
            "watch_review_count": watch_reviews,
            "pilot_ready_count": pilot_ready_count,
            "review_pending_stage_count": int(stage_counts.get("review_pending", 0)),
            "latest_chain": latest_chain,
            "latest_decision": latest_decision,
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "error": "",
        }
