"""Raw content to EventCard extraction service."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactType, Engine, EventCard
from research.model_router import ModelRouter
from research.prompts.v1_signal_extraction import build_signal_extraction_prompt


class SignalExtractionService:
    """Convert raw source events into structured EventCards."""

    _TICKER_STOPWORDS = {
        "A", "AN", "AND", "API", "AT", "BY", "COM", "FOR", "FROM", "GUIDE", "HTTP", "HTTPS",
        "IN", "IO", "NET", "OF", "ON", "OR", "SA", "SNAPSHOT", "THE", "TO", "URL", "USD", "VIA", "WWW",
    }

    def __init__(self, model_router: ModelRouter, artifact_store: ArtifactStore):
        self._model_router = model_router
        self._artifact_store = artifact_store

    @staticmethod
    def _normalize_content(raw_content: str) -> str:
        clean = re.sub(r"<[^>]+>", " ", raw_content or "")
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:16000]

    def extract(
        self,
        raw_content: str,
        source_class: str,
        source_credibility: float,
        source_ids: list[str],
        source_url: str | None = None,
    ) -> ArtifactEnvelope:
        normalized = self._normalize_content(raw_content)
        system_prompt, user_prompt = build_signal_extraction_prompt(
            source_class=source_class,
            credibility=source_credibility,
            content=normalized,
        )
        response = self._model_router.call(
            "signal_extraction",
            prompt=user_prompt,
            system_prompt=system_prompt,
            engine=Engine.ENGINE_B,
        )
        parsed = dict(response.parsed or {})
        parsed.setdefault("claims", [])
        parsed["affected_instruments"] = self._normalize_instruments(
            parsed.get("affected_instruments"),
            raw_content=normalized,
            source_ids=source_ids,
        )
        parsed.setdefault("market_implied_prior", "")
        parsed.setdefault("materiality", "low")
        parsed.setdefault("time_sensitivity", "days")

        event_card = EventCard(
            source_ids=source_ids,
            source_class=source_class,
            source_credibility=source_credibility,
            event_timestamp=parsed.get(
                "event_timestamp",
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
            corroboration_count=int(parsed.get("corroboration_count", 0)),
            claims=parsed["claims"],
            affected_instruments=parsed["affected_instruments"],
            market_implied_prior=parsed["market_implied_prior"],
            materiality=parsed["materiality"],
            time_sensitivity=parsed["time_sensitivity"],
            raw_content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.EVENT_CARD,
            engine=Engine.ENGINE_B,
            ticker=event_card.affected_instruments[0] if event_card.affected_instruments else None,
            body=event_card,
            created_by=f"model:{response.model_provider}",
            tags=[source_class] + ([source_url] if source_url else []),
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope

    @classmethod
    def _normalize_instruments(cls, value: object, *, raw_content: str, source_ids: list[str]) -> list[str]:
        instruments = []
        if isinstance(value, list):
            instruments = [str(item).strip().upper() for item in value if str(item).strip()]
        elif value not in (None, ""):
            instruments = [str(value).strip().upper()]
        if instruments:
            return instruments
        return cls._fallback_instruments(raw_content=raw_content, source_ids=source_ids)

    @classmethod
    def _fallback_instruments(cls, *, raw_content: str, source_ids: list[str]) -> list[str]:
        source_candidates: list[str] = []
        for text in source_ids:
            source_candidates.extend(cls._extract_ticker_candidates(str(text or "")))
        candidates = source_candidates or cls._extract_ticker_candidates(raw_content)
        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                deduped.append(candidate)
        return deduped

    @classmethod
    def _extract_ticker_candidates(cls, text: str) -> list[str]:
        if not text:
            return []
        matches = re.findall(r"\b[A-Z][A-Z.\-]{0,4}\b", text.upper())
        candidates: list[str] = []
        for match in matches:
            token = match.strip(".-")
            if not token or token in cls._TICKER_STOPWORDS:
                continue
            if not any(char.isalpha() for char in token):
                continue
            candidates.append(token)
        return candidates
