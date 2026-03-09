"""EventCard to HypothesisCard formation service."""

from __future__ import annotations

import json

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
        parsed = self._normalize_model_payload(dict(response.parsed or {}))
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

    @classmethod
    def _normalize_model_payload(cls, payload: dict) -> dict:
        normalized = dict(payload)
        normalized["direction"] = cls._normalize_direction(normalized.get("direction"))
        normalized["horizon"] = cls._normalize_horizon(normalized.get("horizon"))
        normalized["confidence"] = cls._normalize_confidence(normalized.get("confidence"))
        normalized["invalidators"] = cls._normalize_string_list(normalized.get("invalidators"))
        normalized["failure_regimes"] = cls._normalize_string_list(normalized.get("failure_regimes"))
        normalized["candidate_expressions"] = cls._normalize_string_list(
            normalized.get("candidate_expressions"),
            preferred_keys=("expression", "trade", "idea", "text"),
        )
        normalized["testable_predictions"] = cls._normalize_string_list(
            normalized.get("testable_predictions"),
            preferred_keys=("prediction", "text", "check", "condition"),
        )
        return normalized

    @staticmethod
    def _normalize_direction(value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"long", "short"}:
            return text
        if any(token in text for token in {"sell", "down", "bear", "short"}):
            return "short"
        return "long"

    @staticmethod
    def _normalize_horizon(value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"intraday", "days", "weeks", "months"}:
            return text
        if "intraday" in text or "hour" in text:
            return "intraday"
        if "month" in text or "quarter" in text:
            return "months"
        if "week" in text:
            return "weeks"
        if "day" in text:
            return "days"
        return "days"

    @staticmethod
    def _normalize_confidence(value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.5
        if numeric > 1.0 and numeric <= 100.0:
            numeric /= 100.0
        return max(0.0, min(1.0, numeric))

    @staticmethod
    def _normalize_string_list(value: object, preferred_keys: tuple[str, ...] = ()) -> list[str]:
        items = value if isinstance(value, list) else ([] if value is None else [value])
        normalized: list[str] = []
        for item in items:
            text = ""
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                for key in preferred_keys:
                    candidate = str(item.get(key) or "").strip()
                    if candidate:
                        text = candidate
                        break
                if not text:
                    text = next(
                        (str(candidate).strip() for candidate in item.values() if str(candidate).strip()),
                        "",
                    )
                if not text:
                    text = json.dumps(item, sort_keys=True)
            elif item is not None:
                text = str(item).strip()
            if text:
                normalized.append(text)
        return normalized
