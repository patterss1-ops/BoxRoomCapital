"""AI analyst panel normalized verdict contract (G-003).

Defines the shared payload schema for AI model stock analysis verdicts.
This is a SEPARATE confidence signal — NOT a Signal Engine layer.
Consumed by G-004 (AI confidence gate) for execution gating.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class AIPanelOpinion(str, Enum):
    """Canonical AI analyst opinion categories."""

    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NEUTRAL = "neutral"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


class TimeHorizon(str, Enum):
    """Investment time horizon for the verdict."""

    INTRADAY = "intraday"
    SHORT_TERM = "short_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


OPINION_SCORE_MAP: Dict[AIPanelOpinion, float] = {
    AIPanelOpinion.STRONG_BUY: 1.0,
    AIPanelOpinion.BUY: 0.5,
    AIPanelOpinion.NEUTRAL: 0.0,
    AIPanelOpinion.SELL: -0.5,
    AIPanelOpinion.STRONG_SELL: -1.0,
}


@dataclass(frozen=True)
class AIModelVerdict:
    """One AI model's stock analysis verdict.

    Frozen for immutability and audit integrity.
    """

    model_name: str
    ticker: str
    as_of: str
    opinion: AIPanelOpinion
    confidence: float
    reasoning: str
    key_factors: Tuple[str, ...]
    time_horizon: TimeHorizon
    prompt_version: str
    response_hash: str
    latency_ms: float
    raw_response: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ticker = str(self.ticker or "").strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        object.__setattr__(self, "ticker", ticker)

        model = str(self.model_name or "").strip().lower()
        if not model:
            raise ValueError("model_name is required.")
        object.__setattr__(self, "model_name", model)

        conf = float(self.confidence)
        if not (0.0 <= conf <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {conf}")
        object.__setattr__(self, "confidence", conf)

        object.__setattr__(self, "key_factors", tuple(self.key_factors or ()))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def opinion_score(self) -> float:
        """Numeric score for aggregation: -1.0 to 1.0."""
        return OPINION_SCORE_MAP[self.opinion]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "ticker": self.ticker,
            "as_of": self.as_of,
            "opinion": self.opinion.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "key_factors": list(self.key_factors),
            "time_horizon": self.time_horizon.value,
            "prompt_version": self.prompt_version,
            "response_hash": self.response_hash,
            "latency_ms": self.latency_ms,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> AIModelVerdict:
        return cls(
            model_name=str(payload["model_name"]),
            ticker=str(payload["ticker"]),
            as_of=str(payload["as_of"]),
            opinion=AIPanelOpinion(str(payload["opinion"])),
            confidence=float(payload["confidence"]),
            reasoning=str(payload.get("reasoning", "")),
            key_factors=tuple(str(f) for f in payload.get("key_factors", ())),
            time_horizon=TimeHorizon(str(payload["time_horizon"])),
            prompt_version=str(payload.get("prompt_version", "")),
            response_hash=str(payload.get("response_hash", "")),
            latency_ms=float(payload.get("latency_ms", 0.0)),
            raw_response=payload.get("raw_response"),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(frozen=True)
class PanelConsensus:
    """Aggregated consensus from multiple AI model verdicts.

    Consumed by G-004 AI confidence gate.
    """

    ticker: str
    as_of: str
    consensus_opinion: AIPanelOpinion
    consensus_confidence: float
    consensus_score: float
    agreement_ratio: float
    opinion_distribution: Dict[str, int]
    models_responded: int
    models_failed: int
    verdicts: Tuple[AIModelVerdict, ...]
    failed_models: Tuple[str, ...]
    provenance_hash: str

    def __post_init__(self) -> None:
        ticker = str(self.ticker or "").strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        object.__setattr__(self, "ticker", ticker)

        conf = float(self.consensus_confidence)
        if not (0.0 <= conf <= 1.0):
            raise ValueError(
                f"consensus_confidence must be in [0, 1], got {conf}"
            )
        object.__setattr__(self, "consensus_confidence", conf)

        score = float(self.consensus_score)
        if not (-1.0 <= score <= 1.0):
            raise ValueError(
                f"consensus_score must be in [-1, 1], got {score}"
            )
        object.__setattr__(self, "consensus_score", score)

        object.__setattr__(self, "verdicts", tuple(self.verdicts or ()))
        object.__setattr__(self, "failed_models", tuple(self.failed_models or ()))
        object.__setattr__(
            self, "opinion_distribution", dict(self.opinion_distribution or {})
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "as_of": self.as_of,
            "consensus_opinion": self.consensus_opinion.value,
            "consensus_confidence": self.consensus_confidence,
            "consensus_score": self.consensus_score,
            "agreement_ratio": self.agreement_ratio,
            "opinion_distribution": dict(self.opinion_distribution),
            "models_responded": self.models_responded,
            "models_failed": self.models_failed,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "failed_models": list(self.failed_models),
            "provenance_hash": self.provenance_hash,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> PanelConsensus:
        return cls(
            ticker=str(payload["ticker"]),
            as_of=str(payload["as_of"]),
            consensus_opinion=AIPanelOpinion(str(payload["consensus_opinion"])),
            consensus_confidence=float(payload["consensus_confidence"]),
            consensus_score=float(payload["consensus_score"]),
            agreement_ratio=float(payload["agreement_ratio"]),
            opinion_distribution=dict(payload.get("opinion_distribution", {})),
            models_responded=int(payload["models_responded"]),
            models_failed=int(payload["models_failed"]),
            verdicts=tuple(
                AIModelVerdict.from_dict(v)
                for v in payload.get("verdicts", ())
            ),
            failed_models=tuple(
                str(m) for m in payload.get("failed_models", ())
            ),
            provenance_hash=str(payload.get("provenance_hash", "")),
        )
