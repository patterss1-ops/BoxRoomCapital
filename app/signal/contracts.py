"""Shared Signal Engine payload contracts (E-001 contract freeze)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from app.signal.types import DEFAULT_LAYER_WEIGHTS, DecisionAction, LayerId, ScoreThresholds


def _parse_iso8601(value: str) -> None:
    """Validate ISO-8601 date/time string."""
    if not value or not value.strip():
        raise ValueError("as_of must be a non-empty ISO-8601 string.")
    candidate = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO-8601 timestamp '{value}'.") from exc


def resolve_layer_weights(
    overrides: Optional[Mapping[LayerId, float]] = None,
) -> Dict[LayerId, float]:
    """
    Return normalized layer weights.

    If `overrides` is provided, overridden values are applied then the full set is
    renormalized to sum to 1.0. This keeps downstream scoring deterministic even
    during experiments.
    """
    weights = dict(DEFAULT_LAYER_WEIGHTS)
    if overrides:
        for layer_id, value in overrides.items():
            if layer_id not in weights:
                raise ValueError(f"Unknown layer weight override: {layer_id}")
            value_float = float(value)
            if value_float < 0:
                raise ValueError(f"Weight for {layer_id.value} must be >= 0.")
            weights[layer_id] = value_float

    total = sum(float(v) for v in weights.values())
    if total <= 0:
        raise ValueError("Total layer weight must be > 0.")

    return {layer_id: (float(weight) / total) for layer_id, weight in weights.items()}


def decide_action(score: float, thresholds: Optional[ScoreThresholds] = None) -> DecisionAction:
    """Map a composite score to the canonical decision action."""
    t = thresholds or ScoreThresholds()
    value = float(score)
    if value >= t.auto_execute_gte:
        return DecisionAction.AUTO_EXECUTE_BUY
    if value >= t.review_gte:
        return DecisionAction.FLAG_FOR_REVIEW
    if value <= t.short_lte:
        return DecisionAction.SHORT_CANDIDATE
    return DecisionAction.NO_ACTION


@dataclass(frozen=True)
class LayerScore:
    """Canonical per-layer score payload."""

    layer_id: LayerId
    ticker: str
    score: float
    as_of: str
    source: str
    provenance_ref: Optional[str] = None
    confidence: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        ticker = str(self.ticker or "").strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        object.__setattr__(self, "ticker", ticker)

        source = str(self.source or "").strip()
        if not source:
            raise ValueError("source is required.")
        object.__setattr__(self, "source", source)

        _parse_iso8601(self.as_of)
        score = float(self.score)
        if not (0.0 <= score <= 100.0):
            raise ValueError(f"score must be within [0, 100]. got={score}")
        object.__setattr__(self, "score", score)

        if self.confidence is not None:
            confidence = float(self.confidence)
            if not (0.0 <= confidence <= 1.0):
                raise ValueError(f"confidence must be within [0, 1]. got={confidence}")
            object.__setattr__(self, "confidence", confidence)

        # Freeze a copy so downstream mutations do not alter contract payloads.
        object.__setattr__(self, "details", dict(self.details or {}))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer_id": self.layer_id.value,
            "ticker": self.ticker,
            "score": self.score,
            "as_of": self.as_of,
            "source": self.source,
            "provenance_ref": self.provenance_ref,
            "confidence": self.confidence,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LayerScore":
        return cls(
            layer_id=LayerId(str(payload["layer_id"])),
            ticker=str(payload["ticker"]),
            score=float(payload["score"]),
            as_of=str(payload["as_of"]),
            source=str(payload["source"]),
            provenance_ref=payload.get("provenance_ref"),
            confidence=payload.get("confidence"),
            details=dict(payload.get("details") or {}),
        )


@dataclass(frozen=True)
class CompositeRequest:
    """Input contract for composite score + decision evaluation."""

    ticker: str
    as_of: str
    layer_scores: Tuple[LayerScore, ...]
    thresholds: ScoreThresholds = field(default_factory=ScoreThresholds)
    weight_overrides: Dict[LayerId, float] = field(default_factory=dict)

    def __post_init__(self):
        ticker = str(self.ticker or "").strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        object.__setattr__(self, "ticker", ticker)
        _parse_iso8601(self.as_of)

        if not self.layer_scores:
            raise ValueError("layer_scores cannot be empty.")

        # Ensure stable tuple + payload consistency.
        score_tuple = tuple(self.layer_scores)
        seen: set[LayerId] = set()
        for layer_score in score_tuple:
            if layer_score.layer_id in seen:
                raise ValueError(f"Duplicate layer score for {layer_score.layer_id.value}.")
            seen.add(layer_score.layer_id)
            if layer_score.ticker != ticker:
                raise ValueError(
                    f"Layer ticker mismatch for {layer_score.layer_id.value}: "
                    f"{layer_score.ticker} != {ticker}."
                )
            if layer_score.as_of != self.as_of:
                raise ValueError(
                    f"Layer as_of mismatch for {layer_score.layer_id.value}: "
                    f"{layer_score.as_of} != {self.as_of}."
                )
        object.__setattr__(self, "layer_scores", score_tuple)

        normalized_overrides: Dict[LayerId, float] = {}
        for key, value in dict(self.weight_overrides or {}).items():
            layer_id = key if isinstance(key, LayerId) else LayerId(str(key))
            normalized_overrides[layer_id] = float(value)
        resolve_layer_weights(normalized_overrides)
        object.__setattr__(self, "weight_overrides", normalized_overrides)

    def resolved_weights(self) -> Dict[LayerId, float]:
        return resolve_layer_weights(self.weight_overrides)

    def score_map(self) -> Dict[LayerId, float]:
        return {item.layer_id: item.score for item in self.layer_scores}

    def sources(self) -> Dict[LayerId, str]:
        return {item.layer_id: item.source for item in self.layer_scores}


@dataclass(frozen=True)
class CompositeResult:
    """Output contract for composite scoring + decision stage."""

    ticker: str
    as_of: str
    weighted_score: float
    convergence_bonus_pct: float
    final_score: float
    action: DecisionAction
    layer_scores: Dict[LayerId, float]
    vetoes: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()

    def __post_init__(self):
        ticker = str(self.ticker or "").strip().upper()
        if not ticker:
            raise ValueError("ticker is required.")
        object.__setattr__(self, "ticker", ticker)
        _parse_iso8601(self.as_of)

        weighted = float(self.weighted_score)
        bonus = float(self.convergence_bonus_pct)
        final = float(self.final_score)
        if weighted < 0 or final < 0:
            raise ValueError("weighted_score/final_score must be >= 0.")
        if bonus < 0:
            raise ValueError("convergence_bonus_pct must be >= 0.")
        object.__setattr__(self, "weighted_score", weighted)
        object.__setattr__(self, "convergence_bonus_pct", bonus)
        object.__setattr__(self, "final_score", final)

        normalized_scores: Dict[LayerId, float] = {}
        for key, value in dict(self.layer_scores or {}).items():
            layer_id = key if isinstance(key, LayerId) else LayerId(str(key))
            score = float(value)
            if not (0.0 <= score <= 100.0):
                raise ValueError(
                    f"layer_scores[{layer_id.value}] must be within [0, 100]. got={score}"
                )
            normalized_scores[layer_id] = score
        object.__setattr__(self, "layer_scores", normalized_scores)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "as_of": self.as_of,
            "weighted_score": self.weighted_score,
            "convergence_bonus_pct": self.convergence_bonus_pct,
            "final_score": self.final_score,
            "action": self.action.value,
            "layer_scores": {layer_id.value: score for layer_id, score in self.layer_scores.items()},
            "vetoes": list(self.vetoes),
            "notes": list(self.notes),
        }


def build_composite_result(
    request: CompositeRequest,
    weighted_score: float,
    convergence_bonus_pct: float = 0.0,
    vetoes: Optional[Iterable[str]] = None,
    notes: Optional[Iterable[str]] = None,
    action: Optional[DecisionAction] = None,
) -> CompositeResult:
    """Build a validated CompositeResult from a request and computed score."""
    final_score = float(weighted_score) * (1.0 + (float(convergence_bonus_pct) / 100.0))
    final_score = max(0.0, min(100.0, final_score))
    decision = action or decide_action(final_score, request.thresholds)
    return CompositeResult(
        ticker=request.ticker,
        as_of=request.as_of,
        weighted_score=float(weighted_score),
        convergence_bonus_pct=float(convergence_bonus_pct),
        final_score=final_score,
        action=decision,
        layer_scores=request.score_map(),
        vetoes=tuple(vetoes or ()),
        notes=tuple(notes or ()),
    )

