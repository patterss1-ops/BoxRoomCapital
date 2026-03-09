"""Hypothesis challenge and falsification service."""

from __future__ import annotations

from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    EventCard,
    FalsificationMemo,
    HypothesisCard,
)
from research.model_router import ModelRouter
from research.prompts.v1_challenge import build_challenge_prompt


class ChallengeService:
    """Produce structured challenge artifacts for a hypothesis."""

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        self._model_router = model_router
        self._artifact_store = artifact_store

    def challenge(self, hypothesis_id: str) -> ArtifactEnvelope:
        validate_lineage = getattr(self._model_router, "validate_no_self_challenge", None)
        if callable(validate_lineage):
            validate_lineage(
                "hypothesis_formation",
                "hypothesis_challenge",
            )
        hypothesis_envelope = self._artifact_store.get(hypothesis_id)
        if hypothesis_envelope is None or hypothesis_envelope.artifact_type != ArtifactType.HYPOTHESIS_CARD:
            raise ValueError(f"HypothesisCard '{hypothesis_id}' not found")

        hypothesis = HypothesisCard.model_validate(hypothesis_envelope.body)
        event_envelope = self._artifact_store.get(hypothesis.event_card_ref)
        event_body = None
        if event_envelope and event_envelope.artifact_type == ArtifactType.EVENT_CARD:
            event_body = EventCard.model_validate(event_envelope.body).model_dump(mode="json")

        system_prompt, user_prompt = build_challenge_prompt(
            hypothesis_card=hypothesis.model_dump(mode="json"),
            event_card=event_body,
        )
        response = self._model_router.call(
            "hypothesis_challenge",
            prompt=user_prompt,
            system_prompt=system_prompt,
            artifact_id=hypothesis_id,
            engine=Engine.ENGINE_B,
        )
        parsed = dict(response.parsed or {})
        parsed.setdefault("hypothesis_ref", hypothesis_id)
        parsed.setdefault("challenge_model", response.model_id)
        parsed.setdefault("challenge_confidence", 0.5)
        memo = FalsificationMemo.model_validate(parsed)
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.FALSIFICATION_MEMO,
            engine=Engine.ENGINE_B,
            ticker=hypothesis_envelope.ticker,
            edge_family=hypothesis_envelope.edge_family,
            chain_id=hypothesis_envelope.chain_id,
            body=memo,
            created_by=f"model:{response.model_provider}",
            tags=["challenge"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope
