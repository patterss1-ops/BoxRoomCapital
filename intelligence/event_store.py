"""Normalized research event persistence with provenance metadata."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Optional

from data.trade_db import DB_PATH, delete_research_events, get_research_event, get_research_events, upsert_research_event


def _canonical_json(value: Any) -> str:
    """Serialize a value into deterministic compact JSON."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _build_event_id(event_type: str, source: str, provenance_hash: str) -> str:
    raw = f"{event_type}|{source}|{provenance_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_provenance_hash(
    event_type: str,
    source: str,
    descriptor: dict[str, Any],
    source_ref: str = "",
) -> str:
    """Compute a deterministic provenance hash from source + descriptor."""
    envelope = {
        "event_type": event_type,
        "source": source,
        "source_ref": source_ref,
        "descriptor": descriptor,
    }
    return hashlib.sha256(_canonical_json(envelope).encode("utf-8")).hexdigest()


def compute_event_id(
    event_type: str,
    source: str,
    descriptor: dict[str, Any],
    source_ref: str = "",
) -> str:
    """Compute the deterministic research-event primary key."""
    provenance_hash = compute_provenance_hash(
        event_type=event_type,
        source=source,
        descriptor=descriptor,
        source_ref=source_ref,
    )
    return _build_event_id(event_type.strip().lower(), source.strip().lower(), provenance_hash)


@dataclass
class EventRecord:
    """Input shape for one research event row."""

    event_type: str
    source: str
    retrieved_at: str
    provenance_descriptor: dict[str, Any]
    source_ref: str = ""
    event_timestamp: str = ""
    symbol: str = ""
    headline: str = ""
    detail: str = ""
    confidence: Optional[float] = None
    payload: Optional[dict[str, Any]] = None
    event_id: str = ""


class EventStore:
    """Facade for writing and querying research events."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def write_event(self, event: EventRecord) -> dict[str, Any]:
        event_type = event.event_type.strip().lower()
        source = event.source.strip().lower()
        source_ref = event.source_ref.strip()
        retrieved_at = event.retrieved_at.strip() or datetime.utcnow().isoformat()
        descriptor = event.provenance_descriptor or {}

        provenance_hash = compute_provenance_hash(
            event_type=event_type,
            source=source,
            descriptor=descriptor,
            source_ref=source_ref,
        )
        event_id = event.event_id.strip() or _build_event_id(event_type, source, provenance_hash)

        descriptor_json = _canonical_json(descriptor)
        payload_json = _canonical_json(event.payload) if event.payload is not None else None
        upsert_research_event(
            event_id=event_id,
            event_type=event_type,
            source=source,
            source_ref=source_ref or None,
            retrieved_at=retrieved_at,
            event_timestamp=event.event_timestamp.strip() or None,
            symbol=event.symbol.strip().upper() or None,
            headline=event.headline.strip() or None,
            detail=event.detail.strip() or None,
            confidence=event.confidence,
            provenance_descriptor=descriptor_json,
            provenance_hash=provenance_hash,
            payload=payload_json,
            db_path=self.db_path,
        )
        return {
            "id": event_id,
            "event_type": event_type,
            "source": source,
            "source_ref": source_ref,
            "retrieved_at": retrieved_at,
            "provenance_hash": provenance_hash,
            "provenance_descriptor": descriptor,
        }

    def get_event(self, event_id: str) -> Optional[dict[str, Any]]:
        """Fetch one persisted event by ID."""
        row = get_research_event(event_id=event_id, db_path=self.db_path)
        if not row:
            return None

        payload_text = row.get("payload")
        descriptor_text = row.get("provenance_descriptor")
        if isinstance(payload_text, str) and payload_text:
            try:
                row["payload"] = json.loads(payload_text)
            except (TypeError, ValueError):
                pass
        if isinstance(descriptor_text, str) and descriptor_text:
            try:
                row["provenance_descriptor"] = json.loads(descriptor_text)
            except (TypeError, ValueError):
                pass
        return row

    def clear_events(self, event_type: str = "") -> int:
        """Delete events, optionally filtered by event_type. Returns count deleted."""
        return delete_research_events(
            event_type=event_type.strip().lower() or None,
            db_path=self.db_path,
        )

    def list_events(
        self,
        limit: int = 100,
        event_type: str = "",
        source: str = "",
    ) -> list[dict[str, Any]]:
        rows = get_research_events(
            limit=limit,
            event_type=event_type.strip().lower() or None,
            source=source.strip().lower() or None,
            db_path=self.db_path,
        )

        normalized: list[dict[str, Any]] = []
        for row in rows:
            payload_text = row.get("payload")
            descriptor_text = row.get("provenance_descriptor")
            if isinstance(payload_text, str) and payload_text:
                try:
                    row["payload"] = json.loads(payload_text)
                except (TypeError, ValueError):
                    pass
            if isinstance(descriptor_text, str) and descriptor_text:
                try:
                    row["provenance_descriptor"] = json.loads(descriptor_text)
                except (TypeError, ValueError):
                    pass
            normalized.append(row)
        return normalized
