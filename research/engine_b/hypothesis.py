"""EventCard to HypothesisCard formation service."""

from __future__ import annotations

from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    EventCard,
    HypothesisCard,
)
from research.model_router import ModelRouter
from research.prompts.v1_hypothesis import build_hypothesis_prompt
from research.taxonomy import TaxonomyService


class HypothesisService:
    """Generate constrained trading hypotheses from EventCards."""

    def __init__(
        self,
        model_router: ModelRouter,
        artifact_store: ArtifactStore,
        taxonomy_service: TaxonomyService | None = None,
    ):
        self._model_router = model_router
        self._artifact_store = artifact_store
        self._taxonomy_service = taxonomy_service or TaxonomyService()

    def form_hypothesis(
        self,
        event_card_id: str,
        regime_snapshot: dict | None = None,
    ) -> ArtifactEnvelope:
        source_event = self._artifact_store.get(event_card_id)
        if source_event is None or source_event.artifact_type != ArtifactType.EVENT_CARD:
            raise ValueError(f"EventCard '{event_card_id}' not found")

        event_card = EventCard.model_validate(source_event.body)
        system_prompt, user_prompt = build_hypothesis_prompt(
            event_card=event_card.model_dump(mode="json"),
            regime_snapshot=regime_snapshot,
        )
        response = self._model_router.call(
            "hypothesis_formation",
            prompt=user_prompt,
            system_prompt=system_prompt,
            artifact_id=event_card_id,
            engine=Engine.ENGINE_B,
        )
        parsed = dict(response.parsed or {})
        parsed.setdefault("event_card_ref", event_card_id)
        edge_family = self._taxonomy_service.validate(parsed.get("edge_family", ""))
        parsed["edge_family"] = edge_family
        hypothesis = HypothesisCard.model_validate(parsed)
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker=source_event.ticker,
            edge_family=edge_family,
            chain_id=source_event.chain_id,
            body=hypothesis,
            created_by=f"model:{response.model_provider}",
            tags=["hypothesis"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope
