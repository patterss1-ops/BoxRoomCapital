"""Decay-triggered review artifacts and promotion blocking support."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from analytics.decay_detector import DecayConfig, StrategyHealth, detect_decay
from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    PromotionOutcome,
    ReviewTrigger,
)


class DecayReviewService:
    """Turn decay-detector output into explicit operator review artifacts."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        decay_detector: Callable[..., list[StrategyHealth]] | None = None,
        pipeline_state_updater: Callable[[str, str], None] | None = None,
        notifier: Callable[[str, str, list[str]], None] | None = None,
    ):
        self._artifact_store = artifact_store
        self._decay_detector = decay_detector or detect_decay
        self._pipeline_state_updater = pipeline_state_updater or (lambda chain_id, stage: None)
        self._notifier = notifier or (lambda strategy_id, status, flags: None)

    def run_decay_check(self, as_of: str, db_path: str | None = None) -> list[ArtifactEnvelope]:
        report_date = as_of[:10]
        kwargs = {"report_date": report_date}
        if db_path is not None:
            kwargs["db_path"] = db_path
        health_rows = self._decay_detector(**kwargs)
        created: list[ArtifactEnvelope] = []

        for health in health_rows:
            if health.status not in {"warning", "decay"}:
                continue
            if self._has_pending_review(health.strategy):
                continue

            artifact_id = str(uuid.uuid4())
            trigger = ReviewTrigger(
                strategy_id=health.strategy,
                trigger_source="decay_detector",
                health_status=health.status,
                flags=list(health.flags),
                recent_metrics={
                    "recent_trades": float(health.recent_trades),
                    "recent_win_rate_pct": float(health.recent_win_rate_pct),
                    "recent_profit_factor": float(health.recent_profit_factor),
                    "recent_pnl": float(health.recent_pnl),
                    "consecutive_losses": float(health.consecutive_losses),
                },
                baseline_metrics={
                    "baseline_win_rate_pct": float(health.baseline_win_rate_pct),
                    "baseline_profit_factor": float(health.baseline_profit_factor),
                },
                recommended_action=self._recommended_action(health.status),
                artifact_id=artifact_id,
            )
            envelope = ArtifactEnvelope(
                artifact_type=ArtifactType.REVIEW_TRIGGER,
                engine=Engine.ENGINE_B,
                ticker=health.strategy,
                body=trigger,
                created_by="system",
                tags=["decay_review"],
                artifact_id=artifact_id,
            )
            envelope.artifact_id = self._artifact_store.save(envelope)
            created.append(envelope)
            self._pipeline_state_updater(envelope.chain_id or "", "review_pending")
            self._notifier(health.strategy, health.status, list(health.flags))
        return created

    def acknowledge_review(
        self,
        chain_id: str,
        operator_decision: PromotionOutcome,
        notes: str,
    ) -> ArtifactEnvelope:
        latest = self._artifact_store.get_latest(chain_id)
        if latest is None or latest.artifact_type != ArtifactType.REVIEW_TRIGGER:
            raise ValueError(f"ReviewTrigger chain '{chain_id}' not found")

        body = dict(latest.body)
        artifact_id = str(uuid.uuid4())
        body.update(
            {
                "artifact_id": artifact_id,
                "operator_ack": True,
                "operator_decision": operator_decision.value,
                "operator_notes": notes,
                "acknowledged_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.REVIEW_TRIGGER,
            engine=latest.engine,
            ticker=latest.ticker,
            edge_family=latest.edge_family,
            chain_id=latest.chain_id,
            parent_id=latest.artifact_id,
            body=ReviewTrigger.model_validate(body),
            created_by="operator",
            tags=list(latest.tags),
            artifact_id=artifact_id,
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        self._pipeline_state_updater(
            chain_id,
            {
                PromotionOutcome.PROMOTE: "review_cleared",
                PromotionOutcome.REVISE: "review_revise",
                PromotionOutcome.PARK: "review_parked",
                PromotionOutcome.REJECT: "review_rejected",
            }[operator_decision],
        )
        return envelope

    def _has_pending_review(self, strategy_id: str) -> bool:
        active = self._artifact_store.query(
            artifact_type=ArtifactType.REVIEW_TRIGGER,
            engine=Engine.ENGINE_B,
            ticker=strategy_id,
            limit=20,
        )
        return any(not artifact.body.get("operator_ack", False) for artifact in active)

    @staticmethod
    def _recommended_action(status: str) -> PromotionOutcome:
        return PromotionOutcome.PARK if status == "decay" else PromotionOutcome.REVISE
