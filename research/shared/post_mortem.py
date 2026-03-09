"""LLM-backed post-mortem generation for research chains."""

from __future__ import annotations

from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactType, Engine, HypothesisCard, PostMortemNote
from research.model_router import ModelRouter
from research.prompts.v1_post_mortem import build_post_mortem_prompt


class PostMortemService:
    """Generate PostMortemNote artifacts for completed research chains."""

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        self._model_router = model_router
        self._artifact_store = artifact_store

    def generate_post_mortem(self, hypothesis_id: str) -> ArtifactEnvelope:
        hypothesis_env = self._artifact_store.get(hypothesis_id)
        if hypothesis_env is None or hypothesis_env.artifact_type != ArtifactType.HYPOTHESIS_CARD:
            raise ValueError(f"HypothesisCard '{hypothesis_id}' not found")

        hypothesis = HypothesisCard.model_validate(hypothesis_env.body)
        chain = self._artifact_store.get_chain(hypothesis_env.chain_id)
        artifacts = [
            {
                "artifact_id": envelope.artifact_id,
                "artifact_type": envelope.artifact_type.value,
                "created_at": envelope.created_at,
                "body": envelope.body,
            }
            for envelope in chain
        ]
        system_prompt, user_prompt = build_post_mortem_prompt(hypothesis_id, artifacts)
        response = self._model_router.call(
            "post_mortem",
            prompt=user_prompt,
            system_prompt=system_prompt,
            artifact_id=hypothesis_id,
            engine=Engine.ENGINE_B,
        )
        parsed = dict(response.parsed or {})
        parsed.setdefault("hypothesis_ref", hypothesis_id)
        note = PostMortemNote.model_validate(parsed)
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.POST_MORTEM_NOTE,
            engine=Engine.ENGINE_B,
            ticker=hypothesis_env.ticker,
            edge_family=hypothesis.edge_family,
            chain_id=hypothesis_env.chain_id,
            body=note,
            created_by=f"model:{response.model_provider}",
            tags=["post_mortem"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope
