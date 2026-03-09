"""Operator pilot sign-off workflow for Engine B trade sheets."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactType, Engine, PilotDecision, ProgressionStage


class PilotSignoffService:
    """Persist operator approval or rejection for pilot-ready research chains."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        pipeline_state_updater: Callable[[str, str, str, str], None] | None = None,
    ):
        self._artifact_store = artifact_store
        self._pipeline_state_updater = pipeline_state_updater or (lambda chain_id, stage, outcome, notes: None)

    def approve_pilot(self, chain_id: str, actor: str, notes: str = "") -> ArtifactEnvelope:
        return self._record_decision(chain_id=chain_id, actor=actor, approved=True, notes=notes)

    def reject_pilot(self, chain_id: str, actor: str, notes: str = "") -> ArtifactEnvelope:
        return self._record_decision(chain_id=chain_id, actor=actor, approved=False, notes=notes)

    def _record_decision(self, *, chain_id: str, actor: str, approved: bool, notes: str) -> ArtifactEnvelope:
        clean_chain_id = str(chain_id or "").strip()
        if not clean_chain_id:
            raise ValueError("chain_id is required")

        chain = self._artifact_store.get_chain(clean_chain_id)
        if not chain:
            raise ValueError(f"Research chain '{clean_chain_id}' not found")

        latest_scoring = next((artifact for artifact in reversed(chain) if artifact.artifact_type == ArtifactType.SCORING_RESULT), None)
        latest_trade_sheet = next((artifact for artifact in reversed(chain) if artifact.artifact_type == ArtifactType.TRADE_SHEET), None)
        latest_hypothesis = next((artifact for artifact in reversed(chain) if artifact.artifact_type == ArtifactType.HYPOTHESIS_CARD), None)
        latest_decision = next((artifact for artifact in reversed(chain) if artifact.artifact_type == ArtifactType.PILOT_DECISION), None)

        if latest_scoring is None:
            raise ValueError(f"No scoring artifact found for chain {clean_chain_id[:8]}")
        next_stage = str(latest_scoring.body.get("next_stage") or "").strip().lower()
        if next_stage != ProgressionStage.PILOT.value:
            raise ValueError(f"Chain {clean_chain_id[:8]} is not pilot-ready")
        if latest_trade_sheet is None:
            raise ValueError(f"Chain {clean_chain_id[:8]} does not include a TradeSheet")

        artifact_id = str(uuid.uuid4())
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.PILOT_DECISION,
            engine=Engine.ENGINE_B,
            ticker=latest_trade_sheet.ticker,
            edge_family=latest_trade_sheet.edge_family,
            chain_id=clean_chain_id,
            parent_id=(latest_decision.artifact_id if latest_decision else latest_trade_sheet.artifact_id),
            body=PilotDecision(
                hypothesis_ref=str((latest_hypothesis.artifact_id if latest_hypothesis else "") or ""),
                trade_sheet_ref=str(latest_trade_sheet.artifact_id or ""),
                approved=approved,
                operator_decision="approve" if approved else "reject",
                operator_notes=notes.strip() or None,
                decided_by=actor.strip() or "operator",
                decided_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
            created_by=actor.strip() or "operator",
            tags=["pilot_signoff", "approved" if approved else "rejected"],
            artifact_id=artifact_id,
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        self._pipeline_state_updater(
            clean_chain_id,
            "review_cleared" if approved else "review_rejected",
            "promote" if approved else "reject",
            notes.strip() or ("Pilot approved by operator." if approved else "Pilot rejected by operator."),
        )
        return envelope
