"""Hypothesis challenge and falsification service."""

from __future__ import annotations

import json

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
        parsed = self._normalize_model_payload(dict(response.parsed or {}), default_model=response.model_id)
        parsed.setdefault("hypothesis_ref", hypothesis_id)
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

    @classmethod
    def _normalize_model_payload(cls, payload: dict, *, default_model: str) -> dict:
        normalized = dict(payload)
        normalized["cheapest_alternative"] = cls._string_from_object(
            normalized.get("cheapest_alternative"),
            preferred_keys=("summary", "alternative", "text", "description"),
        )
        normalized["beta_leakage_check"] = cls._normalize_beta_leakage(normalized.get("beta_leakage_check"))
        normalized["crowding_check"] = cls._normalize_crowding(normalized.get("crowding_check"))
        normalized["prior_evidence"] = cls._normalize_prior_evidence(normalized.get("prior_evidence"))
        normalized["unresolved_objections"] = cls._normalize_string_list(normalized.get("unresolved_objections"))
        normalized["resolved_objections"] = cls._normalize_string_list(normalized.get("resolved_objections"))
        normalized["challenge_model"] = cls._string_from_object(
            normalized.get("challenge_model"),
            preferred_keys=("model", "model_id", "summary", "core_claim"),
        ) or default_model
        normalized["challenge_confidence"] = cls._normalize_confidence(normalized.get("challenge_confidence"))
        return normalized

    @classmethod
    def _normalize_beta_leakage(cls, value: object) -> dict:
        if isinstance(value, dict):
            if {"is_just_market_exposure", "explanation", "estimated_beta"} <= set(value):
                return value
            summary = cls._string_from_object(
                value,
                preferred_keys=("explanation", "summary", "verdict", "analysis"),
            )
            lower = summary.lower()
            is_market = any(token in lower for token in {"high beta", "mostly beta", "just market", "market exposure"})
            estimated_beta = 1.0 if is_market else 0.3
            return {
                "is_just_market_exposure": is_market,
                "explanation": summary or "Model returned beta leakage analysis",
                "estimated_beta": estimated_beta,
            }
        return {
            "is_just_market_exposure": False,
            "explanation": cls._string_from_object(value) or "Model returned beta leakage analysis",
            "estimated_beta": 0.0,
        }

    @classmethod
    def _normalize_crowding(cls, value: object) -> dict:
        if isinstance(value, dict):
            if {"crowding_level", "explanation"} <= set(value):
                return value
            summary = cls._string_from_object(
                value,
                preferred_keys=("explanation", "summary", "verdict", "analysis"),
            )
            lower = summary.lower()
            crowding_level = "medium"
            for level in ("extreme", "high", "medium", "low"):
                if level in lower:
                    crowding_level = level
                    break
            correlated = cls._normalize_string_list(value.get("correlated_strategies"))
            return {
                "crowding_level": crowding_level,
                "explanation": summary or "Model returned crowding analysis",
                "correlated_strategies": correlated,
            }
        return {
            "crowding_level": "medium",
            "explanation": cls._string_from_object(value) or "Model returned crowding analysis",
            "correlated_strategies": [],
        }

    @classmethod
    def _normalize_prior_evidence(cls, value: object) -> list[dict]:
        if isinstance(value, list):
            items = value
        elif value is None:
            items = []
        else:
            items = [value]

        normalized: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                description = cls._string_from_object(
                    item,
                    preferred_keys=("description", "summary", "evidence", "text", "verdict"),
                )
                supports = item.get("supports_hypothesis")
                if not isinstance(supports, bool):
                    supports = "support" in description.lower() and "not support" not in description.lower()
                source = cls._string_from_object(item.get("source")) or "model"
                strength = cls._normalize_strength(item.get("strength"), description)
            else:
                description = cls._string_from_object(item)
                supports = False
                source = "model"
                strength = cls._normalize_strength(None, description)
            if description:
                normalized.append(
                    {
                        "description": description,
                        "supports_hypothesis": supports,
                        "source": source,
                        "strength": strength,
                    }
                )
        return normalized

    @staticmethod
    def _normalize_strength(value: object, context: str = "") -> str:
        text = f"{value or ''} {context}".lower()
        for level in ("strong", "moderate", "weak"):
            if level in text:
                return level
        return "moderate"

    @staticmethod
    def _normalize_confidence(value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.5
        if numeric > 1.0 and numeric <= 100.0:
            numeric /= 100.0
        return max(0.0, min(1.0, numeric))

    @classmethod
    def _normalize_string_list(cls, value: object) -> list[str]:
        items = value if isinstance(value, list) else ([] if value is None else [value])
        normalized: list[str] = []
        for item in items:
            text = cls._string_from_object(item)
            if text:
                normalized.append(text)
        return normalized

    @staticmethod
    def _string_from_object(value: object, preferred_keys: tuple[str, ...] = ()) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in preferred_keys:
                candidate = str(value.get(key) or "").strip()
                if candidate:
                    return candidate
            for candidate in value.values():
                text = str(candidate).strip()
                if text:
                    return text
            return json.dumps(value, sort_keys=True)
        if value is None:
            return ""
        return str(value).strip()
