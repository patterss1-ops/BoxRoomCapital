"""AI panel coordinator — aggregates model verdicts into consensus (G-003)."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.signal.ai_contracts import (
    AIModelVerdict,
    AIPanelOpinion,
    OPINION_SCORE_MAP,
    PanelConsensus,
)

logger = logging.getLogger(__name__)

VerdictFetcher = Callable[[str, str, Optional[Dict[str, Any]]], AIModelVerdict]


def _compute_consensus_provenance(verdicts: Sequence[AIModelVerdict]) -> str:
    """Deterministic hash of all verdict response hashes for audit."""
    hashes = sorted(v.response_hash for v in verdicts)
    combined = "|".join(hashes)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def compute_consensus(
    verdicts: Sequence[AIModelVerdict],
) -> Tuple[AIPanelOpinion, float, float, float, Dict[str, int]]:
    """Compute consensus opinion, confidence, score, agreement ratio, distribution.

    Returns:
        (consensus_opinion, consensus_confidence, consensus_score,
         agreement_ratio, opinion_distribution)
    """
    if not verdicts:
        return AIPanelOpinion.NEUTRAL, 0.0, 0.0, 0.0, {}

    total_weight = sum(v.confidence for v in verdicts)
    if total_weight <= 0:
        weighted_score = 0.0
    else:
        weighted_score = sum(
            v.opinion_score * v.confidence for v in verdicts
        ) / total_weight

    weighted_score = max(-1.0, min(1.0, weighted_score))

    if weighted_score >= 0.75:
        consensus_opinion = AIPanelOpinion.STRONG_BUY
    elif weighted_score >= 0.25:
        consensus_opinion = AIPanelOpinion.BUY
    elif weighted_score >= -0.25:
        consensus_opinion = AIPanelOpinion.NEUTRAL
    elif weighted_score >= -0.75:
        consensus_opinion = AIPanelOpinion.SELL
    else:
        consensus_opinion = AIPanelOpinion.STRONG_SELL

    consensus_confidence = sum(v.confidence for v in verdicts) / len(verdicts)

    opinion_counts = Counter(v.opinion for v in verdicts)
    majority_count = max(opinion_counts.values())
    agreement_ratio = majority_count / len(verdicts)

    opinion_distribution = {
        opinion.value: count for opinion, count in opinion_counts.items()
    }

    return (
        consensus_opinion,
        round(consensus_confidence, 4),
        round(weighted_score, 4),
        round(agreement_ratio, 4),
        opinion_distribution,
    )


class PanelCoordinator:
    """Coordinates multiple AI model clients into a consensus verdict."""

    def __init__(
        self,
        fetchers: Optional[Dict[str, VerdictFetcher]] = None,
    ):
        self._fetchers: Dict[str, VerdictFetcher] = dict(fetchers or {})

    def register(self, model_name: str, fetcher: VerdictFetcher) -> None:
        """Register a model client's fetch function."""
        self._fetchers[model_name.lower()] = fetcher

    def query_panel(
        self,
        ticker: str,
        as_of: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> PanelConsensus:
        """Query all registered models and aggregate into consensus.

        Graceful degradation: works with 1-N models. Failed models are
        recorded but do not block consensus from available verdicts.
        """
        verdicts: List[AIModelVerdict] = []
        failed_models: List[str] = []

        for model_name, fetcher in sorted(self._fetchers.items()):
            try:
                verdict = fetcher(ticker, as_of, context)
                verdicts.append(verdict)
                logger.info(
                    "AI panel %s verdict for %s: %s (conf=%.2f)",
                    model_name,
                    ticker,
                    verdict.opinion.value,
                    verdict.confidence,
                )
            except Exception as exc:
                failed_models.append(model_name)
                logger.warning(
                    "AI panel %s failed for %s: %s",
                    model_name,
                    ticker,
                    exc,
                )

        if not verdicts:
            return PanelConsensus(
                ticker=ticker,
                as_of=as_of,
                consensus_opinion=AIPanelOpinion.NEUTRAL,
                consensus_confidence=0.0,
                consensus_score=0.0,
                agreement_ratio=0.0,
                opinion_distribution={},
                models_responded=0,
                models_failed=len(failed_models),
                verdicts=(),
                failed_models=tuple(failed_models),
                provenance_hash="",
            )

        (
            consensus_opinion,
            consensus_confidence,
            consensus_score,
            agreement_ratio,
            opinion_distribution,
        ) = compute_consensus(verdicts)

        provenance_hash = _compute_consensus_provenance(verdicts)

        return PanelConsensus(
            ticker=ticker,
            as_of=as_of,
            consensus_opinion=consensus_opinion,
            consensus_confidence=consensus_confidence,
            consensus_score=consensus_score,
            agreement_ratio=agreement_ratio,
            opinion_distribution=opinion_distribution,
            models_responded=len(verdicts),
            models_failed=len(failed_models),
            verdicts=tuple(verdicts),
            failed_models=tuple(failed_models),
            provenance_hash=provenance_hash,
        )
