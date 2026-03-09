"""Normalization and deduplication for Engine B raw inputs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from research.artifact_store import ArtifactStore
from research.artifacts import ArtifactEnvelope, ArtifactType, Engine
from research.engine_b.source_scoring import SourceScoringService


@dataclass(frozen=True)
class IntakeResult:
    normalized_content: str
    source_class: str
    source_ids: list[str]
    source_credibility: float
    raw_content_hash: str
    occurred_at: str
    deduplicated: bool = False
    duplicate_artifact_id: str | None = None
    instrument_hints: list[str] = field(default_factory=list)


class IntakeService:
    """Prepare raw content for the Engine B pipeline."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        source_scoring: SourceScoringService | None = None,
        dedup_lookback: int = 50,
    ):
        self._artifact_store = artifact_store
        self._source_scoring = source_scoring or SourceScoringService()
        self._dedup_lookback = dedup_lookback

    @staticmethod
    def _normalize_content(raw_content: str) -> str:
        clean = re.sub(r"<[^>]+>", " ", raw_content or "")
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:16000]

    @staticmethod
    def _extract_instrument_hints(content: str) -> list[str]:
        matches = re.findall(r"\b[A-Z]{1,5}\b", content)
        hints: list[str] = []
        for match in matches:
            if match not in hints:
                hints.append(match)
        return hints[:10]

    def _find_duplicate(self, raw_content_hash: str) -> str | None:
        query = getattr(self._artifact_store, "query", None)
        if not callable(query):
            return None
        recent = query(
            artifact_type=ArtifactType.EVENT_CARD,
            engine=Engine.ENGINE_B,
            limit=self._dedup_lookback,
        )
        for artifact in recent:
            body = artifact.body if isinstance(artifact, ArtifactEnvelope) else {}
            if isinstance(body, dict) and body.get("raw_content_hash") == raw_content_hash:
                return artifact.artifact_id
        return None

    def ingest(
        self,
        raw_content: str,
        source_class: str,
        source_ids: list[str],
        occurred_at: str | None = None,
    ) -> IntakeResult:
        normalized = self._normalize_content(raw_content)
        raw_content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        duplicate_artifact_id = self._find_duplicate(raw_content_hash)
        credibility = self._source_scoring.score_source(
            source_class=source_class,
            source_ids=source_ids,
        )
        return IntakeResult(
            normalized_content=normalized,
            source_class=source_class,
            source_ids=list(source_ids),
            source_credibility=credibility,
            raw_content_hash=raw_content_hash,
            occurred_at=occurred_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            deduplicated=duplicate_artifact_id is not None,
            duplicate_artifact_id=duplicate_artifact_id,
            instrument_hints=self._extract_instrument_hints(normalized),
        )
