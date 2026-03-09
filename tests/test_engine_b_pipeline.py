from tests.research_test_utils import FakeConnection, FakeCursor

from research.artifacts import ArtifactEnvelope, ArtifactType, EdgeFamily, Engine, ProgressionStage, PromotionOutcome
from research.engine_b.pipeline import EngineBPipeline
from research.taxonomy import TaxonomyRejection


class FakeArtifactStore:
    def __init__(self):
        self.items = {}
        self.saved = []

    def get(self, artifact_id):
        return self.items.get(artifact_id)

    def save(self, envelope):
        envelope.artifact_id = envelope.artifact_id or f"artifact-{len(self.saved) + 1}"
        self.saved.append(envelope)
        self.items[envelope.artifact_id] = envelope
        return envelope.artifact_id


class FakeSignalExtractionService:
    def extract(self, **kwargs):
        return ArtifactEnvelope(
            artifact_id="evt-1",
            chain_id="chain-e",
            artifact_type=ArtifactType.EVENT_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            body={
                "source_ids": ["src"],
                "source_class": "news_wire",
                "source_credibility": 0.8,
                "event_timestamp": "2026-03-08T21:00:00Z",
                "corroboration_count": 1,
                "claims": ["Revenue beat"],
                "affected_instruments": ["AAPL"],
                "market_implied_prior": "Muted growth",
                "materiality": "high",
                "time_sensitivity": "days",
                "raw_content_hash": "x" * 64,
            },
        )


class FakeHypothesisService:
    def form_hypothesis(self, **kwargs):
        return ArtifactEnvelope(
            artifact_id="hyp-1",
            chain_id="chain-h",
            artifact_type=ArtifactType.HYPOTHESIS_CARD,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-08T21:00:00Z",
            body={
                "hypothesis_id": "hyp-local",
                "edge_family": "underreaction_revision",
                "event_card_ref": "evt-1",
                "market_implied_view": "Underreaction",
                "variant_view": "More upside",
                "mechanism": "Revision cycle that the market has not priced.",
                "catalyst": "Analyst updates",
                "direction": "long",
                "horizon": "days",
                "confidence": 0.8,
                "invalidators": ["Guide cut"],
                "failure_regimes": [],
                "candidate_expressions": ["AAPL equity"],
                "testable_predictions": ["Positive drift"],
            },
        )


class RejectingHypothesisService:
    def form_hypothesis(self, **kwargs):
        raise TaxonomyRejection("Edge family 'macro_heroics' not in approved taxonomy")


class FakeChallengeService:
    def challenge(self, **kwargs):
        return ArtifactEnvelope(
            artifact_id="fal-1",
            chain_id="chain-f",
            artifact_type=ArtifactType.FALSIFICATION_MEMO,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={
                "hypothesis_ref": "hyp-1",
                "cheapest_alternative": "Pure beta",
                "beta_leakage_check": {
                    "is_just_market_exposure": False,
                    "explanation": "Idiosyncratic revision",
                    "estimated_beta": 0.3,
                },
                "crowding_check": {
                    "crowding_level": "low",
                    "explanation": "ok",
                    "correlated_strategies": [],
                },
                "prior_evidence": [],
                "unresolved_objections": [],
                "resolved_objections": [],
                "challenge_model": "gpt-5.4",
                "challenge_confidence": 0.7,
            },
        )


class FakeScoringEngine:
    def __init__(self, *, next_stage="experiment", outcome="promote", final_score=82.0):
        self.next_stage = next_stage
        self.outcome = outcome
        self.final_score = final_score

    def score(self, **kwargs):
        return ArtifactEnvelope(
            artifact_id="score-1",
            chain_id="chain-s",
            artifact_type=ArtifactType.SCORING_RESULT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            body={
                "hypothesis_ref": "hyp-1",
                "falsification_ref": "fal-1",
                "dimension_scores": {"source_integrity": 9.0},
                "raw_total": 82.0,
                "penalties": {},
                "final_score": self.final_score,
                "outcome": self.outcome,
                "outcome_reason": "Strong",
                "next_stage": self.next_stage,
                "blocking_objections": [],
            },
        )


class FakeExperimentService:
    def __init__(self):
        self.register_calls = []
        self.run_calls = []

    def register_test(self, hypothesis_id, test_spec):
        self.register_calls.append((hypothesis_id, test_spec))
        return ArtifactEnvelope(
            artifact_id="spec-1",
            chain_id="chain-h",
            artifact_type=ArtifactType.TEST_SPEC,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-08T21:02:00Z",
            body={
                **(test_spec.model_dump(mode="json") if hasattr(test_spec, "model_dump") else dict(test_spec)),
            },
        )

    def run_experiment(self, test_spec_id):
        self.run_calls.append(test_spec_id)
        return ArtifactEnvelope(
            artifact_id="exp-1",
            chain_id="chain-h",
            artifact_type=ArtifactType.EXPERIMENT_REPORT,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-08T21:03:00Z",
            body={
                "test_spec_ref": test_spec_id,
                "variants_tested": 1,
                "best_variant": {"name": "baseline", "params": {}},
                "gross_metrics": {
                    "sharpe": 1.1,
                    "sortino": 1.4,
                    "profit_factor": 1.7,
                    "win_rate": 0.55,
                    "max_drawdown": 7.0,
                    "total_return_pct": 12.0,
                    "avg_holding_days": 4.0,
                    "trade_count": 20,
                    "annual_turnover": 120000.0,
                },
                "net_metrics": {
                    "sharpe": 0.9,
                    "sortino": 1.1,
                    "profit_factor": 1.5,
                    "win_rate": 0.52,
                    "max_drawdown": 8.0,
                    "total_return_pct": 9.0,
                    "avg_holding_days": 4.0,
                    "trade_count": 20,
                    "annual_turnover": 120000.0,
                },
                "robustness_checks": [],
                "capacity_estimate": {"max_notional_usd": 250000.0, "limiting_factor": "liq"},
                "correlation_with_existing": {},
                "implementation_caveats": [],
            },
        )


class FakeExpressionService:
    def __init__(self):
        self.calls = []

    def build_trade_sheet(self, hypothesis_id, experiment_id, regime):
        self.calls.append((hypothesis_id, experiment_id, regime))
        return ArtifactEnvelope(
            artifact_id="trade-1",
            chain_id="chain-h",
            artifact_type=ArtifactType.TRADE_SHEET,
            engine=Engine.ENGINE_B,
            ticker="AAPL",
            edge_family=EdgeFamily.UNDERREACTION_REVISION,
            created_at="2026-03-08T21:04:00Z",
            body={
                "hypothesis_ref": hypothesis_id,
                "experiment_ref": experiment_id,
                "instruments": [{"ticker": "AAPL", "instrument_type": "equity", "broker": "ibkr"}],
                "sizing": {"method": "vol_target", "target_risk_pct": 0.01, "max_notional": 25000.0},
                "entry_rules": ["enter"],
                "exit_rules": ["exit"],
                "holding_period_target": "days",
                "risk_limits": {"max_loss_pct": 2.0, "max_portfolio_impact_pct": 3.0, "max_correlated_exposure_pct": 25.0},
                "kill_criteria": ["guide cut"],
            },
        )


def test_engine_b_pipeline_experiment_stage_runs_through_experiment(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.engine_b.pipeline.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.engine_b.pipeline.release_pg_connection", lambda conn: None)
    experiment_service = FakeExperimentService()
    expression_service = FakeExpressionService()

    pipeline = EngineBPipeline(
        artifact_store=FakeArtifactStore(),
        signal_extraction=FakeSignalExtractionService(),
        hypothesis_service=FakeHypothesisService(),
        challenge_service=FakeChallengeService(),
        scoring_engine=FakeScoringEngine(next_stage=ProgressionStage.EXPERIMENT.value),
        experiment_service=experiment_service,
        expression_service=expression_service,
        regime_provider=lambda: {"vol_regime": "normal", "trend_regime": "strong_trend", "carry_regime": "steep", "macro_regime": "risk_on", "sizing_factor": 1.0, "active_overrides": [], "indicators": {}, "as_of": "2026-03-08T21:00:00Z"},
    )

    result = pipeline.process_event(
        raw_content="Revenue beat",
        source_class="news_wire",
        source_credibility=0.8,
        source_ids=["src-1"],
    )

    assert len(result.artifacts) == 6
    assert result.outcome == PromotionOutcome.PROMOTE
    assert result.score == 82.0
    assert result.next_stage == ProgressionStage.EXPERIMENT.value
    assert result.current_stage == "experiment"
    assert result.requires_human_signoff is False
    assert len(experiment_service.register_calls) == 1
    assert experiment_service.run_calls == ["spec-1"]
    assert expression_service.calls == []
    assert len(cursor.executed) == 6


def test_engine_b_pipeline_test_stage_stops_after_test_spec(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.engine_b.pipeline.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.engine_b.pipeline.release_pg_connection", lambda conn: None)
    experiment_service = FakeExperimentService()
    expression_service = FakeExpressionService()

    pipeline = EngineBPipeline(
        artifact_store=FakeArtifactStore(),
        signal_extraction=FakeSignalExtractionService(),
        hypothesis_service=FakeHypothesisService(),
        challenge_service=FakeChallengeService(),
        scoring_engine=FakeScoringEngine(next_stage=ProgressionStage.TEST.value, final_score=76.0),
        experiment_service=experiment_service,
        expression_service=expression_service,
    )

    result = pipeline.process_event(
        raw_content="Revenue beat",
        source_class="news_wire",
        source_credibility=0.8,
        source_ids=["src-1"],
    )

    assert len(result.artifacts) == 5
    assert result.current_stage == "test_spec"
    assert result.next_stage == ProgressionStage.TEST.value
    assert experiment_service.run_calls == []
    assert expression_service.calls == []
    assert len(cursor.executed) == 5


def test_engine_b_pipeline_pilot_stage_builds_trade_sheet(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.engine_b.pipeline.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.engine_b.pipeline.release_pg_connection", lambda conn: None)
    experiment_service = FakeExperimentService()
    expression_service = FakeExpressionService()

    pipeline = EngineBPipeline(
        artifact_store=FakeArtifactStore(),
        signal_extraction=FakeSignalExtractionService(),
        hypothesis_service=FakeHypothesisService(),
        challenge_service=FakeChallengeService(),
        scoring_engine=FakeScoringEngine(next_stage=ProgressionStage.PILOT.value, final_score=93.0),
        experiment_service=experiment_service,
        expression_service=expression_service,
        regime_provider=lambda: {"vol_regime": "normal", "trend_regime": "strong_trend", "carry_regime": "steep", "macro_regime": "risk_on", "sizing_factor": 1.0, "active_overrides": [], "indicators": {}, "as_of": "2026-03-08T21:00:00Z"},
    )

    result = pipeline.process_event(
        raw_content="Revenue beat",
        source_class="news_wire",
        source_credibility=0.8,
        source_ids=["src-1"],
    )

    assert len(result.artifacts) == 7
    assert result.current_stage == "pilot_ready"
    assert result.next_stage == ProgressionStage.PILOT.value
    assert result.requires_human_signoff is True
    assert len(expression_service.calls) == 1
    assert len(cursor.executed) == 7


def test_engine_b_pipeline_halts_on_taxonomy_rejection(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.engine_b.pipeline.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.engine_b.pipeline.release_pg_connection", lambda conn: None)

    pipeline = EngineBPipeline(
        artifact_store=FakeArtifactStore(),
        signal_extraction=FakeSignalExtractionService(),
        hypothesis_service=RejectingHypothesisService(),
        challenge_service=FakeChallengeService(),
        scoring_engine=FakeScoringEngine(next_stage=ProgressionStage.EXPERIMENT.value),
    )

    result = pipeline.process_event(
        raw_content="Revenue beat",
        source_class="news_wire",
        source_credibility=0.8,
        source_ids=["src-1"],
    )

    assert len(result.artifacts) == 1
    assert result.outcome == PromotionOutcome.REJECT
    assert "approved taxonomy" in result.blocking_reasons[0]
    assert len(cursor.executed) == 2


def test_engine_b_pipeline_async_returns_job_id(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("research.engine_b.pipeline.get_pg_connection", lambda: conn)
    monkeypatch.setattr("research.engine_b.pipeline.release_pg_connection", lambda conn: None)

    pipeline = EngineBPipeline(
        artifact_store=FakeArtifactStore(),
        signal_extraction=FakeSignalExtractionService(),
        hypothesis_service=FakeHypothesisService(),
        challenge_service=FakeChallengeService(),
        scoring_engine=FakeScoringEngine(next_stage=ProgressionStage.EXPERIMENT.value),
        experiment_service=FakeExperimentService(),
        expression_service=FakeExpressionService(),
    )

    job_id = pipeline.process_event_async(
        raw_content="Revenue beat",
        source_class="news_wire",
        source_credibility=0.8,
        source_ids=["src-1"],
        job_id="job-1",
    )

    assert job_id == "job-1"
