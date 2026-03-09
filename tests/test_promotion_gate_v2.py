from datetime import datetime, timezone

from fund.promotion_gate import PromotionGateDecision, evaluate_with_artifacts
from research.artifacts import ArtifactEnvelope, ArtifactType, Engine, ProgressionStage, PromotionOutcome


class FakeArtifactStore:
    def __init__(self, chain, query_results=None):
        self._chain = chain
        self._query_results = query_results or []

    def get_chain(self, chain_id):
        return self._chain

    def query(self, **kwargs):
        return list(self._query_results)


def test_promotion_gate_decision_defaults_outcome_from_allowed():
    allowed = PromotionGateDecision(
        allowed=True,
        reason_code="OK",
        message="ok",
        strategy_key="strategy",
    )
    blocked = PromotionGateDecision(
        allowed=False,
        reason_code="NOPE",
        message="nope",
        strategy_key="strategy",
    )

    assert allowed.outcome == PromotionOutcome.PROMOTE
    assert blocked.outcome == PromotionOutcome.REJECT


def test_evaluate_with_artifacts_applies_scoring_outcome(monkeypatch):
    base = PromotionGateDecision(
        allowed=True,
        reason_code="PROMOTION_GATE_PASSED",
        message="ok",
        strategy_key="strategy",
        outcome=PromotionOutcome.PROMOTE,
    )
    monkeypatch.setattr("fund.promotion_gate.evaluate_promotion_gate", lambda **kwargs: base)
    store = FakeArtifactStore(
        [
            ArtifactEnvelope(
                artifact_id="score-1",
                chain_id="chain-1",
                artifact_type=ArtifactType.SCORING_RESULT,
                engine=Engine.ENGINE_B,
                body={
                    "hypothesis_ref": "hyp-1",
                    "falsification_ref": "fal-1",
                    "dimension_scores": {"source": 10.0},
                    "raw_total": 60.0,
                    "penalties": {},
                    "final_score": 60.0,
                    "outcome": "revise",
                    "outcome_reason": "Needs work",
                    "blocking_objections": ["crowding"],
                },
            )
        ]
    )

    decision = evaluate_with_artifacts(
        strategy_key="strategy",
        artifact_store=store,
        chain_id="chain-1",
    )

    assert decision.allowed is False
    assert decision.outcome == PromotionOutcome.REVISE
    assert decision.blocking_objections == ["crowding"]
    assert decision.artifact_refs == ["score-1"]


def test_evaluate_with_artifacts_blocks_unresolved_objections(monkeypatch):
    base = PromotionGateDecision(
        allowed=True,
        reason_code="PROMOTION_GATE_PASSED",
        message="ok",
        strategy_key="strategy",
        outcome=PromotionOutcome.PROMOTE,
    )
    monkeypatch.setattr("fund.promotion_gate.evaluate_promotion_gate", lambda **kwargs: base)
    store = FakeArtifactStore(
        [
            ArtifactEnvelope(
                artifact_id="fal-1",
                chain_id="chain-1",
                artifact_type=ArtifactType.FALSIFICATION_MEMO,
                engine=Engine.ENGINE_B,
                body={
                    "hypothesis_ref": "hyp-1",
                    "cheapest_alternative": "beta",
                    "beta_leakage_check": {
                        "is_just_market_exposure": False,
                        "explanation": "idiosyncratic",
                        "estimated_beta": 0.2,
                    },
                    "crowding_check": {
                        "crowding_level": "high",
                        "explanation": "crowded",
                        "correlated_strategies": ["mom"],
                    },
                    "prior_evidence": [],
                    "unresolved_objections": ["capacity"],
                    "resolved_objections": [],
                    "challenge_model": "claude",
                    "challenge_confidence": 0.8,
                },
            )
        ]
    )

    decision = evaluate_with_artifacts(
        strategy_key="strategy",
        artifact_store=store,
        chain_id="chain-1",
    )

    assert decision.allowed is False
    assert decision.outcome == PromotionOutcome.REVISE
    assert decision.blocking_objections == ["capacity"]


def test_evaluate_with_artifacts_blocks_test_stage_from_live_promotion(monkeypatch):
    base = PromotionGateDecision(
        allowed=True,
        reason_code="PROMOTION_GATE_PASSED",
        message="ok",
        strategy_key="strategy",
        outcome=PromotionOutcome.PROMOTE,
    )
    monkeypatch.setattr("fund.promotion_gate.evaluate_promotion_gate", lambda **kwargs: base)
    store = FakeArtifactStore(
        [
            ArtifactEnvelope(
                artifact_id="score-1",
                chain_id="chain-1",
                artifact_type=ArtifactType.SCORING_RESULT,
                engine=Engine.ENGINE_B,
                body={
                    "hypothesis_ref": "hyp-1",
                    "falsification_ref": "fal-1",
                    "dimension_scores": {"source": 10.0},
                    "raw_total": 76.0,
                    "penalties": {},
                    "final_score": 76.0,
                    "outcome": "promote",
                    "next_stage": ProgressionStage.TEST.value,
                    "outcome_reason": "Ready for test",
                    "blocking_objections": [],
                },
            )
        ]
    )

    decision = evaluate_with_artifacts(
        strategy_key="strategy",
        artifact_store=store,
        chain_id="chain-1",
    )

    assert decision.allowed is False
    assert decision.outcome == PromotionOutcome.REVISE
    assert decision.reason_code == "ARTIFACT_STAGE_TEST_PENDING"
    assert decision.research_stage == ProgressionStage.TEST.value


def test_evaluate_with_artifacts_marks_pilot_ready_chain(monkeypatch):
    base = PromotionGateDecision(
        allowed=True,
        reason_code="PROMOTION_GATE_PASSED",
        message="ok",
        strategy_key="strategy",
        outcome=PromotionOutcome.PROMOTE,
    )
    monkeypatch.setattr("fund.promotion_gate.evaluate_promotion_gate", lambda **kwargs: base)
    store = FakeArtifactStore(
        [
            ArtifactEnvelope(
                artifact_id="score-1",
                chain_id="chain-1",
                artifact_type=ArtifactType.SCORING_RESULT,
                engine=Engine.ENGINE_B,
                body={
                    "hypothesis_ref": "hyp-1",
                    "falsification_ref": "fal-1",
                    "dimension_scores": {"source": 10.0},
                    "raw_total": 93.0,
                    "penalties": {},
                    "final_score": 93.0,
                    "outcome": "promote",
                    "next_stage": ProgressionStage.PILOT.value,
                    "outcome_reason": "Ready for pilot",
                    "blocking_objections": [],
                },
            ),
            ArtifactEnvelope(
                artifact_id="trade-1",
                chain_id="chain-1",
                artifact_type=ArtifactType.TRADE_SHEET,
                engine=Engine.ENGINE_B,
                body={
                    "hypothesis_ref": "hyp-1",
                    "experiment_ref": "exp-1",
                    "instruments": [],
                    "sizing": {"method": "vol_target", "target_risk_pct": 0.01, "max_notional": 25000.0},
                    "entry_rules": ["enter"],
                    "exit_rules": ["exit"],
                    "holding_period_target": "days",
                    "risk_limits": {"max_loss_pct": 2.0, "max_portfolio_impact_pct": 3.0, "max_correlated_exposure_pct": 25.0},
                    "kill_criteria": [],
                },
            ),
        ]
    )

    decision = evaluate_with_artifacts(
        strategy_key="strategy",
        artifact_store=store,
        chain_id="chain-1",
    )

    assert decision.allowed is True
    assert decision.outcome == PromotionOutcome.PROMOTE
    assert decision.reason_code == "ARTIFACT_PROMOTE"
    assert decision.research_stage == ProgressionStage.PILOT.value
    assert decision.requires_human_signoff is True
