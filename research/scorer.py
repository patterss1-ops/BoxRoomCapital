"""Deterministic scoring engine for research hypotheses."""

from __future__ import annotations

from dataclasses import dataclass

from research.artifact_store import ArtifactStore
from research.artifacts import (
    ArtifactEnvelope,
    ArtifactType,
    Engine,
    EventCard,
    FalsificationMemo,
    HypothesisCard,
    ProgressionStage,
    PromotionOutcome,
    RegimeSnapshot,
    ScoringResult,
)


@dataclass
class ScoreContext:
    hypothesis: HypothesisCard
    falsification: FalsificationMemo
    event: EventCard
    regime: RegimeSnapshot | None = None
    existing_positions: dict[str, float] | None = None


class ScoringEngine:
    """Deterministic 100-point rubric evaluation."""

    DIMENSION_WEIGHTS = {
        "source_integrity": 10.0,
        "mechanism_clarity": 15.0,
        "prior_empirical_support": 15.0,
        "incremental_info_advantage": 10.0,
        "regime_fit": 10.0,
        "testability": 10.0,
        "implementation_realism": 15.0,
        "portfolio_fit": 10.0,
        "kill_clarity": 5.0,
    }

    def __init__(self, artifact_store: ArtifactStore):
        self._artifact_store = artifact_store

    def score(
        self,
        hypothesis_id: str,
        falsification_id: str,
        regime: dict | RegimeSnapshot | None = None,
        existing_positions: dict[str, float] | None = None,
    ) -> ArtifactEnvelope:
        hypothesis_env = self._artifact_store.get(hypothesis_id)
        falsification_env = self._artifact_store.get(falsification_id)
        if hypothesis_env is None or hypothesis_env.artifact_type != ArtifactType.HYPOTHESIS_CARD:
            raise ValueError(f"HypothesisCard '{hypothesis_id}' not found")
        if falsification_env is None or falsification_env.artifact_type != ArtifactType.FALSIFICATION_MEMO:
            raise ValueError(f"FalsificationMemo '{falsification_id}' not found")

        hypothesis = HypothesisCard.model_validate(hypothesis_env.body)
        falsification = FalsificationMemo.model_validate(falsification_env.body)
        event_env = self._artifact_store.get(hypothesis.event_card_ref)
        if event_env is None or event_env.artifact_type != ArtifactType.EVENT_CARD:
            raise ValueError(f"EventCard '{hypothesis.event_card_ref}' not found")
        event = EventCard.model_validate(event_env.body)

        regime_model = None
        if regime is not None:
            regime_model = regime if isinstance(regime, RegimeSnapshot) else RegimeSnapshot.model_validate(regime)

        context = ScoreContext(
            hypothesis=hypothesis,
            falsification=falsification,
            event=event,
            regime=regime_model,
            existing_positions=existing_positions,
        )
        dimension_scores = {
            "source_integrity": self._score_source_integrity(context),
            "mechanism_clarity": self._score_mechanism_clarity(context),
            "prior_empirical_support": self._score_prior_empirical_support(context),
            "incremental_info_advantage": self._score_incremental_info_advantage(context),
            "regime_fit": self._score_regime_fit(context),
            "testability": self._score_testability(context),
            "implementation_realism": self._score_implementation_realism(context),
            "portfolio_fit": self._score_portfolio_fit(context),
            "kill_clarity": self._score_kill_clarity(context),
        }
        raw_total = round(sum(dimension_scores.values()), 2)
        penalties = {
            "search_complexity": self._score_search_complexity_penalty(context),
            "crowding": self._score_crowding_penalty(context),
            "data_fragility": self._score_data_fragility_penalty(context),
        }
        final_score = round(max(0.0, min(100.0, raw_total + sum(penalties.values()))), 2)
        blocking_objections = list(context.falsification.unresolved_objections)
        if blocking_objections:
            outcome = PromotionOutcome.PARK
            outcome_reason = "Blocking objections remain unresolved"
            next_stage = None
        elif final_score < 60:
            outcome = PromotionOutcome.REJECT
            outcome_reason = "Score below minimum threshold"
            next_stage = None
        elif final_score < 70:
            outcome = PromotionOutcome.REVISE
            outcome_reason = "Thesis requires revision before testing"
            next_stage = None
        else:
            outcome = PromotionOutcome.PROMOTE
            next_stage = self._determine_next_stage(final_score)
            outcome_reason = f"Score supports progression to {next_stage.value}"

        result = ScoringResult(
            hypothesis_ref=hypothesis_id,
            falsification_ref=falsification_id,
            dimension_scores=dimension_scores,
            raw_total=raw_total,
            penalties=penalties,
            final_score=final_score,
            outcome=outcome,
            outcome_reason=outcome_reason,
            next_stage=next_stage,
            blocking_objections=blocking_objections,
        )
        envelope = ArtifactEnvelope(
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker=hypothesis_env.ticker,
            edge_family=hypothesis_env.edge_family,
            chain_id=hypothesis_env.chain_id,
            body=result,
            created_by="system",
            tags=["scoring"],
        )
        envelope.artifact_id = self._artifact_store.save(envelope)
        return envelope

    @staticmethod
    def _determine_next_stage(final_score: float) -> ProgressionStage:
        if final_score >= 90:
            return ProgressionStage.PILOT
        if final_score >= 80:
            return ProgressionStage.EXPERIMENT
        return ProgressionStage.TEST

    def _score_source_integrity(self, context: ScoreContext) -> float:
        return round(self.DIMENSION_WEIGHTS["source_integrity"] * context.event.source_credibility, 2)

    def _score_mechanism_clarity(self, context: ScoreContext) -> float:
        score = 0.0
        if len(context.hypothesis.mechanism.strip()) >= 20:
            score += 7.0
        elif context.hypothesis.mechanism.strip():
            score += 4.0
        if len(context.hypothesis.variant_view.strip()) >= 15:
            score += 4.0
        if len(context.hypothesis.catalyst.strip()) >= 10:
            score += 4.0
        return min(self.DIMENSION_WEIGHTS["mechanism_clarity"], round(score, 2))

    def _score_prior_empirical_support(self, context: ScoreContext) -> float:
        score = 0.0
        strength_map = {"weak": 2.0, "moderate": 4.0, "strong": 6.0}
        for prior in context.falsification.prior_evidence:
            delta = strength_map[prior.strength]
            score += delta if prior.supports_hypothesis else -delta / 2
        return max(0.0, min(self.DIMENSION_WEIGHTS["prior_empirical_support"], round(score, 2)))

    def _score_incremental_info_advantage(self, context: ScoreContext) -> float:
        source_map = {
            "filing": 9.0,
            "transcript": 8.5,
            "analyst_revision": 8.0,
            "news_wire": 7.0,
            "sa_quant": 6.5,
            "social_curated": 4.0,
            "social_general": 2.0,
        }
        score = source_map.get(context.event.source_class, 3.0)
        if context.event.materiality == "high":
            score += 1.0
        if context.event.time_sensitivity == "immediate":
            score += 0.5
        return min(self.DIMENSION_WEIGHTS["incremental_info_advantage"], round(score, 2))

    def _score_regime_fit(self, context: ScoreContext) -> float:
        if context.regime is None:
            return 7.0
        blockers = {
            context.regime.vol_regime,
            context.regime.trend_regime,
            context.regime.carry_regime,
            context.regime.macro_regime,
        }
        if blockers.intersection(set(context.hypothesis.failure_regimes)):
            return 2.0
        score = 8.0
        if context.regime.sizing_factor >= 0.75:
            score += 2.0
        return min(self.DIMENSION_WEIGHTS["regime_fit"], round(score, 2))

    def _score_testability(self, context: ScoreContext) -> float:
        score = min(6.0, 2.0 * len(context.hypothesis.testable_predictions))
        score += min(4.0, 2.0 * len(context.hypothesis.invalidators))
        return min(self.DIMENSION_WEIGHTS["testability"], round(score, 2))

    def _score_implementation_realism(self, context: ScoreContext) -> float:
        score = 5.0
        if len(context.hypothesis.candidate_expressions) <= 3:
            score += 4.0
        elif len(context.hypothesis.candidate_expressions) <= 5:
            score += 2.0
        if context.hypothesis.horizon in {"days", "weeks", "months"}:
            score += 3.0
        if context.hypothesis.direction in {"long", "short"}:
            score += 3.0
        return min(self.DIMENSION_WEIGHTS["implementation_realism"], round(score, 2))

    def _score_portfolio_fit(self, context: ScoreContext) -> float:
        positions = context.existing_positions or {}
        if not positions:
            return self.DIMENSION_WEIGHTS["portfolio_fit"] - 1.0
        overlap = sum(1 for instrument in context.event.affected_instruments if instrument in positions)
        if overlap == 0:
            return self.DIMENSION_WEIGHTS["portfolio_fit"]
        penalty = min(6.0, overlap * 3.0)
        return max(0.0, round(self.DIMENSION_WEIGHTS["portfolio_fit"] - penalty, 2))

    def _score_kill_clarity(self, context: ScoreContext) -> float:
        if not context.hypothesis.invalidators:
            return 0.0
        return min(self.DIMENSION_WEIGHTS["kill_clarity"], round(2.5 + 1.25 * len(context.hypothesis.invalidators), 2))

    def _score_search_complexity_penalty(self, context: ScoreContext) -> float:
        count = len(context.hypothesis.candidate_expressions)
        if count > 5:
            return -15.0
        if count > 3:
            return -7.0
        if count > 2:
            return -3.0
        return 0.0

    def _score_crowding_penalty(self, context: ScoreContext) -> float:
        return {
            "low": 0.0,
            "medium": -3.0,
            "high": -7.0,
            "extreme": -10.0,
        }[context.falsification.crowding_check.crowding_level]

    def _score_data_fragility_penalty(self, context: ScoreContext) -> float:
        penalty = 0.0
        if context.event.source_class == "social_general":
            penalty -= 8.0
        elif context.event.source_class == "social_curated":
            penalty -= 4.0
        if context.event.corroboration_count == 0:
            penalty -= 2.0
        return max(-10.0, penalty)
