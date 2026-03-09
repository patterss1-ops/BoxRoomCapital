"""L9 Research overlay derived from Engine B scoring artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.signal.contracts import LayerScore
from app.signal.types import LayerId


@dataclass(frozen=True)
class ResearchSignalSnapshot:
    """Minimal normalized view of the latest Engine B scoring state for one ticker."""

    ticker: str
    artifact_id: str
    chain_id: str
    as_of: str
    final_score: float
    outcome: str
    outcome_reason: str = ""
    raw_total: float | None = None
    current_stage: str = ""
    blocking_objections: list[str] = field(default_factory=list)
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticker", str(self.ticker or "").strip().upper())
        object.__setattr__(self, "artifact_id", str(self.artifact_id or "").strip())
        object.__setattr__(self, "chain_id", str(self.chain_id or "").strip())
        object.__setattr__(self, "as_of", str(self.as_of or "").strip())
        object.__setattr__(self, "final_score", max(0.0, min(100.0, float(self.final_score))))
        object.__setattr__(self, "outcome", str(self.outcome or "").strip().lower())
        object.__setattr__(self, "outcome_reason", str(self.outcome_reason or "").strip())
        object.__setattr__(self, "current_stage", str(self.current_stage or "").strip().lower())
        object.__setattr__(
            self,
            "blocking_objections",
            [str(item).strip() for item in self.blocking_objections if str(item).strip()],
        )
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if not self.ticker:
            raise ValueError("ticker is required.")
        if not self.artifact_id:
            raise ValueError("artifact_id is required.")
        if not self.chain_id:
            raise ValueError("chain_id is required.")
        if not self.as_of:
            raise ValueError("as_of is required.")
        if self.confidence is not None:
            object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))


def _translate_research_score(snapshot: ResearchSignalSnapshot) -> float:
    raw = float(snapshot.final_score)
    outcome = snapshot.outcome
    if outcome == "promote":
        translated = raw
    elif outcome == "revise":
        translated = min(raw, 58.0)
    elif outcome == "park":
        translated = min(raw, 35.0 if snapshot.blocking_objections else 42.0)
    elif outcome == "reject":
        translated = min(raw, 12.0)
    else:
        translated = min(raw, 45.0)

    if snapshot.current_stage in {"retired", "review_rejected", "taxonomy_rejected"}:
        translated = min(translated, 10.0)
    return round(max(0.0, min(100.0, translated)), 2)


def score_research_signal(snapshot: ResearchSignalSnapshot) -> LayerScore:
    """Convert a latest Engine B scoring snapshot into an L9 LayerScore."""
    translated_score = _translate_research_score(snapshot)
    vetoes: list[str] = []
    if snapshot.blocking_objections:
        vetoes.append("research_blocking_objections")
    if snapshot.outcome == "reject" or snapshot.current_stage in {"retired", "review_rejected", "taxonomy_rejected"}:
        vetoes.append("research_rejected")

    details = {
        "artifact_type": "scoring_result",
        "chain_id": snapshot.chain_id,
        "outcome": snapshot.outcome,
        "outcome_reason": snapshot.outcome_reason,
        "current_stage": snapshot.current_stage or "scored",
        "raw_final_score": float(snapshot.final_score),
        "raw_total": float(snapshot.raw_total) if snapshot.raw_total is not None else None,
        "translated_score": float(translated_score),
        "blocking_objection_count": len(snapshot.blocking_objections),
        "blocking_objections": list(snapshot.blocking_objections),
        "vetoes": vetoes,
        **snapshot.metadata,
    }
    details = {key: value for key, value in details.items() if value not in (None, "", [], {})}

    confidence = snapshot.confidence
    if confidence is None:
        confidence = round(max(0.35, min(0.99, translated_score / 100.0)), 2)

    return LayerScore(
        layer_id=LayerId.L9_RESEARCH,
        ticker=snapshot.ticker,
        score=translated_score,
        as_of=snapshot.as_of,
        source="research-engine-b",
        provenance_ref=snapshot.artifact_id,
        confidence=confidence,
        details=details,
    )
