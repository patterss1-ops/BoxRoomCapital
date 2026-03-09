"""Operator-facing synthesis of research chains."""

from __future__ import annotations

from research.artifact_store import ArtifactStore
from research.model_router import ModelRouter
from research.prompts.v1_synthesis import build_synthesis_prompt


class SynthesisService:
    """Generate a concise human-readable summary for a research chain."""

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        self._model_router = model_router
        self._artifact_store = artifact_store

    def synthesize(self, chain_id: str) -> str:
        chain = self._artifact_store.get_chain(chain_id)
        if not chain:
            raise ValueError(f"Research chain '{chain_id}' not found")

        artifacts = [
            {
                "artifact_id": envelope.artifact_id,
                "artifact_type": envelope.artifact_type.value,
                "created_at": envelope.created_at,
                "body": envelope.body,
            }
            for envelope in chain
        ]
        system_prompt, user_prompt = build_synthesis_prompt(chain_id, artifacts)
        response = self._model_router.call(
            "research_synthesis",
            prompt=user_prompt,
            system_prompt=system_prompt,
            artifact_id=chain[-1].artifact_id,
            engine=chain[-1].engine,
        )
        summary = (response.raw_text or "").strip()
        if not summary:
            raise ValueError("Synthesis response was empty")
        return summary
