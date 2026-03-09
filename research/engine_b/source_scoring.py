"""Deterministic credibility scoring for Engine B sources."""

from __future__ import annotations


class SourceScoringService:
    """Assign a bounded credibility score to each source tier."""

    BASE_SCORES = {
        "filing": 0.95,
        "transcript": 0.90,
        "analyst_revision": 0.85,
        "news_wire": 0.80,
        "sa_quant": 0.75,
        "social_curated": 0.50,
        "social_general": 0.20,
    }

    def score_source(
        self,
        source_class: str,
        source_ids: list[str],
        corroboration_count: int | None = None,
    ) -> float:
        if source_class not in self.BASE_SCORES:
            raise ValueError(f"Unsupported source_class '{source_class}'")

        score = self.BASE_SCORES[source_class]
        corroboration = corroboration_count if corroboration_count is not None else len(set(source_ids))
        bonus = 0.0
        if corroboration > 1:
            bonus += min(0.10, 0.03 * (corroboration - 1))
        if len(set(source_ids)) > 1:
            bonus += 0.02
        return round(min(0.99, score + bonus), 2)
