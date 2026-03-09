"""LLM regime-transition annotation service."""

from __future__ import annotations

from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactType, Engine, RegimeJournal, RegimeSnapshot
from research.model_router import ModelRouter
from research.prompts.v1_regime_journal import build_regime_journal_prompt


class RegimeJournalService:
    """Generate lightweight operator notes for regime transitions."""

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        self._model_router = model_router
        self._artifact_store = artifact_store

    @staticmethod
    def _state_signature(snapshot: RegimeSnapshot) -> tuple[str, str, str, str]:
        return (
            snapshot.vol_regime,
            snapshot.trend_regime,
            snapshot.carry_regime,
            snapshot.macro_regime,
        )

    def annotate_transition(
        self,
        previous: RegimeSnapshot | dict | None,
        current: RegimeSnapshot | dict,
        regime_snapshot_ref: str | None = None,
        chain_id: str | None = None,
    ) -> ArtifactEnvelope | None:
        previous_model = None
        if previous is not None:
            previous_model = previous if isinstance(previous, RegimeSnapshot) else RegimeSnapshot.model_validate(previous)
        current_model = current if isinstance(current, RegimeSnapshot) else RegimeSnapshot.model_validate(current)

        if previous_model is not None and self._state_signature(previous_model) == self._state_signature(current_model):
            return None

        system_prompt, user_prompt = build_regime_journal_prompt(previous_model, current_model)
        response = self._model_router.call(
            "regime_journal",
            prompt=user_prompt,
            system_prompt=system_prompt,
            artifact_id=regime_snapshot_ref,
            engine=Engine.ENGINE_A,
        )
        parsed = dict(response.parsed or {})
        summary = parsed.get("summary") or response.raw_text.strip()
        journal = RegimeJournal(
            as_of=current_model.as_of,
            regime_snapshot_ref=regime_snapshot_ref,
            summary=summary,
            key_changes=list(parsed.get("key_changes", [])),
            risks=list(parsed.get("risks", [])),
        )
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.REGIME_JOURNAL,
            engine=Engine.ENGINE_A,
            ticker=None,
            chain_id=chain_id,
            body=journal,
            created_by=f"model:{response.model_provider}",
            tags=["regime_journal"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope
